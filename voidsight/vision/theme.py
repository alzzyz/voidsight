"""Warframe UI themes, and turning themed text into a black-and-white image.

Warframe recolours its whole UI per theme, so "which pixels are text" depends on
the player's chosen theme. Each theme has a primary and secondary accent colour;
reward names are drawn in one of the two. Detecting the theme first, then keeping
only pixels near those two hues, isolates the text far better than a plain
luminance threshold — the reward panel sits on top of the mission-end background,
which is bright and busy.

The colour table is derived from WFInfo (Apache-2.0); see NOTICE.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

import numpy as np

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Theme:
    name: str
    primary: RGB
    secondary: RGB

    def hues(self) -> tuple[float, float]:
        return (_hue(self.primary), _hue(self.secondary))


THEMES: tuple[Theme, ...] = (
    Theme("Vitruvian", (190, 169, 102), (245, 227, 173)),
    Theme("Stalker", (153, 31, 35), (255, 61, 51)),
    Theme("Baruuk", (238, 193, 105), (236, 211, 162)),
    Theme("Corpus", (35, 201, 245), (111, 229, 253)),
    Theme("Fortuna", (57, 105, 192), (255, 115, 230)),
    Theme("Grineer", (255, 189, 102), (255, 224, 153)),
    Theme("Lotus", (36, 184, 242), (255, 241, 191)),
    Theme("Nidus", (140, 38, 92), (245, 73, 93)),
    Theme("Orokin", (20, 41, 29), (178, 125, 5)),
    Theme("Tenno", (9, 78, 106), (6, 106, 74)),
    Theme("HighContrast", (2, 127, 217), (255, 255, 0)),
    Theme("Legacy", (255, 255, 255), (232, 213, 93)),
    Theme("Equinox", (158, 159, 167), (232, 227, 227)),
    Theme("DarkLotus", (140, 119, 147), (189, 169, 237)),
    Theme("Zephyr", (253, 132, 2), (255, 53, 0)),
)

BY_NAME = {theme.name.lower(): theme for theme in THEMES}

#: Themes whose accents are grey/white, where hue carries no information and
#: only lightness separates text from background.
_ACHROMATIC = {"Equinox", "Legacy"}


def _hue(rgb: RGB) -> float:
    r, g, b = (channel / 255 for channel in rgb)
    return colorsys.rgb_to_hls(r, g, b)[0] * 360.0


def get(name: str) -> Theme:
    try:
        return BY_NAME[name.lower()]
    except KeyError:
        known = ", ".join(theme.name for theme in THEMES)
        raise KeyError(f"unknown theme {name!r}; known: {known}") from None


def hls(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised RGB -> HLS. Hue in degrees, lightness and saturation in 0..1."""
    rgb = frame.astype(np.float32) / 255.0
    high = rgb.max(axis=-1)
    low = rgb.min(axis=-1)
    span = high - low

    lightness = (high + low) / 2.0
    with np.errstate(divide="ignore", invalid="ignore"):
        saturation = np.where(
            span == 0,
            0.0,
            span / np.where(lightness < 0.5, high + low, 2.0 - high - low),
        )
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        hue = np.select(
            [span == 0, high == r, high == g],
            [0.0, ((g - b) / span) % 6.0, (b - r) / span + 2.0],
            default=(r - g) / span + 4.0,
        ) * 60.0
    return np.nan_to_num(hue), lightness, np.nan_to_num(saturation)


def hue_distance(a: np.ndarray | float, b: float) -> np.ndarray | float:
    """Shortest angular distance between hues, in degrees."""
    diff = np.abs(np.asarray(a, dtype=np.float32) - b) % 360.0
    return np.minimum(diff, 360.0 - diff)


def text_mask(frame: np.ndarray, theme: Theme) -> np.ndarray:
    """Return a boolean mask of pixels that look like this theme's text."""
    return mask_from_hls(hls(frame), theme)


def mask_from_hls(
    channels: tuple[np.ndarray, np.ndarray, np.ndarray], theme: Theme
) -> np.ndarray:
    """Mask of this theme's text pixels, given precomputed HLS channels.

    Text is anti-aliased, so we accept a hue window around either accent rather
    than exact colour equality, and require the pixel to be reasonably light —
    the panel's own background uses the same hues at low lightness. Callers that
    test many themes against one crop convert to HLS once and reuse it.
    """
    hue, lightness, saturation = channels

    if theme.name in _ACHROMATIC:
        return (lightness > 0.55) & (saturation < 0.35)

    primary, secondary = theme.hues()
    near_hue = np.minimum(hue_distance(hue, primary), hue_distance(hue, secondary)) < 20.0
    lit = lightness > 0.30
    saturated = saturation > 0.20
    # Very bright pixels pass regardless of hue: several themes draw the name in
    # near-white with only a tint of the accent.
    washed_out = (lightness > 0.75) & (saturation < 0.25)
    return (near_hue & lit & saturated) | washed_out


def to_binary(mask: np.ndarray) -> np.ndarray:
    """Boolean text mask -> black text on white, which is what Tesseract wants."""
    return np.where(mask, 0, 255).astype(np.uint8)


def binarize(frame: np.ndarray, theme: Theme) -> np.ndarray:
    """Threshold a reward-panel crop for a known theme."""
    return to_binary(text_mask(frame, theme))
