"""Configuration for NexFiremap.

Values come from environment variables, optionally seeded from a ``.env`` file
in the project root. Everything has a sensible default except the FIRMS map
key, which you have to request yourself (see README).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent
STATIC_DIR = PACKAGE_DIR / "static"

# Datasets exposed by the FIRMS area API. ``nrt`` sources carry the last ~2
# months, which is what makes a 30 day cache possible.
SOURCES = {
    "VIIRS_NOAA20_NRT": {
        "label": "VIIRS NOAA-20",
        "instrument": "VIIRS",
        "resolution_m": 375,
        "color": "#ff8f3f",
    },
    "VIIRS_NOAA21_NRT": {
        "label": "VIIRS NOAA-21",
        "instrument": "VIIRS",
        "resolution_m": 375,
        "color": "#ffd166",
    },
    "VIIRS_SNPP_NRT": {
        "label": "VIIRS Suomi-NPP",
        "instrument": "VIIRS",
        "resolution_m": 375,
        "color": "#ff5c38",
    },
    "MODIS_NRT": {
        "label": "MODIS Aqua/Terra",
        "instrument": "MODIS",
        "resolution_m": 1000,
        "color": "#e0457b",
    },
    "LANDSAT_NRT": {
        "label": "Landsat (US/Canada only)",
        "instrument": "OLI",
        "resolution_m": 30,
        "color": "#8e7dff",
    },
}

DEFAULT_SOURCES = [
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "VIIRS_SNPP_NRT",
    "MODIS_NRT",
]


# Named CAP (Common Alerting Protocol) warning feeds, so an operator can write
# NEXFIREMAP_CAP_FEEDS=dwd,mowas instead of pasting URLs. Both target audiences
# are covered; the values are the publishers' own public endpoints.
#
# Nothing here is enabled by default. Polling a foreign government's warning
# service is an outbound connection an install should opt into deliberately,
# and an install with no configured feeds creates no HTTP client and starts no
# poll task at all (see alerts.AlertManager.start).
CAP_PRESETS: dict[str, dict[str, str]] = {
    "dwd": {
        "label": "DWD severe weather (Germany)",
        "url": "https://opendata.dwd.de/weather/alerts/cap/COMMUNEUNION_DWD_STAT/Z_CAP_C_EDZW_LATEST_PVW_STATUS_PREMIUMDWD_COMMUNEUNION_DE.zip",
    },
    "mowas": {
        "label": "MoWaS / NINA civil protection (Germany)",
        "url": "https://warnung.bund.de/api31/mowas/mapData.json",
    },
    "nws": {
        "label": "NWS active alerts (United States)",
        "url": "https://api.weather.gov/alerts/active",
    },
    "ipaws": {
        "label": "IPAWS public alerts (United States)",
        "url": "https://apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/public/recent",
    },
}


def _env_cap_feeds(name: str) -> list[str]:
    """Resolve a comma-separated list of CAP preset names and/or raw URLs.

    Accepting both means the common case is a short word and the uncommon case
    (a Land- or county-level feed nobody can preset) is still possible without
    a code change. An unknown entry that is not a URL is dropped with a warning
    rather than failing startup - the same reasoning as every other `_env_*`
    helper here: a typo in `.env` should degrade one feature, not prevent the
    server from starting during an incident.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    feeds: list[str] = []
    for item in (part.strip() for part in raw.split(",") if part.strip()):
        if item.startswith(("http://", "https://")):
            feeds.append(item)
        elif item.lower() in CAP_PRESETS:
            feeds.append(CAP_PRESETS[item.lower()]["url"])
        else:
            logging.getLogger("nexfiremap.config").warning(
                "Unknown CAP feed %r - expected a URL or one of: %s",
                item, ", ".join(sorted(CAP_PRESETS)))
    return feeds


