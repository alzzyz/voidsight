"""Catalog tests. Network-free: feeds are stubbed with the shapes upstream uses."""

from __future__ import annotations

import pytest

from voidsight.data import catalog as C

FILTERED_ITEMS = {
    "eqmt": {
        "Nidus Prime": {
            "type": "Warframes",
            "vaulted": True,
            "parts": {
                "Nidus Prime Blueprint": {"count": 1, "ducats": 45, "vaulted": True},
                "Nidus Prime Chassis": {"count": 1, "ducats": 100, "vaulted": True},
            },
        },
        "Bronco Prime": {
            "type": "Secondary",
            "vaulted": False,
            "parts": {
                # Old weapons list their blueprint without the suffix the game shows.
                "Bronco Prime": {"count": 1, "ducats": 15, "vaulted": False},
                "Bronco Prime Barrel": {"count": 1, "ducats": 45, "vaulted": False},
            },
        },
    },
    "ignored_items": {"Forma Blueprint": {"plat": 0, "ducats": 0, "volume": 0}},
    "relics": {
        "Axi": {
            "A1": {
                "vaulted": True,
                "rare1": "Nidus Prime Blueprint",
                "uncommon1": "Bronco Prime Barrel",
                "uncommon2": "Nidus Prime Chassis",
                "common1": "Forma Blueprint",
                "common2": "Bronco Prime",
                "common3": "Bronco Prime Barrel",
            },
            "A10": {
                "vaulted": False,
                "rare1": "Bronco Prime",
                "common1": "Forma Blueprint",
            },
        }
    },
}

PRICES = [
    {
        "name": "Nidus Prime Chassis Blueprint",
        "custom_avg": "33.3",
        "today_vol": "12",
        "yesterday_vol": "9",
    },
    {"name": "Bronco Prime Blueprint", "custom_avg": "2.7", "today_vol": "4", "yesterday_vol": "6"},
]

MARKET_ITEMS = {
    "data": [
        {
            "id": "abc123",
            "slug": "nidus_prime_chassis_blueprint",
            "ducats": 100,
            "i18n": {"en": {"name": "Nidus Prime Chassis Blueprint"}},
        },
        {
            "id": "def456",
            "slug": "bronco_prime_blueprint",
            "ducats": 15,
            "i18n": {"en": {"name": "Bronco Prime Blueprint"}},
        },
    ]
}


@pytest.fixture
def cat() -> C.Catalog:
    return C.build(FILTERED_ITEMS, PRICES, MARKET_ITEMS)


def test_normalize_drops_punctuation_and_digits():
    assert C.normalize("Nidus Prime  Chassis!") == "nidus prime chassis"
    assert C.normalize("Axi A1") == "axi a"


def test_normalize_relic_keeps_digits():
    assert C.normalize_relic("Axi A10") == "axi a10"


@pytest.mark.parametrize(
    "text",
    [
        "Nidus Prime Chassis",  # as filtered_items names it
        "Nidus Prime Chassis Blueprint",  # as the game and market name it
        "NIDUS PRIME CHASSIS BLUEPRINT",  # as OCR reads it off the screen
        "nidus  prime   chassis blueprint",
    ],
)
def test_component_resolves_under_every_spelling(cat: C.Catalog, text: str):
    part = cat.lookup(text)
    assert part is not None
    assert part.name == "Nidus Prime Chassis"
    assert part.display_name == "Nidus Prime Chassis Blueprint"


def test_display_name_is_taken_from_the_live_feeds(cat: C.Catalog):
    # "Bronco Prime" is a blueprint everywhere except filtered_items.
    part = cat.lookup("Bronco Prime Blueprint")
    assert part is not None and part.name == "Bronco Prime"
    assert part.display_name == "Bronco Prime Blueprint"
    assert part.slug == "bronco_prime_blueprint"


def test_market_and_price_data_are_merged(cat: C.Catalog):
    part = cat.lookup("Nidus Prime Chassis Blueprint")
    assert part.slug == "nidus_prime_chassis_blueprint"
    assert part.item_id == "abc123"
    assert part.ducats == 100
    assert part.avg_plat == pytest.approx(33.3)
    assert part.volume_today == 12
    assert part.volume_yesterday == 9
    assert part.vaulted is True


def test_ignored_items_are_known_but_not_tradeable(cat: C.Catalog):
    part = cat.lookup("Forma Blueprint")
    assert part is not None
    assert part.tradeable is False
    assert part.ducats == 0


