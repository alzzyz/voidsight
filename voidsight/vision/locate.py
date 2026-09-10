"""Find the reward-name text inside a frame.

The reward panel sits at a fixed place in Warframe's UI, so a scaled crop gets
us close (constants derived from WFInfo; see NOTICE). Inside that crop we do not
assume where the names are relative to the item icons. Instead we find every
band of text-like rows and keep the one that splits into 2-4 evenly spaced
columns — that column structure is what actually distinguishes the row of reward
names from icons, borders and squadmate labels.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from voidsight.vision import theme as theme_module

# Reward panel geometry at 1920x1080, from WFInfo.
PIXEL_REWARD_WIDTH = 968.0
PIXEL_REWARD_HEIGHT = 235.0
PIXEL_REWARD_YDISPLAY = 316.0

#: How far down to keep searching, at 1920x1080. WFInfo's 235px box frames the
#: reward cards themselves, but the row of names does not sit at a fixed offset
#: inside them — it moves with the card layout, and long names wrap onto a
#: second line. Since the row is identified by reading it rather than by where
#: it falls, the crop only has to *contain* the names; the other text this
#: sweeps in (ducat counts, "2 Owned", squadmate names) matches no item name and
#: is discarded during verification.
PANEL_SEARCH_HEIGHT = 420.0
BASE_WIDTH = 1920.0
BASE_HEIGHT = 1080.0

MIN_REWARDS = 1
MAX_REWARDS = 4


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    width: int
    height: int

    def crop(self, frame: np.ndarray) -> np.ndarray:
        return frame[self.y : self.y + self.height, self.x : self.x + self.width]

    def shifted(self, dx: int, dy: int) -> Box:
        return Box(self.x + dx, self.y + dy, self.width, self.height)


@dataclass
class Column:
    """One reward's name, cropped out of the panel."""

    index: int
    box: Box
    image: np.ndarray  # binary, black text on white


@dataclass
class Panel:
    box: Box
    scale: float
    #: The theme whose colour mask produced this reading. Themes with adjacent
    #: accent hues are interchangeable here, so this is the mask that worked
    #: rather than a claim about the player's setting.
    theme: theme_module.Theme
    binary: np.ndarray
    columns: list[Column] = field(default_factory=list)
    #: Geometric plausibility of this candidate, 0..1.
    score: float = 0.0
    #: Diagnostics for `scan --debug-dir`.
    notes: dict[str, object] = field(default_factory=dict)


def panel_box(
    shape: tuple[int, ...],
    ui_scale: float = 1.0,
    region: Sequence[float] | None = None,
) -> Box:
    """Where the reward panel should be for a frame of this size.

    Warframe scales its HUD uniformly and centres it, so the smaller of the two
    axis ratios drives the scale; that keeps ultrawide and 16:10 correct.

    `region` overrides all of that with explicit fractions of the frame
    [x, y, width, height], for a resolution where the derived box is wrong.
    """
    height, width = shape[0], shape[1]
    if region is not None:
        if len(region) != 4:
            raise ValueError("panel_region needs four numbers: x, y, width, height")
        x_fraction, y_fraction, width_fraction, height_fraction = region
        x = max(0, min(int(x_fraction * width), width - 1))
        y = max(0, min(int(y_fraction * height), height - 1))
        return Box(
            x,
            y,
            max(1, min(int(width_fraction * width), width - x)),
            max(1, min(int(height_fraction * height), height - y)),
        )

    scale = min(width / BASE_WIDTH, height / BASE_HEIGHT) * ui_scale
    box_width = int(PIXEL_REWARD_WIDTH * scale)
    box_height = int(PANEL_SEARCH_HEIGHT * scale)
    x = int(width / 2 - box_width / 2)
    y = int(height / 2 - PIXEL_REWARD_YDISPLAY * scale)
    # Clamp into frame, in case of an unusual aspect ratio.
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    return Box(x, y, min(box_width, width - x), min(box_height, height - y))


def _bands(active: np.ndarray, max_gap: int) -> list[tuple[int, int]]:
    """Contiguous runs of True in a 1-D array, merging runs separated by a gap."""
    (indices,) = np.nonzero(active)
    if indices.size == 0:
        return []
    runs: list[tuple[int, int]] = []
    start = previous = int(indices[0])
    for index in indices[1:].tolist():
        if index - previous > max_gap + 1:
            runs.append((start, previous + 1))
            start = index
        previous = index
    runs.append((start, previous + 1))
    return runs


