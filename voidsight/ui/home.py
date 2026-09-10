"""The home page: reward prices, or a considered empty state.

Home shows results and nothing else. Everything configurable lives behind the
settings icon, so that during the ten seconds a reward screen is up there is
exactly one thing on screen.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from voidsight.ui import style
from voidsight.ui.artwork import ArtworkLoader
from voidsight.ui.rewards import RewardCard


class EmptyState(QWidget):
    """What the app looks like almost all of the time. Worth doing properly."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(10)
        layout.addStretch(1)

        self.headline = QLabel("Waiting for a reward screen")
        self.headline.setObjectName("heading")
        self.headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.headline)

        self.detail = QLabel("")
        self.detail.setObjectName("muted")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        layout.addStretch(1)

    def describe(self, *, game_running: bool, watching: bool, has_backend: bool) -> None:
        """Say what the app is actually waiting for, which differs by setup."""
        if not has_backend:
            self.headline.setText("Nothing to capture from")
            self.detail.setText(
                "No capture backend on this machine. Open a screenshot from "
                "Settings › Debug to read one."
            )
        elif not game_running:
            self.headline.setText("Warframe is not running")
            self.detail.setText("Start the game and crack a relic; rewards will appear here.")
        elif watching:
            self.headline.setText("Waiting for a reward screen")
            self.detail.setText("Watching EE.log. Crack a relic and the prices land here.")
        else:
            self.headline.setText("Waiting for a reward screen")
            self.detail.setText(
                "Not following EE.log, so scans have to be triggered from Settings › Debug."
            )


class HomeView(QWidget):
    def __init__(self, artwork: ArtworkLoader | None = None, catalog=None) -> None:
        super().__init__()
        self.artwork = artwork
        self.catalog = catalog
        self._cards: dict[str, RewardCard] = {}
        if artwork is not None:
            artwork.loaded.connect(self._on_artwork)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(24, 20, 24, 24)
        self._layout.setSpacing(14)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(style.SPACE_S)
        self.summary = QLabel("")
        self.summary.setObjectName("faint")
        summary_row.addWidget(self.summary)
        self.provenance = QLabel("")
        self.provenance.setObjectName("badge")
        self.provenance.hide()
        summary_row.addWidget(self.provenance)
        summary_row.addStretch(1)
        self._layout.addLayout(summary_row)

        self.cards = QHBoxLayout()
        self.cards.setSpacing(16)
        self._layout.addLayout(self.cards, stretch=1)

        self.empty = EmptyState()
        self._layout.addWidget(self.empty, stretch=1)
        self.summary.hide()

    def describe_idle(self, **state: bool) -> None:
        self.empty.describe(**state)

    def show_scan(self, payload: dict[str, Any]) -> None:
        self._clear()
        rewards = payload.get("rewards") or []
        if not rewards:
            self.empty.show()
            self.summary.hide()
            self.empty.headline.setText("Nothing found in that frame")
            self.empty.detail.setText(payload.get("reason") or "No reward names were readable.")
            return

        self.empty.hide()
        best = payload.get("best")
        for index, reward in enumerate(rewards):
            card = RewardCard(reward, index == best, self._pixmap_for(reward))
            if part_name := self._part_name(reward):
                self._cards[part_name] = card
            self.cards.addWidget(card)

        parts = [payload.get("relic") or "Rewards"]
        if payload.get("confidence") is not None:
            parts.append(f"{payload['confidence']:.0%} read confidence")
        if payload.get("source"):
            parts.append(payload["source"])
        if payload.get("at"):
            parts.append(payload["at"][11:19])
        self.summary.setText("   ·   ".join(parts))
        self.summary.show()

        # A saved frame reads exactly like a live one — the confidence figure
        # says nothing about where the pixels came from — so say which it was.
        replayed = not payload.get("live_capture")
        self.provenance.setText("NOT FROM GAME" if replayed else "")
        self.provenance.setStyleSheet(f"color: {style.active().warn};" if replayed else "")
        self.provenance.setVisible(replayed)

    def _part_name(self, reward: dict[str, Any]) -> str | None:
        """The catalog key for a reward, which artwork is indexed by."""
        if not (self.catalog and reward.get("matched")):
            return None
        part = self.catalog.lookup(reward["name"])
        return part.name if part else None

    def _pixmap_for(self, reward: dict[str, Any]):
        if self.artwork is None or not (self.catalog and reward.get("matched")):
            return None
        part = self.catalog.lookup(reward["name"])
        return self.artwork.pixmap(part, 44) if part else None

    def _on_artwork(self, part_name: str, _pixmap) -> None:
        """Fill in a card whose icon arrived after it was drawn."""
        if card := self._cards.get(part_name):
            part = self.catalog.lookup(part_name) if self.catalog else None
            if part is not None:
                card.set_artwork(self.artwork.pixmap(part, 44))

    def _clear(self) -> None:
        self._cards.clear()
        while self.cards.count():
            item = self.cards.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
