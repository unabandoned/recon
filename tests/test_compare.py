"""Lockfile reading and two-repo comparison.

Offline and synthetic. The fixtures are shaped around the things that silently
go wrong when reading a lockfile — scoped names, alias entries, pnpm's peer
suffixes, workspaces — rather than around a tidy example.
"""
from __future__ import annotations

import json
import unittest

from recon import compare as C
from recon.facts import Fact
from recon import lockfile as L

NPM_LOCK = json.dumps({
    "lockfileVersion": 3,
    "packages": {
        "": {
            "dependencies": {"buffer": "npm:@unabandoned/buffer@^6", "left-pad": "^1.3.0"},
            "devDependencies": {"vitest": "1.0.0"},
        },
        "node_modules/buffer": {"name": "@unabandoned/buffer", "version": "6.0.4"},
        "node_modules/left-pad": {"version": "1.3.0"},
        "node_modules/vitest": {"version": "1.0.0"},
        "node_modules/left-pad/node_modules/inherits": {"version": "2.0.4"},
    },
})

PNPM_LOCK = """
lockfileVersion: '9.0'
importers:
  .:
    dependencies:
      '@tabler/icons-vue':
        specifier: ^3.20.0
        version: 3.20.0(vue@3.3.4)
      composerize-ts:
        specifier: 0.6.2
        version: 0.6.2
    devDependencies:
      vitest:
        specifier: ^1.0.0
        version: 1.0.0
packages:
  '@tabler/icons-vue@3.20.0':
    resolution: {integrity: sha512-x}
  'composerize-ts@0.6.2':
    resolution: {integrity: sha512-y}
snapshots:
  '@tabler/icons-vue@3.20.0(vue@3.3.4)': {}
  'composerize-ts@0.6.2': {}
"""

PNPM_WORKSPACE = """
lockfileVersion: '9.0'
importers:
  .:
    dependencies:
      root-dep:
        specifier: ^1.0.0
        version: 1.0.0
  packages/web:
    dependencies:
      member-only-dep:
        specifier: ^2.0.0
        version: 2.0.0
packages:
  'root-dep@1.0.0': {}
  'member-only-dep@2.0.0': {}
"""


class ReadNpm(unittest.TestCase):
    def setUp(self):
        fact = L.read(NPM_LOCK, "package-lock.json")
        self.assertTrue(fact.is_ok, fact.detail)
        self.lf = fact.payload

    def test_it_reads_direct_dependencies_with_their_specifiers(self):
        self.assertEqual(self.lf.direct["left-pad"].specifier, "^1.3.0")
        self.assertEqual(self.lf.direct["left-pad"].version, "1.3.0")

    def test_dev_dependencies_are_marked_not_dropped(self):
        self.assertTrue(self.lf.direct["vitest"].dev)
        self.assertNotIn("vitest", self.lf.runtime)
        self.assertIn("vitest", self.lf.direct)

    def test_an_alias_resolves_to_what_it_actually_is(self):
        """`"buffer": "npm:@unabandoned/buffer@^6"` — the scope is in the value.

        Reading the directory name instead of `name` is the bug that made every
        fork-to-fork edge invisible in the original dashboard.
        """
        self.assertIn("@unabandoned/buffer", self.lf.resolved)
        self.assertNotIn("buffer", self.lf.resolved)

    def test_nested_copies_are_counted(self):
        self.assertIn("inherits", self.lf.resolved)

    def test_lockfile_version_1_is_refused_rather_than_read_as_empty(self):
        fact = L.read(json.dumps({"lockfileVersion": 1, "dependencies": {}}),
                      "package-lock.json")
        self.assertFalse(fact.is_ok)
        self.assertIn("lockfileVersion 1", fact.detail)


class ReadPnpm(unittest.TestCase):
    def setUp(self):
        fact = L.read(PNPM_LOCK, "pnpm-lock.yaml")
        self.assertTrue(fact.is_ok, fact.detail)
        self.lf = fact.payload

    def test_the_peer_suffix_is_not_part_of_the_version(self):
        """`3.20.0(vue@3.3.4)` records the peer context pnpm resolved against.

        Comparing it as a version reports a bump every time an unrelated peer
        moves, which is a diff about nothing.
        """
        self.assertEqual(self.lf.direct["@tabler/icons-vue"].version, "3.20.0")
        self.assertEqual(self.lf.resolved["@tabler/icons-vue"], {"3.20.0"})

    def test_scoped_names_survive_the_ident_split(self):
        self.assertEqual(L.split_ident("@scope/pkg@1.2.3"), ("@scope/pkg", "1.2.3"))
        self.assertEqual(L.split_ident("pkg@1.2.3"), ("pkg", "1.2.3"))
        self.assertEqual(L.split_ident("@scope/pkg@3.2.0(vue@3.3.4)"),
                         ("@scope/pkg", "3.2.0"))

    def test_an_exact_specifier_reads_as_pinned(self):
        self.assertTrue(self.lf.direct["composerize-ts"].pinned)
        self.assertFalse(self.lf.direct["@tabler/icons-vue"].pinned)

    def test_workspace_members_are_named_not_flattened_in(self):
        """A monorepo has many manifests; merging them invents a set nobody declares."""
        lf = L.read(PNPM_WORKSPACE, "pnpm-lock.yaml").payload
        self.assertIn("root-dep", lf.direct)
        self.assertNotIn("member-only-dep", lf.direct)
        self.assertEqual(L.workspace_members(PNPM_WORKSPACE), ["packages/web"])


