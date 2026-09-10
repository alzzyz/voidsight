"""What you have picked up this session, and what it is worth selling.

Deliberately not fed by EE.log. The log records that a reward screen appeared
and which relic opened it, but not the item names — that absence is the reason
the OCR pipeline exists at all. So drops come from scans, which means there is
one thing this cannot know: which of the four offered rewards you took. Rather
than guess, every scanned reward is recorded unselected and you confirm the one
you kept; that confirmation is also the gesture that marks it for sale.

Nothing here touches Qt or the network, so a future source — reading the
end-of-mission summary screen, say — can feed the same ledger.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: Newest first, and capped: this is a session ledger, not an archive.
DEFAULT_LIMIT = 40


@dataclass
class Drop:
    """One item seen in a reward screen."""

    name: str
    quantity: int = 1
    platinum: float | None = None
    ducats: int | None = None
    vaulted: bool = False
    tradeable: bool = True
    #: True once the player confirms they took it. Only selected drops are
    #: candidates for a listing.
    selected: bool = False
    live_price: bool = False

    @property
    def sale_value(self) -> float:
        return (self.platinum or 0.0) * self.quantity

    @property
    def sellable(self) -> bool:
        return self.tradeable and self.platinum is not None


@dataclass
class MissionGroup:
    """The drops from a single reward screen."""

    label: str
    at: datetime
    source: str
    drops: list[Drop] = field(default_factory=list)
    #: False when these came from a saved screenshot rather than the game. Kept
    #: on the group because a sell list must not silently mix the two.
    live_capture: bool = True

    @property
    def time_text(self) -> str:
        return self.at.strftime("%H:%M:%S")

    @property
    def selected(self) -> list[Drop]:
        return [drop for drop in self.drops if drop.selected]


class SessionLedger:
    """Recent reward screens, newest first."""

    def __init__(self, limit: int = DEFAULT_LIMIT) -> None:
        self._lock = threading.Lock()
        self._groups: list[MissionGroup] = []
        self.limit = limit

    @property
    def groups(self) -> list[MissionGroup]:
        with self._lock:
            return list(self._groups)

    def add_scan(self, payload: dict[str, Any]) -> MissionGroup | None:
        """Record a scan's readable rewards. Returns the group, or None if the
        scan produced nothing worth listing."""
        drops = [
            Drop(
                name=reward["name"],
                platinum=reward.get("platinum"),
                ducats=reward.get("ducats"),
                vaulted=bool(reward.get("vaulted")),
                tradeable=reward.get("tradeable") is not False,
                live_price=bool(reward.get("live")),
            )
            for reward in payload.get("rewards", [])
            if reward.get("matched")
        ]
        if not drops:
            return None

        at = _parse_time(payload.get("at"))
        group = MissionGroup(
            label=payload.get("relic") or "Reward screen",
            at=at,
            source=payload.get("source") or "scan",
            drops=drops,
            live_capture=bool(payload.get("live_capture", False)),
        )
        with self._lock:
            self._groups.insert(0, group)
            del self._groups[self.limit :]
        return group

    def selected_drops(self) -> list[Drop]:
        """Marked drops that could actually be listed.

        Drops read from a saved screenshot are excluded: they are useful for
        checking the pipeline, and dangerous in a sell list.
        """
        return [
            drop
            for group in self.groups
            if group.live_capture
            for drop in group.selected
        ]

    def total_platinum(self) -> float:
        return sum(drop.sale_value for drop in self.selected_drops())

    def clear(self) -> None:
        with self._lock:
            self._groups.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._groups)


def _parse_time(value: str | None) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(UTC)
