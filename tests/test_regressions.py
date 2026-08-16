"""One test per documented failure. These are the reason this package exists.

Each class below reproduces the *input shape* that produced a wrong number, and
asserts the new pipeline gets it right — or, where the old code's mistake was to
answer confidently at all, asserts that it now declines to.
"""
from __future__ import annotations

import datetime
import unittest

from recon.classify import State, classify, cutoff_for
from recon.facts import Fact
from recon.github import _is_dependency_dashboard
from recon.resolve import manifest_scope_edges, parse_lockfile, resolved_dep_name

TODAY = datetime.date(2026, 8, 16)
CUTOFF = cutoff_for(TODAY)


class Failure1aFailedFetchBecameHealthy(unittest.TestCase):
    """A registry read failed, returned None, and the classifier read None as alive.

    The old `classify()` opened `if not last: return "alive"`, with a comment
    calling it the safe direction. It is not safe, it is quiet: a network blip
    silently reclassified time bombs as healthy and the page rendered a smaller,
    calmer number with nothing to indicate anything had gone wrong.
    """

    def test_failed_release_read_is_unknown_not_alive(self):
        verdict = classify(
            Fact.failed("HTTP 503", source="registry"),
            Fact.ok(["once", "safe-buffer"]),
            CUTOFF,
        )
        self.assertIs(verdict.state, State.UNKNOWN)
        self.assertNotEqual(verdict.state, State.ALIVE)

    def test_unknown_carries_the_reason_all_the_way_out(self):
        verdict = classify(Fact.failed("HTTP 503"), Fact.ok([]), CUTOFF)
        self.assertIn("503", verdict.reason)
        self.assertEqual(verdict.evidence()["last_release"]["status"], "failed")

    def test_unparseable_date_is_unknown(self):
        verdict = classify(Fact.ok("not-a-date"), Fact.ok([]), CUTOFF)
        self.assertIs(verdict.state, State.UNKNOWN)

    def test_abandoned_with_unreadable_deps_is_unknown(self):
        """Abandoned and we cannot tell inert from time bomb — which is exactly
        the distinction that decides whether anyone has to act."""
        verdict = classify(
            Fact.ok("2019-03-04"), Fact.failed("packument has no version 4.8.1"), CUTOFF
        )
        self.assertIs(verdict.state, State.UNKNOWN)

    def test_alive_survives_an_unreadable_dependency_list(self):
        """The asymmetry is deliberate: once alive is settled by the date, the
        missing fact could not have changed the answer, so it is not unknown."""
        verdict = classify(Fact.ok("2026-06-01"), Fact.failed("boom"), CUTOFF)
        self.assertIs(verdict.state, State.ALIVE)

    def test_the_three_real_states_still_work(self):
        self.assertIs(
            classify(Fact.ok("2026-06-01"), Fact.ok([]), CUTOFF).state, State.ALIVE)
        self.assertIs(
            classify(Fact.ok("2016-01-01"), Fact.ok([]), CUTOFF).state, State.INERT)
        self.assertIs(
            classify(Fact.ok("2016-01-01"), Fact.ok(["x"]), CUTOFF).state, State.TIME_BOMB)


