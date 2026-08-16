"""Resolve a package's dependency tree with npm, without running any of it.

`npm install --package-lock-only` walks registry metadata and writes the
resolved graph to a lockfile. No tarball is downloaded and no lifecycle script
executes, which is what makes it safe to point at arbitrary third-party
manifests — including, later, foreign trees submitted through intake.

Three things this module knows that a naive reader does not:

**Aliases.** `"buffer": "npm:@unabandoned/buffer@^6"` installs one package under
another's directory name. The lockfile key is only *where* it was placed;
`name` is *what* it is. Reading the key is how a freshly published fork got
dated from its abandoned upstream's packument and filed as a time bomb.

**Node's lookup rule.** A dependency reference is satisfied by the nearest
enclosing `node_modules`, walking up. Following that rule rather than assuming a
flat hoisted tree is what makes a path *correct* rather than merely plausible:
a nested copy pinned by a version conflict gets attributed to the parent that
pinned it, not to the hoisted one.

**Identity is `(name, version)`, not `name`.** Two forks can resolve different
majors of the same package through different paths, and those majors can
classify differently. Nodes are therefore keyed by their lockfile entry — which
is unique and preserves nested duplicates — and collapsed by name only at the
last possible moment, in a rollup that keeps the worst state. Collapsing earlier
is how a table ends up reporting membership when it was asked about causation.

The resolver is also the second, independent witness for mechanism M2 — it is
npm's own implementation of alias handling, which is exactly why disagreeing
with it is a signal worth failing a build over.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field

from .facts import Fact

SCOPE = "@unabandoned/"
NPM_TIMEOUT = 180


@dataclass(frozen=True, slots=True)
class Node:
    """One package in a resolved tree, identified by its lockfile entry."""

    key: str                      # lockfile key — unique, preserves nesting
    name: str                     # what it actually is (alias-resolved)
    version: str | None
    alias: str | None             # the directory name, when it differs
    deps: tuple[str, ...]         # its own runtime dependency names, per the lockfile
    direct: bool                  # declared by the root manifest
    via: tuple[str, ...]          # hops between the root and it, exclusive
    parent: str | None            # name of the nearest parent on the shortest path
    depth: int | None

    @property
    def ndeps(self) -> int:
        return len(self.deps)

    @property
    def ident(self) -> str:
        return f"{self.name}@{self.version}" if self.version else self.name


@dataclass(slots=True)
class Tree:
    """A resolved tree: nodes by lockfile key, plus the edges between them."""

    root: str
    root_key: str = ""
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: tuple[tuple[str, str], ...] = ()   # (parent key, child key)
    scope_edges: tuple[str, ...] = ()         # @unabandoned/* packages present
    dev: bool = False

    def by_name(self) -> dict[str, list[Node]]:
        out: dict[str, list[Node]] = defaultdict(list)
        for node in self.nodes.values():
            out[node.name].append(node)
        return dict(out)

    def children(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for parent, child in self.edges:
            out[parent].append(child)
        return dict(out)


def npm_available() -> bool:
    return shutil.which("npm") is not None


def _lookup(entries: dict, from_key: str, dep: str) -> str | None:
    """Which lockfile entry satisfies `dep` when required from `from_key`."""
    prefix = from_key
    while True:
        candidate = (prefix + "/node_modules/" + dep).lstrip("/")
        if candidate in entries:
            return candidate
        if not prefix:
            return None
        cut = prefix.rfind("/node_modules/")
        prefix = prefix[:cut] if cut != -1 else ""


def spec_name(spec: str) -> str:
    """The package name in an npm spec: `browserify@17.0.0` -> `browserify`.

    Scoped names carry a leading `@`, so the version separator is the *last* one
    and only counts past position zero. Getting this wrong is quiet rather than
    loud: `parse_lockfile` fails to find its root, falls back to the probe
    package, and the target itself becomes an ordinary node that dominates the
    entire tree — a dominator analysis that is wrong without erroring.
    """
    at = spec.rfind("@")
    return spec[:at] if at > 0 else spec


def parse_lockfile(lock: dict, root_spec: str, *, dev: bool = False) -> Tree:
    """Turn npm's lockfile into a `Tree`. Pure — no subprocess, no network.

    Split out from `resolve_tree` precisely so it can be tested against recorded
    lockfiles, including the alias shapes that caused the original bug.
    """
    entries = lock.get("packages") or {}
    root_package = spec_name(root_spec)
    root_key = "node_modules/" + root_package
    if root_key not in entries:
        # The probe manifest is the only other thing that can be a root. Falling
        # back silently would misattribute the whole tree, so this is the one
        # place the root is allowed to be the empty key — and only when the
        # named package genuinely is not in the lockfile.
        root_key = "" if "" in entries else root_key
    direct = set((entries.get(root_key) or {}).get("dependencies") or {})

    def real_name(key: str) -> str:
        meta = entries.get(key) or {}
        return meta.get("name") or key.split("node_modules/")[-1]

    def included(key: str) -> bool:
        meta = entries.get(key) or {}
        if meta.get("optional"):
            return False
        # `dev: true` marks a dev-only node: excluded from the runtime tree,
        # included when we are deliberately resolving the dev tree.
        return dev or not meta.get("dev")

    # --- edges, following node's resolution rule ---------------------------
    edges: list[tuple[str, str]] = []
    for key in entries:
        if key != root_key and not key.startswith("node_modules/"):
            continue
        if key != root_key and not included(key):
            continue
        for dep in ((entries.get(key) or {}).get("dependencies") or {}):
            child = _lookup(entries, key, dep)
            if child is None or not included(child):
                continue
            edges.append((key, child))

    # --- shortest path from the root down to each node ---------------------
    paths: dict[str, list[str]] = {root_key: [real_name(root_key)]}
    child_map: dict[str, list[str]] = defaultdict(list)
    for parent, child in edges:
        child_map[parent].append(child)
    frontier = [root_key]
    while frontier:
        nxt: list[str] = []
        for key in frontier:
            for child in child_map.get(key, []):
                if child in paths:
                    continue
                paths[child] = paths[key] + [real_name(child)]
                nxt.append(child)
        frontier = nxt

    tree = Tree(root=root_package, root_key=root_key, dev=dev)
    scope: set[str] = set()

    for key in entries:
        if not key.startswith("node_modules/") or key == root_key:
            continue
        if not included(key):
            continue
        meta = entries[key] or {}
        alias = key.split("node_modules/")[-1]
        name = real_name(key)
        if name == root_package:
            continue
        if name.startswith(SCOPE):
            scope.add(name)
        chain = paths.get(key)
        tree.nodes[key] = Node(
            key=key,
            name=name,
            version=meta.get("version"),
            alias=alias if alias != name else None,
            deps=tuple(sorted((meta.get("dependencies") or {}).keys())),
            # The root's `dependencies` are keyed by alias, so directness is a
            # question about the alias, not the resolved name.
            direct=alias in direct,
            via=tuple(chain[1:-1]) if chain and len(chain) > 2 else (),
            parent=chain[-2] if chain and len(chain) > 1 else None,
            depth=(len(chain) - 1) if chain else None,
        )

    tree.edges = tuple(sorted(e for e in edges if e[1] in tree.nodes))
    tree.scope_edges = tuple(sorted(scope))
    return tree


def resolve_tree(package: str, *, dev: bool = False, timeout: int = NPM_TIMEOUT) -> Fact:
    """Resolve `package`'s tree. Returns a `Fact` wrapping a `Tree`.

    A fork that cannot resolve — unpublished, registry hiccup — becomes a failed
    fact carrying the reason. It is then counted as an excluded repo in the
    coverage ledger rather than vanishing from the denominators.
    """
    if not npm_available():
        return Fact.skipped("npm is not on PATH", source="npm")

    work = tempfile.mkdtemp(prefix="recon-resolve-")
    source = f"npm install {package}" + ("" if dev else " --omit=dev")
    try:
        with open(os.path.join(work, "package.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "recon-probe", "version": "1.0.0", "private": True}, fh)

        cmd = ["npm", "install", package, "--package-lock-only",
               "--ignore-scripts", "--no-audit", "--no-fund", "--silent"]
        if not dev:
            cmd.append("--omit=dev")

        proc = subprocess.run(
            cmd, cwd=work, capture_output=True, text=True, timeout=timeout
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return Fact.failed(
                detail[-1][:200] if detail else "npm install failed", source=source
            )

        lock_path = os.path.join(work, "package-lock.json")
        if not os.path.exists(lock_path):
            return Fact.failed("no lockfile produced", source=source)
        with open(lock_path, encoding="utf-8") as fh:
            lock = json.load(fh)

        return Fact.ok(parse_lockfile(lock, package, dev=dev), source=source)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as exc:
        return Fact.failed(f"{type(exc).__name__}: {exc}"[:200], source=source)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def manifest_scope_edges(package_json: dict) -> list[str]:
    """Reader A for mechanism M2: `@unabandoned/*` edges from a manifest.

    The scope can appear in either half of a dependency entry:

        "@unabandoned/ieee754": "^1.2.0"          <- in the key
        "buffer": "npm:@unabandoned/buffer@^6"    <- in the value, via an alias

    Reading only keys is the bug that made every fork->fork topology edge
    invisible. Both halves are read here, and `integrity.scope_edges_agree`
    checks the result against what npm's own resolver saw.
    """
    found: set[str] = set()
    for name, spec in (package_json.get("dependencies") or {}).items():
        found.add(resolved_dep_name(name, spec))
    return sorted(n for n in found if n.startswith(SCOPE))


def resolved_dep_name(name: str, spec: object) -> str:
    """The package a dependency entry actually resolves to."""
    text = str(spec)
    if not text.startswith("npm:"):
        return name
    target = text[len("npm:"):]
    at = target.rfind("@")   # rfind: scoped names carry a leading '@'
    return target[:at] if at > 0 else target
