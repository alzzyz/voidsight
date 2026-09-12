"""Capture buffer, replay backend, scan store and HTTP endpoints."""

from __future__ import annotations

import asyncio
import json

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from tests.test_catalog import FILTERED_ITEMS, MARKET_ITEMS, PRICES
from voidsight.app.server import Session, create_app
from voidsight.app.state import ScanStore, payload_for
from voidsight.capture.base import CaptureError, RingBuffer
from voidsight.capture.replay import ReplayBackend
from voidsight.config import Config
from voidsight.data import catalog as C
from voidsight.pricing.market import MarketClient
from voidsight.testing import render_reward_screen
from voidsight.vision import match as match_module
from voidsight.vision import theme as theme_module
from voidsight.vision.pipeline import Reward, ScanResult


@pytest.fixture
def catalog() -> C.Catalog:
    return C.build(FILTERED_ITEMS, PRICES, MARKET_ITEMS)


@pytest.fixture
def shots(tmp_path):
    """A directory of two synthetic reward screens."""
    names = ["Nidus Prime Chassis", "Bronco Prime Barrel"]
    for index, theme in enumerate(("Vitruvian", "Lotus")):
        frame = render_reward_screen(names, theme_name=theme, seed=index)
        cv2.imwrite(str(tmp_path / f"shot{index}.png"), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    return tmp_path


def reward(index: int, catalog: C.Catalog, name: str | None, raw: str = "") -> Reward:
    part = catalog.lookup(name) if name else None
    return Reward(
        index=index,
        raw_text=raw or (part.display_name.upper() if part else "GIBBERISH"),
        ocr_confidence=0.9,
        match=match_module.Match(
            raw_text=raw, part=part, score=100.0 if part else 30.0, constrained=False
        ),
    )


class StubScanner:
    """Stands in for the vision pipeline so app tests stay independent of OCR."""

    def __init__(self, catalog: C.Catalog, result: ScanResult) -> None:
        self.catalog = catalog
        self.theme = theme_module.get("Vitruvian")
        self.result = result
        self.calls = 0

    def scan(self, frame, *, relic=None, debug_dir=None) -> ScanResult:
        self.calls += 1
        return self.result


def market_stub(platinum: int = 12) -> MarketClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "sell": [
                        {
                            "platinum": platinum,
                            "quantity": 1,
                            "user": {"ingameName": "seller", "status": "ingame"},
                        }
                    ]
                }
            },
        )

    return MarketClient(client=httpx.Client(transport=httpx.MockTransport(handler)))


class TestRingBuffer:
    def test_returns_newest_first(self):
        buffer = RingBuffer()
        first, second = np.zeros((2, 2, 3), np.uint8), np.ones((2, 2, 3), np.uint8)
        buffer.add(first)
        buffer.add(second)
        recent = list(buffer.recent())
        assert len(recent) == 2
        assert recent[0].frame is second

    def test_drops_the_oldest_beyond_capacity(self):
        buffer = RingBuffer(max_frames=2)
        for _ in range(5):
            buffer.add(np.zeros((1, 1, 3), np.uint8))
        assert len(buffer) == 2

    def test_ignores_frames_older_than_the_window(self):
        buffer = RingBuffer()
        buffer.add(np.zeros((1, 1, 3), np.uint8))
        assert list(buffer.recent(seconds=0.0)) == []

    def test_newest_is_none_when_empty(self):
        assert RingBuffer().newest() is None


class TestRingBufferConcurrency:
    """One thread fills it while another searches it. That is its whole job.

    Iterating the deque directly raised "deque mutated during iteration" the
    first time a real capture loop ran alongside a real trigger, and because the
    exception landed in the log-watcher thread it took the trigger down with it
    for the rest of the session.
    """

    def test_searching_while_frames_land(self):
        import threading

        import numpy as np

        from voidsight.capture.base import RingBuffer

        buffer = RingBuffer(seconds=60.0)
        for _ in range(20):
            buffer.add(np.zeros((2, 2, 3), dtype=np.uint8))

        stop = threading.Event()
        errors: list[Exception] = []

        def fill():
            while not stop.is_set():
                buffer.add(np.zeros((2, 2, 3), dtype=np.uint8))

        writer = threading.Thread(target=fill, daemon=True)
        writer.start()
        try:
            for _ in range(200):
                try:
                    list(buffer.recent())
                except Exception as exc:  # the bug, if it is back
                    errors.append(exc)
                    break
        finally:
            stop.set()
            writer.join(timeout=2.0)
        assert not errors

    def test_snapshot_does_not_change_underneath_a_reader(self):
        import numpy as np

        from voidsight.capture.base import RingBuffer

        buffer = RingBuffer(seconds=60.0)
        buffer.add(np.zeros((2, 2, 3), dtype=np.uint8))
        shots = buffer.snapshot()
        buffer.add(np.zeros((2, 2, 3), dtype=np.uint8))
        assert len(shots) == 1


