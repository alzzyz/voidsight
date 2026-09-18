# Stop capture from costing the game its frame rate

Six independent work packages, ordered. **Run one per session, `/clear` between
them.** Each names every file it needs — no exploration required.

---

## How to run this with minimum context

Paste **"Shared context"** below plus **one package**. Then:

- The file list in each package is complete. Do not use Explore/Agent subagents,
  do not grep the repo, do not read `README.md`, `docs/`, `uv.lock` or `.venv`.
- Read with `offset`/`limit` at the line numbers given, not whole files.
- Run only the test file the package names until it passes; run the full suite once
  at the end.
- `DESIGN.md` is 10 KB. When a package says to add an entry, read only lines 1-40
  for the house style and append — never read the whole file.
- Packages 1-3 are sequential. **Package 4 is independent** and can be done any
  time, or dropped. Package 5 depends on 3.
- **Package 2 is reserved for Opus** — the cropped-frame seam has three consumers
  that each assume a frame is the whole window, and a wrong version still passes
  most tests by reading *something*. Everything else is sized for Sonnet.

---

## Shared context

voidsight captures the Warframe window 6x/second for the whole session so a late
`EE.log` line can be answered from a ring buffer. This noticeably lowers in-game
FPS and disturbs v-sync.

`X11Backend.grab()` (`voidsight/capture/x11.py:190-225`) calls `get_image()` on the
game's window. That is a **VRAM-to-CPU download**, synchronous and unaccelerated on
most drivers, so every grab stalls the game's render loop. Its **duration scales
with the area** read back; its **frequency** is the capture rate. The two multiply.

Today both terms are maximised: the **whole window** (3440x1440 = 4.95 Mpx, ~20 MiB
over the socket, ~14.2 MiB strided RGB copy) at **6 Hz**, always. The vision
pipeline then throws ~92% of it away — `locate.panel_box` (`vision/locate.py:79-115`)
crops to a 968x420-scaled centre box first, and `panel_region` is applied *there*,
as a numpy crop. It never reaches the X request.

The goal is to shrink each stall (read back only the panel box) and make stalls
rare (idle at a trickle, burst on a trigger), then add a hotkey and a manual-only
mode.

**Two repo hooks will block a push:**

- `.claude/hooks/check-design-change.sh` denies `git push` when
  `voidsight/{vision,capture,trigger}/`, `live.py`, `session.py` or `config.py`
  changed without `DESIGN.md` changing too. Each package below says whether it
  needs an entry or should use the documented escape hatch (a trailing
  `# no-design-change` comment on the push command, for a change that genuinely
  decides nothing).
- `.claude/hooks/check-version-bump.sh` requires a bump in
  `voidsight/__init__.py` (currently `0.2.0`) and `pyproject.toml`.

Verify with `uv run pytest` and `uv run ruff check` (line-length 100).

---

## Package 0 — `voidsight probe --benchmark`

**Goal.** Measure the stall on the Linux machine before changing anything. Nothing
below should be trusted without these numbers, and none of it is measurable on the
macOS dev box.

**Files.** `voidsight/cli.py` (find the `probe` subparser and its handler),
`voidsight/capture/probe.py` (133 lines, read whole), `voidsight/capture/x11.py:190-225`
(read only), `voidsight/vision/locate.py:79-115` (read only — `panel_box` signature).

**Change.** Add `--benchmark` to the existing `probe` command. Against the live
game, report:

1. Per-grab wall time, min/median/max over ~30 grabs, for three sizes: the full
   window; `panel_box(frame.shape, config.ui_scale, config.panel_region)`; one
   intermediate. Call `panel_box` rather than hardcoding a box, so it measures what
   the pipeline actually uses.
2. **The runaway check.** A sustained 6 Hz full-window loop for 30 s, reporting the
   *achieved* rate and how many iterations had zero sleep. `_capture_loop`
   (`voidsight/live.py:145`) sleeps `interval - elapsed`; if a grab exceeds 166 ms
   that is zero and it grabs back-to-back forever. If this fires, it alone explains
   the symptom.
3. The same sustained loop at the panel-box region, and at 1 Hz.
4. Window geometry and `display_name` (the probe already gathers these).

**Hooks.** Touches `capture/` but decides nothing — use the `# no-design-change`
escape hatch. Version bump still required.

**Verify.** `uv run pytest tests/test_capture.py`. Then, on the Linux box with the
game in a mission: run each sustained case and watch in-game FPS, plus a control
with voidsight closed (XWayland + Proton has documented v-sync problems of its own).

**These numbers decide Package 2.** If the region grab is roughly
area-proportionally faster, it is the main fix. If a small grab stalls nearly as
much as a full one, the readback is sync-dominated, only frequency helps, and the
portal backend (Package 6) should be promoted ahead of everything else.

