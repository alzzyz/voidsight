"""Game detection: three independent signals, none of them required."""

from __future__ import annotations

import time

import pytest

from voidsight import game


class TestLogFreshness:
    def test_a_recently_written_log_means_running(self, tmp_path):
        path = tmp_path / "EE.log"
        path.write_text("Sys [Info]: hello")
        assert game.log_is_fresh(path)

    def test_an_old_log_does_not(self, tmp_path):
        path = tmp_path / "EE.log"
        path.write_text("Sys [Info]: hello")
        stale = time.time() - game.LOG_FRESH_SECONDS - 60
        import os

        os.utime(path, (stale, stale))
        assert not game.log_is_fresh(path)

    def test_a_missing_log_is_not_fresh(self, tmp_path):
        assert not game.log_is_fresh(tmp_path / "nope.log")
        assert not game.log_is_fresh(None)


class TestDetect:
    def test_reports_not_running_with_no_signals(self, monkeypatch):
        monkeypatch.setattr(game, "find_process", lambda: None)
        monkeypatch.setattr(game, "find_window", lambda name="Warframe": None)
        state = game.detect(None)
        assert not state.running
        assert state.summary == "Warframe not detected"
        assert state.detail == "no signals"

    def test_a_process_alone_is_enough(self, monkeypatch):
        monkeypatch.setattr(game, "find_process", lambda: 4242)
        monkeypatch.setattr(game, "find_window", lambda name="Warframe": None)
        state = game.detect(None)
        assert state.running
        assert state.pid == 4242
        # Still worth saying the window was not found: capture needs one.
        assert state.summary.endswith("(no window found)")

    def test_process_and_window_together(self, monkeypatch):
        monkeypatch.setattr(game, "find_process", lambda: 7)
        monkeypatch.setattr(game, "find_window", lambda name="Warframe": "Warframe")
        state = game.detect(None)
        assert state.running
        assert state.summary == "Warframe running"
        assert state.signals == ("process 7", "window")

    def test_a_fresh_log_alone_is_enough(self, monkeypatch, tmp_path):
        # The Flatpak case: no /proc, no X connection, but the log is being written.
        monkeypatch.setattr(game, "find_process", lambda: None)
        monkeypatch.setattr(game, "find_window", lambda name="Warframe": None)
        path = tmp_path / "EE.log"
        path.write_text("running")
        state = game.detect(path)
        assert state.running
        assert state.signals == ("recent EE.log",)

    def test_window_lookup_failures_are_swallowed(self, monkeypatch):
        def explode(name: str = "Warframe"):
            raise RuntimeError("no X display")

        monkeypatch.setattr(game, "find_process", lambda: None)
        monkeypatch.setattr(game.sys, "platform", "linux")
        monkeypatch.setattr("voidsight.capture.x11.find_window_title", explode)
        # Detection is advisory; a broken X connection must not raise.
        assert not game.detect(None).running


class TestProcessScan:
    def test_returns_nothing_off_linux(self, monkeypatch):
        monkeypatch.setattr(game.sys, "platform", "darwin")
        assert game.find_process() is None

    def test_finds_the_proton_executable(self, monkeypatch, tmp_path):
        # /proc, faked: one unrelated process and one Warframe under Wine.
        (tmp_path / "1").mkdir()
        (tmp_path / "1" / "cmdline").write_bytes(b"/usr/bin/bash\x00")
        (tmp_path / "5150").mkdir()
        (tmp_path / "5150" / "cmdline").write_bytes(
            b"Z:\\home\\me\\Warframe\\Warframe.x64.exe\x00-cluster:public\x00"
        )
        monkeypatch.setattr(game.sys, "platform", "linux")
        monkeypatch.setattr(game.os, "listdir", lambda path: ["1", "5150", "self"])

        real_open = open

        def fake_open(path, *args, **kwargs):
            name = str(path).replace("/proc/", "")
            return real_open(tmp_path / name, *args, **kwargs)

        monkeypatch.setattr("builtins.open", fake_open)
        assert game.find_process() == 5150


class TestClientSizePresets:
    """The app's own window size. Distinct from Warframe's UI Scaling, which is
    a percentage that has to match the game's own setting."""

    @pytest.mark.parametrize("label", ["S", "M", "L", "XL"])
    def test_every_label_has_a_geometry_and_a_font(self, label):
        from voidsight.config import client_size

        width, height, font = client_size(label)
        assert width > 0 and height > 0 and font > 0

    def test_sizes_increase_monotonically(self):
        from voidsight.config import CLIENT_SIZES

        widths = [width for _, width, _, _ in CLIENT_SIZES]
        fonts = [font for *_, font in CLIENT_SIZES]
        assert widths == sorted(widths)
        assert fonts == sorted(fonts)

    def test_labels_are_case_insensitive(self):
        from voidsight.config import client_size

        assert client_size("xl") == client_size("XL")

    def test_an_unknown_label_falls_back_to_medium(self):
        from voidsight.config import CLIENT_SIZES, client_size

        assert client_size("enormous") == CLIENT_SIZES[1][1:]

    def test_ui_scale_bounds_are_sane(self):
        from voidsight.config import MAX_UI_SCALE, MIN_UI_SCALE

        # Warframe's own UI Scaling is a percentage; ours mirrors it as a
        # multiplier, so 100% must be inside the range.
        assert MIN_UI_SCALE < 1.0 < MAX_UI_SCALE
