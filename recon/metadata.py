"""The `.unabandoned.yml` schema — the one definition, used everywhere.

Imported by the builder so the two can never disagree on the shape, and run as
a CLI by `reusable-ci` so every fork's metadata is checked on every pull
request.

The file holds **editorial** facts only: what the package is, why we forked it,
where it's used. If GitHub can answer it, it does not belong here.

Hand-asserted facts the build must reproduce (mechanism M3) do *not* live here.
They did briefly, as an `expects-sibling` list per fork, on the co-location
argument. Asserting the org's wiring that way costs one pull request per fork,
so it never happened and the check that read the field passed vacuously for its
whole life. The assertions now live in `fixtures/org.yml` in the recon repo,
where writing one costs a single pull request.
"""
from __future__ import annotations

import sys
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - surfaced as a clear CI message
    sys.stderr.write(
        "error: PyYAML is required (pip install pyyaml). "
        "On GitHub-hosted runners it is preinstalled.\n"
    )
    raise

SCHEMA_VERSION = 1
SCOPE = "@unabandoned/"
VALID_STATUSES = ("active", "seeking-replacement", "deprecated")
FILENAME = ".unabandoned.yml"


def _is_owner_repo(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split("/")
    return len(parts) == 2 and all(parts) and " " not in value


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(data: Any) -> list[str]:
    """Return human-readable error strings; empty means valid."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["top-level document must be a mapping/object"]

    schema = data.get("schema", SCHEMA_VERSION)
    if not isinstance(schema, int) or schema != SCHEMA_VERSION:
        errors.append(f"`schema` must be the integer {SCHEMA_VERSION} (got {schema!r})")

    package = data.get("package")
    if not _nonempty_str(package):
        errors.append("`package` is required and must be a non-empty string")
    elif not package.startswith(SCOPE):
        errors.append(f"`package` must start with '{SCOPE}' (got {package!r})")

    upstream = data.get("upstream")
    if not isinstance(upstream, dict):
        errors.append("`upstream` is required and must be a mapping")
    else:
        if not _is_owner_repo(upstream.get("repo")):
            errors.append("`upstream.repo` is required and must be 'owner/name'")
        if not _nonempty_str(upstream.get("reason")):
            errors.append("`upstream.reason` is required and must be a non-empty string")

    for field in ("summary", "why-forked"):
        if not _nonempty_str(data.get(field)):
            errors.append(f"`{field}` is required and must be a non-empty string")

    status = data.get("status", "active")
    if status not in VALID_STATUSES:
        errors.append(
            f"`status` must be one of {', '.join(VALID_STATUSES)} (got {status!r})"
        )

    used_by = data.get("used-by")
    if used_by is not None:
        if not isinstance(used_by, list):
            errors.append("`used-by` must be a list when present")
        else:
            for i, entry in enumerate(used_by):
                if not isinstance(entry, dict):
                    errors.append(f"`used-by[{i}]` must be a mapping")
                    continue
                for key in ("consumer", "purpose"):
                    if not _nonempty_str(entry.get(key)):
                        errors.append(
                            f"`used-by[{i}].{key}` is required and must be a "
                            "non-empty string"
                        )

    tags = data.get("tags")
    if tags is not None and (
        not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)
    ):
        errors.append("`tags` must be a list of strings when present")

    return errors


def normalise_package(name: str) -> str:
    """`ieee754` and `@unabandoned/ieee754` both mean the same fork."""
    name = name.strip()
    return name if name.startswith("@") else SCOPE + name


def load(text: str) -> Any:
    """Parse YAML text, raising on malformed YAML."""
    return yaml.safe_load(text)


# --------------------------------------------------------------------------- #
# CLI (run by reusable-ci on every fork pull request)
# --------------------------------------------------------------------------- #
def _validate_file(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = load(fh.read())
        except yaml.YAMLError as exc:
            return [f"could not parse YAML: {exc}"]
    return validate(data)


def main(argv: list[str]) -> int:
    paths = argv[1:] or [FILENAME]
    defaulted = not argv[1:]
    had_error = False

    for path in paths:
        try:
            errors = _validate_file(path)
        except FileNotFoundError:
            if defaulted:
                print(f"note: no {path} present — nothing to validate.")
                continue
            print(f"{path}: error — file not found")
            had_error = True
            continue

        if errors:
            had_error = True
            print(f"{path}: INVALID")
            for err in errors:
                print(f"  - {err}")
        else:
            print(f"{path}: ok")

    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
