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
from dataclasses import dataclass

import numpy as np

from voidsight.capture.base import CaptureError, Frame

log = logging.getLogger(__name__)

DEFAULT_WINDOW_NAMES = ("warframe", "gamescope")


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
    """Reads the contents of a named X11 window."""

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
        self._wanted = (window_name.lower(),) if window_name else DEFAULT_WINDOW_NAMES
        self._window = None
        self.window_info: WindowInfo | None = None
        self._find_window()

    def close(self) -> None:
        try:
            self._display.close()
        except Exception:  # pragma: no cover - best effort
            pass

    def windows(self) -> list[WindowInfo]:
        """Every mapped, named top-level window — useful for diagnostics."""
        found: list[WindowInfo] = []
        for window in self._walk(self._root):
            try:
                name = window.get_wm_name()
                geometry = window.get_geometry()
            except Exception:
                continue
            if not name or geometry.width < 200 or geometry.height < 200:
                continue
            found.append(
                WindowInfo(
                    id=window.id,
                    name=str(name),
                    width=geometry.width,
                    height=geometry.height,
                )
            )
        return found

    def _walk(self, window, depth: int = 0):
        if depth > 3:
            return
        try:
            children = window.query_tree().children
        except Exception:
            return
        for child in children:
            yield child
            yield from self._walk(child, depth + 1)

    def _find_window(self) -> None:
        for info in self.windows():
            lowered = info.name.lower()
            if any(wanted in lowered for wanted in self._wanted):
                self._window = self._display.create_resource_object("window", info.id)
                self.window_info = info
                log.info("capturing X11 window %s", info)
                return
        raise CaptureError(
            "no window matching " + "/".join(self._wanted) + " — is Warframe running?"
        )

    def grab(self) -> Frame | None:
        if self._window is None:
            return None
        try:
            geometry = self._window.get_geometry()
            raw = self._window.get_image(
                0, 0, geometry.width, geometry.height, 2, 0xFFFFFFFF  # 2 = ZPixmap
            )
        except Exception as exc:
            log.warning("X11 capture failed: %s", exc)
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
