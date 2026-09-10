"""Artwork: two sources, a disk cache, and no network in tests."""

from __future__ import annotations

import httpx
import pytest

from voidsight.data import assets
from voidsight.data.catalog import Part

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def part(**overrides) -> Part:
    fields = {
        "name": "Nikana Prime Hilt",
        "set_name": "Nikana Prime",
        "kind": "Melee",
        "ducats": 100,
        "vaulted": False,
        "icon": "items/images/en/nikana_prime_hilt.abc123.png",
        "sub_icon": "sub_icons/weapon/prime_handle_128x128.png",
        "game_ref": "/Lotus/Types/Recipes/Weapons/WeaponParts/PrimeNikanaHandle",
    }
    fields.update(overrides)
    return Part(**fields)


@pytest.fixture(autouse=True)
def cache_in_tmp(tmp_path, monkeypatch):
    """Never touch the real icon cache."""
    monkeypatch.setattr(assets, "icon_dir", lambda: tmp_path)
    return tmp_path


def store_with(handler, **kwargs) -> assets.IconStore:
    return assets.IconStore(client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


class TestUrls:
    def test_market_url(self):
        assert assets.market_url("items/images/en/x.png").startswith(
            "https://warframe.market/static/assets/"
        )

    def test_export_url_survives_the_hash_suffix(self):
        # Public-export paths carry "!00_<hash>", which must be quoted, not lost.
        url = assets.export_url("/Lotus/Interface/Icons/X.png!00_a+b/c")
        assert url.startswith("https://content.warframe.com/PublicExport/")
        assert "%21" in url or "!" in url
        assert " " not in url

    def test_cache_paths_are_stable_and_legal(self):
        first = assets.cache_path("https://x/y.png!00_a+b")
        assert first == assets.cache_path("https://x/y.png!00_a+b")
        assert "!" not in first.name and "+" not in first.name
        assert first.suffix == ".png"

    def test_different_urls_do_not_collide(self):
        assert assets.cache_path("https://x/a.png") != assets.cache_path("https://x/b.png")


class TestFetch:
    def test_downloads_and_caches(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

        store = store_with(handler)
        path = store.fetch(part())
        assert path is not None and path.read_bytes() == PNG
        assert len(calls) == 1

        # Second time comes off disk.
        assert store.fetch(part()) == path
        assert len(calls) == 1
        store.close()

    def test_is_usable_as_a_context_manager(self):
        with store_with(lambda request: httpx.Response(200, content=PNG)) as store:
            assert store.fetch(part()) is not None

    def test_cached_returns_none_before_a_fetch(self):
        store = store_with(lambda request: httpx.Response(200, content=PNG))
        assert store.cached(part()) is None
        store.close()

    def test_an_item_without_an_icon_is_skipped(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, content=PNG)

        store = store_with(handler)
        assert store.fetch(part(icon=None)) is None
        assert calls == []
        store.close()

    def test_a_failure_is_not_retried(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(404)

        store = store_with(handler)
        assert store.fetch(part()) is None
        assert store.fetch(part()) is None
        # Repainting a card must not hammer a dead URL.
        assert len(calls) == 1
        store.close()

    def test_a_non_image_response_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>nope", headers={"content-type": "text/html"})

        store = store_with(handler)
        assert store.fetch(part()) is None
        store.close()

    def test_a_network_error_is_swallowed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline")

        store = store_with(handler)
        # Artwork is decoration; being offline must not raise into the UI.
        assert store.fetch(part()) is None
        store.close()