class Failure1bAliasesWereInvisible(unittest.TestCase):
    """The scope lives in the dependency's VALUE, not its key.

        "buffer": "npm:@unabandoned/buffer@^6"

    Reading keys alone made every fork->fork edge invisible, so the topology
    rendered as isolated nodes, and made the audit read a freshly published fork
    as its abandoned upstream.
    """

    def test_alias_spec_resolves_to_the_real_package(self):
        self.assertEqual(
            resolved_dep_name("buffer", "npm:@unabandoned/buffer@^6"),
            "@unabandoned/buffer",
        )

    def test_plain_scoped_key_still_works(self):
        self.assertEqual(
            resolved_dep_name("@unabandoned/ieee754", "^1.2.0"), "@unabandoned/ieee754"
        )

    def test_unaliased_dependency_is_itself(self):
        self.assertEqual(resolved_dep_name("once", "^1.4.0"), "once")

    def test_manifest_reader_finds_edges_in_both_halves(self):
        manifest = {
            "dependencies": {
                "buffer": "npm:@unabandoned/buffer@^6",       # scope in the value
                "@unabandoned/ieee754": "^1.2.0",             # scope in the key
                "once": "^1.4.0",                             # not ours
            }
        }
        self.assertEqual(
            manifest_scope_edges(manifest),
            ["@unabandoned/buffer", "@unabandoned/ieee754"],
        )

    def test_lockfile_reader_identifies_by_name_not_directory(self):
        """The lockfile key is only WHERE it was placed; `name` is WHAT it is."""
        lock = {
            "packages": {
                "": {"dependencies": {"@unabandoned/thing": "^1"}},
                "node_modules/@unabandoned/thing": {
                    "version": "1.0.0",
                    "dependencies": {"buffer": "npm:@unabandoned/buffer@^6"},
                },
                "node_modules/buffer": {
                    "name": "@unabandoned/buffer",   # <- installed under an alias
                    "version": "6.0.3",
                    "dependencies": {"ieee754": "^1.2.1"},
                },
                "node_modules/ieee754": {"version": "1.2.1"},
            }
        }
        tree = parse_lockfile(lock, "@unabandoned/thing")
        names = {n.name for n in tree.nodes.values()}
        self.assertIn("@unabandoned/buffer", names)
        self.assertNotIn("buffer", names)
        self.assertEqual(tree.scope_edges, ("@unabandoned/buffer",))

    def test_both_readers_agree_on_the_same_wiring(self):
        """This agreement is the whole M2 check: Reader A parses our manifest,
        Reader B reads what npm's own resolver produced."""
        manifest = {"dependencies": {"buffer": "npm:@unabandoned/buffer@^6"}}
        lock = {
            "packages": {
                "": {"dependencies": {"@unabandoned/thing": "^1"}},
                "node_modules/@unabandoned/thing": {
                    "version": "1.0.0",
                    "dependencies": {"buffer": "npm:@unabandoned/buffer@^6"},
                },
                "node_modules/buffer": {"name": "@unabandoned/buffer", "version": "6.0.3"},
            }
        }
        tree = parse_lockfile(lock, "@unabandoned/thing")
        self.assertEqual(manifest_scope_edges(manifest), list(tree.scope_edges))


class Failure1cRenovateDashboardCountedAsWork(unittest.TestCase):
    """Renovate's always-open control-surface issue put a floor of 1 everywhere."""

    def test_bot_authored_dashboard_is_excluded(self):
        issue = {"title": "Dependency Dashboard", "user": {"login": "renovate[bot]"}}
        self.assertTrue(_is_dependency_dashboard(issue))

    def test_a_human_issue_with_the_same_title_still_counts(self):
        """Matched on the author as well as the title, so the filter cannot
        quietly grow into hiding real work."""
        issue = {"title": "Dependency Dashboard", "user": {"login": "a-person"}}
        self.assertFalse(_is_dependency_dashboard(issue))

    def test_ordinary_issues_count(self):
        issue = {"title": "crash on empty input", "user": {"login": "renovate[bot]"}}
        self.assertFalse(_is_dependency_dashboard(issue))


