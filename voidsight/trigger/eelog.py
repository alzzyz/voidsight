"""Watching Warframe's EE.log for the reward screen.

The game writes a line when the relic reward choice appears. Reading that is
what makes scanning automatic instead of a hotkey press.

Two Linux-specific problems shape this module. First, the log lives inside a
Proton prefix, whose path depends on where Steam put the library — so it is
discovered rather than assumed. Second, Wine buffers writes to it, so the line
can land noticeably after the screen appeared, and occasionally only once the
buffer flushes. That is why the trigger reports *when* it saw the line, and why
callers scan backwards through buffered frames instead of grabbing one at the
moment of notification.

The trigger strings come from WFInfo; see NOTICE.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

WARFRAME_APP_ID = "230410"
LOG_RELATIVE = Path("pfx/drive_c/users/steamuser/AppData/Local/Warframe/EE.log")

#: Any of these means the reward choice is up.
REWARD_TRIGGERS = (
    "Pause countdown done",
    "Got rewards",
    "Created /Lotus/Interface/ProjectionRewardChoice.swf",
)

#: "Adding fixed rewards from /Lotus/Types/Game/Projections/T2VoidProjectionBronzeC"
#: style lines let us tell which relic was opened, which narrows OCR matching to
#: six candidates. Era names in the log are internal (T1..T5).
RELIC_PATTERN = re.compile(
    r"VoidProjection(?P<name>[A-Za-z]+)(?P<number>\d*)", re.IGNORECASE
)
ERA_BY_TIER = {"T1": "Lith", "T2": "Meso", "T3": "Neo", "T4": "Axi", "T5": "Requiem"}

STEAM_ROOTS = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.steam/root",
    "~/.var/app/com.valvesoftware.Steam/data/Steam",
    "~/snap/steam/common/.local/share/Steam",
)


@dataclass(frozen=True)
class RewardEvent:
    """A reward screen was reported by the log."""

    line: str
    seen_at: float
    relic: str | None = None


def steam_libraries() -> list[Path]:
    """Every Steam library folder, including ones on other drives."""
    libraries: list[Path] = []
    for root in STEAM_ROOTS:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        libraries.append(base / "steamapps")
        libraries.extend(_extra_libraries(base / "steamapps" / "libraryfolders.vdf"))
    return libraries


def _extra_libraries(vdf: Path) -> Iterator[Path]:
    """Parse the paths out of libraryfolders.vdf without a VDF library."""
    try:
        text = vdf.read_text(errors="replace")
    except OSError:
        return
    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        candidate = Path(match.group(1)) / "steamapps"
        if candidate.is_dir():
            yield candidate


def find_log(explicit: str | Path | None = None) -> Path | None:
    """Locate EE.log: an explicit path, else the Proton prefix, else nothing."""
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.exists() else None

    candidates = [
        library / "compatdata" / WARFRAME_APP_ID / LOG_RELATIVE
        for library in steam_libraries()
    ]
    # A native Windows-style location, in case someone runs this under Wine directly.
    candidates.append(
        Path("~/.wine/drive_c/users").expanduser() / "*/AppData/Local/Warframe/EE.log"
    )

    for candidate in candidates:
        if "*" in str(candidate):
            matches = sorted(Path(candidate.anchor).glob(str(candidate).lstrip("/")))
            if matches:
                return matches[0]
        elif candidate.exists():
            return candidate
    return None


def parse_relic(line: str) -> str | None:
    """Extract a relic name like "Axi A1" from a log line, if it names one."""
    tier = re.search(r"\bT([1-5])VoidProjection", line, re.IGNORECASE)
    match = RELIC_PATTERN.search(line)
    if not (tier and match):
        return None
    era = ERA_BY_TIER.get(f"T{tier.group(1)}")
    name = match.group("name")
    if not era or not name:
        return None
    return f"{era} {name}{match.group('number')}".strip()


class LogWatcher:
    """Tails EE.log and calls back when the reward screen shows up.

    Polling rather than inotify: the log is written by a Windows process through
    Wine, and file-change notifications for it are unreliable across that
    boundary. A stat every 250ms costs nothing and never misses a flush.
    """

    def __init__(
        self,
        path: Path,
        on_reward: Callable[[RewardEvent], None],
        *,
        interval: float = 0.25,
        from_start: bool = False,
    ) -> None:
        self.path = Path(path)
        self.on_reward = on_reward
        self.interval = interval
        self._offset = 0 if from_start else self._size()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_relic: str | None = None

    def _size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self.run, name="eelog-watcher", daemon=True)
        self._thread.start()
        log.info("watching %s", self.path)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:  # pragma: no cover - belt and braces
                # This thread is the only thing that notices a reward screen.
                # If it dies the app goes quiet for the rest of the session
                # without saying anything, so it does not get to die.
                log.exception("log watcher poll failed; continuing")
            self._stop.wait(self.interval)

    def poll(self) -> list[RewardEvent]:
        """Read whatever is new and fire the callback for reward lines."""
        events = [self._event_for(line) for line in self.read_new() if self._is_trigger(line)]
        events = [event for event in events if event is not None]
        for event in events:
            try:
                self.on_reward(event)
            except Exception:
                # One failed scan must not cost every later trigger.
                log.exception("handling a reward trigger failed")
        return events

    def read_new(self) -> list[str]:
        size = self._size()
        if size < self._offset:
            # The game restarted and truncated the log.
            log.debug("%s shrank; rereading from the start", self.path)
            self._offset = 0
        if size == self._offset:
            return []
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self._offset)
                text = handle.read()
                self._offset = handle.tell()
        except OSError as exc:
            log.debug("could not read %s: %s", self.path, exc)
            return []
        return text.splitlines()

    def _is_trigger(self, line: str) -> bool:
        if relic := parse_relic(line):
            # Remember the most recent relic so a reward line can be narrowed.
            self._last_relic = relic
        return any(trigger in line for trigger in REWARD_TRIGGERS)

    def _event_for(self, line: str) -> RewardEvent:
        return RewardEvent(line=line.strip(), seen_at=time.monotonic(), relic=self._last_relic)
