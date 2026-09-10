"""Synthetic reward screens.

These are *not* a substitute for real screenshots — the font is wrong and so is
the art — but they exercise the whole pipeline (geometry, theme detection,
thresholding, band/column finding, OCR, matching) on any OS, which is what makes
development away from a Linux gaming box possible. Real screenshots go in
tests/fixtures and are what the numbers get tuned against.
"""

from __future__ import annotations

import cv2
import numpy as np

from voidsight.vision import locate as locate_module
from voidsight.vision import theme as theme_module

FONT = cv2.FONT_HERSHEY_DUPLEX


def render_reward_screen(
    names: list[str],
    *,
    theme_name: str = "Vitruvian",
    size: tuple[int, int] = (1920, 1080),
    ui_scale: float = 1.0,
    seed: int = 0,
    busy_background: bool = True,
    panel_backing: bool = True,
) -> np.ndarray:
    """Draw a fake relic reward screen: a row of icons with names beneath.

    `panel_backing` draws the dark translucent plate the game puts behind the
    reward cards. Leave it on for realistic fixtures; turning it off puts the
    names directly on top of scenery, which is harsher than anything the real
    UI does.
    """
    width, height = size
    theme = theme_module.get(theme_name)
    rng = np.random.default_rng(seed)

    frame = _background(width, height, rng, busy=busy_background)
    box = locate_module.panel_box((height, width), ui_scale)
    if panel_backing:
        _darken_panel(frame, box)
    scale = min(width / locate_module.BASE_WIDTH, height / locate_module.BASE_HEIGHT) * ui_scale

    count = max(1, len(names))
    cell_width = box.width / count
    icon_size = int(box.height * 0.5)
    text_baseline = box.y + int(box.height * 0.86)

    for index, name in enumerate(names):
        centre_x = int(box.x + cell_width * (index + 0.5))

        # Item icon: a bright blob, taller than a text line, to make sure the
        # locator does not mistake the icon row for the name row.
        top = box.y + int(box.height * 0.08)
        icon_colour = tuple(int(channel) for channel in rng.integers(120, 255, size=3))
        cv2.rectangle(
            frame,
            (centre_x - icon_size // 2, top),
            (centre_x + icon_size // 2, top + icon_size),
            icon_colour,
            thickness=-1,
        )

        text = name.upper()
        font_scale, thickness = _fit_text(text, cell_width * 0.92, scale)
        (text_width, _), _ = cv2.getTextSize(text, FONT, font_scale, thickness)
        cv2.putText(
            frame,
            text,
            (centre_x - text_width // 2, text_baseline),
            FONT,
            font_scale,
            theme.secondary,
            thickness,
            lineType=cv2.LINE_AA,
        )
    return frame


def _darken_panel(frame: np.ndarray, box: locate_module.Box, margin: int = 40) -> None:
    """Blend the reward area toward black, as the game's UI plate does."""
    top = max(0, box.y - margin)
    bottom = min(frame.shape[0], box.y + box.height + margin)
    left = max(0, box.x - margin)
    right = min(frame.shape[1], box.x + box.width + margin)
    region = frame[top:bottom, left:right].astype(np.float32)
    frame[top:bottom, left:right] = (region * 0.25).astype(np.uint8)


def _fit_text(text: str, max_width: float, scale: float) -> tuple[float, int]:
    """Largest font scale whose rendering fits the cell."""
    thickness = max(1, int(round(scale)))
    font_scale = 0.75 * scale
    for _ in range(24):
        (width, _), _ = cv2.getTextSize(text, FONT, font_scale, thickness)
        if width <= max_width or font_scale <= 0.2:
            break
        font_scale -= 0.03 * scale
    return font_scale, thickness


def _background(width: int, height: int, rng: np.random.Generator, *, busy: bool) -> np.ndarray:
    """A dark, uneven backdrop, optionally littered with bright shapes.

    The real mission-end screen is busy and bright in places; a flat black
    background would make the thresholding look better than it is.
    """
    gradient = np.linspace(10, 60, height, dtype=np.float32)[:, None]
    frame = np.repeat(gradient[:, :, None], 3, axis=2) * np.array([0.6, 0.8, 1.0], dtype=np.float32)
    frame = np.repeat(frame, width, axis=1)

    if busy:
        for _ in range(40):
            x, y = int(rng.integers(0, width)), int(rng.integers(0, height))
            radius = int(rng.integers(20, 160))
            colour = tuple(int(channel) for channel in rng.integers(30, 200, size=3))
            cv2.circle(frame, (x, y), radius, colour, thickness=-1)
        frame += rng.normal(0, 6, size=frame.shape).astype(np.float32)

    return np.clip(frame, 0, 255).astype(np.uint8)
