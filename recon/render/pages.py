"""The seven pages, rendered from one observation.

Every aggregate carries its denominator and every fact carries its provenance,
because those are the two things whose absence let wrong numbers look right for
as long as they did. The coverage page is first-class rather than a footnote for
the same reason: a tool whose failure mode is silent wrongness should make its
own limits the easiest thing on the site to find.
"""
from __future__ import annotations

from . import svg
from .components import (PAGES, banner, chip, e, page, sparkline, stat, table,
                         trail, value)
from .theme import stylesheet

CSS = stylesheet(svg.CSS)

STATE_ORDER = {"time_bomb": 0, "unknown": 1, "inert": 2, "alive": 3}
GRADE_BLURB = {
    "emergency": "abandoned dependencies with live advisories beneath it",
    "at-risk": "carries time bombs in its resolved tree",
    "unmeasured": "something beneath it could not be measured",
    "clean": "nothing abandoned-and-rotting beneath it",
    "unknown": "tree could not be resolved",
}


def _denominator(obs: dict) -> str:
    t = obs["totals"]
    cov = obs["coverage"]
    excluded = len(cov["repos"]["excluded"])
    failed = len(cov["trees"]["failed"])
    bits = [f'of {t["packages"]} resolved']
    if t["unknown"]:
        bits.append(f'{t["unknown"]} unknown')
    if excluded:
        bits.append(f"{excluded} repos excluded")
    if failed:
        bits.append(f"{failed} trees unresolved")
    return "; ".join(bits)


def _coverage_line(obs: dict) -> str:
    repos = obs["coverage"]["repos"]
    excluded = repos["excluded"]
    line = (f'Covering <b>{repos["included"]}</b> of <b>{repos["discovered"]}</b> '
            "discovered repositories")
    if excluded:
        named = ", ".join(f'{x["repo"]} ({x["reason"]})' for x in excluded[:3])
        more = f" and {len(excluded) - 3} more" if len(excluded) > 3 else ""
        line += f' — {len(excluded)} excluded: {e(named)}{more}'
    return line + '. <a href="health.html">Full ledger →</a>'


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
def overview(obs: dict, trend: dict) -> str:
    t = obs["totals"]
    denom = _denominator(obs)
    tiles = "".join([
        stat(t["forks"], "forks tracked", cls="key",
             denominator=f'{obs["coverage"]["repos"]["discovered"]} repos discovered'),
        stat(t["emergencies"], "emergencies", cls="bad" if t["emergencies"] else "",
             denominator="abandoned + carrying deps + live advisory"),
        stat(t["time_bomb"], "time bombs", cls="bad" if t["time_bomb"] else "",
             denominator=denom),
        stat(t["unknown"], "unknown", cls="warn" if t["unknown"] else "",
             denominator="could not be measured — not the same as healthy"),
        stat(t["inert"], "inert", denominator="abandoned, nothing beneath to rot"),
        stat(t["alive"], "alive", cls="ok", denominator="released within the threshold"),
    ])

    spark = ""
    if trend.get("time_bomb"):
        spark = (
            '<div class="grid2">'
            f'<div><h2 class="section">Time bombs over time</h2>'
            f'{sparkline(trend["time_bomb"])}</div>'
            f'<div><h2 class="section">Packages resolved</h2>'
            f'{sparkline(trend["packages"])}</div></div>'
        )

    top = obs["queue"][:5]
    queue_rows = [
        f'<tr class="{"emergency" if q["emergency"] else ""}">'
        f'<td class="mono">{e(q["package"])}</td>'
        f'<td>{chip(q["state"])}</td>'
        f'<td class="num">{q["clears_count"]}</td>'
        f'<td class="num">{len(q["forks"])}</td>'
        f'<td class="num">{q["score"]}</td>'
        f'<td class="wrap">{e(", ".join(q["clears"][:4]) or "—")}</td></tr>'
        for q in top
    ]

    nodes, edges = svg.graph_from(obs)
    return page(
        current="index.html", title="overview",
        lede=(
            "Which abandoned dependencies are rotting beneath the "
            "<code>@unabandoned/*</code> forks, which single change removes the most "
            f"of it, and how much of the picture we can actually see. {_coverage_line(obs)}"
        ),
        integrity=obs["integrity"],
        css=CSS, meta=obs["meta"],
        body=(
            f'<div class="tiles">{tiles}</div>'
            + spark
            + '<h2 class="section">Top interventions</h2>'
            '<p class="lede">Ranked by how much rot each one removes, not by how '
            'many trees the package appears in. <a href="queue.html">Full queue →</a></p>'
            + table(
                ["Package", "State", "Clears", "Forks", "Score", "Would clear"],
                queue_rows,
                empty="Nothing actionable — no time bombs in any resolved tree.",
            )
            + '<h2 class="section">Topology</h2>'
            '<p class="lede">Consumers on top, forks beneath, coloured by what is in '
            'their trees. <a href="topology.html">Full view →</a></p>'
            f'<div class="panel">{svg.panel(svg.render(nodes, edges))}</div>'
        ),
    )


