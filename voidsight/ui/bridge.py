"""Getting scans from worker threads onto the Qt event loop.

The log watcher and capture loop run in plain threads inside the core, which
knows nothing about Qt. This adapts their callbacks into signals, and runs
manual scans off the UI thread so the window never freezes while OCR and four
market requests are in flight.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from voidsight.app.server import Session

log = logging.getLogger(__name__)


class Bridge(QObject):
    """Signals the views listen to."""

    #: A completed scan, as the payload dict both front ends render.
    scanned = Signal(dict)
    #: Something went wrong doing a scan the user asked for.
    failed = Signal(str)
    #: True while a scan is running.
    busy = Signal(bool)

    def __init__(self, session: Session, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.pool = QThreadPool.globalInstance()

    def publish(self, payload: dict[str, Any]) -> None:
        """Called from the live runner's thread. Qt queues it to the UI thread."""
        self.scanned.emit(payload)

    def report_problem(self, message: str) -> None:
        """Called from the live runner's thread when a trigger produced nothing.

        A trigger that reads no rewards is otherwise indistinguishable from no
        trigger at all: the window simply never changes. Saying so is the
        difference between a broken setup and an app that looks asleep.
        """
        self.failed.emit(message)

    @Slot()
    def scan_now(self) -> None:
        self.busy.emit(True)
        self.pool.start(_ScanTask(self, self._capture))

    @Slot(str)
    def scan_file(self, path: str) -> None:
        """Read a saved screenshot. The only route when nothing can be captured."""
        self.busy.emit(True)
        self.pool.start(_ScanTask(self, lambda: self._read_file(Path(path))))

    def _capture(self) -> dict[str, Any]:
        return self.session.scan_now()

    def _read_file(self, path: Path) -> dict[str, Any]:
        import cv2

        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"could not read {path.name} as an image")
        frame = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        return self.session.scan_frame(frame, source=f"file: {path.name}")


class _ScanTask(QRunnable):
    def __init__(self, bridge: Bridge, work: Callable[[], dict[str, Any]]) -> None:
        super().__init__()
        self.bridge = bridge
        self.work = work

    def run(self) -> None:
        try:
            payload = self.work()
        except Exception as exc:  # a failed scan must not take the app down
            log.warning("scan failed: %s", exc)
            self.bridge.failed.emit(str(exc))
        else:
            self.bridge.scanned.emit(payload)
        finally:
            self.bridge.busy.emit(False)
