"""Settings: everything that is not a result.

Grouped by what the setting is *for* rather than by which module reads it.
Warframe first, because those two values decide whether anything reads at all;
then the overlay; then market; then debug tools, which are here rather than in
the header because they are for diagnosing a setup, not for playing.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from voidsight import startup
from voidsight.app.server import Session
from voidsight.config import MAX_UI_SCALE, MIN_UI_SCALE, config_path
from voidsight.ui import icons, style
from voidsight.ui.overlay import CORNERS, Overlay
from voidsight.ui.widgets import ToggleSwitch
from voidsight.vision import theme as theme_module

DETECT = "Detect automatically"

THEME_HELP = (
    "Match Warframe's own Interface options. The theme decides which pixels count "
    "as reward text; UI Scaling decides how big the reward panel is on screen. "
    "Both are the game's settings, not this app's — the window's own size lives in "
    "the top bar."
)

#: Shown by "Preview overlay", so placement can be judged without a fissure run.
SAMPLE_SCAN = {
    "best": 1,
    "rewards": [
        {
            "name": "Nikana Prime Blueprint", "platinum": 7.0, "ducats": 25,
            "matched": True, "live": True, "vaulted": True,
        },
        {
            "name": "Braton Prime Stock", "platinum": 42.0, "ducats": 15,
            "matched": True, "live": True,
        },
        {
            "name": "Forma Blueprint", "platinum": 0.0, "ducats": 0,
            "matched": True, "live": False,
        },
    ],
}


def _section(title: str) -> QLabel:
    label = QLabel(title)
    label.setObjectName("section")
    return label


class SettingsView(QWidget):
    changed = Signal()
    #: Debug actions, handled by the window that owns the capture session.
    scan_requested = Signal()
    file_requested = Signal(str)

    def __init__(self, session: Session, overlay: Overlay | None = None) -> None:
        super().__init__()
        self.session = session
        self.overlay = overlay

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(style.SPACE_XL, style.SPACE_L, style.SPACE_XL, style.SPACE_XL)
        layout.setSpacing(style.SPACE_M)

        layout.addWidget(_section("Warframe"))
        help_text = QLabel(THEME_HELP)
        help_text.setObjectName("hint")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        form = QFormLayout()
        form.setSpacing(style.SPACE_M)
        self.theme = QComboBox()
        self.theme.addItem(DETECT, userData=None)
        for entry in theme_module.THEMES:
            self.theme.addItem(entry.name, userData=entry.name)
        form.addRow(self._label("UI theme"), self.theme)

        self.scale = QDoubleSpinBox()
        self.scale.setRange(MIN_UI_SCALE * 100, MAX_UI_SCALE * 100)
        self.scale.setSingleStep(5.0)
        self.scale.setDecimals(0)
        self.scale.setSuffix(" %")
        form.addRow(self._label("UI Scaling"), self.scale)
        layout.addLayout(form)

        layout.addSpacing(style.SPACE_M)
        layout.addWidget(_section("Rewards"))
        prefer_form = QFormLayout()
        prefer_form.setSpacing(style.SPACE_M)
        self.prefer = QComboBox()
        self.prefer.addItem("Platinum", userData="platinum")
        self.prefer.addItem("Ducats", userData="ducats")
        prefer_form.addRow(self._label("Highlight best by"), self.prefer)
        layout.addLayout(prefer_form)

        layout.addSpacing(style.SPACE_M)
        layout.addWidget(_section("Overlay"))
        overlay_form = QFormLayout()
        overlay_form.setSpacing(style.SPACE_M)
        self.overlay_enabled = ToggleSwitch()
        overlay_form.addRow(self._label("Draw prices over the game"), self.overlay_enabled)

        self.overlay_corner = QComboBox()
        for corner in CORNERS:
            self.overlay_corner.addItem(corner.replace("-", " "), userData=corner)
        overlay_form.addRow(self._label("Position"), self.overlay_corner)

        self.overlay_screen = QComboBox()
        overlay_form.addRow(self._label("Monitor"), self.overlay_screen)

        self.overlay_seconds = QDoubleSpinBox()
        self.overlay_seconds.setRange(0.0, 60.0)
        self.overlay_seconds.setSingleStep(1.0)
        self.overlay_seconds.setDecimals(0)
        self.overlay_seconds.setSuffix(" s  (0 keeps it up)")
        overlay_form.addRow(self._label("Duration"), self.overlay_seconds)
        layout.addLayout(overlay_form)

        self.overlay_note = QLabel("")
        self.overlay_note.setObjectName("hint")
        self.overlay_note.setWordWrap(True)
        self.overlay_note.hide()
        layout.addWidget(self.overlay_note)

        layout.addSpacing(style.SPACE_M)
        layout.addWidget(_section("Start with Warframe"))
        layout.addWidget(self._startup_panel())

        layout.addSpacing(style.SPACE_M)
        layout.addWidget(_section("warframe.market"))
        market_note = QLabel(
            "Prices come from warframe.market's public API and need no account. "
            "Signing in — to post your own listings from here — is not built yet."
        )
        market_note.setObjectName("hint")
        market_note.setWordWrap(True)
        layout.addWidget(market_note)

        buttons = QHBoxLayout()
        self.save = QPushButton("Save")
        self.save.setObjectName("primary")
        self.save.clicked.connect(self.apply)
        buttons.addWidget(self.save)
        self.preview = QPushButton("Preview overlay")
        self.preview.clicked.connect(self.show_preview)
        buttons.addWidget(self.preview)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        buttons.addWidget(self.status)
        buttons.addStretch(1)
        layout.addSpacing(style.SPACE_S)
        layout.addLayout(buttons)

        layout.addSpacing(style.SPACE_L)
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)
        layout.addSpacing(style.SPACE_S)
        layout.addWidget(_section("Debug"))
        layout.addWidget(self._debug_panel())

        path = QLabel(f"Settings are saved to {config_path()}")
        path.setObjectName("hint")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addSpacing(style.SPACE_M)
        layout.addWidget(path)
        layout.addStretch(1)

        self.reload()

    def _startup_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(style.SPACE_L, style.SPACE_M, style.SPACE_L, style.SPACE_M)
        layout.setSpacing(style.SPACE_S)

        # Steam's way: the right lifetime, but the config file is Steam's.
        steam_help = QLabel(
            "The tidiest hook is Steam's own launch options, which start the client "
            "with the game and close it afterwards. Paste this into Warframe → "
            "Properties → Launch Options:"
        )
        steam_help.setObjectName("hint")
        steam_help.setWordWrap(True)
        layout.addWidget(steam_help)

        command_row = QHBoxLayout()
        command_row.setSpacing(style.SPACE_S)
        self.launch_command = QLineEdit(startup.steam_launch_command())
        self.launch_command.setReadOnly(True)
        self.launch_command.setMinimumWidth(340)
        command_row.addWidget(self.launch_command, stretch=1)
        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self._copy_launch_command)
        command_row.addWidget(self.copy_button)
        layout.addLayout(command_row)

        self.steam_status = QLabel("")
        self.steam_status.setObjectName("hint")
        self.steam_status.setWordWrap(True)
        layout.addWidget(self.steam_status)

        why = QLabel(
            "voidsight does not edit that setting for you: Steam rewrites its "
            "config when it exits, so a change made behind its back is discarded "
            "at best and damages other games' options at worst."
        )
        why.setObjectName("hint")
        why.setWordWrap(True)
        layout.addWidget(why)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addSpacing(style.SPACE_XS)
        layout.addWidget(divider)
        layout.addSpacing(style.SPACE_XS)

        # Our way: a login entry, which is ours to write.
        autostart_row = QHBoxLayout()
        autostart_row.setSpacing(style.SPACE_M)
        self.autostart = ToggleSwitch()
        self.autostart.toggled.connect(self._on_autostart)
        autostart_row.addWidget(self.autostart)
        autostart_label = QLabel("Start at login and wait for Warframe")
        autostart_label.setObjectName("muted")
        autostart_row.addWidget(autostart_label)
        autostart_row.addStretch(1)
        layout.addLayout(autostart_row)

        self.autostart_status = QLabel("")
        self.autostart_status.setObjectName("hint")
        self.autostart_status.setWordWrap(True)
        layout.addWidget(self.autostart_status)
        return panel

    def _copy_launch_command(self) -> None:
        if clipboard := QGuiApplication.clipboard():
            clipboard.setText(self.launch_command.text())
            self.copy_button.setText("Copied")
            QTimer.singleShot(1800, lambda: self.copy_button.setText("Copy"))

    def _on_autostart(self, enabled: bool) -> None:
        if not startup.supported():
            # Reflect reality rather than pretending the switch did something.
            self.autostart.blockSignals(True)
            self.autostart.setChecked(False)
            self.autostart.blockSignals(False)
            return
        try:
            if enabled:
                path = startup.enable()
                self.autostart_status.setText(f"Login entry written to {path}")
            else:
                startup.disable()
                self.autostart_status.setText("Login entry removed.")
        except OSError as exc:
            self.autostart_status.setText(f"Could not change the login entry: {exc}")

    def _refresh_startup(self) -> None:
        """Show what is actually on disk, not what was last clicked."""
        self.launch_command.setText(startup.steam_launch_command())

        state = startup.read_steam_state()
        if state.config_path is None:
            self.steam_status.setText("No Steam install found to check.")
        elif state.already_hooked:
            self.steam_status.setText("Warframe's launch options already run voidsight.")
        elif state.launch_options:
            self.steam_status.setText(
                f"Warframe currently launches with: {state.launch_options}"
            )
        else:
            self.steam_status.setText("Warframe has no launch options set.")

        supported = startup.supported()
        self.autostart.setEnabled(supported)
        self.autostart.blockSignals(True)
        self.autostart.setChecked(supported and startup.is_enabled())
        self.autostart.blockSignals(False)
        self.autostart_status.setText(
            startup.unsupported_reason()
            or (
                f"Enabled: {startup.autostart_path()}"
                if startup.is_enabled()
                else "Not enabled."
            )
        )

    def _debug_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(style.SPACE_L, style.SPACE_M, style.SPACE_L, style.SPACE_M)
        layout.setSpacing(style.SPACE_S)

        blurb = QLabel(
            "Trigger a read by hand instead of waiting for a reward screen. "
            "Useful for checking the setup, and the only route on a machine that "
            "cannot capture the game."
        )
        blurb.setObjectName("hint")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        row = QHBoxLayout()
        row.setSpacing(style.SPACE_S)
        self.scan_button = QPushButton("  Scan now")
        self.scan_button.setIcon(icons.get("scan", style.active().muted))
        self.scan_button.clicked.connect(self.scan_requested)
        row.addWidget(self.scan_button)

        self.open_button = QPushButton("  Open screenshot…")
        self.open_button.setIcon(icons.get("screenshot", style.active().muted))
        self.open_button.clicked.connect(self._pick_file)
        row.addWidget(self.open_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.debug_status = QLabel("")
        self.debug_status.setObjectName("muted")
        self.debug_status.setWordWrap(True)
        layout.addWidget(self.debug_status)

        self.source_label = QLabel("")
        self.source_label.setObjectName("hint")
        layout.addWidget(self.source_label)
        return panel

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        return label

    def _pick_file(self) -> None:
        from pathlib import Path

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open a reward screenshot",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if path:
            self.file_requested.emit(path)

    def reload(self) -> None:
        """Show what is currently in effect."""
        scanner = self.session.scanner
        config = self.session.config
        current = scanner.theme.name if scanner.theme else None
        self.theme.setCurrentIndex(max(0, self.theme.findData(current)))
        self.scale.setValue(round(scanner.config.ui_scale * 100))
        self.prefer.setCurrentIndex(max(0, self.prefer.findData(config.prefer)))

        self.overlay_enabled.setChecked(config.overlay)
        self.overlay_corner.setCurrentIndex(
            max(0, self.overlay_corner.findData(config.overlay_corner))
        )
        self.overlay_seconds.setValue(config.overlay_seconds)

        self.overlay_screen.clear()
        for index, screen in enumerate(QGuiApplication.screens()):
            size = screen.geometry()
            self.overlay_screen.addItem(
                f"{index + 1}: {screen.name()} ({size.width()}x{size.height()})", userData=index
            )
        self.overlay_screen.setCurrentIndex(
            min(config.overlay_screen, max(0, self.overlay_screen.count() - 1))
        )

        self._refresh_startup()

        backend = self.session.backend
        self.scan_button.setEnabled(backend is not None)
        self.source_label.setText(
            f"Frame source: {backend.name}" if backend else "No capture backend on this machine."
        )

    def set_overlay_limitation(self, message: str | None) -> None:
        self.overlay_note.setText(message or "")
        self.overlay_note.setVisible(bool(message))

    def set_debug_status(self, message: str, *, error: bool = False) -> None:
        palette = style.active()
        self.debug_status.setStyleSheet(
            f"color: {palette.bad};" if error else f"color: {palette.muted};"
        )
        self.debug_status.setText(message)

    def set_busy(self, busy: bool) -> None:
        self.scan_button.setEnabled(not busy and self.session.backend is not None)
        self.open_button.setEnabled(not busy)
        self.scan_button.setText("  Scanning…" if busy else "  Scan now")

    def show_preview(self) -> None:
        if self.overlay is None:
            return
        self._apply_overlay_settings()
        self.overlay.show_scan(SAMPLE_SCAN)

    def _apply_overlay_settings(self) -> None:
        config = self.session.config
        config.overlay = self.overlay_enabled.isChecked()
        config.overlay_corner = self.overlay_corner.currentData() or "top-centre"
        config.overlay_screen = self.overlay_screen.currentData() or 0
        config.overlay_seconds = self.overlay_seconds.value()
        if self.overlay is not None:
            self.overlay.apply_settings(
                corner=config.overlay_corner,
                screen_index=config.overlay_screen,
                seconds=config.overlay_seconds,
                opacity=config.overlay_opacity,
            )

    def apply(self) -> None:
        try:
            self.session.scanner.set_theme(self.theme.currentData())
            self.session.scanner.set_ui_scale(self.scale.value() / 100.0)
            self.session.config.prefer = self.prefer.currentData()
            self._apply_overlay_settings()
            self.session.save_config()
        except (KeyError, ValueError) as exc:
            self.status.setStyleSheet(f"color: {style.active().bad};")
            self.status.setText(str(exc).strip("'"))
            return
        self.status.setStyleSheet(f"color: {style.active().good};")
        self.status.setText("Saved")
        self.changed.emit()