class Refusals(unittest.TestCase):
    """An unreadable format is refused, never quietly resolved with npm instead."""

    def test_yarn_and_bun_are_refused_by_name(self):
        for name in ("yarn.lock", "bun.lockb", "bun.lock"):
            fact = L.read("whatever", name)
            self.assertFalse(fact.is_ok, name)
            self.assertIn("not a format recon reads", fact.detail)

    def test_the_refusal_explains_why_falling_back_would_be_worse(self):
        detail = L.read("x", "yarn.lock").detail
        self.assertIn("does not install", detail)

    def test_an_unknown_name_is_a_failed_read(self):
        self.assertFalse(L.read("x", "Cargo.lock").is_ok)

    def test_malformed_content_is_a_failed_read_not_an_empty_one(self):
        self.assertFalse(L.read("{not json", "package-lock.json").is_ok)


class BumpKind(unittest.TestCase):
    def test_it_ranks_the_ordinary_cases(self):
        self.assertEqual(C.bump_kind("1.0.0", "2.0.0"), C.MAJOR)
        self.assertEqual(C.bump_kind("1.0.0", "1.1.0"), C.MINOR)
        self.assertEqual(C.bump_kind("1.0.0", "1.0.1"), C.PATCH)
        self.assertEqual(C.bump_kind("2.0.0", "1.9.9"), C.DOWNGRADE)

    def test_it_refuses_to_rank_what_it_cannot_order(self):
        """A confident direction from a comparison that does not have one is
        worse than saying `changed`."""
        self.assertEqual(C.bump_kind("2.0.0-rc.1", "2.0.0-rc.2"), C.CHANGED)
        self.assertEqual(C.bump_kind("", "1.0.0"), C.CHANGED)
        self.assertEqual(C.bump_kind("latest", "1.0.0"), C.CHANGED)


class Compare(unittest.TestCase):
    def diff(self, baseline_deps, subject_deps):
        def lf(deps):
            return L.Lockfile(
                L.PNPM, "9.0",
                {n: L.Dep(n, spec, ver, dev) for n, spec, ver, dev in deps},
                {n: {ver} for n, _, ver, _ in deps},
            )
        return C.compare(lf(baseline_deps), lf(subject_deps))

    def test_a_scoped_republish_is_a_replacement_not_an_add_and_a_drop(self):
        """The `@unabandoned` pattern, seen from outside.

        `composerize-ts` leaving while `@thetechnetwork/composerize-ts` arrives
        is one decision, and reporting it as two unrelated events buries the
        single most interesting thing a fork can do.
        """
        d = self.diff(
            [("composerize-ts", "0.6.2", "0.6.2", False)],
            [("@thetechnetwork/composerize-ts", "0.9.1", "0.9.1", False)],
        )
        self.assertEqual(d["totals"]["replaced"], 1)
        self.assertEqual(d["totals"]["added"], 0)
        self.assertEqual(d["totals"]["removed"], 0)
        r = d["direct"]["replaced"][0]
        self.assertEqual(r["was"], "composerize-ts")
        self.assertEqual(r["now"], "@thetechnetwork/composerize-ts")
        self.assertEqual(r["into_scope"], "@thetechnetwork")

    def test_an_unrelated_add_and_drop_stays_two_events(self):
        d = self.diff(
            [("alpha", "^1.0.0", "1.0.0", False)],
            [("beta", "^1.0.0", "1.0.0", False)],
        )
        self.assertEqual(d["totals"]["replaced"], 0)
        self.assertEqual(d["totals"]["added"], 1)
        self.assertEqual(d["totals"]["removed"], 1)

    def test_pinning_direction_is_named(self):
        d = self.diff(
            [("x", "^1.0.0", "1.0.0", False)],
            [("x", "1.0.0", "1.0.0", False)],
        )
        self.assertEqual(d["direct"]["pinning"][0]["direction"], "pinned")
        self.assertEqual(d["totals"]["bumped"], 0)   # the version did not move

    def test_majors_sort_above_patches(self):
        d = self.diff(
            [("a", "^1", "1.0.0", False), ("b", "^1", "1.0.0", False)],
            [("a", "^1", "1.0.1", False), ("b", "^2", "2.0.0", False)],
        )
        self.assertEqual([r["package"] for r in d["direct"]["bumped"]], ["b", "a"])
        self.assertEqual(d["totals"]["bumped_major"], 1)

    def test_identical_manifests_say_so(self):
        d = self.diff(
            [("x", "^1.0.0", "1.0.0", False)],
            [("x", "^1.0.0", "1.0.0", False)],
        )
        self.assertIn("identical", C.headline(d))

    def test_the_headline_matches_the_tables(self):
        d = self.diff(
            [("gone", "^1", "1.0.0", False), ("same", "^1", "1.0.0", False)],
            [("new", "^1", "1.0.0", False), ("same", "^2", "2.0.0", False)],
        )
        line = C.headline(d)
        self.assertIn("1 added", line)
        self.assertIn("1 dropped", line)
        self.assertIn("1 bumped (1 major)", line)


