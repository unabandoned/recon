"""The pipeline: live facts in, one canonical observation out.

`observation.json` is simultaneously the dashboard's data file and the history
snapshot. Sorted keys and stable array ordering throughout, so a diff between
two builds shows what changed in the world rather than what changed in a dict's
iteration order.

The module is split on purpose:

    build_core()    derives current state from live facts. It takes no history,
                    reads no snapshot, and has no way to. This is where the
                    reproducibility requirement is enforced structurally rather
                    than by good intentions.

    finish()        bolts on the integrity block, which is the only part that is
                    allowed to look at the previous snapshot (M5).

Nothing in `build_core` may consult the past. A snapshot is a fact *about* the
past and cannot drift from the reality it describes — that is why recording it
does not violate "never record derivable state" — but the moment current-state
computation reads one, the guarantee is gone and the file becomes a second
source of truth. `integrity.snapshot_independence` verifies the separation on
every build.
"""
from __future__ import annotations

import datetime
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from . import integrity, osv
from .classify import SEVERITY, State, classify, cutoff_for, DEFAULT_ABANDONMENT_DAYS
from .facts import Fact
from .github import Fork, GitHub, Discovery
from .graph import ForkGraph, build_queue
from .http import Session
from .registry import Registry
from .resolve import Tree, manifest_scope_edges, npm_available, resolve_tree

SCHEMA_VERSION = 1
SNAPSHOT_ENV = "RECON_SNAPSHOTS"

# An org whose forks wire each other should not derive a graph with no internal
# edges. Deliberately low: the floor is here to catch "zero, because the reader
# is broken", not to police the org's actual shape.
EDGE_FLOOR = 1


def canonical(obj) -> str:
    """The one serialisation. Sorted keys, stable, newline-terminated."""
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


@dataclass(slots=True)
class Inputs:
    """Everything the derivation is allowed to see. Note the absence of history."""

    org: str
    today: datetime.date
    discovery: Discovery
    discovery_fact: Fact
    trees: dict[str, Fact]              # fork package -> Fact[Tree]
    registry: Registry
    session: Session
    advisories: Fact                    # Fact[{ident: [advisory]}]
    abandonment_days: int = DEFAULT_ABANDONMENT_DAYS
    builder_sha: str = ""
    npm_available: bool = True


# --------------------------------------------------------------------------- #
# Gathering (does the I/O; hands `Inputs` to the pure part)
# --------------------------------------------------------------------------- #
def gather(
    github: GitHub,
    registry: Registry,
    session: Session,
    *,
    org: str,
    today: datetime.date,
    resolver: Callable[[str], Fact] = resolve_tree,
    advisory_query: Callable[[Session, list], Fact] = osv.query,
    abandonment_days: int = DEFAULT_ABANDONMENT_DAYS,
    builder_sha: str = "",
    with_advisories: bool = True,
) -> Inputs:
    discovery, discovery_fact = github.discover()

    trees: dict[str, Fact] = {}
    for fork in discovery.forks:
        trees[fork.package] = resolver(fork.package)

    # Warm the registry for every name in every tree, and collect the
    # (name, version) pairs the advisory join needs.
    idents: set[tuple[str, str]] = set()
    for fact in trees.values():
        if not fact.is_ok:
            continue
        for node in fact.payload.nodes.values():
            registry.last_release(node.name)
            if node.version:
                idents.add((node.name, node.version))

    if with_advisories:
        advisories = advisory_query(session, sorted(idents))
    else:
        advisories = Fact.skipped("advisory join disabled", source=osv.OSV_BATCH)

    return Inputs(
        org=org,
        today=today,
        discovery=discovery,
        discovery_fact=discovery_fact,
        trees=trees,
        registry=registry,
        session=session,
        advisories=advisories,
        abandonment_days=abandonment_days,
        builder_sha=builder_sha,
        npm_available=npm_available(),
    )


