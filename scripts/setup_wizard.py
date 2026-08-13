#!/usr/bin/env python3
"""NexFiremap setup wizard - cross-platform (Windows / macOS / Linux).

Walks through everything needed to go from "just cloned this" to "server is
running": creates a virtual environment, installs dependencies (falling
back to installing package-by-package if the bulk install hits a snag, so
one awkward platform-specific wheel doesn't block everything else),
configures `.env` (FIRMS map key + optional tile-fetch contact info), and
tests reachability of every free data source this project talks to -
before you ever start the server.

Run directly:      python scripts/setup_wizard.py
Or via a launcher:  install.bat (Windows) / install.sh / install.command
                    (macOS/Linux) - these just locate a Python interpreter
                    and run this script; all the real logic lives here so
                    it only has to be written once.

Everything here is stdlib-only until dependencies are installed, so it
works even on a completely bare Python install.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = ROOT / ".venv"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
REQUIREMENTS = ROOT / "requirements.txt"

MIN_PYTHON = (3, 10)

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_GREEN, _YELLOW, _RED, _BLUE, _BOLD, _RESET = (
    ("\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m", "\x1b[1m", "\x1b[0m")
    if _USE_COLOR
    else ("", "", "", "", "", "")
)


# ------------------------------------------------------------------ output


def banner() -> None:
    print(f"\n{_BOLD}{_BLUE}NexFiremap setup wizard{_RESET}")
    print("Local NASA FIRMS fire map with a 30-day cache and probabilistic")
    print("fire-event reconstruction. This will take a few minutes.\n")


def section(title: str) -> None:
    print(f"\n{_BOLD}== {title} =={_RESET}")


def ok(msg: str) -> None:
    print(f"  {_GREEN}[ok]{_RESET}   {msg}")


def warn(msg: str) -> None:
    print(f"  {_YELLOW}[warn]{_RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {_RED}[fail]{_RESET} {msg}")


def ask(prompt: str, default: str = "") -> str:
    """Prompts for a free-text answer, returning ``default`` on an empty
    reply or non-interactive stdin (EOF)."""
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{prompt}{suffix}: ")
    except EOFError:
        # Non-interactive stdin (e.g. run from some automation) - take the
        # default rather than hang.
        raw = ""
    # A stray leading BOM has been observed when stdin is piped through
    # certain Windows shells (PowerShell 5.1 -> cmd.exe defaults to a
    # BOM-prefixed encoding for piped native-process input) - strip it so
    # it never silently becomes part of a real answer like the FIRMS key.
    raw = raw.lstrip("\ufeff").strip()
    return raw or default


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """Yes/no prompt; any reply starting with "y" counts as yes, anything
    else (including a blank reply or non-interactive EOF) falls through to
    ``default``."""
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"{prompt} [{hint}]: ").strip().lower()
    except EOFError:
        raw = ""
    if not raw:
        return default
    return raw.startswith("y")


def mask_key(key: str) -> str:
    """Masks a secret for display (e.g. an existing FIRMS key being
    re-confirmed) - short values are fully starred out rather than showing
    first/last 4 characters, since 4+4 could reveal most or all of a very
    short key."""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "..." + key[-4:]


# --------------------------------------------------------------- python/venv


def check_python_version() -> bool:
    if sys.version_info < MIN_PYTHON:
        fail(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required "
            f"(found {platform.python_version()})."
        )
        print("  Get a current Python from https://www.python.org/downloads/")
        return False
    ok(f"Python {platform.python_version()} ({sys.executable})")
    return True


def in_virtualenv() -> bool:
    """True when the interpreter running this script is itself already
    inside a virtualenv - the standard ``sys.prefix != sys.base_prefix``
    check (falls back to comparing prefix to itself, i.e. False, on the
    rare interpreter without ``base_prefix`` at all)."""
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def venv_python_path(venv_dir: Path) -> Path:
    """Path to the interpreter inside a venv - the layout differs between
    Windows (Scripts/python.exe) and POSIX (bin/python)."""
    if platform.system() == "Windows":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def setup_venv() -> Path:
    """Returns a usable Python interpreter path for installing dependencies
    into: reuses the current interpreter if already inside a venv, reuses an
    existing ``.venv`` if one was already created by a prior run, or creates
    a fresh one - falling back to the current interpreter (not failing
    outright) if venv creation itself doesn't work, so setup can still limp
    forward on a constrained environment."""
    if in_virtualenv():
        ok(f"Already running inside a virtual environment ({sys.prefix}) - using it.")
        return Path(sys.executable)

    py = venv_python_path(VENV_DIR)
    if py.is_file():
        ok(f"Reusing the existing virtual environment at {VENV_DIR}")
        return py

    print(f"  Creating a virtual environment at {VENV_DIR} ...")
    result = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if result.returncode != 0 or not py.is_file():
        warn("Could not create a virtual environment - continuing with this Python directly.")
        return Path(sys.executable)
    ok("Virtual environment created.")
    return py


# --------------------------------------------------------------- dependencies


def _requirement_lines() -> list[str]:
    """requirements.txt entries, stripped of comments/blank lines - used
    only for the package-by-package fallback install path below."""
    if not REQUIREMENTS.is_file():
        return []
    lines = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def install_requirements(python_exe: Path) -> dict[str, str]:
    """Installs requirements.txt as a whole first; on failure, falls back
    to installing package-by-package so one problem spec (rasterio has the
    most Windows install friction of the bunch) doesn't block everything
    else. Returns {requirement: error} for anything that never installed."""
    print("  Upgrading pip ...")
    subprocess.run(
        [str(python_exe), "-m", "pip", "install", "--upgrade", "pip"],
        capture_output=True,
        text=True,
    )

    print("  Installing dependencies (can take a few minutes on first run) ...")
    bulk = subprocess.run(
        [str(python_exe), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        capture_output=True,
        text=True,
    )
    if bulk.returncode == 0:
        ok("All dependencies installed.")
        return {}

    warn("Bulk install hit a problem - retrying package by package so a single")
    warn("failure (e.g. a missing platform wheel) doesn't block everything else:")
    failures: dict[str, str] = {}
    for requirement in _requirement_lines():
        result = subprocess.run(
            [str(python_exe), "-m", "pip", "install", requirement],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            ok(requirement)
        else:
            warn(f"{requirement} - FAILED")
            failures[requirement] = (result.stderr or result.stdout)[-500:]
    return failures


# --------------------------------------------------------------------- .env


def _read_lines(path: Path) -> list[str]:
    """Reads a file as lines, or ``[]`` if it doesn't exist yet - used for
    both .env and .env.example so a first-time setup with no .env falls
    back to the example file's structure/comments."""
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def _existing_env_values() -> dict[str, str]:
    """Parses whatever's currently in .env into a flat key/value map, so
    `configure_env` can offer existing values as defaults instead of
    prompting from scratch on a re-run."""
    values: dict[str, str] = {}
    for raw in _read_lines(ENV_FILE):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _set_env_var(lines: list[str], key: str, value: str) -> list[str]:
    """Set key=value in-place, whether the line is currently active or
    commented out as an example; appends a new line if not present at all.
    Idempotent even if a stray duplicate (e.g. both a commented example and
    an active override for the same key) already exists: only the first
    match is rewritten, later matches are dropped rather than duplicated."""
    pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}\s*=.*$")
    new_line = f"{key}={value}"
    out: list[str] = []
    found = False
    for line in lines:
        if pattern.match(line):
            if not found:
                out.append(new_line)
                found = True
            # else: duplicate match for the same key - drop it
        else:
            out.append(line)
    if not found:
        out.append(new_line)
    return out


