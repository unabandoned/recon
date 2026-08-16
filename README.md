# recon

Dependency reconnaissance for the [`unabandoned`](https://github.com/unabandoned)
maintained-fork program. It answers three questions:

1. **What is rotting** beneath the `@unabandoned/*` forks?
2. **Which single change removes the most of it?**
3. **How much of the picture can we actually see?**

The third one is load-bearing. Every bug in this tool's history was a confident
answer to one of the first two that happened to be wrong — and none of them was caught
by reading the output, because wrong output here is *plausible*. So recon is built
around one rule:

> **"We don't know" must be unrepresentable as a benign value.**

A failed fetch cannot become a zero, an absent date cannot become "healthy", and an
excluded repository cannot become nothing at all. See
[`docs/redesign.md`](./docs/redesign.md) for the full design and
[`docs/implementation.md`](./docs/implementation.md) for what is built.

## Quick start

```bash
pip install pyyaml                    # the only dependency; everything else is stdlib
python -m unittest discover -s tests -t .   # the whole suite, fully offline
GITHUB_TOKEN=… python -m recon.cli build    # writes ./public and a snapshot
python -m recon.cli verify                  # non-zero if a check failed
python -m recon.cli intake factor-bundle@2.0.0   # audit a tree we do NOT own
python -m recon.cli compare CorentinTh/it-tools TheTechNetwork/it-tools  # diff two repos
# or paste both into the Intake tab and get the declared-dependency diff in the page
```

## How it avoids lying

Five mechanisms, each aimed at a failure that actually happened. All of them run on
every build and land on the **Coverage & health** page.

| | Mechanism | What it asserts |
|---|---|---|
| **M1** | Errors are a state, never a default | A failed fetch must reach the page as `unknown`, counted in every aggregate, carrying its reason |
| **M2** | Independent double-derivation | Our manifest reader and **npm's own resolver** must agree on the `@unabandoned` edges; the registry and the lockfile must agree on every version's dependencies |
| **M3** | Ground-truth fixtures | Edges, paths and counts a human asserted in [`fixtures/org.yml`](./fixtures/org.yml) must be reproduced by the build; asserting nothing warns rather than passes |
| **M4** | Shape and uniformity invariants | An org of sibling-wired forks cannot have zero internal edges; a metric with a hard non-zero floor on every repo is a counted artifact; the coverage ledger must balance |
| **M5** | Differential vs the last snapshot | No headline aggregate may swing past its threshold without a human naming the change |

Plus one reproducibility invariant: the current build is re-derived with the snapshot
directory masked and the two must be **byte-identical**. History can be read by the
differ and the trend renderer, and by nothing else — which is what keeps a record of
the past from quietly becoming a second source of truth.

`M2` is the strongest of these, because the second witness is not a second copy of our
own parsing — it is npm. The bug that made every fork→fork edge invisible lived
entirely in our reader; npm's lockfile had the right answer the whole time.

## What it produces

Seven pages, from one canonical `observation.json` that is simultaneously the data
file and the history snapshot:

**Overview** · **Forks** (a consumer catalog with tree grades) · **Work queue** ·
**Packages** · **Topology** · **Changes** · **Coverage & health**

Every aggregate shows its denominator. Every fact carries its provenance — the
renderer takes fact-records, not values, so it *cannot* print a number whose origin
isn't available.

### The work queue

The reframe from "which packages are rotten" (a list) to "which single change removes
the most rot" (a graph question). A rotten node that another rotten node **dominates**
is *shadowed* — fixing the one above moots it — so what's left is the highest rot on
each path, ranked by how much it clears and pinned below anything carrying a live
advisory.

Run against real data, it independently reproduces the org's own hardest-won finding:

```
178 nodes: 77 time bombs, 64 inert, 37 alive
EMERGENCY  crypto-browserify   clears 22   score 55.0
           assert              clears  4   score 4.0
           util                clears  4   score 4.0
```

## Status

Phases 0–3 of the design are implemented: the correctness foundation, history and
diff, the OSV advisory join with its emergency tier, and the dominator-based work
queue. Phase 4 (dual runtime/dev trees, published-vs-HEAD deltas) and the intake tier
are not built — see [`docs/implementation.md`](./docs/implementation.md).

The scripts still run from `unabandoned/.github`; nothing has been switched over yet,
and the two will produce different numbers on purpose. Cutover is a deliberate step,
not a side effect of merging this.

Eight decisions still want a human:
[`docs/redesign.md` §13](./docs/redesign.md#13-open-questions-needing-a-human-decision).