class TestReplayBackend:
    def test_serves_images_in_order_and_loops(self, shots):
        backend = ReplayBackend(shots)
        assert len(backend.paths) == 2
        first = backend.grab()
        assert backend.last_path.name == "shot0.png"
        backend.grab()
        backend.grab()
        assert backend.last_path.name == "shot0.png"
        assert first.shape[2] == 3

    def test_stops_at_the_end_when_not_looping(self, shots):
        backend = ReplayBackend(shots, loop=False)
        assert backend.grab() is not None
        assert backend.grab() is not None
        assert backend.grab() is None

    def test_rejects_a_directory_with_no_images(self, tmp_path):
        with pytest.raises(CaptureError):
            ReplayBackend(tmp_path)


class TestPayload:
    def test_describes_each_reward(self, catalog: C.Catalog):
        rewards = [reward(0, catalog, "Nidus Prime Chassis")]
        result = ScanResult(rewards=rewards, theme=theme_module.get("Lotus"))
        with market_stub(platinum=9) as market:
            quotes = market.quotes([rewards[0].part])
        payload = payload_for(result, quotes, source="test")

        assert payload["theme"] == "Lotus"
        assert payload["source"] == "test"
        assert payload["best"] == 0
        entry = payload["rewards"][0]
        assert entry["name"] == "Nidus Prime Chassis Blueprint"
        assert entry["matched"] is True
        assert entry["lowest"] == 9
        assert entry["ducats"] == 100
        assert entry["vaulted"] is True
        assert entry["live"] is True

    def test_marks_an_unreadable_reward(self, catalog: C.Catalog):
        rewards = [reward(0, catalog, None, raw="XVVQ RPME")]
        result = ScanResult(rewards=rewards)
        from voidsight.pricing.market import Quote

        payload = payload_for(result, [Quote(part=None)])
        entry = payload["rewards"][0]
        assert entry["matched"] is False
        assert entry["platinum"] is None
        assert entry["raw_text"] == "XVVQ RPME"

    def test_is_json_serialisable(self, catalog: C.Catalog):
        result = ScanResult(rewards=[reward(0, catalog, "Bronco Prime Barrel")])
        with market_stub() as market:
            quotes = market.quotes([result.rewards[0].part])
        json.dumps(payload_for(result, quotes))


class TestScanStore:
    def test_keeps_the_latest_and_a_capped_history(self):
        store = ScanStore(history=3)
        assert store.latest is None
        for index in range(5):
            store.publish({"at": index})
        assert store.latest == {"at": 4}
        assert [entry["at"] for entry in store.history] == [4, 3, 2]

    def test_publish_without_a_loop_does_not_raise(self):
        ScanStore().publish({"at": "now"})


class TestEndpoints:
    def session(self, catalog: C.Catalog, shots, *, backend=True) -> Session:
        rewards = [
            reward(0, catalog, "Nidus Prime Chassis"),
            reward(1, catalog, "Bronco Prime Barrel"),
        ]
        return Session(
            scanner=StubScanner(
                catalog, ScanResult(rewards=rewards, theme=theme_module.get("Vitruvian"))
            ),
            market=market_stub(),
            config=Config(prefer="ducats"),
            store=ScanStore(),
            backend=ReplayBackend(shots) if backend else None,
        )

    def test_serves_the_page(self, catalog: C.Catalog, shots):
        with TestClient(create_app(self.session(catalog, shots))) as client:
            response = client.get("/")
        assert response.status_code == 200
        assert "voidsight" in response.text

    def test_state_starts_empty(self, catalog: C.Catalog, shots):
        with TestClient(create_app(self.session(catalog, shots))) as client:
            state = client.get("/api/state").json()
        assert state["latest"] is None
        assert state["backend"] == "replay"
        assert state["prefer"] == "ducats"

    def test_scan_reads_prices_and_updates_state(self, catalog: C.Catalog, shots):
        session = self.session(catalog, shots)
        with TestClient(create_app(session)) as client:
            payload = client.post("/api/scan").json()
            assert payload["source"] == "manual"
            assert [entry["name"] for entry in payload["rewards"]] == [
                "Nidus Prime Chassis Blueprint",
                "Bronco Prime Barrel",
            ]
            # prefer=ducats, and the chassis is worth 100 to the barrel's 45.
            assert payload["best"] == 0
            assert client.get("/api/state").json()["latest"]["at"] == payload["at"]
        assert session.scanner.calls == 1

    def test_scan_without_a_backend_is_a_conflict(self, catalog: C.Catalog, shots):
        session = self.session(catalog, shots, backend=False)
        with TestClient(create_app(session)) as client:
            response = client.post("/api/scan")
        assert response.status_code == 409
        assert "backend" in response.json()["detail"]

    def test_event_endpoint_announces_a_stream(self, catalog: C.Catalog, shots):
        # The stream itself never ends, so it is exercised through ScanStore
        # below rather than over HTTP; here we only check it is wired up.
        app = create_app(self.session(catalog, shots))
        routes = {route.path for route in app.routes}
        assert "/api/events" in routes


