"""Org-level ground truth — hand-written facts the build must reproduce.

Every fixture lives here, in the repository that does the deriving. An earlier
design put sibling-edge assertions in each fork's own `.unabandoned.yml` on the
co-location argument: a fact about one fork should be updated in the same pull
request as the wiring it describes. That argument is sound and the cost is
fatal — asserting the org's edge set meant twenty-seven pull requests against
twenty-seven repositories, so nobody opened the first one and the check that
depended on them verified nothing for its entire life. A fixture file only
works if adding to it is cheap enough that someone actually does.

This file does not violate "never record derivable state" — it inverts it. It is
not a cache of what the build found; it is *underivable human knowledge used to
audit the derivation*. If it ever drifts from reality, the build fails, which is
the opposite of the failure mode the rule protects against.

    edges:
      - fork: "@unabandoned/module-deps"
        declares: ["@unabandoned/detective"]

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

EMPTY: dict = {"edges": [], "paths": [], "counts": []}


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
    edges = data.get("edges") or []
    paths = data.get("paths") or []
    counts = data.get("counts") or []

    if not isinstance(edges, list):
        errors.append("`edges` must be a list")
        edges = []
    else:
        clean_edges = []
        for i, item in enumerate(edges):
            if not isinstance(item, dict):
                errors.append(f"`edges[{i}]` must be a mapping")
                continue
            fork = item.get("fork")
            if not isinstance(fork, str) or not fork.strip():
                errors.append(f"`edges[{i}].fork` is required")
                continue
            declares = item.get("declares")
            if not isinstance(declares, list) or not declares:
                errors.append(f"`edges[{i}].declares` must be a non-empty list")
                continue
            fork = md.normalise_package(fork)
            named = []
            for j, name in enumerate(declares):
                if not isinstance(name, str) or not name.strip():
                    errors.append(f"`edges[{i}].declares[{j}]` must be a non-empty string")
                    continue
                if name.startswith("@") and not name.startswith(md.SCOPE):
                    errors.append(
                        f"`edges[{i}].declares[{j}]` must name an {md.SCOPE}* package "
                        f"(got {name!r}); bare names are scoped automatically"
                    )
                    continue
                scoped = md.normalise_package(name)
                if scoped == fork:
                    errors.append(f"`edges[{i}]` must not declare {fork} as its own sibling")
                    continue
                named.append(scoped)
            if named:
                clean_edges.append({"fork": fork, "declares": sorted(set(named))})
        edges = clean_edges

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

    return {"edges": edges, "paths": paths, "counts": counts}, errors
