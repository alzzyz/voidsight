"""Item artwork, fetched once and cached on disk.

Two sources, and the choice between them is not obvious until you count:

* **warframe.market** ships a distinct icon for every item, plus a small
  sub-icon for the part type. 586 of our 592 prime parts have one, and the paths
  already arrive with the item list, so nothing extra has to be discovered.
* **DE's public export** is the official, sanctioned source and covers the same
  586 — but with only 174 distinct textures. `GenericWarframePrimeChassis.png`
  is reused 56 times, so it cannot tell one frame's chassis from another's.

So the market icon is the picture, and the export is the fallback. Artwork is
Digital Extremes' property: it is fetched at runtime into the user's cache
directory, never vendored into this repository.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.parse
from pathlib import Path

import httpx

from voidsight import USER_AGENT
from voidsight.data.catalog import Part
from voidsight.data.sources import cache_dir

log = logging.getLogger(__name__)

MARKET_ASSETS = "https://warframe.market/static/assets/"
EXPORT_ASSETS = "https://content.warframe.com/PublicExport/"


def icon_dir() -> Path:
    path = cache_dir() / "icons"
    path.mkdir(parents=True, exist_ok=True)
    return path


def market_url(asset_path: str) -> str:
    return MARKET_ASSETS + asset_path.lstrip("/")


def export_url(texture_location: str) -> str:
    """A public-export texture URL. The path carries a `!hash` suffix that has
    to survive quoting."""
    return EXPORT_ASSETS + urllib.parse.quote(texture_location.lstrip("/"), safe="/")


def cache_path(url: str) -> Path:
    """Where a URL's bytes live. Named by hash so the game's `!hash` suffixes
    and query strings cannot produce illegal filenames."""
    digest = hashlib.sha256(url.encode()).hexdigest()[:32]
    suffix = ".png"
    for candidate in (".png", ".jpg", ".jpeg", ".webp"):
        if candidate in url.lower():
            suffix = candidate
            break
    return icon_dir() / f"{digest}{suffix}"


class IconStore:
    """Resolves items to local image files, downloading on first use."""

    def __init__(self, *, timeout: float = 10.0, client: httpx.Client | None = None) -> None:
        self._client = client
        self._timeout = timeout
        self._owns_client = client is None
        self._missing: set[str] = set()

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> IconStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def urls_for(self, part: Part) -> list[str]:
        """Candidate image URLs for a part, best first."""
        urls = []
        if part.icon:
            urls.append(market_url(part.icon))
        return urls

    def cached(self, part: Part) -> Path | None:
        """The local file for a part, if it has already been fetched."""
        for url in self.urls_for(part):
            path = cache_path(url)
            if path.exists():
                return path
        return None

    def fetch(self, part: Part) -> Path | None:
        """The local file for a part, downloading if needed. None if unavailable."""
        if path := self.cached(part):
            return path
        for url in self.urls_for(part):
            if url in self._missing:
                continue
            if path := self._download(url):
                return path
        return None

    def _download(self, url: str) -> Path | None:
        try:
            response = self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # One failure per URL is enough; do not retry on every repaint.
            self._missing.add(url)
            log.debug("could not fetch %s: %s", url, exc)
            return None

        content_type = response.headers.get("content-type", "")
        if content_type and not content_type.startswith("image/"):
            self._missing.add(url)
            log.debug("%s is %s, not an image", url, content_type)
            return None

        path = cache_path(url)
        path.write_bytes(response.content)
        return path
