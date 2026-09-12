"""Frame -> identified rewards, with optional debug artifacts."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from voidsight.config import Config
from voidsight.data.catalog import Catalog, Part, Relic
from voidsight.vision import locate as locate_module
from voidsight.vision import match as match_module
from voidsight.vision import ocr as ocr_module
from voidsight.vision import theme as theme_module

log = logging.getLogger(__name__)


@dataclass
class Reward:
    index: int
    raw_text: str
    ocr_confidence: float
    match: match_module.Match

    @property
    def part(self) -> Part | None:
        return self.match.part

    @property
    def name(self) -> str:
        return self.part.display_name if self.part else (self.raw_text or "?")


@dataclass
class ScanResult:
    rewards: list[Reward] = field(default_factory=list)
    theme: theme_module.Theme | None = None
    relic: Relic | None = None
    panel: locate_module.Panel | None = None
    notes: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.rewards) and all(reward.match.ok for reward in self.rewards)

    @property
    def identified(self) -> list[Reward]:
        return [reward for reward in self.rewards if reward.match.ok]

    @property
    def confidence(self) -> float:
        """How much to trust this reading, 0..1.

        Used both to pick between candidate readings of one frame and to decide
        whether to keep looking back through buffered frames for a better one.
        Columns that matched nothing score zero rather than being ignored, so a
        reading that resolves every reward beats one that resolves a single
        column by luck.
        """
        if not self.rewards:
            return 0.0
        scores = [
            (reward.match.score / 100.0) * (0.5 + 0.5 * reward.ocr_confidence)
            if reward.match.ok
            else 0.0
            for reward in self.rewards
        ]
        return float(np.mean(scores))


#: A reading this good is accepted without trying the remaining candidates.
GOOD_ENOUGH = 0.75
#: How many columns to read at once, unless the config says otherwise.
#:
#: One, deliberately. Four concurrent tesseract processes faulting in the same
#: file-backed mappings triggered a kernel general protection fault in
#: filemap_map_pages and hardlocked the machine — a kernel bug that userspace
#: cannot cause and cannot fix, but whose trigger is this workload. The
#: parallel path is kept, off by default, for kernels that survive it.
OCR_WORKERS = 1
#: The most that can ever help: one worker per reward.
MAX_OCR_WORKERS = 4


def scan(
    frame: np.ndarray,
    catalog: Catalog,
    *,
    reader: ocr_module.Reader | None = None,
    ui_scale: float = 1.0,
    ui_theme: theme_module.Theme | None = None,
    relic: Relic | None = None,
    max_candidates: int = 6,
    region: Sequence[float] | None = None,
    workers: int = OCR_WORKERS,
    debug_dir: Path | None = None,
) -> ScanResult:
    """Read the reward names out of a full-frame RGB screenshot.

    Locating the names is ambiguous on a busy frame, so several candidate
    readings are ranked geometrically and then *verified by reading them*: the
    one whose text actually resolves to known item names wins. OCR is the only
    reliable judge of whether a band of pixels is the reward row, and it costs
    little here because candidates are tried best-first and the search stops as
    soon as one reads cleanly.
    """
    reader = reader or ocr_module.default_reader()
    panels = locate_module.candidates(
        frame, ui_scale=ui_scale, ui_theme=ui_theme, limit=max_candidates, region=region
    )
    if not panels:
        panel = locate_module.locate(
            frame, ui_scale=ui_scale, ui_theme=ui_theme, region=region
        )
        result = ScanResult(
            theme=panel.theme, relic=relic, panel=panel, notes=dict(panel.notes)
        )
        if debug_dir:
            _dump(debug_dir, frame, panel, result)
        return result

    names = [reward.part_name for reward in relic.rewards] if relic else None
    best: ScanResult | None = None
    for attempt, panel in enumerate(panels, start=1):
        result = _read_panel(panel, catalog, reader, names, relic, workers)
        result.notes["candidates_tried"] = attempt
        if best is None or _rank(result) > _rank(best):
            best = result
        # A squad can be smaller than four, so a clean two-reward read may be
        # the whole truth — but it is also what a candidate that framed only
        # half the row looks like. Stop when every located name resolved and
        # nothing still on the list proposes more cells than this reading has.
        # Waiting for a full four meant a two-player squad always read all six
        # candidates, at a tesseract subprocess per column.
        if result.ok and result.confidence >= GOOD_ENOUGH:
            if not any(len(later.columns) > len(result.rewards) for later in panels[attempt:]):
                break

    assert best is not None and best.panel is not None
    log.debug(
        "scan: %d/%d candidates tried, theme=%s confidence=%.2f",
        best.notes.get("candidates_tried"),
        len(panels),
        best.theme.name if best.theme else "?",
        best.confidence,
    )
    if debug_dir:
        _dump(debug_dir, frame, best.panel, best)
    return best


#: A pinned theme producing readings this poor is treated as wrong.
RETRY_BELOW = 0.4
#: How often the fifteen-mask theme search may re-run.
#:
#: A frame with no reward screen on it reads exactly like a frame under the
#: wrong theme — nothing matches either way — and a back-scan is mostly such
#: frames. Re-detecting per frame therefore cost more than every other part of
#: the scan combined, on frames that could never have produced a reading.
REDETECT_INTERVAL = 20.0


class Scanner:
    """Stateful scanner for live use.

    Holds the one piece of state worth keeping between frames: which theme's
    colour mask reads this player's UI. The first scan searches for it, later
    scans reuse it (one mask instead of fifteen), and it is written to the
    config file so restarts skip the search entirely. If the pinned theme stops
    reading — the player changed it, or it was learned from a bad frame — the
    search runs again.
    """

    def __init__(
        self,
        catalog: Catalog,
        *,
        config: Config | None = None,
        reader: ocr_module.Reader | None = None,
        persist: bool = True,
        config_path: Path | None = None,
    ) -> None:
        self.catalog = catalog
        self.config = config or Config()
        self.reader = reader or ocr_module.default_reader()
        self.persist = persist
        self.config_path = config_path
        self._theme: theme_module.Theme | None = None
        self._last_redetect = 0.0
        if self.config.theme:
            try:
                self._theme = theme_module.get(self.config.theme)
            except KeyError:
                log.warning("configured theme %r is unknown; ignoring", self.config.theme)

    @property
    def theme(self) -> theme_module.Theme | None:
        return self._theme

    def set_theme(self, name: str | None) -> theme_module.Theme | None:
        """Pin the UI theme, or pass None to work it out from the next scan.

        This is the normal way the theme gets set: the player picks it in the
        settings, the same as they do in Warframe itself. Detection exists for
        players who do not know which one they are using.
        """
        if name is None:
            self._theme = None
            self.config.theme = None
        else:
            self._theme = theme_module.get(name)
            self.config.theme = self._theme.name
        if self.persist:
            self.config.save(self.config_path)
        return self._theme

    def set_ui_scale(self, scale: float) -> float:
        """Set Warframe's interface scale, which fixes the reward panel geometry."""
        if not 0.5 <= scale <= 2.0:
            raise ValueError("ui_scale must be between 0.5 and 2.0")
        self.config.ui_scale = scale
        if self.persist:
            self.config.save(self.config_path)
        return scale

    def scan(
        self,
        frame: np.ndarray,
        *,
        relic: Relic | None = None,
        debug_dir: Path | None = None,
    ) -> ScanResult:
        result = self._scan(frame, self._theme, relic, debug_dir)

        if self._theme is not None and result.confidence < RETRY_BELOW:
            now = time.monotonic()
            if now - self._last_redetect < REDETECT_INTERVAL:
                log.debug(
                    "pinned theme %s read nothing; not re-running the theme search yet",
                    self._theme.name,
                )
            else:
                self._last_redetect = now
                log.info("pinned theme %s read poorly; re-detecting", self._theme.name)
                retry = self._scan(frame, None, relic, debug_dir)
                if _rank(retry) > _rank(result):
                    self._theme = None
                    result = retry

        learned = result.ok and result.confidence >= GOOD_ENOUGH and result.theme
        if self._theme is None and learned:
            self._theme = result.theme
            self.config.theme = result.theme.name
            if self.persist:
                self.config.learn_theme(result.theme.name, path=self.config_path)
        return result

    def _scan(
        self,
        frame: np.ndarray,
        ui_theme: theme_module.Theme | None,
        relic: Relic | None,
        debug_dir: Path | None,
    ) -> ScanResult:
        return scan(
            frame,
            self.catalog,
            reader=self.reader,
            ui_scale=self.config.ui_scale,
            ui_theme=ui_theme,
            relic=relic,
            # Working out the theme happens once, so it can afford a wider
            # search than the steady-state path with the theme pinned.
            max_candidates=6 if ui_theme else 12,
            region=self.config.panel_region,
            workers=self.config.ocr_workers,
            debug_dir=debug_dir,
        )


