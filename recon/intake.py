"""Intake — audit any tree before adopting it (§7b).

The org's origin story was "adopt a tool, discover half its tree is abandoned
and carrying CVEs". This turns that into a feature: point recon at any package
spec, get the same classification the daily build produces, joined against the
fork inventory to say what adopting it would actually cost.

**One code path, different root.** The audit resolves with npm's real resolver,
classifies with `classify()`, ranks with the same dominator machinery, and joins
advisories through the same OSV batch. A second classifier would be a second
home for exactly the bug classes the rest of this repository exists to kill, so
there isn't one — `audit()` assembles existing parts and adds only the join.

**The join is a fact, not an assumption.** Coverage overlap needs the org's own
inventory, which is an input that can be missing. When it is, `covered` is not
zero — it is unknown, and every derived count says so. "We don't know" staying
unrepresentable as a benign value is the whole design principle, and the join is
the easiest place in the codebase to violate it: an empty inventory silently
reports a beautiful adoption plan requiring zero forks.

**Isolation.** An intake report is a timestamped observation of an *external*
tree. It is written under `reports/`, never `snapshots/`, and nothing in the
org's own build reads it. An audit of `factor-bundle` must never inflate the
org's time-bomb count. `tests/test_intake.py` asserts the separation rather than
trusting the convention.
"""
from __future__ import annotations

import datetime
import json
from typing import Callable

from . import integrity, osv
from .classify import SEVERITY, State, classify, cutoff_for, DEFAULT_ABANDONMENT_DAYS
from .facts import Fact
from .graph import ForkGraph, build_queue
from .http import Session
from .registry import Registry
from .resolve import SCOPE, Tree, npm_available, resolve_tree, spec_name

SCHEMA_VERSION = 1

#: What adopting one package would take.
ALIAS = "alias"    # we already maintain a fork — wire it with an npm alias
QUEUED = "queued"  # already a known target in the org's own work queue
FORK = "fork"      # nothing covers it; a new fork is the intervention


class Inventory:
    """What the org already maintains, as read from a published observation.

    Deliberately a `Fact` underneath. An intake run with no inventory is not an
    intake run that found nothing covered; those are different reports and only
    one of them is safe to act on.
    """

    def __init__(self, fact: Fact) -> None:
        self._fact = fact

    @staticmethod
    def from_observation(obs: object, *, source: str = "observation.json") -> "Inventory":
        def extract(doc: object) -> dict:
            if not isinstance(doc, dict):
                raise ValueError("observation is not an object")
            forks = doc.get("forks")
            if not isinstance(forks, list):
                raise ValueError("observation has no `forks` array")
            packages = sorted(
                f["package"] for f in forks
                if isinstance(f, dict) and isinstance(f.get("package"), str)
            )
            if not packages:
                raise ValueError("observation lists no forks")
            queue = sorted({
                q["package"] for q in (doc.get("queue") or [])
                if isinstance(q, dict) and isinstance(q.get("package"), str)
            })
            return {
                "forks": packages,
                "queue": queue,
                "built_at": (doc.get("meta") or {}).get("built_at", ""),
                "builder_sha": (doc.get("meta") or {}).get("builder_sha", ""),
            }

        return Inventory(Fact.ok(obs, source=source).map(extract))

    @staticmethod
    def unavailable(detail: str, *, source: str = "observation.json") -> "Inventory":
        return Inventory(Fact.failed(detail, source=source))

    @property
    def fact(self) -> Fact:
        return self._fact

    @property
    def known(self) -> bool:
        return self._fact.is_ok

    def coverage_index(self) -> dict[str, str]:
        """casefolded upstream name -> the fork package that covers it.

        `@unabandoned/xml-js` is the org's fork of `xml-js`: the scope prefix is
        the whole mapping. The casefolding is not cosmetic. npm requires *scoped*
        package names to be lowercase, while unscoped legacy names need not be,
        so the org's fork of `JSONStream` is necessarily published as
        `@unabandoned/jsonstream`. An exact-match join therefore reports that we
        do not maintain a package we have maintained for months, and the adoption
        plan proposes forking it a second time. That was the first real bug this
        module produced, and it was found by reading an audit of `factor-bundle`
        rather than by any check — which is why the match *evidence* travels with
        every covered row instead of just a boolean.
        """
        if not self.known:
            return {}
        return {
            p[len(SCOPE):].casefold(): p
            for p in self._fact.payload["forks"] if p.startswith(SCOPE)
        }

    def covered_by(self, name: str) -> tuple[str | None, str]:
        """`(fork package, how it matched)` for one upstream package name."""
        index = self.coverage_index()
        fork = index.get(name.casefold())
        if fork is None:
            return None, ""
        return fork, ("exact" if fork == SCOPE + name else "case-insensitive")

    def queued_names(self) -> set[str]:
        return set(self._fact.payload["queue"]) if self.known else set()

    def provenance(self) -> dict:
        base = self._fact.provenance()
        if self.known:
            base = {
                **base,
                "forks": len(self._fact.payload["forks"]),
                "queue": len(self._fact.payload["queue"]),
                "observation_built_at": self._fact.payload["built_at"],
                "observation_builder_sha": self._fact.payload["builder_sha"],
            }
        return base


