# Implementation notes

What is built, where it lives, and what is deliberately not built yet. The design is
[`redesign.md`](./redesign.md); this is the map from it to the code.

## Layout

| Module | Responsibility |
|---|---|
| `recon/facts.py` | The `Fact` type. Reading `.value` on a non-ok fact raises. This is M1. |
| `recon/http.py` | JSON reads that return `Fact`s, plus a ledger of every attempt |
| `recon/github.py` | Discovery, per-fork live state, honest exclusions, the issue filter |
| `recon/registry.py` | One packument per name, memoised; two independent facts read out of it |
| `recon/resolve.py` | npm resolution, alias handling, node's lookup rule, both M2 readers |
| `recon/classify.py` | `alive` / `inert` / `time_bomb` / `unknown`, with evidence |
| `recon/graph.py` | Dominators, shadowing, blast radius, the ranked queue |
| `recon/osv.py` | Advisory batch join and the emergency tier |
| `recon/integrity.py` | Every check, as a value rather than an exception |
| `recon/observation.py` | The pipeline. `build_core` cannot see history; `finish` adds integrity |
| `recon/snapshots.py` | Write, diff, trend, merge attribution |
| `recon/fixtures.py` | Org-level ground truth loading |
| `recon/metadata.py` | The `.unabandoned.yml` schema, incl. `expects-sibling` |
| `recon/render/` | Theme, fact-aware components, computed SVG, the seven pages |

Dependencies: **PyYAML and the npm CLI.** Everything else is stdlib. The tests need
neither npm nor a network.

## Where each mechanism actually lives

- **M1** is structural, not a check. It is enforced by `Fact.value` raising, by
  `classify()` returning `State.UNKNOWN`, and by `render.components.value()` refusing
  to print anything without provenance. `integrity.unknowns_are_accounted` is the
  backstop that notices when a failed fetch produced no unknown anywhere — the exact
  signature of a failure path collapsing into the happy path.
- **M2** has two halves. `scope_edges_agree` compares
  `resolve.manifest_scope_edges` (our parser) against `Tree.scope_edges` (npm's
  resolver). `dependency_counts_agree` compares `registry.declared_deps` against the
  lockfile entry. The second half is new work, not a comparison of two things already
  computed: `ndeps` previously had a single source. The packument was already being
  fetched and its `versions[v].dependencies` discarded.
- **M3** reads `expects-sibling` from each fork's own metadata (co-located, so it gets
  updated in the same PR as the wiring) and `fixtures/org.yml` for cross-fork facts
  that have no single home.
- **M4** is four cheap assertions in `integrity.py`. The uniformity detector looks for
  a hard non-zero floor across every repo, which is what the Renovate-dashboard
  miscount actually looked like — the values varied, so uniformity alone said nothing,
  but no repo could ever reach zero.
- **M5** compares `totals` against the previous snapshot. An intended jump is named
  once via `RECON_ACK_DELTA`.
- **Reproducibility** is `observation.rederive_with_history_masked`, which re-runs the
  derivation with `RECON_SNAPSHOTS` pointed at an empty directory and compares the
  canonical bytes. `build_core` has no history parameter, so a correct build is
  identical by construction; the check exists to catch the regression where someone
  reaches around the parameter list.

## Bugs this has found

Worth recording. The first two are the exact class the design predicts. The third
is a class the design did *not* anticipate, and it is arguably the more dangerous
one, because its failure mode is a check that cries wolf.

**The coverage ledger under-counted its own fetches.** `coverage.fetches` was
snapshotted from `session.summary()` before `_fork_row` ran, and `_fork_row` still
fetches each fork's packument for its published version. The ledger reported 39
fetches where 41 had happened — an undercount, in the module whose job is to make
undercounts impossible. Caught by the snapshot-independence invariant, which noticed
that the second derivation disagreed with the first.

**A versioned spec silently re-rooted the whole analysis.** `resolve_tree("browserify@17.0.0")`
looked for `node_modules/browserify@17.0.0`, missed, fell back to the probe manifest,
and made `browserify` an ordinary node dominating all 178 others. The dominator
analysis was wrong and nothing errored. Caught by running the real resolver and
noticing the queue had exactly one entry. Now `spec_name()` handles it and
`tests/smoke.py` asserts the root every run.

