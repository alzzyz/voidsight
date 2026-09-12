# voidsight

A Linux Warframe companion that reads the **relic reward screen** and tells you what each drop is
worth — live warframe.market platinum prices and ducat values — on your second monitor, while you
still have time to pick.

Inspired by [AlecaFrame](https://alecaframe.com/) (Windows/Overwolf only) and
[WFInfo](https://github.com/WFCD/WFinfo), rebuilt from scratch for Linux and Wayland.

> **Status: proof of concept.** The full pipeline — trigger, OCR, matching, pricing, UI — works end
> to end and is covered by tests. Live screen capture on Linux is written but **not yet verified on
> real hardware**; see [Bringing up a new machine](#bringing-up-a-new-machine).

## How it works

1. Watches Warframe's own `EE.log` for the lines the game writes when a reward screen opens, and
   for the relic you just cracked.
2. Pulls a frame from a rolling capture buffer. Wine buffers the game's log writes, so the trigger
   often arrives *after* the screen appeared — the buffer is searched backwards until a frame reads,
   which is the failure mode existing Linux tools document.
3. Masks the frame by the UI theme's accent colours to isolate reward text, and looks for a band
   that divides into evenly spaced columns. The reward screen has several such bands — ducat
   counts, "2 Owned", squadmate names — so which one is the names is decided by reading them, not
   by where they sit.
4. Runs Tesseract on each column, then fuzzy-matches against the ~600 known prime part names —
   narrowed to the relic's six possible rewards when the log told us which relic it was.
5. Asks warframe.market for current top orders and renders a card per reward.

Locating the names on a busy frame is ambiguous, so several readings are proposed and then
**verified by reading them**: whichever produces text that resolves to real item names wins.

Two scale settings exist and they are easy to confuse. **UI Scaling** in Settings is *Warframe's*
own Interface option — it decides how big the reward panel is on screen, so it has to match the
percentage the game shows. **Client size** (S/M/L/XL, in the top bar) is this app's own window,
and changing it scales the window and its text together.

**Set your UI theme and UI Scaling in Settings** (the button in the page header), to match
Warframe's own Interface options — same as you would in AlecaFrame. Those two settings decide which
pixels count as reward text and how big the reward panel is, so with them set a scan is a single
pass. Leaving the theme on *Detect automatically* makes the first scan try every theme and remember
whichever one read, which works but is slower and can be fooled by a bad frame.

## What it does not do

No reading or writing of game memory, no network interception, no input automation, no modified
game files. It takes screenshots and reads a log file the game itself writes — the same approach
WFInfo has used on Windows for years. See [Digital Extremes on third-party
software](https://support.warframe.com/hc/en-us/articles/360030014351-Third-Party-Software-and-You).

## Install

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/) and Tesseract.

```sh
sudo pacman -S tesseract tesseract-data-eng    # or: apt install tesseract-ocr
uv sync
uv run voidsight update-data                  # item, relic and price data
```

For live capture on Linux, also install the X11 extra: `uv sync --extra x11`.

## Use

The desktop client is the main way to run this:

```sh
uv sync --extra desktop     # on Linux this includes X11 capture
uv run voidsight app
```

The first run downloads the item, relic and price data by itself. Those feeds come from two
hosts, and voidsight starts on whichever it can reach: if WFInfo's data on `api.warframestat.us`
is down, it falls back to warframe.market alone and says so in the header. Reward reading and live
prices still work; relic drop tables, vaulted flags and average prices wait for that host to
return. Tesseract is a system
package and has to come from your distribution:

```sh
sudo pacman -S tesseract tesseract-data-eng     # Arch
sudo apt install tesseract-ocr                  # Debian/Ubuntu
sudo dnf install tesseract                      # Fedora
```

Three tabs — Rewards, Mastery (not built yet), Settings — and it follows `EE.log` on its own. Qt
rather than a web view because the same toolkit has to carry an in-game overlay later; a browser
cannot do a transparent, always-on-top, click-through window over a fullscreen game.

There is also a web UI (`voidsight serve`) rendering the same data, useful for watching from a
phone or another machine. Both front ends consume the identical payload, so neither can drift.

```sh
# Read a saved screenshot — the fastest way to check accuracy.
uv run voidsight scan shot.png
uv run voidsight scan shot.png --debug-dir /tmp/dbg   # every intermediate image
uv run voidsight scan shot.png --relic "Axi A1"       # narrow to that relic's drops

# Which capture backend works on this machine?
uv run voidsight probe --save-to /tmp/probe

# Follow the game and print rewards as they drop.
uv run voidsight watch

# Second-screen web app (follows EE.log too).
uv run voidsight serve --open
```

`app` and `serve` both accept `--replay-dir`, so the whole thing runs off saved screenshots with no
game present — that is how it is developed on a machine that is not playing Warframe.

## Configuration

`~/.config/voidsight/config.toml`, written automatically once the UI theme is worked out:

```toml
theme = "Vitruvian"    # your Warframe UI theme; omit to detect it
ui_scale = 1.0         # Warframe's own UI Scaling, as a multiplier (1.0 = 100%)
client_size = "M"      # size of this app's window: S, M, L, XL
backend = "auto"       # auto | x11 | replay
prefer = "platinum"    # which reward the UI marks as best: platinum | ducats
port = 8765

# Only if the reward panel is not where voidsight looks for it. Fractions of
# the frame: [x, y, width, height]. `scan --debug-dir` draws the box it used.
# panel_region = [0.25, 0.2, 0.5, 0.4]
```

Everything here can be set from the Settings panel except `panel_region`.

## Bringing up a new machine

Capture is the one part that cannot be settled by reading documentation — whether an X11 `GetImage`
on the Warframe window works depends on the compositor. On Wayland, Warframe runs through XWayland
(Proton), so its window is a real X11 window and often readable; KWin may refuse.

1. Start Warframe, then run `voidsight probe --save-to /tmp/probe` and look at the saved PNG.
2. If it captured a real frame, `voidsight watch` should work as is.
3. If it captured black or found nothing, the xdg-desktop-portal screencast path is needed — the
   probe output says so, and that backend is the next thing to build.

While you are there, grab a few real reward screenshots at your own resolution (`probe --save-to`
during a fissure, or Spectacle) and drop them in `tests/fixtures/`. The vision numbers are currently
tuned against synthetic reward screens, which get the layout and themes right but not Warframe's
actual font — and the reward panel's position has only been checked against 1920x1080 captures.
Run `voidsight scan <shot> --debug-dir /tmp/dbg` and look at `overlay.png`: if the drawn box does
not contain the reward names, set `panel_region` and the rest follows.

## Development

```sh
uv run --group dev pytest      # 124 tests, no network
uv run --group dev ruff check .
```

Tests never hit the network: the market API is stubbed with `httpx.MockTransport` and reward screens
are generated by `voidsight.testing.render_reward_screen`.

## Item artwork

Reward cards and quick-sell rows show the item's icon, fetched on first use into
`~/.cache/voidsight/icons/`. Artwork is Digital Extremes' property and is never
vendored into this repository.

Two sources exist and the choice is only obvious once you count them.
[DE's public export](https://content.warframe.com/PublicExport/index_en.txt.lzma)
is the official, sanctioned manifest and covers 586 of our 592 prime parts — but
with just 174 distinct textures, because `GenericWarframePrimeChassis.png` is
reused 56 times. It cannot tell one frame's chassis from another's.
warframe.market ships a distinct icon per item plus a part-type sub-icon for the
same 586, and those paths already arrive with the item list. So the market icon
is the picture and the export is the fallback.

The export remains valuable for another reason: it is keyed by the game's own
`uniqueName` paths, the same identifiers warframe.market exposes as `gameRef`
and the same ones an inventory payload would use. That is the join between our
data and anything of the game's — see [docs/inventory.md](docs/inventory.md).

## Starting with the game

Settings → **Start with Warframe** offers two hooks, and the difference between them is who owns
the file.

**Steam launch options** are the tidier mechanism. Paste this into Warframe → Properties → Launch
Options:

```
voidsight launch -- %command%
```

`launch` starts the client, runs the game, and stops the client when the game exits — Steam waits on
voidsight, which waits on Warframe, so nothing is left running afterwards. The app shows the string
and reports what Steam currently has, but **will not edit it for you**: Steam rewrites
`localconfig.vdf` wholesale when it exits, so a change made behind its back is discarded at best and
damages every other game's launch options at worst.

**A login entry** is ours to write, so that one is a toggle. It drops a `.desktop` file in
`~/.config/autostart/` running `voidsight app --wait-for-game`, which starts hidden, shows the
window when Warframe appears, and hides again when it exits — a login hook should not put a window
in front of someone who is not playing. It reacts to the game being started any way at all, Steam
or Lutris or a bare Wine prefix. Linux only; on other platforms the toggle says so instead of
pretending.

Either hook works on its own, and enabling both is harmless but pointless — the second client to
start finds the first holding a lock in `$XDG_RUNTIME_DIR` and exits quietly rather than tailing the
same log and capturing the same screen twice.

## Overlay

Reward prices can be drawn over the game itself. Turn it on in Settings, or start with
`voidsight app --overlay`, and use **Preview overlay** to place it without running a mission.

Two things it depends on:

**Run Warframe in Borderless Fullscreen.** An exclusively-fullscreen window sits above everything
else and nothing can be drawn over it. Borderless is a normal window as far as the compositor is
concerned, so an always-on-top overlay works — and WFInfo needs the same setting for capture, so
this costs nothing extra.

**On Wayland it runs through XWayland.** A Wayland client cannot place its own windows — `move()` is
a request the compositor may ignore, and KWin does — so a corner-anchored overlay is impossible
natively without the layer-shell protocol, which Qt only reaches through a C++ library with no
Python bindings. On a Wayland session the client therefore asks for Qt's `xcb` plugin, putting it in
the same X server Proton already runs the game in. Set `QT_QPA_PLATFORM` yourself to override; if
you force `wayland`, the client says so in Settings and the overlay will appear wherever KWin
decides.

The overlay never takes focus and never receives input, so it cannot eat a click or an alt-tab
mid-mission. It hides itself after a configurable delay (default 12s, 0 keeps it up).

## Where this is going

The core (2,500 lines: capture, vision, pricing, log watching) imports nothing from either front
end, so views are cheap to add or replace. Planned, roughly in order:

- **Mastery progress** — which frames and weapons are unlevelled. Warframe's public profile endpoint
  returns XP per item and your account id is in `EE.log`, so this needs no credentials and nothing
  unofficial.
- **Owned counts on the reward screen** — the game already prints "2 Owned" under each reward when
  Item Labels is on, so "do I still need this?" is answerable by reading the frame we have.

Knowing what you actually collected — rather than what was offered — is deliberately deferred; see
[docs/inventory.md](docs/inventory.md) for the options, the official account API's mechanism, and
why manual confirmation wins for now.

Full inventory tracking ("which sets am I closest to finishing") is deliberately *not* on that list.
It needs data no public endpoint exposes. AlecaFrame reads it via Overwolf's Warframe game-events
provider, which injects into the game under an agreement between Overwolf and Digital Extremes —
Windows-only, and a business arrangement rather than an API anyone can call. The alternatives are
DE's unofficial account API (your credentials, unblessed by DE), or importing a snapshot from
elsewhere.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for attribution to the WFInfo project, whose
theme colour table, crop geometry and log trigger strings this builds on.

Warframe is a trademark of Digital Extremes Ltd. This project is unofficial and unaffiliated.
