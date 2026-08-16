"""History: timestamped observations, and the diff between two of them.

A git repository of normalised JSON snapshots *is* a time series, and it arrives
with diff, blame, bisect and immutability already built. "Which merge caused
this change?" is git's native question, which is most of the reason this is a
directory of files rather than a database.

The derivability rule survives because a snapshot is a fact about the past, and
the past is not derivable later: the registry's yesterday-state is gone and the
GitHub API has no time machine. A snapshot cannot drift from the reality it
describes, because that reality is frozen. What would break the rule is the
*current* build reading one — so nothing here is imported by `build_core`, and
`integrity.snapshot_independence` checks that on every run.

Merge attribution falls out for free. Every snapshot records each fork's
`head_sha`, so the delta between consecutive snapshots brackets, per fork,
exactly the commit range `sha_prev..sha_now`. The changes view links straight to
the GitHub compare URL — no extra infrastructure, no webhook, no bookkeeping.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .classify import SEVERITY, State

STAMP = re.compile(r"^\d{8}T\d{6}Z\.json$")
TREND_KEYS = ("packages", "time_bomb", "unknown", "inert", "alive",
              "emergencies", "edges", "forks")


def stamp_for(iso: str) -> str:
    """`2026-08-16T04:12:00Z` -> `20260816T041200Z` — sortable as a filename."""
    return re.sub(r"[-:]", "", iso).replace(".000", "").split(".")[0].rstrip("Z") + "Z"


def write(directory: Path, observation: dict, *, at: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp_for(at)}.json"
    path.write_text(
        json.dumps(observation, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def listing(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if STAMP.match(p.name))


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def latest(directory: Path) -> dict | None:
    """The most recent snapshot, or None. Only the differ may call this."""
    files = listing(directory)
    return load(files[-1]) if files else None


def trend(directory: Path, *, limit: int = 30) -> dict[str, list]:
    """Headline aggregates over the last N snapshots, for the sparklines."""
    series: dict[str, list] = {k: [] for k in TREND_KEYS}
    series["at"] = []
    for path in listing(directory)[-limit:]:
        obs = load(path)
        if not obs:
            continue
        totals = obs.get("totals") or {}
        series["at"].append((obs.get("meta") or {}).get("built_at") or path.stem)
        for key in TREND_KEYS:
            series[key].append(totals.get(key))
    return series


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Transition:
    package: str
    was: str
    now: str

    @property
    def worse(self) -> bool:
        try:
            return SEVERITY[State(self.now)] > SEVERITY[State(self.was)]
        except ValueError:
            return False


def diff(previous: dict | None, current: dict, *, org: str = "unabandoned") -> dict:
    """What changed between two observations."""
    if previous is None:
        return {
            "baseline": True,
            "since": None,
            "transitions": [], "edges": {"added": [], "removed": []},
            "advisories": {"appeared": [], "cleared": []},
            "forks": [], "totals": {}, "coverage": {},
        }

    prev_pkgs = {p["name"]: p for p in previous.get("packages", [])}
    curr_pkgs = {p["name"]: p for p in current.get("packages", [])}

    transitions = []
    for name in sorted(set(prev_pkgs) | set(curr_pkgs)):
        was = prev_pkgs.get(name, {}).get("state", "absent")
        now = curr_pkgs.get(name, {}).get("state", "absent")
        if was != now:
            t = Transition(name, was, now)
            transitions.append({
                "package": name, "was": was, "now": now, "worse": t.worse,
                "reason": curr_pkgs.get(name, {}).get("reason", ""),
            })

    def edge_key(e):
        return (e["from"], e["to"])

    prev_edges = {edge_key(e) for e in previous.get("edges", [])}
    curr_edges = {edge_key(e) for e in current.get("edges", [])}

    prev_adv = {
        (p["name"], a["id"])
        for p in previous.get("packages", []) for a in p.get("advisories", [])
    }
    curr_adv = {
        (p["name"], a["id"])
        for p in current.get("packages", []) for a in p.get("advisories", [])
    }

    # Merge attribution: bracket each fork's commit range between the snapshots.
    prev_forks = {f["package"]: f for f in previous.get("forks", [])}
    fork_changes = []
    for fork in current.get("forks", []):
        before = prev_forks.get(fork["package"])
        if not before:
            fork_changes.append({
                "package": fork["package"], "change": "added", "compare": None,
            })
            continue
        old_sha = (before.get("head_sha") or {}).get("value")
        new_sha = (fork.get("head_sha") or {}).get("value")
        moved = bool(old_sha and new_sha and old_sha != new_sha)
        old_grade, new_grade = before.get("grade"), fork.get("grade")
        if not moved and old_grade == new_grade:
            continue
        fork_changes.append({
            "package": fork["package"],
            "change": "moved" if moved else "regraded",
            "grade_was": old_grade,
            "grade_now": new_grade,
            "compare": (
                f"https://github.com/{org}/{fork['repo']}/compare/{old_sha}...{new_sha}"
                if moved else None
            ),
        })

    prev_totals = previous.get("totals", {})
    curr_totals = current.get("totals", {})

    return {
        "baseline": False,
        "since": (previous.get("meta") or {}).get("built_at"),
        "transitions": transitions,
        "edges": {
            "added": [{"from": a, "to": b} for a, b in sorted(curr_edges - prev_edges)],
            "removed": [{"from": a, "to": b} for a, b in sorted(prev_edges - curr_edges)],
        },
        "advisories": {
            "appeared": [{"package": p, "id": i} for p, i in sorted(curr_adv - prev_adv)],
            "cleared": [{"package": p, "id": i} for p, i in sorted(prev_adv - curr_adv)],
        },
        "forks": sorted(fork_changes, key=lambda f: f["package"]),
        "totals": {
            key: {"was": prev_totals.get(key), "now": curr_totals.get(key)}
            for key in sorted(set(prev_totals) | set(curr_totals))
            if prev_totals.get(key) != curr_totals.get(key)
        },
        "coverage": {
            "excluded_was": len((previous.get("coverage", {}).get("repos", {})
                                 .get("excluded")) or []),
            "excluded_now": len((current.get("coverage", {}).get("repos", {})
                                 .get("excluded")) or []),
        },
    }