**Reader B was asking a different question from Reader A.** The first real build
failed `m2.scope-edges-agree` on 4 forks. It was right that they disagreed and
wrong about what that meant: `manifest_scope_edges` reads the fork's *declared*
dependencies, while `Tree.scope_edges` collected every `@unabandoned/*` package
anywhere in the resolved subtree. A fork that reaches a sibling through a
third-party intermediary — `browserify → module-deps → @unabandoned/detective` —
would fail the check forever, and no wiring change could ever satisfy it.

Reader B is now declared-only, resolved through the lockfile so npm's alias
handling still does the work; the transitive set survives as
`Tree.scope_reachable` and is reported on the fork row, because "which of our own
packages end up under this one" is worth knowing — it just isn't an edge.

The first two bugs would not have been caught by looking at output. The third
*was* output, and it was still wrong: a check firing is evidence that the check
and the world disagree, not evidence about which one is at fault. The fixture
world now contains a sibling reached through an intermediary, so the old
semantics fail four tests including `test_a_clean_build_passes_every_check`.

**One check was removed rather than fixed.** `m4.zero-dep-sanity` warned when a
package's resolved version declared no dependencies while `latest` declared some.
On the first real build it fired on `buffer-xor@1.0.3` — no dependencies, against
a 2.x `latest` that has them. A package gaining dependencies in a later major is
ordinary, so the check compared two different versions' dependency lists and
called routine difference suspicious. `m2.dependency_counts_agree` is the
well-formed version of the same concern: same `(name, version)`, two independent
artifacts, hard failure. A check that fires on normal reality trains people to
skim past the panel, which costs more than it can catch.

## What is not built

- **Phase 4 — dual trees.** The runtime/dev split (the security-motivated half: the
  dev tree is what runs beside publish credentials) and published-vs-HEAD resolution.
  `resolve_tree(dev=True)` and `Tree.dev` exist and work; nothing calls them yet, and
  the observation has no `tree: "head"` rows.
- **Intake (§7b).** Neither tier. The authoritative tier is small — parameterise the
  audit root and join against the fork inventory — but it lands after Phase 4 so it
  can report grades honestly.
- **The consumer badge feed.** Grades are computed and rendered; the embeddable badge
  and the RSS/JSON changelog are not.
- **Cutover.** `unabandoned/.github` still builds and publishes the old dashboard.
  Switching over means deciding §13 Q1 and Q7 and redirecting the old URL.

## Notes for whoever picks this up

- The `expects-sibling` field is additive and the existing validator in
  `unabandoned/.github` does not reject unknown keys, so forks can adopt it before any
  cutover without breaking their CI.
- `tests/world.py` is a fixture org built to contain every shape that has bitten us:
  an alias-wired edge, a repo with no metadata, a packument that 404s, and a package
  reached only through one of our own forks. Add to it rather than mocking in-place.
- When an integrity check fails, suspect the check first. Two of the three defects
  above were in the checking machinery, not the thing being checked. The question
  to ask is "are both derivations answering the same question?" before "which fork
  is misconfigured?"
- The pages are built mobile-first at two breakpoints, and they answer different
  questions. **900px** is where a wide table stops working: `thead` is hidden and
  each row becomes a card of label/value pairs, using the `data-label` that
  `components.table` stamps onto every cell. **720px** is where the page chrome
  narrows: padding, one scrolling strip of tabs, single-column option grids.
  Check both when touching `render/` — a tablet at 768px is in card mode but full
  chrome, which is the combination easiest to break by accident.
- The topology ships twice: the computed SVG for wide screens and the same edges
  as a list for narrow ones, with CSS picking one. That is a form decision, not a
  fallback — the graph is 1,088px wide with its root centred, so a phone opens on
  empty canvas, and scaling it to fit puts the labels back under 3px.
- The queue's scoring function is deliberately simple and legible. If it grows, keep
  every input visible next to the rank — a ranking nobody can check is exactly the
  kind of confident number this repository exists to distrust.