# --------------------------------------------------------------------------- #
# Derivation — current state only, no history
# --------------------------------------------------------------------------- #
def build_core(inp: Inputs) -> dict:
    """Derive the whole current-state observation. Deterministic given `Inputs`."""
    cutoff = cutoff_for(inp.today, inp.abandonment_days)
    advisories = inp.advisories.or_else({})
    scope = "@unabandoned/"
    fork_packages = {f.package for f in inp.discovery.forks}

    # --- per-fork trees, classification, dominators ------------------------
    graphs: dict[str, ForkGraph] = {}
    per_fork_edges: dict[str, dict] = {}
    package_rows: dict[str, dict] = {}
    cross_checks: list[dict] = []
    routes: dict[tuple[str, str], list[list[str]]] = defaultdict(list)
    unresolved: list[dict] = []

    for fork in inp.discovery.forks:
        manifest_edges = None
        if fork.manifest.is_ok and isinstance(fork.manifest.payload, dict):
            manifest_edges = manifest_scope_edges(fork.manifest.payload)

        tree_fact = inp.trees.get(fork.package) or Fact.skipped("not resolved")
        lockfile_edges = None
        if tree_fact.is_ok:
            tree: Tree = tree_fact.payload
            lockfile_edges = sorted(e for e in tree.scope_edges if e != fork.package)
        else:
            unresolved.append({
                "fork": fork.package,
                "reason": tree_fact.detail or str(tree_fact.status),
            })

        per_fork_edges[fork.package] = {
            "manifest_edges": manifest_edges,
            "lockfile_edges": lockfile_edges,
        }

        if not tree_fact.is_ok:
            continue

        tree = tree_fact.payload
        states: dict[str, State] = {}
        for key, node in tree.nodes.items():
            last = inp.registry.last_release(node.name)
            lock_deps = Fact.ok(list(node.deps), source=f"lockfile:{fork.package}")
            verdict = classify(last, lock_deps, cutoff)
            states[key] = verdict.state

            # M2: the registry's own declaration for this exact version.
            reg_deps = inp.registry.declared_deps(node.name, node.version)
            cross_checks.append({
                "ident": node.ident,
                "registry_deps": sorted(reg_deps.payload) if reg_deps.is_ok else None,
                "lockfile_deps": sorted(node.deps),
            })

            routes[(fork.package, node.name)].append(list(node.via))
            _accumulate_package(
                package_rows, fork, node, verdict, advisories, inp.registry
            )

        graphs[fork.package] = ForkGraph.build(fork.package, tree, states)

    # --- fork -> fork edges, derived twice and required to agree -----------
    edges: list[dict] = []
    for fork_pkg in sorted(per_fork_edges):
        data = per_fork_edges[fork_pkg]
        manifest = set(data["manifest_edges"] or [])
        lock = set(data["lockfile_edges"] or [])
        for target in sorted(manifest | lock):
            if target not in fork_packages or target == fork_pkg:
                continue
            derivation = (
                "both" if target in manifest and target in lock
                else "manifest" if target in manifest else "lockfile"
            )
            edges.append({
                "from": fork_pkg, "to": target,
                "kind": "runtime", "tree": "published", "derivation": derivation,
            })

    # --- consumer edges (editorial, from used-by) --------------------------
    consumer_edges: list[dict] = []
    for fork in inp.discovery.forks:
        for entry in (fork.metadata.get("used-by") or []):
            consumer = (entry.get("consumer") or "").strip()
            if not consumer:
                continue
            consumer_edges.append({
                "from": consumer, "to": fork.package,
                "kind": "consumer", "derivation": "used-by",
            })
    consumer_edges.sort(key=lambda e: (e["from"], e["to"]))

    # --- the work queue ----------------------------------------------------
    queue = [
        _queue_entry(c, scope, fork_packages)
        for c in build_queue(graphs, advisories)
    ]

    # --- fork rows ---------------------------------------------------------
    # Built BEFORE the coverage ledger, because they still fetch (each fork's own
    # packument, for the published version). Snapshotting `session.summary()`
    # first made the ledger under-report its own failed fetches by exactly the
    # number of reads that happened after it — the shape of undercount this whole
    # module exists to prevent, caught by the snapshot-independence invariant.
    fork_rows = [_fork_row(f, inp, graphs, advisories) for f in inp.discovery.forks]

    # --- coverage ledger ---------------------------------------------------
    packages = sorted(package_rows.values(), key=lambda p: p["name"])
    resolved_forks = sum(1 for f in inp.trees.values() if f.is_ok)
    coverage = {
        "repos": {
            "discovered": inp.discovery.discovered,
            "included": len(inp.discovery.forks),
            "excluded": sorted(inp.discovery.excluded, key=lambda e: e["repo"]),
        },
        "trees": {
            "resolved": resolved_forks,
            "failed": sorted(unresolved, key=lambda u: u["fork"]),
        },
        "fetches": inp.session.summary(),
        "advisories": {
            **inp.advisories.provenance(),
            "packages_with_advisories": len(advisories),
        },
    }

    totals = _totals(packages, edges, inp.discovery.forks)

    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "org": inp.org,
            "observed_date": inp.today.isoformat(),
            "builder_sha": inp.builder_sha,
            "abandonment_days": inp.abandonment_days,
            "npm_available": inp.npm_available,
        },
        "coverage": coverage,
        "totals": totals,
        "forks": fork_rows,
        "packages": packages,
        "edges": sorted(edges, key=lambda e: (e["from"], e["to"])),
        "consumer_edges": consumer_edges,
        "queue": queue,
        "_checks": {          # working data for integrity; stripped before writing
            "per_fork": per_fork_edges,
            "cross_checks": sorted(cross_checks, key=lambda c: c["ident"]),
            "routes": {k: v for k, v in routes.items()},
        },
    }


