"""Each check must fail on the bug it was written for, and pass otherwise.

A check that never fails is decoration. Every case below feeds in the shape of a
real historical bug and asserts the build would have stopped.
"""
from __future__ import annotations

import unittest

from recon import integrity as I


class M1(unittest.TestCase):
    def test_failed_fetch_with_no_unknown_is_a_failure(self):
        """The signature of bug 1a: something failed, yet every answer is confident."""
        check = I.unknowns_are_accounted(
            [{"name": "readable-stream", "state": "alive", "reason": "released 2019"}],
            {"attempted": 100, "failed": 3},
        )
        self.assertEqual(check.status, I.FAIL)

    def test_unknown_without_a_reason_is_a_failure(self):
        check = I.unknowns_are_accounted(
            [{"name": "x", "state": "unknown", "reason": ""}], {"failed": 1}
        )
        self.assertEqual(check.status, I.FAIL)

    def test_failed_fetch_with_a_reasoned_unknown_passes(self):
        check = I.unknowns_are_accounted(
            [{"name": "x", "state": "unknown", "reason": "HTTP 503 from registry"}],
            {"attempted": 100, "failed": 1},
        )
        self.assertEqual(check.status, I.PASS)

    def test_clean_build_passes(self):
        check = I.unknowns_are_accounted(
            [{"name": "x", "state": "alive", "reason": "released 2026-01-01"}],
            {"attempted": 100, "failed": 0},
        )
        self.assertEqual(check.status, I.PASS)


class M2(unittest.TestCase):
    def test_reader_disagreement_fails_the_build(self):
        """Bug 1b exactly: the manifest reader saw nothing, npm's resolver saw four."""
        check = I.scope_edges_agree({
            "@unabandoned/buffer": {
                "manifest_edges": [],
                "lockfile_edges": ["@unabandoned/ieee754"],
            }
        })
        self.assertEqual(check.status, I.FAIL)
        self.assertIn("ieee754", check.detail)

    def test_agreement_passes(self):
        check = I.scope_edges_agree({
            "@unabandoned/buffer": {
                "manifest_edges": ["@unabandoned/ieee754"],
                "lockfile_edges": ["@unabandoned/ieee754"],
            }
        })
        self.assertEqual(check.status, I.PASS)

    def test_a_reader_with_nothing_to_read_is_not_a_disagreement(self):
        """An unresolvable tree is the coverage ledger's problem, not this check's."""
        check = I.scope_edges_agree({
            "@unabandoned/x": {"manifest_edges": ["@unabandoned/y"], "lockfile_edges": None}
        })
        self.assertEqual(check.status, I.PASS)
        self.assertEqual(check.data["compared"], 0)

    def test_dependency_count_disagreement_fails(self):
        check = I.dependency_counts_agree([
            {"ident": "hash-base@3.1.0",
             "registry_deps": ["inherits", "readable-stream", "safe-buffer"],
             "lockfile_deps": ["inherits"]},
        ])
        self.assertEqual(check.status, I.FAIL)
        self.assertIn("hash-base@3.1.0", check.detail)

    def test_unreadable_registry_side_is_skipped_not_failed(self):
        check = I.dependency_counts_agree([
            {"ident": "x@1.0.0", "registry_deps": None, "lockfile_deps": ["a"]},
        ])
        self.assertEqual(check.status, I.PASS)
        self.assertEqual(check.data["compared"], 0)


