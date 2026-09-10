"""Vision pipeline tests against synthetic reward screens."""

from __future__ import annotations

import numpy as np
import pytest

from tests.test_catalog import FILTERED_ITEMS, MARKET_ITEMS, PRICES
from voidsight.config import Config
from voidsight.data import catalog as C
from voidsight.testing import render_reward_screen
from voidsight.vision import locate as L
from voidsight.vision import match as M
from voidsight.vision import ocr as O
from voidsight.vision import pipeline
from voidsight.vision import theme as theme_module

REWARDS = [
    "Nikana Prime Blueprint",
    "Akstiletto Prime Barrel",
    "Braton Prime Stock",
    "Trinity Prime Systems Blueprint",
]

needs_tesseract = pytest.mark.skipif(
    not O.TesseractReader.available(), reason="tesseract binary not installed"
)


@pytest.fixture(scope="module")
def catalog() -> C.Catalog:
    """The real catalog, from cache. The item list is what matching needs."""
    try:
        return C.load(offline=True)
    except FileNotFoundError:
        pytest.skip("no cached data; run `voidsight update-data` first")


@pytest.fixture
def small_catalog() -> C.Catalog:
    return C.build(FILTERED_ITEMS, PRICES, MARKET_ITEMS)


class TestGeometry:
    def test_panel_is_centred_horizontally_at_1080p(self):
        box = L.panel_box((1080, 1920))
        assert box.width == 968
        assert box.x + box.width // 2 == pytest.approx(960, abs=1)
        assert box.y == 1080 // 2 - 316

    def test_search_box_covers_where_names_are_seen_in_real_screenshots(self):
        # Two 1080p captures put the name row at roughly y=445 and y=572; the
        # crop has to reach both, since which one is right is decided by reading.
        box = L.panel_box((1080, 1920))
        assert box.y <= 445
        assert box.y + box.height >= 580

    def test_panel_scales_with_resolution(self):
        box = L.panel_box((1440, 2560))
        assert box.width == pytest.approx(968 * 4 / 3, abs=2)
        assert box.height == pytest.approx(L.PANEL_SEARCH_HEIGHT * 4 / 3, abs=2)

    def test_ultrawide_keeps_the_16_9_panel_centred(self):
        # 3440x1440: Warframe scales the HUD off the height and centres it.
        box = L.panel_box((1440, 3440))
        assert box.x + box.width // 2 == pytest.approx(1720, abs=2)
        assert box.width == pytest.approx(968 * 4 / 3, abs=2)

    def test_region_override_wins(self):
        box = L.panel_box((1080, 1920), region=[0.25, 0.3, 0.5, 0.2])
        assert (box.x, box.y, box.width, box.height) == (480, 324, 960, 216)

    def test_region_override_must_have_four_numbers(self):
        with pytest.raises(ValueError):
            L.panel_box((1080, 1920), region=[0.1, 0.2])

    def test_ultrawide_scales_on_height_not_width(self):
        # 3440x1440 is 21:9; the HUD stays 16:9-sized and centred.
        wide = L.panel_box((1440, 3440))
        normal = L.panel_box((1440, 2560))
        assert wide.width == normal.width
        assert wide.x + wide.width // 2 == pytest.approx(1720, abs=2)

    def test_ui_scale_enlarges_the_panel(self):
        assert L.panel_box((1080, 1920), ui_scale=1.25).width > L.panel_box((1080, 1920)).width


class TestBands:
    def test_contiguous_runs(self):
        active = np.array([0, 1, 1, 0, 0, 0, 1, 1, 1, 0], dtype=bool)
        assert L._bands(active, max_gap=0) == [(1, 3), (6, 9)]

    def test_small_gaps_are_bridged(self):
        active = np.array([1, 1, 0, 1, 1, 0, 0, 0, 0, 1], dtype=bool)
        assert L._bands(active, max_gap=1) == [(0, 5), (9, 10)]

    def test_empty(self):
        assert L._bands(np.zeros(8, dtype=bool), max_gap=2) == []

    def test_solid_bands_score_worse_than_text_like_ones(self):
        assert L._score_fill(0.2) > L._score_fill(0.95)
        assert L._score_fill(0.2) > L._score_fill(0.001)


