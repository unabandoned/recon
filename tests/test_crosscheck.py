"""The browser implementation must agree with the Python one, exactly.

`recon/render/compare.js` is a second implementation of `recon.compare`, which
this repository normally treats as a defect — a second implementation is a
second home for the bug classes recon exists to catch. It is allowed on one
condition, and this is that condition: the two must produce **identical JSON**
for the same inputs.

A second implementation proven to agree is a cross-check, and a genuinely useful
one — it is mechanism M2 applied to recon's own rendering. One that is merely
believed to agree is the thing the whole repository is a reaction to.

Skipped when node is absent, so the offline suite still runs anywhere. CI has a
job that guarantees it actually runs.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from recon import compare as C
from recon import lockfile as L

JS = Path(__file__).resolve().parent.parent / "recon" / "render" / "compare.js"

#: Every shape that behaves differently, in one pair. A tidy example would agree
#: trivially and prove nothing.
CASES = [
    (
        "the it-tools shapes",
        {
            "dependencies": {
                "composerize-ts": "^0.6.2",          # -> a scoped republish
                "@tiptap/pm": "2.1.6",               # already pinned
                "@sindresorhus/slugify": "^2.2.1",   # -> pinned
                "@it-tools/bip39": "^0.0.4",         # dropped
                "buffer": "^5.0.0",                  # -> aliased
                "stable": "^1.0.0",
            },
            "devDependencies": {"vitest": "^1.0.0", "@types/node": "^18.0.0"},
        },
        {
            "dependencies": {
                "@thetechnetwork/composerize-ts": "0.9.1",
                "@tiptap/pm": "3.30.0",
                "@sindresorhus/slugify": "3.0.0",
                "@noble/hashes": "^1.0.0",           # added
                "buffer": "npm:@unabandoned/buffer@^6",
                "stable": "^1.0.0",
            },
            "devDependencies": {"vitest": "^1.0.0", "@types/node": "24.13.3"},
        },
    ),
    ("both empty", {}, {}),
    ("nothing but dev deps", {"devDependencies": {"a": "^1"}}, {"devDependencies": {"a": "2.0.0"}}),
    (
        "scoped on both sides, different scopes",
        {"dependencies": {"@a/thing": "^1"}},
        {"dependencies": {"@b/thing": "^1"}},
    ),
    (
        "an alias replaced by a different alias",
        {"dependencies": {"x": "npm:@one/x@^1"}},
        {"dependencies": {"x": "npm:@two/x@^1"}},
    ),
    (
        "a package that leaves its scope",
        {"dependencies": {"@ns/thing": "^1"}},
        {"dependencies": {"thing": "^1"}},
    ),
]


def _python(baseline: dict, subject: dict) -> dict:
    a = L.read_manifest(json.dumps(baseline))
    b = L.read_manifest(json.dumps(subject))
    assert a.is_ok and b.is_ok
    return C.compare(a.payload, b.payload)


def _node(baseline: dict, subject: dict) -> dict:
    script = f"""
const recon = require({str(JS)!r});
const a = recon.readManifest({json.dumps(baseline)});
const b = recon.readManifest({json.dumps(subject)});
process.stdout.write(JSON.stringify({{
  diff: recon.compare(a, b), headline: recon.headline(recon.compare(a, b))
}}));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise AssertionError(f"node failed: {proc.stderr[-500:]}")
        return json.loads(proc.stdout)
    finally:
        Path(path).unlink(missing_ok=True)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class BrowserAgreesWithPython(unittest.TestCase):
    def test_every_case_produces_identical_json(self):
        for label, baseline, subject in CASES:
            with self.subTest(label):
                got = _node(baseline, subject)
                want = _python(baseline, subject)
                # Canonical JSON on both sides: key order and formatting are not
                # the claim, the values are.
                self.assertEqual(
                    json.dumps(got["diff"], sort_keys=True),
                    json.dumps(want, sort_keys=True),
                    f"{label}: the browser and Python comparisons disagree",
                )

    def test_the_headline_sentence_matches_too(self):
        """It is the one line most readers will actually read."""
        for label, baseline, subject in CASES:
            with self.subTest(label):
                self.assertEqual(_node(baseline, subject)["headline"],
                                 C.headline(_python(baseline, subject)), label)

    def test_repo_parsing_agrees_including_the_refusals(self):
        script = f"""
const recon = require({str(JS)!r});
const out = {{}};
for (const v of {json.dumps([
    "https://github.com/owner/repo", "https://github.com/owner/repo.git",
    "git@github.com:owner/repo.git", "owner/repo@dev",
    "https://github.com/o/r/tree/feature/x",
    "https://example.com/", "not a repo", ""])}) {{
  try {{ const r = recon.parseRepo(v); out[v] = [r.owner, r.repo, r.ref]; }}
  catch (e) {{ out[v] = null; }}
}}
process.stdout.write(JSON.stringify(out));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
            got = json.loads(proc.stdout)
        finally:
            Path(path).unlink(missing_ok=True)

        for value, js_result in got.items():
            with self.subTest(value):
                try:
                    owner, repo, ref = L.parse_repo(value)
                    self.assertEqual(js_result, [owner, repo, ref])
                except ValueError:
                    self.assertIsNone(
                        js_result,
                        f"Python refuses {value!r} and the browser accepts it",
                    )


if __name__ == "__main__":
    unittest.main()
