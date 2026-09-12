"""Work out which capture backend this machine can actually use.

Capture is the one part of this project that cannot be settled by reading
documentation: whether an X11 GetImage on the Warframe window works depends on
the compositor. So the tooling asks the machine rather than assuming, and says
what it found.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from voidsight.capture.base import CaptureError, Frame

log = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    backend: str
    available: bool
    detail: str
    frame: Frame | None = None
    extras: list[str] = field(default_factory=list)
    #: No windows at all, which is a connection fault rather than a capture one.
    empty_tree: bool = False

    @property
    def symbol(self) -> str:
        return "ok  " if self.available else "no  "


def describe_session() -> str:
    """What kind of desktop session this is, as the environment reports it."""
    parts = [
        f"XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE', '?')}",
        f"XDG_CURRENT_DESKTOP={os.environ.get('XDG_CURRENT_DESKTOP', '?')}",
        f"DISPLAY={os.environ.get('DISPLAY', '-')}",
        f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '-')}",
    ]
    return "  ".join(parts)


def describe_game() -> str:
    """Whether the game is running, by a signal that does not involve X.

    Reading /proc answers "is it even up?" independently of whether we can see
    a window, and those two questions have completely different answers when
    the X connection is the broken part.
    """
    from voidsight import game

    pid = game.find_process()
    return f"Warframe process: {f'running (pid {pid})' if pid else 'not found in /proc'}"


def probe_x11(window_name: str | None = None) -> ProbeResult:
    from voidsight.capture.x11 import X11Backend

    try:
        backend = X11Backend(window_name)
    except CaptureError as exc:
        return ProbeResult("x11", False, str(exc))

    try:
        windows = [str(info) for info in backend.windows()]
        # The backend itself waits for the game's window rather than refusing to
        # start without one; a probe is asking about right now, so it reports it.
        if backend.window_info is None:
            return ProbeResult(
                "x11",
                False,
                backend.missing_window_message,
                extras=_inventory(backend, windows),
                empty_tree=not windows,
            )
        frame = backend.grab()
        if frame is None:
            return ProbeResult(
                "x11", False, "window found but GetImage returned nothing", extras=windows
            )
        if not frame.any():
            return ProbeResult(
                "x11",
                False,
                "captured an all-black frame — the compositor is redirecting this window",
                frame=frame,
                extras=windows,
            )
        return ProbeResult(
            "x11",
            True,
            f"captured {frame.shape[1]}x{frame.shape[0]} from {backend.window_info}",
            frame=frame,
            extras=windows,
        )
    finally:
        backend.close()


def _inventory(backend, windows: list[str]) -> list[str]:
    """What this X connection can see, when it cannot see the game.

    An empty tree and a tree full of other people's windows call for opposite
    advice — the first means we are on the wrong X server or cannot read it,
    the second means the game's window is named something unexpected.
    """
    lines = [f"connected to {backend.display_name}"]
    if windows:
        lines.extend(windows)
        return lines

    everything = backend.windows(named_only=False, min_size=1)
    lines.append(f"{backend.tree_size()} window(s) in the tree, none of them named and sizeable")
    lines.extend(str(info) for info in everything[:8])
    return lines


def probe_all(window_name: str | None = None, save_to: Path | None = None) -> list[ProbeResult]:
    results = [probe_x11(window_name)]
    if save_to:
        save_to.mkdir(parents=True, exist_ok=True)
        for result in results:
            if result.frame is not None:
                path = save_to / f"probe-{result.backend}.png"
                cv2.imwrite(str(path), cv2.cvtColor(result.frame, cv2.COLOR_RGB2BGR))
                result.detail += f"  (saved {path})"
    return results
