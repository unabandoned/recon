"""Build the site, write the snapshot, report the integrity verdict.

    python -m recon.cli build      # derive, render, snapshot
    python -m recon.cli verify     # exit non-zero if a published build failed a check

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

from . import fixtures as fixtures_mod
from . import observation as obs_mod
from . import snapshots
from .github import GitHub
from .http import Session
from .integrity import FAIL, WARN
from .registry import Registry
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

    out.mkdir(parents=True, exist_ok=True)
    for name, html in pages.render_all(observation, delta, trend).items():
        (out / name).write_text(html, encoding="utf-8")
    (out / "observation.json").write_text(obs_mod.canonical(observation), encoding="utf-8")
    (out / "changes.json").write_text(obs_mod.canonical(delta), encoding="utf-8")
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

    verify_cmd = sub.add_parser("verify", help="fail if a build failed its checks")
    verify_cmd.add_argument("path", nargs="?", default="public/observation.json")
    verify_cmd.set_defaults(func=verify)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args((argv or []) + ["build"])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
