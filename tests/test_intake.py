"""Intake (§7b): auditing a tree the org does not own.

Three things are worth testing here and only one of them is "does it count
correctly". The other two are the rules that make an intake report safe to act
on: an unavailable inventory must not read as "nothing is covered", and an
intake report must never reach the org's own aggregates.
"""
from __future__ import annotations

import datetime
import unittest

from tests import world

from recon import intake
from recon.http import Session
from recon.registry import Registry

TODAY = datetime.date(2026, 8, 16)
CLOCK = "2026-08-16T06:00:00Z"
SPEC = "foreign-tool@1.0.0"


def audit(*, inventory=None, spec: str = SPEC, advisories: bool = True):
    w = world.World()
    session = Session(clock=lambda: CLOCK, opener=w.opener, retries=0)
    return intake.audit(
        spec,
        registry=Registry(session),
        session=session,
        inventory=inventory if inventory is not None
        else intake.Inventory.from_observation(world.INVENTORY),
        today=TODAY,
        resolver=w.resolver,
        builder_sha="deadbeefcafe",
        with_advisories=advisories,
    )


class Audit(unittest.TestCase):
    def test_it_classifies_the_foreign_tree(self):
        report = audit()
        self.assertTrue(report["tree"]["resolved"])
        names = {p["name"] for p in report["packages"]}
        self.assertIn("JSONStream", names)
        self.assertNotIn("foreign-tool", names)   # the root is not its own dependency
        states = {p["name"]: p["state"] for p in report["packages"]}
        self.assertEqual(states["hash-base"], "time_bomb")
        self.assertEqual(states["through"], "inert")

    def test_the_advisory_join_runs_on_a_foreign_tree_too(self):
        report = audit()
        row = next(p for p in report["packages"] if p["name"] == "readable-stream")
        self.assertTrue(row["advisories"])
        self.assertEqual(report["totals"]["emergencies"], 1)

    def test_the_plan_is_ranked_and_accounts_for_every_rotten_package(self):
        report = audit()
        check = next(c for c in report["integrity"]["checks"]
                     if c["id"] == "intake.plan-clears-rot")
        self.assertEqual(check["status"], "pass")
        scores = [s["score"] for s in report["plan"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_an_inert_package_is_not_an_intervention(self):
        """Abandoned with nothing beneath it is not actionable, by design."""
        report = audit()
        self.assertNotIn("through", {s["package"] for s in report["plan"]})


class CoverageJoin(unittest.TestCase):
    def test_a_fork_whose_scope_forced_lowercase_still_counts_as_coverage(self):
        """The first real bug this module produced.

        npm requires scoped package names to be lowercase, so the org's fork of
        `JSONStream` is published as `@unabandoned/jsonstream`. An exact-match
        join reported it uncovered and the adoption plan proposed forking a
        package the org has maintained for months.
        """
        report = audit()
        row = next(p for p in report["packages"] if p["name"] == "JSONStream")
        self.assertTrue(row["covered"])
        self.assertEqual(row["covered_by"], "@unabandoned/jsonstream")
        self.assertEqual(row["covered_match"], "case-insensitive")

        step = next(s for s in report["plan"] if s["package"] == "JSONStream")
        self.assertEqual(step["action"], intake.ALIAS)
        # And it names the fork that exists, not a synthesised one.
        self.assertEqual(step["fork"], "@unabandoned/jsonstream")

    def test_an_exact_match_is_labelled_as_one(self):
        report = audit()
        row = next(p for p in report["packages"] if p["name"] == "readable-stream")
        self.assertEqual(row["covered_match"], "exact")

    def test_a_package_already_in_the_org_queue_is_not_a_new_fork(self):
        report = audit()
        step = next(s for s in report["plan"] if s["package"] == "hash-base")
        self.assertEqual(step["action"], intake.QUEUED)

    def test_only_the_genuinely_uncovered_package_needs_a_fork(self):
        report = audit()
        forks = sorted(s["package"] for s in report["plan"]
                       if s["action"] == intake.FORK)
        self.assertEqual(forks, ["left-pad"])
        self.assertEqual(report["totals"]["needs_fork"], 1)


class UnknownInventory(unittest.TestCase):
    """An unavailable join is unknown, never zero.

    "0 of 39 covered, 5 new forks needed" is a specific, actionable, wrong
    answer. It is also what any reasonable-looking implementation produces when
    the inventory fails to load, which is why it gets its own tests.
    """

    def setUp(self):
        self.report = audit(
            inventory=intake.Inventory.unavailable("HTTPError: 503")
        )

    def test_coverage_is_unknown_rather_than_false(self):
        for row in self.report["packages"]:
            self.assertIsNone(row["covered"], row["name"])

    def test_no_coverage_total_is_printed_at_all(self):
        totals = self.report["totals"]
        for key in ("covered", "uncovered", "needs_fork", "needs_alias"):
            self.assertNotIn(key, totals)

    def test_the_plan_has_no_actions_it_cannot_justify(self):
        for step in self.report["plan"]:
            self.assertIsNone(step["action"])

    def test_the_build_fails_rather_than_reporting_a_cheap_plan(self):
        check = next(c for c in self.report["integrity"]["checks"]
                     if c["id"] == "intake.coverage-join")
        self.assertEqual(check["status"], "fail")
        self.assertEqual(self.report["integrity"]["status"], "fail")

    def test_an_observation_with_no_forks_is_a_failed_read_not_an_empty_one(self):
        inv = intake.Inventory.from_observation({"forks": [], "meta": {}})
        self.assertFalse(inv.known)

    def test_a_malformed_observation_is_a_failed_read(self):
        self.assertFalse(intake.Inventory.from_observation("not an object").known)
        self.assertFalse(intake.Inventory.from_observation({"nope": 1}).known)


class Unresolved(unittest.TestCase):
    def test_a_spec_that_will_not_resolve_is_reported_not_emptied(self):
        report = audit(spec="no-such-package@9.9.9")
        self.assertFalse(report["tree"]["resolved"])
        self.assertEqual(report["packages"], [])
        self.assertEqual(report["integrity"]["status"], "fail")
        check = next(c for c in report["integrity"]["checks"]
                     if c["id"] == "intake.resolved")
        self.assertEqual(check["status"], "fail")
        self.assertIn("not in the npm registry", check["detail"])


class PlanConservation(unittest.TestCase):
    def test_a_rotten_package_in_no_plan_entry_fails(self):
        check = intake._plan_clears_rot(
            [{"name": "orphan", "state": "time_bomb"}], [], known=True
        )
        self.assertEqual(check.status, "fail")
        self.assertIn("orphan", check.detail)

    def test_a_clean_tree_has_nothing_to_plan_for(self):
        check = intake._plan_clears_rot([{"name": "x", "state": "alive"}], [], known=True)
        self.assertEqual(check.status, "pass")


class Isolation(unittest.TestCase):
    """Intake results never merge into the org's own numbers (§7b)."""

    def test_reports_are_not_written_where_snapshots_are_read_from(self):
        path = intake.report_path(SPEC, at="2026-08-16T06:00:00Z")
        self.assertTrue(path.startswith("reports/"))
        self.assertNotIn("snapshots", path)

    def test_a_scoped_spec_does_not_escape_its_directory(self):
        path = intake.report_path("@scope/pkg@1.0.0", at="2026-08-16T06:00:00Z")
        self.assertEqual(path.count("/"), 2)      # reports/<spec>/<stamp>.json

    def test_the_report_is_not_shaped_like_an_observation(self):
        """The differ and the trend glob observations; an intake report must not
        be mistaken for one if it ever lands beside them."""
        report = audit()
        self.assertEqual(report["kind"], "intake")
        for key in ("forks", "edges", "queue", "consumer_edges"):
            self.assertNotIn(key, report)

    def test_the_org_build_never_reads_the_reports_directory(self):
        import inspect
        from recon import observation, snapshots
        for module in (observation, snapshots):
            self.assertNotIn("reports", inspect.getsource(module))


class ReportIndex(unittest.TestCase):
    """The dashboard's list of audited trees."""

    def setUp(self):
        import json, tempfile
        from pathlib import Path
        self.dir = Path(tempfile.mkdtemp())
        good = self.dir / "foreign-tool@1.0.0"
        good.mkdir()
        (good / "2026-08-16T06-00-00Z.json").write_text(json.dumps({
            "meta": {"spec": "foreign-tool@1.0.0", "audited_at": "2026-08-16T06:00:00Z"},
            "tree": {"resolved": True},
            "totals": {"packages": 6, "time_bomb": 3, "covered": 2},
            "integrity": {"status": "pass"},
        }))
        (good / "2026-08-15T06-00-00Z.json").write_text("{}")
        broken = self.dir / "broken@1.0.0"
        broken.mkdir()
        (broken / "2026-08-16T06-00-00Z.json").write_text("{not json")

    def test_a_missing_directory_is_empty_not_an_error(self):
        from pathlib import Path
        self.assertEqual(intake.index(Path("/nonexistent/reports")), [])

    def test_it_lists_the_newest_audit_per_spec(self):
        rows = intake.index(self.dir)
        row = next(r for r in rows if r["spec"] == "foreign-tool@1.0.0")
        self.assertEqual(row["audited_at"], "2026-08-16T06:00:00Z")
        self.assertEqual(row["audits"], 2)
        self.assertEqual(row["totals"]["time_bomb"], 3)

    def test_an_unreadable_report_is_listed_not_skipped(self):
        """A silently shorter list is the failure mode, even on a minor page."""
        rows = intake.index(self.dir)
        self.assertEqual(len(rows), 2)
        broken = next(r for r in rows if r["spec"] == "broken@1.0.0")
        self.assertTrue(broken["unreadable"])
        self.assertEqual(broken["integrity"], "fail")


class Rendering(unittest.TestCase):
    def test_the_report_page_renders_without_the_dashboard_nav(self):
        from recon.render import pages
        html = pages.intake(audit())
        self.assertTrue(html.startswith("<!doctype html>"))
        # A report is served from `reports/<spec>/`, where a tab href would
        # resolve inside that directory rather than at the site root.
        self.assertNotIn('href="queue.html"', html)
        self.assertIn("recon dashboard", html)

    def test_an_unknown_join_never_renders_a_coverage_count(self):
        from recon.render import pages
        html = pages.intake(audit(inventory=intake.Inventory.unavailable("503")))
        self.assertIn("Coverage overlap is <b>unknown</b>", html)
        self.assertNotIn("already covered by", html)

    def test_the_unresolved_report_renders(self):
        from recon.render import pages
        html = pages.intake(audit(spec="no-such-package@9.9.9"))
        self.assertIn("could not be resolved", html)
        self.assertIn("banner fail", html)


if __name__ == "__main__":
    unittest.main()