---

## Package 1 — cheap capture wins, no behaviour change

**Goal.** Remove per-frame waste. Nothing here changes what is captured.

**Files.** `voidsight/capture/x11.py` (225 lines), `voidsight/capture/base.py:54-97`,
`voidsight/live.py:103` and `:133-145`, `voidsight/game.py:81-91`,
`voidsight/ui/main.py:37` and `:154-194`, `voidsight/cli.py` (two `RingBuffer()`
call sites, ~`:415` and ~`:618`), `tests/test_capture.py`.

**Changes.**

1. **Cache the geometry.** `get_geometry()` runs on every grab (`x11.py:200`),
   costing one X round trip per frame. Refresh it on the existing
   `WINDOW_SEARCH_INTERVAL = 2.0` cadence (`x11.py:33`) instead.
2. **Replace the strided copy.** `np.ascontiguousarray(image[:, :, 2::-1])`
   (`x11.py:225`) is a reversed-stride gather forced into a fresh buffer — the
   per-frame allocation. Use `cv2.cvtColor(..., cv2.COLOR_BGRA2RGB)`.
   `opencv-python-headless` is already a core dependency. Keep the existing
   `channels >= 3` fallback for the 3-channel case.
3. **Derive the ring buffer cap.** `RingBuffer.__init__(self, *, seconds=3.0,
   max_frames=40)` (`base.py:64`) hardcodes 40 while 6 Hz over a 4 s lookback needs
   24 — ~40% of resident frame memory is never read. Take `fps` and compute
   `max_frames = ceil(fps * seconds)`, keeping an explicit override. Update both
   `cli.py` call sites and `live.py:103`.
4. **Stop the 5-second X connection.** `game.detect` -> `find_window` (`game.py:81-91`)
   -> `find_window_title` (`x11.py:47-60`) builds a whole `X11Backend`, opens a
   display, walks the tree to depth 6 and closes it — every 5 s from the Qt main
   thread (`ui/main.py:37`, timer at `:113-116`, handler `refresh_game_state` at
   `:154`). Reuse the session's existing backend when there is one; keep the
   throwaway path as the fallback for when there is not.

**Hooks.** `# no-design-change` escape hatch. Version bump required.

**Verify.** `uv run pytest tests/test_capture.py tests/test_live.py`.

---

## Package 2 — grab only the panel box  *(Opus)*

**Goal.** Cut the readback area ~12x at 3440x1440. This is the largest single win
and the one design-heavy change. Review this diff by eye, not only by its tests.

**Files.** `voidsight/capture/x11.py:190-225`, `voidsight/capture/base.py:29-51`
(`Shot`, `CaptureBackend`), `voidsight/vision/locate.py:79-115` (`panel_box`) and
`:371-400` (`candidates`), `voidsight/vision/pipeline.py:250-302` (`Scanner.scan`,
note `region=self.config.panel_region` at `:299`), `voidsight/live.py:161-186`
(`_keep_failure`), `tests/test_capture.py`, `tests/test_vision.py`.

**Change.** Pass the region's origin and size to `get_image(x, y, w, h, 2,
0xFFFFFFFF)` instead of `0, 0, geometry.width, geometry.height` (`x11.py:201-203`).

**The seam — get this right.** `panel_box(shape, ui_scale, region)` derives its box
from the **frame shape**, so handing it a pre-cropped frame makes it crop again,
wrongly. Three consumers assume a frame is the whole window:

- `Scanner.scan` / `locate.candidates` — must be told the frame *is* the panel box;
- `LiveRunner._keep_failure` (`live.py:161-186`), which writes the frame as a PNG;
- `voidsight scan <frame> --debug-dir`, which re-derives geometry from that PNG.

Resolve it explicitly: carry the capture origin and the full window shape on `Shot`
(`base.py:29-36`), and give `Scanner` an explicit way to be told the frame is
already the region (simplest: a flag that makes `panel_box` return the whole frame,
equivalent to `region=[0,0,1,1]`). Saved failure frames stay diagnosable — smaller,
and re-scannable with the recorded region.

Keep a config escape: if `panel_region` is set, honour it as the grab region too.

**Hooks.** **Needs a `DESIGN.md` entry** — read only lines 1-40 for style, then
append: what a "frame" now means, why the readback is the cost, and what would
overturn it (Package 0's numbers showing the stall is not area-proportional).
Version bump required.

**Verify.** `uv run pytest tests/test_vision.py tests/test_capture.py`. New test: a
cropped frame reads the same rewards as the full frame it came from. Then on the
Linux box: `voidsight probe --benchmark` before/after, same machine, game in a
mission.

---

## Package 3 — trickle while playing, burst on a trigger

**Goal.** Make stalls rare. Both rates configurable.

**Files.** `voidsight/config.py:82-162` (the `Config` dataclass; note `save` at
`:144-153` skips `None` values), `voidsight/live.py:88-116` (constructor) and
`:133-145` (`_capture_loop`) and `:202-265` (`scan_for`), `voidsight/ui/settings.py`
(`:75-78` signals, `:435-462` `reload`, `:496-508` `_apply_overlay_settings`,
`:510-523` `apply`), `voidsight/ui/main.py:259-267`, `tests/test_live.py`,
`tests/test_ui.py:185-255`.

**Changes.**

1. Add `idle_capture_fps` (default `1.0`) and `active_capture_fps` (default `6.0`)
   to `Config`, with `#:` comments in the existing style.
