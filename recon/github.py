"""GitHub reads: discovery, per-fork live state, and honest exclusions.

Discovery answers one question — which repos in the org are forks we maintain —
and it answers it the same way as before: a repo is a fork iff it carries a
valid `.unabandoned.yml`. What changes here is what happens to the ones that
are not. A repo that looks like a fork but has no metadata, or has metadata that
fails the schema, used to be skipped with a line on stderr nobody reads. Now it
is an **excluded** repo with a reason, counted in the ledger, rendered on the
coverage page, and part of the conservation invariant.

That is the direct fix for a repo sitting in the org with no metadata and
nothing published, which was simply absent from the dashboard — indistinguishable
from not existing.

The issue count is the other thing worth reading closely. Renovate's "Dependency
Dashboard" is a control surface, not work: always open, present on every fork,
and already linked from the card as its own fact. Counting it as an issue put a
permanent floor of one under every repo and made the org-wide total almost
entirely noise. It is excluded here, matched on the bot author *and* the title
so a human-filed issue that happens to share the name still counts — and the
count of what was excluded is reported, so the filter can never quietly grow
into hiding real work.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

from .facts import Fact
from .http import Session
from . import metadata as md

API = "https://api.github.com"
RENOVATE_LOGINS = {"renovate[bot]", "renovate-bot", "renovate"}
DEPENDENCY_DASHBOARD = "dependency dashboard"

BAD_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
GOOD_CONCLUSIONS = {"success", "neutral", "skipped", None}


@dataclass(slots=True)
class Fork:
    """One fork's editorial + live state. Every live datum is a Fact."""

    repo: str
    package: str
    html_url: str
    default_branch: str
    metadata: dict
    head_sha: Fact
    open_prs: Fact
    renovate_prs: Fact
    open_issues: Fact
    excluded_issues: Fact
    security: Fact
    autorelease_pending: Fact
    dependency_dashboard_url: Fact
    release: Fact
    manifest: Fact
    ci: Fact

    @property
    def expects_sibling(self) -> list[str]:
        return md.expected_siblings(self.metadata)


@dataclass(slots=True)
class Discovery:
    forks: list[Fork] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    discovered: int = 0