def _accumulate_package(rows, fork: Fork, node, verdict, advisories, registry) -> None:
    """Roll one tree node into the org-wide package table (worst state wins)."""
    row = rows.setdefault(node.name, {
        "name": node.name,
        "state": State.ALIVE.value,
        "reason": "",
        "versions": [],
        "forks": [],
        "direct_somewhere": False,
        "parents": [],
        "shortest": None,
        "advisories": [],
        "evidence": {},
        "ndeps": 0,
    })
    if fork.package not in row["forks"]:
        row["forks"].append(fork.package)
    if node.version and node.version not in row["versions"]:
        row["versions"].append(node.version)
    row["direct_somewhere"] = row["direct_somewhere"] or node.direct
    if node.parent and node.parent not in row["parents"]:
        row["parents"].append(node.parent)

    for adv in advisories.get(node.ident, []):
        if adv not in row["advisories"]:
            row["advisories"].append(adv)

    shortest = row["shortest"]
    if node.depth is not None and (shortest is None or node.depth < shortest["depth"]):
        row["shortest"] = {
            "fork": fork.package, "via": list(node.via), "depth": node.depth,
        }

    if SEVERITY[verdict.state] > SEVERITY[State(row["state"])]:
        row["state"] = verdict.state.value
        row["reason"] = verdict.reason
        row["evidence"] = verdict.evidence()
        row["ndeps"] = node.ndeps
    elif not row["evidence"]:
        row["reason"] = verdict.reason
        row["evidence"] = verdict.evidence()
        row["ndeps"] = node.ndeps

    row["versions"].sort()
    row["forks"].sort()
    row["parents"].sort()


