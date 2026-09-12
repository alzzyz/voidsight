"""The live path: a log trigger searching back through buffered frames."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from tests.test_app import market_stub
from tests.test_catalog import FILTERED_ITEMS, MARKET_ITEMS, PRICES
from voidsight.app.server import Session
from voidsight.app.state import ScanStore
from voidsight.capture.base import RingBuffer
from voidsight.config import Config
from voidsight.data import catalog as C
from voidsight.live import LiveRunner
from voidsight.trigger.eelog import RewardEvent
from voidsight.vision import match as match_module
from voidsight.vision.pipeline import Reward, ScanResult


@pytest.fixture
def catalog() -> C.Catalog:
    return C.build(FILTERED_ITEMS, PRICES, MARKET_ITEMS)


def frame(value: int) -> np.ndarray:
    return np.full((4, 4, 3), value, dtype=np.uint8)


def result_for(catalog: C.Catalog, names: list[str | None], confidence: float = 1.0) -> ScanResult:
    """A ScanResult with a controllable confidence, built from part names."""
    rewards = []
    for index, name in enumerate(names):
        part = catalog.lookup(name) if name else None
        rewards.append(
            Reward(
                index=index,
                raw_text=name or "???",
                ocr_confidence=confidence,
                match=match_module.Match(
                    raw_text=name or "???",
                    part=part,
                    score=100.0 * confidence if part else 20.0,
                    constrained=False,
                ),
            )
        )
    return ScanResult(rewards=rewards)


class ScriptedScanner:
    """Returns a prepared result per frame, keyed by the frame's fill value."""

    def __init__(self, catalog: C.Catalog, by_value: dict[int, ScanResult]) -> None:
        self.catalog = catalog
        self.theme = None
        self.by_value = by_value
        self.seen: list[int] = []
        self.relics: list[object] = []

    def scan(self, frame, *, relic=None, debug_dir=None) -> ScanResult:
        value = int(frame[0, 0, 0])
        self.seen.append(value)
        self.relics.append(relic)
        return self.by_value.get(value, ScanResult())


def build(catalog: C.Catalog, scanner, tmp_path, **kwargs) -> tuple[LiveRunner, Session]:
    # Off unless a test asks for it: this writes into the real state directory,
    # and a test suite has no business leaving PNGs in someone's home.
    kwargs.setdefault("keep_failures", False)
    log_path = tmp_path / "EE.log"
    log_path.write_text("")
    session = Session(
        scanner=scanner,
        market=market_stub(),
        config=Config(),
        store=ScanStore(),
        backend=None,
        buffer=RingBuffer(),
    )
    return LiveRunner(session, log_path, **kwargs), session


class TestBackscan:
    def test_uses_an_older_frame_when_the_newest_is_blank(self, catalog, tmp_path):
        # The reward screen was on frame 1; by the time the log line arrived the
        # screen had gone (frame 2). This is the case Wine's log buffering causes.
        good = result_for(catalog, ["Nidus Prime Chassis", "Bronco Prime Barrel"])
        scanner = ScriptedScanner(catalog, {1: good, 2: ScanResult()})
        runner, session = build(catalog, scanner, tmp_path)
        session.buffer.add(frame(1))
        session.buffer.add(frame(2))

        payload = runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))
        assert payload is not None
        assert [entry["name"] for entry in payload["rewards"]] == [
            "Nidus Prime Chassis Blueprint",
            "Bronco Prime Barrel",
        ]
        # Newest first: the blank frame was tried before the good one.
        assert scanner.seen == [2, 1]

    def test_stops_at_the_first_confident_frame(self, catalog, tmp_path):
        good = result_for(catalog, ["Nidus Prime Chassis"])
        scanner = ScriptedScanner(catalog, {1: good, 2: good, 3: good})
        runner, session = build(catalog, scanner, tmp_path)
        for value in (1, 2, 3):
            session.buffer.add(frame(value))

        runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))
        assert scanner.seen == [3]

    def test_does_not_scan_more_frames_than_allowed(self, catalog, tmp_path):
        scanner = ScriptedScanner(catalog, {})
        runner, session = build(catalog, scanner, tmp_path)
        runner.max_frames_scanned = 2
        for value in range(5):
            session.buffer.add(frame(value))

        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic())) is None
        assert len(scanner.seen) == 2

    def test_prefers_the_frame_that_identifies_more_rewards(self, catalog, tmp_path):
        partial = result_for(catalog, ["Nidus Prime Chassis", None], confidence=0.5)
        full = result_for(catalog, ["Nidus Prime Chassis", "Bronco Prime Barrel"], confidence=0.6)
        scanner = ScriptedScanner(catalog, {1: full, 2: partial})
        runner, session = build(catalog, scanner, tmp_path)
        session.buffer.add(frame(1))
        session.buffer.add(frame(2))

        payload = runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))
        assert len([e for e in payload["rewards"] if e["matched"]]) == 2

    def test_reports_nothing_when_no_frame_shows_rewards(self, catalog, tmp_path):
        scanner = ScriptedScanner(catalog, {})
        runner, session = build(catalog, scanner, tmp_path)
        session.buffer.add(frame(1))
        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic())) is None

    def test_empty_buffer_is_not_an_error(self, catalog, tmp_path):
        runner, _ = build(catalog, ScriptedScanner(catalog, {}), tmp_path)
        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic())) is None

    def test_passes_the_relic_from_the_log_to_the_scanner(self, catalog, tmp_path):
        good = result_for(catalog, ["Nidus Prime Chassis"])
        scanner = ScriptedScanner(catalog, {1: good})
        runner, session = build(catalog, scanner, tmp_path)
        session.buffer.add(frame(1))

        runner.scan_for(
            RewardEvent(line="Got rewards", seen_at=time.monotonic(), relic="Axi A1")
        )
        assert scanner.relics[0] is not None
        assert scanner.relics[0].name == "Axi A1"

    def test_unknown_relic_does_not_stop_the_scan(self, catalog, tmp_path):
        good = result_for(catalog, ["Nidus Prime Chassis"])
        scanner = ScriptedScanner(catalog, {1: good})
        runner, session = build(catalog, scanner, tmp_path)
        session.buffer.add(frame(1))

        payload = runner.scan_for(
            RewardEvent(line="Got rewards", seen_at=time.monotonic(), relic="Axi ZZ9")
        )
        assert payload is not None
        assert scanner.relics[0] is None

    def test_result_is_published_to_the_store(self, catalog, tmp_path):
        good = result_for(catalog, ["Nidus Prime Chassis"])
        scanner = ScriptedScanner(catalog, {1: good})
        runner, session = build(catalog, scanner, tmp_path)
        session.buffer.add(frame(1))

        runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))
        assert session.store.latest is not None
        assert session.store.latest["source"] == "ee.log"


