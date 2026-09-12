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


@pytest.fixture(scope="module")
def relic_catalog(catalog: C.Catalog) -> C.Catalog:
    """The cached catalog, for tests that need its relic drop tables.

    A cache fetched while the WFInfo host was down has items but no relics, and
    a test asking for one then fails in a way that says nothing about the code.
    """
    if not catalog.relics:
        pytest.skip("cached data has no relic table; run `voidsight update-data` while online")
    return catalog


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
        assert len({panel.theme.name for panel in panels}) > 1

    def test_candidates_are_distinct_places_to_look(self):
        """Every slot must be a different hypothesis about where the names are.

        Most themes' masks peak on the same bands, so ranking by score alone
        spends all six slots re-reading one band under six masks. On a real
        3440x1440 frame that meant six readings of the top of the item art
        while the band holding the names was never read at all.
        """
        panels = L.candidates(render_reward_screen(REWARDS, seed=4), limit=6)
        seen: dict[tuple, str] = {}
        for panel in panels:
            key = (panel.notes["band"], len(panel.columns))
            if key in seen:
                # One place may be offered twice, but only under a different
                # mask — the same mask on the same pixels is the same reading.
                assert panel.theme.name != seen[key]
            seen[key] = panel.theme.name
        # Most of the shortlist is somewhere new, rather than one band over.
        assert len(seen) > len(panels) // 2

    def test_a_wrapped_name_is_offered_as_one_band(self):
        """A long reward name wraps, and the cards are bottom-aligned.

        "Bronco Prime Blueprint" beside "Lavos Prime Chassis / Blueprint" puts
        the two names on different lines, so reading either line alone yields
        one name and one fragment. The union of the two has to be on offer.
        """
        mask = np.zeros((120, 400), dtype=bool)
        mask[30:42, 20:180] = True   # line one of the wrapped name
        mask[48:60, 20:140] = True   # line two, and the single-line name
        mask[48:60, 220:380] = True
        bands = L._text_bands(mask, scale=1.0)
        assert any(top <= 30 and bottom >= 60 for top, bottom in bands)
        # The individual lines stay on offer too: most rows are not wrapped.
        assert any(top >= 28 and bottom <= 44 for top, bottom in bands)

    def test_distant_lines_are_not_merged(self):
        # Two unrelated rows of text (names and squadmate labels) must not be
        # glued into one band just because both are text.
        mask = np.zeros((200, 400), dtype=bool)
        mask[30:42, 20:180] = True
        mask[150:162, 20:180] = True
        bands = L._text_bands(mask, scale=1.0)
        assert not any(top <= 42 and bottom >= 150 for top, bottom in bands)

    def test_reports_why_it_found_nothing(self):
        blank = np.zeros((1080, 1920, 3), dtype=np.uint8)
        panel = L.locate(blank)
        assert panel.columns == []
        assert "reason" in panel.notes


class TestScanCost:
    """A live scan reads several buffered frames; each one has to stay cheap."""

    def scanner(self, catalog, **kwargs):
        return pipeline.Scanner(catalog, persist=False, **kwargs)

    def test_the_theme_search_does_not_re_run_per_frame(self, small_catalog, monkeypatch):
        """Frames with no reward screen read exactly like a wrong theme.

        A back-scan is mostly such frames, and re-running the fifteen-mask
        search on each of them cost more than the rest of the scan combined.
        """
        calls: list[object] = []
        real = pipeline.scan

        def counting(frame, catalog, **kwargs):
            calls.append(kwargs.get("ui_theme"))
            return real(frame, catalog, **kwargs)

        monkeypatch.setattr(pipeline, "scan", counting)
        scanner = self.scanner(small_catalog, config=Config(theme="Vitruvian"))
        blank = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for _ in range(4):
            scanner.scan(blank)

        searches = [theme for theme in calls if theme is None]
        assert len(calls) == 5  # four pinned reads, and one search between them
        assert len(searches) == 1

    def test_a_wrong_theme_is_still_re_detected(self, small_catalog, monkeypatch):
        monkeypatch.setattr(pipeline, "REDETECT_INTERVAL", 0.0)
        calls: list[object] = []
        real = pipeline.scan

        def counting(frame, catalog, **kwargs):
            calls.append(kwargs.get("ui_theme"))
            return real(frame, catalog, **kwargs)

        monkeypatch.setattr(pipeline, "scan", counting)
        scanner = self.scanner(small_catalog, config=Config(theme="Vitruvian"))
        scanner.scan(np.zeros((1080, 1920, 3), dtype=np.uint8))
        assert None in calls

    @needs_tesseract
    def test_columns_read_in_parallel_stay_in_their_own_order(self, catalog):
        # Reading the columns concurrently must not shuffle them: reward 0 is
        # the leftmost card, and the UI marks one of them as the best pick.
        frame = render_reward_screen(REWARDS, theme_name="Vitruvian", seed=1)
        result = pipeline.scan(frame, catalog, ui_theme=theme_module.get("Vitruvian"))
        assert [reward.index for reward in result.rewards] == list(range(len(result.rewards)))
        assert [reward.name for reward in result.identified] == [
            name for name in REWARDS if name in {r.name for r in result.identified}
        ]


class TestOcrSegmentation:
    """Tesseract needs telling when a crop holds two lines rather than one."""

    def blank(self, height: int, width: int = 200) -> np.ndarray:
        return np.full((height, width), 255, dtype=np.uint8)

    def test_counts_one_line(self):
        image = self.blank(40)
        image[12:26, 10:180] = 0
        assert O.line_count(image) == 1

    def test_counts_two_lines(self):
        image = self.blank(70)
        image[10:24, 10:180] = 0
        image[40:54, 10:120] = 0
        assert O.line_count(image) == 2

    def test_blank_crop_has_no_lines(self):
        assert O.line_count(self.blank(40)) == 0

    def test_inverted_crops_are_counted_too(self):
        image = np.zeros((40, 200), dtype=np.uint8)
        image[12:26, 10:180] = 255
        assert O.line_count(image) == 1

    def test_reader_switches_to_block_mode_for_two_lines(self):
        reader = O.TesseractReader()
        assert f"--psm {O.SINGLE_LINE_PSM}" in reader.config
        assert f"--psm {O.BLOCK_PSM}" in reader.config_for(O.BLOCK_PSM)


class TestMatch:
    def test_open_set_rejects_nonsense(self, catalog: C.Catalog):
        assert not M.match("qqzzxx wobble frimble", catalog).ok

    def test_open_set_rejects_a_badly_garbled_read(self, catalog: C.Catalog):
        # Against the full ~750-name catalog, this is too mangled to place.
        assert not M.match("Xl1U5 QR8ME CH#5Z1Z", catalog).ok

    def test_relic_constraint_rescues_a_garbled_read(self, relic_catalog: C.Catalog):
        catalog = relic_catalog
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

    def test_relic_narrows_the_search(self, relic_catalog: C.Catalog):
        catalog = relic_catalog
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
