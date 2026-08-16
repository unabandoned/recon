"""`Fact` is the mechanism, so it gets the strictest tests.

The property under test is negative and unusual: it must be *impossible* to read
a value that was never fetched without saying so at the call site.
"""
from __future__ import annotations

import unittest

from recon.facts import Fact, Status, Unknown, tally


class ReadingFacts(unittest.TestCase):
    def test_ok_value_reads(self):
        f = Fact.ok(42, source="test", at="2026-08-16T00:00:00Z")
        self.assertEqual(f.value, 42)
        self.assertTrue(f.is_ok)

    def test_failed_value_raises(self):
        f = Fact.failed("HTTP 503", source="registry")
        with self.assertRaises(Unknown) as ctx:
            _ = f.value
        # The message has to name the source, or a stack trace three modules
        # deep is useless for finding which fetch died.
        self.assertIn("registry", str(ctx.exception))
        self.assertIn("HTTP 503", str(ctx.exception))

    def test_skipped_value_raises(self):
        with self.assertRaises(Unknown):
            _ = Fact.skipped("npm not on PATH").value

    def test_or_else_is_the_only_way_to_default(self):
        self.assertEqual(Fact.failed("boom").or_else(0), 0)
        self.assertEqual(Fact.ok(7).or_else(0), 7)

    def test_none_is_a_real_ok_value(self):
        """`ok(None)` means "we asked and the answer is nothing"."""
        f = Fact.ok(None, source="contents")
        self.assertIsNone(f.value)
        self.assertTrue(f.is_ok)


class MappingFacts(unittest.TestCase):
    def test_map_transforms_ok(self):
        self.assertEqual(Fact.ok([1, 2, 3]).map(len).value, 3)

    def test_map_propagates_failure_untouched(self):
        f = Fact.failed("timeout", source="s")
        mapped = f.map(len)
        self.assertFalse(mapped.is_ok)
        self.assertEqual(mapped.detail, "timeout")

    def test_map_converts_a_raise_into_a_failed_fact(self):
        """A packument that parses but has the wrong shape is a failed read of
        that packument — not a crashed build, and not a silent default."""
        f = Fact.ok({"no": "dist-tags"}).map(lambda d: d["dist-tags"]["latest"])
        self.assertFalse(f.is_ok)
        self.assertIn("KeyError", f.detail)

    def test_require_names_what_was_needed(self):
        with self.assertRaises(Unknown) as ctx:
            Fact.failed("404").require("the org repo list")
        self.assertIn("the org repo list", str(ctx.exception))


class Provenance(unittest.TestCase):
    def test_provenance_always_carries_status(self):
        p = Fact.ok(1, source="u", at="t").provenance()
        self.assertEqual(p["status"], "ok")
        self.assertEqual(p["source"], "u")
        self.assertEqual(p["fetched_at"], "t")

    def test_failed_provenance_carries_detail_not_value(self):
        p = Fact.failed("nope", source="u").provenance()
        self.assertEqual(p["status"], "failed")
        self.assertEqual(p["detail"], "nope")
        self.assertNotIn("value", p)

    def test_tally_counts_every_status(self):
        counts = tally([Fact.ok(1), Fact.ok(2), Fact.failed("x"), Fact.skipped("y")])
        self.assertEqual(counts, {"ok": 2, "failed": 1, "not_attempted": 1})


if __name__ == "__main__":
    unittest.main()
