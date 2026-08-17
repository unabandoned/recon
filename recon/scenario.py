"""What would adopting this package actually cost us?

The work queue answers the *benefit* question — which single change removes the
most rot. This answers the *cost* one, which is the question you ask before
committing rather than after: **if we fork this, what else do we now own?**

They use the same graph and give opposite-shaped answers, so it is worth being
precise about the difference. The queue ranks by blast radius, so a package that
clears 22 rotten nodes sorts to the top. The scenario counts obligations, and a
package can have a large tree and a *small* obligation surface, because most of
what rots beneath it is already rotting beneath something we own. That is the
answer nobody can eyeball, and it is frequently the opposite of what the raw
time-bomb count suggests: `browserify-sign` resolves 56 packages with 27 time
bombs and needs **zero** new forks, because every one of those bombs is already
in our queue or already covered by a fork we publish.

Nothing here re-derives anything. It reads an `intake` report — same resolver,
same classifier, same coverage join — and reframes it, then writes the prompt
that turns the decision into work.
"""
from __future__ import annotations

import urllib.parse

from . import intake

#: `claude-cli://open?q=` is capped by the handler, and exceeding it does not
#: truncate cleanly. Rather than squeezing the instructions to fit, the link
#: *names* them: the full text is published as `onboard.md` and the prompt is a
#: short pointer at it. That removes the ceiling and buys three things a
#: URL-embedded prompt cannot have — the text is versioned with the build, it
#: can be read before anyone clicks, and correcting it does not invalidate links
#: already pasted elsewhere. The limit is still checked, because a pointer that
#: silently exceeded it would fail the same way.
DEEP_LINK_LIMIT = 5000

#: Where the published instructions live. Kept in step with how the dashboard
#: publishes reports (`cp -r .data/reports public/intake`).
SITE = "https://unabandoned.github.io/recon"

#: Repositories any fork onboarding needs open, whatever the package is.
CORE_REPOS = ("unabandoned/.github", "unabandoned/renovate-config")


def build(report: dict, *, org: str = "unabandoned", site: str = SITE) -> dict:
    """Turn an intake report into an adoption scenario. Pure."""
    meta = report.get("meta") or {}
    package = meta.get("package") or meta.get("spec") or ""
    plan = report.get("plan") or []
    packages = report.get("packages") or []
    totals = report.get("totals") or {}
    known = "covered" in totals          # the coverage join succeeded

    by_action = {a: [s for s in plan if s.get("action") == a]
                 for a in (intake.FORK, intake.ALIAS, intake.QUEUED)}
    unknown_action = [s for s in plan if s.get("action") is None]

    inert = [p["name"] for p in packages if p["state"] == "inert"]

    # Every package in this tree that we already publish — not just the ones the
    # plan happens to rank. The plan lists actionable *dominators*, so a sibling
    # we publish is missing from it whenever it is healthy (nothing to fix) or
    # dominated by something else (fixing that clears it). Both are still worth
    # wiring, and both are things you would otherwise pull from upstream while
    # maintaining your own copy of the same package.
    wire = sorted(
        ({"package": p["name"], "fork": p.get("covered_by")}
         for p in packages if p.get("covered") and p.get("covered_by")),
        key=lambda r: r["package"],
    )
    aliases = sorted({r["fork"] for r in wire})

    surface = {
        # Everything you become responsible for by publishing this fork.
        "packages_owned": totals.get("packages", 0),
        "time_bombs": totals.get("time_bomb", 0),
        "unknown": totals.get("unknown", 0),
        "emergencies": totals.get("emergencies", 0),
        "inert_left_alone": len(inert),
        "interventions": len(plan),
    }
    if known:
        surface.update({
            "new_forks": len(by_action[intake.FORK]),
            "aliases": len(by_action[intake.ALIAS]),
            "already_queued": len(by_action[intake.QUEUED]),
            "already_covered": totals.get("covered", 0),
        })

    attach = _attach_list(org, package, aliases, report)
    full_prompt = _prompt(org, package, report, by_action, wire, attach, known)
    onboard_url = _onboard_url(site, meta.get("spec") or package)
    compact = _compact_prompt(org, package, by_action, wire, known, onboard_url, attach)

    return {
        "package": package,
        "spec": meta.get("spec", package),
        "resolved": (report.get("tree") or {}).get("resolved", False),
        "surface": surface,
        # The only genuinely new obligations. Named, because "3 new forks" is
        # not a plan and "fork elliptic, asn1.js and md5.js" is.
        "new_forks": [s["package"] for s in by_action[intake.FORK]],
        "aliases": wire,
        "already_queued": [s["package"] for s in by_action[intake.QUEUED]],
        "unclassified": [s["package"] for s in unknown_action],
        "inert": sorted(inert),
        "attach": attach,
        "prompt": full_prompt,
        "compact_prompt": compact,
        "onboard_url": onboard_url,
        "deep_link": deep_link(org, package, compact),
        "coverage_known": known,
    }


