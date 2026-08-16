"""Live smoke test: does the real resolver still agree with the real registry?

The offline suite fakes npm and the registry so it can be fast and deterministic.
That is the right trade for 120-odd tests, but it means the one thing it cannot
check is the thing mechanism M2 rests on: that our reading of a lockfile and our
reading of a packument describe the same world.

So this runs for real. It resolves a published tree with npm, then asks the
registry directly what each `(name, version)` declares, and fails if the two
ever disagree. Any mismatch is either npm changing a lockfile shape under us or
our parsing being wrong — both worth a red build.

    python -m tests.smoke
"""
from __future__ import annotations

import datetime
import sys
from collections import Counter

from recon.classify import classify, cutoff_for
from recon.facts import Fact
from recon.graph import ForkGraph, build_queue
from recon.http import Session
from recon.registry import Registry
from recon.resolve import npm_available, resolve_tree

TARGET = "browserify@17.0.0"   # pinned: a moving target makes a flaky test


def main() -> int:
    if not npm_available():
        sys.stderr.write("error: npm is not on PATH\n")
        return 2

    clock = lambda: datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    session = Session(clock=clock)
    registry = Registry(session)

    fact = resolve_tree(TARGET)
    if not fact.is_ok:
        sys.stderr.write(f"error: could not resolve {TARGET}: {fact.detail}\n")
        return 1
    tree = fact.payload
    print(f"resolved {TARGET}: {len(tree.nodes)} nodes, {len(tree.edges)} edges")

    if not tree.nodes:
        sys.stderr.write("error: resolved an empty tree\n")
        return 1
    if tree.root_key != "node_modules/browserify":
        sys.stderr.write(
            f"error: root resolved to {tree.root_key!r} — a versioned spec failed "
            "to find its own root, which silently rebases the whole analysis\n"
        )
        return 1

    # --- M2, for real ------------------------------------------------------
    compared = mismatched = 0
    for node in tree.nodes.values():
        declared = registry.declared_deps(node.name, node.version)
        if not declared.is_ok:
            continue
        compared += 1
        if set(declared.payload) != set(node.deps):
            mismatched += 1
            sys.stderr.write(
                f"M2 mismatch: {node.ident} — registry "
                f"{sorted(set(declared.payload) - set(node.deps))}, lockfile "
                f"{sorted(set(node.deps) - set(declared.payload))}\n"
            )
    print(f"M2 registry-vs-lockfile: {compared} compared, {mismatched} mismatched")
    if mismatched:
        return 1
    if compared < len(tree.nodes) // 2:
        sys.stderr.write(
            f"error: only {compared} of {len(tree.nodes)} packages could be "
            "cross-checked — the registry read is mostly failing\n"
        )
        return 1

    # --- the rest of the pipeline still runs on real data ------------------
    cutoff = cutoff_for(datetime.date.today())
    states = {
        key: classify(
            registry.last_release(node.name), Fact.ok(list(node.deps)), cutoff
        ).state
        for key, node in tree.nodes.items()
    }
    print("classification:", dict(Counter(s.value for s in states.values())))

    graph = ForkGraph.build(TARGET, tree, states)
    queue = build_queue({TARGET: graph})
    print(f"dominators: {len(graph.actionable)} actionable, "
          f"{len(graph.shadowed)} shadowed, {len(queue)} queue candidate(s)")
    for cand in queue[:5]:
        print(f"  {cand.name:28s} clears {len(cand.clears):3d}  score {cand.score()}")

    summary = session.summary()
    print(f"fetches: {summary['attempted']} attempted, {summary['failed']} failed")
    if not queue:
        sys.stderr.write("error: a 2020-era tree produced an empty work queue\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
