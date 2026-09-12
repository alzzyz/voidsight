"""Capturing the Warframe window through X11.

Warframe runs under Proton, so on a Wayland desktop it is an XWayland client and
its window is a real X11 window that an X11 client can read with GetImage. That
is the cheap path: no permission prompt, no PipeWire, works the same on Xorg.

Whether KWin actually allows it is a property of the compositor, not something
worth assuming — run `voidsight probe` on the target machine to find out. If it
does not work, the fallback is the xdg-desktop-portal screencast backend.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass

import numpy as np

from voidsight.capture.base import CaptureError, Frame

log = logging.getLogger(__name__)

DEFAULT_WINDOW_NAMES = ("warframe", "gamescope")
#: How deep to walk the X window tree. Compositors nest to different depths —
#: KWin reparents for decorations, gamescope nests a server of its own — and a
#: few extra levels cost one round trip each.
WALK_DEPTH = 6
#: How often to go looking for the game's window while we do not have it. The
#: capture loop asks for a frame several times a second and walking the X tree
#: that often would be wasteful — a game starting is a human-timescale event.
WINDOW_SEARCH_INTERVAL = 2.0


@dataclass(frozen=True)
class WindowInfo:
    id: int
    name: str
    width: int
    height: int

    def __str__(self) -> str:
        return f"{self.name!r} ({self.width}x{self.height}, id=0x{self.id:x})"


def find_window_title(window_name: str = "Warframe") -> str | None:
    """Title of the first X11 window matching a name, or None.

    Used by game detection, which wants to know whether the game has a window
    without opening a capture session or raising when it does not.
    """
    try:
        backend = X11Backend(window_name)
    except CaptureError:
        return None
    try:
        return backend.window_info.name if backend.window_info else None
    finally:
        backend.close()


class X11Backend:
    """Reads the contents of a named X11 window.

    The window is looked for lazily rather than demanded at construction. Both
    startup hooks create the client *before* the game exists — a login entry by
    minutes, a Steam launch option by seconds — so a backend that gave up when
    it found no window would stay dead for the whole session while the header
    cheerfully reported the game as running. The same lookup re-acquires the
    window if the game is restarted underneath us, which a long-lived client
    started at login will outlive several of.

    Construction still fails if there is no X display at all: that is a property
    of the machine rather than of what happens to be running on it.
    """

    name = "x11"

    def __init__(self, window_name: str | None = None) -> None:
        if sys.platform != "linux":  # pragma: no cover - platform dependent
            raise CaptureError(
                f"X11 capture needs Linux; this is {sys.platform}. "
                "Scan saved screenshots instead"
            )
        try:
            from Xlib import display as xdisplay
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise CaptureError(
                "python-xlib is not installed, so the game window cannot be "
                "captured. Install it with: uv sync --extra desktop --extra x11"
            ) from exc

        try:
            self._display = xdisplay.Display()
        except Exception as exc:  # Xlib raises a zoo of errors
            raise CaptureError(f"cannot open the X display: {exc}") from exc

        self._root = self._display.screen().root
        self.wanted = (window_name.lower(),) if window_name else DEFAULT_WINDOW_NAMES
        self._window = None
        self.window_info: WindowInfo | None = None
        self._next_search = 0.0
        self._announced: int | None = None
        self.find_window()

    def close(self) -> None:
        try:
            self._display.close()
        except Exception:  # pragma: no cover - best effort
            pass

    @property
    def display_name(self) -> str:
        """Which X server this is actually talking to."""
        try:
            return str(self._display.get_display_name())
        except Exception:  # pragma: no cover - depends on the xlib build
            return "?"

    def windows(self, *, named_only: bool = True, min_size: int = 200) -> list[WindowInfo]:
        """Mapped top-level windows — the big named ones, or everything.

        The filtered form is what window-matching uses. The unfiltered form is
        for diagnostics: "no window matched" and "the tree was empty" are very
        different faults, and only the second is about capture at all.
        """
        found: list[WindowInfo] = []
        for window in self._walk(self._root):
            try:
                name = window.get_wm_name()
                geometry = window.get_geometry()
            except Exception:
                continue
            if named_only and not name:
                continue
            if geometry.width < min_size or geometry.height < min_size:
                continue
            found.append(
                WindowInfo(
                    id=window.id,
                    name=str(name) if name else "",
                    width=geometry.width,
                    height=geometry.height,
                )
            )
        return found

    def tree_size(self) -> int:
        """How many windows are in the tree at all, named or not."""
        return sum(1 for _ in self._walk(self._root))

    def _walk(self, window, depth: int = 0):
        if depth > WALK_DEPTH:
            return
        try:
            children = window.query_tree().children
        except Exception:
            return
        for child in children:
            yield child
            yield from self._walk(child, depth + 1)

    @property
    def missing_window_message(self) -> str:
        return "no window matching " + "/".join(self.wanted) + " — is Warframe running?"

    def find_window(self, *, force: bool = False) -> WindowInfo | None:
        """Look for the game's window. Safe to call often; rate-limited inside."""
        now = time.monotonic()
        if not force and now < self._next_search:
            return self.window_info
        self._next_search = now + WINDOW_SEARCH_INTERVAL

        for info in self.windows():
            lowered = info.name.lower()
            if any(wanted in lowered for wanted in self.wanted):
                log.debug("found X11 window %s", info)
                self._window = self._display.create_resource_object("window", info.id)
                self.window_info = info
                return info

        if self.window_info is not None:
            log.info("the %s window is gone; watching for it to come back", self.window_info.name)
            self._announced = None
        self._window = None
        self.window_info = None
        return None

    def grab(self) -> Frame | None:
        if self._window is None and self.find_window() is None:
            return None
        # Announced here rather than on discovery: game detection builds a
        # throwaway backend every few seconds just to read a window title, and
        # it would otherwise narrate that to the log file all day.
        if self.window_info is not None and self.window_info.id != self._announced:
            log.info("capturing X11 window %s", self.window_info)
            self._announced = self.window_info.id
        try:
            geometry = self._window.get_geometry()
            raw = self._window.get_image(
                0, 0, geometry.width, geometry.height, 2, 0xFFFFFFFF  # 2 = ZPixmap
            )
        except Exception as exc:
            # Nearly always the window going away mid-session — the game closed,
            # or Proton replaced it. Forget it so the next grab finds its
            # successor instead of retrying a dead id forever.
            log.warning("X11 capture failed (%s); looking for the window again", exc)
            self._window = None
            self.window_info = None
            return None

        data = raw.data
        if isinstance(data, str):  # older python-xlib returns str
            data = data.encode("latin-1")
        pixels = np.frombuffer(data, dtype=np.uint8)
        expected = geometry.width * geometry.height
        if pixels.size < expected * 3:
            log.warning("short image data from X11 (%d bytes)", pixels.size)
            return None

        channels = pixels.size // expected
        image = pixels[: expected * channels].reshape(geometry.height, geometry.width, channels)
        # X gives BGRX/BGR; drop padding and flip to RGB.
        return np.ascontiguousarray(image[:, :, 2::-1] if channels >= 3 else image)
