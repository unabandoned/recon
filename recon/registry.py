"""npm registry reads.

One fetch per package name — the full packument — memoised, and then *two*
independent facts read out of it:

    last_release(name)            when `latest` was published
    declared_deps(name, version)  that exact version's runtime dependencies

The second one is the point. The old audit derived a package's dependency count
from one place only: the lockfile entry npm wrote. The packument response
already carries `versions[<v>].dependencies` for every version, and was already
being fetched and thrown away. Reading it costs nothing extra and gives a
genuinely independent second derivation of the same fact — which is what makes
the M2 cross-check possible at all (see `integrity.dependency_counts_agree`).

Independent is doing real work in that sentence: the lockfile is produced by
npm's resolver from this same metadata, so the two can only disagree if our
reading of one of them is wrong. That is precisely the bug class we are hunting.
"""
from __future__ import annotations

import urllib.parse

from .facts import Fact
from .http import Session

REGISTRY = "https://registry.npmjs.org"


class Registry:
    """Memoised packument access over a `Session`."""

    def __init__(self, session: Session, *, base: str = REGISTRY) -> None:
        self._session = session
        self._base = base
        self._packuments: dict[str, Fact] = {}

    def packument(self, name: str) -> Fact:
        """The whole document for a package name, fetched at most once."""
        if name not in self._packuments:
            url = f"{self._base}/{urllib.parse.quote(name, safe='@')}"
            self._packuments[name] = self._session.get_json(url)
        return self._packuments[name]

    # -- derived facts ------------------------------------------------------
    def last_release(self, name: str) -> Fact:
        """Publish date (YYYY-MM-DD) of the current `latest`.

        A packument that parses but carries no usable date is a *failed* read of
        that date, not an absent one. The distinction is the whole ballgame: the
        old code returned `None` here and the classifier read `None` as "alive".
        """

        def extract(doc: object) -> str:
            if not isinstance(doc, dict):
                raise ValueError("packument is not an object")
            latest = ((doc.get("dist-tags") or {}) or {}).get("latest")
            if not latest:
                raise ValueError("packument has no dist-tags.latest")
            stamp = ((doc.get("time") or {}) or {}).get(latest)
            if not stamp or not isinstance(stamp, str):
                raise ValueError(f"packument has no time entry for {latest}")
            return stamp[:10]

        return self.packument(name).map(extract)

    def declared_deps(self, name: str, version: str | None) -> Fact:
        """`dependencies` of one published version, as declared by the registry.

        Returns a sorted list of dependency names. A version the packument does
        not carry is a failed read — not an empty dependency list, which is what
        would quietly turn a time bomb into an inert package.
        """
        if not version:
            return Fact.skipped("no resolved version", source=f"{self._base}/{name}")

        def extract(doc: object) -> list[str]:
            if not isinstance(doc, dict):
                raise ValueError("packument is not an object")
            versions = doc.get("versions") or {}
            manifest = versions.get(version)
            if manifest is None:
                raise ValueError(f"packument has no version {version}")
            return sorted((manifest.get("dependencies") or {}).keys())

        return self.packument(name).map(extract)

    def latest_version(self, name: str) -> Fact:
        """The `latest` dist-tag, for the published-vs-HEAD comparison."""

        def extract(doc: object) -> str:
            if not isinstance(doc, dict):
                raise ValueError("packument is not an object")
            latest = ((doc.get("dist-tags") or {}) or {}).get("latest")
            if not latest:
                raise ValueError("packument has no dist-tags.latest")
            return str(latest)

        return self.packument(name).map(extract)

    @property
    def names_seen(self) -> list[str]:
        return sorted(self._packuments)
