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
  --fg: #14161c; --fg-muted: #666e7d;
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
    --fg: #e7e9ee; --fg-muted: #98a1b1;
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
  --fg: #e7e9ee; --fg-muted: #98a1b1;
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
.tile .l { font-size: 11.5px; color: var(--fg-muted); margin-top: 3px; }
.tile .d { font-size: 11px; color: var(--fg-muted); margin-top: 5px;
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
table.t { border-collapse: collapse; width: 100%; font-size: 12.5px; min-width: 560px; }
table.t th, table.t td { text-align: left; padding: 8px 11px;
  border-bottom: 1px solid var(--border-muted); white-space: nowrap; vertical-align: top; }
table.t thead th { font-size: 10.5px; letter-spacing: .07em; text-transform: uppercase;
  color: var(--fg-muted); background: var(--panel-2); position: sticky; top: 0; }
table.t tbody tr:last-child td { border-bottom: none; }
table.t td.num { text-align: right; font-variant-numeric: tabular-nums; }
table.t td.wrap { white-space: normal; min-width: 220px; }
table.t td.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
table.t tr.emergency td { background: var(--bad-bg); }

/* ---- trails ---- */
.trail { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 2px 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11.5px; }
.trail span { background: var(--chip-bg); border-radius: 4px; padding: 1px 5px; color: var(--fg-muted); }
.trail .root { color: var(--accent); font-weight: 650; }
.trail .leaf { color: var(--bad); font-weight: 650; }
.trail i { color: var(--fg-muted); font-style: normal; opacity: .6; }
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
.opt .c { color: var(--fg-muted); margin-top: 5px; font-size: 11.5px; }

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
    return PALETTE + BASE + extra
