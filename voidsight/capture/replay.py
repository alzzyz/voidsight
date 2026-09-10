"""A capture backend that reads screenshots from a directory.

This is how the app runs on a machine that is not playing Warframe: point it at
saved reward screens and everything downstream — trigger, scan, pricing, UI —
behaves as it does live. It is also the backend the test suite uses.
"""

from __future__ import annotations

import logging
from itertools import cycle
from pathlib import Path

import cv2

from voidsight.capture.base import CaptureError, Frame

log = logging.getLogger(__name__)

SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


class ReplayBackend:
    """Serves images from a directory, one per grab, looping."""

    name = "replay"

    def __init__(self, directory: Path, *, loop: bool = True) -> None:
        self.directory = Path(directory)
        self.paths = sorted(
            path
            for path in self.directory.iterdir()
            if path.is_file() and path.suffix.lower() in SUFFIXES
        ) if self.directory.is_dir() else []
        if not self.paths:
            raise CaptureError(f"no images in {self.directory}")
        self._order = cycle(self.paths) if loop else iter(self.paths)
        self.last_path: Path | None = None
        log.info("replay backend serving %d image(s) from %s", len(self.paths), self.directory)

    def grab(self) -> Frame | None:
        try:
            path = next(self._order)
        except StopIteration:
            return None
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            log.warning("could not decode %s", path)
            return None
        self.last_path = path
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        return None