def test_relic_codes_with_shared_prefixes_stay_distinct(cat: C.Catalog):
    a1 = cat.relic("Axi A1")
    a10 = cat.relic("Axi A10")
    assert a1 is not None and a10 is not None
    assert a1.code == "A1" and a10.code == "A10"
    assert len(a1.rewards) == 6
    assert a1.rewards[0] == C.RelicReward("Nidus Prime Blueprint", "rare")
    assert a1.vaulted is True and a10.vaulted is False


@pytest.mark.parametrize("spelling", ["Axi A1", "AxiA1", "axi a1", "Axi A1 Relic"])
def test_relic_lookup_tolerates_spelling(cat: C.Catalog, spelling: str):
    assert cat.relic(spelling) is not None


def test_every_relic_reward_resolves_to_a_part(cat: C.Catalog):
    unresolved = [
        reward.part_name
        for relic in cat.relics.values()
        for reward in relic.rewards
        if cat.lookup(reward.part_name) is None
    ]
    assert unresolved == []


class TestDegradedCatalog:
    """One host being down should not stop the app starting.

    WFInfo's item table, relic tables and price feed all live on
    api.warframestat.us. When it is unreachable — as it was, returning 502, on
    the day this was written — warframe.market's own list still names every
    prime part, which is what OCR matches against.
    """

    def market_payload(self):
        return {
            "data": [
                {
                    "id": "1", "slug": "nikana_prime_blueprint", "ducats": 25,
                    "gameRef": "/Lotus/Types/Recipes/Weapons/PrimeNikanaBlueprint",
                    "tags": ["weapon", "prime", "melee", "blueprint"],
                    "i18n": {"en": {"name": "Nikana Prime Blueprint", "icon": "a.png",
                                    "subIcon": "b.png"}},
                },
                {
                    "id": "2", "slug": "nikana_prime_set", "ducats": 190,
                    "tags": ["weapon", "prime", "set", "melee"],
                    "i18n": {"en": {"name": "Nikana Prime Set"}},
                },
                {
                    "id": "3", "slug": "braton_vandal_stock",
                    "tags": ["weapon", "component"],
                    "i18n": {"en": {"name": "Braton Vandal Stock"}},
                },
            ]
        }

    def test_builds_prime_parts_from_the_market_alone(self):
        cat = C.build_from_market(self.market_payload())
        assert [part.display_name for part in cat.parts.values()] == ["Nikana Prime Blueprint"]

    def test_a_set_is_not_a_reward(self):
        # Sets are tradeable but never appear on a reward screen.
        cat = C.build_from_market(self.market_payload())
        assert cat.lookup("Nikana Prime Set") is None

    def test_non_prime_items_are_skipped(self):
        cat = C.build_from_market(self.market_payload())
        assert cat.lookup("Braton Vandal Stock") is None

    def test_carries_what_pricing_and_artwork_need(self):
        part = C.build_from_market(self.market_payload()).lookup("Nikana Prime Blueprint")
        assert part.slug == "nikana_prime_blueprint"
        assert part.ducats == 25
        assert part.icon == "a.png"
        assert part.game_ref.endswith("PrimeNikanaBlueprint")

    def test_announces_what_is_missing(self):
        cat = C.build_from_market(self.market_payload())
        assert cat.degraded
        assert "filtered_items" in cat.missing
        assert cat.relics == {}

    def test_absent_data_is_left_absent(self):
        # Vaulted defaults to False, which shows no badge — an absence, not a
        # claim that the item is unvaulted. Prices are None, not zero.
        part = C.build_from_market(self.market_payload()).lookup("Nikana Prime Blueprint")
        assert part.avg_plat is None
        assert part.volume_today is None

    def test_a_full_catalog_is_not_degraded(self):
        cat = C.build(FILTERED_ITEMS, PRICES, MARKET_ITEMS)
        assert not cat.degraded
        assert cat.missing == ()

    def test_load_falls_back_when_wfinfo_is_down(self, monkeypatch):
        import httpx

        from voidsight.data import sources

        def fake_load(feed, **kwargs):
            if feed is sources.MARKET_ITEMS:
                return self.market_payload()
            raise httpx.HTTPError("502 Bad Gateway")

        monkeypatch.setattr(sources, "load", fake_load)
        cat = C.load()
        assert cat.degraded
        assert cat.lookup("Nikana Prime Blueprint") is not None

    def test_losing_the_market_too_is_fatal(self, monkeypatch):
        import httpx

        from voidsight.data import sources

        def fake_load(feed, **kwargs):
            raise httpx.HTTPError("502 Bad Gateway")

        monkeypatch.setattr(sources, "load", fake_load)
        # Nothing to match against at all; the caller must hear about it.
        with pytest.raises(httpx.HTTPError):
            C.load()