# --------------------------------------------------------------------------- #
# Forks
# --------------------------------------------------------------------------- #
def forks(obs: dict) -> str:
    rows = []
    for f in sorted(obs["forks"], key=lambda f: (f["grade"] != "emergency", f["package"])):
        counts = f["tree"]["counts"]
        install = f'npm i {f["package"]}'
        rows.append(
            f'<tr class="{"emergency" if f["grade"] == "emergency" else ""}">'
            f'<td class="mono"><a href="{e(f["url"])}">{e(f["package"])}</a></td>'
            f'<td>{chip(f["grade"])}</td>'
            f'<td>{value(f["published_version"], fallback="unpublished")}</td>'
            f'<td>{value(f["ci"])}</td>'
            f'<td class="num">{counts["time_bomb"]}</td>'
            f'<td class="num">{counts["unknown"]}</td>'
            f'<td class="num">{f["tree"]["advisories"]}</td>'
            f'<td class="num">{f["tree"]["total"]}</td>'
            f'<td>{value(f["open_prs"])}</td>'
            f'<td>{value(f["open_issues"])}</td>'
            f'<td class="mono">{e(install)}</td></tr>'
        )

    return page(
        current="forks.html", title="forks",
        lede=(
            "The consumer's question: can my project depend on this today? A fork's "
            "<b>grade</b> is derived from everything beneath it, not from its own "
            "freshness — a perfectly maintained fork sitting on an abandoned subtree "
            "is not clean, and saying so is the whole point."
        ),
        integrity=obs["integrity"], css=CSS, meta=obs["meta"],
        body=(
            '<div class="panel"><h2>Grades</h2><p class="sub">'
            + " · ".join(
                f"{chip(g)} {e(blurb)}" for g, blurb in GRADE_BLURB.items()
            )
            + "</p></div>"
            + table(
                ["Fork", "Grade", "Published", "CI", "Bombs", "Unknown",
                 "Advisories", "Tree", "PRs", "Issues", "Install"],
                rows, empty="No forks discovered.",
            )
        ),
    )


# --------------------------------------------------------------------------- #
# Work queue
# --------------------------------------------------------------------------- #
def queue(obs: dict) -> str:
    entries = []
    for i, q in enumerate(obs["queue"], start=1):
        routes = "".join(
            '<div>' + trail(p["fork"], p["via"], q["package"])
            + (f'<div class="opt c">clears {e(", ".join(p["clears"]))}</div>'
               if p["clears"] else "")
            + "</div>"
            for p in q["paths"]
        )
        options = "".join(
            f'<div class="opt"><div class="a">{e(o["action"])}</div>'
            f'{e(o["effect"])}<div class="c">cost: {e(o["cost"])}</div></div>'
            for o in q["options"]
        )
        advisories = (
            '<h4>Advisories</h4><p>' + ", ".join(
                f'<a href="https://osv.dev/vulnerability/{e(a)}">{e(a)}</a>'
                for a in q["advisories"]
            ) + "</p>"
        ) if q["advisories"] else ""
        shadowed = (
            f'<p class="opt c">Shadowed in {e(", ".join(q["shadowed_in"]))} — fixing '
            "something above it there makes this moot.</p>"
        ) if q["shadowed_in"] else ""

        entries.append(
            f'<details class="q {"em" if q["emergency"] else ""}">'
            f'<summary><span class="rank">#{i}</span>'
            f'<span class="name">{e(q["package"])}</span>'
            f'{chip("emergency") if q["emergency"] else chip(q["state"])}'
            f'<span class="why">clears <b>{q["clears_count"]}</b> rotten node(s) '
            f'across <b>{len(q["forks"])}</b> fork(s) · score {q["score"]}</span>'
            "</summary>"
            f'<div class="body">{advisories}'
            "<h4>How it enters each tree</h4>"
            f'<div class="routes">{routes}</div>'
            f"{shadowed}"
            "<h4>Options, and what each one cascades into</h4>"
            f'<div class="opts">{options}</div>'
            "</div></details>"
        )

    return page(
        current="queue.html", title="work queue",
        lede=(
            "Not \"which packages are rotten\" — a list — but \"which single change "
            "removes the most rot\", which is a graph question. A rotten node that "
            "another rotten node <b>dominates</b> is shadowed: fixing the one above "
            "moots it, so it is not separately actionable. What is left is the highest "
            "rot on each path, scored by how much it clears. Dominators are computed "
            "per fork on <code>(name, version)</code> identity and aggregated after — "
            "collapsing by name first is how a table ends up reporting membership when "
            "it was asked about causation."
        ),
        integrity=obs["integrity"], css=CSS, meta=obs["meta"],
        body="".join(entries) or '<p class="empty">Queue is empty — nothing '
                                 "abandoned-and-rotting in any resolved tree.</p>",
    )


