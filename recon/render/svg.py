"""The dependency topology as a computed SVG — no JavaScript, no libraries.

Consumers on top, the forks we own in dependency layers beneath, shared leaves
at the bottom. Every edge is derived, never hand-drawn: fork→fork from the
resolved graph (agreed by two independent readers), consumer→fork from `used-by`.

Layout is a compact layered pass with a barycentre crossing-reduction sweep. The
graph is rendered at its natural size inside a horizontally scrolling panel rather
than being scaled to fit the column: at 27 forks a widest-layer graph is several
thousand pixels across, and `max-width: 100%` was shrinking the labels to a couple
of pixels. A diagram you cannot read is not a smaller diagram, it is a decoration —
so wide graphs scroll, and the type stays at its real size.
Node colour carries tree health rather than just CI, so the graph doubles as a
map of where the rot is: a fork whose subtree contains an advisory-bearing time
bomb is red even when its own CI is green, because that is the fact a reader
actually needs.

Edges derived by only one of the two readers are drawn dashed. In a healthy
build there are none — `m2.scope-edges-agree` fails first — but if the check is
ever downgraded, the disagreement stays visible on the page rather than being
silently smoothed into a solid line.
"""
from __future__ import annotations

import html
from collections import defaultdict

NODE_W = 158
NODE_H = 48
H_GAP = 20
V_GAP = 76
MARGIN = 20
# A layer wider than this wraps onto further rows. 27 sibling-less forks on one
# line is 4,300px: the upper layers then centre off-screen and the graph becomes a
# void with a strip of boxes at the bottom. Wrapping keeps it inside the column.
MAX_PER_ROW = 6

GRADE_CLASS = {
    "emergency": "n-bad",
    "at-risk": "n-bad",
    "unmeasured": "n-warn",
    "clean": "n-ok",
    "unknown": "n-neutral",
}

CSS = """
.topo-scroll { overflow-x: auto; overscroll-behavior-x: contain; }
.topo { display: block; margin: 0 auto;
  font: 600 12.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
.topo-hint { font-size: 12px; color: var(--fg-muted); margin: 8px 0 0; }
.topo-narrow { display: none; }
.topo-list { list-style: none; margin: 0; padding: 0; }
.topo-list li { padding: 10px 0; border-bottom: 1px solid var(--border-muted); }
.topo-list li:last-child { border-bottom: none; }
.topo-list .from { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 650; font-size: 13.5px; display: flex; gap: 8px; align-items: baseline;
  flex-wrap: wrap; }
.topo-list .to { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
.topo-list .to span { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px; color: var(--fg-muted); background: var(--panel-2);
  border: 1px solid var(--border-muted); border-radius: 5px; padding: 2px 7px; }
.topo .edge { fill: none; stroke: var(--border); stroke-width: 1.6; opacity: .85; }
.topo .edge.partial { stroke: var(--warn); stroke-dasharray: 5 3; }
.topo .node text { fill: var(--fg); }
.topo .node .sub { fill: var(--fg-muted); font-size: 10.5px; font-weight: 500; }
.topo .node rect { stroke-width: 1.5; }
.topo .n-ok rect { fill: var(--ok-bg); stroke: var(--ok); }
.topo .n-bad rect { fill: var(--bad-bg); stroke: var(--bad); }
.topo .n-warn rect { fill: var(--warn-bg); stroke: var(--warn); }
.topo .n-neutral rect { fill: var(--panel-2); stroke: var(--border); }
.topo .n-consumer rect { fill: var(--chip-bg); stroke: var(--fg-muted); stroke-dasharray: 4 3; }
.topo .n-consumer text { fill: var(--fg-muted); }
.topo-legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 12px;
  font-size: 12px; color: var(--fg-muted); }
.topo-legend span { display: inline-flex; align-items: center; gap: 6px; }
.topo-legend i { width: 13px; height: 13px; border-radius: 4px; border: 1.5px solid; }
.lg-ok { background: var(--ok-bg); border-color: var(--ok); }
.lg-bad { background: var(--bad-bg); border-color: var(--bad); }
.lg-warn { background: var(--warn-bg); border-color: var(--warn); }
.lg-con { background: var(--chip-bg); border-color: var(--fg-muted); }
"""

LEGEND = (
    '<div class="topo-legend">'
    '<span><i class="lg-ok"></i>clean tree</span>'
    '<span><i class="lg-bad"></i>time bombs / emergency</span>'
    '<span><i class="lg-warn"></i>unmeasured</span>'
    '<span><i class="lg-con"></i>consumer (external)</span>'
    '<span>edges = dependency, derived from the resolved graph + used-by</span>'
    "</div>"
)