def _queue_entry(cand, scope: str, fork_packages: set[str]) -> dict:
    """One work-queue row, with the consequence of each option spelled out."""
    clears = sorted(cand.clears - {cand.name})
    options = []
    already_ours = (scope + cand.name) in fork_packages
    if already_ours:
        options.append({
            "action": "repoint",
            "effect": f"a maintained @unabandoned/{cand.name} already exists — aliasing to it "
                      f"closes {len(cand.clears)} rotten node(s) with no new fork",
            "cost": "one package.json line per consuming fork",
        })
    else:
        options.append({
            "action": "fork",
            "effect": f"the org owns the subtree; {len(cand.clears)} rotten node(s) come under "
                      "Renovate",
            "cost": f"{len(cand.clears)} package(s) added to the maintenance surface",
        })
    options.append({
        "action": "replace",
        "effect": f"aliasing to a maintained equivalent removes {len(clears)} downstream "
                  "node(s) from the tree entirely",
        "cost": "behavioural risk; needs a real equivalent to exist",
    })
    options.append({
        "action": "vendor",
        "effect": "the subtree is internalised and drops out of the audit surface",
        "cost": "counts as a coverage loss, not a win — recon can no longer see it",
    })
    return {
        "package": cand.name,
        "state": cand.state.value,
        "versions": sorted(cand.versions),
        "forks": sorted(cand.forks),
        "score": cand.score(),
        "clears": clears,
        "clears_count": len(cand.clears),
        "advisories": sorted(cand.advisories),
        "max_severity": cand.max_severity,
        "emergency": cand.emergency,
        "shadowed_in": sorted(cand.shadowed_in),
        "paths": cand.paths,
        "options": options,
    }


def _fork_row(fork: Fork, inp: Inputs, graphs: dict, advisories: dict) -> dict:
    """One fork's row, with every live datum carrying its provenance."""
    tree_fact = inp.trees.get(fork.package) or Fact.skipped("not resolved")
    grade = "unknown"
    counts = {"alive": 0, "inert": 0, "time_bomb": 0, "unknown": 0}
    adv_count = 0
    if tree_fact.is_ok and fork.package in graphs:
        fg = graphs[fork.package]
        for key, state in fg.states.items():
            counts[state.value] += 1
            node = fg.tree.nodes[key]
            adv_count += len(advisories.get(node.ident, []))
        if adv_count and counts["time_bomb"]:
            grade = "emergency"
        elif counts["time_bomb"]:
            grade = "at-risk"
        elif counts["unknown"]:
            grade = "unmeasured"
        else:
            grade = "clean"

    published = inp.registry.latest_version(fork.package)
    return {
        "package": fork.package,
        "repo": fork.repo,
        "url": fork.html_url,
        "default_branch": fork.default_branch,
        "head_sha": _f(fork.head_sha),
        "published_version": _f(published),
        "release_tag": _f(fork.release.map(
            lambda r: (r or {}).get("tag_name") if isinstance(r, dict) or r is None else None
        )),
        "status": fork.metadata.get("status", "active"),
        "summary": fork.metadata.get("summary", ""),
        "why_forked": fork.metadata.get("why-forked", ""),
        "upstream": fork.metadata.get("upstream", {}),
        "used_by": fork.metadata.get("used-by", []) or [],
        "tags": sorted(fork.metadata.get("tags", []) or []),
        "ci": _f(fork.ci),
        "open_prs": _f(fork.open_prs),
        "renovate_prs": _f(fork.renovate_prs),
        "open_issues": _f(fork.open_issues),
        "excluded_issues": _f(fork.excluded_issues),
        "security": _f(fork.security),
        "autorelease_pending": _f(fork.autorelease_pending),
        "dependency_dashboard": _f(fork.dependency_dashboard_url),
        "tree": {
            "resolved": tree_fact.is_ok,
            # Siblings anywhere beneath this fork, not just the ones it declares.
            # Deliberately NOT what M2 compares — a sibling reached through an
            # intermediary is that intermediary's edge, not this fork's.
            "scope_reachable": (
                sorted(e for e in tree_fact.payload.scope_reachable
                       if e != fork.package) if tree_fact.is_ok else []
            ),
            "reason": "" if tree_fact.is_ok else (tree_fact.detail or str(tree_fact.status)),
            "counts": counts,
            "advisories": adv_count,
            "total": len(tree_fact.payload.nodes) if tree_fact.is_ok else 0,
        },
        "grade": grade,
    }


def _f(fact: Fact) -> dict:
    """A fact as it travels to the renderer: never a bare value."""
    out = fact.provenance()
    if fact.is_ok:
        out["value"] = fact.payload
    return out