def _rank(result: ScanResult) -> tuple[int, float]:
    """Sort key for competing readings: identify more rewards, then read them better."""
    return (len(result.identified), result.confidence)


def _read_panel(
    panel: locate_module.Panel,
    catalog: Catalog,
    reader: ocr_module.Reader,
    names: list[str] | None,
    relic: Relic | None,
    workers: int = OCR_WORKERS,
) -> ScanResult:
    result = ScanResult(
        theme=panel.theme, relic=relic, panel=panel, notes=dict(panel.notes)
    )
    columns = panel.columns
    if not columns:
        return result

    # Each read spawns a tesseract process, and the spawn is nearly all of the
    # cost, so overlapping them shortens a scan considerably. It is off by
    # default all the same: see OCR_WORKERS.
    parallel = max(1, min(workers, MAX_OCR_WORKERS, len(columns)))
    if parallel == 1:
        reads = [reader.read(column.image) for column in columns]
    else:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            reads = list(pool.map(lambda column: reader.read(column.image), columns))

    for column, read in zip(columns, reads, strict=True):
        result.rewards.append(
            Reward(
                index=column.index,
                raw_text=read.text,
                ocr_confidence=read.confidence,
                match=match_module.match(read.text, catalog, candidates=names),
            )
        )
    return result


