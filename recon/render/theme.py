"""One palette and one stylesheet for every page.

Light and dark are both defined on tokens; the dark block only redefines the
tokens, so nothing can be styled correctly in one theme and invisible in the
other. Colour carries meaning consistently across the whole site: red is rot,
amber is unmeasured, green is healthy, and violet is the tool talking about
itself (integrity, provenance).
"""
from __future__ import annotations

PALETTE = """
:root {
  --bg: #fbfbfd; --panel: #ffffff; --panel-2: #f5f6f9;
  --fg: #14161c; --fg-muted: #565e6b;
  --border: #d9dde5; --border-muted: #e8ebf0;
  --accent: #4b3fd6; --accent-bg: #eeecfd;
  --ok: #17794a; --ok-bg: #e5f5ec;
  --warn: #9a6300; --warn-bg: #fdf1dd;
  --bad: #c02836; --bad-bg: #fdeaec;
  --chip-bg: #eef0f4;
  --shadow: 0 1px 2px rgba(16,20,30,.05), 0 6px 18px rgba(16,20,30,.05);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0e1015; --panel: #171a21; --panel-2: #1e222b;
    --fg: #e7e9ee; --fg-muted: #a3acbb;
    --border: #2b303b; --border-muted: #232830;
    --accent: #9b91ff; --accent-bg: #221f3d;
    --ok: #4ec98a; --ok-bg: #10281c;
    --warn: #e0a952; --warn-bg: #2c2110;
    --bad: #ff7b87; --bad-bg: #331419;
    --chip-bg: #232833;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.25);
  }
}
:root[data-theme="dark"] {
  --bg: #0e1015; --panel: #171a21; --panel-2: #1e222b;
  --fg: #e7e9ee; --fg-muted: #a3acbb;
  --border: #2b303b; --border-muted: #232830;
  --accent: #9b91ff; --accent-bg: #221f3d;
  --ok: #4ec98a; --ok-bg: #10281c;
  --warn: #e0a952; --warn-bg: #2c2110;
  --bad: #ff7b87; --bad-bg: #331419;
  --chip-bg: #232833;
  --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.25);
}
"""