# --------------------------------------------------------------------------- #
# Packages
# --------------------------------------------------------------------------- #
STATE_BLURB = {
    "time_bomb": "abandoned and carrying their own dependencies — the only actionable class",
    "unknown": "could not be measured; unmeasured is not the same as healthy",
    "inert": "abandoned but declaring no dependencies — nothing beneath them to rot",
    "alive": "released within the threshold",
}


def packages(obs: dict) -> str:
    ordered = sorted(
        obs["packages"],
        key=lambda p: (STATE_ORDER.get(p["state"], 9), -len(p["forks"]), p["name"]),
    )
    counts: dict[str, int] = {}
    for p in ordered:
        counts[p["state"]] = counts.get(p["state"], 0) + 1

    rows = []
    seen: set[str] = set()
    for p in ordered:
        # The table is sorted by state, so repeating the chip on every row would
        # print `time bomb` 46 times in a column where every visible value is the
        # same — loud, and carrying no information. One banded header per group
        # says it once and gives the eye somewhere to rest.
        if p["state"] not in seen:
            seen.add(p["state"])
            rows.append(
                f'<tr class="group-head"><td colspan="7">{chip(p["state"])}'
                f'<b>{counts[p["state"]]}</b>'
                f'<span>{e(STATE_BLURB.get(p["state"], ""))}</span></td></tr>'
            )
        shortest = p.get("shortest")
        route = (trail(shortest["fork"], shortest["via"], p["name"])
                 if shortest else '<span class="empty">—</span>')
        adv = ", ".join(a["id"] for a in p["advisories"])
        ev = p.get("evidence", {})
        last = (ev.get("last_release") or {})
        when = last.get("value") if last.get("status") == "ok" else "unknown"
        emergency = p["advisories"] and p["state"] == "time_bomb"
        rows.append(
            f'<tr class="{"emergency" if emergency else ""}">'
            f'<td class="mono strong">{e(p["name"])}</td>'
            f'<td class="num">{p["ndeps"]}</td>'
            f'<td class="num">{len(p["forks"])}</td>'
            f'<td class="prov tight" title="{e(p["reason"])}">{e(when)}</td>'
            f'<td class="mono tight">{e(adv)}</td>'
            f'<td class="path">{route}</td></tr>'
        )

    return page(
        current="packages.html", title="packages",
        lede=(
            "Every package in every resolved tree, with the evidence for its "
            "classification — hover a date for the reason and the fetch that "
            "established it. <b>unknown</b> is a real state here, not a blank: a "
            "package we could not date is unmeasured, which is a different thing "
            "from healthy and renders as a different thing."
        ),
        integrity=obs["integrity"], css=CSS, meta=obs["meta"],
        body=table(
            ["Package", "Own deps", "Forks", "Last release",
             "Advisories", "Shortest path in"],
            rows, empty="No packages resolved.", columns=6,
            widths=["23%", "9%", "7%", "12%", "10%", "39%"],
        ),
    )