def configure_env() -> dict[str, str]:
    """Interactively collects the values `write_env` needs (FIRMS key,
    optional tile-fetch contact email), pre-filling from any existing .env
    so re-running the wizard doesn't force re-entering everything."""
    existing = _existing_env_values()

    print("A free FIRMS map key is required to download fire data:")
    print(f"  {_BLUE}https://firms.modaps.eosdis.nasa.gov/api/map_key/{_RESET}")
    current_key = existing.get("FIRMS_MAP_KEY", "")
    if current_key:
        if ask_yes_no(f"Keep the existing FIRMS_MAP_KEY ({mask_key(current_key)})?", default=True):
            new_key = current_key
        else:
            new_key = ask("Paste your FIRMS map key")
    else:
        new_key = ask("Paste your FIRMS map key (leave blank to configure later)")

    print(
        "\nOptional: a contact address added to the map-tile fetch User-Agent, "
        "per OSM's tile usage policy - only useful for heavy use."
    )
    contact = ask("Contact email (leave blank to skip)", default=existing.get("NEXFIREMAP_CONTACT", ""))

    return {"FIRMS_MAP_KEY": new_key, "NEXFIREMAP_CONTACT": contact}


def write_env(values: dict[str, str]) -> None:
    lines = _read_lines(ENV_FILE) or _read_lines(ENV_EXAMPLE)
    for key, value in values.items():
        if value:  # a blank answer means "leave whatever's already there"
            lines = _set_env_var(lines, key, value)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok(f"Wrote {ENV_FILE}")


