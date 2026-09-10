"""One card per drop. The page that arranges them lives in `home.py`."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

from voidsight.ui import style


def _format_platinum(value: float | None) -> str:
    if value is None:
        return "—"
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


class RewardCard(QFrame):
    """One reward. Reads top to bottom: what it is, what it is worth, why."""

    def __init__(
        self,
        reward: dict[str, Any],
        is_best: bool,
        artwork: QPixmap | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setProperty("best", "true" if is_best else "false")
        self.setProperty("matched", "true" if reward.get("matched") else "false")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(style.SPACE_S)
        self.artwork = QLabel()
        self.artwork.setFixedSize(44, 44)
        self.artwork.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_artwork(artwork)
        header.addWidget(self.artwork, alignment=Qt.AlignmentFlag.AlignTop)

        name = QLabel(reward.get("name") or "unreadable")
        name.setObjectName("name")
        name.setWordWrap(True)
        header.addWidget(name, stretch=1)
        if is_best:
            ribbon = QLabel("BEST")
            ribbon.setObjectName("ribbon")
            ribbon.setAlignment(Qt.AlignmentFlag.AlignTop)
            header.addWidget(ribbon, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        price = QHBoxLayout()
        price.setSpacing(6)
        platinum = QLabel(_format_platinum(reward.get("platinum")))
        platinum.setObjectName("platinum")
        platinum.setProperty("best", "true" if is_best else "false")
        price.addWidget(platinum, alignment=Qt.AlignmentFlag.AlignBottom)
        unit = QLabel("plat · lowest" if reward.get("live") else "plat · avg")
        unit.setObjectName("muted")
        price.addWidget(unit, alignment=Qt.AlignmentFlag.AlignBottom)
        price.addStretch(1)
        layout.addLayout(price)

        for label, value in self._details(reward):
            row = QHBoxLayout()
            left = QLabel(label)
            left.setObjectName("muted")
            row.addWidget(left)
            row.addStretch(1)
            row.addWidget(QLabel(value))
            layout.addLayout(row)

        layout.addStretch(1)
        badges = QHBoxLayout()
        badges.setSpacing(8)
        for text, colour in self._badges(reward):
            badge = QLabel(text.upper())
            badge.setObjectName("badge")
            badge.setStyleSheet(f"color: {colour};")
            badges.addWidget(badge)
        badges.addStretch(1)
        layout.addLayout(badges)

    def set_artwork(self, pixmap: QPixmap | None) -> None:
        """Show the item's icon. Absence is normal — it may still be downloading."""
        if pixmap is None or pixmap.isNull():
            self.artwork.clear()
            return
        self.artwork.setPixmap(pixmap)

    def _details(self, reward: dict[str, Any]) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if reward.get("ducats") is not None:
            ratio = reward.get("ducats_per_platinum")
            suffix = f" · {ratio}/p" if ratio is not None else ""
            rows.append(("Ducats", f"{reward['ducats']}{suffix}"))
        if reward.get("average") is not None:
            volume = reward.get("volume")
            suffix = f" · {volume} sold today" if volume is not None else ""
            rows.append(("Average", f"{_format_platinum(reward['average'])}p{suffix}"))
        offers = reward.get("offers") or []
        if len(offers) > 1:
            rows.append(("Next offers", "  ".join(f"{value}p" for value in offers[1:5])))
        if not reward.get("matched") and reward.get("raw_text"):
            rows.append(("Read as", reward["raw_text"]))
        if reward.get("error"):
            rows.append(("Note", reward["error"]))
        return rows

    def _badges(self, reward: dict[str, Any]) -> list[tuple[str, str]]:
        if not reward.get("matched"):
            return [("not recognised", style.active().bad)]
        palette = style.active()
        badges = [("live", palette.good) if reward.get("live") else ("cached", palette.faint)]
        if reward.get("vaulted"):
            badges.append(("vaulted", palette.value))
        if reward.get("tradeable") is False:
            badges.append(("untradeable", palette.faint))
        if reward.get("ambiguous"):
            badges.append(("uncertain", palette.warn))
        return badges
