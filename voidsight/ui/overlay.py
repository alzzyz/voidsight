"""The in-game overlay: reward prices drawn on top of Warframe.

Deliberately not a Wayland-native window. A Wayland client cannot place its own
surfaces — `move()` is ignored by the compositor — so a corner-anchored overlay
is impossible without the layer-shell protocol, which has no Python bindings.
Running as an X11 client under XWayland gives positioning, stacking and
click-through, and puts the overlay in the same X server Proton already runs the
game in. See `voidsight.ui.platform`.

The window never takes focus and never receives input: it must not be possible
for this to eat a click, a keypress or alt-tab in the middle of a mission.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from voidsight.ui import style

log = logging.getLogger(__name__)

CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right", "top-centre")

#: Space between the overlay and the screen edge.
MARGIN = 28


class OverlayCard(QFrame):
    """One reward, compressed to what is readable in a two-second glance."""

    def __init__(self, reward: dict[str, Any], is_best: bool) -> None:
        super().__init__()
        self.setObjectName("overlay-card")
        palette = style.active()
        border = palette.value if is_best else palette.border_strong
        self.setStyleSheet(
            f"""
            QFrame#overlay-card {{
                background: {_translucent(palette.background)};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(4)

        name = QLabel(reward.get("name") or "unreadable")
        name.setWordWrap(True)
        name.setFixedWidth(190)
        colour = palette.text if reward.get("matched") else palette.bad
        weight = 600 if is_best else 500
        name.setStyleSheet(f"color: {colour}; font-size: 13px; font-weight: {weight};")
        layout.addWidget(name)

        price = QHBoxLayout()
        price.setSpacing(5)
        platinum = reward.get("platinum")
        value = QLabel("—" if platinum is None else _short(platinum))
        value.setStyleSheet(
            f"color: {palette.value if is_best else palette.text};"
            " font-size: 27px; font-weight: 700;"
        )
        price.addWidget(value)
        unit = QLabel("p")
        unit.setStyleSheet(f"color: {palette.muted}; font-size: 13px;")
        price.addWidget(unit, alignment=Qt.AlignmentFlag.AlignBottom)
        price.addStretch(1)
        if reward.get("ducats") is not None:
            ducats = QLabel(f"{reward['ducats']}d")
            ducats.setStyleSheet(f"color: {palette.muted}; font-size: 13px;")
            price.addWidget(ducats, alignment=Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(price)

        notes = []
        if reward.get("vaulted"):
            notes.append("vaulted")
        if not reward.get("live") and reward.get("matched"):
            notes.append("cached price")
        if reward.get("ambiguous"):
            notes.append("uncertain")
        if notes:
            note = QLabel(" · ".join(notes))
            note.setStyleSheet(f"color: {palette.muted}; font-size: 10px;")
            layout.addWidget(note)


def _translucent(colour: str, alpha: int = 238) -> str:
    """A hex colour as rgba, so the overlay dims the game rather than hiding it."""
    value = colour.lstrip("#")
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha})"


def _short(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


class Overlay(QWidget):
    """A frameless, click-through, always-on-top strip of reward cards."""

    def __init__(
        self,
        *,
        corner: str = "top-centre",
        screen_index: int = 0,
        seconds: float = 12.0,
        opacity: float = 0.95,
    ) -> None:
        super().__init__()
        self.corner = corner
        self.screen_index = screen_index
        self.seconds = seconds

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Showing must never pull focus away from the game.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(opacity)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_scan(self, payload: dict[str, Any]) -> None:
        """Display a scan, then hide again once the reward screen is long gone."""
        rewards = payload.get("rewards") or []
        if not rewards:
            self.hide()
            return

        self._clear()
        best = payload.get("best")
        for index, reward in enumerate(rewards):
            self._layout.addWidget(OverlayCard(reward, index == best))

        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        if self.seconds > 0:
            self._timer.start(int(self.seconds * 1000))

    def reposition(self) -> None:
        """Anchor to a corner of the chosen screen.

        Only works because this is an X11 client; a Wayland client's own
        placement requests are ignored by the compositor.
        """
        screens = QGuiApplication.screens()
        if not screens:
            return
        screen = screens[min(self.screen_index, len(screens) - 1)]
        area = screen.geometry()
        size = self.sizeHint()

        if self.corner == "top-centre":
            x = area.x() + (area.width() - size.width()) // 2
            y = area.y() + MARGIN
        else:
            vertical, _, horizontal = self.corner.partition("-")
            x = (
                area.x() + MARGIN
                if horizontal == "left"
                else area.x() + area.width() - size.width() - MARGIN
            )
            y = (
                area.y() + MARGIN
                if vertical == "top"
                else area.y() + area.height() - size.height() - MARGIN
            )
        self.move(x, y)

    def apply_settings(
        self,
        *,
        corner: str | None = None,
        screen_index: int | None = None,
        seconds: float | None = None,
        opacity: float | None = None,
    ) -> None:
        if corner is not None:
            self.corner = corner
        if screen_index is not None:
            self.screen_index = screen_index
        if seconds is not None:
            self.seconds = seconds
        if opacity is not None:
            self.setWindowOpacity(opacity)
        if self.isVisible():
            self.reposition()

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
