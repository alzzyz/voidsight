"""The header: identity on the left, state and settings on the right.

The two chips answer the questions you would otherwise have to go looking for —
is the game running, and am I signed in to warframe.market — without either
becoming a control. Settings is the only thing here you can press.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from voidsight.config import client_size_labels
from voidsight.game import GameState
from voidsight.ui import icons, style
from voidsight.ui.widgets import Segmented


class StatusChip(QWidget):
    """An icon and a word, describing one piece of state."""

    def __init__(self, text: str = "") -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        self.icon = QLabel()
        self.icon.setFixedSize(16, 16)
        layout.addWidget(self.icon)

        self.label = QLabel(text)
        self.label.setObjectName("chip")
        layout.addWidget(self.label)

    def set(
        self,
        text: str,
        colour: str,
        state: str,
        *,
        hollow: bool = False,
        icon: str | None = None,
    ) -> None:
        self.label.setText(text)
        self.label.setProperty("state", state)
        # Qt does not restyle on property changes without being asked.
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)
        glyph = icons.get(icon, colour) if icon else icons.dot(colour, hollow=hollow)
        self.icon.setPixmap(glyph.pixmap(16, 16))
        self.setToolTip(text)


class Header(QFrame):
    settings_toggled = Signal(bool)
    #: "home" or "quicksell".
    page_selected = Signal(str)
    #: "S", "M", "L" or "XL" — the size of this window.
    client_size_selected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("header")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 12, 18, 12)
        layout.setSpacing(16)

        logo = QLabel(f"void<span style='color:{style.active().value}'>sight</span>")
        logo.setObjectName("logo")
        logo.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(logo)

        # Page tabs sit with the logo, not with the status chips: they are what
        # the app *is*, while the right-hand side is what it currently knows.
        layout.addSpacing(style.SPACE_L)
        self.tabs = QButtonGroup(self)
        self.tabs.setExclusive(True)
        for key, label, icon_name in (
            ("home", "Home", "home"),
            ("quicksell", "Quick sell", "quicksell"),
        ):
            button = QPushButton(f"  {label}")
            button.setObjectName("tab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIcon(icons.get(icon_name, style.active().muted))
            button.setProperty("page", key)
            self.tabs.addButton(button)
            layout.addWidget(button)
        self.tabs.buttons()[0].setChecked(True)
        self.tabs.buttonToggled.connect(self._on_tab)

        layout.addStretch(1)

        self.game = StatusChip()
        layout.addWidget(self.game)

        self.market = StatusChip()
        layout.addWidget(self.market)

        # The window's own size is a preference you change while looking at the
        # window, so it sits out here rather than behind the settings icon.
        self.client_size = Segmented(tuple(client_size_labels()), compact=True)
        self.client_size.changed.connect(self.client_size_selected)
        self.client_size.setToolTip("Size of this window")
        layout.addWidget(self.client_size)

        self.settings = QPushButton("  Settings")
        self.settings.setObjectName("nav")
        self.settings.setCheckable(True)
        self.settings.setIcon(icons.gear(style.active().muted))
        self.settings.toggled.connect(self._on_toggle)
        layout.addWidget(self.settings)

        self.set_game_state(GameState(running=False))
        self.set_market_state(connected=False)

    def _on_tab(self, button, checked: bool) -> None:
        if not checked:
            return
        self._restyle_tabs()
        self.page_selected.emit(button.property("page"))

    def _restyle_tabs(self) -> None:
        palette = style.active()
        for button in self.tabs.buttons():
            name = "home" if button.property("page") == "home" else "quicksell"
            colour = palette.structure if button.isChecked() else palette.muted
            button.setIcon(icons.get(name, colour))

    def select_page(self, key: str) -> None:
        """Move the tab selection without emitting, for programmatic navigation."""
        for button in self.tabs.buttons():
            if button.property("page") == key:
                self.tabs.blockSignals(True)
                button.setChecked(True)
                self.tabs.blockSignals(False)
                self._restyle_tabs()
                return

    def _on_toggle(self, checked: bool) -> None:
        self.settings.setIcon(
            icons.gear(style.active().structure if checked else style.active().muted)
        )
        self.settings_toggled.emit(checked)

    def set_game_state(self, state: GameState) -> None:
        if state.running:
            self.game.set(state.summary, style.active().good, "good")
        else:
            self.game.set("Warframe not detected", style.active().faint, "idle", hollow=True)
        if state.signals:
            self.game.setToolTip(f"{state.summary} — {state.detail}")

    def set_market_state(self, *, connected: bool, name: str | None = None) -> None:
        if connected:
            self.market.set(name or "Connected", style.active().good, "good", icon="connected")
        else:
            # Signing in is not built yet; the chip states the fact and no more.
            self.market.set("NOT CONNECTED", style.active().faint, "idle", icon="disconnected")
            self.market.setToolTip(
                "warframe.market sign-in is not implemented yet. Prices are public "
                "data and work without it."
            )