def audit(
    spec: str,
    *,
    registry: Registry,
    session: Session,
    inventory: Inventory,
    today: datetime.date | None = None,
    abandonment_days: int = DEFAULT_ABANDONMENT_DAYS,
    builder_sha: str = "",
    resolver: Callable[[str], Fact] = resolve_tree,
    advisory_query: Callable[[Session, list], Fact] = osv.query,
    with_advisories: bool = True,
) -> dict:
    """Audit one foreign spec. Returns the report; never raises on a bad tree."""
    today = today or datetime.date.today()
    cutoff = cutoff_for(today, abandonment_days)
    root_name = spec_name(spec)

    tree_fact = resolver(spec)
    if not tree_fact.is_ok:
        return _unresolved_report(
            spec, root_name, tree_fact, inventory, session,
            today=today, abandonment_days=abandonment_days, builder_sha=builder_sha,
        )

    tree: Tree = tree_fact.payload
    # The resolver derives the real package name from the lockfile. For a repo
    # spec (`github:owner/repo`) the spec string carries no package name at all,
    # so `spec_name` above is only a placeholder until the tree is in hand.
    root_name = tree.root or root_name

    # --- classify every node, exactly as the daily build does ---------------
    states: dict[str, State] = {}
    idents: set[tuple[str, str]] = set()
    for key, node in tree.nodes.items():
        last = registry.last_release(node.name)
        deps = Fact.ok(list(node.deps), source=f"lockfile:{spec}")
        states[key] = classify(last, deps, cutoff).state
        if node.version:
            idents.add((node.name, node.version))

    if with_advisories:
        advisories_fact = advisory_query(session, sorted(idents))
    else:
        advisories_fact = Fact.skipped("advisory join disabled", source=osv.OSV_BATCH)
    advisories = advisories_fact.or_else({})

    # --- package rows, with the coverage join -------------------------------
    queued_names = inventory.queued_names()
    rows: dict[str, dict] = {}
    for key, node in tree.nodes.items():
        if key == tree.root_key:
            continue
        last = registry.last_release(node.name)
        verdict = classify(last, Fact.ok(list(node.deps)), cutoff)
        row = rows.setdefault(node.name, {
            "name": node.name,
            "versions": [],
            "state": State.ALIVE.value,
            "reason": "",
            "evidence": {},
            "ndeps": node.ndeps,
            "direct": False,
            "via": list(node.via),
            "depth": node.depth,
            "advisories": [],
            # `None`, not False. An unknown inventory must not render as
            # "not covered", which is a claim this build cannot make.
            "covered": None,
            "covered_by": None,
            "covered_match": "",
            "queued": (node.name in queued_names) if inventory.known else None,
        })
        if inventory.known:
            fork, how = inventory.covered_by(node.name)
            row["covered"] = fork is not None
            row["covered_by"] = fork
            row["covered_match"] = how
        if node.version and node.version not in row["versions"]:
            row["versions"].append(node.version)
        row["direct"] = row["direct"] or node.direct
        if node.depth is not None and (row["depth"] is None or node.depth < row["depth"]):
            row["depth"] = node.depth
            row["via"] = list(node.via)
        for adv in advisories.get(node.ident, []):
            if adv not in row["advisories"]:
                row["advisories"].append(adv)
        if SEVERITY[verdict.state] > SEVERITY[State(row["state"])]:
            row["state"] = verdict.state.value
            row["reason"] = verdict.reason
            row["evidence"] = verdict.evidence()
        elif not row["evidence"]:
            row["reason"] = verdict.reason
            row["evidence"] = verdict.evidence()
        row["versions"].sort()

    packages = sorted(rows.values(), key=lambda p: p["name"])

    # --- the adoption plan, from the same dominator machinery ---------------
    graph = ForkGraph.build(spec, tree, states)
    plan = [
        _plan_entry(c, inventory, queued_names)
        for c in build_queue({spec: graph}, advisories)
    ]

    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": "intake",
        "meta": {
            "spec": spec,
            "package": root_name,
            "root": root_name,
            "audited_date": today.isoformat(),
            "abandonment_days": abandonment_days,
            "builder_sha": builder_sha,
            "npm_available": npm_available(),
            "tier": "authoritative",
        },
        "tree": {"resolved": True, "reason": ""},
        "inventory": inventory.provenance(),
        "coverage": {
            "fetches": session.summary(),
            "advisories": {
                **advisories_fact.provenance(),
                "packages_with_advisories": len(advisories),
            },
        },
        "totals": _totals(packages, plan, inventory),
        "packages": packages,
        "plan": plan,
    }
    report["integrity"] = _integrity(report, packages, plan, inventory)
    return report


