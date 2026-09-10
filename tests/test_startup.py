"""Startup hooks: one file we own, one setting we only ever read."""

from __future__ import annotations

import pytest

from voidsight import startup


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Never write to the real autostart directory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def on_linux(monkeypatch):
    monkeypatch.setattr(startup.sys, "platform", "linux")


class TestAutostart:
    def test_disabled_by_default(self, on_linux):
        assert not startup.is_enabled()

    def test_enable_writes_a_desktop_entry(self, on_linux, tmp_path):
        path = startup.enable()
        assert path == tmp_path / "autostart" / "voidsight.desktop"
        text = path.read_text()
        assert text.startswith("[Desktop Entry]")
        assert "Type=Application" in text
        assert "--wait-for-game" in text
        assert startup.is_enabled()

    def test_disable_removes_it(self, on_linux):
        startup.enable()
        assert startup.disable()
        assert not startup.is_enabled()

    def test_disable_is_idempotent(self, on_linux):
        assert not startup.disable()

    def test_enable_is_idempotent(self, on_linux):
        first = startup.enable()
        second = startup.enable()
        assert first == second

    def test_a_custom_exec_line_is_honoured(self, on_linux):
        path = startup.enable("/usr/bin/voidsight app --overlay")
        assert "Exec=/usr/bin/voidsight app --overlay" in path.read_text()

    def test_unsupported_platforms_say_so(self, monkeypatch):
        monkeypatch.setattr(startup.sys, "platform", "darwin")
        assert not startup.supported()
        assert "Linux" in startup.unsupported_reason()

    def test_supported_platforms_have_no_caveat(self, on_linux):
        assert startup.supported()
        assert startup.unsupported_reason() is None


class TestSteamLaunchCommand:
    def test_names_an_executable_and_passes_the_game_through(self):
        command = startup.steam_launch_command()
        assert command.endswith("launch -- %command%")

    def test_falls_back_to_the_interpreter_when_not_on_path(self, monkeypatch):
        monkeypatch.setattr(startup.shutil, "which", lambda name: None)
        assert "-m voidsight.cli" in startup.executable()


class TestSteamState:
    def write_config(self, tmp_path, body: str):
        config = tmp_path / ".steam" / "steam" / "userdata" / "1234" / "config"
        config.mkdir(parents=True)
        path = config / "localconfig.vdf"
        path.write_text(body)
        return path

    @pytest.fixture(autouse=True)
    def fake_home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(startup, "STEAM_ROOTS", (str(tmp_path / ".steam" / "steam"),))
        return tmp_path

    def test_no_steam_install(self):
        state = startup.read_steam_state()
        assert state.config_path is None
        assert state.launch_options is None
        assert not state.already_hooked

    def test_reads_warframes_launch_options(self, tmp_path):
        self.write_config(
            tmp_path,
            '"apps"\n{\n  "230410"\n  {\n    "LaunchOptions"  "mangohud %command%"\n  }\n}\n',
        )
        state = startup.read_steam_state()
        assert state.launch_options == "mangohud %command%"
        assert not state.already_hooked

    def test_detects_that_the_hook_is_already_there(self, tmp_path):
        self.write_config(
            tmp_path,
            '"230410"\n{\n  "LaunchOptions"  "voidsight launch -- %command%"\n}\n',
        )
        assert startup.read_steam_state().already_hooked

    def test_empty_when_warframe_has_no_options(self, tmp_path):
        self.write_config(tmp_path, '"230410"\n{\n  "LastPlayed"  "1700000000"\n}\n')
        assert startup.read_steam_state().launch_options == ""

    def test_another_games_options_are_not_read_as_warframes(self, tmp_path):
        # The app id block must bound the search, or a neighbouring game's
        # options would be reported as Warframe's.
        self.write_config(
            tmp_path,
            '"9999"\n{\n  "LaunchOptions"  "gamescope %command%"\n}\n'
            + '"230410"\n{\n  "LastPlayed" "1"\n}\n',
        )
        assert startup.read_steam_state().launch_options == ""

    def test_warframe_absent_from_the_config(self, tmp_path):
        path = self.write_config(tmp_path, '"apps"\n{\n  "440"\n  {\n  }\n}\n')
        state = startup.read_steam_state()
        assert state.config_path == path
        assert state.launch_options is None

    def test_an_unreadable_config_does_not_raise(self, tmp_path, monkeypatch):
        path = self.write_config(tmp_path, '"230410" { }')

        def explode(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(type(path), "read_text", explode)
        state = startup.read_steam_state()
        assert state.config_path is not None
        assert state.launch_options is None


class TestSingleInstance:
    """Two clients tailing one log and capturing one screen is worse than one."""

    def test_first_holder_wins(self, tmp_path):
        from voidsight.instance import SingleInstance

        path = tmp_path / "voidsight.lock"
        first = SingleInstance(path)
        assert first.acquire()
        assert not SingleInstance(path).acquire()
        first.release()

    def test_the_lock_is_reusable_after_release(self, tmp_path):
        from voidsight.instance import SingleInstance

        path = tmp_path / "voidsight.lock"
        first = SingleInstance(path)
        first.acquire()
        first.release()
        second = SingleInstance(path)
        assert second.acquire()
        second.release()

    def test_a_losing_contender_does_not_erase_the_holders_pid(self, tmp_path):
        import os

        from voidsight.instance import SingleInstance

        # Opening the lock file with "w" would truncate it before the loser
        # discovered it lost, leaving nothing to name in the error message.
        path = tmp_path / "voidsight.lock"
        holder = SingleInstance(path)
        holder.acquire()
        SingleInstance(path).acquire()
        assert holder.holder_pid() == os.getpid()
        holder.release()

    def test_works_as_a_context_manager(self, tmp_path):
        from voidsight.instance import SingleInstance

        path = tmp_path / "voidsight.lock"
        with SingleInstance(path) as lock:
            assert lock.acquired
            assert not SingleInstance(path).acquire()
        assert SingleInstance(path).acquire()

    def test_an_unusable_lock_directory_does_not_block_the_app(self, tmp_path, monkeypatch):
        from voidsight.instance import SingleInstance

        # A companion app must not refuse to start because of a lock file.
        lock = SingleInstance(tmp_path / "missing" / "deeper" / "voidsight.lock")
        assert lock.acquire()