def _load_dotenv(path: Path) -> None:
    """Seed os.environ from a .env file without overwriting real env vars."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_str(name: str, default: str) -> str:
    """Read a string env var, falling back to ``default`` when unset or blank."""
    value = os.environ.get(name, "").strip()
    return value or default


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean-ish env var (``1``/``true``/``yes``/``on``, case-insensitive);
    anything else, including an unset/blank var, falls back to ``default``."""
    value = os.environ.get(name, "").strip().lower()
    if not value: return default
    return value in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    """Read an int env var and clamp it into ``[low, high]``. A missing or
    unparsable value silently falls back to ``default`` rather than raising -
    a malformed .env entry should degrade to a sane setting, not crash startup."""
    try:
        value = int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _env_float(name: str, default: float, *, low: float, high: float) -> float:
    """Float counterpart of ``_env_int`` - same clamp-and-fall-back-silently behaviour."""
    try:
        value = float(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _env_list(name: str, default: list[str]) -> list[str]:
    """Read a comma-separated list of FIRMS source keys, uppercased and filtered
    against ``SOURCES``. Falls back to ``default`` if the var is unset or every
    listed item turns out to be unknown - guards against a typo silently leaving
    the app with zero data sources."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(default)
    items = [item.strip().upper() for item in raw.split(",") if item.strip()]
    return [item for item in items if item in SOURCES] or list(default)


@dataclass(frozen=True)
class Settings:
    """Immutable, process-wide snapshot of all runtime configuration, built once
    by `load_settings()` at startup. Frozen so that nothing downstream can
    mutate a live setting out from under another part of the app - anything
    that needs to change behaviour at runtime does so by constructing a new
    `Settings` (e.g. in tests), not by patching an attribute in place."""

    map_key: str
    host: str
    port: int
    db_path: Path
    cache_days: int
    sources: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))
    # FIRMS caps a single NRT area request at a handful of days - we chunk
    # around it and step down automatically if the API rejects the range.
    # Confirmed live: FIRMS's NRT endpoints reject anything over 5 ("Invalid
    # day range. Expects [1..5]") - starting at 5 instead of guessing higher
    # avoids a guaranteed first-request failure (and its log noise) on every
    # fresh install, while still stepping down further automatically if a
    # given source turns out stricter.
    max_day_range: int = 5
    # Days near "now" keep receiving new detections, so they expire quickly.
    # VIIRS/MODIS are polar orbiters - a given spot only gets a new overpass
    # every couple of hours at best, and FIRMS NRT processing itself lags real
    # time by up to ~3h, so polling far faster than that just spends API
    # transactions on unchanged data. These defaults re-check "today" every
    # 45 minutes rather than hammering the endpoint every few minutes.
    hot_days: int = 2
    hot_ttl_minutes: int = 45
    refresh_interval_minutes: int = 30
    # Coverage bookkeeping grid, in degrees.
    cell_size_deg: float = 10.0
    # Above this many grid cells a viewport is cheaper to satisfy with a single
    # whole-world request than with hundreds of per-cell ones.
    max_cells_per_request: int = 64
    max_concurrent_fetches: int = 3
    request_timeout_s: float = 90.0
    startup_bbox: tuple[float, float, float, float] | None = None
    startup_days: int = 0

    # --- basemap tile cache ---------------------------------------------
    # Every OSM/basemap tile the map draws is proxied through and saved here,
    # so panning back over an area already viewed costs no upstream request.
    tile_cache_dir: Path = field(default_factory=lambda: ROOT_DIR / "data" / "tiles")
    tile_cache_days: int = 30
    tile_cache_max_mb: int = 1024
    tile_max_concurrent: int = 8
    tile_contact: str = ""

    # --- background compute jobs -----------------------------------------
    # Heavy analysis (event clustering, likelihood rasters, burn-scar
    # anchoring, propagation modelling) runs in separate OS processes set to
    # idle OS priority, so it only consumes CPU cycles nothing else wants.
    job_dir: Path = field(default_factory=lambda: ROOT_DIR / "data" / "jobs")
    job_workers: int = 0  # 0 = auto (cpu_count - 1, minimum 1)
    job_retention_days: int = 14
    # A hung job (a stalled network call in a worker, or a pathological
    # input) would otherwise occupy a worker slot forever with no
    # self-healing - unlike a crashed worker (BrokenProcessPool), which
    # already recovers on its own. See jobs.py's JobManager._run.
    job_timeout_s: int = 1800
    # The run-slot semaphore only bounds how many jobs execute at once, not
    # how many can be queued waiting for one - a flood of POST /api/jobs (a
    # buggy client retry loop, a stuck double-submit, or deliberate abuse)
    # would otherwise create an unbounded number of job rows, job
    # directories on disk, and pending asyncio tasks before any of them ever
    # reach the semaphore. A field laptop's disk/RAM budget is exactly what
    # that would exhaust first.
    job_queue_max: int = 200

    # --- verified operational backups -----------------------------------
    backup_dir: Path = field(default_factory=lambda: ROOT_DIR / "data" / "backups")
    backup_interval_minutes: int = 15  # 0 disables scheduled backups; manual remains available
    backup_keep: int = 24

    # Field observations remain in the record indefinitely - this threshold
    # only drives a visible stale warning/style.
    observation_stale_hours: int = 6

    # Vehicle telemetry and drone evidence are served entirely from the
    # incident LAN. Raw positions are never discarded by these display
    # thresholds - they only label derived latest/track responses.
    position_stale_seconds: int = 300
    position_max_speed_kmh: float = 180.0
    telemetry_max_batch: int = 1000
    telemetry_requests_per_minute: int = 120
    drone_dir: Path = field(default_factory=lambda: ROOT_DIR / "data" / "drone")
    drone_max_upload_mb: int = 200
    drone_max_pixels: int = 200_000_000
    drone_mosaic_max_pixels: int = 100_000_000

    # --- LAN access / authentication --------------------------------------
    # Loopback remains frictionless single-user mode. LAN exposure is an
    # explicit fail-closed mode requiring a local administrator credential.
    # These four fields are the safety gate: `lan_mode` is what actually
    # flips the server from binding loopback-only to binding the configured
    # `host` (see load_settings()/wherever the server is started), and it is
    # meant to be paired with a real `admin_password` plus a bounded
    # `session_minutes` so a LAN-exposed instance still requires auth and
    # expires idle sessions rather than staying open indefinitely. The TLS
    # file paths are optional but recommended alongside `lan_mode`, since
    # credentials would otherwise cross the LAN in plaintext.
    lan_mode: bool = False
    admin_password: str = ""
    session_minutes: int = 30
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None

    # --- MQTT telemetry bridge (optional) ---------------------------------
    # Subscribes to a broker for position telemetry. Needs the optional
    # `aiomqtt` dependency; without it the bridge reports itself unavailable
    # and the server starts unchanged (see mqtt.py). `mqtt_topics` is a
    # comma-separated "topic=source_id:format[:callsign]" table, where format
    # is one of json/cot/nmea/mavlink - the same adapters the HTTP feed uses.
    mqtt_url: str = ""
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_client_id: str = ""
    mqtt_topics: str = ""

    # --- external identity (optional) -------------------------------------
    # OIDC and LDAP only ever mint the *existing* local session; nothing
    # downstream knows the difference. Both are off unless configured, and
    # neither disables local password login - an incident LAN's identity
    # provider may be on the far side of the WAN link that just failed, so the
    # local admin password stays as documented break-glass. See federation.py
    # and docs/INCIDENT_LAN_RUNBOOK.md.
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://127.0.0.1:8000/auth/oidc/callback"
    oidc_scopes: str = "openid profile email"
    oidc_username_claim: str = "preferred_username"
    oidc_role_claim: str = "groups"
    oidc_role_map: str = ""
    oidc_default_role: str = "viewer"

    ldap_host: str = ""
    ldap_port: int = 636
    ldap_use_tls: bool = True
    # e.g. "uid={username},ou=people,dc=example,dc=de"
    ldap_user_dn_template: str = ""
    ldap_role_map: str = ""
    ldap_default_role: str = "viewer"

    # --- CoT / TAK gateway (optional, off by default) ---------------------
    # A streaming listener for Cursor on Target (ATAK/WinTAK/TAK Server) and
    # MAVLink drone telemetry. OFF unless `cot_enabled` is set, and bound to
    # loopback unless `cot_host` says otherwise, because neither protocol
    # authenticates its sender: CoT over UDP carries no token and MAVLink v2
    # signing is not verified. The `cot_allow` CIDR list is the actual access
    # control, and it defaults to loopback only - a typo there narrows access
    # rather than widening it. See cot_gateway.py's docstring and
    # docs/INCIDENT_LAN_RUNBOOK.md.
    cot_enabled: bool = False
    cot_host: str = "127.0.0.1"
    cot_tcp_port: int = 8087
    cot_udp_port: int = 6969
    cot_mavlink_port: int = 0  # 0 disables the MAVLink listener
    cot_mavlink_callsign: str = "drone"
    cot_allow: str = ""
    cot_packets_per_minute: int = 600
    # Which position feed inbound CoT is credited to, and its ingest token.
    # Without both, the gateway refuses to listen at all - there would be
    # nowhere for a position to go.
    cot_source_id: str = ""
    cot_source_token: str = ""
    # Incident that inbound markers/drawings become tactical features on.
    # Empty means positions only, which is the safer default: an unauthenticated
    # peer should not be able to draw on an incident unless asked for.
    cot_incident_id: str = ""
    cot_accept_features: bool = False

    # Which tactical symbol set products and the map render with. See
    # symbology.py; the default matches what the frontend already stamps on
    # every feature, so existing records keep their meaning.
    symbology_profile: str = "simplified_multinational"

    # --- CAP public warning feeds (optional) ------------------------------
    # Official alerts (MoWaS/NINA, DWD, IPAWS/NWS) drawn on the same map as
    # the fire picture. Empty by default: polling a government warning service
    # is an outbound connection an install opts into, and with no feeds
    # configured AlertManager creates no client and starts no task.
    cap_feeds: list[str] = field(default_factory=list)
    cap_poll_minutes: int = 10

    # --- EUMETSAT (optional European corroboration source) ----------------
    # Consumer key/secret only - see nexfiremap/eumetsat.py's module
    # docstring for why an access token isn't stored directly (it's
    # short-lived, ~37 minutes live-confirmed, and fetched on demand).
    eumetsat_consumer_key: str = ""
    eumetsat_consumer_secret: str = ""

    @property
    def has_map_key(self) -> bool:
        """Whether a FIRMS map key was configured - callers use this to decide
        whether FIRMS-backed features can run at all instead of letting every
        fetch attempt fail with an auth error."""
        return bool(self.map_key)

    @property
    def has_eumetsat_key(self) -> bool:
        """Whether both EUMETSAT consumer credentials are present. Both halves
        are required to request an access token, so a partially-configured key
        pair is treated as "not configured" rather than attempted and failed."""
        return bool(self.eumetsat_consumer_key and self.eumetsat_consumer_secret)

    @property
    def tile_user_agent(self) -> str:
        # OSM's tile usage policy asks for an identifying User-Agent - a
        # contact point lets a tile provider reach you instead of just
        # blocking your IP if something here misbehaves.
        contact = f"; {self.tile_contact}" if self.tile_contact else ""
        return f"NexFiremap/1.0 (local map cache{contact})"


def load_settings() -> Settings:
    """Build the single `Settings` instance for this process: seed `os.environ`
    from `.env` (without clobbering real env vars), then read/validate/clamp
    every individual setting via the `_env_*` helpers above. Called once at
    startup - the result is meant to be passed around, not re-loaded."""
    _load_dotenv(ROOT_DIR / ".env")

    cache_days = _env_int("NEXFIREMAP_CACHE_DAYS", 30, low=1, high=60)

    db_path = Path(
        _env_str("NEXFIREMAP_DB", str(ROOT_DIR / "data" / "nexfiremap.sqlite3"))
    ).expanduser()

    startup_bbox = None
    raw_bbox = os.environ.get("NEXFIREMAP_STARTUP_BBOX", "").strip()
    if raw_bbox:
        try:
            west, south, east, north = (float(p) for p in raw_bbox.split(","))
            startup_bbox = (west, south, east, north)
        except ValueError:
            # Malformed bbox string - fall back to "no startup bbox" rather
            # than failing app startup over a bad env var.
            startup_bbox = None

    return Settings(
        map_key=_env_str("FIRMS_MAP_KEY", ""),
        host=_env_str("NEXFIREMAP_HOST", "127.0.0.1"),
        port=_env_int("NEXFIREMAP_PORT", 8000, low=1, high=65535),
        db_path=db_path,
        cache_days=cache_days,
        sources=_env_list("NEXFIREMAP_SOURCES", DEFAULT_SOURCES),
        max_day_range=_env_int("NEXFIREMAP_MAX_DAY_RANGE", 5, low=1, high=10),
        hot_days=_env_int("NEXFIREMAP_HOT_DAYS", 2, low=1, high=10),
        hot_ttl_minutes=_env_int("NEXFIREMAP_HOT_TTL_MINUTES", 20, low=1, high=1440),
        refresh_interval_minutes=_env_int(
            "NEXFIREMAP_REFRESH_MINUTES", 15, low=1, high=1440
        ),
        cell_size_deg=_env_float("NEXFIREMAP_CELL_SIZE_DEG", 10.0, low=0.5, high=45.0),
        max_cells_per_request=_env_int(
            "NEXFIREMAP_MAX_CELLS", 64, low=1, high=2048
        ),
        max_concurrent_fetches=_env_int(
            "NEXFIREMAP_MAX_CONCURRENT_FETCHES", 3, low=1, high=8
        ),
        request_timeout_s=_env_float("NEXFIREMAP_TIMEOUT_S", 90.0, low=5.0, high=600.0),
        startup_bbox=startup_bbox,
        startup_days=_env_int("NEXFIREMAP_STARTUP_DAYS", 0, low=0, high=cache_days),
        tile_cache_dir=Path(
            _env_str("NEXFIREMAP_TILE_DIR", str(ROOT_DIR / "data" / "tiles"))
        ).expanduser(),
        tile_cache_days=_env_int("NEXFIREMAP_TILE_CACHE_DAYS", 30, low=1, high=365),
        tile_cache_max_mb=_env_int(
            "NEXFIREMAP_TILE_CACHE_MAX_MB", 1024, low=16, high=1_000_000
        ),
        tile_max_concurrent=_env_int(
            "NEXFIREMAP_TILE_MAX_CONCURRENT", 8, low=1, high=32
        ),
        tile_contact=_env_str("NEXFIREMAP_CONTACT", ""),
        job_dir=Path(
            _env_str("NEXFIREMAP_JOB_DIR", str(ROOT_DIR / "data" / "jobs"))
        ).expanduser(),
        job_workers=_env_int("NEXFIREMAP_JOB_WORKERS", 0, low=0, high=64),
        job_retention_days=_env_int("NEXFIREMAP_JOB_RETENTION_DAYS", 14, low=1, high=365),
        job_timeout_s=_env_int("NEXFIREMAP_JOB_TIMEOUT_S", 1800, low=30, high=86400),
        job_queue_max=_env_int("NEXFIREMAP_JOB_QUEUE_MAX", 200, low=10, high=100000),
        backup_dir=Path(
            _env_str("NEXFIREMAP_BACKUP_DIR", str(ROOT_DIR / "data" / "backups"))
        ).expanduser(),
        backup_interval_minutes=_env_int(
            "NEXFIREMAP_BACKUP_INTERVAL_MINUTES", 15, low=0, high=1440
        ),
        backup_keep=_env_int("NEXFIREMAP_BACKUP_KEEP", 24, low=2, high=1000),
        observation_stale_hours=_env_int("NEXFIREMAP_OBSERVATION_STALE_HOURS", 6, low=1, high=720),
        position_stale_seconds=_env_int("NEXFIREMAP_POSITION_STALE_SECONDS", 300, low=10, high=86400),
        position_max_speed_kmh=_env_float("NEXFIREMAP_POSITION_MAX_SPEED_KMH", 180.0, low=10, high=1000),
        telemetry_max_batch=_env_int("NEXFIREMAP_TELEMETRY_MAX_BATCH", 1000, low=1, high=10000),
        telemetry_requests_per_minute=_env_int("NEXFIREMAP_TELEMETRY_REQUESTS_PER_MINUTE", 120, low=1, high=10000),
        drone_dir=Path(_env_str("NEXFIREMAP_DRONE_DIR", str(ROOT_DIR / "data" / "drone"))).expanduser(),
        drone_max_upload_mb=_env_int("NEXFIREMAP_DRONE_MAX_UPLOAD_MB", 200, low=1, high=4096),
        drone_max_pixels=_env_int("NEXFIREMAP_DRONE_MAX_PIXELS", 200_000_000, low=1_000_000, high=2_000_000_000),
        drone_mosaic_max_pixels=_env_int("NEXFIREMAP_DRONE_MOSAIC_MAX_PIXELS", 100_000_000, low=1_000_000, high=2_000_000_000),
        lan_mode=_env_bool("NEXFIREMAP_LAN_MODE", False),
        admin_password=_env_str("NEXFIREMAP_ADMIN_PASSWORD", ""),
        session_minutes=_env_int("NEXFIREMAP_SESSION_MINUTES", 30, low=5, high=720),
        tls_cert_file=Path(os.environ["NEXFIREMAP_TLS_CERT_FILE"]).expanduser()
        if os.environ.get("NEXFIREMAP_TLS_CERT_FILE", "").strip() else None,
        tls_key_file=Path(os.environ["NEXFIREMAP_TLS_KEY_FILE"]).expanduser()
        if os.environ.get("NEXFIREMAP_TLS_KEY_FILE", "").strip() else None,
        mqtt_url=_env_str("NEXFIREMAP_MQTT_URL", ""),
        mqtt_username=_env_str("NEXFIREMAP_MQTT_USERNAME", ""),
        mqtt_password=_env_str("NEXFIREMAP_MQTT_PASSWORD", ""),
        mqtt_client_id=_env_str("NEXFIREMAP_MQTT_CLIENT_ID", ""),
        mqtt_topics=_env_str("NEXFIREMAP_MQTT_TOPICS", ""),
        oidc_issuer=_env_str("NEXFIREMAP_OIDC_ISSUER", ""),
        oidc_client_id=_env_str("NEXFIREMAP_OIDC_CLIENT_ID", ""),
        oidc_client_secret=_env_str("NEXFIREMAP_OIDC_CLIENT_SECRET", ""),
        oidc_redirect_uri=_env_str("NEXFIREMAP_OIDC_REDIRECT_URI", "http://127.0.0.1:8000/auth/oidc/callback"),
        oidc_scopes=_env_str("NEXFIREMAP_OIDC_SCOPES", "openid profile email"),
        oidc_username_claim=_env_str("NEXFIREMAP_OIDC_USERNAME_CLAIM", "preferred_username"),
        oidc_role_claim=_env_str("NEXFIREMAP_OIDC_ROLE_CLAIM", "groups"),
        oidc_role_map=_env_str("NEXFIREMAP_OIDC_ROLE_MAP", ""),
        oidc_default_role=_env_str("NEXFIREMAP_OIDC_DEFAULT_ROLE", "viewer"),
        ldap_host=_env_str("NEXFIREMAP_LDAP_HOST", ""),
        ldap_port=_env_int("NEXFIREMAP_LDAP_PORT", 636, low=1, high=65535),
        ldap_use_tls=_env_bool("NEXFIREMAP_LDAP_USE_TLS", True),
        ldap_user_dn_template=_env_str("NEXFIREMAP_LDAP_USER_DN_TEMPLATE", ""),
        ldap_role_map=_env_str("NEXFIREMAP_LDAP_ROLE_MAP", ""),
        ldap_default_role=_env_str("NEXFIREMAP_LDAP_DEFAULT_ROLE", "viewer"),
        cot_enabled=_env_bool("NEXFIREMAP_COT_ENABLED", False),
        cot_host=_env_str("NEXFIREMAP_COT_HOST", "127.0.0.1"),
        cot_tcp_port=_env_int("NEXFIREMAP_COT_TCP_PORT", 8087, low=0, high=65535),
        cot_udp_port=_env_int("NEXFIREMAP_COT_UDP_PORT", 6969, low=0, high=65535),
        cot_mavlink_port=_env_int("NEXFIREMAP_COT_MAVLINK_PORT", 0, low=0, high=65535),
        cot_mavlink_callsign=_env_str("NEXFIREMAP_COT_MAVLINK_CALLSIGN", "drone"),
        cot_allow=_env_str("NEXFIREMAP_COT_ALLOW", ""),
        cot_packets_per_minute=_env_int("NEXFIREMAP_COT_PACKETS_PER_MINUTE", 600, low=1, high=100000),
        cot_source_id=_env_str("NEXFIREMAP_COT_SOURCE_ID", ""),
        cot_source_token=_env_str("NEXFIREMAP_COT_SOURCE_TOKEN", ""),
        cot_incident_id=_env_str("NEXFIREMAP_COT_INCIDENT_ID", ""),
        cot_accept_features=_env_bool("NEXFIREMAP_COT_ACCEPT_FEATURES", False),
        symbology_profile=_env_str("NEXFIREMAP_SYMBOLOGY_PROFILE", "simplified_multinational"),
        cap_feeds=_env_cap_feeds("NEXFIREMAP_CAP_FEEDS"),
        cap_poll_minutes=_env_int("NEXFIREMAP_CAP_POLL_MINUTES", 10, low=1, high=1440),
        eumetsat_consumer_key=_env_str("EUMETSAT_CONSUMER_KEY", ""),
        eumetsat_consumer_secret=_env_str("EUMETSAT_CONSUMER_SECRET", ""),
    )
