# recon

Dependency reconnaissance for the [`unabandoned`](https://github.com/unabandoned)
maintained-fork program — the tool that answers *what is rotting in our trees, and what
single change removes the most of it.*

## Status: design phase

This repository is **the destination, not yet the home**. The dashboard currently lives and
runs in [`unabandoned/.github`](https://github.com/unabandoned/.github)
(`scripts/build_dashboard.py`, `dep_audit.py`, `topology.py`, `validate_metadata.py`) and
publishes to <https://unabandoned.github.io/.github/>. Nothing has moved yet.

**[`docs/redesign.md`](./docs/redesign.md)** is the design of record for the redesign and the
move: the architecture decision (stay static, keep GitHub Pages, add committed snapshots), the
five integrity mechanisms, the data model, and a five-phase plan. Read it before writing any
code here.

The move itself is **Phase 1** work and happens together with the first snapshot commits —
see [§2](./docs/redesign.md#2-architecture-decision) for why the dashboard belongs in a
repository that holds no secrets, and [§11](./docs/redesign.md#11-phased-plan) for the
sequencing.

## The one-sentence version

Every documented failure of the current dashboard was a *correctness* failure — a wrong number
that looked plausible — and none was caught by reading the output. So the redesign spends its
complexity budget on mechanisms that make the build contradict itself when it is wrong, not on
infrastructure that makes it faster.

The design rule that follows from that: **"we don't know" must be unrepresentable as a benign
value.** See [§3](./docs/redesign.md#3-trustworthiness-as-a-feature-priority-a--correctly-first).

## Open questions

[§13](./docs/redesign.md#13-open-questions-needing-a-human-decision) lists eight decisions that
need a human before the affected phase starts. Phase 0 is blocked on none of them except the
issue-counting definition (Q6); the rest gate later phases.
