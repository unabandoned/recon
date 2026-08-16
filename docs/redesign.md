# Dependency-Recon Dashboard Redesign — Scoping Document

**Scope:** design and phased plan only. No implementation code.

> **Editorial note.** The body below is the scoping document as written. Three factual claims
> were corrected against the current source in `unabandoned/.github@main`, and one missing
> section heading (§8) was restored; every change is itemised in
> [Appendix B](#appendix-b--claims-checked-against-the-current-source). The failure history the
> document argues from was referenced but not included, so it is reconstructed from the commits
> that fixed each failure in [Appendix A](#appendix-a--the-failure-history). Read the appendices
> as part of the document, not as commentary on it.

---

## 1. Summary of recommendation

Stay on GitHub Pages, static build, history via committed snapshots, and move the dashboard out
of `.github` into its own credential-free repo: [`unabandoned/recon`](https://github.com/unabandoned/recon)
(created). The failure history is entirely a *correctness* problem, not a freshness or
infrastructure problem — none of the five documented failures would have been prevented by a
Worker, KV, or D1, and all of them are addressed by structural changes to how the build treats
errors, derives facts, and remembers its own past. Spend the complexity budget on integrity
mechanisms, not runtime.

The single most important design change: **make "we don't know" unrepresentable as a benign
value.** Bugs 1a and 3 are the same bug — a failure path that collapses into the happy path.
Every mechanism below flows from refusing that.

---

## 2. Architecture decision

### Recommendation: GitHub Pages + committed snapshots

**Why this and not a Worker.** The whole org builds in ~25s and ~170 registry requests, daily
and on demand via `workflow_dispatch`. On-demand already exists; a Worker only buys sub-minute
latency, and nothing in the problem statement needs it. Meanwhile the Worker option costs
exactly the things this project has deliberately avoided: runtime secrets, persistent mutable
state, and a second deployment surface. The project keeps its build tree near zero *because CI
runs beside publish credentials* — introducing an API token-bearing Worker moves in the opposite
direction for zero correctness gain.

**Why snapshots beat D1 for history.** A git repo of normalized JSON snapshots *is* a time
series, and it comes with diff, blame, bisect, and immutability for free. "Which merge caused
this change?" is git's native question. D1 gives you SQL over time — nice, but the query load
here is "compare today to yesterday" and "draw a sparkline," both trivially served by a static
differ at build time.

**Honest trade-offs of staying static:**

- Snapshot growth. ~28 forks, ~150 packages → plausibly 100–300 KB of normalized JSON per day,
  tens of MB per year. Manageable, but needs a retention policy (open question §13). Mitigation:
  keep snapshots on a dedicated `data` branch (or in the new repo where history bloat harms
  nothing), normalize output (sorted keys, stable array order) so diffs are minimal and
  meaningful.
- No true API. `data.json` plus a `snapshots/` directory on Pages is a perfectly serviceable
  read-only static API. If a real API is ever needed, a read-only Worker in front of the same
  committed files is a cheap later addition — that is the hybrid path, and it can be deferred
  with no rework because the data contract is the JSON, not the transport.
- No webhook-driven incremental refresh. At 28 repos and 25 seconds, incremental refresh is
  optimization theater. Daily + on-demand full rebuilds are also a *feature* for trust: every
  build derives everything from scratch, so there is no incremental-state corruption class of
  bug.

**Verdict on freshness vs infra:** freshness does not justify infrastructure. Correctness does,
and correctness is free at build time.

### Move to its own repo: yes

Move the ~1,700 lines to [`unabandoned/recon`](https://github.com/unabandoned/recon) — now
created (reusable workflows stay in `.github` as required). Reasons, in order of weight:

1. **Security posture.** The dashboard repo needs *no secrets at all* — public API reads and
   registry metadata only. `dep_audit.py` runs `npm install --package-lock-only` against
   arbitrary manifests; even with `--ignore-scripts` and no tarballs, running the resolver in a
   repo whose CI environment is guaranteed credential-free is strictly better than running it
   adjacent to the org's most sensitive repo.
2. **Snapshot commits would pollute `.github` history.** Daily data commits in the repo that
   holds the org profile and the reusable publish workflows makes that repo's history — which
   should be auditable and quiet — noisy.
3. **Blast radius.** `.github` is special: every fork's CI depends on it. Dashboard iteration
   (which should be frequent, especially during this redesign) should not share a change surface
   with publish workflows.

Cost: a second Pages site (or a redirect from the old URL — recommend keeping
`unabandoned.github.io/.github/` as a one-line redirect for a while). The stdlib-only constraint
should be *kept* even though the security rationale weakens in a credential-free repo — it's
working, it keeps the build auditable, and the design below needs nothing beyond stdlib plus the
npm CLI already in use.

---

## 3. Trustworthiness as a feature (Priority A — correctly first)

The evidence section's most important sentence is: *"None were caught by inspecting output. All
were caught by contradiction with independently known facts."* That is the design brief. Output
inspection doesn't work because wrong output is plausible; therefore the build must carry its own
contradictions-in-waiting.

Five mechanisms, ordered by how much of the failure history each covers:

### M1. Errors are a first-class state, never a default value

Every fetched fact is a record with `status: ok | failed | not_attempted`, provenance (endpoint,
timestamp), and — only when `ok` — a value. Classification rules may only consume `ok` facts;
anything derived from a non-`ok` fact is classified `unknown`, and `unknown` is a rendered
category alongside alive/inert/time-bomb, counted in every aggregate.

*Would have caught 1a:* the failed packument read becomes `status: failed`, the package becomes
`unknown` instead of "declares no dependencies," and 46-vs-45 time bombs plus a visible `unknown`
bucket is a contradiction someone sees. Also the direct fix for failure 3: `browserify-sign`
becomes a counted exclusion, not an absence.

**This bug class is live in the current source, not merely historical.** `dep_audit.py`
`last_release()` swallows every `URLError`, `JSONDecodeError` and timeout into `date = None`,
commented *"unknown date -> treated as alive, never as a false alarm"*; `classify()` then opens
with `if not last: return "alive"`. A network blip during the build silently reclassifies a time
bomb as healthy, and the page renders a smaller, calmer number with no indication anything went
wrong. That comment names the trade-off deliberately — the argument for M1 is that *neither*
branch is acceptable, and the third option (say "unknown" out loud) costs one enum value.

### M2. Independent double-derivation of the dependency graph

Derive fork→fork edges two ways from two different artifacts:

- **Reader A (manifest):** scan `package.json` dependency *values* for `npm:@unabandoned/...`
  alias syntax (and plain `@unabandoned/` keys, for completeness).
- **Reader B (lockfile):** after resolution, scan the lockfile for any node whose resolved
  package name is in the `@unabandoned` scope.

Assert the edge sets agree. Disagreement fails the build with both sets printed.

*Would have caught 1b directly:* the key-vs-value bug lived in Reader A's territory; the lockfile
— produced by npm's own resolver, which does understand aliases — would have shown the 4 real
edges while the buggy reader showed 0, and the build dies loudly instead of rendering an
isolated-node graph. This is the "manifest aliases vs resolved lockfile" cross-check from the
brief, and it is the highest-value single check in this document because the resolver is a
genuinely independent implementation, not a second copy of our own parsing.

The same principle extends to 1a, and here it requires new work rather than a comparison of two
things already computed. Today a package's dependency count has exactly **one** source: `ndeps`
is `len(meta["dependencies"])` read off the lockfile entry. The packument is fetched (once per
name, cached) but only `dist-tags.latest` and `time` are read from it — its
`versions[v].dependencies` is right there in the response, already paid for, and unused. Reading
it gives a genuinely independent second derivation of the same fact for free, and disagreement on
any `(name, version)` fails the build.

### M3. Ground-truth fixtures (hand-asserted facts the build must reproduce)

A small set of facts the org *knows* — e.g. "the `buffer` fork wires the org's `ieee754` fork,"
"`browserify` reaches `readable-stream` only through the org's `crypto-browserify` fork," "repo X
currently has exactly 1 real open issue." The build asserts each; any miss is a hard failure.

Where they live matters given the no-central-registry rule: put graph fixtures in each fork's own
`.unabandoned.yml` (e.g. `expects_sibling: [ieee754]`) — they are editorial facts, co-located
with the code they describe, exactly the category the rule protects. Cross-fork and org-level
fixtures (issue counts, path assertions) that have no single home can live in one small file in
the recon repo; it is editorial (hand-written, never generated), so it doesn't violate "derivable
data is never recorded" — it's the opposite: *underivable* human knowledge used to audit the
derivation.

*Would have caught 1b and 1c:* a fixture edge missing from the computed graph fails the build
(1b, including the invisible-topology variant); a fixture "repo X has 1 real issue" contradicts
the Renovate-inflated count of 2+ (1c). Fixtures are the codification of exactly how these bugs
were actually caught — "contradiction with independently known facts" — turned from luck into
machinery.

### M4. Shape and uniformity invariants

Cheap assertions about what the world cannot look like:

- Fork→fork edge count ≥ a floor (an org of 28 sibling-wired forks with zero internal edges is
  prima facie wrong — this alone catches 1b's topology symptom).
- Uniformity detector: when a per-repo metric takes the identical value on every repo (every fork
  has exactly one open issue…), flag it — uniform signals are usually a counted artifact, which
  is precisely 1c.
- Conservation: `repos_discovered = repos_included + repos_excluded`, every exclusion carrying a
  reason enum; totals in the UI must reconcile against this or the build fails.
- A package whose packument shows dependencies for its latest version but whose recorded version
  shows zero gets flagged for review (soft check — warn, don't fail).

### M5. Differential check against the previous snapshot

Once history exists (§5): any org-wide aggregate that swings more than a threshold between
consecutive builds (time-bomb count ±20%, edge count ±30%, issue totals, coverage) blocks with a
"confirm this is real" gate — an environment flag or a labeled commit acknowledging the jump.
This catches *regressions* of all three bug classes and catches the failure mode where a "fix"
silently changes semantics (the flat-columns fix in failure 2 was exactly this — a change that
altered meaning while output stayed plausible).

### Minimum set that would have caught 1a–1c

**M1 + M2 + M3.** M1 catches 1a structurally; M2 catches 1b (and 1a's count variant) via a
genuinely independent second derivation; M3 catches 1c and hard-pins 1b's known edges. M4 is
nearly free and worth adding immediately; M5 requires history and is Phase 1.

### Provenance in the UI

Every number rendered carries, on hover/expand: derivation source(s), fetch timestamps, and its
coverage denominator ("46 time bombs of 147 resolved; 3 unknown; 2 repos excluded"). A build-wide
integrity badge (all checks green / N warnings / built despite override) sits in the header. Bare
assertions are banned from the UI by construction: the renderer takes fact-records, not values,
so it *cannot* print a number without its provenance being available.

---

## 4. Data model

One canonical, normalized `observation.json` per build — this is simultaneously the dashboard's
data file and the history snapshot. Sorted keys, stable array ordering, so diffs are minimal.

```
observation.json
├─ meta            schema_version, built_at, builder git SHA,
│                  build duration, npm/node versions, API rate remaining
├─ integrity       checks[]: {id, status, detail, delta_vs_previous}
├─ coverage        repos: discovered / included / excluded[{repo, reason}]
│                  trees: resolved / failed[{fork, reason}]
│                  fetches: attempted / failed[{package, endpoint, code}]
├─ forks[]         name, repo, head_sha, published_version, head_version,
│                  yml facts (upstream, rationale, expects_sibling[]),
│                  publish_status: published | unpublished | missing_yml
├─ packages[]      name, versions_observed[], classification:
│                    alive | inert | time_bomb | unknown
│                  evidence: {last_release, source, fetched_at, status}
│                  advisories[] (ids, severity, ranges)
├─ edges[]         from(name@ver) → to(name@ver),
│                  kind: runtime | dev
│                  tree: published | head
│                  derivation: manifest | lockfile | both   ← must be "both"
│                                                             for fork→fork
├─ trees[]         per fork: rooted resolved tree references (paths are
│                  reconstructable from edges; store per-fork membership)
└─ derived         blast_radius[], dominators[], work_queue[] (§7)
```

Design rules encoded in the model: no field may hold a defaulted value where a fetch failed (M1
lives in the schema — `evidence.status` is mandatory); edges record *which derivation produced
them* so M2's agreement is auditable after the fact; the graph is keyed by `(name, version)`
because different forks resolve different versions of the same package, and collapsing by name is
how membership-not-causation errors (failure 2) sneak back in.

---

## 5. History and diff (Priority C) — and the derivability rule

**The rule and why snapshots don't break it.** "Never record derivable state" exists to prevent a
recorded copy of live reality becoming a stale second source of truth that drifts, gets
hand-edited, or gets trusted over the world. Timestamped observations are a different category on
the exact axis the rule cares about: a snapshot is a fact about *the past* — "this is what we
observed at time T" — and the past is not derivable later. The registry's yesterday-state is
gone; the GitHub API has no time machine. Snapshots cannot drift from the reality they describe,
because that reality is frozen.

The rule's spirit is preserved by one guardrail, stated as a build invariant: **the current build
must be fully reproducible with the snapshots directory deleted.** Snapshots are write-only from
the build's perspective; only the differ and the trend renderer read them. The moment
current-state computation consumes a snapshot, the rule is genuinely violated — that invariant is
checked (build runs its derivation with snapshot input masked, asserts identical output — cheap
at this scale, or enforced by module structure).

**Smallest mechanism:**

1. Each build writes `snapshots/<UTC-timestamp>.json` (the canonical observation) and commits it
   to the `data` branch.
2. A differ compares against the previous snapshot and emits `changes.json`: classification
   transitions (alive→time bomb…), edges added/removed, advisory appearances, coverage changes,
   per-fork version bumps.
3. **Merge attribution:** each snapshot records every fork's `head_sha`. A delta between
   consecutive snapshots brackets, per fork, exactly the commit range `sha_prev..sha_now`; the
   changes view links straight to the GitHub compare URL. "Which merge caused it" falls out with
   zero extra infrastructure.
4. Trend sparklines render from the last N snapshots at build time — static output, no
   client-side history fetching required (though the client *may* fetch older snapshots for
   deep-dive, since they're just files on Pages).

Retention is an open question (§13); recommend dailies for 90 days then monthly compaction, on
the `data` branch so clones of the code stay light.

---

## 6. Honest coverage (Priority D)

Coverage is a ledger, not a caveat. The conservation invariant (M4) makes the ledger
self-checking, and the UI contract is: **no aggregate renders without its denominator and
exclusion list.** Concretely: "46 time bombs *(of 147 resolved packages; 3 unknown — see
health)*" and a header line like "covering 27 of 29 discovered repos — 2 excluded:
browserify-sign (no `.unabandoned.yml`), … (tree resolution failed)". The
`unpublished`/`missing_yml` states from the fork model are the direct fix for failure 3:
`browserify-sign` appears as a visible, categorized gap on the coverage page rather than an
absence. Coverage & Health is a first-class page (§10), not a footnote — a tool whose failure
history is "silent wrongness" should make its own limits the easiest thing to see.

---

## 7. From state to work queue (Priority B)

The reframe is from "which packages are rotten" to "which single intervention removes the most
rot." That's a graph-cut question, and dominator analysis is the right approximation.

**Computation.** Per fork, over its resolved `(name, version)` tree: compute dominators from the
fork's root. A time-bomb node dominated by another time-bomb is *shadowed* — fixing the dominator
makes the dominated node moot (it leaves the tree or becomes someone else's responsibility). The
actionable set is the *dominance frontier of the rot*: the highest time-bomb nodes on each rotten
path. Then aggregate across forks on the union multigraph (edges labeled with tree membership) to
score each candidate:

```
blast_radius(pkg) = Σ over forks reached:
    (subtree of rot removed if this node is fixed)
  × severity weight (open advisories in subtree, §8)
  × tree weight (dev tree beside publish creds scores high, §8/§9)
```

**Per-candidate intervention menu, with computed consequence.** For each queue entry, enumerate
options and what each cascades into: *fork it* (org owns the subtree — N new packages come under
Renovate; show the count), *override/alias to an alive alternative* (subtree removed — show which
paths close), *bump via upstream PR* (only when the stale pin sits under an alive maintainer),
*vendor* (subtree internalized, drops out of the audit surface — flag this as a coverage loss,
not a win). Entries render as causation, not membership — this is the direct repair of failure 2:
"fix `hash-base` → clears `readable-stream` from 6 trees, via
`browserify → crypto-browserify → hash-base → readable-stream` and 1 other path," with every path
expandable.

**Caution.** Dominators must be computed per-fork on `(name, version)`-keyed trees and *then*
aggregated. Computing dominators on a name-collapsed org-wide graph produces wrong answers
whenever two forks resolve different versions through different paths — the same collapse-by-name
error class that produced failure 2's cross-product.

---

## 7b. Intake — audit any tree before adopting it

The org's origin workflow ("adopt a tool, discover half its tree is abandoned with CVEs") becomes
a feature: run recon's classifier against *any* package spec or repo, and join the result against
the fork inventory to produce an adoption plan — which packages are already covered by
`@unabandoned/*` forks (alias them), which are already in the work queue, and which new forks
(dominators) would achieve full coverage.

**Two tiers, honestly labeled.** The key discovery is that npm's own resolution *primitives* are
pure JS and browser-safe: `npm-package-arg` (npm's spec parser — it natively understands the
`npm:@unabandoned/...` alias syntax that bug 1b choked on), `semver`, and `npm-pick-manifest`
(npm's version-selection logic). The registry serves packuments with CORS enabled and OSV's batch
API is CORS-open, so a static page on Pages can resolve, classify, and advisory-join a foreign
tree entirely client-side — no Worker, no runtime secrets, no Actions round-trip.

- **Instant tier (browser, "recon-lite").** Paste a spec, get a verdict in seconds. A packument
  BFS using npm's primitives is ~faithful for the reachable `(name, version)` set but is *not*
  Arborist: peerDependencies (auto-installed since npm 7), optionalDeps, and manifest `overrides`
  are edge cases where it diverges. Every recon-lite result is therefore badged with its fidelity
  limits (`resolved by recon-lite · peer deps not walked`) and **never writes to snapshots,
  reports, or org aggregates**. Vendor pinned copies of the three packages into the Pages assets
  (no CDN imports) to preserve the zero-external-runtime-deps posture.
- **Authoritative tier (Actions).** A "commit full audit" button fires `workflow_dispatch` with
  the spec. The audit runs the *same* pipeline as the daily build — npm's real resolver, same
  classifier, same error-state discipline, same OSV join — rooted at the foreign manifest. One
  code path, different root; a second classifier implementation would be a second home for
  1a/1b-class bugs. The report of record is committed to
  `reports/<pkg>@<version>/<timestamp>.json` on the `data` branch and rendered as a page.

**A rejected alternative, for the record:** a Cloudflare Worker (behind CF Access) doing the
resolution itself. Access would solve auth for the dispatch nicely, but a Worker cannot run the
npm CLI, so it would hand-roll resolution — a second resolver, which is the exact bug-1b pattern
this redesign exists to kill — while also introducing a runtime secret (a PAT). The browser tier
delivers the same instant-UX win with zero secrets; Actions remains the authority. If dispatch
abuse ever becomes real (strangers burning Actions minutes), an Access-gated dispatcher Worker
can be added later without touching the pipeline.

**Free integrity dividend:** when both tiers run the same spec, diff them. recon-lite vs Arborist
disagreement is either a lite limitation to document or a real resolver-facing bug — an M2-style
cross-check that costs nothing extra.

**Rules specific to intake:** reports are timestamped observations of *external* trees — the same
category as snapshots (§5), with the same guardrail (never an input to the org's own build) plus
one more: intake results never merge into org aggregates (an audit of `factor-bundle` must not
inflate the org's time-bomb count). Rendered reports display their audit date prominently and go
visibly stale — recon does not watch trees it doesn't own. If the plan is adopted (the tool gets
forked), the intake report retires: the package gains a `.unabandoned.yml` and enters the normal
daily pipeline. Intake is a doorway, not a second tracking system.

---

## 8. Advisories (Priority E)

OSV's batch query endpoint takes all ~150 `(name, version)` pairs in one or two requests, no API
key, no secrets. Join results onto the graph. The classification gains an escalation tier rather
than a new category: **time bomb + reachable advisory = emergency** — abandoned, carrying deps,
*and* concretely vulnerable with no maintainer to respond is the scenario the whole org exists
for, and today it's invisible.

Advisory data feeds blast-radius weighting (§7) and gets its own queue tier above everything
else. M1 applies here too: an OSV fetch failure must render as "advisory status unknown," never
as "no advisories."

This priority is listed fifth but costs almost nothing and multiplies the value of the work
queue. It belongs in Phase 2 (§11), before the full work-queue machinery.

---

## 9. Dual trees (Priority F) — split it

F bundles two different-priority things.

**Runtime vs dev tree: high priority, security-motivated.** The dev tree that matters is what CI
installs at HEAD — that's what runs beside publish credentials. Resolve each fork's HEAD manifest
twice (with and without `--omit=dev`); any time-bomb or advisory in the dev-only delta is a
*publish-pipeline supply-chain risk* and outranks equivalent rot in a runtime tree, because the
failure mode is credential exfiltration rather than downstream vulnerability. This inverts the
current tool's stance (which audits `--omit=dev` only) for exactly the reason the org keeps its
own build tree near zero.

**Published vs HEAD: useful, lower urgency.** Resolve from the published manifest (registry) and
from HEAD's `package.json` (raw content API); diff per fork. Surfaces "merged but unreleased"
(failure 5) as a per-fork *unreleased delta* badge and catches silent divergence. Cost: roughly
doubles resolution time to ~50–60s. Still trivial; but it's Phase 4 material, not foundational.

---

## 10. UI structure

Eight views (as mocked in `recon-ui-vision`), serving two audiences — the maintainer and the
downstream consumer:

1. **Overview** — headline aggregates *with denominators*, integrity badge, trend sparklines,
   last-build provenance (when, builder SHA, duration).
2. **Forks (consumer catalog)** — the answer to "can my project depend on this today?" Each fork
   gets a **tree grade** derived from everything beneath it (CLEAN / N bombs / EMERGENCY / NOT
   PUBLISHED), not just its own freshness, plus an install line, an embeddable README badge with
   the grade date baked in (a stale badge looks stale), and a consumer-only changelog (grade
   transitions and releases) as a static RSS/JSON feed. Grades and the work queue are two views
   of one graph — fixing a queued dominator visibly flips the dependent forks' grades.
3. **Work queue** — ranked interventions (§7), emergency tier (advisory-bearing) pinned on top,
   each entry expandable to paths and cascade preview.
4. **Package explorer** — per-package: classification *with evidence* ("last release 2019-03-04,
   packument fetched 06:12 UTC"), full paths from every consuming fork, advisories, per-package
   history sparkline.
5. **Topology** — the SVG graph, now with the (real) fork→fork edges, rot coloring,
   published/HEAD and runtime/dev toggles. Keep the no-JS-libraries computed-SVG approach; it has
   proven adequate and matches the project's constraints.
6. **Intake** — audit any tree (§7b): instant recon-lite verdict, coverage overlap ("12 of 41
   already covered via 4 forks"), computed adoption plan, "commit full audit" dispatch.
7. **Changes** — the differ's output: transitions since last build (and date-picker across
   snapshots), each fork's delta linking to its `prev..now` compare URL.
8. **Coverage & health** — exclusions with reasons, failed fetches, unknowns, every integrity
   check's status and history. First-class, linked from every denominator annotation elsewhere.

---

## 11. Phased plan

Each phase ships something usable alone.

**Phase 0 — Correctness foundation.** M1 (error states + `unknown` category + coverage ledger),
M2 (manifest/lockfile double-derivation with hard failure, plus the packument-vs-lockfile
dependency-count cross-check), M3 (fixtures: `expects_sibling` in fork ymls + small org-level
fixture file), M4 (shape/uniformity/conservation invariants), issue-classification fix
(author/label filter for Renovate). Ships: the *same* dashboard, now unable to lie silently, plus
a minimal health section. This phase alone retroactively catches 1a, 1b, 1c, and 3, and is
deliberately UI-light — trust before features.

**Phase 1 — History.** Migrate the scripts into
[`unabandoned/recon`](https://github.com/unabandoned/recon) (repo already created; do the move
together with the first snapshot commits, which motivate it). Snapshot writes on a `data` branch,
differ, changes view, merge attribution via recorded HEAD SHAs, trend sparklines, M5 differential
gates. Ships: failure 4 solved; "did that change help?" answerable.

**Phase 2 — Security + causation.** OSV batch join, emergency tier, and path rendering replacing
every membership-style table (per-fork rooted paths, `(name, version)`-keyed). Ships: failure 2
solved; the real emergencies visible for the first time.

**Phase 3 — Work queue + Intake.** Dominator/blast-radius computation, intervention menu with
cascade previews, queue view. Then Intake (§7b), which reuses both the classifier (Phase 0) and
the dominator machinery: the authoritative Actions tier first (parameterize the audit root +
coverage-overlap join — small), recon-lite browser tier second. Ships: Priority B; the dashboard
becomes a decision tool rather than a status page, and the org's origin workflow becomes a
feature.

**Phase 4 — Dual trees + consumer catalog.** HEAD + dev resolution, dev-tree risk tier feeding
the queue, published-vs-HEAD unreleased-delta badges, topology toggles. Then the Forks catalog
with tree grades and embeddable badges — grades need the full graph, advisories, and dual trees
to be honest, so it lands last even though it's the most consumer-visible feature. Ships: failure
5 solved; publish-pipeline supply-chain risk visible; `@unabandoned/*` becomes consumable with
confidence.

---

## 12. Priorities I'd challenge

1. **E is under-ranked.** Listed fifth, but it's a one-batch-request join with no secrets and it
   defines the top of the work queue. Moved to Phase 2, ahead of the queue machinery it feeds.
2. **F should be split.** The dev-tree half is a security control (it protects the credentials the
   whole design tiptoes around) and deserves more weight than the published-vs-HEAD half, which
   is bookkeeping. Handled in §9; both still land in Phase 4, but if the org wants to pull one
   forward, pull the dev tree.
3. **The freshness framing in the architecture question is a distractor.** Nothing in the failure
   history is a staleness failure. Any argument for the Worker has to stand on API needs or
   genuinely high-frequency data, and neither exists at 28 repos.
4. **One priority is missing from the list: reproducibility.** Every failure was debugged by
   contradiction; contradiction-hunting requires rerunning the build deterministically. Normalized
   output (§4) and the snapshot-independence invariant (§5) are cheap and should be treated as an
   explicit A-tier requirement, not an implementation detail.

Priorities A→B ordering is correct and worth defending: the work queue is only as good as the
graph, and the graph was recently rendering zero of its real internal edges. Trust first.

---

## 13. Open questions needing a human decision

1. **Failed-check publishing behavior.** When an integrity check fails, does the build publish
   nothing (last good stays up, possibly stale), or publish with a prominent red "integrity
   failure — numbers untrusted" banner? Recommendation: publish with banner — a visibly broken
   dashboard gets fixed; a silently stale one doesn't — but this is a judgment call about the
   org's habits.
2. **Snapshot retention.** Keep all forever vs 90-day dailies + monthly compaction; `data` branch
   vs separate data-only repo.
3. **Fixture placement.** Confirm `expects_sibling` (and similar) in per-fork `.unabandoned.yml`
   is acceptable schema growth, vs consolidating all fixtures in the recon repo. Per-fork is more
   consistent with the co-location rule; consolidated is easier to review as a set.
4. **Differential-gate thresholds and override mechanism** (M5): what % swing blocks vs warns, and
   whether override is an env flag, a commit trailer, or a manual re-run input.
5. **`browserify-sign`:** is it *intended* to be published? Its state (repo, no yml, nothing
   published) is now visible either way, but its target state is an editorial fact only a human
   knows.
6. **Issue counting:** after 1c, does the org want issue counts on the dashboard at all, and if
   so, what is the durable definition of a "real" issue (author filter, label allowlist, both)?
7. **Old URL:** redirect from `unabandoned.github.io/.github/` to the new site, for how long?
8. **HEAD-tree fidelity:** resolving HEAD via its raw `package.json` approximates but doesn't
   perfectly reproduce what CI installs (workspace/config nuances). Is manifest-level fidelity
   acceptable, or should Phase 4 resolve from a git tarball?

---

## Appendix A — the failure history

The body argues from five documented failures but was written against a problem statement that
isn't part of this document. Reconstructed here from the commits that fixed each one, so the
argument stands on its own. All in `unabandoned/.github`.

| # | Failure | Fixed in | Evidence |
|---|---------|----------|----------|
| **1a** | Aliased dependencies identified by their **lockfile key** rather than their real package name, so a fork installed as `node_modules/buffer` was dated from upstream `buffer`'s 2020 packument and filed as an abandoned time bomb. It also invented a whole class of finding: every aliased entry looked like a fork pulling its own abandoned upstream — **22 packages across 11 forks reported, 4 real**. | [#28](https://github.com/unabandoned/.github/pull/28) `86b8e20` | Corrected totals: 147 unique packages, 46 time bombs, 44 inert, **57 alive — up from 34**, because the org's own forks were being counted as their abandoned upstreams. |
| **1b** | The **same blind spot** in `build_dashboard.py` made every fork→fork topology edge invisible: a dependency keyed `buffer` never matched the package id `@unabandoned/buffer`. The graph rendered as isolated nodes with only `used-by` edges. | [#28](https://github.com/unabandoned/.github/pull/28) `86b8e20` | *"the data was there all along."* |
| **1c** | Renovate's always-open **"Dependency Dashboard" issue counted as an open issue** on every fork — a permanent floor of 1 under every card, and double-counted since the card already links it. | [#28](https://github.com/unabandoned/.github/pull/28) `86b8e20` | **29 open issues reported across 27 forks; 2 were real work.** No fork could ever show a clean zero. |
| **2** | The upstream-copies table listed declarers and consumers as **two flat columns**, so the reader had to guess the pairing — membership, not causation. | [#29](https://github.com/unabandoned/.github/pull/29) `00d5f2c` | The flat columns hid that `browserify` doesn't consume `browserify-sign` at all — it inherits `readable-stream` through the org's own `crypto-browserify` fork. **Three real entry points, not four consumers**; fixing `crypto-browserify` closes `browserify` with it. |
| **3** | `browserify-sign` has a repo but no `.unabandoned.yml` and nothing published, so it is **silently absent** from the dashboard rather than visibly excluded. | *unfixed* | The §6 coverage ledger is the fix. |
| **4** | **No history.** Nothing records what the numbers were yesterday, so "did that change help?" is unanswerable and regressions are invisible. | *unfixed* | The §5 snapshots are the fix. |
| **5** | **Merged but unreleased** work is invisible: the dashboard resolves published manifests only, so a fork whose HEAD has moved past its release looks identical to one that hasn't. | *unfixed* | The §9 published-vs-HEAD diff is the fix. |

Note the shape: **1a, 1b and 2 are one root cause each rendered plausibly.** Every corrected
number was smaller-and-calmer or larger-and-noisier than the truth, and every one of them looked
completely reasonable on the page. That is the whole argument for §3.

---

## Appendix B — claims checked against the current source

Checked against `unabandoned/.github@main` (`00d5f2c`). Three corrections and one structural
repair were applied to the body; everything else verified as written.

**Corrected:**

1. **"~900 lines" → "~1,700 lines"** (§2). Actual: `build_dashboard.py` 531 + `dep_audit.py` 592
   + `topology.py` 233 + `validate_metadata.py` 159 = **1,515 lines of Python**, plus the
   190-line `dashboard_template.html` = 1,705. Nearly double the estimate the move was scoped
   against. It doesn't change the recommendation, but it changes the size of Phase 1.
2. **The packument tactical note in M1 was dropped — that work is already done.** The document
   proposed replacing "the per-version endpoint" with the packument to eliminate a
   404-on-existing-version class. There is exactly one registry fetch in the codebase
   (`dep_audit.py:75`), it already requests the full packument (`{REGISTRY}/{name}`), and it is
   memoised per name in `_DATE_CACHE`. There is no per-version endpoint to migrate away from.
3. **M2's second half is new work, not a cross-check of two existing derivations.** The document
   describes the packument-vs-lockfile dependency-count check as *"two independent derivations of
   the same fact, both cheap, both already being computed."* The second one is not being
   computed: `ndeps` has a single source, `len(meta["dependencies"])` off the lockfile entry
   (`dep_audit.py:196`). The packument response *does* already contain
   `versions[v].dependencies` and is already paid for, so the check is still nearly free — but it
   requires reading a field that is currently discarded, which is why it's named explicitly in
   the Phase 0 line.

**Repaired:** §8 had lost its heading — the advisories section ran on unlabelled from §7b while
being cross-referenced as "§8" from §7, §9 and §12. Heading restored; the §9/§10 cross-references
in §6 and §7 were renumbered to match.

**Verified as written:** the ~25s / ~170-request build cost; `--package-lock-only --omit=dev
--ignore-scripts` (registry metadata only, no tarballs, no lifecycle scripts —
`dep_audit.py:152`); the 365-day `ABANDONMENT_DAYS` threshold; the three-way
alive/inert/bomb classification and its rationale; 27 published forks / 147 packages / 46 bombs /
44 inert / 57 alive; the stdlib-only constraint (PyYAML is the single dependency, installed into
a throwaway venv by the `dashboard` workflow); daily `cron: '0 6 * * *'` plus
`workflow_dispatch` plus push-on-builder-change.

**One finding the document didn't have (folded into M1):** the M1 bug class is not merely
historical, it is live. `last_release()` returns `None` on any `URLError`, `JSONDecodeError` or
timeout, and `classify()` opens `if not last: return "alive"` — both carrying comments that name
the choice deliberately (*"unknown date -> treated as alive, never as a false alarm"*). A network
blip during the build silently reclassifies time bombs as healthy. This is bug 1a's exact shape —
a failure path collapsing into the happy path — sitting in the current source with a comment
explaining why. It is the single most concrete argument for Phase 0.