class TestLocate:
    """Geometry, with the theme pinned.

    Theme resolution is a runtime concern (see `Scanner`), so these exercise the
    locator the way live code calls it: one known theme, one mask.
    """

    THEME = theme_module.get("Vitruvian")

    def locate(self, names: list[str], **kwargs) -> L.Panel:
        frame = render_reward_screen(names, theme_name="Vitruvian", **kwargs)
        return L.locate(frame, ui_theme=self.THEME)

    def test_finds_four_evenly_spaced_columns(self):
        panel = self.locate(REWARDS, seed=1, busy_background=False)
        assert len(panel.columns) == 4
        centres = [column.box.x + column.box.width / 2 for column in panel.columns]
        gaps = np.diff(centres)
        assert np.std(gaps) / np.mean(gaps) < 0.05

    @pytest.mark.parametrize("count", [1, 2, 3, 4])
    def test_offers_the_correct_grouping_as_a_candidate(self, count: int):
        # The locator proposes every plausible way to divide the row and does
        # not claim to know which is right — `pipeline.scan` settles that by
        # reading them. What matters here is that the truth is on the list.
        frame = render_reward_screen(
            REWARDS[:count], theme_name="Vitruvian", seed=2, busy_background=False
        )
        panels = L.candidates(frame, ui_theme=self.THEME, limit=8)
        assert count in {len(panel.columns) for panel in panels}

    def test_icons_are_not_mistaken_for_names(self):
        # The renderer draws icon blobs taller than a text line directly above
        # the names; the located band must be the text, not the icons.
        panel = self.locate(REWARDS, seed=4, busy_background=False)
        top, bottom = panel.notes["band"]
        assert bottom - top < 0.25 * panel.box.height

    def test_ranks_several_candidates_on_a_busy_frame(self):
        # Without a pinned theme every theme's mask is a hypothesis; the caller
        # is expected to verify them by reading, so more than one is offered.
        panels = L.candidates(render_reward_screen(REWARDS, seed=4), limit=6)
        assert len(panels) > 1
        assert panels == sorted(panels, key=lambda panel: panel.score, reverse=True)
        # Spread across themes rather than six variations of one mask, and no
        # two candidates proposing the same theme and reward count.
        assert len({panel.theme.name for panel in panels}) > 1
        shapes = [(panel.theme.name, len(panel.columns)) for panel in panels]
        assert len(set(shapes)) == len(shapes)

    def test_reports_why_it_found_nothing(self):
        blank = np.zeros((1080, 1920, 3), dtype=np.uint8)
        panel = L.locate(blank)
        assert panel.columns == []
        assert "reason" in panel.notes


class TestMatch:
    def test_open_set_rejects_nonsense(self, catalog: C.Catalog):
        assert not M.match("qqzzxx wobble frimble", catalog).ok

    def test_open_set_rejects_a_badly_garbled_read(self, catalog: C.Catalog):
        # Against the full ~750-name catalog, this is too mangled to place.
        assert not M.match("Xl1U5 QR8ME CH#5Z1Z", catalog).ok

    def test_relic_constraint_rescues_a_garbled_read(self, catalog: C.Catalog):
        garbled = "NlDU5 PR1ME CHA5S1S BLUEPR1NT"
        relic = catalog.relic("Meso N9") or next(iter(catalog.relics.values()))
        candidates = [reward.part_name for reward in relic.rewards]
        # Narrowing to a relic's six rewards must still pick the right one, and
        # be more certain about it than the open-set search was.
        open_set = M.match(garbled, catalog)
        constrained = M.match(garbled, catalog, candidates=["Nidus Prime Chassis"] + candidates)
        assert constrained.ok
        assert constrained.part.name == "Nidus Prime Chassis"
        assert constrained.constrained
        assert constrained.score >= open_set.score

    def test_relic_constraint_only_considers_that_relics_rewards(
        self, small_catalog: C.Catalog
    ):
        relic = small_catalog.relic("Axi A10")
        result = M.match(
            "Nidus Prime Chassis Blueprint",
            small_catalog,
            candidates=[reward.part_name for reward in relic.rewards],
        )
        # Axi A10 cannot drop it, so the read resolves to something it can.
        assert result.part.name != "Nidus Prime Chassis"

    def test_reports_ambiguity(self, small_catalog: C.Catalog):
        result = M.match("Bronco Prime Barrel", small_catalog)
        assert result.ok
        assert result.runner_up is not None