def _attach_list(org: str, package: str, aliases: list[str], report: dict) -> list[str]:
    """Which repositories the onboarding session needs open.

    The fork itself, the two that every fork's CI and Renovate config come from,
    and one per sibling this fork will alias — because wiring an alias means
    reading what that sibling actually publishes, not guessing its version range.
    """
    repos = [f"{org}/{package}"] + list(CORE_REPOS)
    scope = "@" + org + "/"
    for fork in aliases:
        name = fork[len(scope):] if fork.startswith(scope) else fork
        slug = f"{org}/{name}"
        if slug not in repos:
            repos.append(slug)
    return repos


def _prompt(org, package, report, by_action, wire, attach, known) -> str:
    """The onboarding prompt. Specific enough to act on, honest about what it assumes."""
    scope = f"@{org}/{package}"
    lines = [
        f"Onboard the freshly forked `{org}/{package}` into the `{org}` org — where the "
        f"abandoned dependencies our projects pull in are parked and kept current.",
        "",
        "Context from the adoption audit that produced this prompt:",
        f"- The published tree resolves {report['totals'].get('packages', 0)} packages, "
        f"{report['totals'].get('time_bomb', 0)} of them abandoned-and-carrying-dependencies.",
    ]
    if known:
        n_wire = len(wire)
        lines.append(
            f"- {len(by_action[intake.FORK])} of those need a NEW fork; "
            f"{len(by_action[intake.QUEUED])} are already ranked in our work queue; "
            f"{n_wire} {'is' if n_wire == 1 else 'are'} already published by us and "
            f"should be aliased rather than pulled from upstream."
        )
    else:
        lines.append(
            "- Coverage against our existing forks could NOT be determined for this "
            "audit, so treat every recommendation below as unverified."
        )

    lines += ["", "Do the following, and read each fork's CLAUDE.md before changing anything:", ""]
    n = 1
    lines += [
        f"{n}. Add `.unabandoned.yml` from `{org}/.github/templates/.unabandoned.yml`. "
        "Fill in `upstream`, `summary`, `why-forked` and `used-by` truthfully — it is the "
        "single source of truth the dashboard reads, and CI validates it.",
    ]
    n += 1
    lines += [
        f"{n}. Add `renovate.json` containing exactly "
        f'`{{ "extends": ["github>{org}/renovate-config"], "forkProcessing": "enabled" }}`. '
        "`forkProcessing` must be in the fork's own root config; the preset-inherited "
        "value is ignored for the fork-skip decision.",
    ]
    n += 1
    lines += [
        f"{n}. Add the thin workflow callers that delegate to "
        f"`{org}/.github/.github/workflows/reusable-*.yml`, pinning the `uses:` ref and "
        "passing this fork's Node matrix, default branch, and whether a build runs. "
        "OIDC needs `permissions: id-token: write` on the CALLING job, and creds pass "
        "with `secrets: inherit`.",
    ]
    n += 1

    if wire:
        wiring = ", ".join(f"`{r['package']}` -> `{r['fork']}`" for r in wire)
        lines += [
            f"{n}. Wire the siblings we already maintain, as npm aliases in "
            f"`package.json`: {wiring}. The scope goes in the VALUE, not the key — "
            f'`"readable-stream": "npm:@{org}/readable-stream@^4"` — with a range that '
            "matches what that fork actually publishes. Check it; do not guess.",
        ]
        n += 1

    if by_action[intake.FORK]:
        lines += [
            f"{n}. These are abandoned, carry their own dependencies, and are NOT covered "
            f"by anything we publish: {', '.join(s['package'] for s in by_action[intake.FORK])}. "
            "Each one is another fork parked in this org, or a documented "
            "decision to replace or vendor it. Do not silence them.",
        ]
        n += 1
    elif not known:
        # With no inventory every action is unclassified, so `no FORK entries`
        # means "we could not tell", not "there are none". Saying the second is
        # the same mistake as reporting 0-covered for an unreadable inventory,
        # and it is worse here because it lands in a prompt someone will act on.
        lines += [
            f"{n}. How many NEW forks this needs could NOT be determined — the audit "
            "could not read our fork inventory. Do not treat that as zero. Re-run the "
            "audit against a good inventory before deciding anything about coverage.",
        ]
        n += 1
    else:
        lines += [
            f"{n}. No new forks are required: every abandoned-and-rotting package beneath "
            "this one is already published by us or already ranked in our work queue. "
            "Confirm that against the dashboard before relying on it.",
        ]
        n += 1

    lines += [
        f"{n}. Do NOT pin to dodge a breaking major. The org's position is fix "
        "forward — adopt the new major and repair the real breakages.",
        "",
        "Two steps cannot be automated and are not yours to do: installing the Renovate "
        "app, and `npm trust` to configure the trusted publisher (both are 2FA-gated).",
        "",
        "Open a pull request per logical change; the title is the Conventional Commit "
        "that release-please reads, because every PR is squash-merged.",
    ]
    return "\n".join(lines)


