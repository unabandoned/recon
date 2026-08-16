"""HTTP reads that return `Fact`s, and a ledger of every attempt.

Two jobs, and the second one is the interesting one:

1. Turn a URL into a `Fact` — never into `None`, never into `{}`.
2. Remember every attempt, so the coverage ledger can say "147 packages
   resolved, 3 registry reads failed" instead of quietly reporting 144.

The ledger is the difference between a build that had a bad day and a build
that *knows* it had a bad day. `Session.ledger` is the raw material for the
`coverage.fetches` block and for the M1 integrity check.

Everything is injectable (`opener`, `clock`) so the whole pipeline can be run
offline against recorded fixtures — which is what makes the reproducibility
requirement (§12.4 of the design) testable rather than aspirational.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from .facts import Fact

USER_AGENT = "unabandoned-recon"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 2
RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class Attempt:
    """One recorded fetch, successful or not."""

    url: str
    status: str          # "ok" | "failed"
    code: int | None     # HTTP status where there was one
    detail: str
    at: str


def _redact(url: str) -> str:
    """Strip any query-string credentials before a URL enters the record.

    Nothing here builds authenticated query strings today, but the ledger is
    published to a public page, so this is a guard against a future caller
    putting a token in a URL and the ledger cheerfully committing it.
    """
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in {"token", "access_token", "api_key", "key", "secret"}
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), "")
    )


class Session:
    """A JSON-over-HTTP reader that records what it did."""

    def __init__(
        self,
        *,
        clock: Callable[[], str],
        opener: Callable[[urllib.request.Request, int], bytes] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._open = opener or _urlopen
        self._timeout = timeout
        self._retries = retries
        self._sleep = sleep
        self.ledger: list[Attempt] = []

    # -- core ---------------------------------------------------------------
    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        absent_is_ok: bool = False,
    ) -> Fact:
        """GET and parse JSON.

        `absent_is_ok` turns a 404 into `Fact.ok(None)` — the right shape for
        "this repo has no `.unabandoned.yml`", which is a real answer rather
        than a failed read. Every other non-2xx is a failure and says so.
        """
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)

        recorded = _redact(url)
        last = "unattempted"
        code: int | None = None

        for attempt in range(self._retries + 1):
            at = self._clock()
            try:
                raw = self._open(req, self._timeout)
            except urllib.error.HTTPError as exc:
                code = exc.code
                if exc.code == 404 and absent_is_ok:
                    self._record(recorded, "ok", 404, "absent", at)
                    return Fact.ok(None, source=recorded, at=at)
                last = f"HTTP {exc.code}"
                if exc.code in RETRY_STATUS and attempt < self._retries:
                    self._sleep(2 ** attempt)
                    continue
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = f"{type(exc).__name__}: {exc}"[:200]
                if attempt < self._retries:
                    self._sleep(2 ** attempt)
                    continue
            else:
                try:
                    doc = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    # Reached the server, got something unparseable. Not a
                    # transport problem and not worth retrying, but emphatically
                    # not a successful read either.
                    self._record(recorded, "failed", code, f"malformed JSON: {exc}"[:200], at)
                    return Fact.failed(f"malformed JSON: {exc}"[:200], source=recorded, at=at)
                self._record(recorded, "ok", 200, "", at)
                return Fact.ok(doc, source=recorded, at=at)
            break

        at = self._clock()
        self._record(recorded, "failed", code, last, at)
        return Fact.failed(last, source=recorded, at=at)

    def post_json(
        self, url: str, body: Any, *, headers: dict[str, str] | None = None
    ) -> Fact:
        """POST JSON, read JSON. Used only for OSV's batch advisory query."""
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)

        at = self._clock()
        try:
            raw = self._open(req, self._timeout)
            doc = json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError,
                UnicodeDecodeError, json.JSONDecodeError) as exc:
            detail = f"{type(exc).__name__}: {exc}"[:200]
            self._record(_redact(url), "failed", getattr(exc, "code", None), detail, at)
            return Fact.failed(detail, source=_redact(url), at=at)
        self._record(_redact(url), "ok", 200, "", at)
        return Fact.ok(doc, source=_redact(url), at=at)

    # -- ledger -------------------------------------------------------------
    def _record(self, url: str, status: str, code: int | None, detail: str, at: str) -> None:
        self.ledger.append(Attempt(url, status, code, detail, at))

    @property
    def failures(self) -> list[Attempt]:
        return [a for a in self.ledger if a.status != "ok"]

    def summary(self) -> dict:
        """The `coverage.fetches` block: attempted, failed, and every failure named."""
        return {
            "attempted": len(self.ledger),
            "failed": len(self.failures),
            "failures": sorted(
                (
                    {"url": a.url, "code": a.code, "detail": a.detail}
                    for a in self.failures
                ),
                key=lambda d: (d["url"], d["detail"]),
            ),
        }


def _urlopen(req: urllib.request.Request, timeout: int) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()