BASE = """
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 26px 22px 64px; }

/* ---- masthead + nav ---- */
.masthead { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: baseline;
  justify-content: space-between; margin-bottom: 6px; }
.masthead h1 { margin: 0; font-size: 21px; letter-spacing: -.02em; font-weight: 650; }
.masthead h1 .dim { color: var(--fg-muted); font-weight: 500; }
.lede { color: var(--fg-muted); margin: 2px 0 18px; max-width: 82ch; font-size: 14px; }
nav.tabs { display: flex; flex-wrap: wrap; gap: 4px; margin: 0 0 22px;
  border-bottom: 1px solid var(--border); }
nav.tabs a { padding: 8px 13px; font-size: 13.5px; font-weight: 550; color: var(--fg-muted);
  border-bottom: 2px solid transparent; margin-bottom: -1px; }
nav.tabs a:hover { color: var(--fg); text-decoration: none; }
nav.tabs a.on { color: var(--accent); border-bottom-color: var(--accent); }

/* ---- panels ---- */
.panel { background: var(--panel); border: 1px solid var(--border); border-radius: 13px;
  padding: 18px; box-shadow: var(--shadow); margin: 0 0 20px; }
.panel > h2 { margin: 0 0 4px; font-size: 16px; letter-spacing: -.01em; }
.panel > .sub { margin: 0 0 14px; color: var(--fg-muted); font-size: 13px; max-width: 84ch; }
h2.section { margin: 26px 0 10px; font-size: 16px; letter-spacing: -.01em; }

/* ---- stat tiles ---- */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr)); gap: 11px; }
.tile { background: var(--panel-2); border: 1px solid var(--border-muted);
  border-radius: 10px; padding: 12px 14px; border-top: 3px solid var(--border); }
.tile .n { font-size: 26px; font-weight: 650; letter-spacing: -.02em;
  font-variant-numeric: tabular-nums; line-height: 1.1; }
.tile .l { font-size: 12.5px; color: var(--fg-muted); margin-top: 3px; }
.tile .d { font-size: 12px; line-height: 1.45; color: var(--fg-muted); margin-top: 5px;
  border-top: 1px dashed var(--border-muted); padding-top: 5px; }
.tile.bad { border-top-color: var(--bad); } .tile.bad .n { color: var(--bad); }
.tile.warn { border-top-color: var(--warn); } .tile.warn .n { color: var(--warn); }
.tile.ok { border-top-color: var(--ok); } .tile.ok .n { color: var(--ok); }
.tile.key { border-top-color: var(--accent); } .tile.key .n { color: var(--accent); }

/* ---- chips ---- */
.chip { display: inline-block; font-size: 10.5px; letter-spacing: .05em; font-weight: 650;
  text-transform: uppercase; padding: 2px 7px; border-radius: 999px; white-space: nowrap; }
.c-time_bomb, .c-bad, .c-fail, .c-emergency { background: var(--bad-bg); color: var(--bad); }
.c-unknown, .c-warn, .c-unmeasured { background: var(--warn-bg); color: var(--warn); }
.c-alive, .c-ok, .c-pass, .c-clean { background: var(--ok-bg); color: var(--ok); }
.c-inert, .c-neutral { background: var(--chip-bg); color: var(--fg-muted); }
.c-at-risk { background: var(--bad-bg); color: var(--bad); }
.c-key { background: var(--accent-bg); color: var(--accent); }

/* ---- provenance ---- */
.prov { border-bottom: 1px dotted var(--border); cursor: help; }
.unknown-v { color: var(--warn); font-weight: 650; }
.denom { color: var(--fg-muted); font-weight: 400; font-size: .78em; }

/* ---- tables ---- */
.scroll { overflow-x: auto; border: 1px solid var(--border-muted); border-radius: 10px; }
table.t { border-collapse: collapse; width: 100%; font-size: 13.5px; min-width: 560px; }
table.t th, table.t td { text-align: left; padding: 9px 13px;
  border-bottom: 1px solid var(--border-muted); white-space: nowrap; vertical-align: baseline;
  line-height: 1.5; }
table.t tbody tr:nth-child(even) { background: var(--panel-2); }
table.t tbody tr:hover { background: var(--panel-2); }
/* A state group's first row carries the label; the rest inherit it from the rule above. */
table.t tbody tr.group-start td { border-top: 2px solid var(--border); padding-top: 14px; }
table.t tbody tr.group-start:first-child td { border-top: none; }
table.t td.mono.strong { color: var(--fg); font-weight: 600; }
table.t tr.group-head td { background: var(--panel-2); border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border); padding: 9px 13px; white-space: normal; }
table.t tr.group-head:hover td { background: var(--panel-2); }
table.t tr.group-head b { font-variant-numeric: tabular-nums; margin: 0 8px 0 10px;
  font-size: 13.5px; }
table.t tr.group-head span { color: var(--fg-muted); font-size: 12.5px; font-weight: 400; }
table.t thead th { font-size: 11px; letter-spacing: .07em; text-transform: uppercase;
  color: var(--fg-muted); background: var(--panel-2); position: sticky; top: 0;
  white-space: normal; vertical-align: bottom; }
table.t tbody tr:last-child td { border-bottom: none; }
table.t td.num, table.t th.num { text-align: right; font-variant-numeric: tabular-nums;
  width: 1%; white-space: nowrap; }
table.t td.tight { width: 1%; white-space: nowrap; }
table.t td.dim { color: var(--fg-muted); }
table.t td.wrap { white-space: normal; min-width: 220px; }
table.t.fixed { table-layout: fixed; min-width: 820px; }
table.t td.path { white-space: normal; overflow-wrap: anywhere; }
table.t.fixed td.mono { overflow-wrap: anywhere; white-space: normal; }
table.t td.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
table.t tr.emergency td { background: var(--bad-bg); }

/* ---- trails ---- */
.trail { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 2px 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
.trail span { color: var(--fg-muted); }
.trail .root { color: var(--accent); font-weight: 600; }
.trail .leaf { color: var(--fg); font-weight: 650; }
.trail i { color: var(--fg-muted); opacity: .6; font-style: normal; padding: 0 2px; }
.routes { display: flex; flex-direction: column; gap: 4px; }

/* ---- integrity banner ---- */
.banner { border-radius: 12px; padding: 13px 16px; margin: 0 0 20px; font-size: 13.5px;
  border: 1px solid; display: flex; gap: 12px; align-items: flex-start; }
.banner .icon { font-size: 17px; line-height: 1.2; }
.banner b { display: block; margin-bottom: 2px; }
.banner.fail { background: var(--bad-bg); border-color: var(--bad); color: var(--bad); }
.banner.warn { background: var(--warn-bg); border-color: var(--warn); color: var(--warn); }
.banner.pass { background: var(--ok-bg); border-color: var(--ok); color: var(--ok); }
.banner a { color: inherit; text-decoration: underline; }

/* ---- queue ---- */
.q { border: 1px solid var(--border); border-radius: 12px; margin: 0 0 11px;
  background: var(--panel); overflow: hidden; }
.q.em { border-color: var(--bad); }
/* ---- the compare form ---- */
.cmp { display: grid; gap: 10px; grid-template-columns: 1fr 1fr auto;
  align-items: end; margin: 4px 0 6px; }
.cmp label { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px;
  color: var(--fg-muted); font-weight: 600; min-width: 0; }
.cmp input { font: inherit; font-size: 13.5px; padding: 9px 11px; min-width: 0;
  border: 1px solid var(--border); border-radius: 9px;
  background: var(--panel-2); color: var(--fg); }
.cmp input:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.cmp button { font: inherit; font-size: 13.5px; font-weight: 600; cursor: pointer;
  padding: 10px 20px; border-radius: 9px; border: 1px solid transparent;
  background: var(--accent); color: #fff; min-height: 40px; }
.cmp button:hover { filter: brightness(1.08); }
.cmp-out:not(:empty) { margin-top: 14px; }
.cmp-out .panel { margin-bottom: 12px; }
.cmp-out .tiles { margin-bottom: 12px; }

/* A long table inside a panel: collapsed by default, with its count in the
   summary so the page stays scannable without hiding that the rows exist. */
.fold > summary { cursor: pointer; padding: 4px 0 10px; font-weight: 600;
  list-style: none; color: var(--fg); display: flex; gap: 8px; align-items: baseline; }
.fold > summary::-webkit-details-marker { display: none; }
.fold > summary::before { content: "▸"; color: var(--fg-muted); }
.fold[open] > summary::before { content: "▾"; }
.fold > summary .n { color: var(--fg-muted); font-weight: 500; }
.q > summary { cursor: pointer; padding: 13px 16px; display: flex; flex-wrap: wrap;
  gap: 8px 12px; align-items: baseline; list-style: none; }
.q > summary::-webkit-details-marker { display: none; }
.q > summary::before { content: "▸"; color: var(--fg-muted); margin-right: 2px; }
.q[open] > summary::before { content: "▾"; }
.q .rank { font-variant-numeric: tabular-nums; color: var(--fg-muted); font-size: 12.5px;
  min-width: 2.2em; }
.q .name { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-weight: 650; font-size: 14px; }
.q .why { color: var(--fg-muted); font-size: 12.5px; flex: 1 1 320px; }
.q .body { padding: 0 16px 15px; border-top: 1px solid var(--border-muted); }
.q h4 { margin: 14px 0 6px; font-size: 12px; text-transform: uppercase;
  letter-spacing: .06em; color: var(--fg-muted); }
.opts { display: grid; gap: 8px; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }
.opt { background: var(--panel-2); border: 1px solid var(--border-muted);
  border-radius: 9px; padding: 10px 12px; font-size: 12.5px; }
.opt .a { font-weight: 650; text-transform: uppercase; font-size: 10.5px;
  letter-spacing: .06em; color: var(--accent); margin-bottom: 3px; }
.opt .c { color: var(--fg-muted); margin-top: 5px; font-size: 12.5px; }

/* ---- misc ---- */
.spark { display: block; height: 34px; width: 100%; overflow: visible; }
.spark path { fill: none; stroke: var(--accent); stroke-width: 1.6; }
.spark .area { fill: var(--accent-bg); stroke: none; }
.empty { color: var(--fg-muted); font-size: 13px; padding: 10px 2px; }
.foot { margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border);
  color: var(--fg-muted); font-size: 12px; }
.grid2 { display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); }
"""