def _onboard_url(site: str, spec: str) -> str:
    """Where the full instructions are published, matching `intake.report_path`."""
    return f"{site.rstrip('/')}/intake/{spec.replace('/', '%2F')}/onboard.md"


def _compact_prompt(org, package, by_action, wire, known, onboard_url, attach) -> str:
    """A pointer, plus enough to be useful if the fetch fails.

    It would be shorter to say only "read this URL", and worse. A prompt whose
    entire content is a fetch has nothing to fall back on when the fetch fails,
    and the reader — who is meant to check it before pressing Enter — cannot
    tell what they are agreeing to. So the shape of the work and the two rules
    that are never negotiable travel in the prompt itself; the detail is behind
    the link.
    """
    bits = [
        f"Onboard the forked `{org}/{package}` into the `{org}` org.",
        f"Read {onboard_url} first and follow it — it is this org's own generated "
        f"instructions, and it carries the full checklist and the current audit.",
        "Attach these repositories: " + ", ".join(attach) + ".",
    ]
    if wire:
        bits.append(
            "Wire these siblings we already publish as npm aliases (the scope goes in "
            "the VALUE, not the key): "
            + ", ".join(f"{r['package']} -> {r['fork']}" for r in wire) + "."
        )
    if by_action[intake.FORK]:
        bits.append(
            "Nothing we publish covers these, so each needs a fork/replace/vendor "
            "decision: " + ", ".join(s["package"] for s in by_action[intake.FORK]) + "."
        )
    elif known:
        bits.append(
            "No new forks needed — everything rotten beneath it is already covered by "
            "a fork we publish or already ranked in our work queue."
        )
    else:
        bits.append(
            "How many new forks this needs could NOT be determined: the audit could "
            "not read our fork inventory. Do not treat that as zero."
        )
    bits.append("Read the repo's CLAUDE.md before changing anything. Fix forward, do "
                "not pin.")
    return " ".join(bits)


def onboard_document(report: dict, scenario: dict, *, org: str = "unabandoned") -> str:
    """The hosted instructions the deep link points at.

    Markdown rather than plain text so it reads as a document to a human doing
    the review the deep link asks for, and carries the audit that produced it —
    an instruction sheet with no evidence behind it is one nobody can check.
    """
    meta = report.get("meta") or {}
    surf = scenario["surface"]
    lines = [
        f"# Onboarding `{org}/{scenario['package']}`",
        "",
        f"> Generated by recon from the adoption audit of `{meta.get('spec', '')}` "
        f"on {meta.get('audited_at', meta.get('audited_date', ''))}. Never "
        f"hand-edited. If this disagrees with the repository's own `CLAUDE.md`, "
        f"the repository wins.",
        "",
        "## What adopting this commits us to",
        "",
        f"- **{surf['packages_owned']}** packages become ours to keep current.",
        f"- **{surf['time_bombs']}** of them are abandoned *and* carry their own "
        f"dependencies.",
        f"- **{surf['inert_left_alone']}** are abandoned with nothing beneath them "
        f"to rot — leave those alone.",
    ]
    if scenario["coverage_known"]:
        lines += [
            f"- **{surf['new_forks']}** need a NEW fork; **{surf['already_queued']}** "
            f"are already ranked in our work queue and are not new obligations.",
        ]
    else:
        lines += [
            "- How much of this we already cover could **not** be determined for this "
            "audit — the fork inventory could not be read. Do not read that as zero.",
        ]
    lines += ["", "## Steps", "", scenario["prompt"], ""]

    if scenario["new_forks"]:
        lines += [
            "## Packages that would need their own fork",
            "",
            "Abandoned, carrying dependencies, and not covered by anything we publish.",
            "",
        ] + [f"- `{n}`" for n in scenario["new_forks"]] + [""]
    if scenario["already_queued"]:
        lines += [
            "## Already our problem",
            "",
            "Ranked in the work queue regardless of this decision, so adopting this "
            "does not add them.",
            "",
        ] + [f"- `{n}`" for n in scenario["already_queued"]] + [""]
    return "\n".join(lines)


def deep_link(org: str, package: str, prompt: str) -> dict:
    """A `claude-cli://open` link, and an honest note when it will not fit.

    The handler caps `q`, and over-long values do not degrade gracefully, so the
    length is checked rather than hoped for. `claude-cli://` also cannot be
    linked from GitHub-rendered Markdown — GitHub strips the scheme — but the
    dashboard is an HTML page, which is exactly where this works.
    """
    query = urllib.parse.urlencode({"repo": f"{org}/{package}", "q": prompt})
    url = f"claude-cli://open?{query}"
    return {
        "url": url if len(prompt) <= DEEP_LINK_LIMIT else "",
        "length": len(prompt),
        "limit": DEEP_LINK_LIMIT,
        "fits": len(prompt) <= DEEP_LINK_LIMIT,
    }