def _plan_entry(cand, inventory: "Inventory", queued: set[str]) -> dict:
    fork, how = inventory.covered_by(cand.name) if inventory.known else (None, "")
    if not inventory.known:
        action = None            # unknown, and rendered as unknown
    elif fork is not None:
        action = ALIAS
    elif cand.name in queued:
        action = QUEUED
    else:
        action = FORK
    return {
        "package": cand.name,
        "action": action,
        # The fork's real published name, read from the inventory. Synthesising
        # `SCOPE + name` printed `@unabandoned/JSONStream`, which does not exist.
        "fork": fork,
        "match": how,
        "state": cand.state.value,
        "versions": sorted(cand.versions),
        "clears": sorted(cand.clears),
        "clears_count": len(cand.clears),
        "score": cand.score(),
        "emergency": cand.emergency,
        "advisories": sorted(cand.advisories),
        "max_severity": cand.max_severity,
        "via": (cand.paths[0]["via"] if cand.paths else []),
    }


def _totals(packages: list[dict], plan: list[dict], inventory: Inventory) -> dict:
    by_state = {s.value: 0 for s in State}
    for row in packages:
        by_state[row["state"]] += 1
    rot = [p for p in packages if p["state"] in (State.TIME_BOMB.value, State.UNKNOWN.value)]
    totals = {
        "packages": len(packages),
        **by_state,
        "with_advisories": sum(1 for p in packages if p["advisories"]),
        "emergencies": sum(
            1 for p in packages
            if p["advisories"] and p["state"] == State.TIME_BOMB.value
        ),
        "rot": len(rot),
        "interventions": len(plan),
    }
    # Every count that depends on the join is absent rather than zero when the
    # join could not be made. A plan of "0 new forks" is the single most
    # dangerous number this report can print, so it is not printable by default.
    if inventory.known:
        totals["covered"] = sum(1 for p in packages if p["covered"])
        totals["uncovered"] = sum(1 for p in packages if p["covered"] is False)
        totals["needs_fork"] = sum(1 for s in plan if s["action"] == FORK)
        totals["needs_alias"] = sum(1 for s in plan if s["action"] == ALIAS)
        totals["already_queued"] = sum(1 for s in plan if s["action"] == QUEUED)
    return totals


def _integrity(report: dict, packages: list[dict], plan: list[dict],
               inventory: Inventory) -> dict:
    checks = [
        integrity.unknowns_are_accounted(packages, report["coverage"]["fetches"]),
        _coverage_join_known(inventory),
        _plan_clears_rot(packages, plan, known=inventory.known),
    ]
    return {
        "status": integrity.worst_status(checks),
        "checks": [c.to_json() for c in sorted(checks, key=lambda c: c.id)],
        "counts": integrity.counts(checks),
    }


def _coverage_join_known(inventory: Inventory) -> integrity.Check:
    """An unavailable inventory must not read as "nothing is covered"."""
    if inventory.known:
        prov = inventory.provenance()
        return integrity.Check(
            "intake.coverage-join", "M1", integrity.PASS,
            "The fork inventory was available to join against",
            f"joined against {prov['forks']} fork(s) and a {prov['queue']}-entry "
            f"queue from the observation built {prov['observation_built_at'] or 'unknown'}",
            {k: prov[k] for k in ("forks", "queue", "observation_built_at")},
        )
    return integrity.Check(
        "intake.coverage-join", "M1", integrity.FAIL,
        "The fork inventory was available to join against",
        f"could not read the org's fork inventory ({inventory.fact.detail}) — "
        "coverage and the adoption plan are unknown for this run, not empty",
        {"detail": inventory.fact.detail},
    )


