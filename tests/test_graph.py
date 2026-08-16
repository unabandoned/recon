"""Dominators, shadowing and blast radius.

The queue's whole claim is that fixing one node clears several. If the dominator
maths is wrong the queue confidently recommends the wrong work, which is a more
expensive kind of wrong than a miscount.
"""
from __future__ import annotations

import unittest

from recon.classify import State
from recon.graph import ForkGraph, build_queue, dominated_by, dominators
from recon.resolve import Node, Tree


def tree_of(edges, states):
    """Build a minimal Tree from (parent, child) name pairs rooted at 'root'."""
    t = Tree(root="root", root_key="root")
    names = {n for e in edges for n in e} | set(states)
    for name in names:
        if name == "root":
            continue
        t.nodes[name] = Node(
            key=name, name=name.split("@")[0], version=name.split("@")[-1]
            if "@" in name else "1.0.0", alias=None, deps=(), direct=False,
            via=(), parent=None, depth=1,
        )
    t.edges = tuple(edges)
    return t


class Dominators(unittest.TestCase):
    def test_linear_chain(self):
        idom = dominators("a", [("a", "b"), ("b", "c"), ("c", "d")])
        self.assertEqual(idom["b"], "a")
        self.assertEqual(idom["c"], "b")
        self.assertEqual(idom["d"], "c")

    def test_diamond_joins_at_the_root(self):
        """`d` is reachable two ways, so only `a` dominates it."""
        idom = dominators("a", [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
        self.assertEqual(idom["d"], "a")

    def test_single_chokepoint_dominates(self):
        idom = dominators("a", [("a", "b"), ("b", "c"), ("b", "d"), ("c", "e"), ("d", "e")])
        self.assertEqual(idom["e"], "b")

    def test_dominated_by_includes_transitive_descendants(self):
        idom = dominators("a", [("a", "b"), ("b", "c"), ("c", "d")])
        owned = dominated_by(idom, "a")
        self.assertEqual(owned["b"], {"b", "c", "d"})
        self.assertEqual(owned["c"], {"c", "d"})

    def test_cycle_does_not_hang(self):
        """Resolved trees are acyclic, but a malformed input must not spin."""
        idom = dominators("a", [("a", "b"), ("b", "c"), ("c", "b")])
        self.assertIn("c", idom)


class Shadowing(unittest.TestCase):
    def test_rot_under_rot_is_shadowed(self):
        """Fixing `hash-base` moots `readable-stream` beneath it, so only the
        higher one is separately actionable."""
        tree = tree_of(
            [("root", "hash-base"), ("hash-base", "readable-stream")],
            {"hash-base": State.TIME_BOMB, "readable-stream": State.TIME_BOMB},
        )
        fg = ForkGraph.build("fork", tree, {
            "hash-base": State.TIME_BOMB, "readable-stream": State.TIME_BOMB,
        })
        self.assertEqual(fg.actionable, ["hash-base"])
        self.assertEqual(fg.shadowed, ["readable-stream"])

    def test_rot_under_a_healthy_parent_is_actionable(self):
        tree = tree_of(
            [("root", "alive-pkg"), ("alive-pkg", "rotten")],
            {"alive-pkg": State.ALIVE, "rotten": State.TIME_BOMB},
        )
        fg = ForkGraph.build("fork", tree, {
            "alive-pkg": State.ALIVE, "rotten": State.TIME_BOMB,
        })
        self.assertEqual(fg.actionable, ["rotten"])
        self.assertEqual(fg.shadowed, [])

    def test_rot_reachable_two_ways_is_not_shadowed(self):
        """Fixing either parent leaves the other route open, so it stands alone."""
        tree = tree_of(
            [("root", "a"), ("root", "b"), ("a", "leaf"), ("b", "leaf")],
            {"a": State.TIME_BOMB, "b": State.TIME_BOMB, "leaf": State.TIME_BOMB},
        )
        fg = ForkGraph.build("fork", tree, {
            "a": State.TIME_BOMB, "b": State.TIME_BOMB, "leaf": State.TIME_BOMB,
        })
        self.assertEqual(sorted(fg.actionable), ["a", "b", "leaf"])

    def test_unknown_counts_as_rot(self):
        """Unmeasured is not healthy — it belongs in the queue, not out of it."""
        tree = tree_of([("root", "mystery")], {"mystery": State.UNKNOWN})
        fg = ForkGraph.build("fork", tree, {"mystery": State.UNKNOWN})
        self.assertEqual(fg.actionable, ["mystery"])

    def test_clears_counts_the_whole_dominated_subtree(self):
        tree = tree_of(
            [("root", "top"), ("top", "mid"), ("mid", "leaf")],
            {"top": State.TIME_BOMB, "mid": State.TIME_BOMB, "leaf": State.TIME_BOMB},
        )
        fg = ForkGraph.build("fork", tree, {
            "top": State.TIME_BOMB, "mid": State.TIME_BOMB, "leaf": State.TIME_BOMB,
        })
        self.assertEqual(fg.clears("top"), {"top", "mid", "leaf"})


class Queue(unittest.TestCase):
    def _two_forks(self):
        shared = tree_of(
            [("root", "hash-base"), ("hash-base", "readable-stream")],
            {"hash-base": State.TIME_BOMB, "readable-stream": State.TIME_BOMB},
        )
        states = {"hash-base": State.TIME_BOMB, "readable-stream": State.TIME_BOMB}
        return {
            "@unabandoned/a": ForkGraph.build("@unabandoned/a", shared, states),
            "@unabandoned/b": ForkGraph.build("@unabandoned/b", shared, states),
        }

    def test_queue_ranks_the_dominator_not_the_leaf(self):
        queue = build_queue(self._two_forks())
        self.assertEqual(queue[0].name, "hash-base")

    def test_shadowed_leaf_is_recorded_not_promoted(self):
        queue = build_queue(self._two_forks())
        names = [c.name for c in queue]
        self.assertNotIn("readable-stream", names)

    def test_reach_across_forks_raises_the_score(self):
        one = {"@unabandoned/a": self._two_forks()["@unabandoned/a"]}
        self.assertGreater(
            build_queue(self._two_forks())[0].score(), build_queue(one)[0].score()
        )

    def test_an_advisory_makes_an_entry_an_emergency_and_outranks(self):
        graphs = self._two_forks()
        advisories = {"readable-stream@1.0.0": [{"id": "GHSA-xxxx", "severity": "HIGH"}]}
        queue = build_queue(graphs, advisories)
        self.assertTrue(queue[0].emergency)
        self.assertIn("GHSA-xxxx", queue[0].advisories)
        self.assertEqual(queue[0].max_severity, "HIGH")

    def test_emergencies_sort_above_everything(self):
        big = tree_of(
            [("root", "huge")] + [("huge", f"n{i}") for i in range(10)],
            {"huge": State.TIME_BOMB, **{f"n{i}": State.TIME_BOMB for i in range(10)}},
        )
        big_states = {"huge": State.TIME_BOMB,
                      **{f"n{i}": State.TIME_BOMB for i in range(10)}}
        graphs = {
            "@unabandoned/big": ForkGraph.build("@unabandoned/big", big, big_states),
            **self._two_forks(),
        }
        queue = build_queue(graphs, {"hash-base@1.0.0": [{"id": "GHSA-1", "severity": "CRITICAL"}]})
        # `huge` clears far more, but the advisory-bearing entry is pinned above it.
        self.assertEqual(queue[0].name, "hash-base")
        self.assertEqual(queue[1].name, "huge")


if __name__ == "__main__":
    unittest.main()
