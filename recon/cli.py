"""Build the site, write the snapshot, report the integrity verdict.

    python -m recon.cli build      # derive, render, snapshot
    python -m recon.cli verify     # exit non-zero if a published build failed a check
    python -m recon.cli intake SPEC  # audit a foreign tree (§7b, authoritative tier)
    python -m recon.cli compare A B  # diff two repos' committed lockfiles

The split matters for the publish decision. `build` always writes output, even
when a check fails, because a visibly broken dashboard gets fixed and a silently
stale one does not — the page carries a red "these numbers are not trustworthy"
banner instead of quietly serving yesterday's. `verify` then turns that into a
red CI run *after* the deploy, so the site is honest and the job is loud.

Environment:
    GITHUB_TOKEN / GH_TOKEN   GitHub API token (public reads; no write scope needed)
    RECON_ORG                 org to scan (default: unabandoned)
    RECON_OUT                 output directory (default: public)
    RECON_SNAPSHOTS           snapshot directory (default: snapshots)
    RECON_FIXTURES            org fixture file (default: fixtures/org.yml)
    RECON_ACK_DELTA           comma-separated aggregates whose jump is intended
    RECON_NO_ADVISORIES       set to skip the OSV join
    RECON_NO_SNAPSHOT         set to build without writing a snapshot
    RECON_REPORTS             intake report directory (default: reports)
    RECON_COMPARISONS         compare report directory (default: comparisons)
    RECON_ABANDONMENT_DAYS    intake abandonment threshold in days
    RECON_INVENTORY           observation to join intake against (default: public/observation.json)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import compare as compare_mod
from . import fixtures as fixtures_mod
from . import intake as intake_mod
from . import lockfile as lockfile_mod
from . import observation as obs_mod
from . import scenario as scenario_mod
from . import snapshots
from .classify import DEFAULT_ABANDONMENT_DAYS
from .github import GitHub
from .http import Session
from .integrity import FAIL, WARN
from .registry import Registry
from .render import llms as llms_mod
from .render import pages


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _builder_sha() -> str:
    for env in ("GITHUB_SHA", "RECON_SHA"):
        if os.environ.get(env):
            return os.environ[env]
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def build(args) -> int:
    started = time.monotonic()
    org = os.environ.get("RECON_ORG", "unabandoned")
    out = Path(os.environ.get("RECON_OUT", "public"))
    snap_dir = Path(os.environ.get(obs_mod.SNAPSHOT_ENV, "snapshots"))
    fixture_path = Path(os.environ.get("RECON_FIXTURES", "fixtures/org.yml"))
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    acknowledged = {
        s.strip() for s in os.environ.get("RECON_ACK_DELTA", "").split(",") if s.strip()
    }

    fixture_data, fixture_errors = fixtures_mod.load(fixture_path)
    if fixture_errors:
        for err in fixture_errors:
            sys.stderr.write(f"error: {err}\n")
        return 2

    session = Session(clock=_utc_now)
    github = GitHub(session, org=org, token=token)
    registry = Registry(session)

    inputs = obs_mod.gather(
        github, registry, session,
        org=org,
        today=datetime.date.today(),
        builder_sha=_builder_sha(),
        with_advisories=not os.environ.get("RECON_NO_ADVISORIES"),
    )
    if not inputs.discovery.forks and not inputs.discovery_fact.is_ok:
        sys.stderr.write(
            f"error: could not list repositories for {org}: {inputs.discovery_fact.detail}\n"
        )
        return 2

    core = obs_mod.build_core(inputs)

    # History is read here and nowhere upstream of it. `rederive_with_history_masked`
    # re-runs the derivation with the snapshot directory pointed at an empty
    # temp dir and the check compares the two — so a future edit that makes
    # current state depend on the past fails the build that introduces it.
    previous = snapshots.latest(snap_dir)
    rederived = obs_mod.rederive_with_history_masked(inputs, snap_dir)

    checks = obs_mod.run_checks(
        core, fixtures=fixture_data, previous=previous,
        acknowledged=acknowledged, rederived=rederived,
    )

    built_at = _utc_now()
    duration_ms = int((time.monotonic() - started) * 1000)
    observation = obs_mod.finish(core, checks, built_at=built_at, duration_ms=duration_ms)

    delta = snapshots.diff(previous, observation, org=org)
    trend = snapshots.trend(snap_dir)

    # Committed intake reports, listed on the intake tab. Read here rather than
    # anywhere upstream: `build_core` must not be able to see them, or an audit
    # of somebody else's tree could reach the org's own numbers.
    reports = intake_mod.index(Path(os.environ.get("RECON_REPORTS", "reports")))

    out.mkdir(parents=True, exist_ok=True)
    for name, html in pages.render_all(observation, delta, trend, reports).items():
        (out / name).write_text(html, encoding="utf-8")
    (out / "observation.json").write_text(obs_mod.canonical(observation), encoding="utf-8")
    (out / "changes.json").write_text(obs_mod.canonical(delta), encoding="utf-8")
    # The site, written for an agent: a map to `observation.json`, the program's
    # rules, and where the hosted onboarding instructions live. Derived from the
    # same observation, so it cannot describe a site that no longer exists.
    (out / "llms.txt").write_text(llms_mod.render(observation, reports), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    if not os.environ.get("RECON_NO_SNAPSHOT"):
        written = snapshots.write(snap_dir, observation, at=built_at)
        print(f"snapshot: {written}")

    _report(observation, duration_ms)
    return 0


def _report(observation: dict, duration_ms: int) -> None:
    totals = observation["totals"]
    integ = observation["integrity"]
    cov = observation["coverage"]
    print(
        f"built {totals['forks']} fork(s), {totals['packages']} package(s) in "
        f"{duration_ms / 1000:.1f}s — "
        f"{totals['time_bomb']} time bomb(s), {totals['unknown']} unknown, "
        f"{totals['emergencies']} emergency(ies), {totals['edges']} fork edge(s)"
    )
    print(
        f"coverage: {cov['repos']['included']}/{cov['repos']['discovered']} repos, "
        f"{cov['trees']['resolved']} tree(s) resolved, "
        f"{cov['fetches']['failed']}/{cov['fetches']['attempted']} fetch(es) failed"
    )
    for check in integ["checks"]:
        if check["status"] in (FAIL, WARN):
            marker = "FAIL" if check["status"] == FAIL else "warn"
            sys.stderr.write(f"{marker}: {check['id']} — {check['detail']}\n")
    print(f"integrity: {integ['status']} ({integ['counts']})")


def intake(args) -> int:
    """Audit a foreign tree and write the report of record.

    The report is written whatever its verdict, for the same reason the
    dashboard publishes with a red banner: a visibly failed audit gets looked
    at, a missing one gets assumed fine. The exit code carries the verdict.
    """
    if not (args.spec or "").strip():
        # Without this the report lands at `reports/<timestamp>.json` instead of
        # `reports/<spec>/<timestamp>.json`, where the index — which expects one
        # directory per spec — never finds it again.
        sys.stderr.write("error: a spec is required, e.g. factor-bundle@2.0.0\n")
        return 2

    started = time.monotonic()
    out = Path(os.environ.get("RECON_REPORTS", "reports"))
    inventory_path = Path(
        os.environ.get("RECON_INVENTORY", "public/observation.json")
    )

    # The join is an explicit, named input. Reading it is not "recon consulting
    # its own history" — that prohibition is about the org's current-state
    # derivation, and an intake report is an observation of somebody else's
    # tree. It records which observation it joined against so the answer stays
    # auditable after the inventory moves on.
    try:
        inventory = intake_mod.Inventory.from_observation(
            json.loads(inventory_path.read_text(encoding="utf-8")),
            source=str(inventory_path),
        )
    except (OSError, json.JSONDecodeError) as exc:
        inventory = intake_mod.Inventory.unavailable(
            f"{type(exc).__name__}: {exc}"[:200], source=str(inventory_path)
        )

    days = os.environ.get("RECON_ABANDONMENT_DAYS", "").strip()
    if days and not days.isdigit():
        sys.stderr.write(f"error: RECON_ABANDONMENT_DAYS must be a number, got {days!r}\n")
        return 2

    session = Session(clock=_utc_now)
    registry = Registry(session)
    audited_at = _utc_now()

    report = intake_mod.audit(
        args.spec,
        registry=registry,
        session=session,
        inventory=inventory,
        today=datetime.date.today(),
        abandonment_days=int(days) if days else DEFAULT_ABANDONMENT_DAYS,
        builder_sha=_builder_sha(),
        with_advisories=not os.environ.get("RECON_NO_ADVISORIES"),
    )
    report["meta"]["audited_at"] = audited_at
    report["meta"]["duration_ms"] = int((time.monotonic() - started) * 1000)

    path = Path(intake_mod.report_path(args.spec, at=audited_at, root=str(out)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obs_mod.canonical(report), encoding="utf-8")

    # The timestamped page is the record; `index.html` is a pointer to the most
    # recent one, so the directory has a stable URL without the report of record
    # ever being overwritten.
    html = pages.intake(report)
    path.with_suffix(".html").write_text(html, encoding="utf-8")
    (path.parent / "index.html").write_text(html, encoding="utf-8")

    # The instructions the deep link names, published beside the report rather
    # than squeezed into a 5,000-character URL parameter.
    scen = scenario_mod.build(report)
    if scen["resolved"]:
        (path.parent / "onboard.md").write_text(
            scenario_mod.onboard_document(report, scen), encoding="utf-8"
        )

    print(f"report: {path}")
    _report_intake(report)
    return 1 if report["integrity"]["status"] == FAIL else 0


def _report_intake(report: dict) -> None:
    meta, totals = report["meta"], report["totals"]
    if not report["tree"]["resolved"]:
        sys.stderr.write(
            f"error: {meta['spec']} did not resolve: {report['tree']['reason']}\n"
        )
        return
    print(
        f"audited {meta['root']}: {totals['packages']} package(s) — "
        f"{totals.get('time_bomb', 0)} time bomb(s), {totals.get('unknown', 0)} unknown, "
        f"{totals['emergencies']} emergency(ies)"
    )
    if "needs_fork" in totals:
        print(
            f"adoption: {totals['covered']} already covered, "
            f"{totals['needs_alias']} alias(es), {totals['already_queued']} already queued, "
            f"{totals['needs_fork']} new fork(s)"
        )
    else:
        sys.stderr.write(
            "warn: no fork inventory — coverage and the adoption plan are unknown "
            "for this run, not empty\n"
        )
    for check in report["integrity"]["checks"]:
        if check["status"] in (FAIL, WARN):
            marker = "FAIL" if check["status"] == FAIL else "warn"
            sys.stderr.write(f"{marker}: {check['id']} — {check['detail']}\n")


def compare(args) -> int:
    """Diff two repositories' committed lockfiles.

    No resolver and no registry: both sides already resolved their own trees and
    committed the answer, so this reads ground truth rather than re-deriving it.
    That is why it is fast enough to be interactive and exact enough to act on.
    """
    out = Path(os.environ.get("RECON_COMPARISONS", "comparisons"))

    # Both refs are validated before anything is fetched. A malformed ref is a
    # usage error, not a finding — reporting it as "one side could not be read"
    # would file it alongside "that repo has no lockfile", which is a fact about
    # the world rather than about the command line.
    for ref in (args.baseline, args.subject):
        try:
            lockfile_mod.parse_repo(ref)
        except ValueError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 2

    session = Session(clock=_utc_now)
    compared_at = _utc_now()

    baseline = lockfile_mod.fetch(session, args.baseline)
    subject = lockfile_mod.fetch(session, args.subject)

    report = compare_mod.build_report(
        baseline, subject,
        baseline_ref=args.baseline, subject_ref=args.subject,
        session=session, compared_at=compared_at, builder_sha=_builder_sha(),
    )

    slug = f"{_slug(args.baseline)}...{_slug(args.subject)}"
    path = out / slug / f"{compared_at.replace(':', '-')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obs_mod.canonical(report), encoding="utf-8")

    html = pages.compare(report)
    path.with_suffix(".html").write_text(html, encoding="utf-8")
    (path.parent / "index.html").write_text(html, encoding="utf-8")

    print(f"report: {path}")
    if report["diff"] is None:
        for check in report["integrity"]["checks"]:
            if check["status"] == FAIL:
                sys.stderr.write(f"FAIL: {check['id']} — {check['detail']}\n")
        return 1
    print(report["headline"])
    for check in report["integrity"]["checks"]:
        if check["status"] == WARN:
            sys.stderr.write(f"warn: {check['id']} — {check['detail']}\n")
    return 0


def _slug(ref: str) -> str:
    owner, repo, _ = lockfile_mod.parse_repo(ref)
    return f"{owner}~{repo}"


def verify(args) -> int:
    """Exit non-zero if the published observation failed an integrity check."""
    path = Path(args.path)
    try:
        observation = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"error: could not read {path}: {exc}\n")
        return 2

    integ = observation.get("integrity") or {}
    failures = [c for c in integ.get("checks", []) if c["status"] == FAIL]
    if failures:
        sys.stderr.write(
            f"{len(failures)} integrity check(s) failed — the published numbers are "
            "not trustworthy:\n"
        )
        for check in failures:
            sys.stderr.write(f"  - {check['id']}: {check['detail']}\n")
        return 1
    warnings = integ.get("counts", {}).get("warn", 0)
    print(f"integrity ok ({integ.get('counts')})"
          + (f" — {warnings} warning(s), see health.html" if warnings else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recon", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    build_cmd = sub.add_parser("build", help="derive, render and snapshot")
    build_cmd.set_defaults(func=build)

    intake_cmd = sub.add_parser(
        "intake", help="audit a foreign package tree before adopting it")
    intake_cmd.add_argument("spec", help="npm spec, e.g. factor-bundle@2.0.0")
    intake_cmd.set_defaults(func=intake)

    compare_cmd = sub.add_parser(
        "compare", help="diff two repositories' committed lockfiles")
    compare_cmd.add_argument("baseline", help="the repo compared against")
    compare_cmd.add_argument("subject", help="the repo being examined")
    compare_cmd.set_defaults(func=compare)

    verify_cmd = sub.add_parser("verify", help="fail if a build failed its checks")
    verify_cmd.add_argument("path", nargs="?", default="public/observation.json")
    verify_cmd.set_defaults(func=verify)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args((argv or []) + ["build"])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
