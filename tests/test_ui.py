"""Desktop client tests, run against Qt's offscreen platform."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="desktop extra not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.test_app import market_stub  # noqa: E402
from tests.test_catalog import FILTERED_ITEMS, MARKET_ITEMS, PRICES  # noqa: E402
from voidsight.app.server import Session  # noqa: E402
from voidsight.app.state import ScanStore  # noqa: E402
from voidsight.config import Config  # noqa: E402
from voidsight.data import catalog as C  # noqa: E402
from voidsight.ui.home import HomeView  # noqa: E402
from voidsight.ui.main import MainWindow  # noqa: E402
from voidsight.ui.rewards import RewardCard  # noqa: E402
from voidsight.ui.settings import DETECT, SettingsView  # noqa: E402
from voidsight.vision.pipeline import Scanner  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def catalog() -> C.Catalog:
    return C.build(FILTERED_ITEMS, PRICES, MARKET_ITEMS)


@pytest.fixture
def shots(tmp_path):
    """A directory holding one synthetic reward screen."""
    import cv2

    from voidsight.testing import render_reward_screen

    directory = tmp_path / "shots"
    directory.mkdir()
    frame = render_reward_screen(["Nidus Prime Chassis", "Bronco Prime Barrel"], seed=1)
    cv2.imwrite(str(directory / "shot.png"), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    return directory


@pytest.fixture
def session(catalog: C.Catalog, tmp_path) -> Session:
    config = Config()
    return Session(
        scanner=Scanner(catalog, config=config, persist=True, config_path=tmp_path / "c.toml"),
        market=market_stub(),
        config=config,
        store=ScanStore(),
        backend=None,
    )


def payload(**overrides) -> dict:
    base = {
        "at": "2026-09-10T12:34:56+00:00",
        "source": "x11: Warframe",
        "live_capture": True,
        "theme": "Vitruvian",
        "relic": "Axi A1",
        "confidence": 0.97,
        "ok": True,
        "reason": None,
        "best": 1,
        "rewards": [
            {
                "index": 0,
                "matched": True,
                "name": "Nikana Prime Blueprint",
                "raw_text": "NIKANA PRIME BLUEPRINT",
                "score": 100.0,
                "ocr_confidence": 0.94,
                "ambiguous": False,
                "platinum": 7.0,
                "lowest": 7,
                "offers": [7, 8, 9],
                "average": 5.9,
                "volume": 12,
                "ducats": 25,
                "ducats_per_platinum": 3.6,
                "vaulted": True,
                "tradeable": True,
                "live": True,
                "error": None,
            },
            {
                "index": 1,
                "matched": True,
                "name": "Braton Prime Stock",
                "raw_text": "BRATON PRIME STOCK",
                "score": 98.0,
                "ocr_confidence": 0.9,
                "ambiguous": False,
                "platinum": 42.0,
                "lowest": 42,
                "offers": [42],
                "average": 40.0,
                "volume": 3,
                "ducats": 15,
                "ducats_per_platinum": 0.4,
                "vaulted": False,
                "tradeable": True,
                "live": True,
                "error": None,
            },
        ],
    }
    base.update(overrides)
    return base


class TestHomeView:
    def test_starts_empty_with_no_summary(self, qt_app):
        view = HomeView()
        assert view.cards.count() == 0
        assert view.summary.isHidden()

    def test_renders_a_card_per_reward(self, qt_app):
        view = HomeView()
        view.show_scan(payload())
        assert view.cards.count() == 2
        assert "Axi A1" in view.summary.text()
        assert "97%" in view.summary.text()

    def test_marks_the_best_reward(self, qt_app):
        view = HomeView()
        view.show_scan(payload())
        cards = [view.cards.itemAt(i).widget() for i in range(view.cards.count())]
        assert [card.property("best") for card in cards] == ["false", "true"]

    def test_replaces_rewards_between_scans(self, qt_app):
        view = HomeView()
        view.show_scan(payload())
        view.show_scan(payload(rewards=payload()["rewards"][:1], best=0))
        assert view.cards.count() == 1

    def test_explains_an_empty_scan(self, qt_app):
        view = HomeView()
        view.show_scan(payload(rewards=[], best=None, ok=False, reason="no text found"))
        assert view.cards.count() == 0
        assert "no text found" in view.empty.detail.text()

    def test_idle_state_names_what_it_is_waiting_for(self, qt_app):
        view = HomeView()
        view.describe_idle(game_running=False, watching=True, has_backend=True)
        assert "not running" in view.empty.headline.text().lower()

        view.describe_idle(game_running=True, watching=True, has_backend=True)
        assert "EE.log" in view.empty.detail.text()

        view.describe_idle(game_running=True, watching=True, has_backend=False)
        assert "capture" in view.empty.headline.text().lower() + view.empty.detail.text().lower()

    def test_unmatched_reward_is_flagged(self, qt_app):
        reward = dict(payload()["rewards"][0])
        reward.update({"matched": False, "name": "unreadable", "platinum": None})
        card = RewardCard(reward, is_best=False)
        assert card.property("matched") == "false"


class TestSettingsView:
    def test_shows_detect_when_no_theme_is_set(self, qt_app, session: Session):
        view = SettingsView(session)
        assert view.theme.currentText() == DETECT
        # UI Scaling is Warframe's own setting, edited as the percentage the
        # game shows — not this window's size, which lives in the header.
        assert view.scale.value() == pytest.approx(100.0)
        assert view.scale.suffix().strip() == "%"

    def test_saving_pins_theme_and_scale(self, qt_app, session: Session):
        view = SettingsView(session)
        view.theme.setCurrentIndex(view.theme.findData("Nidus"))
        view.scale.setValue(125)
        view.prefer.setCurrentIndex(view.prefer.findData("ducats"))
        view.apply()

        assert session.scanner.theme.name == "Nidus"
        assert session.scanner.config.ui_scale == pytest.approx(1.25)
        assert session.config.prefer == "ducats"
        assert view.status.text() == "Saved"

    @pytest.mark.parametrize("percent", [50, 100, 125, 200])
    def test_percentages_round_trip(self, qt_app, session: Session, percent):
        view = SettingsView(session)
        view.scale.setValue(percent)
        view.apply()
        assert session.scanner.config.ui_scale == pytest.approx(percent / 100)

    def test_an_odd_stored_scale_is_shown_as_a_percentage(self, qt_app, catalog):
        from voidsight.config import Config
        from voidsight.vision.pipeline import Scanner

        config = Config(ui_scale=1.12)
        session = Session(
            scanner=Scanner(catalog, config=config, persist=False),
            market=market_stub(),
            config=config,
            store=ScanStore(),
        )
        assert SettingsView(session).scale.value() == pytest.approx(112.0)

    def test_choosing_detect_clears_the_pinned_theme(self, qt_app, session: Session):
        session.scanner.set_theme("Lotus")
        view = SettingsView(session)
        view.theme.setCurrentIndex(view.theme.findData(None))
        view.apply()
        assert session.scanner.theme is None

    def test_reload_reflects_external_changes(self, qt_app, session: Session):
        view = SettingsView(session)
        session.scanner.set_theme("Zephyr")
        view.reload()
        assert view.theme.currentText() == "Zephyr"

    def test_debug_scan_is_disabled_without_a_backend(self, qt_app, session: Session):
        view = SettingsView(session)
        assert session.backend is None
        assert not view.scan_button.isEnabled()
        # Opening a file is the fallback and must stay available.
        assert view.open_button.isEnabled()
        assert "No capture backend" in view.source_label.text()

    def test_debug_scan_emits_a_request(self, qt_app, session: Session, shots):
        from voidsight.capture.replay import ReplayBackend

        session.backend = ReplayBackend(shots)
        view = SettingsView(session)
        fired: list[int] = []
        view.scan_requested.connect(lambda: fired.append(1))
        view.scan_button.click()
        assert fired == [1]


class TestOverlaySettings:
    def test_overlay_is_off_by_default(self, qt_app, session: Session):
        view = SettingsView(session)
        assert not view.overlay_enabled.isChecked()
        assert session.config.overlay is False

    def test_enabling_and_placing_the_overlay(self, qt_app, session: Session):
        from voidsight.ui.overlay import Overlay

        overlay = Overlay()
        view = SettingsView(session, overlay=overlay)
        view.overlay_enabled.setChecked(True)
        view.overlay_corner.setCurrentIndex(view.overlay_corner.findData("bottom-right"))
        view.overlay_seconds.setValue(20)
        view.apply()

        assert session.config.overlay is True
        assert session.config.overlay_corner == "bottom-right"
        assert session.config.overlay_seconds == 20
        # Applied live, not on next launch.
        assert overlay.corner == "bottom-right"
        assert overlay.seconds == 20

    def test_overlay_settings_survive_a_reload(self, qt_app, catalog: C.Catalog, tmp_path):
        from voidsight.config import Config

        path = tmp_path / "config.toml"
        config = Config(overlay=True, overlay_corner="top-left", overlay_seconds=8.0)
        config.save(path)
        assert Config.load(path).overlay is True
        assert Config.load(path).overlay_corner == "top-left"
        assert Config.load(path).overlay_seconds == 8.0

    def test_preview_shows_the_overlay_without_a_scan(self, qt_app, session: Session):
        from voidsight.ui.overlay import Overlay

        overlay = Overlay()
        view = SettingsView(session, overlay=overlay)
        view.show_preview()
        assert overlay._layout.count() == 3

    def test_limitation_note_is_hidden_unless_it_applies(self, qt_app, session: Session):
        view = SettingsView(session)
        assert not view.overlay_note.isVisible()
        view.set_overlay_limitation("Wayland cannot place it")
        assert view.overlay_note.text() == "Wayland cannot place it"


class TestBridgeFileScan:
    def test_reads_a_screenshot_from_disk(self, qt_app, session: Session, shots):
        from voidsight.ui.bridge import Bridge

        bridge = Bridge(session)
        received: list[dict] = []
        bridge.scanned.connect(received.append)
        bridge._read_file(next(iter(sorted(shots.iterdir()))))
        # The payload is published through the store either way.
        assert session.store.latest is not None
        assert session.store.latest["source"].endswith(".png")

    def test_reports_a_file_it_cannot_decode(self, qt_app, session: Session, tmp_path):
        from voidsight.ui.bridge import Bridge

        broken = tmp_path / "not-an-image.png"
        broken.write_text("nope")
        with pytest.raises(RuntimeError, match="could not read"):
            Bridge(session)._read_file(broken)


class TestMainWindow:
    def test_opens_on_home(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        assert window.pages.currentWidget() is window.home
        assert window.pages.count() == 3  # home, quick sell, settings

    def test_settings_button_switches_pages(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        window.header.settings.setChecked(True)
        assert window.pages.currentWidget() is window.settings
        window.header.settings.setChecked(False)
        assert window.pages.currentWidget() is window.home

    def test_a_scan_returns_to_home_and_reports(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        window.header.settings.setChecked(True)
        window.on_scan(payload())
        # A result outranks whatever page you were on.
        assert window.pages.currentWidget() is window.home
        assert not window.header.settings.isChecked()
        assert "2/2" in window.statusBar().currentMessage()

    def test_busy_state_disables_the_debug_buttons(self, qt_app, session: Session, shots):
        from voidsight.capture.replay import ReplayBackend
        from voidsight.ui.bridge import Bridge

        session.backend = ReplayBackend(shots)
        window = MainWindow(session, Bridge(session))
        window.settings.set_busy(True)
        assert not window.settings.scan_button.isEnabled()
        assert not window.settings.open_button.isEnabled()
        window.settings.set_busy(False)
        assert window.settings.scan_button.isEnabled()

    def test_without_a_backend_the_client_still_opens(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        # This is the macOS case, and any Linux box where probing found nothing.
        assert session.backend is None
        window = MainWindow(session, Bridge(session))
        assert window.pages.count() == 3
        assert not window.settings.scan_button.isEnabled()
        assert window.settings.open_button.isEnabled()

    def test_header_reports_the_game_state(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        # No Warframe on a test runner, so this is the state that must render.
        assert not window.game_state.running
        assert "not detected" in window.header.game.label.text().lower()

    def test_failure_is_reported_in_the_debug_panel(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        window.on_failure("replay backend returned no frame")
        assert "no frame" in window.settings.debug_status.text()


class TestOverlay:
    """The overlay must be invisible to input and never steal focus."""

    def overlay(self, **kwargs):
        from voidsight.ui.overlay import Overlay

        return Overlay(**kwargs)

    def test_is_click_through_and_never_focused(self, qt_app):
        from PySide6.QtCore import Qt

        overlay = self.overlay()
        flags = overlay.windowFlags()
        # A missed click or a stolen alt-tab mid-mission is unacceptable.
        assert flags & Qt.WindowType.WindowTransparentForInput
        assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.FramelessWindowHint
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def test_renders_a_card_per_reward(self, qt_app):
        overlay = self.overlay()
        overlay.show_scan(payload())
        assert overlay._layout.count() == 2

    def test_replaces_cards_between_scans(self, qt_app):
        overlay = self.overlay()
        overlay.show_scan(payload())
        overlay.show_scan(payload(rewards=payload()["rewards"][:1], best=0))
        assert overlay._layout.count() == 1

    def test_hides_itself_when_a_scan_finds_nothing(self, qt_app):
        overlay = self.overlay()
        overlay.show_scan(payload())
        overlay.show_scan(payload(rewards=[], best=None))
        assert not overlay.isVisible()

    def test_auto_hides_after_the_configured_time(self, qt_app):
        overlay = self.overlay(seconds=0.05)
        overlay.show_scan(payload())
        assert overlay._timer.isActive()
        assert overlay._timer.interval() == 50

    def test_zero_seconds_keeps_it_up(self, qt_app):
        overlay = self.overlay(seconds=0)
        overlay.show_scan(payload())
        assert not overlay._timer.isActive()

    @pytest.mark.parametrize(
        "corner", ["top-left", "top-right", "bottom-left", "bottom-right", "top-centre"]
    )
    def test_anchors_to_each_corner(self, qt_app, corner):
        from PySide6.QtGui import QGuiApplication

        overlay = self.overlay(corner=corner)
        overlay.show_scan(payload())
        area = QGuiApplication.screens()[0].geometry()
        position = overlay.pos()
        # Inside the screen, and not overlapping the opposite edge.
        assert area.x() <= position.x() <= area.x() + area.width()
        assert area.y() <= position.y() <= area.y() + area.height()

    def test_settings_apply_without_a_restart(self, qt_app):
        overlay = self.overlay(corner="top-left", seconds=5)
        overlay.apply_settings(corner="bottom-right", seconds=20, opacity=0.5)
        assert overlay.corner == "bottom-right"
        assert overlay.seconds == 20
        assert overlay.windowOpacity() == pytest.approx(0.5, abs=0.02)

    def test_an_unknown_screen_index_falls_back(self, qt_app):
        overlay = self.overlay(screen_index=99)
        overlay.show_scan(payload())  # must not raise
        assert overlay._layout.count() == 2


class TestPlatformChoice:
    def test_wayland_session_switches_to_xcb(self, monkeypatch):
        from voidsight.ui import platform

        monkeypatch.setattr(platform.sys, "platform", "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        assert platform.prefer_xwayland() == "xcb"
        assert os.environ["QT_QPA_PLATFORM"] == "xcb"

    def test_an_explicit_choice_is_respected(self, monkeypatch):
        from voidsight.ui import platform

        monkeypatch.setattr(platform.sys, "platform", "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("QT_QPA_PLATFORM", "wayland")
        assert platform.prefer_xwayland() is None
        assert os.environ["QT_QPA_PLATFORM"] == "wayland"

    def test_x11_session_is_left_alone(self, monkeypatch):
        from voidsight.ui import platform

        monkeypatch.setattr(platform.sys, "platform", "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        assert platform.prefer_xwayland() is None

    def test_non_linux_is_left_alone(self, monkeypatch):
        from voidsight.ui import platform

        monkeypatch.setattr(platform.sys, "platform", "darwin")
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        assert platform.prefer_xwayland() is None

    def test_wayland_native_is_reported_as_limited(self):
        from voidsight.ui import platform

        assert not platform.overlay_supported("wayland")
        assert "position itself" in platform.overlay_limitation("wayland")
        assert platform.overlay_limitation("xcb") is None


class TestDesignSystem:
    """Rules that keep components consistent, and one real bug they prevent."""

    def test_labels_do_not_paint_a_background(self, qt_app):
        # A blanket QWidget background rule puts a solid block behind every
        # label, including ones sitting on cards. This is what that looked like.
        from voidsight.ui import style

        sheet = style.stylesheet()
        assert "QLabel, QCheckBox {" in sheet
        assert "background: transparent" in sheet
        widget_rule = sheet.split("QWidget {")[1].split("}")[0]
        assert "background:" not in widget_rule

    def test_containers_do_paint(self, qt_app):
        from voidsight.ui import style

        sheet = style.stylesheet()
        assert "QMainWindow, QStackedWidget, QWidget#page" in sheet
        assert style.active().background in sheet

    def test_one_radius_for_controls_and_one_for_cards(self, qt_app):
        from voidsight.ui import style

        sheet = style.stylesheet()
        assert f"border-radius: {style.RADIUS}px" in sheet
        assert f"border-radius: {style.RADIUS_CARD}px" in sheet
        # Nothing should invent its own corner size.
        import re

        radii = {int(value) for value in re.findall(r"border-radius: (\d+)px", sheet)}
        assert radii <= {style.RADIUS, style.RADIUS_CARD, style.RADIUS_PILL, 0, 5}

    def test_controls_share_one_height(self, qt_app):
        from voidsight.ui import style

        assert f"min-height: {style.CONTROL_HEIGHT}px" in style.stylesheet()

    def test_there_is_no_palette_switching(self, qt_app):
        from voidsight.ui import style

        # One design, not a theming framework.
        assert not hasattr(style, "PALETTES")
        assert not hasattr(style, "use")


class TestToggleSwitch:
    def test_starts_off(self, qt_app):
        from voidsight.ui.widgets import ToggleSwitch

        toggle = ToggleSwitch()
        assert not toggle.isChecked()
        assert toggle.offset == 0.0

    def test_setting_state_moves_the_knob_without_animating(self, qt_app):
        from voidsight.ui.widgets import ToggleSwitch

        toggle = ToggleSwitch()
        toggle.setChecked(True)
        # Programmatic state must land immediately: loading settings should not
        # look like the user just flicked every switch.
        assert toggle.offset == 1.0
        toggle.setChecked(False)
        assert toggle.offset == 0.0

    def test_clicking_toggles_and_emits(self, qt_app):
        from voidsight.ui.widgets import ToggleSwitch

        toggle = ToggleSwitch()
        seen: list[bool] = []
        toggle.toggled.connect(seen.append)
        toggle.click()
        assert toggle.isChecked()
        assert seen == [True]

    def test_is_keyboard_reachable(self, qt_app):
        from PySide6.QtCore import Qt

        from voidsight.ui.widgets import ToggleSwitch

        assert ToggleSwitch().focusPolicy() == Qt.FocusPolicy.StrongFocus

    def test_settings_uses_a_switch_not_a_checkbox(self, qt_app, session: Session):
        from PySide6.QtWidgets import QCheckBox

        from voidsight.ui.widgets import ToggleSwitch

        view = SettingsView(session)
        assert isinstance(view.overlay_enabled, ToggleSwitch)
        assert not isinstance(view.overlay_enabled, QCheckBox)


class TestQuickSellNavigation:
    def test_tabs_sit_with_the_logo_and_switch_pages(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        assert [b.text().strip() for b in window.header.tabs.buttons()] == ["Home", "Quick sell"]

        window.header.tabs.buttons()[1].setChecked(True)
        assert window.pages.currentWidget() is window.quicksell
        window.header.tabs.buttons()[0].setChecked(True)
        assert window.pages.currentWidget() is window.home

    def test_leaving_settings_returns_to_the_last_content_page(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        window.show_page("quicksell")
        window.header.settings.setChecked(True)
        assert window.pages.currentWidget() is window.settings
        window.header.settings.setChecked(False)
        # Not back to Home: you were on Quick sell.
        assert window.pages.currentWidget() is window.quicksell

    def test_a_scan_populates_the_ledger(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        window.on_scan(payload())
        assert len(window.ledger) == 1
        assert [drop.name for drop in window.ledger.groups[0].drops] == [
            "Nikana Prime Blueprint",
            "Braton Prime Stock",
        ]
        # Nothing is pre-selected: the app cannot know which was taken.
        assert window.ledger.selected_drops() == []

    def test_a_scan_brings_home_forward_even_from_quick_sell(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        window.show_page("quicksell")
        window.on_scan(payload())
        assert window.pages.currentWidget() is window.home


class TestQuickSellView:
    def group_payload(self, **overrides):
        base = payload()
        base.update(overrides)
        return base

    def test_empty_until_something_is_scanned(self, qt_app):
        from voidsight.session import SessionLedger
        from voidsight.ui.quicksell import QuickSellView

        view = QuickSellView(SessionLedger())
        assert view.summary.text() == "Quick sell"
        assert not view.clear_button.isEnabled()

    def test_one_block_per_reward_screen(self, qt_app):
        from voidsight.session import SessionLedger
        from voidsight.ui.quicksell import QuickSellView

        ledger = SessionLedger()
        ledger.add_scan(payload())
        ledger.add_scan(payload())
        view = QuickSellView(ledger)
        assert view.body_layout.count() - 1 == 2  # minus the stretch
        assert view.clear_button.isEnabled()

    def test_clicking_a_row_marks_it_and_totals_it(self, qt_app):
        from voidsight.session import SessionLedger
        from voidsight.ui.quicksell import QuickSellView

        ledger = SessionLedger()
        ledger.add_scan(payload())
        view = QuickSellView(ledger)
        block = view.body_layout.itemAt(0).widget()
        row = block.rows[1]  # Braton Prime Stock, 42p

        row.toggle()
        assert row.drop.selected
        view.refresh_summary()
        assert "42 plat" in view.summary.text()

    def test_quantity_multiplies_the_value(self, qt_app):
        from voidsight.session import SessionLedger
        from voidsight.ui.quicksell import QuickSellView

        ledger = SessionLedger()
        ledger.add_scan(payload())
        view = QuickSellView(ledger)
        row = view.body_layout.itemAt(0).widget().rows[1]
        row.toggle()
        row.plus.click()
        assert row.drop.quantity == 2
        view.refresh_summary()
        assert "84 plat" in view.summary.text()

    def test_quantity_never_drops_below_one(self, qt_app):
        from voidsight.session import SessionLedger
        from voidsight.ui.quicksell import QuickSellView

        ledger = SessionLedger()
        ledger.add_scan(payload())
        view = QuickSellView(ledger)
        row = view.body_layout.itemAt(0).widget().rows[0]
        row.minus.click()
        row.minus.click()
        assert row.drop.quantity == 1

    def test_untradeable_rows_cannot_be_marked(self, qt_app):
        from voidsight.session import SessionLedger
        from voidsight.ui.quicksell import QuickSellView

        forma = {
            "index": 2, "matched": True, "name": "Forma Blueprint", "raw_text": "FORMA BLUEPRINT",
            "score": 100.0, "ocr_confidence": 0.9, "ambiguous": False, "platinum": 0.0,
            "lowest": None, "offers": [], "average": 0.0, "volume": 0, "ducats": 0,
            "ducats_per_platinum": None, "vaulted": False, "tradeable": False,
            "live": False, "error": None,
        }
        ledger = SessionLedger()
        ledger.add_scan(payload(rewards=[forma]))
        view = QuickSellView(ledger)
        row = view.body_layout.itemAt(0).widget().rows[0]
        row.toggle()
        assert not row.drop.selected
        assert "not tradeable" in row.price.text()

    def test_listing_is_blocked_until_sign_in_exists(self, qt_app):
        from voidsight.session import SessionLedger
        from voidsight.ui.quicksell import QuickSellView

        view = QuickSellView(SessionLedger())
        assert not view.list_button.isEnabled()
        assert "warframe.market" in view.list_button.toolTip()

    def test_clear_empties_the_ledger(self, qt_app):
        from voidsight.session import SessionLedger
        from voidsight.ui.quicksell import QuickSellView

        ledger = SessionLedger()
        ledger.add_scan(payload())
        view = QuickSellView(ledger)
        view.clear_button.click()
        assert len(ledger) == 0
        assert view.summary.text() == "Quick sell"


class TestProvenance:
    """A saved frame must never be presentable as a live one."""

    def test_home_flags_a_replayed_scan(self, qt_app):
        from voidsight.ui.home import HomeView

        view = HomeView()
        view.show_scan(payload(live_capture=False, source="file: shot.png"))
        assert view.provenance.isVisible() or view.provenance.text() == "NOT FROM GAME"
        assert "shot.png" in view.summary.text()

    def test_home_does_not_flag_a_live_scan(self, qt_app):
        from voidsight.ui.home import HomeView

        view = HomeView()
        view.show_scan(payload(live_capture=True))
        assert view.provenance.text() == ""

    def test_quick_sell_marks_a_replayed_group(self, qt_app):
        from voidsight.session import SessionLedger
        from voidsight.ui.quicksell import QuickSellView

        ledger = SessionLedger()
        ledger.add_scan(payload(live_capture=False))
        from PySide6.QtWidgets import QLabel

        view = QuickSellView(ledger)
        block = view.body_layout.itemAt(0).widget()
        labels = [child.text() for child in block.findChildren(QLabel)]
        assert any("NOT FROM GAME" in text for text in labels)

    def test_a_replayed_row_cannot_be_totalled(self, qt_app):
        from voidsight.session import SessionLedger
        from voidsight.ui.quicksell import QuickSellView

        ledger = SessionLedger()
        ledger.add_scan(payload(live_capture=False))
        view = QuickSellView(ledger)
        row = view.body_layout.itemAt(0).widget().rows[1]
        row.toggle()
        view.refresh_summary()
        # The row still marks — but it is not offered for sale.
        assert row.drop.selected
        assert "plat" not in view.summary.text()

    def test_the_backend_decides_liveness(self, qt_app, session: Session, shots):
        from voidsight.capture.replay import ReplayBackend

        session.backend = ReplayBackend(shots)
        assert not session.live_capture
        # And the source names the file, not just the backend.
        session.scan_now()
        assert "shot.png" in session.store.latest["source"]


class TestClientSize:
    """The window's own size: a UX control, in the top bar, not in Settings."""

    def test_lives_in_the_header(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        assert [b.text() for b in window.header.client_size.group.buttons()] == [
            "S", "M", "L", "XL"
        ]
        # And not in Settings, which is for the game's settings.
        assert not hasattr(window.settings, "client_size")

    def test_defaults_to_medium(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        assert window.header.client_size.value() == "M"
        assert session.config.client_size == "M"

    @pytest.mark.parametrize("label", ["S", "M", "L", "XL"])
    def test_each_step_resizes_the_window(self, qt_app, session: Session, label):
        from voidsight.config import client_size
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        window.apply_client_size(label, persist=False)
        width, height, _ = client_size(label)
        assert window.size().width() == width
        assert window.size().height() == height
        assert session.config.client_size == label

    def test_steps_also_scale_the_font(self, qt_app, session: Session):
        from PySide6.QtWidgets import QApplication

        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        window.apply_client_size("S", persist=False)
        small = QApplication.instance().font().pointSize()
        window.apply_client_size("XL", persist=False)
        assert QApplication.instance().font().pointSize() > small

    def test_choosing_a_size_persists_it(self, qt_app, catalog, tmp_path):
        from voidsight.config import Config
        from voidsight.ui.bridge import Bridge
        from voidsight.vision.pipeline import Scanner

        path = tmp_path / "config.toml"
        config = Config()
        session = Session(
            scanner=Scanner(catalog, config=config, persist=True, config_path=path),
            market=market_stub(),
            config=config,
            store=ScanStore(),
        )
        window = MainWindow(session, Bridge(session))
        window.apply_client_size("L")
        assert Config.load(path).client_size == "L"

    def test_opening_the_window_writes_nothing(self, qt_app, catalog, tmp_path):
        from voidsight.config import Config
        from voidsight.ui.bridge import Bridge
        from voidsight.vision.pipeline import Scanner

        # Startup must not persist anything: only a deliberate choice should.
        path = tmp_path / "config.toml"
        config = Config()
        session = Session(
            scanner=Scanner(catalog, config=config, persist=True, config_path=path),
            market=market_stub(),
            config=config,
            store=ScanStore(),
        )
        MainWindow(session, Bridge(session))
        assert not path.exists()

    def test_an_unknown_size_is_ignored_not_stored(self, qt_app, session: Session):
        from voidsight.ui.bridge import Bridge

        window = MainWindow(session, Bridge(session))
        before = window.size().width()
        window.apply_client_size("ENORMOUS")
        assert window.size().width() == before
        assert session.config.client_size == "M"

    def test_an_unknown_stored_size_falls_back(self, qt_app, catalog):
        from voidsight.config import Config
        from voidsight.ui.bridge import Bridge
        from voidsight.vision.pipeline import Scanner

        config = Config(client_size="ENORMOUS")
        session = Session(
            scanner=Scanner(catalog, config=config, persist=False),
            market=market_stub(),
            config=config,
            store=ScanStore(),
        )
        # Must still open, at the default size, with the header agreeing.
        window = MainWindow(session, Bridge(session))
        assert window.size().width() == 1120
        assert window.header.client_size.value() == "M"


class TestWaitForGame:
    """A login hook should track the game, not sit in front of you all day."""

    def window(self, session, *, wait: bool):
        from voidsight.ui.bridge import Bridge

        return MainWindow(session, Bridge(session), wait_for_game=wait)

    def test_appears_when_the_game_starts(self, qt_app, session: Session, monkeypatch):
        from voidsight import game

        window = self.window(session, wait=True)
        assert not window.game_state.running

        monkeypatch.setattr(
            game, "detect", lambda *a, **k: game.GameState(running=True, signals=("process 1",))
        )
        window.refresh_game_state()
        assert not window.isHidden()

    def test_hides_again_when_the_game_exits(self, qt_app, session: Session, monkeypatch):
        from voidsight import game

        window = self.window(session, wait=True)
        monkeypatch.setattr(
            game, "detect", lambda *a, **k: game.GameState(running=True, signals=("process 1",))
        )
        window.refresh_game_state()
        window.show()

        monkeypatch.setattr(game, "detect", lambda *a, **k: game.GameState(running=False))
        window.refresh_game_state()
        assert window.isHidden()

    def test_normal_mode_ignores_the_game_state(self, qt_app, session: Session, monkeypatch):
        from voidsight import game

        window = self.window(session, wait=False)
        window.show()
        monkeypatch.setattr(game, "detect", lambda *a, **k: game.GameState(running=False))
        window.refresh_game_state()
        # Launched by hand, so it stays put whatever the game is doing.
        assert not window.isHidden()


class TestStartupSection:
    """The Settings panel for launching with the game."""

    def test_shows_the_launch_option_string(self, qt_app, session: Session):
        view = SettingsView(session)
        assert view.launch_command.text().endswith("launch -- %command%")
        # Read-only: it is something to copy, not to edit.
        assert view.launch_command.isReadOnly()

    def test_copy_puts_it_on_the_clipboard(self, qt_app, session: Session):
        from PySide6.QtGui import QGuiApplication

        view = SettingsView(session)
        view.copy_button.click()
        assert QGuiApplication.clipboard().text() == view.launch_command.text()

    def test_autostart_is_disabled_off_linux(self, qt_app, session: Session, monkeypatch):
        from voidsight import startup

        monkeypatch.setattr(startup.sys, "platform", "darwin")
        view = SettingsView(session)
        view._refresh_startup()
        assert not view.autostart.isEnabled()
        assert "Linux" in view.autostart_status.text()

    def test_toggling_autostart_writes_and_removes_the_entry(
        self, qt_app, session: Session, tmp_path, monkeypatch
    ):
        from voidsight import startup

        monkeypatch.setattr(startup.sys, "platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        view = SettingsView(session)
        view._refresh_startup()

        view.autostart.setChecked(True)
        view._on_autostart(True)
        assert startup.autostart_path().exists()

        view._on_autostart(False)
        assert not startup.autostart_path().exists()

    def test_reports_when_steam_is_already_hooked(
        self, qt_app, session: Session, tmp_path, monkeypatch
    ):
        from voidsight import startup

        config = tmp_path / "userdata" / "1" / "config"
        config.mkdir(parents=True)
        (config / "localconfig.vdf").write_text(
            '"230410"\n{\n "LaunchOptions" "voidsight launch -- %command%"\n}\n'
        )
        monkeypatch.setattr(startup, "STEAM_ROOTS", (str(tmp_path),))
        view = SettingsView(session)
        view._refresh_startup()
        assert "already" in view.steam_status.text().lower()

    def test_never_writes_to_steams_config(self, qt_app, session: Session, tmp_path, monkeypatch):
        from voidsight import startup

        config = tmp_path / "userdata" / "1" / "config"
        config.mkdir(parents=True)
        vdf = config / "localconfig.vdf"
        original = '"230410"\n{\n "LaunchOptions" "mangohud %command%"\n}\n'
        vdf.write_text(original)
        monkeypatch.setattr(startup, "STEAM_ROOTS", (str(tmp_path),))

        view = SettingsView(session)
        view._refresh_startup()
        view.autostart.setChecked(True)
        view.apply()
        # Steam rewrites this file on exit; touching it risks every game's
        # options, so the app only ever reads it.
        assert vdf.read_text() == original
