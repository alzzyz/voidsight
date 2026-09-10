"""User configuration, at ~/.config/voidsight/config.toml.

Some of this is set by the user (which capture backend, where EE.log is) and
some is *learned* at runtime and written back — the UI theme in particular.
Warframe's theme is an account-level setting, so it is not in the game's local
EE.cfg and cannot be read off disk; but it also almost never changes, so paying
to work it out once and remembering it is much better than re-deriving it from
every frame.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


#: Client window sizes. This is the app's own window, not Warframe's UI scale —
#: they were conflated once and the names now keep them apart. Each step scales
#: the window and its base font together, so it behaves like a zoom level rather
#: than the same text in a roomier frame.
CLIENT_SIZES: tuple[tuple[str, int, int, int], ...] = (
    ("S", 960, 580, 12),
    ("M", 1120, 660, 14),
    ("L", 1340, 790, 15),
    ("XL", 1600, 940, 17),
)

#: Warframe's own UI Scaling is a percentage in its Interface options, and ours
#: has to match it, so it is stored as a multiplier and edited as a percentage.
MIN_UI_SCALE, MAX_UI_SCALE = 0.5, 2.0


def client_size_labels() -> list[str]:
    return [label for label, *_ in CLIENT_SIZES]


DEFAULT_CLIENT_SIZE = "M"


def resolve_client_size(label: str | None) -> str:
    """A valid size label. A config file may hold anything; the window still
    has to open at a sensible size."""
    if label and label.upper() in client_size_labels():
        return label.upper()
    return DEFAULT_CLIENT_SIZE


def client_size(label: str) -> tuple[int, int, int]:
    """(width, height, font point size) for a size label."""
    for name, width, height, font in CLIENT_SIZES:
        if name == label.upper():
            return width, height, font
    return CLIENT_SIZES[1][1:]


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "voidsight"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class Config:
    #: Warframe UI theme name, or None to work it out from the next scan.
    theme: str | None = None
    #: Warframe's own "UI Scaling" setting, as a multiplier (1.0 = 100%).
    #: Decides how big the reward panel is on screen.
    ui_scale: float = 1.0
    #: Size of *this* app's window: S, M, L or XL.
    client_size: str = "M"
    #: Optional override for where the reward panel sits, as fractions of the
    #: frame: [x, y, width, height]. The built-in geometry comes from a
    #: 1920x1080 UI and is scaled, which should hold elsewhere — but it is a
    #: guess about someone else's monitor, so it can be corrected here without
    #: touching code. `voidsight scan --debug-dir` draws the box it used.
    panel_region: list[float] | None = None
    #: Draw reward prices over the game while it is running.
    overlay: bool = False
    #: Where on screen: top-left, top-right, bottom-left, bottom-right, top-centre.
    overlay_corner: str = "top-centre"
    #: Which monitor, by index. The game's screen, not the client's.
    overlay_screen: int = 0
    #: How long the overlay stays up. The reward screen lasts about ten seconds.
    overlay_seconds: float = 12.0
    overlay_opacity: float = 0.95
    #: "auto", "x11", "portal" or "replay".
    backend: str = "auto"
    #: Path to EE.log; None means auto-discover the Proton prefix.
    log_path: str | None = None
    #: PipeWire restore token, so screen-capture is approved only once.
    restore_token: str | None = None
    port: int = 8765
    #: "platinum" or "ducats" — which reward the UI highlights as best.
    prefer: str = "platinum"
    platform: str = "pc"

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or config_path()
        if not path.exists():
            return cls()
        try:
            raw = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as exc:
            log.warning("ignoring unreadable config %s: %s", path, exc)
            return cls()
        known = {field.name for field in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            log.warning("ignoring unknown config keys: %s", ", ".join(sorted(unknown)))
        return cls(**{key: value for key, value in raw.items() if key in known})

    def save(self, path: Path | None = None) -> Path:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# voidsight configuration", ""]
        for key, value in asdict(self).items():
            if value is None:
                continue
            lines.append(f"{key} = {_toml_value(value)}")
        path.write_text("\n".join(lines) + "\n")
        return path

    def learn_theme(self, theme_name: str, *, path: Path | None = None) -> bool:
        """Remember a theme discovered at runtime. True if this changed anything."""
        if self.theme == theme_name:
            return False
        log.info("learned UI theme %s; writing to %s", theme_name, path or config_path())
        self.theme = theme_name
        self.save(path)
        return True


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