class TestSilentFailures:
    """A trigger that yields nothing must say so.

    The first live run on real hardware had a working trigger and no capture
    backend, and the app's entire response was to carry on looking idle. There
    was nothing for the user to report and nothing in the window to read.
    """

    def test_an_empty_buffer_is_reported_as_a_capture_problem(self, catalog, tmp_path):
        problems: list[str] = []
        runner, _ = build(
            catalog, ScriptedScanner(catalog, {}), tmp_path, on_problem=problems.append
        )

        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic())) is None
        assert len(problems) == 1
        assert "no frames were captured" in problems[0]

    def test_unreadable_frames_are_reported_as_a_reading_problem(self, catalog, tmp_path):
        problems: list[str] = []
        runner, session = build(
            catalog, ScriptedScanner(catalog, {}), tmp_path, on_problem=problems.append
        )
        session.buffer.add(frame(1))

        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic())) is None
        # Frames existed and did not read: a different fault from having none.
        assert "1 captured frame did not read" in problems[0]

    def test_a_good_scan_reports_no_problem(self, catalog, tmp_path):
        problems: list[str] = []
        good = result_for(catalog, ["Nidus Prime Chassis"])
        runner, session = build(
            catalog, ScriptedScanner(catalog, {1: good}), tmp_path, on_problem=problems.append
        )
        session.buffer.add(frame(1))

        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))
        assert problems == []


class TestForwardWatch:
    """The trigger can land before the panel has rendered, not only after.

    Warframe writes "Got rewards" when the client receives them; the screen
    animates in afterwards. Looking only backwards meant the first trigger
    always failed and the result waited for the *next* log line — seconds into
    a screen that is only up for ten.
    """

    def live(self, session):
        """Mark the session as capturing from the game, which gates the watch."""

        class LiveBackend:
            name = "x11"

            def grab(self):
                return None

            def close(self):
                pass

        session.backend = LiveBackend()

    def test_reads_a_frame_that_arrives_after_the_trigger(self, catalog, tmp_path):
        good = result_for(catalog, ["Nidus Prime Chassis", "Bronco Prime Barrel"])
        scanner = ScriptedScanner(catalog, {7: good})
        runner, session = build(catalog, scanner, tmp_path, forward_seconds=3.0)
        self.live(session)
        runner.fps = 50.0
        # Nothing buffered yet: the screen is not up at the moment of the line.
        session.buffer.add(frame(1))

        def land_the_screen():
            time.sleep(0.2)
            session.buffer.add(frame(7))

        thread = threading.Thread(target=land_the_screen, daemon=True)
        thread.start()
        payload = runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))
        thread.join()

        assert payload is not None
        assert [entry["name"] for entry in payload["rewards"]] == [
            "Nidus Prime Chassis Blueprint",
            "Bronco Prime Barrel",
        ]

    def test_gives_up_after_the_deadline(self, catalog, tmp_path):
        runner, session = build(catalog, ScriptedScanner(catalog, {}), tmp_path,
                                forward_seconds=0.3)
        self.live(session)
        runner.fps = 50.0
        session.buffer.add(frame(1))

        started = time.monotonic()
        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic())) is None
        elapsed = time.monotonic() - started
        assert 0.2 < elapsed < 3.0

    def test_no_waiting_when_nothing_is_capturing(self, catalog, tmp_path):
        """A replayed or backend-less session has no new frames coming."""
        runner, session = build(catalog, ScriptedScanner(catalog, {}), tmp_path,
                                forward_seconds=5.0)
        assert session.backend is None
        session.buffer.add(frame(1))

        started = time.monotonic()
        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic())) is None
        assert time.monotonic() - started < 1.0

    def test_a_readable_buffer_does_not_wait_at_all(self, catalog, tmp_path):
        good = result_for(catalog, ["Nidus Prime Chassis"])
        runner, session = build(catalog, ScriptedScanner(catalog, {1: good}), tmp_path,
                                forward_seconds=5.0)
        self.live(session)
        session.buffer.add(frame(1))

        started = time.monotonic()
        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))
        assert time.monotonic() - started < 1.0

    def test_a_frame_is_never_read_twice(self, catalog, tmp_path):
        scanner = ScriptedScanner(catalog, {})
        runner, session = build(catalog, scanner, tmp_path, forward_seconds=0.4)
        self.live(session)
        runner.fps = 50.0
        session.buffer.add(frame(1))

        runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))
        # The buffer never gained a frame, so the one frame is read once.
        assert scanner.seen == [1]


