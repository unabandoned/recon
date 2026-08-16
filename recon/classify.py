"""Classify a package by whether it *can* rot — with `unknown` as a real answer.

    alive      released within the threshold; a maintainer can still respond
    inert      abandoned but declares zero runtime deps; nothing beneath to rot
    time bomb  abandoned AND carrying runtime deps; the subtree ages unwatched
    unknown    we could not establish one of the facts the verdict depends on

"On latest" is not a health signal — a frozen package pinned to frozen
dependencies is still rotting — which is why the classification turns on
capacity to rot rather than current drift.

The fourth state is the new one and the reason this module exists. Previously a
failed registry read produced `alive`, with a comment explaining that this was
the safe direction ("never a false alarm"). It is not safe; it is just quiet.
A package we could not date is not healthy, it is unmeasured, and the two
should never render as the same word. `unknown` is counted in every aggregate
and carries the reason it is unknown all the way to the page.

Note the asymmetry: once a package is known *alive*, its dependency count is
irrelevant to the verdict, so an unreadable dependency list does not make it
unknown. Unknown is reserved for cases where the missing fact would actually
have changed the answer.
"""
from __future__ import annotations

import datetime
import enum
from dataclasses import dataclass

from .facts import Fact

DEFAULT_ABANDONMENT_DAYS = 365


class State(str, enum.Enum):
    ALIVE = "alive"
    INERT = "inert"
    TIME_BOMB = "time_bomb"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value


# Worst-wins ordering for rolling several versions of one name into a single
# row. `unknown` sits above `inert`: an unmeasured package deserves more
# attention than one we have positively established cannot rot.
SEVERITY = {
    State.ALIVE: 0,
    State.INERT: 1,
    State.UNKNOWN: 2,
    State.TIME_BOMB: 3,
}


@dataclass(frozen=True, slots=True)
class Verdict:
    state: State
    reason: str
    last_release: Fact
    deps: Fact

    @property
    def is_unknown(self) -> bool:
        return self.state is State.UNKNOWN

    def evidence(self) -> dict:
        """What the UI shows when a reader asks "how do you know?"."""
        return {
            "state": self.state.value,
            "reason": self.reason,
            "last_release": {
                **self.last_release.provenance(),
                **({"value": self.last_release.payload} if self.last_release.is_ok else {}),
            },
            "dependencies": {
                **self.deps.provenance(),
                **({"count": len(self.deps.payload)} if self.deps.is_ok else {}),
            },
        }


def cutoff_for(today: datetime.date, days: int = DEFAULT_ABANDONMENT_DAYS) -> datetime.date:
    return today - datetime.timedelta(days=days)


def classify(last_release: Fact, deps: Fact, cutoff: datetime.date) -> Verdict:
    """Decide one package's state from two facts and a threshold.

    `last_release` is a Fact wrapping an ISO date string; `deps` is a Fact
    wrapping a list of dependency names.
    """
    if not last_release.is_ok:
        return Verdict(
            State.UNKNOWN,
            f"release date unavailable ({last_release.detail or last_release.status})",
            last_release,
            deps,
        )

    raw = last_release.payload
    try:
        released = datetime.date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return Verdict(
            State.UNKNOWN, f"unparseable release date {raw!r}", last_release, deps
        )

    if released >= cutoff:
        # Alive is settled by the date alone, so an unreadable dependency list
        # does not downgrade the verdict to unknown — it could not have changed it.
        return Verdict(State.ALIVE, f"released {released.isoformat()}", last_release, deps)

    if not deps.is_ok:
        # Abandoned, and we cannot tell inert from time bomb — which is exactly
        # the distinction that decides whether anyone has to act.
        return Verdict(
            State.UNKNOWN,
            f"abandoned since {released.isoformat()}, dependency list unavailable "
            f"({deps.detail or deps.status})",
            last_release,
            deps,
        )

    n = len(deps.payload)
    if n:
        return Verdict(
            State.TIME_BOMB,
            f"no release since {released.isoformat()}; carries {n} runtime "
            f"dependenc{'y' if n == 1 else 'ies'}",
            last_release,
            deps,
        )
    return Verdict(
        State.INERT,
        f"no release since {released.isoformat()}; declares no runtime dependencies",
        last_release,
        deps,
    )


def worst(states) -> State:
    """Worst-wins rollup for a name that resolves to several versions."""
    best = State.ALIVE
    for s in states:
        if SEVERITY[s] > SEVERITY[best]:
            best = s
    return best
