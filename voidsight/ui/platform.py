"""Choosing the Qt platform plugin, which decides whether an overlay is possible.

On a Wayland session a client cannot place its own windows: `move()` is a
request the compositor is free to ignore, and KWin does. That makes a
corner-anchored, always-on-top overlay impossible for a Wayland-native client
without the layer-shell protocol, which Qt reaches only through LayerShellQt —
a C++ library with no Python bindings.

XWayland has none of those restrictions, and Warframe is already there because
Proton runs it under XWayland. So on a Wayland session we ask Qt for the xcb
plugin. The user can override with QT_QPA_PLATFORM as usual; we only set it if
they have expressed no preference.
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)

WAYLAND_ONLY_NOTE = (
    "Running as a Wayland client: the overlay cannot position itself and will "
    "appear wherever KWin puts it. Install Qt's xcb platform plugin for a "
    "placeable overlay."
)


def session_type() -> str:
    """"wayland", "x11" or "" — what the desktop session reports."""
    return (os.environ.get("XDG_SESSION_TYPE") or "").lower()


def prefer_xwayland() -> str | None:
    """Point Qt at xcb on a Wayland session. Returns the platform chosen, if any.

    Called before QApplication exists, since the plugin is picked at startup.
    """
    if sys.platform != "linux":
        return None
    if os.environ.get("QT_QPA_PLATFORM"):
        return None  # the user has already chosen
    if session_type() != "wayland":
        return None

    os.environ["QT_QPA_PLATFORM"] = "xcb"
    log.info("Wayland session detected; using Qt's xcb plugin so the overlay can position itself")
    return "xcb"


def overlay_supported(platform_name: str) -> bool:
    """Whether windows on this platform can place and stack themselves."""
    return platform_name not in ("wayland", "wayland-egl", "offscreen", "minimal")


def overlay_limitation(platform_name: str) -> str | None:
    """A sentence explaining why the overlay will misbehave, or None."""
    if overlay_supported(platform_name):
        return None
    if platform_name.startswith("wayland"):
        return WAYLAND_ONLY_NOTE
    return f"The {platform_name} platform cannot show an overlay."