def _score_columns(groups: list[tuple[int, int]]) -> float:
    """How much a set of column groups looks like a row of reward names."""
    count = len(groups)
    if not MIN_REWARDS <= count <= MAX_REWARDS:
        return 0.0
    if count == 1:
        return 0.4  # possible, but a single blob is weak evidence
    centres = [(start + end) / 2 for start, end in groups]
    gaps = np.diff(centres)
    widths = np.array([end - start for start, end in groups], dtype=np.float32)
    # Reward cells are identical, so both spacing and width should be uniform.
    gap_evenness = 1.0 - min(1.0, float(np.std(gaps) / max(np.mean(gaps), 1.0)))
    width_evenness = 1.0 - min(1.0, float(np.std(widths) / max(np.mean(widths), 1.0)))
    return 0.5 + 0.3 * gap_evenness + 0.2 * width_evenness


def _text_bands(mask: np.ndarray, scale: float) -> list[tuple[int, int]]:
    """Candidate horizontal bands that could be a line of text.

    Taking maximal runs of lit rows does not work on a real frame: the reward
    panel also contains item art and background, so everything merges into one
    tall band and the text line is never considered on its own. Instead, treat
    each local peak in row density as a possible text line and grow it while the
    density stays close to that peak, which isolates a line inside a busy
    region.
    """
    density = mask.sum(axis=1).astype(np.float32)
    if density.max() <= 0:
        return []

    # Smooth over a few rows so a single ragged row does not create a peak.
    window = max(1, int(3 * scale) | 1)
    kernel = np.ones(window, dtype=np.float32) / window
    smooth = np.convolve(density, kernel, mode="same")

    floor = max(2.0, 0.01 * mask.shape[1])
    min_height = max(6, int(10 * scale))
    max_height = max(min_height + 4, int(64 * scale))

    bands: set[tuple[int, int]] = set()
    for row in range(len(smooth)):
        peak = smooth[row]
        if peak <= floor:
            continue
        if row > 0 and smooth[row - 1] > peak:
            continue
        if row + 1 < len(smooth) and smooth[row + 1] > peak:
            continue

        cutoff = max(floor, 0.5 * peak)
        top = row
        while top > 0 and smooth[top - 1] >= cutoff and row - top < max_height:
            top -= 1
        bottom = row
        while (
            bottom + 1 < len(smooth)
            and smooth[bottom + 1] >= cutoff
            and bottom - top < max_height
        ):
            bottom += 1
        if min_height <= bottom + 1 - top <= max_height:
            bands.add((top, bottom + 1))
    return sorted(bands)


def _split_at(runs: list[tuple[int, int]], boundaries: set[int]) -> list[tuple[int, int]]:
    """Merge consecutive runs, breaking only after the given run indices."""
    grouped: list[tuple[int, int]] = []
    start, end = runs[0]
    for index, (next_start, next_end) in enumerate(runs[1:]):
        if index in boundaries:
            grouped.append((start, end))
            start = next_start
        end = next_end
    grouped.append((start, end))
    return grouped


def _grouping_hypotheses(
    column_density: np.ndarray, scale: float
) -> list[list[tuple[int, int]]]:
    """Plausible ways to divide a band of text into one group per reward.

    No single merge distance works: the gap between two rewards and the gap
    between two words shrink together as the font gets smaller, so a threshold
    tuned for a four-reward row splits a long single name into words, and one
    tuned for a lone name merges two crowded rewards. Since a reward row has at
    most four cells, just enumerate the possibilities — split at the widest gap,
    the two widest, the three widest — and let the caller decide by reading them.
    """
    runs = _bands(column_density > 0, max_gap=0)
    if len(runs) <= 1:
        return [runs]

    gaps = [nxt[0] - cur[1] for cur, nxt in zip(runs, runs[1:], strict=False)]
    widest = sorted(range(len(gaps)), key=lambda index: gaps[index], reverse=True)
    # Deliberately permissive: a long name in a crowded four-reward row leaves
    # only a few pixels between cells, barely more than the space between its
    # own words. Proposing a split that turns out to be mid-name costs one OCR
    # call and is thrown out by the caller, whereas never proposing the right
    # one loses a reward entirely.
    floor = max(3.0, 4.0 * scale)

    hypotheses = [_split_at(runs, set())]  # one reward, nothing split
    for count in range(1, MAX_REWARDS):
        if count > len(widest) or gaps[widest[count - 1]] < floor:
            break
        hypotheses.append(_split_at(runs, set(widest[:count])))

    # Equal-cell divisions only fill in reward counts the gaps did not suggest.
    # They are evenly spaced by construction, so scoring them beside gap-derived
    # splits would always flatter them; as fallbacks they add the possibility
    # that was missing without displacing evidence actually found in the pixels.
    covered = {len(hypothesis) for hypothesis in hypotheses}
    hypotheses.extend(
        hypothesis
        for hypothesis in _equal_cell_hypotheses(runs)
        if len(hypothesis) not in covered
    )
    return hypotheses


