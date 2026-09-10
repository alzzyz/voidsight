"""Item artwork for the views.

Icons come off the network the first time they are needed, which must never
happen on the UI thread — a reward screen lasts about ten seconds and the window
freezing for a download would be worse than showing no picture at all. So a
cached file is used immediately, and anything else is fetched on a worker and
announced when it lands.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QPixmap

from voidsight.data.assets import IconStore
from voidsight.data.catalog import Part

log = logging.getLogger(__name__)


class ArtworkLoader(QObject):
    """Hands out pixmaps, fetching missing ones in the background."""

    #: (part name, pixmap) once an icon has arrived.
    loaded = Signal(str, QPixmap)
    #: Internal hop from a worker thread. Emitting a signal across threads is
    #: queued by Qt, which is what gets us back onto the UI thread — QPixmap
    #: must not be constructed anywhere else.
    _fetched = Signal(str, str)

    def __init__(self, store: IconStore | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = store or IconStore()
        self.pool = QThreadPool.globalInstance()
        self._pixmaps: dict[str, QPixmap] = {}
        self._pending: set[str] = set()
        self._fetched.connect(self._on_fetched)

    def close(self) -> None:
        self.store.close()

    def pixmap(self, part: Part, size: int) -> QPixmap | None:
        """A scaled icon if one is at hand, else None and a background fetch."""
        key = part.name
        if key in self._pixmaps:
            return self._scaled(self._pixmaps[key], size)

        if path := self.store.cached(part):
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self._pixmaps[key] = pixmap
                return self._scaled(pixmap, size)

        if part.icon and key not in self._pending:
            self._pending.add(key)
            self.pool.start(_FetchTask(self, part))
        return None

    @staticmethod
    def _scaled(pixmap: QPixmap, size: int) -> QPixmap:
        return pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    @Slot(str, str)
    def _on_fetched(self, part_name: str, path: str) -> None:
        self._pending.discard(part_name)
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        self._pixmaps[part_name] = pixmap
        self.loaded.emit(part_name, pixmap)


class _FetchTask(QRunnable):
    def __init__(self, loader: ArtworkLoader, part: Part) -> None:
        super().__init__()
        self.loader = loader
        self.part = part

    def run(self) -> None:
        try:
            path = self.loader.store.fetch(self.part)
        except Exception as exc:  # artwork is decoration; never fatal
            log.debug("artwork fetch failed for %s: %s", self.part.name, exc)
            path = None
        if path is None:
            self.loader._pending.discard(self.part.name)
            return
        self.loader._fetched.emit(self.part.name, str(path))
