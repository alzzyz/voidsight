# Where voidsight is going

What is next, and why. Decisions that shaped the code live in
[DESIGN.md](../DESIGN.md); this is the work queue.

Status: the pipeline works end to end on one machine (3440x1440, X11 capture,
real reward screens read at 0.95 confidence). It has not yet been shown to work
on a second.

## Now — the things a player actually hits

**Make a degraded start impossible to miss.** Fixed once: a partial cache used to
be indistinguishable from a complete one, so a feed that 502'd stayed unfetched
for as long as the *other* feed remained valid. That silently removed relic
narrowing and made `Forma Blueprint` unmatchable for seven hours. The retry is in;
what is still missing is making the degraded state visible in the client rather
than a chip in the header, and a periodic re-check while running.

**Finish the Theme/Background correction.** The colour table masks reward text,
and the setting it must match is Warframe's **Background**, not its Theme — a
player who followed our own instructions read nothing all evening. The copy is
corrected; the config key is still called `theme` for compatibility. Decide
whether to rename it with an alias, or leave it and let the documentation carry
the distinction.

**Tell "no panel yet" apart from "cannot read this panel".** `RETRY_BELOW = 0.4`
fires on frames with no reward screen on them at all, triggering a fifteen-mask
theme search on mission HUD. It should be gated on having located a plausible
reward band and failed to read it. A frame with no candidate band is evidence
about the frame, not the theme.

**Work out why the forward watch has never fired.** `"appeared after the trigger"`
appears zero times in a real session log, despite the trigger demonstrably
arriving before the panel animates in. Either `_watch_forward` is being skipped
or it never clears `GOOD_ENOUGH`. Instrument a replay; do not guess.

## Next — capture that costs what it should

`LiveRunner._capture_loop` runs at 6 fps for the whole session whether or not a
reward screen is near. At 3440x1440 that is 14.2 MiB per frame, ~85 MiB/s
sustained, and `RingBuffer(seconds=4.0, max_frames=40)` holds ~567 MiB — of which
~227 MiB is never read, because the cap is a hardcoded constant rather than
`ceil(fps * seconds)`.

The buffer exists to survive a *late* log line. Observed behaviour is the
opposite: the line arrives early, every time. So capture should react to the
trigger — idle at a trickle or nothing, burst on a reward line, watch forward for
the length of the screen (~15s, measured, versus `FORWARD_SECONDS = 8.0` today),
and keep only a small backward window for a moderately late line.

**Measure the trigger-to-panel delta before building it.** That number sizes all
three windows and decides whether idle capture can go to zero. Two fixes stand
alone regardless: derive `max_frames` from `fps * seconds`, and give
`_watch_forward` a scan budget the way the backward pass has `max_frames_scanned`.

## Then — someone else's machine

A collaborator's install starts, sees Warframe running, and finds no window: KDE
Wayland, GE-Proton, sitting in the orbiter. That output predates the probe rewrite
that reports the game's PID from `/proc`, names the X server actually reached, and
lists every window rather than only matching ones. **The next action is a fresh
`voidsight probe --save-to`, not a code change.** Three hypotheses, written so the
output can be read against them:

- *Match the window by `_NET_WM_PID`, not by name.* `capture/x11.py` matches
  `("warframe", "gamescope")` while `capture/probe.py` already knows the PID.
  Confirmed by: the inventory listing a window that is plainly the game under a
  name we do not match.
- *Wrong X server.* Enumerate `/tmp/.X11-unix/`, report which was used, add
  `probe --display`. Confirmed by: PID visible in `/proc`, X server knows nothing.
- *Neither*, in which case the portal backend stops being third and becomes first.

**The portal backend** is declared in four places — the `pyproject` extra,
`config.py`'s documented values, `LIVE_BACKENDS`, and `Config.restore_token` — and
implemented in none. `cli.py` silently returns `None`, surfacing as "no capture
backend available". Say "not built yet" now; build it after. It sidesteps window
discovery entirely, which is why it is the fallback for any machine the two
hypotheses above do not explain.

