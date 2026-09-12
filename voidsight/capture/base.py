"""Capture backend interface and the frame buffer behind it.

Frames are RGB uint8 arrays of the whole game window or screen.

The buffer exists because of how the trigger works. Warframe writes the reward
screen to EE.log, but Wine buffers that write, so the log line can arrive after
the screen is already gone — the known failure mode of existing Linux tooling.
Backends that can stream therefore push frames continuously, and a late trigger
looks *backwards* through the buffer for a frame that still had the rewards on it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

log = logging.getLogger(__name__)

Frame = np.ndarray


@dataclass(frozen=True)
class Shot:
    frame: Frame
    captured_at: float

    @property
    def age(self) -> float:
        return time.monotonic() - self.captured_at


class CaptureError(RuntimeError):
    """A backend could not be started, or is no longer usable."""


@runtime_checkable
class CaptureBackend(Protocol):
    #: Short identifier used in config and on the command line.
    name: str

    def grab(self) -> Frame | None:
        """Return the current frame, or None if there is nothing to capture."""

    def close(self) -> None: ...


class RingBuffer:
    """The last few seconds of frames, newest last.

    Written by the capture thread and read by the log watcher's, which is the
    whole point of it — so every access takes the lock, and a reader gets a
    snapshot. Iterating the deque live raises "deque mutated during iteration"
    the moment a frame lands mid-search, and that exception surfaces inside the
    watcher thread, killing the trigger for the rest of the session.
    """

    def __init__(self, *, seconds: float = 3.0, max_frames: int = 40) -> None:
        self.seconds = seconds
        self._shots: deque[Shot] = deque(maxlen=max_frames)
        self._lock = threading.Lock()

    def add(self, frame: Frame) -> Shot:
        shot = Shot(frame=frame, captured_at=time.monotonic())
        with self._lock:
            self._shots.append(shot)
        return shot

    def newest(self) -> Shot | None:
        with self._lock:
            return self._shots[-1] if self._shots else None

    def snapshot(self) -> list[Shot]:
        """Every buffered frame, newest first, as a list that cannot change."""
        with self._lock:
            return list(reversed(self._shots))

    def recent(self, *, seconds: float | None = None) -> Iterator[Shot]:
        """Buffered frames, newest first — the order to search a late trigger in."""
        window = self.seconds if seconds is None else seconds
        for shot in self.snapshot():
            if shot.age <= window:
                yield shot

    def clear(self) -> None:
        with self._lock:
            self._shots.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._shots)
