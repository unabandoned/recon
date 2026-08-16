"""Renderer primitives that take fact-records, not values.

This is where the M1 discipline reaches the page. `stat()` and `value()` accept
the serialised fact — `{"status": ..., "value": ..., "source": ..., "fetched_at": ...}`
— and there is deliberately no overload that takes a bare number. A caller who
wants to print something must hand over its provenance, because the alternative
is a page full of confident numbers whose origins nobody can reconstruct.

The consequences are small and pleasant: an unknown renders as an amber
"unknown" carrying the reason in its tooltip instead of a plausible zero, and
every aggregate can show its denominator without any extra plumbing.
"""
from __future__ import annotations

import html
from typing import Any, Iterable

__all__ = [
    "e", "chip", "value", "stat", "trail", "table", "banner", "sparkline",
    "page", "tabs", "PAGES",
]

PAGES = [
    ("index.html", "Overview"),
    ("forks.html", "Forks"),
    ("queue.html", "Work queue"),
    ("packages.html", "Packages"),
    ("topology.html", "Topology"),
    ("changes.html", "Changes"),
    ("health.html", "Coverage &amp; health"),
]


def e(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def chip(kind: str, label: str | None = None) -> str:
    text = label if label is not None else str(kind).replace("_", " ")
    return f'<span class="chip c-{e(kind)}">{e(text)}</span>'


# --------------------------------------------------------------------------- #
# Facts on the page
# --------------------------------------------------------------------------- #
def _tooltip(fact: dict) -> str:
    bits = [f"status: {fact.get('status', '?')}"]
    if fact.get("source"):
        bits.append(f"source: {fact['source']}")
    if fact.get("fetched_at"):
        bits.append(f"fetched: {fact['fetched_at']}")
    if fact.get("detail"):
        bits.append(f"detail: {fact['detail']}")
    return " · ".join(bits)


def value(fact: dict | None, *, fallback: str = "unknown") -> str:
    """Render one fact inline, carrying its provenance in the tooltip.

    A non-ok fact renders as the word `unknown`, styled to be noticed, with the
    reason available on hover. It never renders as a dash, a zero, or an empty
    cell — all three read as "nothing to see here", which is exactly the wrong
    thing to say about a measurement that failed.
    """
    if not isinstance(fact, dict):
        return f'<span class="unknown-v">{e(fallback)}</span>'
    if fact.get("status") != "ok":
        return (f'<span class="unknown-v prov" title="{e(_tooltip(fact))}">'
                f"{e(fallback)}</span>")
    payload = fact.get("value")
    if payload is None:
        return f'<span class="prov" title="{e(_tooltip(fact))}">none</span>'
    if isinstance(payload, bool):
        payload = "yes" if payload else "no"
    return f'<span class="prov" title="{e(_tooltip(fact))}">{e(payload)}</span>'


def stat(n: Any, label: str, *, cls: str = "", denominator: str = "",
         fact: dict | None = None) -> str:
    """One headline tile. `denominator` is not optional in spirit.

    Every aggregate on this site shows what it is a fraction of, because the
    number alone has repeatedly been the thing that was wrong.
    """
    if fact is not None and fact.get("status") != "ok":
        body = f'<div class="n unknown-v prov" title="{e(_tooltip(fact))}">unknown</div>'
    else:
        body = f'<div class="n">{e(n)}</div>'
    denom = f'<div class="d">{denominator}</div>' if denominator else ""
    return (f'<div class="tile {e(cls)}">{body}'
            f'<div class="l">{e(label)}</div>{denom}</div>')


def trail(root: str, via: Iterable[str], leaf: str) -> str:
    """`browserify -> crypto-browserify -> hash-base -> readable-stream`.

    A package deep in a tree is not actionable until you can see which link put
    it there, so the chain renders in full rather than as a count of hops.
    """
    hops = [f'<span class="root">{e(root)}</span>']
    hops += [f"<span>{e(v)}</span>" for v in (via or [])]
    hops.append(f'<span class="leaf">{e(leaf)}</span>')
    # Plain text with a hairline separator. These were filled pills, which put
    # ~440 grey blocks on the packages page and made the least important column
    # the loudest thing on it.
    return '<span class="trail">' + "<i>›</i>".join(hops) + "</span>"


def table(headers: list[str], rows: list[str], *, empty: str = "Nothing to show.",
          columns: int | None = None, widths: list[str] | None = None) -> str:
    """`columns` pads the header row when body rows span more cells than headers —
    a grouped table has a full-width band the headers do not describe.

    `widths` switches the table to fixed layout and makes the column widths
    authoritative. Auto layout hands surplus width to whichever column has the
    longest content, which is how a column of empty cells kept 185px while the
    one carrying the dependency paths was squeezed into three wrapped lines.
    """
    if not rows:
        return f'<p class="empty">{e(empty)}</p>'
    head = "".join(f"<th>{h}</th>" for h in headers)
    if columns and columns > len(headers):
        head += "<th></th>" * (columns - len(headers))
    cols = ""
    cls = "t"
    if widths:
        cols = "<colgroup>" + "".join(f'<col style="width:{w}">' for w in widths) + "</colgroup>"
        cls = "t fixed"
    return (f'<div class="scroll"><table class="{cls}">{cols}<thead><tr>' + head
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def banner(integrity: dict) -> str:
    """The build-wide verdict, at the top of every page.

    A visibly broken dashboard gets fixed; a silently stale one does not. So a
    failed check publishes *with* this banner rather than publishing nothing.
    """
    status = integrity.get("status", "pass")
    counts = integrity.get("counts", {})
    checks = integrity.get("checks", [])
    if status == "fail":
        failed = [c for c in checks if c["status"] == "fail"]
        detail = "; ".join(c["detail"] for c in failed[:2])
        return (
            '<div class="banner fail"><span class="icon">⚠</span><div>'
            f"<b>Integrity failure — these numbers are not trustworthy.</b>"
            f"{e(detail)} "
            f'<a href="health.html">See all {len(failed)} failing check(s) →</a>'
            "</div></div>"
        )
    if status == "warn":
        return (
            '<div class="banner warn"><span class="icon">◐</span><div>'
            f"<b>{counts.get('warn', 0)} integrity warning(s).</b>"
            "Nothing indicates a miscount, but something is worth a look. "
            '<a href="health.html">Coverage &amp; health →</a>'
            "</div></div>"
        )
    return (
        '<div class="banner pass"><span class="icon">✓</span><div>'
        f"<b>All {counts.get('pass', 0)} integrity checks passed.</b>"
        "Every number below was cross-checked against an independent derivation "
        'or a hand-asserted fact. <a href="health.html">See the checks →</a>'
        "</div></div>"
    )


def sparkline(series: list, *, width: int = 260, height: int = 34) -> str:
    """A trend line computed at build time — no client-side history fetching."""
    points = [v for v in series if isinstance(v, (int, float))]
    if len(points) < 2:
        return '<p class="empty">Not enough history yet — one snapshot per build.</p>'
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1
    step = width / (len(points) - 1)
    coords = [
        (i * step, height - ((v - lo) / span) * (height - 6) - 3)
        for i, v in enumerate(points)
    ]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = line + f" L{coords[-1][0]:.1f},{height} L0,{height} Z"
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'role="img" aria-label="trend from {lo} to {hi}">'
        f'<path class="area" d="{area}" /><path d="{line}" /></svg>'
    )


# --------------------------------------------------------------------------- #
# Page shell
# --------------------------------------------------------------------------- #
def tabs(current: str) -> str:
    return '<nav class="tabs">' + "".join(
        f'<a class="{"on" if href == current else ""}" href="{href}">{label}</a>'
        for href, label in PAGES
    ) + "</nav>"


def page(*, current: str, title: str, lede: str, body: str, css: str,
         meta: dict, integrity: dict | None = None) -> str:
    built = (meta or {}).get("built_at", "")
    sha = (meta or {}).get("builder_sha", "")
    duration = (meta or {}).get("duration_ms")
    foot_bits = [f"Built {e(built)}"]
    if duration is not None:
        foot_bits.append(f"in {duration / 1000:.1f}s")
    if sha:
        foot_bits.append(f"from <code>{e(sha[:8])}</code>")
    foot_bits.append(
        'Generated by <a href="https://github.com/unabandoned/recon">recon</a> — '
        'never hand-edited. <a href="observation.json">observation.json</a>'
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>recon — {e(title)}</title>"
        f"<style>{css}</style></head><body><div class=\"wrap\">"
        '<div class="masthead"><h1>recon <span class="dim">· '
        f'{e(title)}</span></h1></div>'
        f'<p class="lede">{lede}</p>'
        + tabs(current)
        + (banner(integrity) if integrity else "")
        + body
        + f'<p class="foot">{" · ".join(foot_bits)}</p>'
        "</div></body></html>"
    )
