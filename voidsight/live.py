"""Running against the live game: watch the log, keep frames, scan on cue.

The interesting part is what happens when the trigger fires. Wine buffers
Warframe's writes to EE.log, so the reward line often arrives after the screen
has already appeared and sometimes after it is gone — the failure existing Linux
tools document. Frames are therefore captured continuously into a short ring
buffer, and a trigger searches *backwards* through it for a frame that reads,
instead of grabbing whatever is on screen at the instant the line lands.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from voidsight.app.server import Session
from voidsight.capture.base import RingBuffer
from voidsight.trigger.eelog import LogWatcher, RewardEvent

log = logging.getLogger(__name__)

#: Frames per second to keep in the buffer. The reward screen lasts ~10s, so
#: this only has to be fast enough to catch it, not smooth.
CAPTURE_FPS = 6.0
#: How far back a late trigger may look.
LOOKBACK_SECONDS = 4.0
#: Stop searching once a frame reads this well.
GOOD_ENOUGH = 0.75


class LiveRunner:
    """Capture loop plus log watcher, feeding a Session."""

    def __init__(
        self,
        session: Session,
        log_path: Path,
        *,
        fps: float = CAPTURE_FPS,
        lookback: float = LOOKBACK_SECONDS,
        max_frames_scanned: int = 6,
        on_result: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.session = session
        self.buffer = session.buffer or RingBuffer(seconds=lookback)
        session.buffer = self.buffer
        self.fps = fps
        self.lookback = lookback
        self.max_frames_scanned = max_frames_scanned
        self.on_result = on_result
        self.watcher = LogWatcher(log_path, self._on_reward)
        self._stop = threading.Event()
        self._capture_thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="capture", daemon=True
        )
        self._capture_thread.start()
        self.watcher.start()

    def stop(self) -> None:
        self._stop.set()
        self.watcher.stop()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=2.0)
            self._capture_thread = None

    def _capture_loop(self) -> None:
        interval = 1.0 / self.fps
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if (frame := self.session.backend.grab()) is not None:
                    self.buffer.add(frame)
            except Exception as exc:  # a backend dying must not kill the loop
                log.warning("capture failed: %s", exc)
            self._stop.wait(max(0.0, interval - (time.monotonic() - started)))

    def _on_reward(self, event: RewardEvent) -> None:
        payload = self.scan_for(event)
        if payload and self.on_result:
            self.on_result(payload)

    def scan_for(self, event: RewardEvent) -> dict[str, Any] | None:
        """Find the best reading among recently captured frames and price it."""
        relic = self.session.catalog.relic(event.relic) if event.relic else None
        if event.relic and relic is None:
            log.debug("log named relic %r, which is not in the catalog", event.relic)

        best = None
        best_rank = (0, 0.0)
        scanned = 0
        for shot in self.buffer.recent(seconds=self.lookback):
            if scanned >= self.max_frames_scanned:
                break
            scanned += 1
            result = self.session.read(shot.frame, relic=relic)
            rank = (len(result.identified), result.confidence)
            if rank > best_rank:
                best, best_rank = result, rank
            if result.ok and result.confidence >= GOOD_ENOUGH:
                break

        if best is None or not best.rewards:
            log.info(
                "reward trigger fired but no buffered frame showed rewards (%d checked)",
                scanned,
            )
            return None
        log.info(
            "reward screen read from %d frame(s), %d/%d identified",
            scanned,
            len(best.identified),
            len(best.rewards),
        )
        return self.session.price_and_publish(best, source="ee.log")
