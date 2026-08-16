"""End-to-end: a whole build over a fixture org, offline and deterministic.

This is the test that proves the pieces compose — discovery, resolution,
classification, the advisory join, dominators, the integrity checks and every
rendered page — and the one that makes the reproducibility claim checkable
rather than aspirational.
"""
from __future__ import annotations

import datetime
import json
import tempfile
import unittest
from pathlib import Path

from tests import world

from recon import fixtures as fixtures_mod
from recon import observation as obs_mod
from recon import snapshots
from recon.github import GitHub
from recon.http import Session
from recon.registry import Registry
from recon.render import pages

TODAY = datetime.date(2026, 8, 16)
CLOCK = "2026-08-16T06:00:00Z"


def build(w: world.World | None = None, *, advisories: bool = True):
    w = w or world.World()
    session = Session(clock=lambda: CLOCK, opener=w.opener, retries=0)
    github = GitHub(session, org=world.ORG, token="fake")
    registry = Registry(session)
    inputs = obs_mod.gather(
        github, registry, session, org=world.ORG, today=TODAY,
        resolver=w.resolver, builder_sha="deadbeefcafe",
        with_advisories=advisories,
    )
    return w, inputs, obs_mod.build_core(inputs)


def finished(core, inputs, *, previous=None, fixtures=None):
    checks = obs_mod.run_checks(
        core, fixtures=fixtures or {"paths": [], "counts": []},
        previous=previous, acknowledged=set(),
        rederived=obs_mod.canonical(obs_mod.strip(core)),
    )
    return obs_mod.finish(core, checks, built_at=CLOCK, duration_ms=1234)


class Discovery(unittest.TestCase):
    def setUp(self):
        self.w, self.inputs, self.core = build()

    def test_only_repos_with_valid_metadata_are_forks(self):
        self.assertEqual(
            [f["package"] for f in self.core["forks"]],
            ["@unabandoned/browserify", "@unabandoned/buffer",
             "@unabandoned/crypto-browserify", "@unabandoned/detective",
             "@unabandoned/ieee754"],
        )

    def test_a_repo_without_metadata_is_excluded_with_a_reason_not_dropped(self):
        """browserify-sign has a repo and no yml. It used to be invisible."""
        excluded = {x["repo"]: x["reason"] for x in self.core["coverage"]["repos"]["excluded"]}
        self.assertEqual(excluded["browserify-sign"], "no-metadata")
        self.assertEqual(excluded["infra"], "no-metadata")

    def test_archived_repos_are_not_discovered_at_all(self):
        names = {x["repo"] for x in self.core["coverage"]["repos"]["excluded"]}
        self.assertNotIn("old-thing", names)

    def test_the_ledger_balances(self):
        repos = self.core["coverage"]["repos"]
        self.assertEqual(
            repos["discovered"], repos["included"] + len(repos["excluded"])
        )


class Classification(unittest.TestCase):
    def setUp(self):
        self.w, self.inputs, self.core = build()
        self.packages = {p["name"]: p for p in self.core["packages"]}

    def test_abandoned_with_deps_is_a_time_bomb(self):
        self.assertEqual(self.packages["hash-base"]["state"], "time_bomb")

    def test_abandoned_without_deps_is_inert(self):
        self.assertEqual(self.packages["inherits"]["state"], "inert")

    def test_recently_released_is_alive(self):
        """A fork of ours, published last month, resolved as a sibling."""
        self.assertEqual(self.packages["@unabandoned/ieee754"]["state"], "alive")

    def test_being_on_latest_is_not_a_health_signal(self):
        """readable-stream 3.6.2 IS the latest release — and it is four years
        old and carries dependencies, so it is rotting regardless."""
        self.assertEqual(self.packages["readable-stream"]["state"], "time_bomb")

    def test_a_time_bomb_with_an_advisory_is_an_emergency(self):
        self.assertEqual(self.core["totals"]["emergencies"], 1)

    def test_an_unreadable_packument_is_unknown_not_alive(self):
        """through2 has no packument in the fixture world. It must not pass as healthy."""
        self.assertEqual(self.packages["through2"]["state"], "unknown")
        self.assertIn("release date unavailable", self.packages["through2"]["reason"])

    def test_the_unknown_is_counted_in_the_totals(self):
        self.assertEqual(self.core["totals"]["unknown"], 1)

    def test_every_package_carries_its_evidence(self):
        for pkg in self.core["packages"]:
            self.assertIn("status", pkg["evidence"]["last_release"])
            self.assertTrue(pkg["reason"])