class TestSettings:
    """Theme and UI scale are configuration the player sets, not guesses."""

    def session(self, catalog: C.Catalog, shots, config=None) -> Session:
        from voidsight.vision.pipeline import Scanner

        # One Config, shared — the scanner and the app must not drift apart.
        config = config or Config()
        return Session(
            scanner=Scanner(catalog, config=config, persist=False),
            market=market_stub(),
            config=config,
            store=ScanStore(),
            backend=ReplayBackend(shots),
        )

    def test_lists_every_theme_for_the_picker(self, catalog: C.Catalog, shots):
        with TestClient(create_app(self.session(catalog, shots))) as client:
            data = client.get("/api/settings").json()
        assert "Vitruvian" in data["themes"]
        assert len(data["themes"]) == 15
        assert data["theme"] is None  # unset means detect
        assert data["ui_scale"] == 1.0

    def test_selecting_a_theme_pins_it(self, catalog: C.Catalog, shots):
        session = self.session(catalog, shots)
        with TestClient(create_app(session)) as client:
            data = client.post("/api/settings", json={"theme": "Nidus"}).json()
        assert data["theme"] == "Nidus"
        assert session.scanner.theme.name == "Nidus"

    def test_empty_theme_means_detect_again(self, catalog: C.Catalog, shots):
        config = Config(theme="Lotus")
        session = self.session(catalog, shots, config)
        with TestClient(create_app(session)) as client:
            data = client.post("/api/settings", json={"theme": ""}).json()
        assert data["theme"] is None
        assert session.scanner.theme is None

    def test_rejects_an_unknown_theme(self, catalog: C.Catalog, shots):
        with TestClient(create_app(self.session(catalog, shots))) as client:
            response = client.post("/api/settings", json={"theme": "Nonesuch"})
        assert response.status_code == 400
        assert "Nonesuch" in response.json()["detail"]

    def test_sets_the_ui_scale(self, catalog: C.Catalog, shots):
        session = self.session(catalog, shots)
        with TestClient(create_app(session)) as client:
            data = client.post("/api/settings", json={"ui_scale": 1.25}).json()
        assert data["ui_scale"] == 1.25
        assert session.scanner.config.ui_scale == 1.25

    @pytest.mark.parametrize("scale", [0.1, 4.0])
    def test_rejects_an_implausible_ui_scale(self, catalog: C.Catalog, shots, scale: float):
        with TestClient(create_app(self.session(catalog, shots))) as client:
            response = client.post("/api/settings", json={"ui_scale": scale})
        assert response.status_code == 422

    def test_changes_which_reward_is_highlighted(self, catalog: C.Catalog, shots):
        session = self.session(catalog, shots)
        with TestClient(create_app(session)) as client:
            data = client.post("/api/settings", json={"prefer": "ducats"}).json()
            assert data["prefer"] == "ducats"
        assert session.config.prefer == "ducats"

    def test_rejects_an_unknown_preference(self, catalog: C.Catalog, shots):
        with TestClient(create_app(self.session(catalog, shots))) as client:
            response = client.post("/api/settings", json={"prefer": "credits"})
        assert response.status_code == 400

    def test_nothing_is_written_when_persistence_is_off(
        self, catalog: C.Catalog, shots, tmp_path, monkeypatch
    ):
        # The test suite and one-off scans must never edit the user's config.
        import voidsight.config as config_module

        path = tmp_path / "config.toml"
        monkeypatch.setattr(config_module, "config_path", lambda: path)
        with TestClient(create_app(self.session(catalog, shots))) as client:
            client.post("/api/settings", json={"prefer": "ducats", "theme": "Nidus"})
        assert not path.exists()

    def test_settings_are_written_to_disk(self, catalog: C.Catalog, tmp_path):
        from voidsight.vision.pipeline import Scanner

        path = tmp_path / "config.toml"
        scanner = Scanner(catalog, config=Config(), persist=True, config_path=path)
        scanner.set_theme("Zephyr")
        scanner.set_ui_scale(1.15)
        reloaded = Config.load(path)
        assert reloaded.theme == "Zephyr"
        assert reloaded.ui_scale == 1.15


class TestFanOut:
    """Scans arrive on a worker thread and must reach pages on the event loop."""

    @pytest.mark.asyncio
    async def test_subscribers_receive_published_scans(self):
        store = ScanStore()
        store.bind_loop(asyncio.get_running_loop())
        queue = store.subscribe()

        await asyncio.to_thread(store.publish, {"at": "now", "rewards": []})
        payload = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert payload["at"] == "now"

    @pytest.mark.asyncio
    async def test_unsubscribed_pages_stop_receiving(self):
        store = ScanStore()
        store.bind_loop(asyncio.get_running_loop())
        queue = store.subscribe()
        store.unsubscribe(queue)

        await asyncio.to_thread(store.publish, {"at": "now"})
        await asyncio.sleep(0)
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_a_stalled_page_does_not_block_others(self):
        store = ScanStore()
        store.bind_loop(asyncio.get_running_loop())
        stalled = store.subscribe()
        healthy = store.subscribe()
        for index in range(stalled.maxsize + 3):
            store.publish({"at": index})
            await asyncio.sleep(0)  # let the loop run the queued fan-out
            if not healthy.empty():
                healthy.get_nowait()
        # The slow queue filled up and dropped updates; publishing kept working.
        assert stalled.full()
        assert store.latest["at"] == stalled.maxsize + 2