def _layers(nodes: dict, edges: list[tuple[str, str]]):
    """Rank each node by its longest path to a sink, following out-edges."""
    out = defaultdict(list)
    for s, d in edges:
        if s in nodes and d in nodes:
            out[s].append(d)

    rank: dict[str, int] = {}

    def depth(start: str) -> int:
        # Iterative longest-path with an explicit stack: cycles are impossible in
        # a resolved tree but a malformed input should not blow the stack.
        stack = [(start, iter(out.get(start, [])), frozenset({start}))]
        while stack:
            node, it, seen = stack[-1]
            advanced = False
            for child in it:
                if child in seen or child not in nodes:
                    continue
                if child not in rank:
                    stack.append((child, iter(out.get(child, [])), seen | {child}))
                    advanced = True
                    break
            if advanced:
                continue
            children = [c for c in out.get(node, []) if c in rank and c not in seen]
            rank[node] = 0 if not children else 1 + max(rank[c] for c in children)
            stack.pop()
        return rank.get(start, 0)

    for n in nodes:
        if n not in rank:
            depth(n)
    top = max(rank.values(), default=0)
    layers: dict[int, list[str]] = defaultdict(list)
    for n in sorted(nodes):
        layers[top - rank.get(n, 0)].append(n)
    return layers


def _order(layers, edges):
    """One down and one up barycentre sweep to cut crossings."""
    adj, radj = defaultdict(list), defaultdict(list)
    for s, d in edges:
        adj[s].append(d)
        radj[d].append(s)
    last = max(layers) if layers else 0

    for li in range(1, last + 1):
        above = {n: i for i, n in enumerate(layers[li - 1])}
        layers[li].sort(key=lambda n: _bary([above[p] for p in radj[n] if p in above],
                                            len(above)))
    for li in range(last - 1, -1, -1):
        below = {n: i for i, n in enumerate(layers[li + 1])}
        layers[li].sort(key=lambda n: _bary([below[c] for c in adj[n] if c in below],
                                            len(below)))
    return layers


def _bary(positions: list[int], fallback: int) -> float:
    return sum(positions) / len(positions) if positions else fallback / 2


def render(nodes: dict, edges: list[dict]) -> str:
    """nodes: {id: {label, sub, kind, grade}}; edges: [{from, to, derivation}]."""
    if not nodes:
        return ""
    pairs = [(e["from"], e["to"]) for e in edges if e["from"] in nodes and e["to"] in nodes]
    layers = _order(_layers(nodes, pairs), pairs)

    # Flatten layers into drawn rows, wrapping any layer too wide for the column.
    # Rank order is preserved: a layer's wrapped rows stay adjacent and in order.
    rows: list[list[str]] = []
    for li in sorted(layers):
        row = layers[li]
        for start in range(0, len(row), MAX_PER_ROW):
            rows.append(row[start:start + MAX_PER_ROW])
    if not rows:
        return ""

    width = max(len(r) * NODE_W + (len(r) - 1) * H_GAP for r in rows) + 2 * MARGIN
    height = len(rows) * NODE_H + (len(rows) - 1) * V_GAP + 2 * MARGIN

    pos: dict[str, tuple[float, float]] = {}
    for ri, row in enumerate(rows):
        row_w = len(row) * NODE_W + (len(row) - 1) * H_GAP
        x0 = (width - row_w) / 2
        y = MARGIN + ri * (NODE_H + V_GAP)
        for i, n in enumerate(row):
            pos[n] = (x0 + i * (NODE_W + H_GAP), y)

    edge_svg = []
    for edge in edges:
        s, d = edge["from"], edge["to"]
        if s not in pos or d not in pos:
            continue
        sx, sy = pos[s]
        dx, dy = pos[d]
        x1, y1 = sx + NODE_W / 2, sy + NODE_H
        x2, y2 = dx + NODE_W / 2, dy
        my = (y1 + y2) / 2
        partial = " partial" if edge.get("derivation") in ("manifest", "lockfile") else ""
        edge_svg.append(
            f'<path class="edge{partial}" d="M{x1:.1f},{y1:.1f} '
            f'C{x1:.1f},{my:.1f} {x2:.1f},{my:.1f} {x2:.1f},{y2:.1f}" />'
        )

    node_svg = []
    for n, meta in sorted(nodes.items()):
        if n not in pos:
            continue
        x, y = pos[n]
        cls = ("n-consumer" if meta.get("kind") == "consumer"
               else GRADE_CLASS.get(meta.get("grade", "unknown"), "n-neutral"))
        label = html.escape(meta.get("label", n))
        sub = html.escape(meta.get("sub", ""))
        text = (
            f'<text x="{NODE_W/2:.1f}" y="{NODE_H/2 - 5:.1f}" '
            f'dominant-baseline="central" text-anchor="middle">{label}</text>'
            f'<text class="sub" x="{NODE_W/2:.1f}" y="{NODE_H/2 + 10:.1f}" '
            f'dominant-baseline="central" text-anchor="middle">{sub}</text>'
        ) if sub else (
            f'<text x="{NODE_W/2:.1f}" y="{NODE_H/2:.1f}" '
            f'dominant-baseline="central" text-anchor="middle">{label}</text>'
        )
        node_svg.append(
            f'<g class="node {cls}" transform="translate({x:.1f},{y:.1f})">'
            f'<title>{label}{" — " + sub if sub else ""}</title>'
            f'<rect width="{NODE_W}" height="{NODE_H}" rx="9" />{text}</g>'
        )

    return (
        f'<svg class="topo" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" '
        f'style="min-width:{width:.0f}px" role="img" '
        'aria-label="Fork dependency topology">'
        f'{"".join(edge_svg)}{"".join(node_svg)}</svg>'
    )


