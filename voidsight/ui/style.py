"""The design system: colour, spacing, shape, and the component rules built on them.

Two accents with different jobs. `structure` marks navigation, focus and
selection; `value` marks what the player is deciding about — the platinum figure
and the best pick — and nothing else. Keeping value scarce is what makes a
reward screen readable in the seconds it is up.

Two rules worth knowing before editing the stylesheet:

* **Only containers paint a background.** A blanket `QWidget { background }`
  rule also hits every label, which then paints the page colour on top of
  whatever card it sits in — text in little black boxes. Labels and checkboxes
  are explicitly transparent here, and backgrounds are named per container.
* **Shape and spacing come from the constants below**, not from numbers typed
  into a rule. A control is `RADIUS`, a card is `RADIUS_CARD`, anything
  pill-shaped is `RADIUS_PILL`; that is what stops six subtly different corner
  radii accumulating.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PALETTE = "Lotus"


@dataclass(frozen=True)
class Palette:
    name: str
    #: Surfaces, darkest first.
    background: str
    surface: str
    surface_2: str
    surface_3: str
    border: str
    border_strong: str
    text: str
    muted: str
    faint: str
    #: Navigation, focus, selection.
    structure: str
    structure_dim: str
    structure_wash: str
    #: Platinum, the best pick. Nothing else.
    value: str
    value_dim: str
    good: str
    warn: str
    bad: str


PALETTE = Palette(
    name="Lotus",
    background="#0d0a14", surface="#171223", surface_2="#211a31", surface_3="#2b2240",
    border="#332947", border_strong="#453a5e",
    text="#ece8f5", muted="#9086a8", faint="#6b6280",
    structure="#a678f0", structure_dim="#6d4aa8", structure_wash="#1e1630",
    value="#f0c05a", value_dim="#7a5f24",
    good="#5bd6a0", warn="#f5b944", bad="#f2686b",
)


def active() -> Palette:
    return PALETTE


# --- Shape and spacing -------------------------------------------------------
#: Controls: buttons, inputs, segments. One radius, used everywhere.
RADIUS = 8
#: Containers that hold controls.
RADIUS_CARD = 12
#: Anything read as a token rather than a control: chips, toggles, ribbons.
RADIUS_PILL = 999

#: Every control is this tall, so a row of mixed widgets lines up.
CONTROL_HEIGHT = 36
#: 4-point spacing scale. Layout code uses these names, not bare numbers.
SPACE_XS, SPACE_S, SPACE_M, SPACE_L, SPACE_XL = 4, 8, 14, 20, 28

#: Hover and toggle transitions. Qt stylesheets cannot animate, so this is for
#: the widgets that animate themselves (see `widgets.ToggleSwitch`).
DURATION_MS = 130


#: Tried in order at startup. Naming a missing family in the stylesheet makes Qt
#: scan every installed font looking for an alias, which it then warns about, so
#: the choice is resolved against what is actually installed instead.
FONT_PREFERENCES = (
    "Inter",
    "SF Pro Text",
    "Segoe UI Variable Text",
    "Segoe UI",
    "Noto Sans",
    "Cantarell",
    "Helvetica Neue",
    "DejaVu Sans",
)


def resolve_font() -> str | None:
    """First preferred font family that exists here, or None for Qt's default."""
    from PySide6.QtGui import QFontDatabase

    installed = set(QFontDatabase.families())
    return next((name for name in FONT_PREFERENCES if name in installed), None)


