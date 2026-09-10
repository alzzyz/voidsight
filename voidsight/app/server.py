"""The local HTTP app: one page, one event stream, one manual trigger."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from voidsight.app.state import ScanStore, payload_for
from voidsight.capture.base import CaptureBackend, RingBuffer
from voidsight.config import Config, config_path
from voidsight.data.catalog import Catalog
from voidsight.pricing.market import MarketClient
from voidsight.vision import theme as theme_module
from voidsight.vision.pipeline import Scanner


class SettingsUpdate(BaseModel):
    """What the settings panel can change. Empty theme means "detect it"."""

    theme: str | None = None
    ui_scale: float | None = Field(default=None, ge=0.5, le=2.0)
    prefer: str | None = None

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"

#: Backends that read the running game. Anything else is a saved frame.
LIVE_BACKENDS = frozenset({"x11", "portal"})


@dataclass
class Session:
    """Everything a running app needs, so tests can assemble it piecemeal."""

    scanner: Scanner
    market: MarketClient
    config: Config
    store: ScanStore
    backend: CaptureBackend | None = None
    buffer: RingBuffer | None = None

    @property
    def catalog(self) -> Catalog:
        return self.scanner.catalog

    def save_config(self) -> None:
        """Persist settings, honouring the scanner's persistence choice.

        Writing goes through the scanner because that is what knows whether
        this session is allowed to touch the user's config file at all — tests
        and one-off scans run with it switched off.
        """
        if getattr(self.scanner, "persist", False):
            self.config.save(getattr(self.scanner, "config_path", None))

    def scan_now(self, *, source: str | None = None) -> dict[str, Any]:
        """Grab a frame, read it, price it, and publish the result."""
        if self.backend is None:
            raise RuntimeError("no capture backend configured")
        frame = self.backend.grab()
        if frame is None:
            raise RuntimeError(f"{self.backend.name} backend returned no frame")
        if self.buffer is not None:
            self.buffer.add(frame)
        return self.scan_frame(frame, source=source or self.describe_source())

    def describe_source(self) -> str:
        """Where the last frame came from, named precisely.

        A result that came out of a saved screenshot must not be presentable as
        one that came out of the game — the numbers look identical and the items
        end up in front of a sell button either way.
        """
        if self.backend is None:
            return "none"
        if last := getattr(self.backend, "last_path", None):
            return f"{self.backend.name}: {Path(last).name}"
        return self.backend.name

    @property
    def live_capture(self) -> bool:
        """Whether frames are coming from the running game rather than a file."""
        return self.backend is not None and self.backend.name in LIVE_BACKENDS

    def read(self, frame, *, relic=None):
        """Vision only. Kept separate from pricing so that searching back
        through buffered frames does not fire a market request per frame."""
        return self.scanner.scan(frame, relic=relic)

    def price_and_publish(self, result, *, source: str | None = None) -> dict[str, Any]:
        """Attach live prices to a reading and push it to open pages."""
        quotes = self.market.quotes([reward.part for reward in result.rewards if reward.part])
        # Re-align quotes with rewards, since unmatched rewards have no part.
        aligned = []
        pending = list(quotes)
        for reward in result.rewards:
            if reward.part is not None and pending:
                aligned.append(pending.pop(0))
            else:
                aligned.append(_empty_quote(reward))
        payload = payload_for(
            result,
            aligned,
            prefer=self.config.prefer,
            source=source,
            live_capture=self.live_capture,
        )
        self.store.publish(payload)
        return payload

    def scan_frame(self, frame, *, source: str | None = None, relic=None) -> dict[str, Any]:
        return self.price_and_publish(self.read(frame, relic=relic), source=source)


def _empty_quote(reward):
    from voidsight.pricing.market import Quote

    return Quote(part=reward.part, live=False)


def create_app(session: Session) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Scans are published from worker threads; the store needs this loop to
        # hand them to connected pages.
        session.store.bind_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(title="voidsight", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return {
            "latest": session.store.latest,
            "history": session.store.history,
            "theme": session.scanner.theme.name if session.scanner.theme else None,
            "backend": session.backend.name if session.backend else None,
            "prefer": session.config.prefer,
        }

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return _settings_payload(session)

    @app.post("/api/settings")
    async def set_settings(update: SettingsUpdate) -> dict[str, Any]:
        """Apply settings from the page and write them to the config file."""
        try:
            if update.theme is not None:
                session.scanner.set_theme(update.theme or None)
            if update.ui_scale is not None:
                session.scanner.set_ui_scale(update.ui_scale)
            if update.prefer is not None:
                if update.prefer not in ("platinum", "ducats"):
                    raise ValueError("prefer must be 'platinum' or 'ducats'")
                session.config.prefer = update.prefer
                session.save_config()
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc).strip("'")) from exc
        return _settings_payload(session)

    @app.post("/api/scan")
    async def scan() -> dict[str, Any]:
        try:
            return await run_in_threadpool(session.scan_now, source="manual")
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        queue = session.store.subscribe()

        async def stream():
            try:
                if latest := session.store.latest:
                    yield _sse(latest)
                while True:
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield _sse(payload)
            finally:
                session.store.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _settings_payload(session: Session) -> dict[str, Any]:
    # Theme and scale are read back from the scanner's own config, since that is
    # the copy it scans with; reporting a different object could show a setting
    # that is not actually in effect.
    scanner_config = getattr(session.scanner, "config", session.config)
    return {
        "theme": session.scanner.theme.name if session.scanner.theme else None,
        "themes": [theme.name for theme in theme_module.THEMES],
        "ui_scale": scanner_config.ui_scale,
        "prefer": session.config.prefer,
        "config_path": str(config_path()),
    }


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"
