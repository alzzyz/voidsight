"""Pricing tests. No network: the market API is stubbed with httpx MockTransport."""

from __future__ import annotations

import time

import httpx
import pytest

from tests.test_catalog import FILTERED_ITEMS, MARKET_ITEMS, PRICES
from voidsight.data import catalog as C
from voidsight.pricing import market as M


def order(platinum: int, status: str = "ingame", quantity: int = 1) -> dict:
    return {
        "platinum": platinum,
        "quantity": quantity,
        "user": {"ingameName": f"seller{platinum}", "status": status},
    }


def response_for(orders: list[dict]) -> dict:
    return {"apiVersion": "0.25.0", "data": {"sell": orders, "buy": []}}


@pytest.fixture
def catalog() -> C.Catalog:
    return C.build(FILTERED_ITEMS, PRICES, MARKET_ITEMS)


class TestParseOffers:
    def test_sorts_cheapest_first(self):
        offers = M._parse_offers(response_for([order(30), order(7), order(12)]))
        assert [offer.platinum for offer in offers] == [7, 12, 30]

    def test_skips_sellers_who_are_not_reachable(self):
        offers = M._parse_offers(
            response_for([order(5, "offline"), order(9, "online"), order(11, "ingame")])
        )
        assert [offer.platinum for offer in offers] == [9, 11]

    def test_skips_malformed_orders(self):
        payload = response_for([{"user": {"status": "ingame"}}, order(4)])
        assert [offer.platinum for offer in M._parse_offers(payload)] == [4]

    def test_tolerates_an_empty_response(self):
        assert M._parse_offers({}) == []
        assert M._parse_offers({"data": {}}) == []


class TestRateLimiter:
    def test_spaces_requests_out(self):
        limiter = M.RateLimiter(per_second=20.0)
        started = time.monotonic()
        for _ in range(4):
            limiter.wait()
        # First call is free, the next three wait ~50ms each.
        assert time.monotonic() - started >= 0.1

    def test_first_call_does_not_wait(self):
        started = time.monotonic()
        M.RateLimiter(per_second=2.0).wait()
        assert time.monotonic() - started < 0.05


class TestMarketClient:
    def client(self, catalog: C.Catalog, orders: list[dict], counter: list[int]):
        def handler(request: httpx.Request) -> httpx.Response:
            counter.append(1)
            assert request.headers["Platform"] == "pc"
            assert "voidsight" in request.headers["User-Agent"]
            return httpx.Response(200, json=response_for(orders))

        transport = httpx.MockTransport(handler)
        headers = {"Platform": "pc", "User-Agent": "voidsight/test"}
        return M.MarketClient(client=httpx.Client(transport=transport, headers=headers))

    def test_quotes_live_prices(self, catalog: C.Catalog):
        calls: list[int] = []
        part = catalog.lookup("Nidus Prime Chassis Blueprint")
        with self.client(catalog, [order(11), order(9)], calls) as market:
            quote = market.quote(part)
        assert quote.live
        assert quote.lowest == 9
        assert quote.platinum == 9.0
        assert quote.ducats == 100
        assert quote.ducats_per_platinum == pytest.approx(100 / 9)

    def test_caches_within_the_window(self, catalog: C.Catalog):
        calls: list[int] = []
        part = catalog.lookup("Nidus Prime Chassis Blueprint")
        with self.client(catalog, [order(9)], calls) as market:
            market.quote(part)
            market.quote(part)
        assert len(calls) == 1

    def test_refetches_once_the_cache_expires(self, catalog: C.Catalog):
        calls: list[int] = []
        part = catalog.lookup("Nidus Prime Chassis Blueprint")
        with self.client(catalog, [order(9)], calls) as market:
            market.cache_seconds = 0.0
            market.quote(part)
            market.quote(part)
        assert len(calls) == 2

    def test_falls_back_to_the_average_when_the_api_fails(self, catalog: C.Catalog):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        part = catalog.lookup("Nidus Prime Chassis Blueprint")
        with M.MarketClient(client=httpx.Client(transport=httpx.MockTransport(handler))) as market:
            quote = market.quote(part)
        assert not quote.live
        assert quote.lowest is None
        # The cached WFInfo average still gives the player a number to act on.
        assert quote.platinum == pytest.approx(33.3)
        assert quote.error

    def test_skips_items_the_market_does_not_list(self, catalog: C.Catalog):
        calls: list[int] = []
        part = C.Part(name="Sagek Prime Barrel", set_name=None, kind=None, ducats=45, vaulted=False)
        with self.client(catalog, [order(9)], calls) as market:
            quote = market.quote(part)
        assert not calls
        assert quote.error == "not listed on warframe.market"

    def test_skips_untradeable_rewards(self, catalog: C.Catalog):
        calls: list[int] = []
        with self.client(catalog, [order(9)], calls) as market:
            quote = market.quote(catalog.lookup("Forma Blueprint"))
        assert not calls
        assert quote.platinum in (0.0, None)


class TestBestIndex:
    def quote(self, name: str, catalog: C.Catalog, platinum: int | None, ducats: int) -> M.Quote:
        part = C.Part(name=name, set_name=None, kind=None, ducats=ducats, vaulted=False)
        offers = [M.Offer(platinum, 1, "seller", "ingame")] if platinum is not None else []
        return M.Quote(part=part, offers=offers, live=bool(offers))

    def test_picks_the_most_platinum(self, catalog: C.Catalog):
        quotes = [
            self.quote("cheap", catalog, 4, 100),
            self.quote("rich", catalog, 40, 15),
            self.quote("mid", catalog, 12, 45),
        ]
        assert M.best_index(quotes) == 1

    def test_can_prefer_ducats(self, catalog: C.Catalog):
        quotes = [
            self.quote("cheap", catalog, 4, 100),
            self.quote("rich", catalog, 40, 15),
        ]
        assert M.best_index(quotes, prefer="ducats") == 0

    def test_ducats_break_a_platinum_tie(self, catalog: C.Catalog):
        quotes = [
            self.quote("a", catalog, 10, 15),
            self.quote("b", catalog, 10, 100),
        ]
        assert M.best_index(quotes) == 1

    def test_handles_unpriced_rewards(self, catalog: C.Catalog):
        quotes = [self.quote("none", catalog, None, 0), self.quote("some", catalog, 3, 15)]
        assert M.best_index(quotes) == 1

    def test_returns_none_without_quotes(self):
        assert M.best_index([]) is None

    def untradeable(self, name: str, ducats: int = 0) -> M.Quote:
        part = C.Part(
            name=name, set_name=None, kind=None, ducats=ducats,
            vaulted=False, tradeable=False,
        )
        return M.Quote(part=part)

    def test_never_recommends_an_untradeable_reward(self, catalog: C.Catalog):
        """Forma cannot be sold at any price, so it is not a "best" pick."""
        quotes = [self.untradeable("Forma Blueprint"), self.quote("sellable", catalog, 3, 15)]
        assert M.best_index(quotes) == 1

    def test_no_best_pick_when_nothing_is_tradeable(self):
        quotes = [self.untradeable("Forma Blueprint"), self.untradeable("Forma Blueprint")]
        assert M.best_index(quotes) is None

    def test_untradeable_does_not_win_on_ducats_either(self, catalog: C.Catalog):
        quotes = [
            self.untradeable("Forma Blueprint", ducats=999),
            self.quote("sellable", catalog, 1, 15),
        ]
        assert M.best_index(quotes, prefer="ducats") == 1
