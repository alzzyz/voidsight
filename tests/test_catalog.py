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
