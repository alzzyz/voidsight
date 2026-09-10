"""Shared scan state, and turning a scan into something the page can render."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime
from typing import Any

from voidsight.pricing.market import Quote, best_index
from voidsight.vision.pipeline import ScanResult

log = logging.getLogger(__name__)


def payload_for(
    result: ScanResult,
    quotes: list[Quote],
    *,
    prefer: str = "platinum",
    source: str | None = None,
    live_capture: bool = False,
) -> dict[str, Any]:
    """Flatten a scan plus its prices into the JSON the page consumes."""
    rewards = []
    for reward, quote in zip(result.rewards, quotes, strict=False):
        part = reward.part
        rewards.append(
            {
                "index": reward.index,
                "matched": part is not None,
                "name": part.display_name if part else (reward.raw_text or "unreadable"),
                "raw_text": reward.raw_text,
                "score": round(reward.match.score, 1),
                "ocr_confidence": round(reward.ocr_confidence, 2),
                "ambiguous": reward.match.ambiguous,
                "platinum": quote.platinum,
                "lowest": quote.lowest,
                "offers": [offer.platinum for offer in quote.offers[:5]],
                "average": part.avg_plat if part else None,
                "volume": part.volume_today if part else None,
                "ducats": quote.ducats if part else None,
                "ducats_per_platinum": (
                    round(quote.ducats_per_platinum, 1)
                    if quote.ducats_per_platinum is not None
                    else None
                ),
                "vaulted": bool(part.vaulted) if part else None,
                "tradeable": bool(part.tradeable) if part else None,
                "live": quote.live,
                "error": quote.error,
            }
        )

    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": source,
        #: False when the frame came from a saved image rather than the game.
        "live_capture": live_capture,
        "theme": result.theme.name if result.theme else None,
        "relic": result.relic.name if result.relic else None,
        "confidence": round(result.confidence, 2),
        "ok": result.ok,
        "reason": result.notes.get("reason"),
        "best": best_index(quotes, prefer) if quotes else None,
        "rewards": rewards,
    }


class ScanStore:
    """Latest scan, plus fan-out to connected pages.

    Scans arrive from a worker thread (the log watcher) while subscribers live
    on the event loop, so publishing hops threads explicitly.
    """

    def __init__(self, history: int = 20) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        self._history: list[dict[str, Any]] = []
        self._history_limit = history
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest

    @property
    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, payload: dict[str, Any]) -> None:
        """Record a scan and push it to every open page. Safe from any thread."""
        with self._lock:
            self._latest = payload
            self._history.insert(0, payload)
            del self._history[self._history_limit :]

        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._fan_out, payload)
        except RuntimeError:  # loop already closed
            pass

    def _fan_out(self, payload: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                log.debug("dropping update for a page that is not keeping up")