def stylesheet(palette: Palette | None = None) -> str:
    p = palette or active()
    return f"""
/* Text and metrics only: painting a background here would put a solid block
   behind every label, including the ones sitting on cards. */
QWidget {{ color: {p.text}; font-size: 14px; }}
QLabel, QCheckBox {{ background: transparent; }}

/* Containers, which do paint. */
QMainWindow, QStackedWidget, QWidget#page {{ background: {p.background}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}

QLabel#logo {{ font-size: 17px; font-weight: 700; letter-spacing: 2px; }}
QLabel#heading {{ font-size: 15px; font-weight: 600; }}
QLabel#section {{
    font-size: 11px; font-weight: 700; letter-spacing: 1.4px;
    color: {p.faint}; text-transform: uppercase;
}}
QLabel#muted, QLabel#hint {{ color: {p.muted}; font-size: 12px; }}
QLabel#faint {{ color: {p.faint}; font-size: 12px; }}

QFrame#header {{ background: {p.surface}; border-bottom: 1px solid {p.border}; }}
QFrame#divider {{ background: {p.border}; max-height: 1px; border: none; }}

QLabel#chip {{ color: {p.muted}; font-size: 12px; font-weight: 500; }}
QLabel#chip[state="good"] {{ color: {p.good}; }}
QLabel#chip[state="bad"] {{ color: {p.bad}; }}
QLabel#chip[state="idle"] {{ color: {p.faint}; }}

/* Buttons. One shape, one height, and a hover that lifts rather than recolours
   — recolouring on hover competes with the accents, which carry meaning. */
QPushButton {{
    background: {p.surface_2}; border: 1px solid {p.border};
    border-radius: {RADIUS}px; padding: 0 {SPACE_M}px;
    min-height: {CONTROL_HEIGHT}px; color: {p.text};
}}
QPushButton:hover:!disabled {{ background: {p.surface_3}; border-color: {p.border_strong}; }}
QPushButton:pressed {{ background: {p.structure_wash}; border-color: {p.structure_dim}; }}
QPushButton:focus {{ border-color: {p.structure}; outline: none; }}
QPushButton:disabled {{ color: {p.faint}; background: {p.surface}; border-color: {p.border}; }}

/* The one filled button per screen: the action you came to perform. */
QPushButton#primary {{
    background: {p.structure_wash}; border-color: {p.structure_dim}; color: {p.text};
}}
QPushButton#primary:hover:!disabled {{ border-color: {p.structure}; background: {p.surface_3}; }}

/* Navigation reads as text until it is the page you are on. */
QPushButton#nav {{
    background: transparent; border: 1px solid transparent;
    padding: 0 {SPACE_M}px; color: {p.muted};
}}
QPushButton#nav:hover {{ color: {p.text}; background: {p.surface_2}; }}
QPushButton#nav:checked {{
    color: {p.structure}; background: {p.structure_wash}; border-color: {p.structure_dim};
}}

/* Page tabs, beside the logo. Same flat-until-active rule as nav. */
QPushButton#tab {{
    background: transparent; border: 1px solid transparent;
    border-radius: {RADIUS}px; padding: 0 {SPACE_M}px;
    min-height: {CONTROL_HEIGHT}px; color: {p.muted}; font-weight: 500;
}}
QPushButton#tab:hover {{ color: {p.text}; background: {p.surface_2}; }}
QPushButton#tab:checked {{
    color: {p.structure}; background: {p.structure_wash}; border-color: {p.structure_dim};
}}

/* Quick-sell rows. The whole row is the hit target, so it has to look like one. */
QFrame#row {{
    background: transparent; border: 1px solid transparent; border-radius: {RADIUS}px;
}}
QFrame#row:hover {{ background: {p.surface_2}; }}
QFrame#row[selected="true"] {{
    background: {p.structure_wash}; border-color: {p.structure_dim};
}}
QFrame#row[sellable="false"] {{ background: transparent; }}
QFrame#row[sellable="false"]:hover {{ background: transparent; }}
QFrame#row[sellable="false"] QLabel#name {{ color: {p.faint}; font-weight: 500; }}

QPushButton#stepper {{
    background: {p.surface_2}; border: 1px solid {p.border};
    border-radius: {RADIUS_PILL}px; padding: 0; min-height: 0;
    color: {p.muted}; font-size: 15px; font-weight: 600;
}}
QPushButton#stepper:hover:!disabled {{ color: {p.text}; border-color: {p.border_strong}; }}
QPushButton#stepper:disabled {{ color: {p.faint}; background: transparent; }}

QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit {{
    background: {p.surface_2}; border: 1px solid {p.border};
    border-radius: {RADIUS}px; padding: 0 {SPACE_S}px;
    min-height: {CONTROL_HEIGHT}px; min-width: 210px;
    selection-background-color: {p.structure_dim};
}}
QComboBox:hover, QDoubleSpinBox:hover {{ border-color: {p.border_strong}; }}
QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border-color: {p.structure}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {p.surface_2}; border: 1px solid {p.border};
    border-radius: {RADIUS}px; padding: {SPACE_XS}px;
    selection-background-color: {p.structure_wash}; selection-color: {p.text}; outline: none;
}}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 18px; border: none; }}

/* Segmented control: one shape, split. Only the ends are rounded. */
QPushButton#segment {{
    background: {p.surface_2}; border: 1px solid {p.border};
    border-radius: 0; padding: 0; min-width: 54px;
    min-height: {CONTROL_HEIGHT}px; color: {p.muted};
}}
QPushButton#segment:checked {{
    background: {p.structure_wash}; color: {p.structure}; border-color: {p.structure_dim};
}}
QPushButton#segment:hover:!checked {{ color: {p.text}; background: {p.surface_3}; }}
QPushButton#segment[edge="left"] {{
    border-top-left-radius: {RADIUS}px; border-bottom-left-radius: {RADIUS}px;
}}
QPushButton#segment[edge="right"] {{
    border-top-right-radius: {RADIUS}px; border-bottom-right-radius: {RADIUS}px;
}}

/* Compact segments: the header's client-size picker, which must not shout. */
QPushButton#segment-compact {{
    background: {p.surface_2}; border: 1px solid {p.border};
    border-radius: 0; padding: 0; min-width: 30px; max-width: 34px;
    min-height: 26px; max-height: 26px; color: {p.faint}; font-size: 11px;
}}
QPushButton#segment-compact:checked {{
    background: {p.structure_wash}; color: {p.structure}; border-color: {p.structure_dim};
}}
QPushButton#segment-compact:hover:!checked {{ color: {p.text}; }}
QPushButton#segment-compact[edge="left"] {{
    border-top-left-radius: {RADIUS}px; border-bottom-left-radius: {RADIUS}px;
}}
QPushButton#segment-compact[edge="right"] {{
    border-top-right-radius: {RADIUS}px; border-bottom-right-radius: {RADIUS}px;
}}

QFrame#card, QFrame#panel {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: {RADIUS_CARD}px;
}}
QFrame#card[best="true"] {{ border: 1px solid {p.value_dim}; }}
QFrame#card[matched="false"] {{ border: 1px dashed {p.border}; }}

QLabel#name {{ font-size: 16px; font-weight: 600; }}
QLabel#platinum {{ font-size: 36px; font-weight: 700; }}
QLabel#platinum[best="true"] {{ color: {p.value}; }}
/* Where the platinum figure would be on a card that has no price. Deliberately
   quiet and never `value`: these cards are not what the player is choosing
   between, and the accent has to stay scarce to mean anything. */
QLabel#state {{ font-size: 18px; font-weight: 600; color: {p.muted}; }}
QLabel#ribbon {{
    background: {p.value}; color: {p.background};
    font-size: 10px; font-weight: 800; letter-spacing: 1px;
    padding: 3px {SPACE_S}px; border-radius: {RADIUS_PILL}px;
}}
QLabel#badge {{ font-size: 10px; font-weight: 600; letter-spacing: .6px; }}

QStatusBar {{ background: {p.surface}; color: {p.muted}; border-top: 1px solid {p.border}; }}
QStatusBar::item {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {p.surface_3}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.border_strong}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{
    background: {p.surface_2}; color: {p.text};
    border: 1px solid {p.border}; padding: 6px {SPACE_S}px; border-radius: {RADIUS}px;
}}
"""
