"""Live warframe.market prices for the rewards on screen.

What matters during a reward screen is what someone will actually pay right now,
so this asks for the current top orders from online sellers rather than a
rolling average. The average from the WFInfo feed is kept as a fallback and as
context — a part with a good price and no volume is not really worth that price.

The public API allows 3 requests a second, which a four-reward scan fits inside
comfortably; the limiter is there so that repeated scans and retries cannot
drift over it.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import httpx

from voidsight import USER_AGENT
from voidsight.data.catalog import Part

log = logging.getLogger(__name__)

BASE_URL = "https://api.warframe.market/v2"
#: Documented public limit.
REQUESTS_PER_SECOND = 3.0
#: Prices move slowly enough that re-asking within a mission is pointless.
CACHE_SECONDS = 60.0
#: Sellers in these states can be traded with now.
TRADEABLE_STATUS = frozenset({"ingame", "online"})


@dataclass(frozen=True)
class Offer:
    platinum: int
    quantity: int
    seller: str
    status: str


@dataclass
class Quote:
    """What a reward is worth, live if possible.

    `part` is None when OCR read a column but nothing in the catalog matched;
    the page still shows the slot, just without a price.
    """

    part: Part | None
    offers: list[Offer] = field(default_factory=list)
    live: bool = False
    error: str | None = None

    @property
    def lowest(self) -> int | None:
        """Cheapest live sell order — what you would pay to buy it now."""
        return self.offers[0].platinum if self.offers else None

    @property
    def platinum(self) -> float | None:
        """Best available price estimate, live if we have it."""
        if self.lowest is not None:
            return float(self.lowest)
        return self.part.avg_plat if self.part else None

    @property
    def ducats(self) -> int:
        return self.part.ducats if self.part else 0

    @property
    def ducats_per_platinum(self) -> float | None:
        """How good this is to sell for ducats instead of platinum."""
        price = self.platinum
        if not price:
            return None
        return self.ducats / price


class RateLimiter:
    """Token bucket, shared across threads."""

    def __init__(self, per_second: float = REQUESTS_PER_SECOND) -> None:
        self.interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.interval
        if delay:
            time.sleep(delay)


class MarketClient:
    """Fetches top orders, with rate limiting and a short cache."""

    def __init__(
        self,
        *,
        platform: str = "pc",
        crossplay: bool = True,
        cache_seconds: float = CACHE_SECONDS,
        timeout: float = 6.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Platform": platform,
            "Language": "en",
            "Crossplay": "true" if crossplay else "false",
        }
        self.cache_seconds = cache_seconds
        self.limiter = RateLimiter()
        self._client = client or httpx.Client(timeout=timeout, headers=self.headers)
        self._owns_client = client is None
        self._cache: dict[str, tuple[float, list[Offer]]] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> MarketClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def quote(self, part: Part) -> Quote:
        """Price one reward, falling back to its cached average."""
        if not part.tradeable:
            return Quote(part=part, live=False)
        if not part.slug:
            # Brand-new items are in the game before warframe.market lists them.
            return Quote(part=part, live=False, error="not listed on warframe.market")
        try:
            offers = self._top_offers(part.slug)
        except httpx.HTTPError as exc:
            log.warning("market lookup for %s failed: %s", part.slug, exc)
            return Quote(part=part, live=False, error=str(exc))
        return Quote(part=part, offers=offers, live=True)

    def quotes(self, parts: list[Part]) -> list[Quote]:
        return [self.quote(part) for part in parts]

    def _top_offers(self, slug: str) -> list[Offer]:
        with self._lock:
            cached = self._cache.get(slug)
            if cached and time.monotonic() - cached[0] < self.cache_seconds:
                return cached[1]

        self.limiter.wait()
        response = self._client.get(f"{BASE_URL}/orders/item/{slug}/top")
        response.raise_for_status()
        offers = _parse_offers(response.json())

        with self._lock:
            self._cache[slug] = (time.monotonic(), offers)
        return offers


def _parse_offers(payload: dict) -> list[Offer]:
    """Pull sell orders from a /top response, cheapest first."""
    orders = (payload.get("data") or {}).get("sell") or []
    offers = []
    for order in orders:
        user = order.get("user") or {}
        status = str(user.get("status", "")).lower()
        if status not in TRADEABLE_STATUS:
            continue
        try:
            platinum = int(order["platinum"])
        except (KeyError, TypeError, ValueError):
            continue
        offers.append(
            Offer(
                platinum=platinum,
                quantity=int(order.get("quantity", 1) or 1),
                seller=str(user.get("ingameName", "?")),
                status=status,
            )
        )
    return sorted(offers, key=lambda offer: offer.platinum)


def best_index(quotes: list[Quote], prefer: str = "platinum") -> int | None:
    """Which reward to take, by the player's stated preference.

    Ducats break platinum ties, since a part worth the same platinum as another
    is strictly better if it is also worth more ducats.
    """
    # Untradeable rewards are excluded, not just ranked last: "best" means the
    # one to take for value, and a Forma Blueprint cannot be sold at any price.
    # If every reward is untradeable there is no best pick, which is honest.
    ranked = [
        (index, quote)
        for index, quote in enumerate(quotes)
        if quote.part is not None and quote.part.tradeable
    ]
    if not ranked:
        return None

    def platinum_key(item: tuple[int, Quote]) -> tuple[float, int]:
        _, quote = item
        return (quote.platinum or 0.0, quote.ducats)

    def ducat_key(item: tuple[int, Quote]) -> tuple[int, float]:
        _, quote = item
        return (quote.ducats, quote.platinum or 0.0)

    key = ducat_key if prefer == "ducats" else platinum_key
    return max(ranked, key=key)[0]