def edge_list(nodes: dict, edges: list[dict]) -> str:
    """The same graph as text, for screens a node-link diagram does not suit.

    On a phone the right form for this data is not a diagram. The canvas is
    ~1,100px wide with its root centred, so a 390px viewport opens on empty
    space, and scaling it to fit would put the labels back below legibility.
    Both are emitted and CSS picks one, so neither costs a request or any JS.
    """
    out: dict[str, list[str]] = {}
    for edge in edges:
        src, dst = edge["from"], edge["to"]
        if src not in nodes or dst not in nodes:
            continue
        out.setdefault(src, []).append(dst)
    if not out:
        return '<p class="empty">No fork depends on another fork.</p>'

    def label(node_id: str) -> str:
        return html.escape(nodes[node_id].get("label", node_id))

    rows = []
    for src in sorted(out, key=label):
        meta = nodes[src]
        grade = meta.get("grade", "")
        chip = (f'<span class="chip c-{html.escape(grade)}">{html.escape(grade)}</span>'
                if grade and grade != "unknown" else "")
        targets = " ".join(
            f'<span>{label(d)}</span>' for d in sorted(set(out[src]), key=label)
        )
        rows.append(
            f'<li><div class="from">{label(src)}{chip}</div>'
            f'<div class="to">{targets}</div></li>'
        )
    return '<ul class="topo-list">' + "".join(rows) + "</ul>"


def panel(svg: str, nodes: dict | None = None, edges: list[dict] | None = None) -> str:
    """The diagram for wide screens, the same edges as a list for narrow ones."""
    if not svg:
        return '<p class="empty">No graph to draw.</p>'
    listing = edge_list(nodes or {}, edges or [])
    return (f'<div class="topo-scroll">{svg}</div>'
            f'<div class="topo-narrow">{listing}</div>'
            f'{LEGEND}'
            '<p class="topo-hint">Scroll sideways to follow the widest layer — the '
            'graph is drawn at full size rather than scaled down to fit.</p>')


def graph_from(observation: dict) -> tuple[dict, list[dict]]:
    """Build the renderable graph straight from the observation."""
    nodes: dict[str, dict] = {}
    for fork in observation.get("forks", []):
        tree = fork.get("tree", {})
        counts = tree.get("counts", {})
        bits = []
        if counts.get("time_bomb"):
            n = counts["time_bomb"]
            bits.append(f"{n} bomb{'' if n == 1 else 's'}")
        if tree.get("advisories"):
            n = tree["advisories"]
            bits.append(f"{n} advisor{'y' if n == 1 else 'ies'}")
        if counts.get("unknown"):
            bits.append(f"{counts['unknown']} unknown")
        nodes[fork["package"]] = {
            "label": fork["package"].split("/", 1)[-1],
            "sub": ", ".join(bits),
            "kind": "fork",
            "grade": fork.get("grade", "unknown"),
        }

    edges = list(observation.get("edges", []))
    for edge in observation.get("consumer_edges", []):
        if edge["from"] in nodes:
            edges.append({"from": edge["from"], "to": edge["to"], "derivation": "both"})
            continue
        cid = "consumer:" + edge["from"]
        nodes.setdefault(cid, {"label": edge["from"], "sub": "", "kind": "consumer"})
        edges.append({"from": cid, "to": edge["to"], "derivation": "both"})
    return nodes, edges
