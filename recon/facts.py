"""A fetched value that cannot silently pretend to be known.

Every failure in this tool's history has one shape: a failure path that
collapses into the happy path. A registry read times out, the handler returns
`None`, the classifier reads `None` as "no release date", and a rotting package
is filed as healthy. Nothing crashes. The page renders a smaller, calmer number
than the truth, and no amount of looking at it reveals the problem.

`Fact` makes that shape unrepresentable. A fetched value is one of:

    ok             the fetch succeeded; there is a value
    failed         it was attempted and did not work; there is a reason
    not_attempted  it was deliberately skipped; there is a reason

Reading `.value` on anything but `ok` raises `Unknown`. There is no default, no
`or None`, no silent zero. Code that wants to proceed without the value has to
say so out loud — `.or_else(x)` names the fallback at the call site, where a
reviewer can see it — and code that derives from a non-ok fact is expected to
produce `unknown`, which is a rendered, counted category rather than an absence.

The cost of this discipline is one enum value. The cost of not having it is
`classify()` opening `if not last: return "alive"`.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable

__all__ = ["Status", "Unknown", "Fact", "all_ok", "first_failure", "tally"]


class Status(str, enum.Enum):
    OK = "ok"
    FAILED = "failed"
    NOT_ATTEMPTED = "not_attempted"

    def __str__(self) -> str:  # so f-strings and JSON render the bare word
        return self.value


class Unknown(Exception):
    """Raised when code reads a value that was never successfully fetched.

    This is a bug-catcher, not a control-flow tool. If you are catching it, you
    almost certainly wanted `.or_else()` or `.is_ok` at the point where the
    unknown first mattered.
    """

    def __init__(self, fact: "Fact") -> None:
        super().__init__(
            f"read .value of a {fact.status} fact from {fact.source or '<unknown source>'}"
            + (f": {fact.detail}" if fact.detail else "")
        )
        self.fact = fact


@dataclass(frozen=True, slots=True)
class Fact:
    """A value plus the story of where it came from.

    `payload` is the raw slot and is deliberately awkward to reach for; `.value`
    is the ergonomic path and it is the one that enforces the discipline.
    """

    status: Status
    payload: Any = None
    source: str = ""
    fetched_at: str | None = None
    detail: str = ""

    # -- constructors -------------------------------------------------------
    @staticmethod
    def ok(value: Any, *, source: str = "", at: str | None = None) -> "Fact":
        return Fact(Status.OK, value, source, at, "")

    @staticmethod
    def failed(detail: str, *, source: str = "", at: str | None = None) -> "Fact":
        return Fact(Status.FAILED, None, source, at, detail)

    @staticmethod
    def skipped(reason: str, *, source: str = "") -> "Fact":
        return Fact(Status.NOT_ATTEMPTED, None, source, None, reason)

    # -- reading ------------------------------------------------------------
    @property
    def is_ok(self) -> bool:
        return self.status is Status.OK

    @property
    def value(self) -> Any:
        if self.status is not Status.OK:
            raise Unknown(self)
        return self.payload

    def or_else(self, default: Any) -> Any:
        """Read with an explicit fallback.

        The point is that the fallback is written at the call site. `f.or_else(0)`
        in a diff is a reviewable decision; `return None` buried in an exception
        handler three modules away is how bug 1a happened.
        """
        return self.payload if self.status is Status.OK else default

    def map(self, fn: Callable[[Any], Any]) -> "Fact":
        """Transform an ok value, propagating failure untouched.

        A raise inside `fn` becomes a failed fact rather than an exception: a
        packument that parses but has an unexpected shape is a failed read of
        that packument, not a crashed build.
        """
        if self.status is not Status.OK:
            return self
        try:
            return replace(self, payload=fn(self.payload))
        except Exception as exc:  # noqa: BLE001 - deliberately broad; see docstring
            return Fact(Status.FAILED, None, self.source, self.fetched_at,
                        f"{type(exc).__name__}: {exc}"[:200])

    def require(self, what: str) -> Any:
        """Read, raising a message that names what the caller needed.

        For the handful of facts the build genuinely cannot proceed without.
        """
        if self.status is not Status.OK:
            raise Unknown(replace(self, detail=f"{what}: {self.detail}"))
        return self.payload

    # -- serialisation ------------------------------------------------------
    def provenance(self) -> dict:
        """The record that travels with every rendered number."""
        out: dict[str, Any] = {"status": self.status.value}
        if self.source:
            out["source"] = self.source
        if self.fetched_at:
            out["fetched_at"] = self.fetched_at
        if self.detail:
            out["detail"] = self.detail
        return out


def all_ok(facts: Iterable[Fact]) -> bool:
    return all(f.is_ok for f in facts)


def first_failure(facts: Iterable[Fact]) -> Fact | None:
    for f in facts:
        if not f.is_ok:
            return f
    return None


def tally(facts: Iterable[Fact]) -> dict[str, int]:
    """Counts by status — the raw material of the coverage ledger."""
    counts = {s.value: 0 for s in Status}
    for f in facts:
        counts[f.status.value] += 1
    return counts