2. `_capture_loop` currently snapshots `interval = 1.0 / self.fps` once before the
   loop (`live.py:134`). Read the interval from the current mode each pass instead.
   `scan_for` raises to active for the length of the reward screen (~15 s per
   `EE.log`) and drops back after.
3. **Wiring that does not exist yet.** `LiveRunner`'s timings are constructor-only
   kwargs (`live.py:88-116`), never read from `Config`; and the runner is a **local
   variable in `run()`** that nothing keeps a reference to (`ui/main.py:259-267`).
   Keep it on `MainWindow` (or on `Session`) so Settings and the hotkey can reach
   it.
4. Settings UI: follow the documented pattern — field on `Config`, widget next to a
   `QFormLayout`, set in `reload()`, read in `apply()` before
   `session.save_config()`, and an applier shaped like `_apply_overlay_settings`
   (`settings.py:496-508`) to push it into the live runner.

At 1 Hz the 4 s lookback holds 4 frames against a backward pass that reads up to 6
(`live.py:220`) — reduced but intact. `idle_capture_fps = 0` means no capture at
all, which is what manual-only mode (Package 5) sets.

**Hooks.** **Needs a `DESIGN.md` entry** — why capture idles instead of running
flat out, and what would overturn it (a trigger observed arriving late enough that
a 1 Hz buffer misses the panel). Version bump required.

**Verify.** `uv run pytest tests/test_live.py tests/test_ui.py`. Note
`tests/test_live.py` sets `runner.fps` directly at `:252, :275, :309, :402, :423` —
all five need updating for the idle/active split. New test: the loop changes rate on
trigger and reverts.

---

## Package 4 — the OCR burst (independent; droppable)

**Goal.** Two defects that land in the 15 seconds you are looking at the reward
screen. Unrelated to the capture stall — do this any time, or not at all.

**Files.** `voidsight/live.py:147-159` (`_watch_forward`), `:202-265` (`scan_for`,
note `_last_success` set at `:264`), `voidsight/trigger/eelog.py:33-37`,
`tests/test_live.py`.

**Changes.**

1. **`SETTLE_SECONDS` arms on success only.** `self._last_success` is set at
   `live.py:264`, after a reading. Warframe writes **three** trigger strings per
   reward screen (`eelog.py:33-37`), so when the first scan fails to read, the
   second and third each launch a complete scan. Arm the settle timer on every
   trigger that starts a scan, not only on a successful one.
2. **The forward watch has no scan budget.** The backward pass breaks at
   `max_frames_scanned = 6` (`live.py:220`); `_watch_forward` is bounded only by an
   8 s deadline, so it can scan ~48 frames, each costing up to 24-48 `tesseract`
   process spawns (`vision/pipeline.py:148-162`, `vision/ocr.py:98-127`). Give it
   the same budget.

**Hooks.** Touches `live.py`. These are bug fixes, so the `# no-design-change`
escape hatch is defensible — but the settle change alters deliberate-looking
behaviour, so a short `DESIGN.md` entry is the safer call. Version bump required.

**Verify.** `uv run pytest tests/test_live.py`. New tests: a *failed* trigger arms
the settle timer; the forward watch stops at its budget.

---

## Package 5 — hotkey and manual-only mode

**Goal.** A bindable global hotkey, plus a mode with no background capture at all.

**Depends on Package 3** (needs the runner reference and the rate config).

**Files.** New `voidsight/ui/hotkey.py`; `voidsight/capture/x11.py:87-105` (the
display handle), `voidsight/ui/bridge.py:51-63`, `voidsight/ui/main.py:87-88` and
`:199-221`, `voidsight/ui/settings.py:343-346`, `voidsight/ui/widgets.py`
(`ToggleSwitch`/`Segmented` as the custom-widget precedent), `voidsight/config.py`,
`voidsight/ui/platform.py:35-49`, `tests/test_ui.py`.

**The scan path already exists end to end** — build nothing new here:
`settings.scan_requested` -> `bridge.scan_now` (`bridge.py:51-54`, on a
`QThreadPool`) -> `Session.scan_now()` (`app/server.py:67-76`) -> `MainWindow.on_scan`
(`main.py:199-221`), which already updates home, ledger, quicksell, overlay and the
status bar. A hotkey only has to reach `bridge.scan_now`.