class M3(unittest.TestCase):
    ASSERTED = [{"fork": "@unabandoned/buffer", "declares": ["@unabandoned/ieee754"]}]

    def test_missing_declared_sibling_fails(self):
        check = I.expected_siblings_present(
            {"@unabandoned/buffer": {"manifest_edges": [], "lockfile_edges": []}},
            self.ASSERTED,
        )
        self.assertEqual(check.status, I.FAIL)
        self.assertIn("no scope edges at all", check.detail)

    def test_declared_sibling_present_passes(self):
        check = I.expected_siblings_present(
            {"@unabandoned/buffer": {
                "manifest_edges": ["@unabandoned/ieee754"],
                "lockfile_edges": ["@unabandoned/ieee754"],
            }},
            self.ASSERTED,
        )
        self.assertEqual(check.status, I.PASS)
        self.assertEqual(check.data, {"asserted": 1, "derived": 1})

    def test_asserting_nothing_warns_rather_than_passing(self):
        """No fixtures is an absence of evidence, not evidence of absence."""
        check = I.expected_siblings_present(
            {"@unabandoned/buffer": {
                "manifest_edges": ["@unabandoned/ieee754"],
                "lockfile_edges": ["@unabandoned/ieee754"],
            }},
            [],
        )
        self.assertEqual(check.status, I.WARN)
        self.assertEqual(check.data, {"asserted": 0, "derived": 1})

    def test_an_assertion_about_a_fork_that_does_not_exist_fails(self):
        check = I.expected_siblings_present({}, self.ASSERTED)
        self.assertEqual(check.status, I.FAIL)

    def test_asserted_path_that_does_not_exist_fails(self):
        checks = I.org_fixtures_hold(
            {"paths": [{"fork": "@unabandoned/browserify", "package": "readable-stream",
                        "via": ["@unabandoned/crypto-browserify", "hash-base"]}]},
            {"routes": {}},
        )
        self.assertEqual(checks[0].status, I.FAIL)

    def test_asserted_path_via_the_wrong_route_fails(self):
        """The precise repair of failure 2: the fork reaches it, but not that way."""
        checks = I.org_fixtures_hold(
            {"paths": [{"fork": "@unabandoned/browserify", "package": "readable-stream",
                        "via": ["browserify-sign"]}]},
            {"routes": {("@unabandoned/browserify", "readable-stream"):
                        [["@unabandoned/crypto-browserify", "hash-base"]]}},
        )
        self.assertEqual(checks[0].status, I.FAIL)

    def test_matching_path_passes(self):
        checks = I.org_fixtures_hold(
            {"paths": [{"fork": "@unabandoned/browserify", "package": "readable-stream",
                        "via": ["@unabandoned/crypto-browserify", "hash-base"]}]},
            {"routes": {("@unabandoned/browserify", "readable-stream"):
                        [["@unabandoned/crypto-browserify", "hash-base"]]}},
        )
        self.assertEqual(checks[0].status, I.PASS)

    def test_asserted_count_mismatch_fails(self):
        checks = I.org_fixtures_hold(
            {"counts": [{"metric": "open_issues", "subject": "xml-js", "equals": 1}]},
            {"counts": {("open_issues", "xml-js"): 2}},
        )
        self.assertEqual(checks[0].status, I.FAIL)
        self.assertIn("asserted 1, observed 2", checks[0].detail)

    def test_no_fixtures_at_all_warns(self):
        checks = I.org_fixtures_hold({}, {})
        self.assertEqual(checks[0].status, I.WARN)


