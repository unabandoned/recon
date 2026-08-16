"""A small fixture org, served offline, exercising every shape that has bitten us.

    @unabandoned/browserify           reaches readable-stream ONLY through the
                                      org's own crypto-browserify fork
    @unabandoned/crypto-browserify    pulls hash-base -> readable-stream
    @unabandoned/buffer               wires @unabandoned/ieee754 via an ALIAS,
                                      so the scope lives in the value
    @unabandoned/detective            reached by browserify ONLY through the
                                      third-party `module-deps` — so it is not
                                      browserify's declared edge, and M2 must
                                      not treat it as one
    @unabandoned/ieee754              a clean leaf
    browserify-sign                   a repo with no metadata — must appear as a
                                      counted exclusion, not an absence
    infra                             genuinely not a fork

Plus one package (`through2`) whose packument read fails, so every build over
this world has an `unknown` in it and the M1 path is always exercised.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse

from recon.facts import Fact
from recon.resolve import parse_lockfile

ORG = "unabandoned"


def _yml(package, upstream, *, expects=None, used_by=None, status="active"):
    lines = [
        "schema: 1",
        f'package: "{package}"',
        "upstream:",
        f"  repo: {upstream}",
        '  reason: "no releases since 2019"',
        f'summary: "does a thing"',
        f'why-forked: "abandoned upstream with an outdated tree"',
        f"status: {status}",
    ]
    if expects:
        lines.append("expects-sibling:")
        lines += [f"  - {name}" for name in expects]
    if used_by:
        lines.append("used-by:")
        for consumer, purpose in used_by:
            lines += [f"  - consumer: {consumer}", f'    purpose: "{purpose}"']
    return "\n".join(lines) + "\n"


REPOS = [
    {"name": "browserify", "default_branch": "master", "archived": False,
     "html_url": f"https://github.com/{ORG}/browserify"},
    {"name": "crypto-browserify", "default_branch": "master", "archived": False,
     "html_url": f"https://github.com/{ORG}/crypto-browserify"},
    {"name": "buffer", "default_branch": "master", "archived": False,
     "html_url": f"https://github.com/{ORG}/buffer"},
    {"name": "ieee754", "default_branch": "master", "archived": False,
     "html_url": f"https://github.com/{ORG}/ieee754"},
    {"name": "detective", "default_branch": "master", "archived": False,
     "html_url": f"https://github.com/{ORG}/detective"},
    {"name": "browserify-sign", "default_branch": "master", "archived": False,
     "html_url": f"https://github.com/{ORG}/browserify-sign"},
    {"name": "infra", "default_branch": "main", "archived": False,
     "html_url": f"https://github.com/{ORG}/infra"},
    {"name": "old-thing", "default_branch": "master", "archived": True,
     "html_url": f"https://github.com/{ORG}/old-thing"},
]

METADATA = {
    "browserify": _yml("@unabandoned/browserify", "browserify/browserify",
                       expects=["crypto-browserify"],
                       used_by=[("some-app", "bundles the front end")]),
    "crypto-browserify": _yml("@unabandoned/crypto-browserify",
                              "crypto-browserify/crypto-browserify"),
    "buffer": _yml("@unabandoned/buffer", "feross/buffer", expects=["ieee754"]),
    "ieee754": _yml("@unabandoned/ieee754", "feross/ieee754"),
    "detective": _yml("@unabandoned/detective", "browserify/detective"),
}

MANIFESTS = {
    "browserify": {"name": "@unabandoned/browserify", "dependencies": {
        "crypto-browserify": "npm:@unabandoned/crypto-browserify@^3",
        # Third-party, and it is what drags @unabandoned/detective in. Browserify
        # declares no edge to detective, so M2 must not expect one.
        "module-deps": "^6.2.3",
    }},
    "crypto-browserify": {"name": "@unabandoned/crypto-browserify", "dependencies": {
        "hash-base": "^3.0.0", "through2": "^2.0.0",
    }},
    "buffer": {"name": "@unabandoned/buffer", "dependencies": {
        "ieee754": "npm:@unabandoned/ieee754@^1",   # scope in the VALUE
    }},
    "ieee754": {"name": "@unabandoned/ieee754", "dependencies": {}},
    "detective": {"name": "@unabandoned/detective", "dependencies": {}},
}

LOCKS = {
    "@unabandoned/browserify": {"packages": {
        "": {"dependencies": {"@unabandoned/browserify": "^17"}},
        "node_modules/@unabandoned/browserify": {
            "version": "17.0.1",
            "dependencies": {"crypto-browserify": "npm:@unabandoned/crypto-browserify@^3",
                             "module-deps": "^6.2.3"}},
        "node_modules/module-deps": {
            "version": "6.2.3",
            "dependencies": {"detective": "npm:@unabandoned/detective@^5"}},
        "node_modules/detective": {
            "name": "@unabandoned/detective", "version": "5.2.2"},
        "node_modules/crypto-browserify": {
            "name": "@unabandoned/crypto-browserify", "version": "3.12.1",
            "dependencies": {"hash-base": "^3.0.0", "through2": "^2.0.0"}},
        "node_modules/hash-base": {
            "version": "3.1.0", "dependencies": {"readable-stream": "^3.6.0"}},
        "node_modules/readable-stream": {
            "version": "3.6.2", "dependencies": {"inherits": "^2.0.3"}},
        "node_modules/inherits": {"version": "2.0.4"},
        "node_modules/through2": {
            "version": "2.0.5", "dependencies": {"readable-stream": "^2.3.5"}},
    }},
    "@unabandoned/crypto-browserify": {"packages": {
        "": {"dependencies": {"@unabandoned/crypto-browserify": "^3"}},
        "node_modules/@unabandoned/crypto-browserify": {
            "version": "3.12.1",
            "dependencies": {"hash-base": "^3.0.0", "through2": "^2.0.0"}},
        "node_modules/hash-base": {
            "version": "3.1.0", "dependencies": {"readable-stream": "^3.6.0"}},
        "node_modules/readable-stream": {
            "version": "3.6.2", "dependencies": {"inherits": "^2.0.3"}},
        "node_modules/inherits": {"version": "2.0.4"},
        "node_modules/through2": {
            "version": "2.0.5", "dependencies": {"readable-stream": "^2.3.5"}},
    }},
    "@unabandoned/buffer": {"packages": {
        "": {"dependencies": {"@unabandoned/buffer": "^6"}},
        "node_modules/@unabandoned/buffer": {
            "version": "6.0.4",
            "dependencies": {"ieee754": "npm:@unabandoned/ieee754@^1"}},
        "node_modules/ieee754": {
            "name": "@unabandoned/ieee754", "version": "1.2.2"},
    }},
    "@unabandoned/ieee754": {"packages": {
        "": {"dependencies": {"@unabandoned/ieee754": "^1"}},
        "node_modules/@unabandoned/ieee754": {"version": "1.2.2"},
    }},
    "@unabandoned/detective": {"packages": {
        "": {"dependencies": {"@unabandoned/detective": "^5"}},
        "node_modules/@unabandoned/detective": {"version": "5.2.2"},
    }},
}

# name -> (latest version, publish date, {version: [deps]})
PACKUMENTS = {
    "@unabandoned/browserify": ("17.0.1", "2026-07-01",
                               {"17.0.1": ["crypto-browserify", "module-deps"]}),
    "@unabandoned/crypto-browserify": ("3.12.1", "2026-07-02",
                                       {"3.12.1": ["hash-base", "through2"]}),
    "@unabandoned/buffer": ("6.0.4", "2026-06-20", {"6.0.4": ["ieee754"]}),
    "@unabandoned/ieee754": ("1.2.2", "2026-06-20", {"1.2.2": []}),
    "@unabandoned/detective": ("5.2.2", "2026-07-05", {"5.2.2": []}),
    "module-deps": ("6.2.3", "2019-05-01", {"6.2.3": ["detective"]}),
    "hash-base": ("3.1.0", "2020-01-20", {"3.1.0": ["readable-stream"]}),
    "readable-stream": ("3.6.2", "2022-06-01", {"3.6.2": ["inherits"]}),
    "inherits": ("2.0.4", "2019-01-01", {"2.0.4": []}),
    # through2 has no packument entry -> the fetch fails -> `unknown`.
}

ADVISORIES = {
    "readable-stream@3.6.2": [
        {"id": "GHSA-test-0001", "severity": "HIGH",
         "summary": "prototype pollution in readable-stream"},
    ],
}


class World:
    """A fake HTTP world. Also records how many times each URL was hit."""

    def __init__(self, *, fail_urls: set[str] | None = None):
        self.calls: list[str] = []
        self.fail_urls = fail_urls or set()

    # -- the opener the Session takes ---------------------------------------
    def opener(self, req, timeout):
        url = req.full_url
        self.calls.append(url)
        for fragment in self.fail_urls:
            if fragment in url:
                raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
        if req.get_method() == "POST":
            return self._osv(json.loads(req.data.decode("utf-8")))
        return self._get(url)

    # -- resolver the pipeline takes ----------------------------------------
    def resolver(self, package: str) -> Fact:
        lock = LOCKS.get(package)
        if lock is None:
            return Fact.failed(f"404 Not Found - {package} is not in the npm registry",
                               source=f"npm install {package}")
        return Fact.ok(parse_lockfile(lock, package), source=f"npm install {package}")

    # -- internals ----------------------------------------------------------
    def _get(self, url: str) -> bytes:
        parts = urllib.parse.urlsplit(url)
        path = parts.path

        if parts.netloc == "registry.npmjs.org":
            name = urllib.parse.unquote(path.lstrip("/"))
            entry = PACKUMENTS.get(name)
            if entry is None:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            latest, date, versions = entry
            return _json({
                "name": name,
                "dist-tags": {"latest": latest},
                "time": {latest: f"{date}T00:00:00.000Z"},
                "versions": {
                    v: {"version": v, "dependencies": {d: "*" for d in deps}}
                    for v, deps in versions.items()
                },
            })

        if parts.netloc == "api.osv.dev":
            vid = path.rsplit("/", 1)[-1]
            for advs in ADVISORIES.values():
                for adv in advs:
                    if adv["id"] == vid:
                        return _json({
                            "id": vid, "summary": adv["summary"],
                            "database_specific": {"severity": adv["severity"]},
                        })
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        if path == f"/orgs/{ORG}/repos":
            page = _page(parts.query)
            return _json(REPOS if page == 1 else [])

        segments = path.strip("/").split("/")
        if segments[:2] == ["repos", ORG]:
            repo = segments[2]
            rest = segments[3:]
            if rest[:1] == ["contents"]:
                filename = "/".join(rest[1:])
                if filename == ".unabandoned.yml":
                    text = METADATA.get(repo)
                elif filename == "package.json":
                    manifest = MANIFESTS.get(repo)
                    text = json.dumps(manifest) if manifest else None
                else:
                    text = None
                if text is None:
                    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
                return _json({
                    "name": filename,
                    "content": base64.b64encode(text.encode()).decode(),
                })
            if rest[:1] == ["pulls"]:
                page = _page(parts.query)
                return _json(_pulls(repo) if page == 1 else [])
            if rest[:1] == ["issues"]:
                page = _page(parts.query)
                return _json(_issues(repo) if page == 1 else [])
            if rest[:2] == ["releases", "latest"]:
                entry = PACKUMENTS.get(f"@unabandoned/{repo}")
                if not entry:
                    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
                return _json({"tag_name": f"v{entry[0]}",
                              "published_at": f"{entry[1]}T00:00:00Z"})
            if rest[:1] == ["commits"] and rest[-1] == "check-runs":
                return _json({"check_runs": [
                    {"status": "completed", "conclusion": "success", "name": "ci"},
                ]})
            if rest[:1] == ["commits"]:
                return _json({"sha": f"{repo}-sha-0000000000000000000000000000"})

        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    def _osv(self, body: dict) -> bytes:
        results = []
        for q in body.get("queries", []):
            ident = f'{q["package"]["name"]}@{q["version"]}'
            advs = ADVISORIES.get(ident)
            results.append({"vulns": [{"id": a["id"]} for a in advs]} if advs else {})
        return _json({"results": results})


def _pulls(repo: str) -> list:
    if repo == "browserify":
        return [
            {"number": 1, "user": {"login": "renovate[bot]"}, "labels": []},
            {"number": 2, "user": {"login": "a-person"},
             "labels": [{"name": "security"}]},
        ]
    if repo == "buffer":
        return [{"number": 3, "user": {"login": "renovate[bot]"}, "labels": []}]
    return []


def _issues(repo: str) -> list:
    """Every fork carries Renovate's dashboard; only browserify has real work."""
    dashboard = {
        "number": 100, "title": "Dependency Dashboard",
        "user": {"login": "renovate[bot]"}, "labels": [],
        "html_url": f"https://github.com/{ORG}/{repo}/issues/100",
    }
    if repo == "browserify":
        return [dashboard, {"number": 101, "title": "crash on empty input",
                            "user": {"login": "a-person"}, "labels": []}]
    return [dashboard]


def _page(query: str) -> int:
    return int(dict(urllib.parse.parse_qsl(query)).get("page", 1))


def _json(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")
