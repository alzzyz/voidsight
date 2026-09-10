"""The live path: a log trigger searching back through buffered frames."""

from __future__ import annotations

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


def build(catalog: C.Catalog, scanner, tmp_path) -> tuple[LiveRunner, Session]:
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
    return LiveRunner(session, log_path), session


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
