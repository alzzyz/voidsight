"""Icons, from QtAwesome.

Roughly 25,000 icons across FontAwesome, Material Design, Phosphor and Remix,
recoloured on demand. Names are indirected through this module so that swapping
an icon — or the whole set — is one edit here rather than a hunt through the
views.
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QIcon

log = logging.getLogger(__name__)

DEFAULT_SIZE = 16

#: Semantic name -> QtAwesome name. Material Design Icons throughout, for a
#: consistent stroke weight.
NAMES = {
    "settings": "mdi6.cog-outline",
    "connected": "mdi6.link-variant",
    "disconnected": "mdi6.link-variant-off",
    "scan": "mdi6.magnify-scan",
    "screenshot": "mdi6.image-outline",
    "overlay": "mdi6.picture-in-picture-top-right",
    "game": "mdi6.controller",
    "dot": "mdi6.circle-medium",
    "dot-hollow": "mdi6.circle-outline",
    "warning": "mdi6.alert-outline",
    "home": "mdi6.view-dashboard-outline",
    "quicksell": "mdi6.tag-outline",
    "sell": "mdi6.cash-multiple",
    "checked": "mdi6.check-circle",
    "unchecked": "mdi6.circle-outline",
    "blocked": "mdi6.minus-circle-outline",
}


def get(name: str, colour: str, size: int = DEFAULT_SIZE) -> QIcon:
    """An icon by semantic name, tinted. Never raises: a missing icon is not
    worth taking the window down for."""
    try:
        import qtawesome

        return qtawesome.icon(NAMES.get(name, name), color=colour)
    except Exception as exc:  # pragma: no cover - only if the font is missing
        log.debug("icon %r unavailable: %s", name, exc)
        return QIcon()


def gear(colour: str, size: int = DEFAULT_SIZE) -> QIcon:
    return get("settings", colour, size)


def dot(colour: str, size: int = DEFAULT_SIZE, *, hollow: bool = False) -> QIcon:
    return get("dot-hollow" if hollow else "dot", colour, size)


def link(colour: str, size: int = DEFAULT_SIZE, *, connected: bool = False) -> QIcon:
    return get("connected" if connected else "disconnected", colour, size)