class _FakeSession:
    def summary(self):
        return {"attempted": 2, "failed": 0, "failures": []}


class Report(unittest.TestCase):
    """Assembly, and the rule that an unread side is not an empty diff."""

    def ok(self, tool=L.PNPM):
        return Fact.ok(
            L.Lockfile(tool, "9.0", {"x": L.Dep("x", "^1.0.0", "1.0.0")}, {"x": {"1.0.0"}}),
            source=f"o/r@main/{tool}",
        )

    def build(self, a, b):
        return C.build_report(
            a, b, baseline_ref="o/a", subject_ref="o/b",
            session=_FakeSession(), compared_at="2026-08-16T06:00:00Z",
        )

    def test_a_clean_pair_produces_a_diff_and_passes(self):
        r = self.build(self.ok(), self.ok())
        self.assertIsNotNone(r["diff"])
        self.assertEqual(r["integrity"]["status"], "pass")

    def test_an_unread_side_produces_no_diff_rather_than_an_empty_one(self):
        """An empty diff renders as "these repositories are identical", which is
        the most confidently wrong thing this report could say."""
        r = self.build(self.ok(), Fact.failed("HTTP 404"))
        self.assertIsNone(r["diff"])
        self.assertEqual(r["headline"], "")
        self.assertEqual(r["integrity"]["status"], "fail")
        check = next(c for c in r["integrity"]["checks"]
                     if c["id"] == "compare.both-sides-read")
        self.assertEqual(check["data"]["unread"], ["subject"])

    def test_a_refused_lockfile_format_reaches_the_report(self):
        r = self.build(self.ok(), L.read("x", "yarn.lock"))
        self.assertIsNone(r["diff"])
        self.assertIn("yarn", r["integrity"]["checks"][0]["detail"])

    def test_comparing_across_package_managers_warns(self):
        """Direct deps stay comparable; the resolved trees measure the resolvers."""
        r = self.build(self.ok(L.NPM), self.ok(L.PNPM))
        self.assertIsNotNone(r["diff"])
        check = next(c for c in r["integrity"]["checks"]
                     if c["id"] == "compare.same-package-manager")
        self.assertEqual(check["status"], "warn")
        self.assertEqual(r["integrity"]["status"], "warn")

    def test_the_report_is_not_shaped_like_an_observation(self):
        r = self.build(self.ok(), self.ok())
        self.assertEqual(r["kind"], "compare")
        for key in ("forks", "edges", "queue", "packages"):
            self.assertNotIn(key, r)


class Rendering(unittest.TestCase):
    def test_both_shapes_render(self):
        from recon.render import pages
        good = C.build_report(
            Fact.ok(L.Lockfile(L.PNPM, "9.0", {"x": L.Dep("x", "^1", "1.0.0")}, {"x": {"1.0.0"}})),
            Fact.ok(L.Lockfile(L.PNPM, "9.0", {"y": L.Dep("y", "^1", "1.0.0")}, {"y": {"1.0.0"}})),
            baseline_ref="o/a", subject_ref="o/b", session=_FakeSession())
        html = pages.compare(good)
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertNotIn('href="queue.html"', html)   # served outside the site root

        broken = C.build_report(
            Fact.failed("HTTP 404"), Fact.failed("HTTP 404"),
            baseline_ref="o/a", subject_ref="o/b", session=_FakeSession())
        html = pages.compare(broken)
        self.assertIn("No comparison", html)
        self.assertIn("banner fail", html)


if __name__ == "__main__":
    unittest.main()
