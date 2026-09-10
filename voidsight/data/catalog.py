"""One lookup table merging the three feeds.

`filtered_items` supplies the item names, ducat values, vaulted flags and relic
drop tables; `prices` supplies rolling average platinum and daily volume;
warframe.market's item list supplies the slug and id needed to ask for live
orders.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from voidsight.data import sources

RARITIES = (
    ("rare1", "rare"),
    ("uncommon1", "uncommon"),
    ("uncommon2", "uncommon"),
    ("common1", "common"),
    ("common2", "common"),
    ("common3", "common"),
)

_NON_ALPHA = re.compile(r"[^a-z]+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(name: str) -> str:
    """Collapse an item name to lowercase words, dropping punctuation and digits.

    OCR output is noisy around non-letters, so item matching happens in this
    space. Digits are dropped because no item name contains one.
    """
    return " ".join(_NON_ALPHA.sub(" ", name.lower()).split())


def normalize_relic(name: str) -> str:
    """Like `normalize`, but keeps digits — relic codes are e.g. "Axi A1"."""
    return " ".join(_NON_ALNUM.sub(" ", name.lower()).split())


@dataclass(frozen=True)
class Part:
    """A single relic reward."""

    name: str
    set_name: str | None
    kind: str | None
    ducats: int
    vaulted: bool
    tradeable: bool = True
    #: Name as the game and warframe.market spell it, which for warframe
    #: components carries a "Blueprint" suffix that `name` lacks.
    display_name: str = ""
    slug: str | None = None
    item_id: str | None = None
    #: The game's own identifier, e.g.
    #: "/Lotus/Types/Recipes/Weapons/WeaponParts/PrimeNikanaHandle". Shared with
    #: DE's public export and with inventory payloads, so it is the key that
    #: joins our data to anything of the game's.
    game_ref: str | None = None
    #: warframe.market asset paths, relative to its static host.
    icon: str | None = None
    sub_icon: str | None = None
    avg_plat: float | None = None
    volume_today: int | None = None
    volume_yesterday: int | None = None

    def __post_init__(self) -> None:
        if not self.display_name:
            object.__setattr__(self, "display_name", self.name)


@dataclass(frozen=True)
class RelicReward:
    part_name: str
    rarity: str


@dataclass(frozen=True)
class Relic:
    era: str
    code: str
    vaulted: bool
    rewards: tuple[RelicReward, ...]

    @property
    def name(self) -> str:
        return f"{self.era} {self.code}"


@dataclass
class Catalog:
    parts: dict[str, Part] = field(default_factory=dict)
    relics: dict[str, Relic] = field(default_factory=dict)
    #: normalized text (including aliases) -> canonical part name
    aliases: dict[str, str] = field(default_factory=dict)

    def lookup(self, name: str) -> Part | None:
        """Resolve a name, alias or OCR-normalized string to a part."""
        if name in self.parts:
            return self.parts[name]
        canonical = self.aliases.get(normalize(name))
        return self.parts.get(canonical) if canonical else None

    def relic(self, name: str) -> Relic | None:
        """Look up a relic by "Axi A1", "AxiA1" or "Axi A1 Relic"."""
        key = normalize_relic(name).removesuffix(" relic").replace(" ", "")
        return self.relics.get(key)

    @property
    def match_keys(self) -> list[str]:
        """Every normalized string the matcher may match against."""
        return list(self.aliases)

    def resolve_all(self, names: Iterable[str]) -> list[Part | None]:
        return [self.lookup(name) for name in names]


def build(
    filtered_items: dict[str, Any],
    prices: list[dict[str, Any]],
    market_items: dict[str, Any],
) -> Catalog:
    price_by_name = {entry["name"]: entry for entry in prices}
    market_by_name: dict[str, dict[str, Any]] = {}
    for item in market_items.get("data", []):
        english = item.get("i18n", {}).get("en", {})
        if name := english.get("name"):
            market_by_name[normalize(name)] = item

    catalog = Catalog()

    def add(part: Part) -> None:
        catalog.parts[part.name] = part
        catalog.aliases[normalize(part.name)] = part.name
        # The on-screen name differs for warframe components; index both.
        catalog.aliases[normalize(part.display_name)] = part.name

    for set_name, entry in filtered_items.get("eqmt", {}).items():
        for part_name, part in entry.get("parts", {}).items():
            add(
                _with_market_data(
                    Part(
                        name=part_name,
                        set_name=set_name,
                        kind=entry.get("type"),
                        ducats=int(part.get("ducats", 0)),
                        vaulted=bool(part.get("vaulted", entry.get("vaulted", False))),
                    ),
                    price_by_name,
                    market_by_name,
                )
            )

    # Non-tradeable rewards (Forma, Kuva, Ayatan stars...). Worth recognising so
    # a scan can say "this slot is a Forma blueprint" rather than guessing.
    for part_name, part in filtered_items.get("ignored_items", {}).items():
        if part_name in catalog.parts:
            continue
        add(
            Part(
                name=part_name,
                set_name=None,
                kind=None,
                ducats=int(part.get("ducats", 0)),
                vaulted=False,
                tradeable=False,
                avg_plat=float(part.get("plat", 0) or 0),
                volume_today=int(part.get("volume", 0) or 0),
            )
        )

    for era, relics in filtered_items.get("relics", {}).items():
        for code, relic in relics.items():
            rewards = tuple(
                RelicReward(part_name=relic[key], rarity=rarity)
                for key, rarity in RARITIES
                if relic.get(key)
            )
            record = Relic(
                era=era,
                code=code,
                vaulted=bool(relic.get("vaulted", False)),
                rewards=rewards,
            )
            catalog.relics[normalize_relic(record.name).replace(" ", "")] = record

    return catalog


def _with_market_data(
    part: Part,
    price_by_name: dict[str, dict[str, Any]],
    market_by_name: dict[str, dict[str, Any]],
) -> Part:
    """Attach live-feed data, resolving the naming mismatch between feeds.

    `filtered_items` names warframe components without the "Blueprint" suffix
    that both the game and warframe.market use ("Nidus Prime Chassis" vs
    "Nidus Prime Chassis Blueprint"), and does the same for a handful of old
    weapon blueprints ("Bronco Prime"). Rather than hardcoding which suffixes
    behave which way, try the bare name and then the +Blueprint form, and adopt
    whichever the feeds recognise as the display name.
    """
    updates: dict[str, Any] = {}
    candidates = [part.name, f"{part.name} Blueprint"]

    market = price = None
    for candidate in candidates:
        market = market_by_name.get(normalize(candidate))
        if market:
            updates["display_name"] = candidate
            break
    for candidate in candidates:
        price = price_by_name.get(candidate)
        if price:
            updates.setdefault("display_name", candidate)
            break

    if price:
        updates["avg_plat"] = _as_float(price.get("custom_avg"))
        updates["volume_today"] = _as_int(price.get("today_vol"))
        updates["volume_yesterday"] = _as_int(price.get("yesterday_vol"))
    if market:
        updates["slug"] = market.get("slug")
        updates["item_id"] = market.get("id")
        updates["game_ref"] = market.get("gameRef")
        english = market.get("i18n", {}).get("en", {})
        updates["icon"] = english.get("icon")
        updates["sub_icon"] = english.get("subIcon")
        if not part.ducats and market.get("ducats"):
            updates["ducats"] = int(market["ducats"])
    if not updates:
        return part
    return Part(**{**part.__dict__, **updates})


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load(*, refresh: bool = False, offline: bool = False) -> Catalog:
    """Build a catalog from cached (or freshly downloaded) feeds."""
    return build(
        sources.load(sources.FILTERED_ITEMS, refresh=refresh, offline=offline),
        sources.load(sources.PRICES, refresh=refresh, offline=offline),
        sources.load(sources.MARKET_ITEMS, refresh=refresh, offline=offline),
    )
