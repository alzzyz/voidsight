"""Capture backends, against a fake X server.

The X11 backend is the one part of this project that cannot be exercised by the
test suite on the machine it runs on — there may be no display, and there is
certainly no Warframe. So the display is faked, and what is tested is the part
that went wrong in the field: when the backend looks for the game's window.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from voidsight.capture.base import CaptureError


class FakeWindow:
    def __init__(self, id: int, name: str | None, width: int, height: int) -> None:
        self.id = id
        self.name = name
        self.width = width
        self.height = height
        self.alive = True
        self.children: list[FakeWindow] = []

    def get_wm_name(self) -> str | None:
        return self.name

    def get_geometry(self) -> Any:
        if not self.alive:
            raise RuntimeError("BadWindow")
        return types.SimpleNamespace(width=self.width, height=self.height)

    def query_tree(self) -> Any:
        return types.SimpleNamespace(children=list(self.children))

    def get_image(self, x, y, width, height, fmt, mask) -> Any:
        if not self.alive:
            raise RuntimeError("BadWindow")
        # BGRX, which is what a real X server hands back.
        return types.SimpleNamespace(data=bytes([10, 20, 30, 0]) * (width * height))


class FakeDisplay:
    def __init__(self, root: FakeWindow) -> None:
        self.root = root
        self.tree_walks = 0
        self.closed = False

    def screen(self) -> Any:
        return types.SimpleNamespace(root=_CountingWindow(self.root, self))

    def create_resource_object(self, kind: str, id: int) -> FakeWindow:
        for window in self.root.children:
            if window.id == id:
                return window
        raise AssertionError(f"no such window {id}")

    def close(self) -> None:
        self.closed = True


class _CountingWindow:
    """The root, counting how often the backend walks the tree."""

    def __init__(self, window: FakeWindow, display: FakeDisplay) -> None:
        self._window = window
        self._display = display

    def query_tree(self) -> Any:
        self._display.tree_walks += 1
        return self._window.query_tree()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._window, name)


@pytest.fixture
def fake_x11(monkeypatch):
    """Install a fake Xlib, and hand back the root window to populate."""
    root = FakeWindow(1, None, 3440, 1440)
    display = FakeDisplay(root)

    module = types.ModuleType("Xlib")
    module.display = types.ModuleType("Xlib.display")
    module.display.Display = lambda *a, **k: display
    monkeypatch.setitem(sys.modules, "Xlib", module)
    monkeypatch.setitem(sys.modules, "Xlib.display", module.display)
    return root, display


def backend(window_name: str | None = None):
    from voidsight.capture.x11 import X11Backend

    return X11Backend(window_name)


def add_game(root: FakeWindow, *, id: int = 0x1400001, name: str = "Warframe") -> FakeWindow:
    window = FakeWindow(id, name, 3440, 1440)
    root.children.append(window)
    return window


class TestWindowDiscovery:
    """Both startup hooks run the client before the game exists."""

    def test_starts_without_the_game_window(self, fake_x11):
        root, _ = fake_x11
        capture = backend()
        # Constructing must not fail: the game is simply not up yet.
        assert capture.window_info is None
        assert capture.grab() is None

    def test_finds_the_window_once_the_game_appears(self, fake_x11):
        root, _ = fake_x11
        capture = backend()
        assert capture.grab() is None

        add_game(root)
        capture._next_search = 0.0  # the game started sooner than we would ask
        frame = capture.grab()
        assert frame is not None
        assert frame.shape == (1440, 3440, 3)
        assert capture.window_info.name == "Warframe"

    def test_does_not_walk_the_tree_on_every_frame(self, fake_x11):
        root, display = fake_x11
        capture = backend()
        walks = display.tree_walks
        for _ in range(10):
            capture.grab()
        # One construction-time search, and nothing added since.
        assert display.tree_walks == walks

    def test_recovers_when_the_window_dies(self, fake_x11):
        root, _ = fake_x11
        game = add_game(root)
        capture = backend()
        assert capture.grab() is not None

        game.alive = False  # the game exited, or Proton restarted it
        assert capture.grab() is None
        assert capture.window_info is None

        root.children.remove(game)
        replacement = add_game(root, id=0x1600001)
        capture._next_search = 0.0
        assert capture.grab() is not None
        assert capture.window_info.id == replacement.id

    def test_no_display_is_still_an_error(self, monkeypatch):
        """A machine with no X server cannot capture, and should say so."""
        module = types.ModuleType("Xlib")
        module.display = types.ModuleType("Xlib.display")

        def explode(*a, **k):
            raise RuntimeError("no protocol specified")

        module.display.Display = explode
        monkeypatch.setitem(sys.modules, "Xlib", module)
        monkeypatch.setitem(sys.modules, "Xlib.display", module.display)

        with pytest.raises(CaptureError, match="cannot open the X display"):
            backend()

    def test_window_title_lookup_tolerates_an_absent_game(self, fake_x11):
        from voidsight.capture.x11 import find_window_title

        assert find_window_title() is None
        add_game(fake_x11[0])
        assert find_window_title() == "Warframe"


class TestProbe:
    """A probe answers about right now, so it does report a missing window."""

    def test_reports_a_missing_window(self, fake_x11):
        from voidsight.capture import probe

        result = probe.probe_x11()
        assert not result.available
        assert "is Warframe running?" in result.detail

    def test_reports_a_capture(self, fake_x11):
        from voidsight.capture import probe

        add_game(fake_x11[0])
        result = probe.probe_x11()
        assert result.available
        assert "3440x1440" in result.detail
