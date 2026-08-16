"""Compare two repositories' committed lockfiles.

The question this answers is the org's own thesis pointed outward: *a fork is
only worth carrying if the tree is actually healthier than what it forked.*
Given two repos, it says what the second one did to the first — what it added,
dropped, bumped, pinned, and what it replaced with a scoped republish of its
own, which is exactly the `@unabandoned/*` pattern seen from the outside.

Two properties make this cheap and exact where the rest of intake is neither:

* **No resolver.** Both sides already resolved and committed their answers.
* **No registry.** Every fact here comes from two files. Classification needs
  publish dates and is a separate, expensive step; this is not that step, and
  it deliberately reports nothing about health so it cannot imply it.

The sides are `baseline` and `subject`, not `upstream` and `fork`. Nothing here
verifies a fork relationship — the caller asserts the pairing by choosing what
to compare, and calling one side "upstream" would be recon inventing a fact.
"""
from __future__ import annotations

import re

from .lockfile import Dep, Lockfile

MAJOR, MINOR, PATCH = "major", "minor", "patch"
DOWNGRADE, CHANGED = "downgrade", "changed"

_NUM = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def _parts(version: str) -> tuple[int, int, int] | None:
    m = _NUM.match((version or "").lstrip("v"))
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def bump_kind(old: str, new: str) -> str:
    """Deliberately simple, and it says `changed` when it cannot tell.

    Prereleases and non-numeric versions are not ranked. Guessing an ordering
    for `2.0.0-rc.1` would produce a confident direction from a comparison that
    does not have one.
    """
    a, b = _parts(old), _parts(new)
    if a is None or b is None or a == b:
        return CHANGED
    if b < a:
        return DOWNGRADE
    if b[0] != a[0]:
        return MAJOR
    if b[1] != a[1]:
        return MINOR
    return PATCH


def basename(name: str) -> str:
    """`@thetechnetwork/composerize-ts` -> `composerize-ts`."""
    return name.split("/", 1)[1] if name.startswith("@") and "/" in name else name


def _scope(name: str) -> str:
    return name.split("/", 1)[0] if name.startswith("@") and "/" in name else ""


