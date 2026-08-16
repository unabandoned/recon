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
| `recon/lockfile.py` | Read a committed npm/pnpm lockfile. Parsing, never resolving |
| `recon/compare.py` | Diff two repositories' lockfiles — added/dropped/replaced/bumped/pinned |
| `recon/intake.py` | §7b: audit a foreign tree, join it against the fork inventory |
| `recon/integrity.py` | Every check, as a value rather than an exception |
| `recon/observation.py` | The pipeline. `build_core` cannot see history; `finish` adds integrity |
| `recon/snapshots.py` | Write, diff, trend, merge attribution |
| `recon/fixtures.py` | Org-level ground truth: asserted edges, paths and counts |
| `recon/metadata.py` | The `.unabandoned.yml` schema — editorial fields only |
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
- **M3** reads every fixture from `fixtures/org.yml`: asserted `edges` (which fork
  declares which sibling), `paths` (how a package is reached), and `counts`. An empty
  fixture set warns rather than passes — see the fourth bug below for why that is not
  a stylistic choice.
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

Worth recording. The first two are the exact class the design predicts. The last
two are classes the design did *not* anticipate, and both are failures of the
checking machinery rather than the thing checked: one cries wolf, the other never
cries at all.

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

**A check passed for its whole life without verifying anything.**
`m3.expected-siblings` read an `expects-sibling` list from each fork's own
`.unabandoned.yml`. The co-location argument for putting it there was sound —
a fact about one fork should change in the same pull request as the wiring it
describes — and the cost was fatal: asserting the org's edge set meant 27 pull
requests against 27 repositories. Nobody opened the first one. The check looped
over 27 empty lists, asserted nothing, and fell through to PASS, reporting
`0 hand-asserted edge(s) reproduced by the build` in green while 32 real edges
went unverified.

Two separate faults, and the second is the one that generalises. The placement
made the check expensive to satisfy; the *default* made it silent about being
unsatisfied. A check whose zero-evidence branch is PASS cannot fail, and a check
that cannot fail is worse than no check, because it occupies the slot where a
real one would go and reports the colour of a real one that is working. The
fixtures now live in `fixtures/org.yml` — one repository, one pull request — and
the empty case is a WARN that names how many derived edges nothing is asserting.
A pass states its coverage (`3 of 32`) rather than its raw count, because
"3 edges reproduced" reads as complete and "3 of 32" cannot.

The same shape is worth grepping for elsewhere: any check that iterates a
collection, `continue`s on the empty element, and returns PASS after the loop.

**The coverage join reported that we do not maintain a package we maintain.**
Found by reading the first real intake report rather than by any check.
`@unabandoned/jsonstream` is the org's fork of `JSONStream`; npm requires
*scoped* package names to be lowercase, while unscoped legacy names need not
be, so the two can never match exactly. The exact-match join marked it
uncovered and the adoption plan proposed forking it a second time — and, worse,
labelled the intervention `@unabandoned/JSONStream`, a package that does not
exist. The join now casefolds and carries its match evidence (`exact` /
`case-insensitive`) into every covered row, because a reader who cannot see how
a match was made cannot catch it being wrong.

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
- **Intake's browser tier (§7b, "recon-lite").** Built as a spike, measured, and
  deliberately not shipped. See below.
- **The consumer badge feed.** Grades are computed and rendered; the embeddable badge
  and the RSS/JSON changelog are not.
- **Cutover.** `unabandoned/.github` still builds and publishes the old dashboard.
  Switching over means deciding §13 Q1 and Q7 and redirecting the old URL.

## Intake, and the tier that was measured and dropped

`recon.intake.audit()` is the authoritative tier: npm's real resolver, the same
classifier, the same dominator ranking, the same OSV join, rooted at a foreign
spec instead of a fork. It adds exactly one thing — the coverage join against
the org's own inventory — and that join is a `Fact`, so an unreadable inventory
produces `covered: null` and a report with **no** coverage totals at all rather
than a confident "0 covered, fork everything".

The spec (§7b) also called for an instant browser tier using npm's own
primitives, and made a specific claim: that a packument BFS would be
"~faithful for the reachable `(name, version)` set", diverging only on peer
deps, optional deps and `overrides`. It was built and measured against the
authoritative tier on the same spec:

| | packages | time bombs | wall clock | downloaded |
|---|---|---|---|---|
| npm's resolver | 39 | 13 | ~75 s (Actions) | — |
| recon-lite (browser) | 48 | 17 | 7 s | 3.4 MB |

**+23% packages, +31% time bombs.** The divergence is not the edge cases the
spec named; it is deduplication, which is most of what a resolver does. The
number recon-lite is most wrong about is the exact number the adoption decision
turns on.

Three other findings, all of which only appear once you build it:

- `npm-package-arg` reads a bare `process` global and imports `node:path`,
  `node:os`, `node:url`; `npm-install-checks` imports `node:fs` and
  `node:process`. Shimming was assumed safe on the grounds that only registry
  specs are ever resolved — **wrong**: `npm-pick-manifest` normalises every
  candidate manifest's `bin` field, so `path.basename` and `path.join` run on
  the first package. The shim has to be a *correct* POSIX `path`, which is a
  third implementation of something, in a repository whose thesis is that
  second implementations are where bugs live. The first draft of it disagreed
  with `node:path` on three of fifteen cases.
- Classification needs publish dates, and the abbreviated packument
  (`application/vnd.npm.install-v1+json`) has no `time` field. The full document
  is roughly twice the bytes — hence 3.4 MB for one 39-package tree, on a
  dashboard whose stated priority is the phone.
- Vendoring pulls nine packages (`npm-package-arg`, `npm-pick-manifest`,
  `semver`, `hosted-git-info`, `lru-cache`, `npm-install-checks`,
  `npm-normalize-package-bin`, `proc-log`, `validate-npm-package-name`) into a
  tool whose entire purpose is making invisible dependency trees visible.

None of this is fatal, and the spike works — it resolves real trees in a real
browser. It is recorded here rather than shipped because the trade it actually
offers is "7 seconds instead of a workflow dispatch, in exchange for an answer
that is 31% wrong and 3.4 MB heavy". The intake page says so in as many words,
with the numbers, rather than quietly omitting the feature. If it is ever
wanted, the honest version is a *fidelity-badged estimate* that refuses to print
a plan — and it should be built with `vendor/recon-lite/package.json` +
lockfile committed so Renovate can see the tree, plus a CI check that the
committed bundle still matches the lockfile.

## Compare: reading a lockfile is not resolving one

`recon.cli compare A B` takes two repository URLs and diffs their **committed
lockfiles**. It is the org's own thesis pointed outward — *a fork is only worth
carrying if the tree is actually healthier* — and it costs almost nothing,
because it does no resolution and touches no registry. Two files in, one diff
out.

That is not in tension with the decision to drop recon-lite. recon-lite was
rejected for *approximating npm's resolution* and being 31% wrong. A lockfile
has nothing to approximate: the tool already resolved and committed the answer,
and reading it is the only way to learn what a project actually installs rather
than what it would install today. The two conclusions come from the same rule,
not opposite ones.

Consequences of that rule, in the code:

- **A format we cannot read is refused, never resolved around.** `yarn.lock`
  and `bun.lockb` return a failed `Fact` naming the format. Falling back to
  `npm install` would report a tree the project does not install — `it-tools`
  pins exact versions while its upstream uses `^` ranges throughout, so the
  npm-resolved pair would differ in ways that are artifacts of the resolver.
- **An unread side produces no diff, not an empty one.** An empty diff renders
  as "these repositories are identical", which is the most confidently wrong
  thing this report could say. `compare.both-sides-read` fails and the page says
  there is no comparison.
- **Cross-tool comparisons warn.** npm and pnpm hoist and dedupe differently, so
  a tree-size delta between them measures the resolvers as much as the projects.
  Direct dependencies stay comparable and the check says exactly that.
- **`bump_kind` says `changed` when it cannot order two versions.** Guessing a
  direction for `2.0.0-rc.1` invents one the comparison does not have.
- **Workspace members are named, not merged.** A monorepo has many manifests;
  flattening them invents a dependency set no package declares.

Two bugs this found, both in the reading rather than the diffing:

**The peer suffix ate the package name.** pnpm records the peer context it
resolved against inside the version string — `'@tabler/icons-vue@3.20.0(vue@3.3.4)'`.
Splitting the ident on the last `@` therefore lands *inside the parenthetical*
and yields the package name `@tabler/icons-vue@3.20.0(vue`. Every such entry
became its own phantom package, inflating both trees: the real `it-tools` pair
is 1,063 → 1,219 packages, not the 1,268 → 1,456 the bug reported. The suffix
now comes off before the split, and it is the second time in this repository
that a "split on the last @" has been wrong for a reason nobody anticipated.

**A scoped republish is one decision, not two.** `composerize-ts` leaving while
`@thetechnetwork/composerize-ts` arrives is precisely the `@unabandoned` pattern
seen from outside, and reporting it as an unrelated add and drop buries the most
interesting thing a fork can do. Matched on the unscoped basename — the same
join `intake` uses for coverage, so the two agree on what "the same package
under a different owner" means.

## Notes for whoever picks this up

- Fixtures are only worth what they cost to write. Assert what you independently know;
  pasting the derived edge list into `org.yml` produces a golden file that detects change
  — which M5 already does — while proving nothing about correctness.
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
- An intake report is written under `reports/`, never `snapshots/`. That is not
  a naming convention, it is the enforcement: the org build globs `snapshots/`
  for history, so a report landing there would enter the differ and inflate the
  org's own counts with somebody else's tree. `tests/test_intake.py` asserts
  both the path and that neither `observation.py` nor `snapshots.py` mentions
  `reports` at all.
- The queue's scoring function is deliberately simple and legible. If it grows, keep
  every input visible next to the rank — a ranking nobody can check is exactly the
  kind of confident number this repository exists to distrust.
