"""Starting voidsight alongside Warframe.

Two mechanisms, and only one of them is ours to write.

**Steam launch options** are the idiomatic Linux approach: Steam runs
`voidsight launch -- %command%`, which starts the client, runs the game, and
tears the client down when the game exits. Lifetime is exactly right and no
daemon is involved. But those options live in Steam's `localconfig.vdf`, which
Steam rewrites wholesale when it exits — editing it behind Steam's back can
discard the change or, worse, damage every other game's options. So this module
*generates* the string and reports what Steam currently has; pasting it is the
user's job.

**A desktop autostart entry** is ours: a `.desktop` file in the XDG autostart
directory that launches the client at login, waiting for the game to appear.
That we can write and remove safely.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

APP_ID = "230410"
ENTRY_NAME = "voidsight.desktop"

#: Steam's per-user config, holding per-game launch options.
STEAM_ROOTS = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/data/Steam",
)


def supported() -> bool:
    """Autostart entries are an XDG thing, so Linux only."""
    return sys.platform == "linux"


def unsupported_reason() -> str | None:
    if supported():
        return None
    return (
        f"Autostart entries are a Linux desktop feature; this is {sys.platform}. "
        "The launch-option string below still applies to the Linux machine."
    )


def autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "autostart"


def autostart_path() -> Path:
    return autostart_dir() / ENTRY_NAME


def executable() -> str:
    """How to invoke voidsight from outside a shell that has the venv active."""
    if found := shutil.which("voidsight"):
        return found
    # Installed in a virtualenv that is not on PATH: name the interpreter.
    return f"{sys.executable} -m voidsight.cli"


def steam_launch_command() -> str:
    """The string to paste into Warframe's Steam launch options."""
    return f"{executable()} launch -- %command%"


def desktop_entry(exec_line: str | None = None) -> str:
    """The autostart file's contents."""
    command = exec_line or f"{executable()} app --wait-for-game"
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=voidsight",
            "Comment=Warframe relic reward prices",
            f"Exec={command}",
            "Terminal=false",
            "Categories=Game;Utility;",
            "X-GNOME-Autostart-enabled=true",
            "",
        ]
    )


def is_enabled() -> bool:
    return autostart_path().exists()


def enable(exec_line: str | None = None) -> Path:
    """Write the autostart entry. Returns the path written."""
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desktop_entry(exec_line))
    log.info("autostart enabled: %s", path)
    return path


def disable() -> bool:
    """Remove the autostart entry. True if one was there."""
    path = autostart_path()
    if not path.exists():
        return False
    path.unlink()
    log.info("autostart disabled: %s", path)
    return True


@dataclass(frozen=True)
class SteamState:
    """What Steam currently has configured for Warframe, if we can tell."""

    config_path: Path | None = None
    launch_options: str | None = None

    @property
    def already_hooked(self) -> bool:
        return bool(self.launch_options and "voidsight" in self.launch_options)


def find_steam_config() -> Path | None:
    """The first `localconfig.vdf` that mentions a user, or None."""
    for root in STEAM_ROOTS:
        userdata = Path(root).expanduser() / "userdata"
        if not userdata.is_dir():
            continue
        for user in sorted(userdata.iterdir()):
            candidate = user / "config" / "localconfig.vdf"
            if candidate.is_file():
                return candidate
    return None


def read_steam_state() -> SteamState:
    """Report Warframe's current launch options. Read-only, by design.

    The file is Valve's KeyValues format; rather than parse all of it, find the
    app's block and read the one field we care about. A parse miss returns None,
    which the UI treats as "unknown" rather than "empty".
    """
    path = find_steam_config()
    if path is None:
        return SteamState()
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        log.debug("could not read %s: %s", path, exc)
        return SteamState(config_path=path)

    marker = f'"{APP_ID}"'
    index = text.find(marker)
    if index == -1:
        return SteamState(config_path=path)

    # Search only the app's own block, so another game's options cannot be read
    # as Warframe's.
    window = text[index : index + 4000]
    key = '"LaunchOptions"'
    key_at = window.find(key)
    if key_at == -1:
        return SteamState(config_path=path, launch_options="")

    remainder = window[key_at + len(key) :]
    first = remainder.find('"')
    if first == -1:
        return SteamState(config_path=path, launch_options="")
    second = remainder.find('"', first + 1)
    value = remainder[first + 1 : second] if second != -1 else ""
    return SteamState(config_path=path, launch_options=value)
