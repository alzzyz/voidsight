# Knowing what you actually collected

**Status: deferred.** Quick Sell asks you to confirm what you took. This note
records why, and what the alternatives cost, so the question does not have to be
researched again.

## What the game gives us for free

`EE.log` records that a reward screen appeared, and names **one** of the four
rewards: your own. Each player's relic rolls an item, all four are shown, and you
pick one — and the log names only the one your own client rolled, because it is
computed locally:

```
5478.855  VoidProjections: 55b8dd… gets reward /Lotus/…/YareliPrimeSystemsBlueprint
5478.855  VoidProjections: Host got reward info from 55b8dd…    ← same ms: local
5478.891  VoidProjections: Host got reward info from 580bbb…    ← +36ms, unnamed
5478.932  VoidProjections: Host got reward info from 6a42a4…    ← +77ms, unnamed
5478.946  VoidProjections: Host got reward info from 59eb25…    ← +91ms, unnamed
```

The other three arrive over the network as opaque reward info. That is why the
OCR pipeline exists: not because the log names nothing, but because it cannot
answer the question the screen actually poses — *is someone else's drop worth
more than mine?*

> An earlier version of this note claimed the log contained no item names at all,
> and that this absence was "the entire reason the OCR pipeline exists". That was
> wrong. See [DESIGN.md](../DESIGN.md) for the evidence and the caveat (the
> sample is six cracks, all of them hosted by the same machine).

For *inventory* the conclusion is unchanged, and for a different reason: the log
never records which of the four you **took**. `CommitInventoryChangesToDB`
commits silently, naming nothing. So there is still no free source for "what did
I pick up". Everything below is a way of buying one.

## Option 1 — read the mission-results screen

The end-of-mission summary lists what you earned. Reading it is the same
technique as `vision/`, pointed at a different screen: new crop geometry, denser
text, possibly scrolling.

* No credentials, no accounts, nothing unsanctioned. Stays inside the boundary
  DE has tolerated for years.
* Only sees what the screen shows, and inherits every OCR failure mode.

## Option 2 — the official account API, with an inventory delta

DE's mobile companion app talks to an undocumented API that does expose
inventory. Verified reachable, mechanism confirmed from
[cephalon-sofis/warframe_api](https://github.com/cephalon-sofis/warframe_api):

```
POST https://api.warframe.com/API/PHP/login.php
     {email, password: whirlpool(password), time, date, mobile: true, appVersion}
     headers: X-Titanium-Id (the Android app's id), X-Requested-With
  -> {id: accountId, Nonce: nonce}

POST https://api.warframe.com/API/PHP/inventory.php
     {mobile: true, accountId, nonce}
```

The delta design works, and the piece that makes it clean is already in our
data: inventory is keyed by Lotus paths like
`/Lotus/Types/Recipes/Weapons/WeaponParts/PrimeNikanaHandle`, and
warframe.market's `/v2/items` gives every item a `gameRef` in exactly that
format. Snapshot before, fetch after, diff, and you have named and priced items
with no OCR at all — two requests per mission.

What it costs:

* **Credentials.** The password is whirlpool-hashed client-side, so plaintext
  never leaves the machine, but the hash is password-equivalent to this API and
  would have to live in the system keyring.
* **Impersonating DE's client.** The request only works with the Android app's
  hardcoded `X-Titanium-Id` and a matching `appVersion`. This is the part that
  reads as unsanctioned rather than merely undocumented.
* **It breaks on DE's schedule.** A 400 with "version out of date" kills it
  until the version string is updated.
* **2FA accounts** presumably cannot log in this way at all.
* The library's own README warns that "use of this code may result in your Tenno
  status being revoked".

One risk that does *not* apply: `mobile: true` is documented in that client as
preventing session clobbering, so an API login does not kick you out of the game.

## Why deferred

The strongest evidence is AlecaFrame's own choice. It had every incentive to use
this API — available on Windows, exactly the data it wanted — and went through
Overwolf's blessed game-events access instead. The project most invested in this
feature decided the account API was not worth it.

Manual confirmation costs one tap and cannot get anyone banned. Revisit when the
Linux fundamentals — capture, log triggering, overlay — are verified on real
hardware, and Option 1 is the one to build first.