class TestSettling:
    """One reward screen writes several trigger lines; one reading is enough."""

    def test_a_second_trigger_is_ignored_after_a_reading(self, catalog, tmp_path):
        good = result_for(catalog, ["Nidus Prime Chassis"])
        scanner = ScriptedScanner(catalog, {1: good})
        problems: list[str] = []
        runner, session = build(
            catalog, scanner, tmp_path, on_problem=problems.append, settle_seconds=30.0
        )
        session.buffer.add(frame(1))

        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))
        session.buffer.clear()
        # The screen is on its way out by the time the next line lands; without
        # settling this reports a failure to read what was just read.
        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic())) is None
        assert problems == []
        assert scanner.seen == [1]

    def test_a_later_mission_still_scans(self, catalog, tmp_path):
        good = result_for(catalog, ["Nidus Prime Chassis"])
        scanner = ScriptedScanner(catalog, {1: good, 2: good})
        runner, session = build(catalog, scanner, tmp_path, settle_seconds=0.0)
        session.buffer.add(frame(1))
        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))
        session.buffer.clear()
        session.buffer.add(frame(2))
        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))


class TestKeptFailures:
    """A reward screen that would not read is the only evidence there will be."""

    def test_an_unread_frame_is_saved(self, catalog, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        problems: list[str] = []
        runner, session = build(
            catalog,
            ScriptedScanner(catalog, {}),
            tmp_path,
            on_problem=problems.append,
            keep_failures=True,
        )
        session.buffer.add(frame(1))

        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic())) is None
        saved = sorted((tmp_path / "voidsight" / "unread").glob("reward-*.png"))
        assert len(saved) == 1
        assert saved[0].name in problems[0]

    def test_only_a_few_are_kept(self, catalog, tmp_path, monkeypatch):
        import time as time_module

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        runner, session = build(
            catalog, ScriptedScanner(catalog, {}), tmp_path, keep_failures=True
        )
        stamps = iter([f"2026091{n}-000000" for n in range(6)])
        monkeypatch.setattr(time_module, "strftime", lambda *a: next(stamps))

        for _ in range(5):
            session.buffer.add(frame(1))
            runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic()))

        kept = sorted((tmp_path / "voidsight" / "unread").glob("reward-*.png"))
        assert len(kept) == 3
        # Full-screen PNGs: the newest are the ones worth having.
        assert kept[-1].name == "reward-20260914-000000.png"

    def test_nothing_is_written_when_there_were_no_frames(self, catalog, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        runner, _ = build(
            catalog, ScriptedScanner(catalog, {}), tmp_path, keep_failures=True
        )

        assert runner.scan_for(RewardEvent(line="Got rewards", seen_at=time.monotonic())) is None
        assert not (tmp_path / "voidsight" / "unread").exists()


class TestCaptureLoop:
    def test_survives_having_no_backend_yet(self, catalog, tmp_path):
        """The loop starts before the game does, so it may have nothing to ask."""
        runner, session = build(catalog, ScriptedScanner(catalog, {}), tmp_path)
        assert session.backend is None
        runner.fps = 50.0
        runner.start()
        try:
            time.sleep(0.1)
        finally:
            runner.stop()
        assert len(session.buffer) == 0

    def test_picks_up_a_backend_that_arrives_late(self, catalog, tmp_path):
        """A backend set after start() must be used without a restart."""

        class LateBackend:
            name = "x11"

            def grab(self):
                return frame(1)

            def close(self):
                pass

        runner, session = build(catalog, ScriptedScanner(catalog, {}), tmp_path)
        runner.fps = 50.0
        runner.start()
        try:
            time.sleep(0.05)
            session.backend = LateBackend()
            deadline = time.monotonic() + 2.0
            while not len(session.buffer) and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            runner.stop()
        assert len(session.buffer) > 0
