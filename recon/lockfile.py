"""Read a committed lockfile. Parsing, never resolving.

The distinction is the whole reason this module is allowed to exist. recon
already refuses to re-implement npm's resolver — a second resolver is a second
home for the bug classes this repository exists to catch, and the measured cost
of the one that was tried is in `docs/implementation.md`. A lockfile has nothing
to approximate: the tool already did the resolving and committed the answer.
Reading it is the *opposite* of a second resolver, because it is the only way to
learn what a project actually installs rather than what it would install today.

That distinction also sets the failure rule. If the lockfile is in a format this
module does not read, the answer is a failed `Fact` naming the format — never a
fallback to resolving the manifest with npm, which would silently report a tree
nobody installs. `it-tools` pins exact versions while its upstream uses `^`
ranges throughout, so npm-resolving the pair would report differences that are
artifacts of the resolver rather than facts about the repositories.

Supported: npm (`package-lock.json`, v2/v3) and pnpm (`pnpm-lock.yaml`, v6/v9).
Refused loudly: yarn (custom v1 grammar, YAML v2+) and bun (binary).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .facts import Fact

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - surfaced as a clear message
    yaml = None

NPM = "npm"
PNPM = "pnpm"
#: Not a lockfile at all — a manifest read on its own. It carries what a project
#: *declares* (names, specifiers, aliases) but not what it resolved to, so a
#: comparison built from it must not imply the second.
MANIFEST = "manifest"

#: Filename -> tool. Order matters: the first one a repo has is the one it uses.
KNOWN = [
    ("pnpm-lock.yaml", PNPM),
    ("package-lock.json", NPM),
    ("npm-shrinkwrap.json", NPM),
]

#: Formats we can identify but deliberately do not parse.
REFUSED = {
    "yarn.lock": "yarn (v1 uses a bespoke grammar, v2+ a different YAML shape)",
    "bun.lockb": "bun (binary lockfile)",
    "bun.lock": "bun",
}

# `3.20.0(vue@3.3.4)` — pnpm records the peer context it resolved against in the
# version string. It is not part of the version and comparing it as one reports
# a bump every time an unrelated peer moves.
_PEER = re.compile(r"\(.*\)$")


@dataclass(frozen=True, slots=True)
class Dep:
    """One direct dependency: what was asked for, and what that became."""

    name: str
    specifier: str      # the range in the manifest — "^2.2.1"
    version: str        # what the lockfile pinned — "2.2.1"
    dev: bool = False
    # The package actually installed under `name`, when an alias makes them
    # differ: `"buffer": "npm:@unabandoned/buffer@^6"` keeps the key `buffer`
    # while installing something else entirely. That indirection *is* the
    # adoption mechanism this org uses, so a diff that only reads keys cannot
    # see the single change it most needs to report.
    resolved_name: str = ""

    @property
    def package(self) -> str:
        return self.resolved_name or self.name

    @property
    def aliased(self) -> bool:
        return bool(self.resolved_name) and self.resolved_name != self.name

    @property
    def pinned(self) -> bool:
        """An exact specifier. The org has an opinion about this ("don't pin")."""
        return bool(self.specifier) and re.fullmatch(r"\d+\.\d+\.\d+.*", self.specifier) is not None


@dataclass(slots=True)
class Lockfile:
    tool: str
    lockfile_version: str
    direct: dict[str, Dep] = field(default_factory=dict)     # name -> Dep (runtime + dev)
    resolved: dict[str, set[str]] = field(default_factory=dict)  # name -> versions anywhere

    @property
    def runtime(self) -> dict[str, Dep]:
        return {n: d for n, d in self.direct.items() if not d.dev}

    @property
    def total_packages(self) -> int:
        return len(self.resolved)


def split_ident(ident: str) -> tuple[str, str]:
    """`@scope/name@1.2.3` -> (`@scope/name`, `1.2.3`).

    Two traps, and they compound. A scoped package starts with `@`, so the
    separator is the *last* `@` that is not at position zero — splitting on the
    first renames every scoped package. But pnpm's snapshot keys also carry the
    peer context they resolved against, as in
    `'@tabler/icons-vue@3.20.0(vue@3.3.4)'`, and *that* `@` is the last one. So
    the suffix has to come off before the search, or the split lands inside the
    parenthetical and yields the name `@tabler/icons-vue@3.20.0(vue`.
    """
    ident = _PEER.sub("", ident)
    at = ident.rfind("@")
    if at <= 0:
        return ident, ""
    return ident[:at], ident[at + 1:]


def _alias_target(specifier: str) -> str:
    """The real package behind an `npm:` alias specifier, or "" when there isn't one.

    Only the unambiguous signal is used. pnpm also encodes the resolved package
    in the version string for aliases, but that field carries peer context and
    other decoration too, and guessing a package name out of it is the kind of
    inference that produces a confident wrong answer.
    """
    spec = (specifier or "").strip()
    if not spec.startswith("npm:"):
        return ""
    name, _version = split_ident(spec[4:])
    return name


def clean_version(version: str) -> str:
    return _PEER.sub("", (version or "").strip())


def read_manifest(text: str) -> Fact:
    """Read a `package.json` as a `Lockfile` with declarations but no resolutions.

    A manifest is the only artifact every project has — some repos commit no
    lockfile, and the browser-side comparison reads manifests because they are
    JSON in every ecosystem while pnpm and yarn lockfiles are not. It answers
    what a project *declares*: names, specifiers, aliases, and therefore adds,
    drops, replacements and pinning. It cannot answer what got installed, so
    `version` is empty everywhere and `resolved` is empty — which makes the
    version-delta and tree sections of a comparison come out empty rather than
    wrong.
    """
    try:
        doc = json.loads(text)
        if not isinstance(doc, dict):
            raise ValueError("package.json is not an object")
    except (ValueError, TypeError) as exc:
        return Fact.failed(f"{type(exc).__name__}: {exc}"[:200], source="package.json")

    direct: dict[str, Dep] = {}
    for block, dev in (("dependencies", False), ("devDependencies", True)):
        entries = doc.get(block)
        if not isinstance(entries, dict):
            continue
        for name, spec in entries.items():
            direct[str(name)] = Dep(str(name), str(spec), "", dev,
                                    _alias_target(str(spec)))
    return Fact.ok(Lockfile(MANIFEST, "", direct, {}), source="package.json")


def read(text: str, filename: str) -> Fact:
    """Parse a lockfile. Returns `Fact[Lockfile]`, failed if unreadable."""
    source = f"lockfile:{filename}"
    base = filename.rsplit("/", 1)[-1]

    if base in REFUSED:
        return Fact.failed(
            f"{base} is not a format recon reads — {REFUSED[base]}. Resolving the "
            "manifest with npm instead would report a tree this project does not "
            "install, so no audit is attempted.",
            source=source,
        )

    tool = dict(KNOWN).get(base)
    if tool is None:
        return Fact.failed(f"unrecognised lockfile name {base!r}", source=source)

    try:
        if tool == NPM:
            return Fact.ok(_read_npm(text), source=source)
        return Fact.ok(_read_pnpm(text), source=source)
    except Exception as exc:  # noqa: BLE001 - a broken lockfile is a failed read
        return Fact.failed(f"{type(exc).__name__}: {exc}"[:200], source=source)


def _read_npm(text: str) -> Lockfile:
    doc = json.loads(text)
    entries = doc.get("packages")
    if not isinstance(entries, dict):
        raise ValueError("no `packages` block — lockfileVersion 1 is not supported")

    root = entries.get("") or {}
    direct: dict[str, Dep] = {}
    for block, dev in (("dependencies", False), ("devDependencies", True)):
        for name, spec in (root.get(block) or {}).items():
            node = entries.get(f"node_modules/{name}") or {}
            # npm records an alias by putting the real package in `name` while
            # the directory keeps the alias. Authoritative, so no guessing.
            direct[name] = Dep(name, str(spec),
                               clean_version(node.get("version", "")), dev,
                               str(node.get("name") or ""))

    resolved: dict[str, set[str]] = {}
    for key, meta in entries.items():
        if not key.startswith("node_modules/"):
            continue
        # `name` wins when present: it is how npm records an alias, where the
        # directory is the alias and the package is something else entirely.
        name = (meta or {}).get("name") or key.rsplit("node_modules/", 1)[-1]
        version = clean_version((meta or {}).get("version", ""))
        if version:
            resolved.setdefault(name, set()).add(version)

    return Lockfile(NPM, str(doc.get("lockfileVersion", "")), direct, resolved)


def _read_pnpm(text: str) -> Lockfile:
    if yaml is None:
        raise RuntimeError("PyYAML is required to read pnpm-lock.yaml")
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("pnpm-lock.yaml is not a mapping")

    importers = doc.get("importers")
    direct: dict[str, Dep] = {}
    if isinstance(importers, dict):
        # `.` is the workspace root. Other importers are workspace members and
        # are deliberately not merged in: a monorepo has many manifests and
        # flattening them would invent a dependency set no package declares.
        root = importers.get(".") or {}
    else:
        root = {k: doc.get(k) for k in ("dependencies", "devDependencies")}

    for block, dev in (("dependencies", False), ("devDependencies", True)):
        for name, entry in (root.get(block) or {}).items():
            if isinstance(entry, dict):
                spec, version = entry.get("specifier", ""), entry.get("version", "")
            else:                       # pnpm v5 and earlier: name -> version
                spec, version = "", entry
            direct[name] = Dep(name, str(spec), clean_version(str(version)), dev,
                               _alias_target(str(spec)))

    resolved: dict[str, set[str]] = {}
    for block in ("packages", "snapshots"):
        for ident in (doc.get(block) or {}):
            name, version = split_ident(str(ident))
            if name and version:
                resolved.setdefault(name, set()).add(version)

    return Lockfile(PNPM, str(doc.get("lockfileVersion", "")), direct, resolved)


def workspace_members(text: str) -> list[str]:
    """Importer paths other than the root, so a monorepo can say so."""
    if yaml is None:
        return []
    try:
        doc = yaml.safe_load(text)
        return sorted(k for k in (doc.get("importers") or {}) if k != ".")
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# Fetching one from a repository
# --------------------------------------------------------------------------- #
RAW = "https://raw.githubusercontent.com"
#: What GitHub allows in an owner or repository name.
_NAME = re.compile(r"[A-Za-z0-9._-]+")
#: A branch, tag or SHA. Slashes are legal in refs (`feature/x`), so `..` has to
#: be excluded by name — otherwise `owner/repo@../..` walks the raw URL to a path
#: the caller never asked for.
_REF = re.compile(r"[A-Za-z0-9._/-]+")
DEFAULT_REFS = ("main", "master")


def parse_repo(value: str) -> tuple[str, str, str | None]:
    """Accept the forms a human actually pastes.

        https://github.com/owner/repo
        https://github.com/owner/repo/tree/branch
        owner/repo
        owner/repo@branch
    """
    text = (value or "").strip().rstrip("/")
    ref: str | None = None
    # `git@github.com:owner/repo` is what the clone box offers on the SSH tab,
    # and it is a form people paste.
    if text.startswith("git@github.com:"):
        text = text[len("git@github.com:"):]
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.endswith(".git"):
        text = text[:-4]
    if "/tree/" in text:
        text, ref = text.split("/tree/", 1)
    elif text.count("@") == 1 and not text.startswith("@"):
        text, ref = text.split("@", 1)
    parts = [p for p in text.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"cannot read an owner/repo out of {value!r}")
    owner, repo = parts[0], parts[1]
    # Without this, `https://example.com/` parses as owner `https:` and repo
    # `example.com`, fetches a nonsense raw URL, and reports "no lockfile found"
    # — a fact about the world, for what is actually a typo.
    if not (_NAME.fullmatch(owner) and _NAME.fullmatch(repo)):
        raise ValueError(
            f"{value!r} does not name a GitHub repository "
            f"(read owner={owner!r}, repo={repo!r})"
        )
    if ref is not None and (
        not _REF.fullmatch(ref) or ".." in ref.split("/")
    ):
        raise ValueError(f"{ref!r} is not a usable git ref")
    return owner, repo, ref


def fetch(session, value: str) -> Fact:
    """Find and read a repo's lockfile. `Fact[Lockfile]` with `.source` naming it.

    Tries the supported names first, then the refused ones — so a yarn repo gets
    "yarn.lock is not a format recon reads" rather than "no lockfile found",
    which would be true and useless.
    """
    owner, repo, ref = parse_repo(value)
    refs = (ref,) if ref else DEFAULT_REFS
    tried: list[str] = []

    for candidate_ref in refs:
        for name, _tool in KNOWN:
            url = f"{RAW}/{owner}/{repo}/{candidate_ref}/{name}"
            got = session.get_text(url, absent_is_ok=True)
            if got.is_ok and got.payload is not None:
                return _stamp(read(got.payload, name), owner, repo, candidate_ref, name)
            tried.append(f"{candidate_ref}/{name}")

    for candidate_ref in refs:
        for name in REFUSED:
            url = f"{RAW}/{owner}/{repo}/{candidate_ref}/{name}"
            got = session.get_text(url, absent_is_ok=True)
            if got.is_ok and got.payload is not None:
                return _stamp(read(got.payload, name), owner, repo, candidate_ref, name)

    return Fact.failed(
        f"no lockfile found in {owner}/{repo} (tried {', '.join(tried)})",
        source=f"{RAW}/{owner}/{repo}",
    )


def _stamp(fact: Fact, owner: str, repo: str, ref: str, name: str) -> Fact:
    from dataclasses import replace as _replace
    return _replace(fact, source=f"{owner}/{repo}@{ref}/{name}")