def compare(baseline: Lockfile, subject: Lockfile) -> dict:
    """Diff two lockfiles. Pure — no I/O, no registry, no clock."""
    a, b = baseline.direct, subject.direct

    added_names = sorted(set(b) - set(a))
    removed_names = sorted(set(a) - set(b))

    # --- replacements: a package swapped for a scoped republish of itself ----
    # `composerize-ts` leaving while `@thetechnetwork/composerize-ts` arrives is
    # not an unrelated add and drop, it is the same decision the `@unabandoned`
    # scope exists to make. Matched on the unscoped basename, which is the same
    # join `intake` uses for coverage, so the two agree on what "the same
    # package under a different owner" means.
    replaced: list[dict] = []
    by_base_removed = {basename(n): n for n in removed_names}
    by_base_added = {basename(n): n for n in added_names}
    for base in sorted(set(by_base_removed) & set(by_base_added)):
        was, now = by_base_removed[base], by_base_added[base]
        if was == now:
            continue
        replaced.append({
            "package": base,
            "was": was, "now": now,
            "was_version": a[was].version, "now_version": b[now].version,
            "into_scope": _scope(now), "out_of_scope": _scope(was),
            "via_alias": False,
        })
    # An alias keeps the manifest key and swaps the package underneath it —
    # `"buffer": "^5"` becoming `"buffer": "npm:@unabandoned/buffer@^6"`. The key
    # is unchanged on both sides, so nothing above sees it, and it is exactly the
    # adoption move this org makes. Reported as a replacement, which is what it is.
    for name in sorted(set(a) & set(b)):
        if a[name].package == b[name].package:
            continue
        replaced.append({
            "package": name,
            "was": a[name].package, "now": b[name].package,
            "was_version": a[name].version, "now_version": b[name].version,
            "into_scope": _scope(b[name].package),
            "out_of_scope": _scope(a[name].package),
            "via_alias": True,
        })

    swapped = {r["was"] for r in replaced} | {r["now"] for r in replaced}
    aliased_keys = {r["package"] for r in replaced if r.get("via_alias")}

    added = [_dep_row(b[n]) for n in added_names if n not in swapped]
    removed = [_dep_row(a[n]) for n in removed_names if n not in swapped]

    # --- version and pinning deltas on what both sides carry ----------------
    bumped: list[dict] = []
    pinning: list[dict] = []
    for name in sorted(set(a) & set(b)):
        old, new = a[name], b[name]
        # An alias swap is already reported as a replacement. Listing it again
        # as a version bump would compare two different packages' versions and
        # call the result a bump.
        if name in aliased_keys:
            continue
        if old.version != new.version and old.version and new.version:
            bumped.append({
                "package": name,
                "from": old.version, "to": new.version,
                "kind": bump_kind(old.version, new.version),
                "from_specifier": old.specifier, "to_specifier": new.specifier,
                "dev": new.dev,
            })
        if old.pinned != new.pinned:
            pinning.append({
                "package": name,
                "from": old.specifier, "to": new.specifier,
                # "Fix forward, don't pin" is the org's stated position, so the
                # direction is named rather than left for the reader to infer.
                "direction": "pinned" if new.pinned else "unpinned",
            })

    # --- the transitive picture ---------------------------------------------
    tree_added = sorted(set(subject.resolved) - set(baseline.resolved))
    tree_removed = sorted(set(baseline.resolved) - set(subject.resolved))
    multi_version = sorted(
        n for n, vs in subject.resolved.items() if len(vs) > 1
    )

    by_kind = {k: 0 for k in (MAJOR, MINOR, PATCH, DOWNGRADE, CHANGED)}
    for row in bumped:
        by_kind[row["kind"]] += 1

    return {
        "baseline": _side(baseline),
        "subject": _side(subject),
        "direct": {
            "added": added,
            "removed": removed,
            "replaced": replaced,
            "bumped": sorted(bumped, key=lambda r: (
                [MAJOR, DOWNGRADE, MINOR, PATCH, CHANGED].index(r["kind"]), r["package"])),
            "pinning": pinning,
        },
        "tree": {
            "added": tree_added,
            "removed": tree_removed,
            "multi_version": multi_version,
        },
        "totals": {
            "direct_baseline": len(a),
            "direct_subject": len(b),
            "added": len(added),
            "removed": len(removed),
            "replaced": len(replaced),
            "bumped": len(bumped),
            **{f"bumped_{k}": v for k, v in by_kind.items()},
            "pinning": len(pinning),
            "tree_baseline": baseline.total_packages,
            "tree_subject": subject.total_packages,
            "tree_added": len(tree_added),
            "tree_removed": len(tree_removed),
            "multi_version": len(multi_version),
        },
    }


def _side(lf: Lockfile) -> dict:
    return {
        "tool": lf.tool,
        "lockfile_version": lf.lockfile_version,
        "direct": len(lf.direct),
        "runtime": len(lf.runtime),
        "packages": lf.total_packages,
        "pinned": sum(1 for d in lf.direct.values() if d.pinned),
    }


def _dep_row(dep: Dep) -> dict:
    return {
        "package": dep.name, "version": dep.version,
        "specifier": dep.specifier, "dev": dep.dev, "pinned": dep.pinned,
    }


def headline(diff: dict) -> str:
    """One sentence a human can check against the tables below it."""
    t = diff["totals"]
    bits = []
    if t["added"]:
        bits.append(f"{t['added']} added")
    if t["removed"]:
        bits.append(f"{t['removed']} dropped")
    if t["replaced"]:
        bits.append(f"{t['replaced']} replaced with a scoped republish")
    if t["bumped"]:
        majors = t[f"bumped_{MAJOR}"]
        bits.append(
            f"{t['bumped']} bumped" + (f" ({majors} major)" if majors else "")
        )
    if t[f"bumped_{DOWNGRADE}"]:
        bits.append(f"{t[f'bumped_{DOWNGRADE}']} downgraded")
    if t["pinning"]:
        bits.append(f"{t['pinning']} pinning change(s)")
    if not bits:
        return "The two manifests declare identical direct dependencies."
    delta = t["tree_subject"] - t["tree_baseline"]
    sign = "+" if delta > 0 else ""
    return (
        "Against the baseline, the subject has "
        + ", ".join(bits)
        + f" — and {sign}{delta} package(s) in the resolved tree "
          f"({t['tree_baseline']} → {t['tree_subject']})."
    )


