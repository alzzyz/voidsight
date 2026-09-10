"""Is Warframe actually running?

Three independent signals, because no single one is reliable on every setup:

* **The process.** Under Proton the game is `Warframe.x64.exe` running in Wine.
  Reading `/proc` finds it regardless of display server, needs no permissions
  and no X connection. This is the primary signal.
* **The window.** If an X11 connection is available, a window named Warframe
  confirms it is not merely launching, and gives us its geometry. Under Wayland
  this still works for the game because Proton runs it through XWayland — but
  it tells us nothing if the client itself is Wayland-native.
* **EE.log.** A log written to in the last minute means the game is live even
  if the other two are unavailable, which is what makes this work inside a
  Flatpak sandbox where /proc is not shared.

Detection is deliberately advisory. Nothing refuses to run because the game
looks absent; the UI just says so.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Proton runs the Windows build, so this is the executable name on Linux too.
PROCESS_NAMES = ("warframe.x64.exe", "warframe.exe")
#: A log touched more recently than this means the game is running.
LOG_FRESH_SECONDS = 90.0


@dataclass(frozen=True)
class GameState:
    running: bool
    #: Which signals fired, for display and for debugging someone's setup.
    signals: tuple[str, ...] = ()
    pid: int | None = None
    window: str | None = None

    @property
    def summary(self) -> str:
        if not self.running:
            return "Warframe not detected"
        if self.window:
            return "Warframe running"
        return "Warframe running (no window found)"

    @property
    def detail(self) -> str:
        return ", ".join(self.signals) if self.signals else "no signals"


def find_process() -> int | None:
    """PID of the running game, by scanning /proc. Linux only."""
    if sys.platform != "linux":
        return None
    try:
        entries = os.listdir("/proc")
    except OSError:  # pragma: no cover - unusual, but not worth failing over
        return None

    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                cmdline = handle.read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue  # process exited, or not ours to read
        lowered = cmdline.lower()
        if any(name in lowered for name in PROCESS_NAMES):
            return int(entry)
    return None


def find_window(window_name: str = "Warframe") -> str | None:
    """Title of the game's X11 window, if an X server is reachable."""
    try:
        from voidsight.capture.x11 import find_window_title
    except ImportError:
        return None
    try:
        return find_window_title(window_name)
    except Exception as exc:  # X11 is optional; never let it break detection
        log.debug("window lookup failed: %s", exc)
        return None


def log_is_fresh(log_path: Path | None) -> bool:
    """Whether EE.log was written to recently enough to mean 'playing now'."""
    if not log_path:
        return False
    try:
        return time.time() - log_path.stat().st_mtime < LOG_FRESH_SECONDS
    except OSError:
        return False


def detect(log_path: Path | None = None, *, window_name: str = "Warframe") -> GameState:
    """Combine every available signal into one answer."""
    signals: list[str] = []

    pid = find_process()
    if pid is not None:
        signals.append(f"process {pid}")

    window = find_window(window_name)
    if window:
        signals.append("window")

    if log_is_fresh(log_path):
        signals.append("recent EE.log")

    return GameState(running=bool(signals), signals=tuple(signals), pid=pid, window=window)
