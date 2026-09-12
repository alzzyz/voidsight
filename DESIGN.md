# Design decisions

Why voidsight is built the way it is. Newest first.

Each entry says what was decided, why, and what would overturn it. The last part
matters most: a decision recorded without its expiry condition becomes folklore.

Read this before changing anything under `voidsight/vision/`, `voidsight/capture/`,
`voidsight/trigger/`, `live.py`, `session.py` or `config.py` — a push touching those
without touching this file is refused by `.claude/hooks/check-design-change.sh`.

---

## The mask follows Warframe's Background, not its Theme

**Decided:** the colour table voidsight masks with corresponds to Warframe's
**Background** setting. Everything that calls it a "theme" is misleading and is
being corrected.

**Why.** A player running Theme *Zephyr* with Background *Vitruvian* set
`theme = "Zephyr"` — exactly what the README, the config comment and the Settings
field all instructed — and the tool read nothing for an entire evening. Zephyr's
accents are orange and red `((253,132,2), (255,53,0))`; they match the header
chrome (`VOID FISSURE/REWARDS`) and the squadmate name row, which is precisely
what the mask locked onto, OCR'ing `archer ot` and `mega KE`. The reward item
names are drawn on the panel backdrop and carry Vitruvian's golds
`((190,169,102), (245,227,173))`.

Measured on `unread/reward-20260912-210248.png`, 3440x1440:

| pinned setting | result |
|---|---|
| `Zephyr` (the player's Theme) | reads nothing; falls back to a 15-mask search |
| `Vitruvian` (the player's Background) | 4/4 identified, confidence 0.95 |

**What would overturn it.** A player whose Theme and Background differ and for
whom the *Theme* value is the one that reads. That would mean the relationship is
not "background wins" but something conditional — likely which UI element the
band detector happened to select. Two more mismatched configurations would settle
it; we have one data point and it is consistent, not conclusive.

**Consequence.** The user-facing name should be "Background", with the old term
kept as an alias so existing configs keep working. The colour table itself is
correct and is not being changed — it comes from WFInfo (see NOTICE), where the
same values are also labelled themes.

## A partial data cache is treated as a complete one

**Decided:** this is a defect, recorded here because the fix changes behaviour
that looks deliberate.

**Why.** `_catalog_or_explain` (`cli.py`) calls `catalog.load(offline=True)` and
returns as soon as it succeeds. `load` succeeds when *any* feed is cached. On
2026-09-12 `api.warframestat.us` returned 502 while `market_items.json` cached
fine, so for seven hours every start built a catalog with **0 relics** and 584
parts instead of 773 and 597 — and never retried, because a cached market feed
made it look like a complete offline load.

The damage was not obvious. Missing relic tables silently disable the narrowing
in `match.py` (`CONSTRAINED_THRESHOLD = 60` against six candidates never engages;
everything runs open-set at `OPEN_SET_THRESHOLD = 80` against ~600). And
`Forma Blueprint` — untradeable, therefore absent from warframe.market, present
only in WFInfo's `filtered_items` — was unmatchable. On the frame above that
capped confidence at 0.48 against a true value of 0.95, because
`ScanResult.confidence` averages unmatched columns in as zero and
`ScanResult.ok` requires *every* column to match.

That `ok` is load-bearing well beyond its name: it gates the early exit in
`scan()`, the `learned` theme write-back, and `_Search.consider`'s stopping
condition. One untradeable reward in a screen therefore disabled theme learning
and forced the candidate search to run to exhaustion on every frame.

**What would overturn it.** Nothing about the diagnosis; it is measured. But the
*fix* is a judgement call — retry missing feeds on start with a short timeout, or
surface the degradation loudly enough that a player fixes it themselves. The
former risks blocking startup on a dead host, which is the failure `offline=True`
existed to avoid in the first place.

## OCR exists to read the other players' rewards, not your own

**Decided:** the OCR pipeline's purpose is reading the three rewards `EE.log`
does not name.

**Why.** `docs/inventory.md` claimed the log "does not contain item names" and
that "that absence is the entire reason the OCR pipeline exists". That is wrong,
and the correction changes what the pipeline is *for*. The log names exactly one
item per crack — yours:

```
5478.855  VoidProjections: 55b8dd… gets reward /Lotus/…/YareliPrimeSystemsBlueprint
5478.855  VoidProjections: Host got reward info from 55b8dd…    ← same ms: rolled locally
5478.891  VoidProjections: Host got reward info from 580bbb…    ← +36ms, never named
5478.932  VoidProjections: Host got reward info from 6a42a4…    ← +77ms, never named
5478.946  VoidProjections: Host got reward info from 59eb25…    ← +91ms, never named
```

Six reward events in a 10 MB log, six named items, always the same player id,
always the one with zero network latency — your own roll, computed locally. The
other three arrive over the network as opaque reward info.

So the screen poses a question the log cannot answer: *is someone else's drop
worth more than mine?* That is what OCR is for. The log also never records which
reward you took, so it cannot feed inventory either.

**Unused consequence.** Nothing in the codebase reads that line. It is free
ground truth for one of the four names on every crack — an accuracy signal that
needs no test fixtures, and an anchor for locating the panel.

**What would overturn it.** The sample is six cracks, all of them **hosted** by
this machine. If a pure client does not log its own roll, the line is useless for
anyone not hosting, and the "free ground truth" idea collapses.

## The reward trigger fires before the panel is on screen

**Decided:** frames are searched backwards through a ring buffer *and* forwards
as they arrive, because the trigger can land on either side of the panel.

**Why.** Wine buffers Warframe's writes to `EE.log`, so the line can arrive after
the screen is up — the failure existing Linux tools document. But Warframe writes
`Got rewards` when the client *receives* the rewards, before the panel has
animated in, so the line also arrives early. Observed: a first trigger finds
nothing and a second, ~4s later, reads the same screen. `EE.log` shows the panel
then stays up about fifteen seconds (`Got rewards` -> `Selection countdown done`).

**What would overturn it.** Measurement. The backward window (`LOOKBACK_SECONDS
= 4.0`) and forward window (`FORWARD_SECONDS = 8.0`) are guesses, and the forward
watch's success message has never once appeared in a real log. If the trigger
reliably arrives *early*, the backward buffer is insuring against a failure that
does not happen — and it costs ~85 MiB/s of capture and ~567 MiB resident at
3440x1440, continuously, for the whole session.

## `ocr_workers` defaults to 1

**Decided:** one tesseract process per scan, with the parallel path kept but off.

**Why.** Four concurrent tesseract processes faulting in the same file-backed
mappings triggered a kernel general protection fault in `filemap_map_pages` on
7.2.4-1-cachyos, which hardlocked the machine with no clean shutdown. Four
Oopses, one per worker, with the identical bad pointer across four different page
tables — corruption in the shared page-cache entry, not in any one process. Full
analysis in `KERNEL-CRASH-2026-09-12.md`.

No userspace program can legally fault the kernel and none can work around one
except by not provoking it. Raising this to 4 shortens a scan by ~40%.

**What would overturn it.** A kernel seen surviving the workload. This is a
workaround for someone else's bug, not a design preference.

## Steam's launch options are read, never written

**Decided:** voidsight generates the launch-option string and reports what Steam
currently has, but will not edit `localconfig.vdf`.

**Why.** Steam rewrites that file wholesale when it exits, so a change made behind
its back is discarded at best and damages every other game's launch options at
worst. The XDG autostart entry is ours to write, so that one is a toggle.

**What would overturn it.** A supported Steam API for per-game launch options.

## Qt rather than a web view

**Decided:** PySide6 for the desktop client, with a FastAPI web UI alongside
consuming the identical payload.

**Why.** The in-game overlay needs a transparent, always-on-top, click-through
window over a fullscreen game. A browser cannot do that, and the overlay has to
share a toolkit with the client rather than being a second application.

**What would overturn it.** Dropping the overlay, which is the only requirement
the web UI cannot meet.

## The client forces Qt's `xcb` plugin on Wayland

**Decided:** `ui/platform.py::prefer_xwayland()` sets `QT_QPA_PLATFORM=xcb` on a
Wayland session unless the user has set it themselves.

**Why.** A Wayland client cannot place its own windows — `move()` is a request the
compositor may ignore, and KWin does — so a corner-anchored overlay is impossible
natively without layer-shell, which Qt reaches only through a C++ library with no
Python bindings. Proton already runs the game under XWayland, so joining that X
server puts the overlay where the game is.

**What would overturn it.** Python bindings for layer-shell, or Qt gaining native
support for positioning.

## Inventory via DE's account API is deferred

**Decided:** manual confirmation now; read the mission-results screen when this is
revisited; do not use DE's mobile companion API.

**Why.** Summarised from [docs/inventory.md](docs/inventory.md), which has the
full reasoning, the verified request shapes, and the costs — credentials in the
keyring, impersonating DE's Android client, and breakage on DE's schedule. The
strongest argument is that AlecaFrame had every incentive to use that API and
chose Overwolf's blessed access instead.

**What would overturn it.** DE documenting the endpoint, or shipping a real one.