class GitHub:
    def __init__(self, session: Session, *, org: str, token: str = "") -> None:
        self._session = session
        self._org = org
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    # -- primitives ---------------------------------------------------------
    def get(self, path: str, *, params: dict | None = None, absent_is_ok: bool = False) -> Fact:
        url = path if path.startswith("http") else API + path
        return self._session.get_json(
            url, headers=self._headers, params=params, absent_is_ok=absent_is_ok
        )

    def paginated(self, path: str, params: dict | None = None, *, pages: int = 3) -> Fact:
        """Collect a list endpoint. A failed page fails the whole read.

        Deliberately all-or-nothing: a partial list rendered as complete is how
        an undercount looks confident.
        """
        params = dict(params or {})
        params.setdefault("per_page", 100)
        out: list = []
        source = path
        for page in range(1, pages + 1):
            fact = self.get(path, params={**params, "page": page})
            if not fact.is_ok:
                return fact
            batch = fact.payload or []
            if not isinstance(batch, list):
                return Fact.failed(f"expected a list from {path}", source=source)
            out.extend(batch)
            if len(batch) < params["per_page"]:
                break
        return Fact.ok(out, source=source)

    def _contents(self, repo: str, path: str) -> Fact:
        """A repo file's decoded text, or ok(None) when genuinely absent."""
        fact = self.get(
            f"/repos/{self._org}/{repo}/contents/{path}", absent_is_ok=True
        )
        if not fact.is_ok:
            return fact
        payload = fact.payload
        if payload is None:
            return Fact.ok(None, source=fact.source, at=fact.fetched_at)
        if not isinstance(payload, dict) or "content" not in payload:
            return Fact.failed(f"{path}: unexpected contents payload",
                               source=fact.source, at=fact.fetched_at)
        return fact.map(lambda p: base64.b64decode(p["content"]).decode("utf-8"))

    # -- discovery ----------------------------------------------------------
    def discover(self) -> tuple[Discovery, Fact]:
        """Every fork in the org, plus every repo deliberately left out and why."""
        repos = self.paginated(f"/orgs/{self._org}/repos", {"type": "public"})
        if not repos.is_ok:
            return Discovery(), repos

        result = Discovery()
        for repo_obj in repos.payload:
            name = repo_obj.get("name")
            if not name:
                continue
            if repo_obj.get("archived"):
                continue  # archived repos are not part of the program's surface
            result.discovered += 1

            text = self._contents(name, md.FILENAME)
            if not text.is_ok:
                result.excluded.append({
                    "repo": name, "reason": "metadata-unreadable",
                    "detail": text.detail,
                })
                continue
            if text.payload is None:
                # No metadata at all. For most repos this is correct — infra repos
                # are not forks. But it is recorded either way, because the case
                # that matters (a real fork nobody has onboarded) is
                # indistinguishable from the case that does not, and silently
                # dropping both is how one of them stayed invisible.
                result.excluded.append({
                    "repo": name, "reason": "no-metadata",
                    "detail": f"no {md.FILENAME} on the default branch",
                })
                continue

            try:
                data = md.load(text.payload)
            except Exception as exc:  # noqa: BLE001 - malformed YAML is a schema failure
                result.excluded.append({
                    "repo": name, "reason": "metadata-invalid",
                    "detail": f"could not parse: {exc}"[:200],
                })
                continue

            errors = md.validate(data)
            if errors:
                result.excluded.append({
                    "repo": name, "reason": "metadata-invalid",
                    "detail": "; ".join(errors)[:300],
                })
                continue

            result.forks.append(self.gather(repo_obj, data))

        result.forks.sort(key=lambda f: f.package.lower())
        result.excluded.sort(key=lambda e: e["repo"])
        return result, Fact.ok(len(result.forks), source=f"/orgs/{self._org}/repos")

    # -- per fork -----------------------------------------------------------
    def gather(self, repo_obj: dict, data: dict) -> Fork:
        repo = repo_obj["name"]
        org = self._org
        branch = repo_obj.get("default_branch") or "master"

        prs = self.paginated(f"/repos/{org}/{repo}/pulls", {"state": "open"})
        issues_raw = self.paginated(f"/repos/{org}/{repo}/issues", {"state": "open"})

        open_prs = prs.map(len)
        renovate_prs = prs.map(
            lambda items: sum(1 for p in items if _login(p) in RENOVATE_LOGINS)
        )
        autorelease = prs.map(
            lambda items: any(_has_label(p, "autorelease: pending") for p in items)
        )
        security_prs = prs.map(lambda items: sum(1 for p in items if _has_label(p, "security")))

        # /issues includes pull requests; filter them out before anything else.
        real_issues = issues_raw.map(
            lambda items: [i for i in items if "pull_request" not in i]
        )
        dashboard = real_issues.map(_find_dependency_dashboard)
        open_issues = real_issues.map(
            lambda items: sum(1 for i in items if not _is_dependency_dashboard(i))
        )
        excluded_issues = real_issues.map(
            lambda items: sum(1 for i in items if _is_dependency_dashboard(i))
        )
        security_issues = real_issues.map(
            lambda items: sum(
                1 for i in items
                if _has_label(i, "security") and not _is_dependency_dashboard(i)
            )
        )

        security = Fact.ok(
            security_prs.or_else(0) + security_issues.or_else(0),
            source="derived: security-labelled PRs + issues",
        ) if (security_prs.is_ok and security_issues.is_ok) else Fact.failed(
            "security count needs both PR and issue reads",
            source="derived: security-labelled PRs + issues",
        )

        release = self.get(f"/repos/{org}/{repo}/releases/latest", absent_is_ok=True)
        manifest = self._contents(repo, "package.json").map(
            lambda text: json.loads(text) if text is not None else None
        )
        head = self.get(f"/repos/{org}/{repo}/commits/{branch}").map(
            lambda c: c.get("sha") if isinstance(c, dict) else None
        )

        return Fork(
            repo=repo,
            package=data.get("package", repo),
            html_url=repo_obj.get("html_url", f"https://github.com/{org}/{repo}"),
            default_branch=branch,
            metadata=data,
            head_sha=head,
            open_prs=open_prs,
            renovate_prs=renovate_prs,
            open_issues=open_issues,
            excluded_issues=excluded_issues,
            security=security,
            autorelease_pending=autorelease,
            dependency_dashboard_url=dashboard,
            release=release,
            manifest=manifest,
            ci=self.ci_state(repo, branch),
        )

    def ci_state(self, repo: str, ref: str) -> Fact:
        """Aggregate check-runs on the default-branch head into one word.

        "No check runs" is `Fact.ok("none")` — a real answer about a repo with no
        CI — while a failed read is a failed read. Previously both were the
        string "unknown", which made a broken API call look like a policy choice.
        """
        fact = self.get(f"/repos/{self._org}/{repo}/commits/{ref}/check-runs")
        if not fact.is_ok:
            return fact

        def summarise(payload: object) -> str:
            runs = (payload or {}).get("check_runs") if isinstance(payload, dict) else None
            if runs is None:
                raise ValueError("no check_runs in payload")
            if not runs:
                return "none"
            if any(r.get("status") != "completed" for r in runs):
                return "pending"
            if any(r.get("conclusion") in BAD_CONCLUSIONS for r in runs):
                return "failing"
            if all(r.get("conclusion") in GOOD_CONCLUSIONS for r in runs):
                return "passing"
            return "mixed"

        return fact.map(summarise)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _login(item: dict) -> str:
    return ((item or {}).get("user") or {}).get("login") or ""


def _has_label(item: dict, name: str) -> bool:
    return any(
        (lbl.get("name", "").lower() == name.lower())
        for lbl in (item or {}).get("labels", [])
    )


def _is_dependency_dashboard(issue: dict) -> bool:
    """Renovate's control-surface issue, matched on title *and* bot author."""
    return (
        (issue.get("title") or "").strip().lower() == DEPENDENCY_DASHBOARD
        and _login(issue) in RENOVATE_LOGINS
    )


def _find_dependency_dashboard(issues: list) -> str | None:
    for issue in issues:
        if _is_dependency_dashboard(issue):
            return issue.get("html_url")
    return None
