"""Advisory join against OSV — the escalation tier, not a new category.

Abandoned, carrying dependencies, *and* concretely vulnerable with nobody left
to respond is the exact scenario this org exists for. Until now it was invisible
here: the audit knew a package was a time bomb and knew nothing about whether it
was already going off.

OSV's batch endpoint takes every `(name, version)` pair in one or two requests,
needs no API key and no secret, and is the cheapest large win available. The
classification does not gain a fourth state — a vulnerable time bomb is still a
time bomb — it gains a tier above every other queue entry.

M1 applies here with particular force. A failed OSV read must render as
"advisory status unknown", never as "no advisories". A silent zero here is worse
than a silent zero anywhere else in this tool, because "no known CVEs" is
exactly the sentence someone will quote when deciding whether to ship.
"""
from __future__ import annotations

from .facts import Fact
from .http import Session

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
ECOSYSTEM = "npm"
BATCH_LIMIT = 500


def _severity_of(vuln: dict) -> str:
    """The advisory's severity word, preferring the ecosystem-specific rating."""
    db = vuln.get("database_specific") or {}
    if isinstance(db, dict) and db.get("severity"):
        return str(db["severity"]).upper()
    for entry in vuln.get("severity") or []:
        if isinstance(entry, dict) and entry.get("type") == "CVSS_V3":
            return "SCORED"
    return ""


def query(session: Session, idents: list[tuple[str, str]]) -> Fact:
    """Look up advisories for `(name, version)` pairs.

    Returns a Fact wrapping {"name@version": [advisory, ...]}. Partial failure
    is still failure: if any batch fails, the whole join is failed, because a
    partial advisory map rendered as complete is the silent-zero trap this
    module is here to avoid.
    """
    pairs = sorted({(n, v) for n, v in idents if n and v})
    if not pairs:
        return Fact.ok({}, source=OSV_BATCH)

    found: dict[str, list[dict]] = {}
    ids: set[str] = set()

    for start in range(0, len(pairs), BATCH_LIMIT):
        chunk = pairs[start:start + BATCH_LIMIT]
        body = {
            "queries": [
                {"package": {"name": name, "ecosystem": ECOSYSTEM}, "version": version}
                for name, version in chunk
            ]
        }
        batch = session.post_json(OSV_BATCH, body)
        if not batch.is_ok:
            return Fact.failed(
                f"advisory batch {start // BATCH_LIMIT + 1} failed: {batch.detail}",
                source=OSV_BATCH,
                at=batch.fetched_at,
            )

        results = (batch.payload or {}).get("results")
        if not isinstance(results, list) or len(results) != len(chunk):
            return Fact.failed(
                f"advisory batch returned {len(results) if isinstance(results, list) else '?'} "
                f"results for {len(chunk)} queries",
                source=OSV_BATCH,
                at=batch.fetched_at,
            )

        for (name, version), result in zip(chunk, results):
            vulns = (result or {}).get("vulns") or []
            if not vulns:
                continue
            ident = f"{name}@{version}"
            for vuln in vulns:
                vid = vuln.get("id")
                if not vid:
                    continue
                ids.add(vid)
                found.setdefault(ident, []).append({"id": vid, "severity": "", "summary": ""})

    # The batch endpoint returns ids only. Detail is a second, small round of
    # reads — only for advisories actually present, usually a handful.
    details: dict[str, dict] = {}
    for vid in sorted(ids):
        detail = session.get_json(OSV_VULN + vid)
        if not detail.is_ok:
            return Fact.failed(
                f"advisory {vid} detail unavailable: {detail.detail}",
                source=OSV_VULN + vid,
                at=detail.fetched_at,
            )
        doc = detail.payload or {}
        details[vid] = {
            "id": vid,
            "severity": _severity_of(doc),
            "summary": (doc.get("summary") or "")[:200],
            "aliases": sorted(doc.get("aliases") or [])[:6],
        }

    for ident, advs in found.items():
        found[ident] = sorted(
            (details.get(a["id"], a) for a in advs), key=lambda a: a["id"]
        )

    return Fact.ok(found, source=OSV_BATCH)
