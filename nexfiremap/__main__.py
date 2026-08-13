"""Entry point: ``python -m nexfiremap``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from .config import load_settings


def _already_running(host: str, port: int) -> bool:
    """Best-effort check for whether a NexFiremap instance is already
    serving on this host/port - so running start.bat/start.sh/start.command
    a second time (e.g. by double-clicking it again) opens the existing
    server instead of crashing with a raw 'address already in use'
    traceback. Identifies it via /api/config's "app" field rather than
    just checking the port is open, so an unrelated service on the same
    port is never mistaken for a second NexFiremap."""
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{probe_host}:{port}/api/config"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False
    return isinstance(body, dict) and body.get("app") == "NexFiremap"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nexfiremap",
        description="Local NASA FIRMS fire map server with a 30 day cache.",
    )
    parser.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="port (default 8000)")
    parser.add_argument("--reload", action="store_true", help="auto-reload on changes")
    parser.add_argument("--open", action="store_true", help="open a browser on start")
    parser.add_argument(
        "--log-level", default="info", choices=["debug", "info", "warning", "error"]
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    if host not in {"127.0.0.1", "localhost", "::1"} and not settings.lan_mode:
        print(
            "\n  Refusing non-loopback bind without NEXFIREMAP_LAN_MODE=true.\n"
            "  LAN mode also requires NEXFIREMAP_ADMIN_PASSWORD (12+ characters).\n",
            file=sys.stderr,
        )
        return 2
    if settings.lan_mode and len(settings.admin_password) < 12:
        print("\n  LAN mode requires NEXFIREMAP_ADMIN_PASSWORD with at least 12 characters.\n", file=sys.stderr)
        return 2
    if bool(settings.tls_cert_file) != bool(settings.tls_key_file):
        print("\n  TLS requires both NEXFIREMAP_TLS_CERT_FILE and NEXFIREMAP_TLS_KEY_FILE.\n", file=sys.stderr)
        return 2
    if settings.tls_cert_file and (not settings.tls_cert_file.is_file() or not settings.tls_key_file.is_file()):
        print("\n  TLS certificate or private-key file was not found.\n", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    if not settings.has_map_key:
        print(
            "\n  NexFiremap: no FIRMS map key found.\n"
            "  The map will start, but it cannot download any data yet.\n"
            "  Get a free key at https://firms.modaps.eosdis.nasa.gov/api/map_key/\n"
            "  then put FIRMS_MAP_KEY=your-key in the .env file.\n",
            file=sys.stderr,
        )

    if _already_running(host, port):
        print(f"\n  NexFiremap is already running at http://{host}:{port} - not starting a second copy.\n")
        if args.open:
            webbrowser.open(f"http://{host}:{port}")
        return 0

    scheme = "https" if settings.tls_cert_file else "http"
    if settings.lan_mode:
        print(
            "\n  WARNING: INCIDENT LAN MODE IS ENABLED.\n"
            "  Restrict this server to a trusted incident network, issue named accounts,\n"
            + ("  Direct TLS is active; confirm every client trusts this incident certificate.\n"
               if settings.tls_cert_file else
               "  WARNING: TLS is not configured; do not carry operational data over untrusted links.\n"),
            file=sys.stderr,
        )
    print(f"\n  NexFiremap -> {scheme}://{host}:{port}\n")
    if args.open:
        webbrowser.open(f"{scheme}://{host}:{port}")

    try:
        uvicorn.run(
            "nexfiremap.api:app",
            host=host,
            port=port,
            reload=args.reload,
            log_level=args.log_level,
            ssl_certfile=str(settings.tls_cert_file) if settings.tls_cert_file else None,
            ssl_keyfile=str(settings.tls_key_file) if settings.tls_key_file else None,
        )
    except OSError as exc:
        # A bind failure that reached us directly (uvicorn's own socket
        # helper instead calls sys.exit(1) on bind errors - see below).
        print(
            f"\n  NexFiremap could not bind to http://{host}:{port}: {exc}\n"
            "  Something else is likely using this port. Try:\n"
            f"    python run.py --port {port + 1}\n"
            "  or set NEXFIREMAP_PORT in .env.\n",
            file=sys.stderr,
        )
        return 1
    except SystemExit as exc:
        # uvicorn's Config.bind_socket() logs the OSError itself and calls
        # sys.exit(1) directly rather than raising - catch that so a
        # port-already-in-use re-run of start.bat/.sh/.command still ends
        # with a clear, actionable message instead of just a bare exit(1).
        if exc.code not in (0, None):
            print(
                f"\n  NexFiremap could not bind to http://{host}:{port} "
                "(see the ERROR line above - the port is likely already in use).\n"
                f"  Try:\n"
                f"    python run.py --port {port + 1}\n"
                "  or set NEXFIREMAP_PORT in .env.\n",
                file=sys.stderr,
            )
        return exc.code if isinstance(exc.code, int) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