def stylesheet(extra: str = "") -> str:
    # MOBILE last: its media query must win over the base rules it narrows.
    return PALETTE + BASE + extra + MOBILE


# --------------------------------------------------------------------------- #
# Small screens
# --------------------------------------------------------------------------- #
# A phone is not a narrow desktop. A six-column table that scrolls sideways is a
# desktop table you have to drag, and on the Forks page that meant seeing "FORK"
# and half of "GRADE" — every other fact hidden behind a gesture nobody makes.
# Below this width the tables stop being tables: each row becomes a card of
# label/value pairs, using the `data-label` each cell carries.
MOBILE = """
@media (max-width: 720px) {
  .wrap { padding: 18px 14px 48px; }
  .masthead h1 { font-size: 19px; }
  .lede { font-size: 14px; margin-bottom: 14px; }

  /* One scrolling strip instead of three stacked rows of tabs. */
  nav.tabs { flex-wrap: nowrap; overflow-x: auto; overscroll-behavior-x: contain;
    scrollbar-width: none; gap: 2px; }
  nav.tabs::-webkit-scrollbar { display: none; }
  nav.tabs a { white-space: nowrap; padding: 8px 11px; }

  .panel { padding: 14px; border-radius: 11px; }
  .tiles { grid-template-columns: repeat(auto-fit, minmax(136px, 1fr)); gap: 9px; }
  .tile .n { font-size: 23px; }

  .q > summary { padding: 12px 13px; gap: 6px 10px; }
  .q .why { flex-basis: 100%; }
  .q .body { padding: 0 13px 13px; }
  .opts { grid-template-columns: 1fr; }
  /* The button needs the full width once the fields stack, or it lands in a
     column of its own beside a wrapped label. */
  .cmp { grid-template-columns: 1fr; }
  .grid2 { grid-template-columns: 1fr; }
  .banner { padding: 12px 13px; }
}

@media (max-width: 900px) {
  /* Tables become cards. */
  .scroll { border: none; border-radius: 0; overflow-x: visible; }
  table.t, table.t.fixed { display: block; min-width: 0; width: 100%; }
  table.t colgroup, table.t thead { display: none; }
  table.t tbody { display: block; }
  table.t tbody tr { display: block; background: var(--panel);
    border: 1px solid var(--border); border-radius: 10px; margin: 0 0 9px; }
  table.t tbody tr:nth-child(even) { background: var(--panel); }
  table.t tbody tr:hover { background: var(--panel); }
  table.t tbody tr.emergency { border-color: var(--bad); }
  table.t td { display: flex; gap: 12px; align-items: baseline; width: auto !important;
    justify-content: space-between; text-align: left; white-space: normal;
    border-bottom: 1px solid var(--border-muted); padding: 8px 12px; }
  /* A card cell is a flex row, and a flex item will not shrink below its
     min-content width. A package name is one unbreakable token — an
     `@unabandoned/combine-source-map` in the value column pushes the whole
     document sideways. `anywhere` (not `break-word`) is the one that also
     reduces the intrinsic minimum, so the item can actually shrink. */
  table.t td, table.t td code { overflow-wrap: anywhere; }
  table.t td > * { min-width: 0; }
  table.t tr td:last-child { border-bottom: none; }
  table.t td:empty { display: none; }
  table.t td::before { content: attr(data-label); color: var(--fg-muted);
    font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
    flex: 0 0 33%; font-weight: 600; }
  table.t td:not([data-label])::before { content: none; }
  /* The first cell is the card's title, so it gets the whole line. */
  table.t td:first-child { display: block; font-size: 14.5px; padding-top: 11px; }
  table.t td:first-child::before { content: none; }
  table.t tr.group-head td { display: block; padding: 9px 12px; }
  table.t tr.group-head td::before { content: none; }
  table.t tr.group-start td { border-top: none; padding-top: 8px; }

  /* A node-link diagram is the wrong form here: swap in the edge list. The
     graph is 1,088px wide, so anything narrower opens on empty canvas. */
  .topo-scroll, .topo-hint, .topo-legend { display: none; }
  .topo-narrow { display: block; }
}
"""