def _plan_clears_rot(packages: list[dict], plan: list[dict], *, known: bool) -> integrity.Check:
    """Executing the whole plan must leave no time bomb standing.

    A conservation invariant in the M4 style. The plan is assembled from
    dominators, so a rotten package that no entry clears means either the
    dominator walk missed it or the plan dropped it — both silent, and both
    produce an adoption estimate that is too cheap.
    """
    rot = {
        p["name"] for p in packages
        if p["state"] in (State.TIME_BOMB.value, State.UNKNOWN.value)
    }
    if not rot:
        return integrity.Check(
            "intake.plan-clears-rot", "M4", integrity.PASS,
            "The adoption plan accounts for every rotten package",
            "no time bombs or unknowns in this tree — nothing to plan for",
            {"rot": 0, "unaccounted": []},
        )
    cleared = {step["package"] for step in plan}
    for step in plan:
        cleared.update(step["clears"])
    gap = sorted(rot - cleared)
    if gap:
        return integrity.Check(
            "intake.plan-clears-rot", "M4", integrity.FAIL,
            "The adoption plan accounts for every rotten package",
            f"{len(gap)} rotten package(s) are in the tree but in no plan entry — "
            f"e.g. {', '.join(gap[:5])}; the adoption estimate is too cheap",
            {"rot": len(rot), "unaccounted": gap[:20]},
        )
    return integrity.Check(
        "intake.plan-clears-rot", "M4", integrity.PASS,
        "The adoption plan accounts for every rotten package",
        f"all {len(rot)} rotten package(s) are cleared by the "
        f"{len(plan)} intervention(s)"
        + ("" if known else " (actions unknown — no inventory to classify them)"),
        {"rot": len(rot), "unaccounted": []},
    )


def _unresolved_report(spec, root_name, tree_fact, inventory, session, *,
                       today, abandonment_days, builder_sha) -> dict:
    """A spec that will not resolve is a reported failure, not an empty tree.

    The alternative — returning a report with zero packages — reads as "clean
    tree, adopt freely", which is the exact shape of lie this codebase exists to
    make unrepresentable.
    """
    check = integrity.Check(
        "intake.resolved", "M1", integrity.FAIL,
        "The audited spec resolved",
        f"could not resolve {spec}: {tree_fact.detail or tree_fact.status}",
        {"spec": spec, "detail": tree_fact.detail},
    )
    checks = [check, _coverage_join_known(inventory)]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "intake",
        "meta": {
            "spec": spec,
            "package": root_name,
            "root": spec,
            "audited_date": today.isoformat(),
            "abandonment_days": abandonment_days,
            "builder_sha": builder_sha,
            "npm_available": npm_available(),
            "tier": "authoritative",
        },
        "tree": {"resolved": False, "reason": tree_fact.detail or str(tree_fact.status)},
        "inventory": inventory.provenance(),
        "coverage": {
            "fetches": session.summary(),
            "advisories": {"packages_with_advisories": 0},
        },
        "totals": {"packages": 0, "rot": 0, "interventions": 0},
        "packages": [],
        "plan": [],
        "integrity": {
            "status": integrity.worst_status(checks),
            "checks": [c.to_json() for c in sorted(checks, key=lambda c: c.id)],
            "counts": integrity.counts(checks),
        },
    }


def index(reports_dir) -> list[dict]:
    """Summarise the committed reports, newest audit per spec first.

    Read for the dashboard's intake page only. It is a directory listing of
    audits of *other people's* trees; nothing here reaches the org's totals,
    and `build_core` — which is where that guarantee has to hold — never sees
    it. The page is assembled after the derivation, from `finish`'s output.
    """
    from pathlib import Path

    root = Path(reports_dir)
    if not root.is_dir():
        return []

    rows: list[dict] = []
    for spec_dir in sorted(root.iterdir()):
        if not spec_dir.is_dir():
            continue
        reports = sorted(p for p in spec_dir.glob("*.json"))
        if not reports:
            continue
        latest = reports[-1]
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A report we cannot read is listed as unreadable rather than
            # skipped. A silently shorter list is the failure mode this
            # repository exists to prevent, even on a page this minor.
            rows.append({
                "spec": spec_dir.name, "audited_at": "", "unreadable": True,
                "totals": {}, "integrity": "fail", "audits": len(reports),
                "href": f"{spec_dir.name}/index.html",
            })
            continue
        rows.append({
            "spec": data.get("meta", {}).get("spec", spec_dir.name),
            "audited_at": data.get("meta", {}).get("audited_at", ""),
            "unreadable": False,
            "resolved": data.get("tree", {}).get("resolved", False),
            "totals": data.get("totals", {}),
            "integrity": data.get("integrity", {}).get("status", "fail"),
            "audits": len(reports),
            "href": f"{spec_dir.name}/index.html",
        })
    rows.sort(key=lambda r: (r["audited_at"], r["spec"]), reverse=True)
    return rows


def report_path(spec: str, *, at: str, root: str = "reports") -> str:
    """`reports/<pkg>@<version>/<timestamp>.json`, per §7b.

    Note the directory: `reports/`, never `snapshots/`. The org build globs
    `snapshots/` for history, so an intake report landing there would enter the
    differ and the trend as though it were an observation of the org itself —
    which is precisely the "never merges into org aggregates" rule, enforced by
    where the bytes land rather than by remembering.
    """
    safe_spec = spec.replace("/", "%2F")
    safe_at = at.replace(":", "-")
    return f"{root.rstrip('/')}/{safe_spec}/{safe_at}.json"