def _dump(
    directory: Path,
    frame: np.ndarray,
    panel: locate_module.Panel,
    result: ScanResult,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(directory / "frame.png"), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    cv2.imwrite(
        str(directory / "panel.png"), cv2.cvtColor(panel.box.crop(frame), cv2.COLOR_RGB2BGR)
    )
    cv2.imwrite(str(directory / "panel_binary.png"), panel.binary)
    for column in panel.columns:
        cv2.imwrite(str(directory / f"column_{column.index}.png"), column.image)
        cv2.imwrite(
            str(directory / f"column_{column.index}_ocr_input.png"),
            ocr_module.preprocess(column.image),
        )

    overlay = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR).copy()
    box = panel.box
    cv2.rectangle(
        overlay, (box.x, box.y), (box.x + box.width, box.y + box.height), (0, 200, 255), 2
    )
    for column in panel.columns:
        cb = column.box
        cv2.rectangle(overlay, (cb.x, cb.y), (cb.x + cb.width, cb.y + cb.height), (0, 255, 0), 2)
    cv2.imwrite(str(directory / "overlay.png"), overlay)

    report = {
        "theme": panel.theme.name,
        "scale": round(panel.scale, 4),
        "panel_box": panel.box.__dict__,
        "notes": {key: str(value) for key, value in result.notes.items()},
        "relic": result.relic.name if result.relic else None,
        "rewards": [
            {
                "index": reward.index,
                "raw_text": reward.raw_text,
                "ocr_confidence": round(reward.ocr_confidence, 3),
                "match": reward.part.display_name if reward.part else None,
                "score": round(reward.match.score, 1),
                "constrained": reward.match.constrained,
                "runner_up": reward.match.runner_up,
            }
            for reward in result.rewards
        ],
    }
    (directory / "report.json").write_text(json.dumps(report, indent=2))
    log.info("debug artifacts written to %s", directory)