# --------------------------------------------------------------------------- #
# Topology
# --------------------------------------------------------------------------- #
def topology(obs: dict) -> str:
    nodes, edges = svg.graph_from(obs)
    partial = [x for x in obs["edges"] if x["derivation"] != "both"]
    note = ""
    if partial:
        note = (
            '<p class="lede">⚠ ' + str(len(partial)) + " edge(s) were seen by only one "
            "of the two readers and are drawn dashed. In a healthy build there are none "
            "— the M2 check fails first.</p>"
        )
    return page(
        current="topology.html", title="topology",
        lede=(
            "De-risking flows up the tree. Every edge is derived twice — once from each "
            "fork's manifest (including the alias syntax where the scope lives in the "
            "value, not the key) and once from the lockfile npm's own resolver produced "
            "— and the build fails if the two disagree."
        ),
        integrity=obs["integrity"], css=CSS, meta=obs["meta"],
        body=note + f'<div class="panel">{svg.panel(svg.render(nodes, edges))}</div>',
    )


# --------------------------------------------------------------------------- #
# Changes
# --------------------------------------------------------------------------- #
def changes(obs: dict, delta: dict) -> str:
    if delta.get("baseline"):
        body = ('<p class="empty">This is the first snapshot — there is nothing to '
                "compare against yet. Every subsequent build lands here.</p>")
        return page(current="changes.html", title="changes",
                    lede="What moved since the previous build.",
                    integrity=obs["integrity"], css=CSS, meta=obs["meta"], body=body)

    trans = [
        f'<tr><td class="mono">{e(t["package"])}</td>'
        f'<td>{chip(t["was"]) if t["was"] != "absent" else "—"}</td>'
        f'<td>{chip(t["now"]) if t["now"] != "absent" else "—"}</td>'
        f'<td class="wrap">{e(t["reason"])}</td></tr>'
        for t in delta["transitions"]
    ]
    def _compare_cell(f: dict) -> str:
        url = f.get("compare")
        return f'<a href="{e(url)}">compare</a>' if url else "—"

    forks_rows = [
        f'<tr><td class="mono">{e(f["package"])}</td>'
        f'<td>{e(f["change"])}</td>'
        f'<td>{chip(f["grade_was"]) if f.get("grade_was") else "—"} → '
        f'{chip(f["grade_now"]) if f.get("grade_now") else "—"}</td>'
        f'<td>{_compare_cell(f)}</td>'
        "</tr>"
        for f in delta["forks"]
    ]
    adv_rows = [
        f'<tr><td class="mono">{e(a["package"])}</td><td>appeared</td>'
        f'<td class="mono"><a href="https://osv.dev/vulnerability/{e(a["id"])}">'
        f'{e(a["id"])}</a></td></tr>'
        for a in delta["advisories"]["appeared"]
    ] + [
        f'<tr><td class="mono">{e(a["package"])}</td><td>cleared</td>'
        f'<td class="mono">{e(a["id"])}</td></tr>'
        for a in delta["advisories"]["cleared"]
    ]

    return page(
        current="changes.html", title="changes",
        lede=(
            f'What moved since {e(delta.get("since") or "the previous build")}. Each fork '
            "records its <code>head_sha</code> in every snapshot, so the delta between "
            "two builds brackets exactly the commit range that could have caused it — "
            '"which merge did this?" answers itself.'
        ),
        integrity=obs["integrity"], css=CSS, meta=obs["meta"],
        body=(
            '<h2 class="section">Classification transitions</h2>'
            + table(["Package", "Was", "Now", "Why"], trans,
                    empty="No package changed state.")
            + '<h2 class="section">Forks</h2>'
            + table(["Fork", "Change", "Grade", "Commits"], forks_rows,
                    empty="No fork moved or changed grade.")
            + '<h2 class="section">Advisories</h2>'
            + table(["Package", "Change", "Advisory"], adv_rows,
                    empty="No advisory appeared or cleared.")
        ),
    )


