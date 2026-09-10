"""One client at a time.

Both startup hooks can be switched on at once — a login entry that waits for
the game, plus Steam launch options that start a client with it — and nothing
stopped that producing two clients tailing the same log, capturing the same
screen and stacking two windows. An advisory file lock is enough: the kernel
drops it when the holder dies, so a crashed client leaves nothing to clean up,
unlike a PID file.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

LOCK_NAME = "voidsight.lock"


def runtime_dir() -> Path:
    """Where transient per-user state belongs, with fallbacks."""
    if runtime := os.environ.get("XDG_RUNTIME_DIR"):
        return Path(runtime)
    if cache := os.environ.get("XDG_CACHE_HOME"):
        return Path(cache)
    return Path.home() / ".cache"


def lock_path() -> Path:
    directory = runtime_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / LOCK_NAME


class SingleInstance:
    """Holds the lock for as long as it is open.

    Used as a context manager. `acquired` is False when another client already
    holds it, which is a normal outcome rather than an error: a second launch
    should stand aside quietly.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or lock_path()
        self.acquired = False
        self._handle = None

    def __enter__(self) -> SingleInstance:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def acquire(self) -> bool:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows
            self.acquired = True
            return True

        # Open without truncating: a losing contender must not wipe the file
        # before it discovers it lost, or the holder's recorded pid is gone and
        # the error message has nothing useful to say.
        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            log.warning("could not open %s: %s", self.path, exc)
            self.acquired = True  # never block the app over a lock file
            return True

        handle = os.fdopen(descriptor, "r+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            self.acquired = False
            log.info("another voidsight client already holds %s", self.path)
            return False

        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        self._handle = handle
        self.acquired = True
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.close()
        except OSError:  # pragma: no cover
            pass
        self._handle = None
        self.acquired = False

    def holder_pid(self) -> int | None:
        """PID recorded in the lock file, for a useful message."""
        try:
            return int(self.path.read_text().strip())
        except (OSError, ValueError):
            return None