# --------------------------------------------------------------------------- #
# The report of record
# --------------------------------------------------------------------------- #
def build_report(baseline_fact, subject_fact, *, baseline_ref: str, subject_ref: str,
                 session, compared_at: str = "", builder_sha: str = "") -> dict:
    """Assemble the comparison, or a report that says why there isn't one."""
    from . import integrity

    sides = {
        "baseline": {"input": baseline_ref, **baseline_fact.provenance()},
        "subject": {"input": subject_ref, **subject_fact.provenance()},
    }
    checks = [_both_sides_read(baseline_fact, subject_fact, sides)]

    report = {
        "schema_version": 1,
        "kind": "compare",
        "meta": {
            "baseline": baseline_ref, "subject": subject_ref,
            "compared_at": compared_at, "builder_sha": builder_sha,
        },
        "sides": sides,
        "coverage": {"fetches": session.summary()},
    }

    if baseline_fact.is_ok and subject_fact.is_ok:
        a, b = baseline_fact.payload, subject_fact.payload
        report["diff"] = compare(a, b)
        report["headline"] = headline(report["diff"])
        checks.append(_same_tool(a, b))
    else:
        # No diff at all, rather than an empty one. An empty diff renders as
        # "these repositories are identical", which is the most confidently
        # wrong thing this report could say.
        report["diff"] = None
        report["headline"] = ""

    report["integrity"] = {
        "status": integrity.worst_status(checks),
        "checks": [c.to_json() for c in sorted(checks, key=lambda c: c.id)],
        "counts": integrity.counts(checks),
    }
    return report


def _both_sides_read(baseline_fact, subject_fact, sides) -> "object":
    from . import integrity
    unread = [
        name for name, fact in (("baseline", baseline_fact), ("subject", subject_fact))
        if not fact.is_ok
    ]
    if unread:
        detail = "; ".join(
            f"{n}: {sides[n].get('detail') or 'unread'}" for n in unread
        )
        return integrity.Check(
            "compare.both-sides-read", "M1", integrity.FAIL,
            "Both lockfiles were read",
            f"{len(unread)} side(s) could not be read — {detail}. No comparison is "
            "reported, because an empty diff reads as 'identical'.",
            {"unread": unread},
        )
    return integrity.Check(
        "compare.both-sides-read", "M1", integrity.PASS,
        "Both lockfiles were read",
        f"baseline {sides['baseline'].get('source', '')} and "
        f"subject {sides['subject'].get('source', '')}",
        {},
    )


def _same_tool(a, b) -> "object":
    from . import integrity
    if a.tool == b.tool:
        return integrity.Check(
            "compare.same-package-manager", "M2", integrity.PASS,
            "Both sides were resolved by the same package manager",
            f"both {a.tool} (lockfile {a.lockfile_version} / {b.lockfile_version})",
            {"tool": a.tool},
        )
    # Direct dependencies stay comparable — they come from the manifest either
    # way. The resolved trees do not: npm and pnpm hoist and dedupe differently,
    # so the package counts measure the tools as much as the projects.
    return integrity.Check(
        "compare.same-package-manager", "M2", integrity.WARN,
        "Both sides were resolved by the same package manager",
        f"baseline uses {a.tool} and subject uses {b.tool} — direct dependencies "
        "remain comparable, but the resolved-tree counts partly measure the "
        "difference between the two resolvers rather than between the projects",
        {"baseline": a.tool, "subject": b.tool},
    )
