"""Shared components.

Qt's stock widgets are correct but generic, and a few of them read badly in a
dark app — a checkbox in particular is a square that fills in, which says
"tick this item in a list" rather than "this feature is on". These are the small
number of replacements worth owning, built on Qt's own classes so they keep
focus, keyboard handling and accessibility.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QPushButton, QWidget

from voidsight.ui import style


class ToggleSwitch(QAbstractButton):
    """An on/off switch: a track and a knob that slides.

    A switch states that something is *on*, and takes effect immediately. A
    checkbox states that something is *selected*, pending a confirm. This app's
    booleans are the former.
    """

    TRACK_WIDTH = 42
    TRACK_HEIGHT = 22
    KNOB_MARGIN = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._offset = 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(style.DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(self.TRACK_WIDTH, style.CONTROL_HEIGHT)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    #: 0 when off, 1 when on. Animated, so the knob slides rather than jumps.
    offset = Property(float, _get_offset, _set_offset)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt's casing
        super().setChecked(checked)
        # Setting state programmatically should not animate from nowhere.
        self._offset = 1.0 if checked else 0.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's casing
        palette = style.active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        track = QRectF(
            0,
            (self.height() - self.TRACK_HEIGHT) / 2,
            self.TRACK_WIDTH,
            self.TRACK_HEIGHT,
        )
        off = QColor(palette.surface_3)
        on = QColor(palette.structure)
        blend = QColor(
            int(off.red() + (on.red() - off.red()) * self._offset),
            int(off.green() + (on.green() - off.green()) * self._offset),
            int(off.blue() + (on.blue() - off.blue()) * self._offset),
        )
        painter.setPen(
            QColor(palette.structure if self.hasFocus() else palette.border)
        )
        painter.setBrush(blend)
        radius = track.height() / 2
        painter.drawRoundedRect(track, radius, radius)

        knob_size = self.TRACK_HEIGHT - self.KNOB_MARGIN * 2
        travel = self.TRACK_WIDTH - knob_size - self.KNOB_MARGIN * 2
        knob = QRectF(
            track.left() + self.KNOB_MARGIN + travel * self._offset,
            track.top() + self.KNOB_MARGIN,
            knob_size,
            knob_size,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette.text if self._offset > 0.5 else palette.muted))
        painter.drawEllipse(knob)
        painter.end()


class Segmented(QWidget):
    """A row of exclusive buttons — one choice, all options visible.

    Better than a dropdown when there are few options and comparing them
    matters, which is exactly the case for interface size.
    """

    changed = Signal(str)

    def __init__(
        self,
        options: tuple[str, ...],
        parent: QWidget | None = None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QButtonGroup

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for index, option in enumerate(options):
            button = QPushButton(option)
            button.setObjectName("segment-compact" if compact else "segment")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if index == 0:
                button.setProperty("edge", "left")
            elif index == len(options) - 1:
                button.setProperty("edge", "right")
            self.group.addButton(button, index)
            layout.addWidget(button)
        layout.addStretch(1)

        self.options = options
        self.group.idToggled.connect(self._on_toggle)

    def _on_toggle(self, index: int, checked: bool) -> None:
        if checked:
            self.changed.emit(self.options[index])

    def value(self) -> str:
        button = self.group.checkedButton()
        return button.text() if button else self.options[0]

    def set_value(self, option: str, *, silent: bool = False) -> None:
        """Select an option. `silent` reflects stored state without announcing a
        change, so loading settings cannot look like the user chose something."""
        if option not in self.options:
            return
        if silent:
            self.group.blockSignals(True)
        try:
            self.group.button(self.options.index(option)).setChecked(True)
        finally:
            if silent:
                self.group.blockSignals(False)
