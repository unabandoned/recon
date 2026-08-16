"""The adoption scenario: what owning a package would cost.

The queue's question is "what should we fix first". This is the other one —
"what do we take on if we adopt this" — and the interesting property is that the
two can point in opposite directions. A package with a large tree can add almost
no obligation, because most of what rots beneath it already rots beneath
something we own.

The failure mode that matters here is the same one as everywhere else in this
repository, and it is worse in a prompt than on a page: with no fork inventory,
*every* plan entry is unclassified, so "no entries needing a new fork" means
"we could not tell" rather than "there are none". Reporting the second is how a
prompt tells someone the adoption is free when nothing checked.
"""
from __future__ import annotations

import unittest
import urllib.parse

from tests.test_intake import audit

from recon import intake, scenario


class Surface(unittest.TestCase):
    def setUp(self):
        self.s = scenario.build(audit())

    def test_it_counts_what_you_would_own(self):
        surf = self.s["surface"]
        self.assertGreater(surf["packages_owned"], 0)
        self.assertEqual(surf["time_bombs"], 4)
        self.assertEqual(surf["inert_left_alone"], 2)  # `through`, `inherits`

    def test_obligations_are_named_not_just_counted(self):
        """"1 new fork" is not a plan; "fork left-pad" is."""
        self.assertEqual(self.s["new_forks"], ["left-pad"])
        self.assertEqual(self.s["already_queued"], ["hash-base"])

    def test_the_wiring_list_is_every_sibling_we_publish_not_just_ranked_ones(self):
        """The plan ranks actionable *dominators*, so it omits a sibling we
        publish whenever that sibling is healthy or is dominated by something
        else. Both are still worth aliasing — otherwise the fork keeps pulling
        a package from upstream while we maintain our own copy of it."""
        self.assertEqual(
            [(a["package"], a["fork"]) for a in self.s["aliases"]],
            [("JSONStream", "@unabandoned/jsonstream"),
             ("readable-stream", "@unabandoned/readable-stream")],
        )
        # readable-stream is covered but is NOT a plan entry: hash-base dominates it.
        self.assertNotIn(
            "readable-stream",
            [s["package"] for s in (audit()["plan"]) if s["action"] == intake.ALIAS],
        )

    def test_an_already_queued_package_is_not_a_new_obligation(self):
        """It is already our problem; adopting this does not make it more so."""
        self.assertNotIn("hash-base", self.s["new_forks"])
        self.assertEqual(self.s["surface"]["already_queued"], 1)


class Attach(unittest.TestCase):
    def test_it_lists_the_fork_the_two_core_repos_and_every_alias_target(self):
        attach = scenario.build(audit())["attach"]
        self.assertEqual(attach[0], "unabandoned/foreign-tool")
        for core in scenario.CORE_REPOS:
            self.assertIn(core, attach)
        # One per sibling being aliased — wiring an alias means reading what that
        # sibling publishes rather than guessing a range.
        self.assertIn("unabandoned/jsonstream", attach)
        self.assertIn("unabandoned/readable-stream", attach)

    def test_it_does_not_repeat_a_repository(self):
        attach = scenario.build(audit())["attach"]
        self.assertEqual(len(attach), len(set(attach)))


class Prompt(unittest.TestCase):
    def setUp(self):
        self.s = scenario.build(audit())

    def test_it_names_the_actual_aliases_to_wire(self):
        self.assertIn("@unabandoned/jsonstream", self.s["prompt"])

    def test_it_names_the_packages_that_need_a_new_fork(self):
        self.assertIn("left-pad", self.s["prompt"])
        self.assertIn("NOT covered", self.s["prompt"])

    def test_it_carries_the_programs_own_rules(self):
        for rule in ("forkProcessing", "id-token: write", "fix forward"):
            self.assertIn(rule, self.s["prompt"].replace("Fix forward", "fix forward"))

    def test_it_does_not_claim_the_two_human_steps_are_automatable(self):
        self.assertIn("cannot be automated", self.s["prompt"])
        self.assertIn("npm trust", self.s["prompt"])


class UnknownCoverage(unittest.TestCase):
    """No inventory means the cost is unknown, never zero.

    Caught by an existing intake test rather than by design: with no inventory
    every action is `None`, so the "no packages need a new fork" branch fired and
    the prompt said adoption required no new forks. That is the same shape as
    reporting "0 covered" for an unreadable inventory, and a prompt is a worse
    place for it than a page, because someone acts on a prompt.
    """

    def setUp(self):
        self.s = scenario.build(
            audit(inventory=intake.Inventory.unavailable("HTTPError: 503"))
        )

    def test_no_coverage_numbers_are_printed_at_all(self):
        for key in ("new_forks", "aliases", "already_queued", "already_covered"):
            self.assertNotIn(key, self.s["surface"])

    def test_the_prompt_says_it_could_not_be_determined(self):
        self.assertIn("could NOT be determined", self.s["prompt"])
        self.assertIn("Do not treat that as zero", self.s["prompt"])

    def test_the_prompt_never_claims_no_forks_are_required(self):
        lowered = self.s["prompt"].lower()
        self.assertNotIn("no new forks are required", lowered)
        self.assertNotIn("already covered by", lowered)

    def test_the_compact_prompt_is_equally_careful(self):
        self.assertNotIn("No new forks needed", self.s["compact_prompt"])

    def test_every_plan_entry_reads_as_unclassified(self):
        self.assertEqual(self.s["new_forks"], [])
        self.assertTrue(self.s["unclassified"])


class DeepLink(unittest.TestCase):
    def test_it_targets_the_fork_and_carries_the_prompt(self):
        s = scenario.build(audit())
        link = s["deep_link"]
        self.assertTrue(link["fits"])
        self.assertTrue(link["url"].startswith("claude-cli://open?"))
        query = urllib.parse.parse_qs(link["url"].split("?", 1)[1])
        self.assertEqual(query["repo"], ["unabandoned/foreign-tool"])
        self.assertEqual(query["q"], [s["compact_prompt"]])

    def test_an_over_long_prompt_yields_no_link_rather_than_a_broken_one(self):
        """The handler caps `q`, and over-long values do not degrade cleanly."""
        link = scenario.deep_link("unabandoned", "x", "y" * (scenario.DEEP_LINK_LIMIT + 1))
        self.assertFalse(link["fits"])
        self.assertEqual(link["url"], "")
        self.assertGreater(link["length"], link["limit"])

    def test_the_compact_prompt_is_built_to_fit(self):
        self.assertLessEqual(
            len(scenario.build(audit())["compact_prompt"]), scenario.DEEP_LINK_LIMIT
        )


class Unresolved(unittest.TestCase):
    def test_a_tree_that_would_not_resolve_has_no_scenario(self):
        s = scenario.build(audit(spec="no-such-package@9.9.9"))
        self.assertFalse(s["resolved"])

    def test_and_the_page_renders_nothing_for_it(self):
        from recon.render import pages
        self.assertEqual(pages._scenario_panel(audit(spec="no-such-package@9.9.9")), "")


if __name__ == "__main__":
    unittest.main()