**`voidsight report`** — one pasteable file: session, game, window inventory,
config, versions, log tail, `EE.log` freshness. Every remote round-trip currently
costs a conversation reconstructing what the other machine looks like.

## Use the reward name the log already gives us

`EE.log` names one of the four rewards on every crack — your own roll, as a Lotus
path in exactly the `Part.game_ref` format the catalog already stores. Nothing in
the codebase reads it. Four uses:

- score OCR accuracy on every crack, with no test fixtures at all;
- anchor panel location on a string known to be on screen, instead of proposing
  candidates and verifying by reading;
- show the player *their* drop's price when OCR fails entirely, where today they
  get nothing;
- confirm the mask is right without a fifteen-theme search.

Caveat to carry: verified on six cracks, all hosted by the same machine. Whether a
pure client logs its own roll is untested, and the whole idea depends on it.

## Behave like an installed application

**Identity first** — there is no logo, no app icon, no `Icon=` in the autostart
entry, no `setWindowIcon`. It blocks the two below.

**A tray icon.** Greenfield, and the missing half of `--wait-for-game`, which
hides the window with no way to bring it back. The icon can carry state — waiting,
game up, capturing, last scan failed — which is a standing answer to the "is it
even running?" problem the troubleshooting section exists for. Check
`quitOnLastWindowClosed`, which is never set, so hiding the last visible window
may quit the app outright. Fall back gracefully where there is no tray.

**Autostart worth configuring.** Verify the entry rather than only writing it:
`executable()` resolves once, so a moved venv breaks it silently. Fix
`find_steam_config()`, which returns the *first* Steam account's launch options
and so can report another account's as Warframe's. Then offer real choices —
start hidden, show when the game appears, quit when it exits, overlay on.

## Tell the player something the price does not

**Mastery.** Warframe's public profile endpoint returns XP per item and the
account id is in `EE.log`, so this needs no credentials and nothing unofficial.
The join exists already: `Part.set_name` and `Part.game_ref` walk a reward to the
item it builds. Surfaces as a third state on the reward card — *mastered, sell it*
versus *needed* — and as the Mastery tab the README advertises and `ui/header.py`
does not have.

**Owned counts off the frame.** The game prints "2 Owned" under each reward when
Item Labels is on, and `vision/locate.py` already identifies that band as one it
deliberately rejects. Reading it instead costs no new infrastructure.

**Inventory.** Persist `SessionLedger` first — it is already Qt-free, thread-safe
and documented as awaiting another source; it is just capped at 40 and in memory.
Persisting it turns confirmations the player already makes into an owned-parts
history. Mission-results OCR comes after. See [inventory.md](inventory.md) for why
the account API is not on this list.

## Design

- **Identity**: a mark, and an icon set from it.
- **The overlay**: what is actually seen, under a ten-second clock, and currently
  drawn against the client's palette rather than the game's.
- **Mockups for Mastery and Inventory** before they are coded.

## Loose ends

- `tests/fixtures/` does not exist, though the README asks for real reward
  screenshots there. Panel geometry is now verified at 1920x1080 and 3440x1440.
- `KERNEL-CRASH-2026-09-12.md` sits in the tree with a header saying "not
  committed". Commit it as an incident record or ignore it.
- No CI. `pytest` plus `ruff` in an action removes a class of "worked here".
- `ocr_workers` and `panel_region` are TOML-only. `panel_region` belongs there;
  `ocr_workers` arguably belongs in Settings behind the warning it already has.

## Not doing

Carried forward so it is not re-litigated. Reasoning in [DESIGN.md](../DESIGN.md).

- **DE's account API for inventory** — credentials, client impersonation, and
  AlecaFrame's own choice against it.
- **Raising `ocr_workers` by default** — it hardlocked a machine.
- **Editing Steam's launch options** — Steam rewrites that file wholesale.
- **Retry logic around the OCR kernel fault** — the kernel is already dead.
