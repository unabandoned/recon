"""The checks that make a wrong build fail loudly instead of rendering nicely.

Every bug in this tool's history was caught the same way: someone noticed the
output contradicted something they independently knew. None was caught by
reading the page, because wrong output here is *plausible* — a smaller time-bomb
count, a graph with no internal edges, an issue total with a floor of one.

So the build carries its own contradictions-in-waiting. Each check below is a
statement about what the world cannot look like, phrased so that a bug of a
known class trips it.

    M1  errors are a state, never a default        — a failed fetch must reach
                                                     the page as `unknown`
    M2  independent double-derivation              — two witnesses for the same
                                                     fact, one of them npm itself
    M3  ground-truth fixtures                      — facts a human asserted that
                                                     the build must reproduce
    M4  shape and uniformity invariants            — cheap statements about
                                                     impossible worlds
    M5  differential vs the previous snapshot      — catches regressions of all
                                                     of the above

Checks are values, not exceptions: every one runs, and the build reports the
whole set. A check that could only ever run in isolation would tell you the
first thing wrong instead of everything wrong, and debugging by contradiction
wants the full picture.

Severity is deliberate. `fail` means a number on the page is probably wrong.
`warn` means something is odd and worth a human glance but is not evidence of
miscounting. Only `fail` gates publication.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .classify import State

PASS = "pass"
WARN = "warn"
FAIL = "fail"


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    mechanism: str
    status: str
    title: str
    detail: str
    data: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        out = {
            "id": self.id,
            "mechanism": self.mechanism,
            "status": self.status,
            "title": self.title,
            "detail": self.detail,
        }
        if self.data:
            out["data"] = self.data
        return out


def worst_status(checks: Iterable[Check]) -> str:
    order = {PASS: 0, WARN: 1, FAIL: 2}
    return max((c.status for c in checks), key=lambda s: order[s], default=PASS)


def counts(checks: Iterable[Check]) -> dict[str, int]:
    """The per-status tally every integrity block carries."""
    checks = list(checks)
    return {s: sum(1 for c in checks if c.status == s) for s in (PASS, WARN, FAIL)}


# --------------------------------------------------------------------------- #
# M1 — errors are a state, never a default
# --------------------------------------------------------------------------- #
def unknowns_are_accounted(packages: list[dict], fetch_summary: dict) -> Check:
    """Every `unknown` carries a reason, and no failed fetch vanished silently.

    The failure this catches is the original one: a registry read fails, the
    handler returns a benign default, and the package classifies as healthy. If
    that happens now, a failed fetch exists with no `unknown` to show for it.
    """
    unknown = [p for p in packages if p["state"] == State.UNKNOWN.value]
    reasonless = [p["name"] for p in unknown if not p.get("reason")]
    failed_fetches = fetch_summary.get("failed", 0)

    if reasonless:
        return Check(
            "m1.unknowns-have-reasons", "M1", FAIL,
            "Unknown classifications carry their reason",
            f"{len(reasonless)} package(s) classified unknown with no reason recorded: "
            + ", ".join(sorted(reasonless)[:5]),
            {"packages": sorted(reasonless)[:20]},
        )

    # A failed fetch with zero unknowns means a failure path found a way to
    # produce a confident answer anyway — exactly the shape of bug 1a.
    if failed_fetches and not unknown:
        return Check(
            "m1.unknowns-have-reasons", "M1", FAIL,
            "Failed fetches produce unknowns",
            f"{failed_fetches} fetch(es) failed but no package classified unknown — "
            "a failure path is collapsing into a confident answer",
            {"failed_fetches": failed_fetches},
        )

    return Check(
        "m1.unknowns-have-reasons", "M1", PASS,
        "Failed fetches produce unknowns",
        f"{len(unknown)} unknown package(s), {failed_fetches} failed fetch(es); "
        "every unknown carries its reason",
        {"unknown": len(unknown), "failed_fetches": failed_fetches},
    )


# --------------------------------------------------------------------------- #
# M2 — independent double-derivation
# --------------------------------------------------------------------------- #
def scope_edges_agree(per_fork: dict[str, dict]) -> Check:
    """Manifest-derived scope edges must equal lockfile-derived scope edges.

    Reader A parses `package.json` — including the alias syntax where the scope
    lives in the *value*, not the key. Reader B reads the resolved lockfile,
    which npm produced with its own alias handling.

    This is the highest-value single check here, because Reader B is a genuinely
    independent implementation rather than a second copy of our own parsing. The
    bug that made every fork->fork edge invisible lived in Reader A's territory;
    Reader B would have shown the real edges and this check would have failed the
    build instead of rendering a graph of isolated nodes.
    """
    disagreements = []
    compared = 0
    for fork, data in sorted(per_fork.items()):
        manifest = data.get("manifest_edges")
        lock = data.get("lockfile_edges")
        if manifest is None or lock is None:
            continue  # one of the two readers had nothing to read; coverage's problem
        compared += 1
        a, b = set(manifest), set(lock)
        if a != b:
            disagreements.append({
                "fork": fork,
                "manifest_only": sorted(a - b),
                "lockfile_only": sorted(b - a),
            })

    if disagreements:
        first = disagreements[0]
        return Check(
            "m2.scope-edges-agree", "M2", FAIL,
            "Manifest and lockfile agree on @unabandoned edges",
            f"{len(disagreements)} fork(s) disagree — e.g. {first['fork']}: "
            f"manifest-only {first['manifest_only'] or '[]'}, "
            f"lockfile-only {first['lockfile_only'] or '[]'}",
            {"disagreements": disagreements[:20], "compared": compared},
        )
    return Check(
        "m2.scope-edges-agree", "M2", PASS,
        "Manifest and lockfile agree on @unabandoned edges",
        f"{compared} fork(s) cross-checked; both readers agree",
        {"compared": compared},
    )


def dependency_counts_agree(observations: list[dict]) -> Check:
    """Registry-declared dependencies must match the lockfile's for the same version.

    Two derivations of one fact, from two artifacts. The lockfile is npm's
    resolution of the very packument we also read directly, so they can only
    disagree if one of our readings is wrong — which is the bug class worth
    failing over.

    Only `(name, version)` pairs where *both* readings succeeded are compared;
    a failed packument read is M1's business, not this check's.
    """
    mismatches = []
    compared = 0
    for obs in observations:
        registry = obs.get("registry_deps")
        lock = obs.get("lockfile_deps")
        if registry is None or lock is None:
            continue
        compared += 1
        if set(registry) != set(lock):
            mismatches.append({
                "ident": obs["ident"],
                "registry_only": sorted(set(registry) - set(lock)),
                "lockfile_only": sorted(set(lock) - set(registry)),
            })

    if mismatches:
        first = mismatches[0]
        return Check(
            "m2.dependency-counts-agree", "M2", FAIL,
            "Registry and lockfile agree on declared dependencies",
            f"{len(mismatches)} of {compared} package version(s) disagree — e.g. "
            f"{first['ident']}: registry-only {first['registry_only'] or '[]'}, "
            f"lockfile-only {first['lockfile_only'] or '[]'}",
            {"mismatches": mismatches[:20], "compared": compared},
        )
    return Check(
        "m2.dependency-counts-agree", "M2", PASS,
        "Registry and lockfile agree on declared dependencies",
        f"{compared} package version(s) cross-checked; both derivations agree",
        {"compared": compared},
    )


# --------------------------------------------------------------------------- #
# M3 — ground-truth fixtures
# --------------------------------------------------------------------------- #
def expected_siblings_present(
    per_fork: dict[str, dict], asserted: list[dict] | None = None
) -> Check:
    """Every hand-asserted sibling edge must appear in the derived graph.

    These are facts a human wrote down in `fixtures/org.yml`. The build has to
    reproduce them. This is the mechanism that turns "someone happened to notice
    the graph looked wrong" into something that happens every night.

    An empty fixture set is a **warning, not a pass**. The first version of this
    check returned PASS when nothing was asserted, and since the assertions then
    lived in each fork's own metadata — twenty-seven repositories, twenty-seven
    pull requests — nothing ever was. It reported green for its whole life while
    verifying zero of thirty-two real edges. A check that cannot fail is worse
    than no check: it occupies the slot where a real one would go.
    """
    derived_total = sum(
        len(set(d.get("manifest_edges") or []) | set(d.get("lockfile_edges") or []))
        for d in per_fork.values()
    )
    asserted = asserted or []

    if not asserted:
        return Check(
            "m3.expected-siblings", "M3", WARN,
            "Hand-asserted sibling edges appear in the derived graph",
            f"no sibling edge is asserted in fixtures/org.yml — the build derived "
            f"{derived_total} fork-to-fork edge(s) and nothing independent says any "
            f"of them is right",
            {"asserted": 0, "derived": derived_total},
        )

    problems: list[dict] = []
    checked = 0
    for item in asserted:
        fork = item.get("fork", "")
        want = sorted(set(item.get("declares") or []))
        data = per_fork.get(fork)
        if data is None:
            problems.append({
                "fork": fork, "missing": want, "derived": [],
                "reason": "no such fork in this build",
            })
            continue
        derived = set(data.get("lockfile_edges") or []) | set(data.get("manifest_edges") or [])
        checked += len(want)
        gap = sorted(set(want) - derived)
        if gap:
            problems.append({
                "fork": fork, "missing": gap, "derived": sorted(derived),
                "reason": "edge not derived",
            })

    if problems:
        first = problems[0]
        return Check(
            "m3.expected-siblings", "M3", FAIL,
            "Hand-asserted sibling edges appear in the derived graph",
            f"{len(problems)} assertion(s) are not reproduced — e.g. {first['fork']} "
            f"expects {', '.join(first['missing'])} but the build derived "
            f"{', '.join(first['derived']) or 'no scope edges at all'}",
            {"missing": problems[:20], "asserted": checked, "derived": derived_total},
        )
    return Check(
        "m3.expected-siblings", "M3", PASS,
        "Hand-asserted sibling edges appear in the derived graph",
        f"{checked} of {derived_total} derived edge(s) are hand-asserted, and the "
        f"build reproduced every one",
        {"asserted": checked, "derived": derived_total},
    )


def org_fixtures_hold(fixtures: dict, observed: dict) -> list[Check]:
    """Cross-fork facts with no single home, asserted in the recon repo.

    This file is hand-written and never generated, so it is editorial rather
    than a recorded copy of derivable state. That distinction matters: it is not
    a cache of what the build found, it is *underivable human knowledge used to
    audit the derivation*, which is the opposite of the thing the no-registry
    rule prohibits.
    """
    checks: list[Check] = []

    # -- reachability assertions: "fork X reaches package Y only via Z"
    for i, item in enumerate(fixtures.get("paths") or []):
        fork = item.get("fork")
        package = item.get("package")
        want_via = item.get("via")
        routes = observed.get("routes", {}).get((fork, package))
        if routes is None:
            checks.append(Check(
                f"m3.path.{i}", "M3", FAIL,
                "Asserted dependency path holds",
                f"{fork} was asserted to reach {package}"
                + (f" via {' -> '.join(want_via)}" if want_via else "")
                + ", but the build found no such path",
                {"fork": fork, "package": package, "expected_via": want_via},
            ))
            continue
        if want_via is not None and list(want_via) not in [list(r) for r in routes]:
            checks.append(Check(
                f"m3.path.{i}", "M3", FAIL,
                "Asserted dependency path holds",
                f"{fork} reaches {package}, but not via {' -> '.join(want_via)} — "
                f"found {routes}",
                {"fork": fork, "package": package,
                 "expected_via": want_via, "observed": [list(r) for r in routes]},
            ))
            continue
        checks.append(Check(
            f"m3.path.{i}", "M3", PASS,
            "Asserted dependency path holds",
            f"{fork} reaches {package}"
            + (f" via {' -> '.join(want_via)}" if want_via else ""),
            {"fork": fork, "package": package},
        ))

    # -- counted assertions: "repo X has exactly N real open issues"
    for i, item in enumerate(fixtures.get("counts") or []):
        metric = item.get("metric")
        subject = item.get("subject")
        want = item.get("equals")
        got = observed.get("counts", {}).get((metric, subject))
        if got is None:
            checks.append(Check(
                f"m3.count.{i}", "M3", WARN,
                "Asserted count holds",
                f"no observed value for {metric} of {subject} — fixture may be stale",
                {"metric": metric, "subject": subject},
            ))
        elif got != want:
            checks.append(Check(
                f"m3.count.{i}", "M3", FAIL,
                "Asserted count holds",
                f"{metric} of {subject}: asserted {want}, observed {got}",
                {"metric": metric, "subject": subject, "expected": want, "observed": got},
            ))
        else:
            checks.append(Check(
                f"m3.count.{i}", "M3", PASS,
                "Asserted count holds",
                f"{metric} of {subject} = {want}",
                {"metric": metric, "subject": subject},
            ))

    if not checks:
        checks.append(Check(
            "m3.org-fixtures", "M3", WARN,
            "Reachability and counted fixtures are asserted",
            "no path or count fixtures defined — sibling edges say how the forks "
            "are wired, but nothing asserts how a package is reached through that "
            "wiring, or how many of anything there should be",
            {},
        ))
    return checks


# --------------------------------------------------------------------------- #
# M4 — shape and uniformity invariants
# --------------------------------------------------------------------------- #
def fork_edge_floor(edge_count: int, fork_count: int, floor: int) -> Check:
    """An org of sibling-wired forks with (almost) no internal edges is wrong.

    Cheap, blunt, and it alone catches the symptom of the topology bug: a graph
    that rendered every fork as an isolated node.
    """
    if fork_count < 2:
        return Check(
            "m4.edge-floor", "M4", PASS, "Fork graph has internal edges",
            f"{fork_count} fork(s) — too few for the floor to mean anything",
            {"edges": edge_count, "forks": fork_count},
        )
    if edge_count < floor:
        return Check(
            "m4.edge-floor", "M4", FAIL, "Fork graph has internal edges",
            f"only {edge_count} fork->fork edge(s) across {fork_count} forks "
            f"(floor {floor}) — the graph is probably not being derived correctly",
            {"edges": edge_count, "forks": fork_count, "floor": floor},
        )
    return Check(
        "m4.edge-floor", "M4", PASS, "Fork graph has internal edges",
        f"{edge_count} fork->fork edge(s) across {fork_count} fork(s) (floor {floor})",
        {"edges": edge_count, "forks": fork_count, "floor": floor},
    )


def consumers_are_named(consumer_edges: list[dict], fork_packages: set[str]) -> Check:
    """Something outside this org has to appear in `used-by`, or nothing does.

    The org exists because *our projects* depend on these packages; the forks are
    where those dependencies are parked so they do not bury the organization the
    projects live in. So the question the whole thing is for — which of our
    projects is standing under this rot — is answered by `used-by` and nothing
    else derives it.

    Every `used-by` entry currently names a **sibling fork**, which the resolver
    already derives from `package.json`. That is not wrong, it is just not the
    fact this field exists to carry, and the failure is silent in the worst way:
    the topology draws no external consumers and reads as "no project depends on
    these" rather than "nobody wrote down which ones do". Same shape as a failed
    fetch becoming a zero, aimed at the one number the org is actually about.
    """
    external = sorted({
        e["from"] for e in consumer_edges if e.get("from") not in fork_packages
    })
    internal = sum(1 for e in consumer_edges if e.get("from") in fork_packages)
    evidence = {
        "external_consumers": len(external),
        "sibling_edges": internal,
        "named": external[:20],
    }
    title = "Some consumer outside the org is named in `used-by`"

    if not external:
        return Check(
            "m4.consumers-named", "M4", WARN, title,
            f"no repository outside this org appears in any fork's `used-by` — "
            f"{internal} entr(y/ies) name a sibling fork, which the resolver already "
            f"derives. Which of our projects reach these trees is unrecorded, not zero",
            evidence,
        )
    return Check(
        "m4.consumers-named", "M4", PASS, title,
        f"{len(external)} consumer(s) outside the org named across {len(consumer_edges)} "
        f"`used-by` entr(y/ies)",
        evidence,
    )


def uniformity(metric: str, values: dict[str, Any], *, minimum: int = 4) -> Check:
    """A per-repo metric identical on every repo is usually a counted artifact.

    This is the shape of the issue-count bug: Renovate's always-open dashboard
    put a floor of exactly one under every fork, and the tell was that the number
    was the *same everywhere*, not that it was large.
    """
    if len(values) < minimum:
        return Check(
            f"m4.uniformity.{metric}", "M4", PASS, f"`{metric}` is not suspiciously uniform",
            f"only {len(values)} repo(s) — too few to judge", {"metric": metric},
        )
    distinct = set(values.values())
    if len(distinct) == 1:
        only = next(iter(distinct))
        status = PASS if only in (0, None) else WARN
        detail = (
            f"every one of {len(values)} repos reports {metric} = {only}"
            + ("" if status == PASS else " — uniform non-zero signals are usually an artifact")
        )
        return Check(
            f"m4.uniformity.{metric}", "M4", status,
            f"`{metric}` is not suspiciously uniform", detail,
            {"metric": metric, "value": only, "repos": len(values)},
        )

    # A hard floor is the subtler version of the same artifact, and the one that
    # actually happened: the values varied, so uniformity alone said nothing, but
    # no repo could ever reach zero because one item was being counted everywhere.
    floor = min(values.values())
    if floor > 0:
        return Check(
            f"m4.uniformity.{metric}", "M4", WARN,
            f"`{metric}` is not suspiciously uniform",
            f"no repo reports fewer than {floor} for {metric} — a hard non-zero floor "
            "across every repo usually means something is being counted that should not be",
            {"metric": metric, "floor": floor, "repos": len(values)},
        )
    return Check(
        f"m4.uniformity.{metric}", "M4", PASS, f"`{metric}` is not suspiciously uniform",
        f"{len(distinct)} distinct value(s) across {len(values)} repo(s)",
        {"metric": metric, "distinct": len(distinct)},
    )


def conservation(coverage: dict) -> Check:
    """discovered = included + excluded, and every exclusion carries a reason.

    The ledger has to add up, or the denominators on the page are fiction.
    """
    repos = coverage.get("repos", {})
    discovered = repos.get("discovered", 0)
    included = repos.get("included", 0)
    excluded = repos.get("excluded", []) or []
    reasonless = [e.get("repo") for e in excluded if not e.get("reason")]

    if reasonless:
        return Check(
            "m4.conservation", "M4", FAIL, "The coverage ledger balances",
            f"{len(reasonless)} excluded repo(s) carry no reason: "
            + ", ".join(str(r) for r in reasonless[:5]),
            {"reasonless": reasonless[:20]},
        )
    if discovered != included + len(excluded):
        return Check(
            "m4.conservation", "M4", FAIL, "The coverage ledger balances",
            f"discovered {discovered} != included {included} + excluded {len(excluded)}",
            {"discovered": discovered, "included": included, "excluded": len(excluded)},
        )
    return Check(
        "m4.conservation", "M4", PASS, "The coverage ledger balances",
        f"{discovered} discovered = {included} included + {len(excluded)} excluded, "
        "every exclusion reasoned",
        {"discovered": discovered, "included": included, "excluded": len(excluded)},
    )


# `m4.zero-dep-sanity` was here and has been removed rather than tuned.
#
# It flagged a package whose resolved version declares no dependencies while its
# `latest` declares some. On the first real build that fired on `buffer-xor@1.0.3`,
# which has no dependencies, against a 2.x `latest` that does — a package gaining
# dependencies in a later major is ordinary, not suspicious, so the check had no
# discriminating power and would have warned forever.
#
# The concern it was reaching for is real, but `m2.dependency_counts_agree` is the
# well-formed version of it: that compares the SAME (name, version) across two
# independent artifacts, which can only disagree if one of our readings is wrong,
# and it fails hard instead of warning. A check that fires on normal reality
# teaches people to skim past the panel, which costs more than it can ever catch.


# --------------------------------------------------------------------------- #
# M5 — differential vs the previous snapshot
# --------------------------------------------------------------------------- #
DEFAULT_THRESHOLDS = {
    "time_bomb": 0.20,
    "edges": 0.30,
    "packages": 0.25,
    "unknown": 1.00,
    "open_issues": 0.50,
}


def differential(
    current: dict[str, int],
    previous: dict[str, int] | None,
    *,
    thresholds: dict[str, float] | None = None,
    acknowledged: set[str] | None = None,
) -> Check:
    """Block on an unexplained swing in any headline aggregate.

    Catches regressions of every other mechanism, and the subtler failure where
    a "fix" silently changes what a number *means* while the output stays
    plausible. An intended jump is acknowledged explicitly (see
    `RECON_ACK_DELTA`), which forces the change to be named by a human once.
    """
    if previous is None:
        return Check(
            "m5.differential", "M5", PASS, "Aggregates moved plausibly since the last build",
            "no previous snapshot — nothing to compare against yet", {},
        )

    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    acknowledged = acknowledged or set()
    swings = []
    for key, limit in sorted(thresholds.items()):
        now = current.get(key)
        was = previous.get(key)
        if now is None or was is None:
            continue
        if was == 0:
            moved = 1.0 if now else 0.0
        else:
            moved = abs(now - was) / was
        if moved > limit:
            swings.append({
                "metric": key, "previous": was, "current": now,
                "change": round(moved, 3), "threshold": limit,
                "acknowledged": key in acknowledged,
            })

    unacknowledged = [s for s in swings if not s["acknowledged"]]
    if unacknowledged:
        first = unacknowledged[0]
        return Check(
            "m5.differential", "M5", FAIL,
            "Aggregates moved plausibly since the last build",
            f"{len(unacknowledged)} aggregate(s) swung past their threshold — "
            f"{first['metric']} {first['previous']} -> {first['current']} "
            f"({first['change']:.0%} > {first['threshold']:.0%}). Confirm with "
            f"RECON_ACK_DELTA={first['metric']} if the change is real.",
            {"swings": swings},
        )
    if swings:
        return Check(
            "m5.differential", "M5", WARN,
            "Aggregates moved plausibly since the last build",
            f"{len(swings)} large but acknowledged change(s): "
            + ", ".join(f"{s['metric']} {s['previous']}->{s['current']}" for s in swings),
            {"swings": swings},
        )
    return Check(
        "m5.differential", "M5", PASS, "Aggregates moved plausibly since the last build",
        "no aggregate moved past its threshold", {"compared": len(thresholds)},
    )


# --------------------------------------------------------------------------- #
# Reproducibility — the invariant that keeps history from becoming an input
# --------------------------------------------------------------------------- #
def snapshot_independence(with_history: str, without_history: str) -> Check:
    """The current build must be identical with the snapshot directory deleted.

    Snapshots are a record of the past, which is not derivable later — that is
    why keeping them does not violate "never record derivable state". The rule's
    spirit survives on one condition: they are write-only from the derivation's
    point of view. Only the differ and the trend renderer may read them.

    Rather than trusting module discipline, the build re-derives the observation
    with history masked and compares. The facts are already fetched, so the
    second derivation costs no network and a few milliseconds.
    """
    if with_history == without_history:
        return Check(
            "repro.snapshot-independence", "repro", PASS,
            "Current state does not depend on history",
            "re-deriving with the snapshot directory masked produced identical output",
            {"bytes": len(with_history)},
        )
    # Find the first divergence so the failure is actionable rather than "differs".
    at = next(
        (i for i, (a, b) in enumerate(zip(with_history, without_history)) if a != b),
        min(len(with_history), len(without_history)),
    )
    return Check(
        "repro.snapshot-independence", "repro", FAIL,
        "Current state does not depend on history",
        f"re-deriving with history masked changed the output at byte {at} — "
        "something in the current-state derivation is reading a snapshot",
        {"diverges_at": at,
         "with": with_history[max(0, at - 60):at + 60],
         "without": without_history[max(0, at - 60):at + 60]},
    )