class M4(unittest.TestCase):
    def test_zero_edges_across_many_forks_fails(self):
        """The symptom of bug 1b, caught without needing to know any real edge."""
        self.assertEqual(I.fork_edge_floor(0, 27, 1).status, I.FAIL)

    def test_edges_present_passes(self):
        self.assertEqual(I.fork_edge_floor(4, 27, 1).status, I.PASS)

    def test_a_tiny_org_is_exempt(self):
        self.assertEqual(I.fork_edge_floor(0, 1, 1).status, I.PASS)

    def test_identical_nonzero_metric_everywhere_warns(self):
        """Bug 1c's tell was that the number was the same everywhere."""
        check = I.uniformity("open_issues", {f"r{i}": 1 for i in range(27)})
        self.assertEqual(check.status, I.WARN)

    def test_identical_zero_is_fine(self):
        check = I.uniformity("open_issues", {f"r{i}": 0 for i in range(27)})
        self.assertEqual(check.status, I.PASS)

    def test_a_hard_nonzero_floor_warns(self):
        check = I.uniformity("open_issues", {"a": 1, "b": 1, "c": 2, "d": 5, "e": 1})
        self.assertEqual(check.status, I.WARN)
        self.assertEqual(check.data["floor"], 1)

    def test_varied_values_pass(self):
        check = I.uniformity("open_issues", {"a": 0, "b": 1, "c": 2, "d": 0, "e": 4})
        self.assertEqual(check.status, I.PASS)

    def test_used_by_that_only_names_siblings_warns(self):
        """The live shape: 19 of 27 forks carry `used-by`, and every entry names
        another fork — a fact the resolver already derives. So the question the
        org exists for, which of *our projects* stands under this rot, has no
        answer, while the topology's empty consumer row reads like "none"."""
        forks = {"@unabandoned/browserify", "@unabandoned/module-deps"}
        edges = [
            {"from": "@unabandoned/browserify", "to": "@unabandoned/module-deps"},
            {"from": "@unabandoned/module-deps", "to": "@unabandoned/detective"},
        ]
        check = I.consumers_are_named(edges, forks)
        self.assertEqual(check.status, I.WARN)
        self.assertEqual(check.data["external_consumers"], 0)
        self.assertIn("unrecorded, not zero", check.detail)

    def test_naming_one_repo_outside_the_org_passes(self):
        check = I.consumers_are_named(
            [{"from": "acme/web", "to": "@unabandoned/browserify"}],
            {"@unabandoned/browserify"},
        )
        self.assertEqual(check.status, I.PASS)
        self.assertEqual(check.data["named"], ["acme/web"])

    def test_no_used_by_entries_at_all_still_warns(self):
        """Nothing recorded is the same unknown as nothing external recorded."""
        self.assertEqual(I.consumers_are_named([], {"@unabandoned/x"}).status, I.WARN)

    def test_ledger_that_does_not_balance_fails(self):
        check = I.conservation({
            "repos": {"discovered": 29, "included": 27,
                      "excluded": [{"repo": "x", "reason": "no-metadata"}]},
        })
        self.assertEqual(check.status, I.FAIL)

    def test_exclusion_without_a_reason_fails(self):
        check = I.conservation({
            "repos": {"discovered": 2, "included": 1, "excluded": [{"repo": "x"}]},
        })
        self.assertEqual(check.status, I.FAIL)

    def test_balanced_ledger_passes(self):
        check = I.conservation({
            "repos": {"discovered": 29, "included": 27, "excluded": [
                {"repo": "a", "reason": "no-metadata"},
                {"repo": "b", "reason": "metadata-invalid"},
            ]},
        })
        self.assertEqual(check.status, I.PASS)


class M5(unittest.TestCase):
    def test_no_previous_snapshot_passes(self):
        self.assertEqual(I.differential({"time_bomb": 46}, None).status, I.PASS)

    def test_a_large_unexplained_swing_fails(self):
        check = I.differential({"time_bomb": 12}, {"time_bomb": 46})
        self.assertEqual(check.status, I.FAIL)
        self.assertIn("RECON_ACK_DELTA", check.detail)

    def test_an_acknowledged_swing_warns_instead(self):
        check = I.differential(
            {"time_bomb": 12}, {"time_bomb": 46}, acknowledged={"time_bomb"}
        )
        self.assertEqual(check.status, I.WARN)

    def test_a_small_move_passes(self):
        self.assertEqual(
            I.differential({"time_bomb": 45}, {"time_bomb": 46}).status, I.PASS
        )

    def test_appearing_from_zero_is_a_swing(self):
        check = I.differential({"emergencies": 3}, {"emergencies": 0},
                               thresholds={"emergencies": 0.2})
        self.assertEqual(check.status, I.FAIL)


class Reproducibility(unittest.TestCase):
    def test_identical_rederivation_passes(self):
        self.assertEqual(I.snapshot_independence("{}", "{}").status, I.PASS)

    def test_history_leaking_into_current_state_fails(self):
        check = I.snapshot_independence('{"a": 1}', '{"a": 2}')
        self.assertEqual(check.status, I.FAIL)
        self.assertIn("reading a snapshot", check.detail)


class Aggregation(unittest.TestCase):
    def test_worst_status_wins(self):
        checks = [
            I.Check("a", "M1", I.PASS, "", ""),
            I.Check("b", "M2", I.WARN, "", ""),
            I.Check("c", "M3", I.FAIL, "", ""),
        ]
        self.assertEqual(I.worst_status(checks), I.FAIL)
        self.assertEqual(I.worst_status(checks[:2]), I.WARN)
        self.assertEqual(I.worst_status([]), I.PASS)


if __name__ == "__main__":
    unittest.main()
