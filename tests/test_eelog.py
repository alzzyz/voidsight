"""EE.log discovery, parsing and tailing, against synthetic logs."""

from __future__ import annotations

import time

import pytest

from voidsight.trigger import eelog

REWARD_LINE = "142.548 Sys [Info]: Got rewards"
RELIC_LINE = (
    "138.201 Sys [Diag]: Adding fixed rewards from "
    "/Lotus/Types/Game/Projections/T4VoidProjectionA1"
)


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "EE.log"
    path.write_text("0.001 Sys [Info]: Log started\n")
    return path


def collector():
    events: list[eelog.RewardEvent] = []
    return events, events.append


class TestFindLog:
    def test_explicit_path_wins(self, log_file):
        assert eelog.find_log(log_file) == log_file

    def test_missing_explicit_path_is_none(self, tmp_path):
        assert eelog.find_log(tmp_path / "nope.log") is None

    def test_reads_extra_library_folders(self, tmp_path, monkeypatch):
        steam = tmp_path / ".steam" / "steam"
        (steam / "steamapps").mkdir(parents=True)
        other = tmp_path / "games" / "steamapps"
        expected = other / "compatdata" / eelog.WARFRAME_APP_ID / eelog.LOG_RELATIVE
        expected.parent.mkdir(parents=True)
        expected.write_text("")
        (steam / "steamapps" / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n "1"\n {\n  "path"  "%s"\n }\n}\n' % (tmp_path / "games")
        )
        monkeypatch.setattr(eelog, "STEAM_ROOTS", (str(steam),))
        assert eelog.find_log() == expected


class TestParseRelic:
    @pytest.mark.parametrize(
        "tier,expected",
        [("T1", "Lith"), ("T2", "Meso"), ("T3", "Neo"), ("T4", "Axi"), ("T5", "Requiem")],
    )
    def test_maps_internal_tiers_to_eras(self, tier: str, expected: str):
        line = f"/Lotus/Types/Game/Projections/{tier}VoidProjectionB4"
        assert eelog.parse_relic(line) == f"{expected} B4"

    def test_ignores_unrelated_lines(self):
        assert eelog.parse_relic("Sys [Info]: Login succeeded") is None


class TestLogWatcher:
    def test_only_reads_what_is_appended(self, log_file):
        events, sink = collector()
        watcher = eelog.LogWatcher(log_file, sink)
        assert watcher.poll() == []

        with log_file.open("a") as handle:
            handle.write(REWARD_LINE + "\n")
        assert len(watcher.poll()) == 1
        assert watcher.poll() == []
        assert events[0].line.endswith("Got rewards")

    @pytest.mark.parametrize("trigger", eelog.REWARD_TRIGGERS)
    def test_fires_on_every_known_trigger(self, log_file, trigger: str):
        events, sink = collector()
        watcher = eelog.LogWatcher(log_file, sink)
        with log_file.open("a") as handle:
            handle.write(f"12.0 Sys [Info]: {trigger}\n")
        watcher.poll()
        assert len(events) == 1

    def test_attaches_the_relic_seen_before_the_reward(self, log_file):
        events, sink = collector()
        watcher = eelog.LogWatcher(log_file, sink)
        with log_file.open("a") as handle:
            handle.write(RELIC_LINE + "\n" + REWARD_LINE + "\n")
        watcher.poll()
        assert events[0].relic == "Axi A1"

    def test_ignores_ordinary_lines(self, log_file):
        events, sink = collector()
        watcher = eelog.LogWatcher(log_file, sink)
        with log_file.open("a") as handle:
            handle.write("12.0 Sys [Info]: Loading level\n34.0 Net [Info]: ping 30ms\n")
        watcher.poll()
        assert events == []

    def test_recovers_when_the_game_truncates_the_log(self, log_file):
        events, sink = collector()
        watcher = eelog.LogWatcher(log_file, sink)
        with log_file.open("a") as handle:
            handle.write(REWARD_LINE + "\n")
        watcher.poll()

        # Restarting Warframe replaces it with a fresh, shorter log.
        log_file.write_text(REWARD_LINE + "\n")
        assert log_file.stat().st_size < watcher._offset
        assert len(watcher.poll()) == 1

    def test_records_when_the_line_was_seen(self, log_file):
        events, sink = collector()
        watcher = eelog.LogWatcher(log_file, sink)
        before = time.monotonic()
        with log_file.open("a") as handle:
            handle.write(REWARD_LINE + "\n")
        watcher.poll()
        assert events[0].seen_at >= before

    def test_missing_file_is_not_fatal(self, tmp_path):
        events, sink = collector()
        watcher = eelog.LogWatcher(tmp_path / "gone.log", sink)
        assert watcher.poll() == []

    def test_start_and_stop_are_idempotent(self, log_file):
        _, sink = collector()
        watcher = eelog.LogWatcher(log_file, sink, interval=0.01)
        watcher.start()
        watcher.start()
        watcher.stop()
        watcher.stop()

    def test_background_thread_picks_up_appends(self, log_file):
        events, sink = collector()
        watcher = eelog.LogWatcher(log_file, sink, interval=0.01)
        watcher.start()
        try:
            with log_file.open("a") as handle:
                handle.write(REWARD_LINE + "\n")
            deadline = time.monotonic() + 2.0
            while not events and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            watcher.stop()
        assert len(events) == 1


class TestWatcherSurvival:
    """The watcher thread is the only thing that notices a reward screen."""

    def test_a_failing_callback_does_not_stop_later_triggers(self, tmp_path):
        seen: list[str] = []

        def explode_once(event):
            seen.append(event.line)
            if len(seen) == 1:
                raise RuntimeError("scan blew up")

        log_path = tmp_path / "EE.log"
        log_path.write_text("")
        watcher = eelog.LogWatcher(log_path, explode_once)

        log_path.write_text("Script [Info]: ProjectionRewardChoice.lua: Got rewards\n")
        watcher.poll()
        log_path.write_text(
            "Script [Info]: ProjectionRewardChoice.lua: Got rewards\n"
            "Script [Info]: ProjectionRewardChoice.lua: Got rewards\n"
        )
        watcher.poll()
        # The second trigger still arrived, which it would not have if the
        # first exception had propagated out of the thread.
        assert len(seen) == 2
