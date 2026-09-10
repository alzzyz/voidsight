"""The client window: a header, two pages, and a status line."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from voidsight import __version__, game
from voidsight.app.server import Session
from voidsight.config import client_size, client_size_labels, resolve_client_size
from voidsight.session import SessionLedger
from voidsight.ui import platform, style
from voidsight.ui.artwork import ArtworkLoader
from voidsight.ui.bridge import Bridge
from voidsight.ui.header import Header
from voidsight.ui.home import HomeView
from voidsight.ui.overlay import Overlay
from voidsight.ui.quicksell import QuickSellView
from voidsight.ui.settings import SettingsView

log = logging.getLogger(__name__)

#: How often to re-check whether the game is running. Cheap, but not free.
DETECT_INTERVAL_MS = 5000


class MainWindow(QMainWindow):
    def __init__(
        self,
        session: Session,
        bridge: Bridge,
        log_path: Path | None = None,
        *,
        wait_for_game: bool = False,
    ) -> None:
        super().__init__()
        self.session = session
        self.bridge = bridge
        self.log_path = log_path
        self.wait_for_game = wait_for_game
        self.setWindowTitle("voidsight")
        # Resolve first: a hand-edited config should not leave the window at
        # Qt's arbitrary default.
        self.apply_client_size(resolve_client_size(session.config.client_size), persist=False)

        self.overlay = Overlay(
            corner=session.config.overlay_corner,
            screen_index=session.config.overlay_screen,
            seconds=session.config.overlay_seconds,
            opacity=session.config.overlay_opacity,
        )

        self.ledger = SessionLedger()
        self.artwork = ArtworkLoader(parent=self)

        self.header = Header()
        self.header.settings_toggled.connect(self.show_settings)
        self.header.page_selected.connect(self.show_page)
        self.header.client_size_selected.connect(self.apply_client_size)
        # Silent: showing the stored size must not count as choosing it, or
        # startup would write the config file every launch — and a value the
        # user never picked could be persisted that way.
        self.header.client_size.set_value(
            resolve_client_size(session.config.client_size), silent=True
        )

        self.home = HomeView(self.artwork, session.catalog)
        self.home.setObjectName("page")
        self.settings = SettingsView(session, overlay=self.overlay)
        self.settings.setObjectName("page")
        self.settings.changed.connect(self._on_settings_changed)
        self.settings.scan_requested.connect(bridge.scan_now)
        self.settings.file_requested.connect(bridge.scan_file)

        self.quicksell = QuickSellView(self.ledger, self.artwork, session.catalog)

        self.pages = QStackedWidget()
        self.pages.addWidget(self.home)
        self.pages.addWidget(self.quicksell)
        self.pages.addWidget(self.settings)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.pages, stretch=1)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())

        bridge.scanned.connect(self.on_scan)
        bridge.failed.connect(self.on_failure)
        bridge.busy.connect(self.settings.set_busy)

        self._current_page = "home"
        self.game_state = game.GameState(running=False)
        self._detect_timer = QTimer(self)
        self._detect_timer.timeout.connect(self.refresh_game_state)
        self._detect_timer.start(DETECT_INTERVAL_MS)
        self.refresh_game_state()

    def apply_client_size(self, label: str, *, persist: bool = True) -> None:
        """Resize the window and scale its base font together.

        Font as well as geometry, so the steps read as a zoom level instead of
        the same text in a roomier frame. Unknown labels are ignored rather than
        stored, so a hand-edited config cannot poison the file further.
        """
        if label.upper() not in client_size_labels():
            log.warning("ignoring unknown client size %r", label)
            return
        width, height, font_size = client_size(label)
        self.session.config.client_size = label.upper()
        self.resize(width, height)
        if application := QApplication.instance():
            font = application.font()
            font.setPointSize(font_size)
            application.setFont(font)
        if persist:
            self.session.save_config()

    def show_settings(self, show: bool) -> None:
        if show:
            self.settings.reload()
            self.pages.setCurrentWidget(self.settings)
        else:
            self.show_page(self._current_page)

    def show_page(self, key: str) -> None:
        """Switch between the two content pages, leaving settings."""
        self._current_page = key
        self.header.settings.setChecked(False)
        page = self.quicksell if key == "quicksell" else self.home
        if key == "quicksell":
            self.quicksell.refresh()
        self.pages.setCurrentWidget(page)

    def refresh_game_state(self) -> None:
        """Poll for the game, and keep the idle page honest about what it is waiting for."""
        was_running = self.game_state.running
        self.game_state = game.detect(self.log_path)
        self.header.set_game_state(self.game_state)

        # Started from a login hook: follow the game in and out, so the window
        # is present exactly while there is something to read.
        if self.wait_for_game and self.game_state.running != was_running:
            if self.game_state.running:
                self.show()
                self.raise_()
            else:
                self.hide()
        self.home.describe_idle(
            game_running=self.game_state.running,
            watching=self.log_path is not None,
            has_backend=self.session.backend is not None,
        )

    def _on_settings_changed(self) -> None:
        self.refresh_game_state()

    def on_scan(self, payload: dict[str, Any]) -> None:
        self.home.show_scan(payload)
        # Every readable scan is offered for quick-selling; which of the four
        # was actually taken is the player's to confirm there.
        self.ledger.add_scan(payload)
        self.quicksell.refresh()
        # A result always wins the foreground; settings can wait.
        self.header.settings.setChecked(False)
        self.header.select_page("home")
        self._current_page = "home"
        self.pages.setCurrentWidget(self.home)
        if self.session.config.overlay:
            self.overlay.show_scan(payload)

        identified = sum(1 for reward in payload.get("rewards", []) if reward.get("matched"))
        total = len(payload.get("rewards", []))
        message = (
            f"Read {identified}/{total} rewards from {payload.get('source', 'a frame')}"
            if total
            else "No rewards found in that frame"
        )
        self.statusBar().showMessage(message)
        self.settings.set_debug_status(message)

    def on_failure(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self.settings.set_debug_status(message, error=True)


def run(
    session: Session,
    log_path: Path | None = None,
    *,
    wait_for_game: bool = False,
) -> int:
    """Start the client. Owns the live runner for as long as the window is open."""
    # Before QApplication: the platform plugin is chosen at construction, and it
    # decides whether the overlay can place itself at all.
    platform.prefer_xwayland()

    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("voidsight")
    application.setApplicationVersion(__version__)
    application.setStyleSheet(style.stylesheet())
    if family := style.resolve_font():
        font = application.font()
        font.setFamily(family)
        application.setFont(font)

    bridge = Bridge(session)
    window = MainWindow(session, bridge, log_path, wait_for_game=wait_for_game)

    if limitation := platform.overlay_limitation(application.platformName()):
        if session.config.overlay:
            log.warning("%s", limitation)
        window.settings.set_overlay_limitation(limitation)

    runner = None
    if log_path is not None:
        from voidsight.live import LiveRunner

        runner = LiveRunner(session, log_path, on_result=bridge.publish)
        runner.start()
        window.statusBar().showMessage(f"Watching {log_path}")
    elif session.backend is not None:
        window.statusBar().showMessage("No EE.log found — scan from Settings › Debug")
    else:
        window.statusBar().showMessage(
            "No capture backend — open a screenshot from Settings › Debug"
        )

    # With --wait-for-game the window appears when the game does, so a login
    # hook does not put a window in front of someone who is not playing yet.
    if wait_for_game and not window.game_state.running:
        log.info("waiting for Warframe before showing the window")
    else:
        window.show()
    try:
        return application.exec()
    finally:
        window.overlay.hide()
        window.artwork.close()
        if runner is not None:
            runner.stop()
