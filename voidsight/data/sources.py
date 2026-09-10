"""Cached fetchers for the three upstream data feeds.

All three are public and unauthenticated. Note that api.warframestat.us rejects
requests carrying a default HTTP-client User-Agent with 403, so every request
here goes out with our own.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from voidsight import USER_AGENT

log = logging.getLogger(__name__)

FILTERED_ITEMS_URL = "https://api.warframestat.us/wfinfo/filtered_items/"
WFINFO_PRICES_URL = "https://api.warframestat.us/wfinfo/prices/"
MARKET_ITEMS_URL = "https://api.warframe.market/v2/items"

DAY = 24 * 60 * 60
HOUR = 60 * 60


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    path = Path(base) / "voidsight"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Feed:
    """An upstream JSON document plus how long a local copy stays fresh."""

    name: str
    url: str
    ttl: int
    headers: dict[str, str] | None = None

    @property
    def path(self) -> Path:
        return cache_dir() / f"{self.name}.json"

    def age(self) -> float | None:
        try:
            return time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return None


FILTERED_ITEMS = Feed("filtered_items", FILTERED_ITEMS_URL, ttl=DAY)
PRICES = Feed("prices", WFINFO_PRICES_URL, ttl=HOUR)
MARKET_ITEMS = Feed(
    "market_items",
    MARKET_ITEMS_URL,
    ttl=DAY,
    headers={"Platform": "pc", "Language": "en"},
)

ALL_FEEDS = (FILTERED_ITEMS, PRICES, MARKET_ITEMS)


def load(feed: Feed, *, refresh: bool = False, offline: bool = False) -> Any:
    """Return a feed's payload, downloading it only when the local copy is stale.

    A stale copy is preferred over failing: if the network call errors out and we
    have anything cached, we use it and log a warning. Only a cold cache with no
    network raises.
    """
    age = feed.age()
    if not refresh and age is not None and (offline or age < feed.ttl):
        return _read(feed)
    if offline:
        raise FileNotFoundError(
            f"no cached copy of {feed.name} and offline mode requested; "
            f"run `voidsight update-data` while online"
        )

    try:
        payload = _download(feed)
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        if age is None:
            raise
        log.warning(
            "could not refresh %s (%s); using cached copy %.0fh old",
            feed.name,
            exc,
            age / HOUR,
        )
        return _read(feed)

    tmp = feed.path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(feed.path)
    return payload


def _download(feed: Feed) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    headers.update(feed.headers or {})
    log.debug("GET %s", feed.url)
    response = httpx.get(feed.url, headers=headers, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.json()


def _read(feed: Feed) -> Any:
    return json.loads(feed.path.read_text())


def refresh_all(*, refresh: bool = True) -> dict[str, Any]:
    return {feed.name: load(feed, refresh=refresh) for feed in ALL_FEEDS}