# ------------------------------------------------------------- connectivity

CONNECTIVITY_TARGETS: list[tuple[str, str]] = [
    ("NASA FIRMS", "https://firms.modaps.eosdis.nasa.gov/"),
    ("Celestrak (satellite orbits)", "https://celestrak.org/NORAD/elements/"),
    (
        "Microsoft Planetary Computer (imagery/terrain)",
        "https://planetarycomputer.microsoft.com/api/stac/v1",
    ),
    (
        "Open-Meteo (weather)",
        "https://archive-api.open-meteo.com/v1/archive"
        "?latitude=0&longitude=0&start_date=2024-01-01&end_date=2024-01-01&hourly=temperature_2m",
    ),
    ("OpenStreetMap tiles", "https://tile.openstreetmap.org/0/0/0.png"),
    # Reachability only (no credentials needed for this) - EUMETSAT_CONSUMER_KEY/
    # SECRET, if configured, get a deeper check in run_connectivity_tests below,
    # the same way FIRMS_MAP_KEY does via check_firms_key.
    ("EUMETSAT Data Store (optional, MTG/FCI corroboration)", "https://api.eumetsat.int/token"),
]


def _fetch(url: str, timeout: float = 8.0) -> tuple[int, bytes]:
    """Bare urllib GET with an identifying User-Agent - stdlib only, since
    this script must run before dependencies (including httpx) are
    installed."""
    req = urllib.request.Request(url, headers={"User-Agent": "NexFiremap-Setup/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def check_url(url: str, timeout: float = 8.0) -> tuple[bool, str]:
    try:
        status, _ = _fetch(url, timeout)
        return True, f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        # Some endpoints 404/405 a bare GET but that still proves the host
        # is reachable and answering - only 5xx/timeouts count as failure.
        if exc.code < 500:
            return True, f"HTTP {exc.code} (reachable)"
        return False, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError) or "certificate" in str(reason).lower():
            # NexFiremap's own HTTP client (httpx) verifies against the
            # actively-maintained `certifi` CA bundle, not this machine's OS
            # trust store - so a stale local root/intermediate cert here
            # doesn't mean the app itself can't reach this host. Confirmed
            # by cross-checking the same URL with httpx during development.
            return True, "reachable (this machine's CA trust store looks stale, but the app uses its own bundled cert list, unaffected)"
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 - report any other failure reason
        return False, str(exc)


def check_firms_key(key: str) -> tuple[bool, str]:
    if not key:
        return False, "no key configured yet"
    encoded_key = urllib.parse.quote(key, safe="")
    url = f"https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY={encoded_key}"
    try:
        _, body_bytes = _fetch(url)
    except urllib.error.HTTPError as exc:
        # FIRMS answers a bad key with e.g. HTTP 403 and a plain-text
        # explanation in the body, not a 200 - read it rather than just
        # reporting the bare status code.
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return False, detail[:200] if detail else f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    body = body_bytes.decode("utf-8", errors="replace")
    if "invalid" in body.lower():
        return False, "FIRMS says this key is invalid"
    try:
        data = json.loads(body)
        inner = data.get(key, data) if isinstance(data, dict) else {}
        limit = inner.get("transaction_limit")
        used = inner.get("current_transactions")
        if limit is not None:
            return True, f"valid - {used}/{limit} transactions used this window"
    except (json.JSONDecodeError, AttributeError):
        pass
    return True, "key accepted"


def check_eumetsat_credentials(key: str, secret: str) -> tuple[bool, str]:
    """Attempts the real OAuth2 client_credentials exchange
    (nexfiremap/eumetsat.py's `_get_access_token` does the same thing at
    runtime) so a misconfigured/typo'd EUMETSAT key+secret is caught here,
    at setup time, rather than only surfacing as a job failure the first
    time the "EUMETSAT confirmation" layer is used."""
    if not key or not secret:
        return False, "not configured (optional - see README's EUMETSAT section)"
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    req = urllib.request.Request(
        "https://api.eumetsat.int/token",
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "NexFiremap-Setup/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return False, (detail[:200] if detail else f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if not body.get("access_token"):
        return False, f"token endpoint accepted the request but returned no access_token: {body}"
    return True, "credentials accepted, token issued"


def run_connectivity_tests(env_values: dict[str, str]) -> dict[str, bool]:
    """Probes every free data source this project talks to, plus the
    FIRMS key (and EUMETSAT credentials, if configured) - all before the
    server ever starts, so connectivity/credential problems surface here
    with an actionable message rather than as a mysterious job failure
    later."""
    section("Testing connectivity")
    results: dict[str, bool] = {}
    for name, url in CONNECTIVITY_TARGETS:
        reachable, detail = check_url(url)
        results[name] = reachable
        (ok if reachable else warn)(f"{name}: {detail}")

    key_ok, key_detail = check_firms_key(env_values.get("FIRMS_MAP_KEY", ""))
    results["FIRMS map key"] = key_ok
    (ok if key_ok else warn)(f"FIRMS map key: {key_detail}")

    # Not part of configure_env()'s prompts (EUMETSAT is opt-in, set up
    # manually per the README) - read straight from .env instead.
    existing = _existing_env_values()
    eumetsat_key = existing.get("EUMETSAT_CONSUMER_KEY", "")
    eumetsat_secret = existing.get("EUMETSAT_CONSUMER_SECRET", "")
    if eumetsat_key or eumetsat_secret:
        eumetsat_ok, eumetsat_detail = check_eumetsat_credentials(eumetsat_key, eumetsat_secret)
        results["EUMETSAT credentials"] = eumetsat_ok
        (ok if eumetsat_ok else warn)(f"EUMETSAT credentials: {eumetsat_detail}")
    return results


# ------------------------------------------------------------------- extras


def chmod_launchers() -> None:
    """Best-effort +x on the shell launchers so double-clicking works on
    macOS/Linux without a manual chmod first. No-op on Windows."""
    if platform.system() == "Windows":
        return
    for name in ("start.sh", "start.command", "install.sh", "install.command"):
        path = ROOT / name
        if path.is_file():
            try:
                path.chmod(path.stat().st_mode | 0o111)
            except OSError:
                pass


def print_summary(dep_failures: dict[str, str], connectivity: dict[str, bool]) -> None:
    """Final human-readable rollup of everything checked above - the
    "can I actually use this now" answer, kept separate from the individual
    check functions so it can present install/network/key issues in one
    place rather than scattered across the earlier sections' own output."""
    section("Summary")
    if dep_failures:
        warn(f"{len(dep_failures)} package(s) failed to install:")
        for pkg in dep_failures:
            print(f"    - {pkg}")
        print(
            "  The core fire map still works - affected features will report\n"
            "  themselves as unavailable via /api/config rather than crashing."
        )
    else:
        ok("All dependencies installed.")

    # The FIRMS key is a separate concern from network reachability - don't
    # lump "not configured yet" in with "couldn't reach the host".
    network = {k: v for k, v in connectivity.items() if k != "FIRMS map key"}
    unreachable = [name for name, reachable in network.items() if not reachable]
    if unreachable:
        warn("Not reachable right now: " + ", ".join(unreachable))
        print("  (Firewall/proxy/offline - the app will retry these at runtime.)")
    else:
        ok("Every data source is reachable.")

    if not connectivity.get("FIRMS map key"):
        warn("No working FIRMS map key yet - fire data can't be downloaded until one is set.")
        print("  Get one free at https://firms.modaps.eosdis.nasa.gov/api/map_key/ , then re-run")
        print("  this wizard or edit FIRMS_MAP_KEY in .env directly.")

    launcher = "start.bat" if platform.system() == "Windows" else "./start.sh"
    print(f"\n  Launch anytime with: {_BOLD}{launcher}{_RESET}  (or: python run.py --open)\n")


def launch_server(python_exe: Path) -> int:
    """Hands off to run.py using the (possibly freshly created) venv
    interpreter, treating Ctrl+C as a normal stop rather than an error exit
    code."""
    print(f"\n{_BOLD}Starting NexFiremap ...{_RESET} (Ctrl+C to stop)\n")
    try:
        return subprocess.call([str(python_exe), str(ROOT / "run.py"), "--open"])
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------- main


def main() -> int:
    """Runs the whole wizard end to end, in the order a fresh clone needs:
    verify the Python version, get a virtualenv, install dependencies,
    configure .env, test connectivity, then optionally launch the server -
    each section prints its own progress via `section`/`ok`/`warn`/`fail`
    as it goes."""
    banner()

    section("Checking Python")
    if not check_python_version():
        return 1

    section("Setting up the virtual environment")
    python_exe = setup_venv()

    section("Installing dependencies")
    dep_failures = install_requirements(python_exe)

    section("Configuring .env")
    env_values = configure_env()
    write_env(env_values)

    connectivity = run_connectivity_tests(env_values)
    chmod_launchers()
    print_summary(dep_failures, connectivity)

    if ask_yes_no("Start NexFiremap now?", default=True):
        return launch_server(python_exe)

    print("Whenever you're ready, run the launcher for your platform.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