def _totals(packages: list[dict], edges: list[dict], forks: list[Fork]) -> dict:
    by_state = defaultdict(int)
    for p in packages:
        by_state[p["state"]] += 1
    with_advisories = sum(1 for p in packages if p["advisories"])
    emergencies = sum(
        1 for p in packages if p["advisories"] and p["state"] == State.TIME_BOMB.value
    )
    invisible = sum(
        1 for p in packages
        if p["state"] == State.TIME_BOMB.value and not p["direct_somewhere"]
    )
    return {
        "forks": len(forks),
        "packages": len(packages),
        "alive": by_state[State.ALIVE.value],
        "inert": by_state[State.INERT.value],
        "time_bomb": by_state[State.TIME_BOMB.value],
        "unknown": by_state[State.UNKNOWN.value],
        "with_advisories": with_advisories,
        "emergencies": emergencies,
        "invisible": invisible,
        "edges": len(edges),
    }


# --------------------------------------------------------------------------- #
# Integrity + finishing
# --------------------------------------------------------------------------- #
def run_checks(
    core: dict,
    *,
    fixtures: dict,
    previous: dict | None,
    acknowledged: set[str],
    rederived: str | None = None,
) -> list[integrity.Check]:
    work = core["_checks"]
    coverage = core["coverage"]
    totals = core["totals"]

    issue_values = {
        f["repo"]: f["open_issues"]["value"]
        for f in core["forks"] if "value" in f["open_issues"]
    }

    observed = {
        "routes": work["routes"],
        "counts": {
            ("open_issues", f["repo"]): f["open_issues"].get("value")
            for f in core["forks"]
        },
    }

    checks = [
        integrity.unknowns_are_accounted(core["packages"], coverage["fetches"]),
        integrity.scope_edges_agree(work["per_fork"]),
        integrity.dependency_counts_agree(work["cross_checks"]),
        integrity.expected_siblings_present(work["per_fork"], fixtures.get("edges")),
        integrity.fork_edge_floor(totals["edges"], totals["forks"], EDGE_FLOOR),
        integrity.uniformity("open_issues", issue_values),
        integrity.conservation(coverage),
        integrity.differential(
            totals,
            (previous or {}).get("totals") if previous else None,
            acknowledged=acknowledged,
        ),
    ]
    checks.extend(integrity.org_fixtures_hold(fixtures, observed))
    if rederived is not None:
        checks.append(
            integrity.snapshot_independence(canonical(strip(core)), rederived)
        )
    return checks


def strip(core: dict) -> dict:
    """The observation as published — working data for the checks removed."""
    return {k: v for k, v in core.items() if not k.startswith("_")}


def finish(core: dict, checks: list[integrity.Check], *, built_at: str,
           duration_ms: int) -> dict:
    obs = strip(core)
    obs["meta"] = {**obs["meta"], "built_at": built_at, "duration_ms": duration_ms}
    obs["integrity"] = {
        "status": integrity.worst_status(checks),
        "checks": [c.to_json() for c in sorted(checks, key=lambda c: c.id)],
        "counts": integrity.counts(checks),
    }
    return obs


def rederive_with_history_masked(inp: Inputs, snapshots_dir) -> str:
    """Re-run the derivation with the snapshot directory pointed somewhere empty.

    `build_core` has no history parameter, so in a correct build this is
    identical by construction. The point is to catch the regression where
    somebody reaches around the parameter list — an `os.environ` lookup, a
    module-level path, a convenience import of `snapshots.latest()` — and makes
    current state quietly depend on the past. Costs one re-derivation over facts
    that are already fetched: no network, milliseconds.
    """
    import tempfile

    previous = os.environ.get(SNAPSHOT_ENV)
    with tempfile.TemporaryDirectory(prefix="recon-nohistory-") as empty:
        os.environ[SNAPSHOT_ENV] = empty
        try:
            return canonical(strip(build_core(inp)))
        finally:
            if previous is None:
                os.environ.pop(SNAPSHOT_ENV, None)
            else:
                os.environ[SNAPSHOT_ENV] = previous
