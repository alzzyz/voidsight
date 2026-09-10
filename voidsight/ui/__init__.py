"""The desktop client.

Qt rather than a web view because the same toolkit has to carry the in-game
overlay later: a transparent, always-on-top, click-through window over a
fullscreen game is something Qt does and a browser does not.

Nothing here holds application logic. Views render the same payload dictionary
the web UI consumes (see `voidsight.app.state.payload_for`), so the two front
ends cannot drift apart, and neither is load-bearing.
"""