@needs_tesseract
class TestPipeline:
    """End to end on synthetic frames, with the theme known — the live path."""

    @pytest.mark.parametrize("theme_name", ["Vitruvian", "Lotus", "Stalker", "Equinox", "Nidus"])
    def test_reads_every_reward_name(self, catalog: C.Catalog, theme_name: str):
        frame = render_reward_screen(REWARDS, theme_name=theme_name, seed=3)
        result = pipeline.scan(frame, catalog, ui_theme=theme_module.get(theme_name))
        assert result.ok, result.notes
        assert [reward.part.display_name for reward in result.rewards] == REWARDS

    @pytest.mark.parametrize("size", [(1920, 1080), (2560, 1440), (3840, 2160), (1600, 900)])
    def test_reads_at_other_resolutions(self, catalog: C.Catalog, size: tuple[int, int]):
        frame = render_reward_screen(REWARDS, size=size, seed=5)
        result = pipeline.scan(frame, catalog, ui_theme=theme_module.get("Vitruvian"))
        assert [reward.part.display_name for reward in result.rewards] == REWARDS

    @pytest.mark.parametrize("count", [2, 3, 4])
    def test_reads_smaller_squads(self, catalog: C.Catalog, count: int):
        frame = render_reward_screen(REWARDS[:count], seed=8)
        result = pipeline.scan(frame, catalog, ui_theme=theme_module.get("Vitruvian"))
        assert [reward.part.display_name for reward in result.rewards] == REWARDS[:count]

    def test_relic_narrows_the_search(self, catalog: C.Catalog):
        relic = catalog.relic("Axi A1")
        frame = render_reward_screen(
            [reward.part_name for reward in relic.rewards][:4], seed=9
        )
        result = pipeline.scan(
            frame, catalog, ui_theme=theme_module.get("Vitruvian"), relic=relic
        )
        assert result.ok
        assert all(reward.match.constrained for reward in result.rewards)

    def test_confidence_is_high_on_a_clean_read(self, catalog: C.Catalog):
        result = pipeline.scan(
            render_reward_screen(REWARDS, seed=6), catalog, ui_theme=theme_module.get("Vitruvian")
        )
        assert result.confidence > 0.7

    def test_confidence_is_zero_when_nothing_is_found(self, catalog: C.Catalog):
        result = pipeline.scan(np.zeros((1080, 1920, 3), dtype=np.uint8), catalog)
        assert result.rewards == []
        assert result.confidence == 0.0

    def test_finds_the_theme_without_being_told(self, catalog: C.Catalog):
        result = pipeline.scan(render_reward_screen(REWARDS, seed=3), catalog)
        assert [reward.part.display_name for reward in result.rewards] == REWARDS

    def test_debug_artifacts_are_written(self, catalog: C.Catalog, tmp_path):
        pipeline.scan(
            render_reward_screen(REWARDS, seed=7),
            catalog,
            ui_theme=theme_module.get("Vitruvian"),
            debug_dir=tmp_path,
        )
        for name in ("frame.png", "panel.png", "panel_binary.png", "overlay.png", "report.json"):
            assert (tmp_path / name).exists()
        assert (tmp_path / "column_0.png").exists()


@needs_tesseract
class TestScanner:
    """The theme is worked out once, then reused and remembered."""

    def test_learns_and_pins_the_theme(self, catalog: C.Catalog, tmp_path):
        config = Config()
        scanner = pipeline.Scanner(catalog, config=config, persist=False)
        assert scanner.theme is None

        result = scanner.scan(render_reward_screen(REWARDS, theme_name="Lotus", seed=3))
        assert result.ok
        assert scanner.theme is not None
        assert config.theme == scanner.theme.name

    def test_pinned_theme_is_reused_without_searching(self, catalog: C.Catalog):
        config = Config(theme="Vitruvian")
        scanner = pipeline.Scanner(catalog, config=config, persist=False)
        result = scanner.scan(render_reward_screen(REWARDS, seed=3))
        assert result.notes["themes_tried"] == 1
        assert result.ok

    def test_recovers_when_the_pinned_theme_stops_reading(self, catalog: C.Catalog):
        # Tenno is a dark teal; it cannot read a pale-gold Vitruvian UI.
        config = Config(theme="Tenno")
        scanner = pipeline.Scanner(catalog, config=config, persist=False)
        result = scanner.scan(render_reward_screen(REWARDS, theme_name="Vitruvian", seed=3))
        assert result.ok
        assert [reward.part.display_name for reward in result.rewards] == REWARDS

    def test_unknown_configured_theme_is_ignored(self, catalog: C.Catalog):
        scanner = pipeline.Scanner(catalog, config=Config(theme="Nonesuch"), persist=False)
        assert scanner.theme is None

    def test_learned_theme_is_written_to_disk(self, catalog: C.Catalog, tmp_path):
        path = tmp_path / "config.toml"
        config = Config()
        assert config.learn_theme("Lotus", path=path)
        assert not config.learn_theme("Lotus", path=path)
        assert Config.load(path).theme == "Lotus"
