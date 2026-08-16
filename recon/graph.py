"""Dominators, shadowing, and blast radius — turning state into a work queue.

"Which packages are rotten" is a list. "Which single change removes the most
rot" is a graph-cut question, and dominators are the right approximation for it.

A node `d` **dominates** `n` when every path from the fork's root to `n` passes
through `d`. So if `d` is fixed — forked, aliased to something alive, replaced —
`n` leaves the tree with it, or becomes someone else's problem. A rotten node
dominated by another rotten node is **shadowed**: real, but not separately
actionable, because fixing the thing above it moots it.

The actionable set is what remains: the highest rot on each rotten path. Its
**blast radius** is how much rot it dominates, summed over every fork it appears
in, weighted by severity.

Two rules keep this honest, both learned the hard way:

1. Dominators are computed **per fork**, on `(name, version)` identity, and
   aggregated afterwards. Computing them on a name-collapsed org-wide graph
   gives wrong answers the moment two forks resolve different versions through
   different paths — the same collapse-by-name mistake that produced a table
   reporting membership when it had been asked about causation.
2. A candidate's score names the paths it came from. A queue entry that cannot
   show its work is a number to be trusted rather than checked, and this tool's
   entire failure history is numbers that deserved checking.

The algorithm is the iterative Cooper–Harvey–Kennedy dominator solver. At ~150
nodes per tree it converges in a handful of passes and needs no dependencies.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .classify import SEVERITY, State
from .resolve import Node, Tree


# --------------------------------------------------------------------------- #
# Dominators
# --------------------------------------------------------------------------- #
def _reverse_postorder(root: str, children: dict[str, list[str]]) -> list[str]:
    """DFS postorder, reversed — the traversal order the solver needs.

    Iterative rather than recursive: dependency graphs are shallow but a cycle
    or a pathological chain should not blow the Python stack in a build.
    """
    seen: set[str] = set()
    post: list[str] = []
    stack: list[tuple[str, bool]] = [(root, False)]
    while stack:
        node, expanded = stack.pop()
        if expanded:
            post.append(node)
            continue
        if node in seen:
            continue
        seen.add(node)
        stack.append((node, True))
        for child in sorted(children.get(node, []), reverse=True):
            if child not in seen:
                stack.append((child, False))
    post.reverse()
    return post


def dominators(root: str, edges) -> dict[str, str]:
    """Immediate dominator of every node reachable from `root`.

    Returns {node: idom}. The root maps to itself; unreachable nodes are absent.
    """
    children: dict[str, list[str]] = defaultdict(list)
    parents: dict[str, list[str]] = defaultdict(list)
    for a, b in edges:
        children[a].append(b)
        parents[b].append(a)

    order = _reverse_postorder(root, children)
    rpo = {node: i for i, node in enumerate(order)}
    idom: dict[str, str] = {root: root}

    def intersect(a: str, b: str) -> str:
        while a != b:
            while rpo[a] > rpo[b]:
                a = idom[a]
            while rpo[b] > rpo[a]:
                b = idom[b]
        return a

    changed = True
    while changed:
        changed = False
        for node in order:
            if node == root:
                continue
            candidates = [p for p in parents.get(node, []) if p in idom and p in rpo]
            if not candidates:
                continue
            new = candidates[0]
            for p in candidates[1:]:
                new = intersect(p, new)
            if idom.get(node) != new:
                idom[node] = new
                changed = True
    return idom


def dominated_by(idom: dict[str, str], root: str) -> dict[str, set[str]]:
    """{node: every node it dominates, itself included}."""
    out: dict[str, set[str]] = {n: {n} for n in idom}
    for node in idom:
        walker = node
        while walker != root:
            walker = idom.get(walker, root)
            out.setdefault(walker, {walker}).add(node)
            if walker == root:
                break
    return out


# --------------------------------------------------------------------------- #
# The queue
# --------------------------------------------------------------------------- #
ROT = {State.TIME_BOMB, State.UNKNOWN}


@dataclass(slots=True)
class Candidate:
    """One intervention, with the consequence of taking it."""

    name: str
    versions: set[str] = field(default_factory=set)
    forks: set[str] = field(default_factory=set)
    state: State = State.UNKNOWN
    clears: set[str] = field(default_factory=set)      # rot names removed if fixed
    advisories: set[str] = field(default_factory=set)  # advisory ids in the cleared set
    max_severity: str = ""
    paths: list[dict] = field(default_factory=list)    # one route per fork
    shadowed_in: set[str] = field(default_factory=set)

    @property
    def emergency(self) -> bool:
        return bool(self.advisories)

    def score(self) -> float:
        """Blast radius: rot removed, weighted by severity and advisory pressure.

        Deliberately simple and legible — the ranking has to be explainable in
        one sentence on the page, and every input is shown next to it.
        """
        base = float(len(self.clears))
        reach = 1.0 + 0.25 * (len(self.forks) - 1)
        advisory = 1.0 + 1.5 * len(self.advisories)
        return round(base * reach * advisory, 3)


def build_queue(
    per_fork: dict[str, "ForkGraph"],
    advisories_by_ident: dict[str, list[dict]] | None = None,
) -> list[Candidate]:
    """Aggregate per-fork dominator analysis into one ranked list.

    `per_fork` maps fork package name -> ForkGraph. Every fork is analysed on
    its own `(name, version)` graph first; only the *results* are merged.
    """
    advisories_by_ident = advisories_by_ident or {}
    candidates: dict[str, Candidate] = {}

    for fork, fg in per_fork.items():
        for node_key in fg.actionable:
            node = fg.tree.nodes[node_key]
            cand = candidates.setdefault(node.name, Candidate(name=node.name))
            cand.forks.add(fork)
            if node.version:
                cand.versions.add(node.version)
            state = fg.states.get(node_key, State.UNKNOWN)
            if SEVERITY[state] > SEVERITY[cand.state]:
                cand.state = state

            for cleared_key in fg.clears(node_key):
                cleared = fg.tree.nodes.get(cleared_key)
                if cleared is None:
                    continue
                cand.clears.add(cleared.name)
                for adv in advisories_by_ident.get(cleared.ident, []):
                    cand.advisories.add(adv["id"])
                    if _worse(adv.get("severity", ""), cand.max_severity):
                        cand.max_severity = adv.get("severity", "")

            cand.paths.append({
                "fork": fork,
                "via": list(node.via),
                "package": node.name,
                "version": node.version,
                "clears": sorted(
                    {fg.tree.nodes[k].name for k in fg.clears(node_key)
                     if k in fg.tree.nodes} - {node.name}
                ),
            })

        for node_key in fg.shadowed:
            node = fg.tree.nodes[node_key]
            if node.name in candidates:
                candidates[node.name].shadowed_in.add(fork)

    ranked = sorted(
        candidates.values(),
        key=lambda c: (not c.emergency, -c.score(), c.name),
    )
    for cand in ranked:
        cand.paths.sort(key=lambda p: p["fork"])
    return ranked


_SEVERITY_ORDER = ["", "LOW", "MODERATE", "MEDIUM", "HIGH", "CRITICAL"]


def _worse(a: str, b: str) -> bool:
    def rank(s: str) -> int:
        try:
            return _SEVERITY_ORDER.index((s or "").upper())
        except ValueError:
            return 0
    return rank(a) > rank(b)


@dataclass(slots=True)
class ForkGraph:
    """One fork's resolved tree with dominator analysis applied."""

    fork: str
    tree: Tree
    states: dict[str, State]                       # lockfile key -> state
    idom: dict[str, str] = field(default_factory=dict)
    _dominated: dict[str, set[str]] = field(default_factory=dict)
    actionable: list[str] = field(default_factory=list)
    shadowed: list[str] = field(default_factory=list)

    @staticmethod
    def build(fork: str, tree: Tree, states: dict[str, State]) -> "ForkGraph":
        root = tree.root_key
        idom = dominators(root, tree.edges)
        dominated = dominated_by(idom, root)

        rot = {k for k, s in states.items() if s in ROT and k in idom}
        actionable: list[str] = []
        shadowed: list[str] = []
        for key in sorted(rot):
            walker = idom.get(key, root)
            is_shadowed = False
            seen: set[str] = set()
            while walker != root and walker not in seen:
                seen.add(walker)
                if walker in rot:
                    is_shadowed = True
                    break
                walker = idom.get(walker, root)
            (shadowed if is_shadowed else actionable).append(key)

        fg = ForkGraph(fork, tree, states, idom, dominated, actionable, shadowed)
        return fg

    def clears(self, key: str) -> set[str]:
        """Rot nodes that leave this tree if `key` is fixed."""
        owned = self._dominated.get(key, {key})
        return {k for k in owned if self.states.get(k) in ROT}