class Failure2MembershipVersusCausation(unittest.TestCase):
    """Knowing a package is in the tree is not knowing how it got there.

    The flat-columns table crossed declarers with consumers and hid that
    `browserify` does not consume `browserify-sign` at all — it inherits
    `readable-stream` through the org's own `crypto-browserify` fork.
    """

    def test_path_follows_nodes_own_resolution_rule(self):
        lock = {
            "packages": {
                "": {"dependencies": {"@unabandoned/browserify": "^17"}},
                "node_modules/@unabandoned/browserify": {
                    "version": "17.0.0",
                    "dependencies": {"crypto-browserify": "npm:@unabandoned/crypto-browserify@^3"},
                },
                "node_modules/crypto-browserify": {
                    "name": "@unabandoned/crypto-browserify",
                    "version": "3.12.0",
                    "dependencies": {"hash-base": "^3.0.0"},
                },
                "node_modules/hash-base": {
                    "version": "3.1.0",
                    "dependencies": {"readable-stream": "^3.6.0"},
                },
                "node_modules/readable-stream": {"version": "3.6.2"},
            }
        }
        tree = parse_lockfile(lock, "@unabandoned/browserify")
        rs = next(n for n in tree.nodes.values() if n.name == "readable-stream")
        self.assertEqual(
            list(rs.via), ["@unabandoned/crypto-browserify", "hash-base"]
        )
        self.assertEqual(rs.parent, "hash-base")
        self.assertEqual(rs.depth, 3)

    def test_nested_copy_is_attributed_to_the_parent_that_pinned_it(self):
        """A version conflict resolves on an earlier hop, not the hoisted one —
        which is what makes the path correct rather than merely plausible."""
        lock = {
            "packages": {
                "": {"dependencies": {"@unabandoned/root": "^1"}},
                "node_modules/@unabandoned/root": {
                    "version": "1.0.0",
                    "dependencies": {"a": "^1", "b": "^1"},
                },
                "node_modules/a": {"version": "1.0.0", "dependencies": {"dep": "^2"}},
                "node_modules/b": {"version": "1.0.0", "dependencies": {"dep": "^1"}},
                "node_modules/dep": {"version": "2.0.0"},
                "node_modules/a/node_modules/dep": {"version": "1.0.0"},
            }
        }
        tree = parse_lockfile(lock, "@unabandoned/root")
        nested = tree.nodes["node_modules/a/node_modules/dep"]
        hoisted = tree.nodes["node_modules/dep"]
        self.assertEqual(nested.parent, "a")
        self.assertEqual(nested.version, "1.0.0")
        self.assertEqual(hoisted.version, "2.0.0")
        # Both survive as distinct nodes: identity is (name, version), and
        # collapsing them here is how the cross-product bug got in.
        self.assertEqual(nested.ident, "dep@1.0.0")
        self.assertEqual(hoisted.ident, "dep@2.0.0")


if __name__ == "__main__":
    unittest.main()


class SpecNameMustNotSilentlyReroot(unittest.TestCase):
    """A versioned spec has to find its own root in the lockfile.

    Caught by running the real resolver: `resolve_tree("browserify@17.0.0")`
    looked for `node_modules/browserify@17.0.0`, missed, fell back to the probe
    manifest, and made `browserify` an ordinary node dominating all 178 others.
    The dominator analysis was wrong and nothing errored — the exact failure mode
    this package is built around.
    """

    def test_spec_name_strips_the_version(self):
        from recon.resolve import spec_name
        self.assertEqual(spec_name("browserify@17.0.0"), "browserify")
        self.assertEqual(spec_name("@unabandoned/buffer@^6"), "@unabandoned/buffer")
        self.assertEqual(spec_name("@unabandoned/buffer"), "@unabandoned/buffer")
        self.assertEqual(spec_name("browserify"), "browserify")

    def test_a_versioned_spec_still_finds_its_root(self):
        lock = {
            "packages": {
                "": {"dependencies": {"thing": "^1"}},
                "node_modules/thing": {"version": "1.0.0",
                                       "dependencies": {"leaf": "^1"}},
                "node_modules/leaf": {"version": "1.0.0"},
            }
        }
        tree = parse_lockfile(lock, "thing@1.0.0")
        self.assertEqual(tree.root_key, "node_modules/thing")
        # The target must not appear as a node in its own tree.
        self.assertEqual(sorted(n.name for n in tree.nodes.values()), ["leaf"])