class Edges(unittest.TestCase):
    def setUp(self):
        self.w, self.inputs, self.core = build()

    def test_a_sibling_reached_through_an_intermediary_is_not_a_declared_edge(self):
        """The false alarm the first real build produced.

        browserify reaches @unabandoned/detective only through the third-party
        `module-deps`. That is module-deps' edge, not browserify's — so it must
        not appear as a fork->fork edge, and M2 must not expect the manifest
        reader to have found it. Reader B used to collect every sibling anywhere
        in the subtree, which guaranteed a disagreement for any fork wired this
        way, and duly failed the build on 4 real forks.
        """
        edges = {(e["from"], e["to"]) for e in self.core["edges"]}
        self.assertNotIn(
            ("@unabandoned/browserify", "@unabandoned/detective"), edges
        )
        # But the fact itself is not thrown away.
        browserify = next(f for f in self.core["forks"]
                          if f["package"] == "@unabandoned/browserify")
        self.assertIn("@unabandoned/detective", browserify["tree"]["scope_reachable"])

    def test_the_two_readers_are_asked_the_same_question(self):
        """Reader B must be declared-only, matching what the manifest reader sees."""
        tree = self.w.resolver("@unabandoned/browserify").value
        self.assertEqual(list(tree.scope_edges), ["@unabandoned/crypto-browserify"])
        self.assertIn("@unabandoned/detective", tree.scope_reachable)
        self.assertNotEqual(set(tree.scope_edges), set(tree.scope_reachable))

    def test_alias_wired_edges_are_derived_by_both_readers(self):
        """The regression that made every fork->fork edge invisible."""
        edges = {(e["from"], e["to"]): e["derivation"] for e in self.core["edges"]}
        self.assertEqual(edges[("@unabandoned/buffer", "@unabandoned/ieee754")], "both")
        self.assertEqual(
            edges[("@unabandoned/browserify", "@unabandoned/crypto-browserify")], "both"
        )

    def test_the_graph_is_not_a_set_of_isolated_nodes(self):
        self.assertGreaterEqual(self.core["totals"]["edges"], 2)

    def test_consumer_edges_come_from_used_by(self):
        self.assertIn(
            {"from": "some-app", "to": "@unabandoned/browserify",
             "kind": "consumer", "derivation": "used-by"},
            self.core["consumer_edges"],
        )


class Issues(unittest.TestCase):
    def setUp(self):
        self.w, self.inputs, self.core = build()
        self.forks = {f["repo"]: f for f in self.core["forks"]}

    def test_renovate_dashboard_is_not_counted_as_work(self):
        """Every fixture fork carries the dashboard; only browserify has real work."""
        self.assertEqual(self.forks["browserify"]["open_issues"]["value"], 1)
        self.assertEqual(self.forks["buffer"]["open_issues"]["value"], 0)

    def test_what_was_excluded_is_itself_reported(self):
        """So the filter can never quietly grow into hiding real work."""
        self.assertEqual(self.forks["buffer"]["excluded_issues"]["value"], 1)

    def test_the_dashboard_is_still_linked(self):
        self.assertIn("issues/100", self.forks["buffer"]["dependency_dashboard"]["value"])


class Advisories(unittest.TestCase):
    def test_an_advisory_is_joined_onto_the_package(self):
        _, _, core = build()
        rs = next(p for p in core["packages"] if p["name"] == "readable-stream")
        self.assertEqual([a["id"] for a in rs["advisories"]], ["GHSA-test-0001"])
        self.assertEqual(rs["advisories"][0]["severity"], "HIGH")

    def test_a_failed_advisory_read_is_unknown_never_no_advisories(self):
        w = world.World(fail_urls={"api.osv.dev"})
        _, _, core = build(w)
        self.assertEqual(core["coverage"]["advisories"]["status"], "failed")
        self.assertEqual(core["totals"]["with_advisories"], 0)
        # And the page must not be able to claim "no advisories" from that.
        self.assertNotEqual(core["coverage"]["advisories"]["status"], "ok")