def _equal_cell_hypotheses(runs: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Divisions that assume the reward cells are equal width.

    They are: Warframe lays the rewards out as identical cells, so once the
    player has told us the UI scale the layout is known rather than guessed.
    Cutting the text extent into N equal parts finds the cell boundaries even
    when a long name crowds its neighbour so closely that no gap between them
    stands out — the case where splitting at the widest gaps picks a space
    inside a name instead.

    The extent is measured from the text itself rather than the panel, so it
    holds whether a short row is centred or left-aligned.
    """
    first, last = runs[0][0], runs[-1][1]
    span = last - first
    if span <= 0:
        return []

    found: list[list[tuple[int, int]]] = []
    for count in range(2, MAX_REWARDS + 1):
        width = span / count
        groups: list[tuple[int, int]] = []
        for index in range(count):
            low = first + index * width
            high = first + (index + 1) * width
            members = [
                run
                for run in runs
                if low <= (run[0] + run[1]) / 2 < high
                or (index == count - 1 and (run[0] + run[1]) / 2 >= high)
            ]
            if not members:
                break
            groups.append((members[0][0], members[-1][1]))
        if len(groups) == count:
            found.append(groups)
    return found


def _score_fill(fill: float) -> float:
    """Penalise bands that are too sparse or too solid to be text.

    Glyph strokes cover roughly a fifth of their bounding box. A band that is
    almost entirely lit is a filled shape — an icon, a border, a progress bar —
    and a nearly empty one is noise.
    """
    if 0.04 <= fill <= 0.45:
        return 1.0
    if fill < 0.04:
        return max(0.0, fill / 0.04)
    return max(0.0, 1.0 - (fill - 0.45) / 0.35)


def _score_texture(column_density: np.ndarray, groups: list[tuple[int, int]]) -> float:
    """Reward groups made of many thin strokes rather than one solid shape.

    This is what separates real text from a bright shape in the mission-end
    background: a word is a dozen or more separate vertical runs, while a blob,
    bar or icon is one. Without it, a wrong theme's mask over a busy background
    can out-score the actual reward names.
    """
    if not groups:
        return 0.0
    runs = [
        len(_bands(column_density[start:end] > 0, max_gap=0)) for start, end in groups
    ]
    return min(1.0, float(np.median(runs)) / 4.0)


@dataclass(frozen=True)
class _Candidate:
    score: float
    theme: theme_module.Theme
    band: tuple[int, int]
    groups: list[tuple[int, int]]
    mask: np.ndarray


def _candidates_for_theme(
    mask: np.ndarray, ui_theme: theme_module.Theme, scale: float
) -> list[_Candidate]:
    """Every text-like band in this theme's mask, scored."""
    found: list[_Candidate] = []
    min_width = max(8, int(12 * scale))
    for top, bottom in _text_bands(mask, scale):
        band = mask[top:bottom]
        column_density = band.sum(axis=0)
        for hypothesis in _grouping_hypotheses(column_density, scale):
            groups = [(s, e) for s, e in hypothesis if e - s >= min_width]
            if not groups:
                continue
            covered = sum(end - start for start, end in groups) * (bottom - top)
            fill = float(band.sum()) / max(1, covered)
            score = (
                _score_columns(groups)
                * _score_fill(fill)
                * _score_texture(column_density, groups)
            )
            if score > 0:
                found.append(_Candidate(score, ui_theme, (top, bottom), groups, mask))
    return found


def candidates(
    frame: np.ndarray,
    *,
    ui_scale: float = 1.0,
    ui_theme: theme_module.Theme | None = None,
    limit: int = 4,
    region: Sequence[float] | None = None,
) -> list[Panel]:
    """Rank plausible readings of the reward panel, best geometry first.

    The active theme decides which pixels count as text, but the mission-end
    background is bright and colourful, so no purely geometric score reliably
    tells the row of names from a lucky arrangement of scenery in some other
    theme's mask. Rather than guess, this returns several candidates for the
    caller to verify by actually reading them — see `pipeline.scan`, which keeps
    whichever candidate yields text that matches real item names. Pinning
    `ui_theme` collapses the search to one theme.
    """
    box = panel_box(frame.shape, ui_scale, region)
    crop = box.crop(frame)
    scale = min(frame.shape[1] / BASE_WIDTH, frame.shape[0] / BASE_HEIGHT) * ui_scale
    channels = theme_module.hls(crop)

    themes = (ui_theme,) if ui_theme else theme_module.THEMES
    found: list[_Candidate] = []
    for candidate_theme in themes:
        mask = theme_module.mask_from_hls(channels, candidate_theme)
        if mask.any():
            found.extend(_candidates_for_theme(mask, candidate_theme, scale))

    found.sort(key=lambda item: item.score, reverse=True)
    # Spread the shortlist across themes. Several themes share an accent hue, so
    # one theme's near-duplicate readings would otherwise fill it and crowd out
    # the mask that actually reads. With a single pinned theme there is nothing
    # to spread across, and the whole shortlist goes to its best readings.
    per_theme = max(2, limit // max(1, len(themes)))
    shortlist: list[_Candidate] = []
    counts: dict[str, int] = {}
    seen_shapes: set[tuple[str, int]] = set()
    for candidate in found:
        # One entry per theme and reward count. Peak detection returns several
        # near-identical bands for the same line of text, and without this they
        # fill the shortlist with the same reading three times over and crowd
        # out the split that actually separates all four rewards.
        shape = (candidate.theme.name, len(candidate.groups))
        if shape in seen_shapes:
            continue
        seen = counts.get(candidate.theme.name, 0)
        if seen >= per_theme:
            continue
        seen_shapes.add(shape)
        counts[candidate.theme.name] = seen + 1
        shortlist.append(candidate)

    panels = [
        _to_panel(candidate, box, scale, len(themes), len(found))
        for candidate in shortlist[:limit]
    ]
    for panel in panels:
        panel.notes["candidates_found"] = len(found)
    return panels


def locate(
    frame: np.ndarray,
    *,
    ui_scale: float = 1.0,
    ui_theme: theme_module.Theme | None = None,
    region: Sequence[float] | None = None,
) -> Panel:
    """The most geometrically plausible reading of the reward panel."""
    found = candidates(frame, ui_scale=ui_scale, ui_theme=ui_theme, limit=1, region=region)
    if found:
        return found[0]

    box = panel_box(frame.shape, ui_scale, region)
    scale = min(frame.shape[1] / BASE_WIDTH, frame.shape[0] / BASE_HEIGHT) * ui_scale
    fallback = ui_theme or theme_module.THEMES[0]
    panel = Panel(
        box=box,
        scale=scale,
        theme=fallback,
        binary=theme_module.binarize(box.crop(frame), fallback),
    )
    panel.notes["reason"] = "no theme produced a band of evenly spaced columns"
    return panel


def _to_panel(
    candidate: _Candidate, box: Box, scale: float, themes_tried: int, considered: int
) -> Panel:
    top, bottom = candidate.band
    mask = candidate.mask
    panel = Panel(
        box=box,
        scale=scale,
        theme=candidate.theme,
        binary=theme_module.to_binary(mask),
        score=candidate.score,
    )
    panel.notes.update(
        {
            "themes_tried": themes_tried,
            "bands_considered": considered,
            "band_score": round(candidate.score, 3),
            "band": (top, bottom),
        }
    )

    pad = max(2, int(4 * scale))
    for index, (start, end) in enumerate(candidate.groups):
        local = Box(
            x=max(0, start - pad),
            y=max(0, top - pad),
            width=min(mask.shape[1], end + pad) - max(0, start - pad),
            height=min(mask.shape[0], bottom + pad) - max(0, top - pad),
        )
        panel.columns.append(
            Column(
                index=index,
                box=local.shifted(box.x, box.y),
                image=local.crop(panel.binary),
            )
        )
    return panel
