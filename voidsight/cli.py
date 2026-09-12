"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from voidsight import __version__

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="voidsight", description=__doc__)
    parser.add_argument("--version", action="version", version=f"voidsight {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="do not write ~/.local/state/voidsight/voidsight.log",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    update = sub.add_parser("update-data", help="refresh the item, relic and price feeds")
    update.add_argument(
        "--offline",
        action="store_true",
        help="only report what is already cached",
    )
    update.set_defaults(func=_update_data)

    scan = sub.add_parser("scan", help="read the rewards in a screenshot")
    scan.add_argument("image", type=Path, help="screenshot of the reward screen")
    scan.add_argument("--debug-dir", type=Path, help="write intermediate images here")
    scan.add_argument("--theme", help="pin the Warframe UI theme instead of detecting it")
    scan.add_argument("--ui-scale", type=float, help="Warframe interface scale, if not 1.0")
    scan.add_argument("--relic", help='narrow matching to one relic\'s drops, e.g. "Axi A1"')
    scan.add_argument("--save-theme", action="store_true", help="remember the detected theme")
    scan.set_defaults(func=_scan)

    serve = sub.add_parser("serve", help="run the second-screen web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, help="default 8765, or whatever config says")
    serve.add_argument("--backend", choices=["auto", "x11", "replay"], help="frame source")
    serve.add_argument("--log-path", type=Path, help="path to EE.log (default: auto-discover)")
    serve.add_argument(
        "--no-watch",
        action="store_true",
        help="do not follow EE.log; scan only when asked",
    )
    serve.add_argument("--replay-dir", type=Path, help="directory of screenshots to serve")
    serve.add_argument("--theme", help="pin the Warframe UI theme")
    serve.add_argument("--open", action="store_true", help="open the page in a browser")
    serve.set_defaults(func=_serve)

    probe = sub.add_parser("probe", help="report which capture backends work here")
    probe.add_argument("--window", help="window name to look for (default: Warframe)")
    probe.add_argument("--save-to", type=Path, help="write captured frames here as PNGs")
    probe.set_defaults(func=_probe)

    watch = sub.add_parser("watch", help="follow EE.log and print rewards as they drop")
    watch.add_argument("--backend", choices=["auto", "x11", "replay"], help="frame source")
    watch.add_argument("--replay-dir", type=Path, help="directory of screenshots to serve")
    watch.add_argument("--log-path", type=Path, help="path to EE.log (default: auto-discover)")
    watch.add_argument("--theme", help="pin the Warframe UI theme")
    watch.set_defaults(func=_watch)

    app = sub.add_parser("app", help="run the desktop client")
    app.add_argument("--backend", choices=["auto", "x11", "replay"], help="frame source")
    app.add_argument("--replay-dir", type=Path, help="directory of screenshots to serve")
    app.add_argument("--log-path", type=Path, help="path to EE.log (default: auto-discover)")
    app.add_argument("--theme", help="pin the Warframe UI theme")
    app.add_argument(
        "--no-watch", action="store_true", help="do not follow EE.log; scan only when asked"
    )
    app.add_argument(
        "--overlay",
        action="store_true",
        help="draw reward prices over the game (needs Borderless Fullscreen)",
    )
    app.add_argument(
        "--wait-for-game",
        action="store_true",
        help="start hidden and show the window once Warframe is detected",
    )
    app.set_defaults(func=_app)

    launch = sub.add_parser(
        "launch",
        help="run the client alongside a game command (for Steam launch options)",
    )
    launch.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="the game command, after --. Steam substitutes this as %%command%%",
    )
    launch.set_defaults(func=_launch)

    args = parser.parse_args(argv)
    _setup_logging(verbose=args.verbose, to_file=not args.no_log_file)
    log.debug("voidsight %s: %s", __version__, " ".join(sys.argv[1:]))
    return args.func(args)


def _setup_logging(*, verbose: bool, to_file: bool) -> None:
    """Console logging, plus a log file unless it is turned off.

    The file is what makes a client started by a login hook or a Steam launch
    option debuggable at all: its stderr goes nowhere, so the first live run on
    real hardware left no trace of why it had no capture backend.
    """
    from logging.handlers import RotatingFileHandler

    from voidsight.config import log_file_path

    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    # Idempotent: a second call replaces our handlers rather than doubling every
    # line, and leaves anyone else's (a test runner's, say) alone.
    for existing in list(root.handlers):
        if getattr(existing, "_voidsight", False):
            root.removeHandler(existing)
            existing.close()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    console._voidsight = True
    root.addHandler(console)
    # Third-party debug logging is per-request noise that would bury ours.
    for noisy in ("httpx", "httpcore", "PIL", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not to_file:
        return
    path = log_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    except OSError as exc:  # a read-only home must not stop the app
        log.warning("not logging to %s: %s", path, exc)
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler._voidsight = True
    root.addHandler(handler)


def _load_catalog():
    """The catalog, fetching the feeds if this machine has none yet.

    A first run has an empty cache. Failing with a traceback and telling someone
    to go and run another command is a poor welcome for three HTTP requests we
    can make ourselves — so fetch, and only complain if that cannot be done.
    Returns None when there is nothing usable, having already explained why.
    """
    import httpx

    from voidsight.data import catalog as catalog_module

    try:
        cached = catalog_module.load(offline=True)
    except FileNotFoundError:
        cached = None

    if cached is not None and not cached.degraded:
        return cached

    if cached is not None:
        # A *partial* cache used to be indistinguishable from a complete one:
        # `load(offline=True)` succeeds as soon as any one feed is present, so a
        # host that was down when the cache was written stayed unread for as long
        # as the other feed remained valid. That cost an evening of unreadable
        # scans — no relic tables meant no match narrowing, and a missing
        # `Forma Blueprint` (untradeable, so absent from warframe.market) capped
        # confidence below the threshold that accepts a reading at all. So retry
        # the missing feeds, and fall back to what we had if they are still down.
        print(
            f"Cached data is missing {', '.join(cached.missing)}; retrying…",
            file=sys.stderr,
        )
        try:
            return catalog_module.load(refresh=True)
        except (httpx.HTTPError, OSError) as exc:
            log.debug("retry of %s failed: %s", ", ".join(cached.missing), exc)
            return cached

    print("No item data cached yet; fetching it now…", file=sys.stderr)
    try:
        catalog = catalog_module.load(refresh=True)
    except (httpx.HTTPError, OSError) as exc:
        print(
            f"could not download the item data: {exc}\n"
            "voidsight needs one online run to fetch item names, relic tables and "
            "prices. Connect and try again, or run `voidsight update-data`.",
            file=sys.stderr,
        )
        return None
    print(f"Fetched {len(catalog.parts)} items and {len(catalog.relics)} relics.", file=sys.stderr)
    return catalog


def _warn_if_degraded(catalog) -> None:
    if not getattr(catalog, "degraded", False):
        return
    print(
        f"Running without {', '.join(catalog.missing)} (that host is unreachable). "
        "Reward reading and live prices work; relic drop tables, vaulted flags and "
        "average prices are unavailable until it returns.",
        file=sys.stderr,
    )


def _update_data(args: argparse.Namespace) -> int:
    from voidsight.data import catalog as catalog_module
    from voidsight.data import sources

    cat = catalog_module.load(refresh=not args.offline, offline=args.offline)

    tradeable = sum(1 for part in cat.parts.values() if part.tradeable)
    priced = sum(1 for part in cat.parts.values() if part.avg_plat is not None)
    slugged = sum(1 for part in cat.parts.values() if part.slug)
    eras = sorted({relic.era for relic in cat.relics.values()})

    print(f"cache: {sources.cache_dir()}")
    print(
        f"parts: {len(cat.parts)} ({tradeable} tradeable, "
        f"{slugged} with market slug, {priced} priced)"
    )
    print(f"relics: {len(cat.relics)} across {len(eras)} eras ({', '.join(eras)})")
    if cat.degraded:
        print(f"degraded: could not fetch {', '.join(cat.missing)}")
    print(f"match keys: {len(cat.match_keys)}")

    missing = _missing_reward_names(cat)
    if missing:
        print(f"warning: {len(missing)} relic reward names are not in the item table:")
        for name in sorted(missing)[:10]:
            print(f"  - {name}")
    return 0


def _scan(args: argparse.Namespace) -> int:
    import cv2

    from voidsight.config import Config
    from voidsight.vision import ocr, pipeline

    if not args.image.exists():
        print(f"no such file: {args.image}", file=sys.stderr)
        return 2
    if not ocr.TesseractReader.available():
        print("tesseract is not installed; install it and try again", file=sys.stderr)
        return 2

    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        print(f"could not read {args.image} as an image", file=sys.stderr)
        return 2
    frame = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    config = Config.load()
    if args.theme:
        config.theme = args.theme
    if args.ui_scale:
        config.ui_scale = args.ui_scale

    catalog = _load_catalog()
    if catalog is None:
        return 2
    _warn_if_degraded(catalog)
    relic = catalog.relic(args.relic) if args.relic else None
    if args.relic and relic is None:
        print(f"unknown relic {args.relic!r}", file=sys.stderr)
        return 2

    scanner = pipeline.Scanner(catalog, config=config, persist=args.save_theme)
    result = scanner.scan(frame, relic=relic, debug_dir=args.debug_dir)

    theme_name = result.theme.name if result.theme else "?"
    print(f"{frame.shape[1]}x{frame.shape[0]}  theme={theme_name}", end="")
    print(f"  confidence={result.confidence:.2f}" + (f"  relic={relic.name}" if relic else ""))
    if not result.rewards:
        print(f"no rewards found ({result.notes.get('reason', 'unknown reason')})")
        return 1

    for reward in result.rewards:
        part = reward.part
        if part is None:
            print(f"  [{reward.index}] ? {reward.raw_text!r} (best {reward.match.score:.0f})")
            continue
        plat = f"{part.avg_plat:.0f}p avg" if part.avg_plat is not None else "no price"
        flags = "".join(
            [" [vaulted]" if part.vaulted else "", " [untradeable]" if not part.tradeable else ""]
        )
        print(f"  [{reward.index}] {part.display_name}  {plat}  {part.ducats} ducats{flags}")
    return 0 if result.ok else 1


def _probe(args: argparse.Namespace) -> int:
    from voidsight.capture import probe

    print(probe.describe_session())
    print(probe.describe_game())
    print()
    results = probe.probe_all(args.window, args.save_to)
    for result in results:
        print(f"  {result.symbol} {result.backend:8} {result.detail}")
        for extra in result.extras[:12]:
            print(f"          {extra}")

    if any(result.available for result in results):
        working = next(result.backend for result in results if result.available)
        print(f"\nusable backend: {working}")
        return 0

    print()
    if all(result.empty_tree for result in results):
        # An empty tree is not a capture problem, and sending someone off to
        # build a portal backend over it wastes their afternoon.
        print(
            "This X connection sees no windows at all — not Warframe's, not anyone's.\n"
            "That is a connection fault rather than a capture one. Usually one of:\n"
            "  - the game is not actually running (the line above says which)\n"
            "  - DISPLAY names a different X server than the one the game is on;\n"
            "    on a Wayland session the game's XWayland server may be :1, not :0\n"
            "  - the client runs in a sandbox (Flatpak, container) that does not\n"
            "    share the host's X socket with it\n"
            "Compare `echo $DISPLAY` here against the game's own environment:\n"
            "  tr '\\0' '\\n' < /proc/$(pgrep -f Warframe.x64.exe | head -1)/environ \\\n"
            "    | grep -E '^DISPLAY='"
        )
    else:
        print(
            "No backend captured a frame, but this X connection can see windows —\n"
            "so either the game's window is named something we do not match (the\n"
            "list above says what is there), or the compositor refused to let us\n"
            "read it, which is the case the xdg-desktop-portal screencast backend\n"
            "exists for. Report the output above."
        )
    return 1


def _build_backend(args: argparse.Namespace, config) -> object | None:
    """Pick a capture backend from flags, config, then availability."""
    from voidsight.capture.base import CaptureError
    from voidsight.capture.replay import ReplayBackend

    choice = getattr(args, "backend", None) or config.backend
    replay_dir = getattr(args, "replay_dir", None)

    if replay_dir or choice == "replay":
        return ReplayBackend(replay_dir or Path.cwd())
    if choice in ("auto", "x11"):
        from voidsight.capture.x11 import X11Backend

        try:
            return X11Backend()
        except CaptureError as exc:
            if choice == "x11":
                raise
            log.warning("x11 backend unavailable: %s", exc)
            print(f"x11 backend unavailable ({exc})", file=sys.stderr)
            return None
    return None


def _watch(args: argparse.Namespace) -> int:
    from voidsight.app.server import Session
    from voidsight.app.state import ScanStore
    from voidsight.capture.base import CaptureError, RingBuffer
    from voidsight.config import Config
    from voidsight.live import LiveRunner
    from voidsight.pricing.market import MarketClient
    from voidsight.trigger.eelog import find_log
    from voidsight.vision import ocr, pipeline

    if not ocr.TesseractReader.available():
        print("tesseract is not installed; install it and try again", file=sys.stderr)
        return 2

    config = Config.load()
    if args.theme:
        config.theme = args.theme

    log_path = find_log(args.log_path or config.log_path)
    if log_path is None:
        print(
            "could not find EE.log — pass --log-path, or start Warframe once so"
            " Proton creates it",
            file=sys.stderr,
        )
        return 2

    try:
        backend = _build_backend(args, config)
    except CaptureError as exc:
        print(f"capture backend: {exc}", file=sys.stderr)
        return 2
    if backend is None:
        print("no capture backend available; run `voidsight probe`", file=sys.stderr)
        return 2

    catalog = _load_catalog()
    if catalog is None:
        return 2
    _warn_if_degraded(catalog)
    session = Session(
        scanner=pipeline.Scanner(catalog, config=config),
        market=MarketClient(platform=config.platform),
        config=config,
        store=ScanStore(),
        backend=backend,
        buffer=RingBuffer(),
    )
    runner = LiveRunner(session, log_path, on_result=_print_payload)

    print(f"watching {log_path}\ncapturing via {backend.name}; crack a relic (ctrl-c to stop)")
    runner.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print()
    finally:
        runner.stop()
        session.market.close()
        backend.close()
    return 0


def _app(args: argparse.Namespace) -> int:
    from voidsight.app.server import Session
    from voidsight.app.state import ScanStore
    from voidsight.capture.base import CaptureError, RingBuffer
    from voidsight.config import Config
    from voidsight.instance import SingleInstance
    from voidsight.pricing.market import MarketClient
    from voidsight.trigger.eelog import find_log
    from voidsight.vision import ocr, pipeline

    try:
        from voidsight.ui.main import run
    except ImportError:
        print(
            "the desktop client needs PySide6: uv sync --extra desktop",
            file=sys.stderr,
        )
        return 2
    if not ocr.TesseractReader.available():
        print("tesseract is not installed; install it and try again", file=sys.stderr)
        return 2

    config = Config.load()
    if args.theme:
        config.theme = args.theme
    if args.overlay:
        config.overlay = True

    # A missing capture backend is not fatal here: the window still opens, and
    # screenshots can be scanned from the File menu. Only the live capture and
    # automatic triggering are unavailable, which the client says on its face.
    try:
        backend = _build_backend(args, config)
    except CaptureError as exc:
        print(f"capture backend: {exc}", file=sys.stderr)
        backend = None

    catalog = _load_catalog()
    if catalog is None:
        return 2
    _warn_if_degraded(catalog)
    session = Session(
        scanner=pipeline.Scanner(catalog, config=config),
        market=MarketClient(platform=config.platform),
        config=config,
        store=ScanStore(),
        backend=backend,
        buffer=RingBuffer(),
    )
    log_path = None if args.no_watch else find_log(args.log_path or config.log_path)

    # Both startup hooks can be enabled at once; two clients tailing one log and
    # capturing one screen is worse than one, so the second stands aside.
    lock = SingleInstance()
    if not lock.acquire():
        holder = lock.holder_pid()
        where = f" (pid {holder})" if holder else ""
        print(f"a voidsight client is already running{where}", file=sys.stderr)
        return 0

    log.info(
        "client starting: backend=%s, EE.log=%s, theme=%s",
        backend.name if backend else "none",
        log_path or "not found",
        config.theme or "detect",
    )

    def make_backend():
        """Another go at capture, for the window to call once the game is up."""
        try:
            return _build_backend(args, config)
        except CaptureError as exc:
            log.warning("capture backend: %s", exc)
            return None

    try:
        return run(session, log_path, wait_for_game=args.wait_for_game, make_backend=make_backend)
    finally:
        lock.release()
        session.market.close()
        # session.backend, not the local one: the window may have replaced it.
        if session.backend is not None:
            session.backend.close()


def _launch(args: argparse.Namespace) -> int:
    """Start the client, run the game, then stop the client.

    Meant for Steam launch options: `voidsight launch -- %command%`. Steam
    waits on this process, which waits on the game, so the client's lifetime
    matches the session exactly and nothing is left running afterwards.
    """
    import subprocess

    command = [part for part in args.command if part != "--"]
    if not command:
        print(
            "nothing to launch. Use this as a Steam launch option:\n"
            f"  {startup_module().steam_launch_command()}",
            file=sys.stderr,
        )
        return 2

    client = None
    try:
        client = subprocess.Popen(
            [sys.argv[0], "app", "--wait-for-game"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        # The game must start even if we cannot.
        print(f"could not start the voidsight client: {exc}", file=sys.stderr)

    try:
        return subprocess.call(command)
    finally:
        if client is not None and client.poll() is None:
            client.terminate()
            try:
                client.wait(timeout=5)
            except subprocess.TimeoutExpired:
                client.kill()


def startup_module():
    from voidsight import startup

    return startup


def _print_payload(payload: dict) -> None:
    """Print a scan as it happens. Flushed, so piping to a file stays live."""
    best = payload.get("best")
    header = f"[{payload['at'][11:19]}] {payload.get('relic') or 'rewards'}"
    lines = [f"{header}  ({payload['confidence']:.0%} confident)"]
    for index, reward in enumerate(payload["rewards"]):
        marker = "->" if index == best else "  "
        plat = reward["platinum"]
        price = f"{plat:g}p" if plat is not None else "no price"
        ducats = f"{reward['ducats']}d" if reward["ducats"] is not None else ""
        lines.append(f"  {marker} {reward['name']:<36} {price:>9}  {ducats}")
    print("\n".join(lines), flush=True)


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from voidsight.app.server import Session, create_app
    from voidsight.app.state import ScanStore
    from voidsight.capture.base import CaptureError, RingBuffer
    from voidsight.config import Config
    from voidsight.pricing.market import MarketClient
    from voidsight.vision import ocr, pipeline

    if not ocr.TesseractReader.available():
        print("tesseract is not installed; install it and try again", file=sys.stderr)
        return 2

    config = Config.load()
    if args.theme:
        config.theme = args.theme
    if args.backend:
        config.backend = args.backend
    port = args.port or config.port

    # A missing capture backend is not fatal here: the window still opens, and
    # screenshots can be scanned from the File menu. Only the live capture and
    # automatic triggering are unavailable, which the client says on its face.
    try:
        backend = _build_backend(args, config)
    except CaptureError as exc:
        print(f"capture backend: {exc}", file=sys.stderr)
        backend = None

    catalog = _load_catalog()
    if catalog is None:
        return 2
    _warn_if_degraded(catalog)
    session = Session(
        scanner=pipeline.Scanner(catalog, config=config),
        market=MarketClient(platform=config.platform),
        config=config,
        store=ScanStore(),
        backend=backend,
        buffer=RingBuffer(),
    )
    app = create_app(session)

    runner = None
    if not args.no_watch:
        from voidsight.live import LiveRunner
        from voidsight.trigger.eelog import find_log

        log_path = find_log(args.log_path or config.log_path)
        if log_path is None:
            print("EE.log not found; running without automatic triggers", file=sys.stderr)
        else:
            runner = LiveRunner(session, log_path)
            runner.start()
            print(f"watching {log_path}")

    url = f"http://{args.host}:{port}"
    print(f"voidsight serving on {url}  (backend: {backend.name})")
    if args.open:
        import webbrowser

        webbrowser.open(url)
    try:
        uvicorn.run(app, host=args.host, port=port, log_level="warning")
    finally:
        if runner is not None:
            runner.stop()
        session.market.close()
        backend.close()
    return 0


def _missing_reward_names(cat) -> set[str]:
    """Relic rewards with no matching part. Should be empty; a canary on feed drift."""
    return {
        reward.part_name
        for relic in cat.relics.values()
        for reward in relic.rewards
        if cat.lookup(reward.part_name) is None
    }


if __name__ == "__main__":
    sys.exit(main())