class Queue(unittest.TestCase):
    def setUp(self):
        self.w, self.inputs, self.core = build()
        self.entries = {q["package"]: q for q in self.core["queue"]}

    def test_the_dominator_is_queued_not_the_leaf_beneath_it(self):
        self.assertIn("hash-base", self.entries)

    def test_every_entry_shows_the_route_it_came_from(self):
        for entry in self.core["queue"]:
            self.assertTrue(entry["paths"])
            for path in entry["paths"]:
                self.assertIn("fork", path)

    def test_hash_base_reaches_browserify_through_our_own_fork(self):
        """The fact the flat-columns table hid: browserify inherits it via
        crypto-browserify, so fixing crypto-browserify closes browserify too."""
        paths = {p["fork"]: p["via"] for p in self.entries["hash-base"]["paths"]}
        self.assertEqual(
            paths["@unabandoned/browserify"], ["@unabandoned/crypto-browserify"]
        )

    def test_every_entry_offers_options_with_costs(self):
        for entry in self.core["queue"]:
            self.assertTrue(entry["options"])
            for opt in entry["options"]:
                self.assertTrue(opt["effect"])
                self.assertTrue(opt["cost"])


class Integrity(unittest.TestCase):
    def test_a_clean_build_passes_every_check(self):
        w, inputs, core = build()
        obs = finished(core, inputs)
        failures = [c for c in obs["integrity"]["checks"] if c["status"] == "fail"]
        self.assertEqual(failures, [], msg=json.dumps(failures, indent=2))

    def test_declared_sibling_edges_are_reproduced(self):
        w, inputs, core = build()
        obs = finished(core, inputs)
        check = next(c for c in obs["integrity"]["checks"] if c["id"] == "m3.expected-siblings")
        self.assertEqual(check["status"], "pass")
        self.assertEqual(check["data"]["asserted"], 2)

    def test_a_stale_org_fixture_fails_the_build(self):
        w, inputs, core = build()
        obs = finished(core, inputs, fixtures={
            "paths": [{"fork": "@unabandoned/browserify", "package": "readable-stream",
                       "via": ["browserify-sign"]}],
            "counts": [],
        })
        self.assertEqual(obs["integrity"]["status"], "fail")

    def test_a_true_org_fixture_passes(self):
        w, inputs, core = build()
        obs = finished(core, inputs, fixtures={
            "paths": [{"fork": "@unabandoned/browserify", "package": "readable-stream",
                       "via": ["@unabandoned/crypto-browserify", "hash-base"]}],
            "counts": [{"metric": "open_issues", "subject": "browserify", "equals": 1}],
        })
        self.assertEqual(obs["integrity"]["status"], "pass")

    def test_m1_check_sees_the_failed_packument(self):
        w, inputs, core = build()
        obs = finished(core, inputs)
        check = next(c for c in obs["integrity"]["checks"]
                     if c["id"] == "m1.unknowns-have-reasons")
        self.assertEqual(check["status"], "pass")
        self.assertGreaterEqual(check["data"]["failed_fetches"], 1)
        self.assertGreaterEqual(check["data"]["unknown"], 1)


class Reproducibility(unittest.TestCase):
    def test_two_builds_of_the_same_world_are_byte_identical(self):
        _, _, a = build()
        _, _, b = build()
        self.assertEqual(obs_mod.canonical(obs_mod.strip(a)),
                         obs_mod.canonical(obs_mod.strip(b)))

    def test_derivation_never_reads_the_snapshot_directory(self):
        """The invariant that keeps history from becoming a second source of truth."""
        w, inputs, core = build()
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "snapshots"
            obs = finished(core, inputs)
            snapshots.write(snap, obs, at=CLOCK)
            rederived = obs_mod.rederive_with_history_masked(inputs, snap)
        self.assertEqual(obs_mod.canonical(obs_mod.strip(core)), rederived)

    def test_canonical_output_is_sorted(self):
        _, _, core = build()
        text = obs_mod.canonical(obs_mod.strip(core))
        self.assertEqual(json.loads(text), obs_mod.strip(core))
        self.assertTrue(text.endswith("\n"))


class SnapshotsAndDiff(unittest.TestCase):
    def test_first_build_is_a_baseline(self):
        w, inputs, core = build()
        obs = finished(core, inputs)
        self.assertTrue(snapshots.diff(None, obs)["baseline"])

    def test_a_state_transition_is_reported(self):
        w, inputs, core = build()
        current = finished(core, inputs)
        previous = json.loads(json.dumps(current))
        for pkg in previous["packages"]:
            if pkg["name"] == "hash-base":
                pkg["state"] = "inert"
        delta = snapshots.diff(previous, current)
        move = next(t for t in delta["transitions"] if t["package"] == "hash-base")
        self.assertEqual((move["was"], move["now"]), ("inert", "time_bomb"))
        self.assertTrue(move["worse"])

    def test_a_moved_fork_gets_a_compare_url(self):
        w, inputs, core = build()
        current = finished(core, inputs)
        previous = json.loads(json.dumps(current))
        previous["forks"][0]["head_sha"]["value"] = "0000000000000000000000000000000000000000"
        delta = snapshots.diff(previous, current, org=world.ORG)
        moved = next(f for f in delta["forks"] if f["change"] == "moved")
        self.assertIn("/compare/0000000", moved["compare"])

    def test_trend_reads_written_snapshots(self):
        w, inputs, core = build()
        obs = finished(core, inputs)
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp)
            snapshots.write(snap, obs, at="2026-08-14T06:00:00Z")
            snapshots.write(snap, obs, at="2026-08-15T06:00:00Z")
            series = snapshots.trend(snap)
        self.assertEqual(len(series["time_bomb"]), 2)


