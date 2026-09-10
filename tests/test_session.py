"""The session ledger: what was offered, what you say you took, what it is worth."""

from __future__ import annotations

import pytest

from voidsight.session import SessionLedger


def payload(**overrides):
    base = {
        "at": "2026-09-10T12:00:00+00:00",
        "relic": "Axi A1",
        "source": "x11: Warframe",
        "live_capture": True,
        "rewards": [
            {"matched": True, "name": "Fang Prime Handle", "platinum": 6.0, "ducats": 15,
             "tradeable": True, "live": True, "vaulted": False},
            {"matched": True, "name": "Sybaris Prime Barrel", "platinum": 14.0, "ducats": 45,
             "tradeable": True, "live": True, "vaulted": True},
            {"matched": True, "name": "Forma Blueprint", "platinum": 0.0, "ducats": 0,
             "tradeable": False, "live": False, "vaulted": False},
            {"matched": False, "name": "unreadable", "platinum": None},
        ],
    }
    base.update(overrides)
    return base


class TestAddScan:
    def test_records_only_readable_rewards(self):
        ledger = SessionLedger()
        group = ledger.add_scan(payload())
        assert [drop.name for drop in group.drops] == [
            "Fang Prime Handle",
            "Sybaris Prime Barrel",
            "Forma Blueprint",
        ]

    def test_nothing_is_preselected(self):
        # The app cannot know which of the four rewards was taken, and guessing
        # would put wrong items in front of a sell button.
        ledger = SessionLedger()
        group = ledger.add_scan(payload())
        assert all(not drop.selected for drop in group.drops)
        assert ledger.selected_drops() == []

    def test_keeps_the_relic_as_the_label(self):
        ledger = SessionLedger()
        assert ledger.add_scan(payload()).label == "Axi A1"

    def test_falls_back_when_the_relic_is_unknown(self):
        ledger = SessionLedger()
        assert ledger.add_scan(payload(relic=None)).label == "Reward screen"

    def test_a_scan_with_nothing_readable_is_not_recorded(self):
        ledger = SessionLedger()
        assert ledger.add_scan(payload(rewards=[{"matched": False, "name": "?"}])) is None
        assert len(ledger) == 0

    def test_newest_first(self):
        ledger = SessionLedger()
        ledger.add_scan(payload(relic="Lith A1"))
        ledger.add_scan(payload(relic="Meso B2"))
        assert [group.label for group in ledger.groups] == ["Meso B2", "Lith A1"]

    def test_history_is_capped(self):
        ledger = SessionLedger(limit=3)
        for index in range(6):
            ledger.add_scan(payload(relic=f"Axi A{index}"))
        assert len(ledger) == 3
        assert ledger.groups[0].label == "Axi A5"

    def test_a_missing_timestamp_still_gets_one(self):
        ledger = SessionLedger()
        assert ledger.add_scan(payload(at=None)).at is not None

    def test_a_malformed_timestamp_does_not_raise(self):
        ledger = SessionLedger()
        assert ledger.add_scan(payload(at="not-a-time")).at is not None


class TestValuation:
    def test_totals_only_what_is_marked(self):
        ledger = SessionLedger()
        group = ledger.add_scan(payload())
        assert ledger.total_platinum() == 0.0
        group.drops[1].selected = True
        assert ledger.total_platinum() == pytest.approx(14.0)

    def test_quantity_multiplies(self):
        ledger = SessionLedger()
        group = ledger.add_scan(payload())
        group.drops[0].selected = True
        group.drops[0].quantity = 3
        assert ledger.total_platinum() == pytest.approx(18.0)

    def test_untradeable_drops_are_not_sellable(self):
        ledger = SessionLedger()
        group = ledger.add_scan(payload())
        forma = group.drops[2]
        assert not forma.sellable
        assert forma.tradeable is False

    def test_an_unpriced_drop_is_not_sellable(self):
        ledger = SessionLedger()
        group = ledger.add_scan(
            payload(rewards=[{"matched": True, "name": "Galariak Prime Blade", "platinum": None}])
        )
        assert not group.drops[0].sellable

    def test_selection_spans_groups(self):
        ledger = SessionLedger()
        first = ledger.add_scan(payload(relic="Lith A1"))
        second = ledger.add_scan(payload(relic="Meso B2"))
        first.drops[0].selected = True
        second.drops[1].selected = True
        assert len(ledger.selected_drops()) == 2
        assert ledger.total_platinum() == pytest.approx(20.0)

    def test_replayed_drops_are_never_listable(self):
        # A screenshot scan is for checking the pipeline. Offering its items for
        # sale would list things the player may not own.
        ledger = SessionLedger()
        group = ledger.add_scan(payload(live_capture=False, source="file: shot.png"))
        group.drops[0].selected = True
        assert not group.live_capture
        assert ledger.selected_drops() == []
        assert ledger.total_platinum() == 0.0

    def test_live_and_replayed_groups_do_not_mix(self):
        ledger = SessionLedger()
        replayed = ledger.add_scan(payload(live_capture=False, relic="Lith A1"))
        live = ledger.add_scan(payload(live_capture=True, relic="Meso B2"))
        replayed.drops[0].selected = True
        live.drops[0].selected = True
        assert [drop.name for drop in ledger.selected_drops()] == ["Fang Prime Handle"]
        assert ledger.total_platinum() == pytest.approx(6.0)

    def test_provenance_defaults_to_not_live(self):
        # A payload that fails to say where it came from is treated as unsafe.
        ledger = SessionLedger()
        group = ledger.add_scan({"rewards": [{"matched": True, "name": "X", "platinum": 1.0}]})
        assert not group.live_capture

    def test_clear_removes_everything(self):
        ledger = SessionLedger()
        ledger.add_scan(payload())
        ledger.clear()
        assert len(ledger) == 0
        assert ledger.selected_drops() == []
