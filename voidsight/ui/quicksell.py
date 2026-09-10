"""Quick sell: confirm what you took, then list it.

The list is built from scans, so it shows every reward that was *offered* and
asks which you kept. Tapping a row is that confirmation and the mark-for-sale in
one action, because they are the same decision.

Creating the listings needs a warframe.market account, which is not built yet;
the button says so rather than being hidden, so the flow reads end to end.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from voidsight.session import Drop, MissionGroup, SessionLedger
from voidsight.ui import icons, style
from voidsight.ui.artwork import ArtworkLoader


def _plat(value: float | None) -> str:
    if value is None:
        return "—"
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


class DropRow(QFrame):
    """One item: confirm it, set how many, see what it is worth."""

    toggled = Signal()
    quantity_changed = Signal()

    def __init__(self, drop: Drop, artwork: QPixmap | None = None) -> None:
        super().__init__()
        self.drop = drop
        self.setObjectName("row")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(style.SPACE_M, style.SPACE_S, style.SPACE_M, style.SPACE_S)
        layout.setSpacing(style.SPACE_M)

        self.check = QLabel()
        self.check.setFixedSize(20, 20)
        layout.addWidget(self.check)

        self.artwork = QLabel()
        self.artwork.setFixedSize(26, 26)
        self.artwork.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if artwork is not None and not artwork.isNull():
            self.artwork.setPixmap(artwork)
        layout.addWidget(self.artwork)

        self.name = QLabel(drop.name)
        self.name.setObjectName("name")
        layout.addWidget(self.name, stretch=1)

        if drop.vaulted:
            vaulted = QLabel("VAULTED")
            vaulted.setObjectName("badge")
            vaulted.setStyleSheet(f"color: {style.active().value};")
            layout.addWidget(vaulted)

        self.quantity = QLabel()
        self.quantity.setObjectName("muted")
        self.quantity.setFixedWidth(28)
        self.quantity.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.minus = QPushButton("−")
        self.plus = QPushButton("+")
        for button in (self.minus, self.plus):
            button.setObjectName("stepper")
            button.setFixedSize(24, 24)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.minus.clicked.connect(lambda: self._step(-1))
        self.plus.clicked.connect(lambda: self._step(1))
        layout.addWidget(self.minus)
        layout.addWidget(self.quantity)
        layout.addWidget(self.plus)

        self.price = QLabel()
        self.price.setObjectName("price")
        self.price.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.price.setFixedWidth(96)
        layout.addWidget(self.price)

        self.ducats = QLabel(f"{drop.ducats}d" if drop.ducats else "")
        self.ducats.setObjectName("faint")
        self.ducats.setFixedWidth(44)
        self.ducats.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.ducats)

        self.refresh()

    def toggle(self) -> None:
        """Mark or unmark this drop. Untradeable items cannot be marked."""
        if not self.drop.sellable:
            return
        self.drop.selected = not self.drop.selected
        self.refresh()
        self.toggled.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt's casing
        # The whole row is the target: a 20px checkbox is a poor thing to aim at
        # when you have seconds to spare and a hand on WASD.
        self.toggle()
        super().mousePressEvent(event)

    def _step(self, delta: int) -> None:
        self.drop.quantity = max(1, min(99, self.drop.quantity + delta))
        self.refresh()
        self.quantity_changed.emit()

    def refresh(self) -> None:
        palette = style.active()
        selected = self.drop.selected
        self.setProperty("selected", "true" if selected else "false")
        self.setProperty("sellable", "true" if self.drop.sellable else "false")
        self.style().unpolish(self)
        self.style().polish(self)

        if not self.drop.sellable:
            glyph = icons.get("blocked", palette.faint)
        elif selected:
            glyph = icons.get("checked", palette.structure)
        else:
            glyph = icons.get("unchecked", palette.muted)
        self.check.setPixmap(glyph.pixmap(20, 20))

        self.quantity.setText(f"×{self.drop.quantity}")
        for button in (self.minus, self.plus):
            button.setEnabled(self.drop.sellable)

        if not self.drop.tradeable:
            self.price.setText("not tradeable")
            self.price.setStyleSheet(f"color: {palette.faint}; font-size: 12px;")
        else:
            total = self.drop.sale_value
            self.price.setText(f"{_plat(total)} p")
            colour = palette.value if selected else palette.text
            self.price.setStyleSheet(f"color: {colour}; font-size: 15px; font-weight: 600;")


class GroupBlock(QFrame):
    """One reward screen's worth of drops."""

    changed = Signal()

    def __init__(self, group: MissionGroup, pixmaps: dict[str, QPixmap] | None = None) -> None:
        super().__init__()
        self.group = group
        pixmaps = pixmaps or {}
        self.setObjectName("panel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(style.SPACE_S, style.SPACE_M, style.SPACE_S, style.SPACE_S)
        layout.setSpacing(style.SPACE_XS)

        header = QHBoxLayout()
        header.setContentsMargins(style.SPACE_S, 0, style.SPACE_S, 0)
        title = QLabel(group.label)
        title.setObjectName("section")
        header.addWidget(title)
        header.addStretch(1)
        if not group.live_capture:
            flag = QLabel("NOT FROM GAME")
            flag.setObjectName("badge")
            flag.setStyleSheet(f"color: {style.active().warn};")
            flag.setToolTip(
                "Read from a saved screenshot, not the running game. Listing these "
                "would offer items you may not own."
            )
            header.addWidget(flag)

        meta = QLabel(f"{group.time_text}  ·  {group.source}")
        meta.setObjectName("faint")
        header.addWidget(meta)
        layout.addLayout(header)

        self.rows = [DropRow(drop, pixmaps.get(drop.name)) for drop in group.drops]
        for row in self.rows:
            row.toggled.connect(self.changed)
            row.quantity_changed.connect(self.changed)
            layout.addWidget(row)


class QuickSellView(QWidget):
    """The page: a summary bar, then one block per reward screen."""

    def __init__(
        self,
        ledger: SessionLedger,
        artwork: ArtworkLoader | None = None,
        catalog=None,
    ) -> None:
        super().__init__()
        self.ledger = ledger
        self.artwork = artwork
        self.catalog = catalog
        if artwork is not None:
            # Icons arriving late should appear without the user doing anything.
            artwork.loaded.connect(lambda *_: self.refresh())
        self.setObjectName("page")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(style.SPACE_XL, style.SPACE_L, style.SPACE_XL, style.SPACE_L)
        layout.setSpacing(style.SPACE_M)

        layout.addWidget(self._summary_bar())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(style.SPACE_M)
        self.body_layout.addStretch(1)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, stretch=1)

        self.empty = QLabel(
            "Nothing scanned yet.\n\n"
            "Rewards read from the game land here. Tap the ones you actually took "
            "to mark them for sale — the app cannot tell which of the four you picked."
        )
        self.empty.setObjectName("hint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty, stretch=1)

        self.refresh()

    def _summary_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(style.SPACE_M)

        self.summary = QLabel("")
        self.summary.setObjectName("heading")
        layout.addWidget(self.summary)
        layout.addStretch(1)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self._on_clear)
        layout.addWidget(self.clear_button)

        self.list_button = QPushButton("  Create sell orders")
        self.list_button.setObjectName("primary")
        self.list_button.setIcon(icons.get("sell", style.active().muted))
        self.list_button.setEnabled(False)
        self.list_button.setToolTip(
            "Posting listings needs a warframe.market account. Sign-in is not built yet — "
            "prices shown here come from the public API."
        )
        layout.addWidget(self.list_button)
        return bar

    def _on_clear(self) -> None:
        self.ledger.clear()
        self.refresh()

    def refresh(self) -> None:
        """Rebuild from the ledger. Cheap: a session holds tens of rows, not thousands."""
        while self.body_layout.count() > 1:
            item = self.body_layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()

        groups = self.ledger.groups
        for index, group in enumerate(groups):
            block = GroupBlock(group, self._pixmaps_for(group))
            block.changed.connect(self.refresh_summary)
            self.body_layout.insertWidget(index, block)

        self.scroll.setVisible(bool(groups))
        self.empty.setVisible(not groups)
        self.refresh_summary()

    def _pixmaps_for(self, group: MissionGroup) -> dict[str, QPixmap]:
        """Icons for a group's drops, by display name. Missing ones are fine."""
        if self.artwork is None or self.catalog is None:
            return {}
        found = {}
        for drop in group.drops:
            part = self.catalog.lookup(drop.name)
            if part is None:
                continue
            if pixmap := self.artwork.pixmap(part, 26):
                found[drop.name] = pixmap
        return found

    def refresh_summary(self) -> None:
        selected = self.ledger.selected_drops()
        count = sum(drop.quantity for drop in selected)
        total = self.ledger.total_platinum()
        if not self.ledger.groups:
            self.summary.setText("Quick sell")
        elif not selected:
            self.summary.setText("Quick sell   ·   nothing marked yet")
        else:
            self.summary.setText(f"Quick sell   ·   {count} item(s)   ·   ~{_plat(total)} plat")
        self.clear_button.setEnabled(bool(self.ledger.groups))