**Changes.**

1. **`XGrabKey` on the root window**, on its own thread, emitting into `Bridge`.
   `python-xlib` is already in the `desktop` extra (`pyproject.toml:25`) and
   `X11Backend` already holds a live display handle (`x11.py:95`) — **no new
   dependency**. **`QShortcut` will not work**: it fires only when our window has
   focus, and Warframe is fullscreen. The app already forces `QT_QPA_PLATFORM=xcb`
   (`ui/platform.py:35-49`), so it is an X11 client even on Wayland.
2. **A key-capture widget in Settings.** None exists — there is no `QShortcut`,
   `QKeySequence` or `QKeySequenceEdit` anywhere in the app. Follow the custom-widget
   precedent in `ui/widgets.py`.
3. **`hotkey` config field.** `Config.save` skips `None` (`config.py:144-153`), so
   unset means unbound, for free.
4. **Manual-only toggle** in Settings: sets the idle rate to zero and suppresses the
   trigger-driven burst.
5. **Degrade honestly.** If the grab fails — another client holds the key, or a
   nested gamescope X server owns the real keyboard input — say so in Settings.
   `ui/platform.py::overlay_limitation` is the precedent for surfacing a platform
   limitation in the UI.

**Hooks.** Touches `config.py`, so **a `DESIGN.md` entry** is required. Add it at
the top (newest first). Draft — fill in the bracketed numbers from Package 0 and
from in-game measurement, since this file's style is measurement-led:

> ## Manual capture is a fallback around the live loop
>
> **Decided:** the hotkey and manual-only mode exist to go *around*
> `LiveRunner._capture_loop`, not to complement it. They are provisional, and stand
> until capture is cheap enough to run continuously without being felt in the game.
>
> **Why.** `X11Backend.grab()` is a VRAM-to-CPU readback, synchronous and
> unaccelerated on most drivers, so every grab inserts a stall into the game's
> render loop — the stall's duration scaling with the area read back and its
> frequency with the capture rate. At 6 Hz over a full 3440x1440 window that was a
> noticeable FPS drop and visibly disturbed v-sync. [Measured: N ms per full-window
> grab, N ms per panel-box grab, achieved rate N Hz against a nominal 6.] Until
> capture costs little enough to run continuously, the honest answer is not to run
> it at all and to let the player say when.
>
> **Consequence.** Manual-only mode is the one configuration with no background
> cost whatsoever, so it is the recommended setting on a machine where capture is
> expensive — which means it has to be discoverable, and the UI has to say *why* it
> exists rather than offering it as a bare toggle.
>
> **What would overturn it.** Capture that costs what it should: the
> xdg-desktop-portal/PipeWire backend landing, or the region grab bringing the
> stall low enough that continuous capture is imperceptible. At that point manual
> mode stops being the recommended path and reverts to what it also is — an
> accessibility feature, and the only route on a machine where `EE.log` does not
> resolve or the trigger never fires.

Version bump required.

**Verify.** `uv run pytest tests/test_ui.py`. On the Linux box: press the hotkey
with Warframe focused and fullscreen, confirm a scan fires and the overlay appears.
Check `~/.local/state/voidsight/voidsight.log` for the rate transitions.

---

## Package 6 — the portal backend (not this round)

The structural fix: the compositor already produced the frame, so nothing forces a
readback of the game's live surface. Declared in four places and implemented in
none — `pyproject.toml:29`, `config.py:122` (`restore_token`), `app/server.py:39`
(`LIVE_BACKENDS`), `README.md:160` — while `cli.py` silently returns `None`,
surfacing as "no capture backend available". At minimum, make it say "not built
yet".

Shape: `CreateSession` -> `SelectSources` -> `Start` -> `OpenPipeWireRemote` over
D-Bus, yielding a PipeWire fd and node id, then GStreamer
`pipewiresrc ! videoconvert ! appsink` via PyGObject. `restore_token` exists so the
permission prompt happens once.

Temper expectations: OBS users report PipeWire capture being slower in *throughput*
than X11 paths — but throughput of a continuous encode is not the same as a stall
imposed on the game, which is what we are removing. **Package 0's numbers are what
make this decidable.** It also sidesteps window discovery entirely, which is what
currently blocks the collaborator's machine.

---

## Final pass, once packages are done

- `uv run pytest && uv run ruff check`.
- Rewrite `docs/roadmap.md` "Next — capture that costs what it should" to match what
  was built and measured.
- In-game during a fissure: FPS and frame-time with voidsight closed, in trickle
  mode, and in manual-only mode. Then crack a relic and confirm the rewards still
  read — the whole point is that the saving costs no scans.