class Rendering(unittest.TestCase):
    def setUp(self):
        self.w, self.inputs, self.core = build()
        self.obs = finished(self.core, self.inputs)
        self.delta = snapshots.diff(None, self.obs)
        self.html = pages.render_all(self.obs, self.delta, {})

    def test_every_page_renders(self):
        self.assertEqual(sorted(self.html), sorted(
            ["index.html", "forks.html", "queue.html", "packages.html",
             "topology.html", "changes.html", "health.html"]))
        for name, doc in self.html.items():
            self.assertTrue(doc.startswith("<!doctype html>"), name)
            self.assertIn("</html>", doc)

    def test_unknown_renders_as_unknown_not_as_a_number(self):
        self.assertIn("through2", self.html["packages.html"])
        self.assertIn("unknown", self.html["packages.html"])

    def test_excluded_repos_are_visible_on_the_health_page(self):
        self.assertIn("browserify-sign", self.html["health.html"])
        self.assertIn("no-metadata", self.html["health.html"])

    def test_aggregates_carry_denominators(self):
        self.assertIn("of 8 resolved", self.html["index.html"])

    def test_the_integrity_banner_is_on_every_page(self):
        for name, doc in self.html.items():
            self.assertIn("integrity", doc.lower(), name)

    def test_a_failing_check_produces_a_red_banner(self):
        obs = finished(self.core, self.inputs, fixtures={
            "paths": [{"fork": "@unabandoned/browserify", "package": "nope"}],
            "counts": [],
        })
        html = pages.overview(obs, {})
        self.assertIn("not trustworthy", html)
        self.assertIn("banner fail", html)

    def test_every_table_cell_carries_its_column_label(self):
        """The phone layout hides `thead` and renders each row as a card, so a
        cell with no label loses the only thing that said what it was."""
        from recon.render.components import table
        out = table(["Package", "Forks"],
                    ['<tr><td class="mono">once</td><td class="num">3</td></tr>'])
        self.assertIn('<td data-label="Package" class="mono">', out)
        self.assertIn('<td data-label="Forks" class="num">', out)

    def test_a_full_width_band_is_left_unlabelled(self):
        """A group header spans every column, so no single heading names it."""
        from recon.render.components import table
        out = table(["A", "B"], ['<tr class="group-head"><td colspan="2">time bomb</td></tr>'])
        self.assertIn('<td colspan="2">', out)
        self.assertNotIn("data-label", out.split("<tbody>")[1])

    def test_pages_carry_the_narrow_screen_rules(self):
        for name, doc in self.html.items():
            self.assertIn("@media (max-width: 720px)", doc, name)
            self.assertIn("@media (max-width: 900px)", doc, name)

    def test_topology_ships_a_narrow_screen_alternative(self):
        """A node-link diagram is the wrong form at 390px, so the same edges are
        emitted as a list and CSS picks one — no JS, no second request."""
        self.assertIn("topo-narrow", self.html["topology.html"])
        self.assertIn("topo-list", self.html["topology.html"])

    def test_no_page_escapes_its_own_markup(self):
        """A crude but effective guard against unescaped interpolation."""
        for name, doc in self.html.items():
            self.assertNotIn("<script", doc.lower(), name)


class FixtureFile(unittest.TestCase):
    def test_a_missing_fixture_file_is_not_an_error(self):
        data, errors = fixtures_mod.load(Path("/nonexistent/org.yml"))
        self.assertEqual(errors, [])
        self.assertEqual(data, {"paths": [], "counts": []})

    def test_a_malformed_fixture_is_an_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as fh:
            fh.write("paths:\n  - fork: x\n")   # missing `package`
            path = Path(fh.name)
        data, errors = fixtures_mod.load(path)
        self.assertTrue(errors)
        path.unlink()


if __name__ == "__main__":
    unittest.main()