# --------------------------------------------------------------------------- #
# Coverage & health
# --------------------------------------------------------------------------- #
def health(obs: dict) -> str:
    integ = obs["integrity"]
    cov = obs["coverage"]

    check_rows = [
        f'<tr><td>{chip(c["status"])}</td>'
        f'<td class="mono">{e(c["id"])}</td>'
        f'<td>{e(c["mechanism"])}</td>'
        f'<td class="wrap">{e(c["title"])}</td>'
        f'<td class="wrap">{e(c["detail"])}</td></tr>'
        for c in sorted(integ["checks"],
                        key=lambda c: ({"fail": 0, "warn": 1, "pass": 2}[c["status"]], c["id"]))
    ]
    excl_rows = [
        f'<tr><td class="mono">{e(x["repo"])}</td><td>{chip("neutral", x["reason"])}</td>'
        f'<td class="wrap">{e(x.get("detail", ""))}</td></tr>'
        for x in cov["repos"]["excluded"]
    ]
    tree_rows = [
        f'<tr><td class="mono">{e(x["fork"])}</td><td class="wrap">{e(x["reason"])}</td></tr>'
        for x in cov["trees"]["failed"]
    ]
    fetch_rows = [
        f'<tr><td class="mono wrap">{e(x["url"])}</td>'
        f'<td class="num">{e(x.get("code") or "—")}</td>'
        f'<td class="wrap">{e(x["detail"])}</td></tr>'
        for x in cov["fetches"]["failures"]
    ]
    unknown_rows = [
        f'<tr><td class="mono">{e(p["name"])}</td>'
        f'<td class="num">{len(p["forks"])}</td>'
        f'<td class="wrap">{e(p["reason"])}</td></tr>'
        for p in obs["packages"] if p["state"] == "unknown"
    ]

    tiles = "".join([
        stat(cov["repos"]["discovered"], "repos discovered", cls="key"),
        stat(cov["repos"]["included"], "included",
             denominator=f'{len(cov["repos"]["excluded"])} excluded, all reasoned'),
        stat(cov["trees"]["resolved"], "trees resolved",
             cls="warn" if cov["trees"]["failed"] else "",
             denominator=f'{len(cov["trees"]["failed"])} failed'),
        stat(cov["fetches"]["attempted"], "fetches attempted",
             denominator=f'{cov["fetches"]["failed"]} failed'),
        stat(obs["totals"]["unknown"], "packages unmeasured",
             cls="warn" if obs["totals"]["unknown"] else ""),
        stat(integ["counts"].get("fail", 0), "checks failing",
             cls="bad" if integ["counts"].get("fail") else "ok",
             denominator=f'{integ["counts"].get("warn", 0)} warning(s), '
                         f'{integ["counts"].get("pass", 0)} passing'),
    ])

    return page(
        current="health.html", title="coverage & health",
        lede=(
            "Coverage is a ledger, not a caveat. Every repository the build saw is "
            "either included or excluded with a named reason, and the two have to add "
            "up — <code>m4.conservation</code> fails the build if they do not. A tool "
            "whose failure mode is silent wrongness should make its own limits the "
            "easiest thing to find."
        ),
        integrity=integ, css=CSS, meta=obs["meta"],
        body=(
            f'<div class="tiles">{tiles}</div>'
            '<h2 class="section">Integrity checks</h2>'
            '<p class="lede">Each one is a statement about what the world cannot look '
            "like, phrased so a bug of a known class trips it. Only <b>fail</b> means a "
            "number is probably wrong.</p>"
            + table(["", "Check", "Mechanism", "Asserts", "Result"], check_rows)
            + '<h2 class="section">Repositories excluded</h2>'
            + table(["Repo", "Reason", "Detail"], excl_rows,
                    empty="Every discovered repository is included.")
            + '<h2 class="section">Trees that failed to resolve</h2>'
            + table(["Fork", "Reason"], tree_rows, empty="Every tree resolved.")
            + '<h2 class="section">Failed fetches</h2>'
            '<p class="lede">Each of these produced an <b>unknown</b> downstream rather '
            "than a default value. That is the entire point of the exercise.</p>"
            + table(["URL", "Code", "Detail"], fetch_rows,
                    empty="Every fetch succeeded.")
            + '<h2 class="section">Unmeasured packages</h2>'
            + table(["Package", "Forks", "Why unknown"], unknown_rows,
                    empty="Every package in every tree was measurable.")
        ),
    )


def render_all(obs: dict, delta: dict, trend: dict) -> dict[str, str]:
    return {
        "index.html": overview(obs, trend),
        "forks.html": forks(obs),
        "queue.html": queue(obs),
        "packages.html": packages(obs),
        "topology.html": topology(obs),
        "changes.html": changes(obs, delta),
        "health.html": health(obs),
    }
