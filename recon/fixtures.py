"""Org-level ground truth — hand-written facts the build must reproduce.

Most fixtures belong in the fork they describe (`expects-sibling` in
`.unabandoned.yml`), because the co-location rule exists so that a fact gets
updated in the same pull request as the thing it describes. What lands here is
what has no single home: cross-fork reachability assertions and counted facts
about the org as a whole.

This file does not violate "never record derivable state" — it inverts it. It is
not a cache of what the build found; it is *underivable human knowledge used to
audit the derivation*. If it ever drifts from reality, the build fails, which is
the opposite of the failure mode the rule protects against.

    paths:
      - fork: "@unabandoned/browserify"
        package: readable-stream
        via: ["@unabandoned/crypto-browserify", "hash-base"]

    counts:
      - metric: open_issues
        subject: xml-js
        equals: 1
"""
from __future__ import annotations

from pathlib import Path

from . import metadata as md

EMPTY: dict = {"paths": [], "counts": []}


def load(path: Path) -> tuple[dict, list[str]]:
    """Read the fixture file. Returns (fixtures, errors)."""
    if not path.is_file():
        return dict(EMPTY), []
    try:
        data = md.load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - a broken fixture file is a hard error
        return dict(EMPTY), [f"could not parse {path}: {exc}"]

    if not isinstance(data, dict):
        return dict(EMPTY), [f"{path}: top-level document must be a mapping"]

    errors: list[str] = []
    paths = data.get("paths") or []
    counts = data.get("counts") or []

    if not isinstance(paths, list):
        errors.append("`paths` must be a list")
        paths = []
    else:
        for i, item in enumerate(paths):
            if not isinstance(item, dict):
                errors.append(f"`paths[{i}]` must be a mapping")
                continue
            for key in ("fork", "package"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    errors.append(f"`paths[{i}].{key}` is required")
            via = item.get("via")
            if via is not None and (
                not isinstance(via, list) or not all(isinstance(v, str) for v in via)
            ):
                errors.append(f"`paths[{i}].via` must be a list of strings when present")

    if not isinstance(counts, list):
        errors.append("`counts` must be a list")
        counts = []
    else:
        for i, item in enumerate(counts):
            if not isinstance(item, dict):
                errors.append(f"`counts[{i}]` must be a mapping")
                continue
            for key in ("metric", "subject"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    errors.append(f"`counts[{i}].{key}` is required")
            if not isinstance(item.get("equals"), int):
                errors.append(f"`counts[{i}].equals` is required and must be an integer")

    return {"paths": paths, "counts": counts}, errors
