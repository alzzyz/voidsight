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
from voidsight.capture.base import RingBuffer, Shot
from voidsight.trigger.eelog import LogWatcher, RewardEvent

log = logging.getLogger(__name__)

#: Frames per second to keep in the buffer. The reward screen lasts ~10s, so
#: this only has to be fast enough to catch it, not smooth.
CAPTURE_FPS = 6.0
#: How far back a late trigger may look.
LOOKBACK_SECONDS = 4.0
#: Stop searching once a frame reads this well.
GOOD_ENOUGH = 0.75
#: How many unreadable reward screens to keep on disk.
KEPT_FAILURES = 3
#: How long to keep reading *new* frames when the buffer held nothing.
#:
#: The buffer exists because Wine can deliver the log line late. It can also
#: arrive early: Warframe writes "Got rewards" when the client receives them,
#: which is before the panel has finished animating in — so the frames worth
#: reading do not exist yet. Looking only backwards meant waiting for the
#: *next* trigger line, several seconds into a ten-second screen.
FORWARD_SECONDS = 8.0
#: How long after a reading to ignore further triggers.
#:
#: One reward screen produces several trigger lines, seconds apart. Once one of
#: them has been read, the rest would re-scan a screen that is on its way out
#: and report a failure to read the thing that was just read.
SETTLE_SECONDS = 12.0


class _Search:
    """The best reading found so far, across however many frames it took."""

    def __init__(self, session: Session, relic: object | None) -> None:
        self.session = session
        self.relic = relic
        self.best: Any = None
        self.best_rank = (0, 0.0)
        self.scanned = 0
        self.newest: Shot | None = None
        self.found = False
        self._read: set[float] = set()

    def consider(self, shot: Shot | None) -> bool:
        """Read one frame. True when it is good enough to stop looking."""
        if shot is None or shot.captured_at in self._read:
            return False
        self._read.add(shot.captured_at)
        self.scanned += 1
        # Genuinely the newest frame looked at, not the first: the backward
        # pass runs newest-first and the forward one oldest-first, and it is
        # this frame that gets kept when nothing read.
        if self.newest is None or shot.captured_at > self.newest.captured_at:
            self.newest = shot

        result = self.session.read(shot.frame, relic=self.relic)
        rank = (len(result.identified), result.confidence)
        if rank > self.best_rank:
            self.best, self.best_rank = result, rank
        if result.rewards:
            self.found = True
        return bool(result.ok and result.confidence >= GOOD_ENOUGH)


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
        on_problem: Callable[[str], None] | None = None,
        keep_failures: bool = True,
        forward_seconds: float = FORWARD_SECONDS,
        settle_seconds: float = SETTLE_SECONDS,
    ) -> None:
        self.session = session
        self.buffer = session.buffer or RingBuffer(seconds=lookback)
        session.buffer = self.buffer
        self.fps = fps
        self.lookback = lookback
        self.max_frames_scanned = max_frames_scanned
        self.on_result = on_result
        self.on_problem = on_problem
        self.keep_failures = keep_failures
        self.forward_seconds = forward_seconds
        self.settle_seconds = settle_seconds
        self._last_success = 0.0
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
                # Re-read each pass: a client that started before the game may
                # acquire a backend after this loop is already running.
                if (backend := self.session.backend) is not None:
                    if (frame := backend.grab()) is not None:
                        self.buffer.add(frame)
            except Exception as exc:  # a backend dying must not kill the loop
                log.warning("capture failed: %s", exc)
            self._stop.wait(max(0.0, interval - (time.monotonic() - started)))

    def _watch_forward(self, search: _Search) -> None:
        """Read frames as they are captured, until one shows the rewards.

        Cheap to sit in: the capture thread keeps filling the buffer either
        way, and this only reads frames it has not read already.
        """
        deadline = time.monotonic() + self.forward_seconds
        interval = 1.0 / max(self.fps, 1.0)
        while not self._stop.is_set() and time.monotonic() < deadline:
            self._stop.wait(interval)
            if search.consider(self.buffer.newest()):
                log.info("reward screen appeared after the trigger, %d frame(s) in", search.scanned)
                return

    def _keep_failure(self, shot: Shot | None) -> Path | None:
        """Write a reward screen we could not read, for scanning afterwards.

        The reward screen is up for ten seconds and does not come back, so a
        frame that defeated the vision pipeline is the only way to work out why
        — `voidsight scan <frame> --debug-dir` on it shows where it looked.
        """
        if shot is None or not self.keep_failures:
            return None
        import cv2

        from voidsight.config import state_dir

        directory = state_dir() / "unread"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = directory / f"reward-{stamp}.png"
            cv2.imwrite(str(path), cv2.cvtColor(shot.frame, cv2.COLOR_RGB2BGR))
            # Bounded: these are full-screen PNGs and nothing else prunes them.
            for old_frame in sorted(directory.glob("reward-*.png"))[:-KEPT_FAILURES]:
                old_frame.unlink(missing_ok=True)
        except Exception as exc:  # OSError, and whatever cv2 decides to raise
            log.warning("could not save the unread frame: %s", exc)
            return None
        return path

    def _report(self, message: str) -> None:
        """Tell the front end about a trigger that produced nothing.

        Without this a failed scan looks exactly like a mission with no rewards
        in it: the window simply never changes.
        """
        if self.on_problem is not None:
            self.on_problem(message)

    def _on_reward(self, event: RewardEvent) -> None:
        payload = self.scan_for(event)
        if payload and self.on_result:
            self.on_result(payload)

    def scan_for(self, event: RewardEvent) -> dict[str, Any] | None:
        """Find the best reading among captured frames and price it.

        Backwards through what is already buffered, then — if none of it showed
        rewards — forwards through frames as they arrive, because the trigger
        can land on either side of the panel appearing.
        """
        since_success = time.monotonic() - self._last_success
        if since_success < self.settle_seconds:
            log.debug("ignoring a trigger %.1fs after a reading", since_success)
            return None

        relic = self.session.catalog.relic(event.relic) if event.relic else None
        if event.relic and relic is None:
            log.debug("log named relic %r, which is not in the catalog", event.relic)

        search = _Search(self.session, relic)
        for shot in self.buffer.recent(seconds=self.lookback):
            if search.scanned >= self.max_frames_scanned:
                break
            if search.consider(shot):
                break

        if not search.found and self.forward_seconds > 0 and self.session.live_capture:
            self._watch_forward(search)

        best, scanned, newest = search.best, search.scanned, search.newest
        if best is None or not best.rewards:
            # Distinguish "could not read the rewards" from "there was nothing to
            # read": an empty buffer means capture is broken, not OCR, and that
            # is the one failure a silent app makes impossible to guess at.
            if not scanned:
                self._report(
                    "Reward screen detected, but no frames were captured "
                    f"({self.session.describe_source()})"
                )
                log.warning(
                    "reward trigger fired with an empty frame buffer — nothing is "
                    "being captured (source: %s)",
                    self.session.describe_source(),
                )
            else:
                saved = self._keep_failure(newest)
                frames = "frame" if scanned == 1 else "frames"
                where = f" — frame saved to {saved}" if saved else ""
                self._report(
                    f"Reward screen detected, but {scanned} captured {frames} "
                    f"did not read{where}"
                )
                log.info(
                    "reward trigger fired but no buffered frame showed rewards "
                    "(%d checked)%s",
                    scanned,
                    f"; saved {saved}" if saved else "",
                )
            return None
        log.info(
            "reward screen read from %d frame(s), %d/%d identified",
            scanned,
            len(best.identified),
            len(best.rewards),
        )
        self._last_success = time.monotonic()
        return self.session.price_and_publish(best, source="ee.log")
