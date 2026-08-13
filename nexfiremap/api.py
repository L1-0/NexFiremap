"""HTTP API and static hosting for NexFiremap."""

from __future__ import annotations

import asyncio
import importlib
import hmac
import json
import logging
import sqlite3
import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .basemaps import BASEMAPS, OVERLAYS, TERRAIN_DEM
from .backups import BackupManager
from .cache import CacheManager, clamp_bbox
from .config import SOURCES, STATIC_DIR, Settings, load_settings
from .db import Database
from .jobs import JobManager, JobQueueFull
from .field_import import FieldImportManager
from .drone import DroneError, DroneManager
from .geocode import GeocodeService
from .map_packs import MapPackError, MapPackManager
from .merge import MergeManager
from .offline_sources import MAX_UPLOAD_BYTES, OfflineSourceError, OfflineSourceManager
from .products import ProductManager
from .operations import (
    AREA_TYPES,
    FEATURE_TYPES,
    LINE_TYPES,
    NotFoundError,
    OperationsError,
    OperationsStore,
    PackageConflict,
    POINT_TYPES,
    RevisionConflict,
    SAFETY_CHECKS,
    default_period,
)
from .tiles import TRANSPARENT_PNG, TileCache, public_layer
from .tactics import TacticsManager
from .security import SecurityManager
from .telemetry import TelemetryError, TelemetryManager, TelemetryRateLimit
from .wind import WindError, WindManager

log = logging.getLogger("nexfiremap.api")

# The event-analysis modules (Phases 1-4b) pull in heavier optional
# dependencies (skyfield, scipy, scikit-image, rasterio, pystac-client,
# planetary-computer) that a given install might be missing -
# e.g. rasterio historically has the most Windows install friction of the
# bunch. Importing each independently, rather than letting one failure take
# the whole server down, means the core fire map still works and /api/config
# can tell the frontend (and the install wizard) exactly what's missing.
_OPTIONAL_MODULES = ["orbits", "events", "likelihood", "imagery", "terrain", "validation", "industrial", "structures", "eumetsat"]
AVAILABLE_FEATURES: dict[str, bool] = {}
FEATURE_ERRORS: dict[str, str] = {}
_optional: dict[str, Any] = {}

for _name in _OPTIONAL_MODULES:
    try:
        _optional[_name] = importlib.import_module(f".{_name}", __package__)
        AVAILABLE_FEATURES[_name] = True
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see below
        # A missing dependency raises ImportError, which is what normally
        # happens here - but a *present*, mismatched dependency version (no
        # lockfile, only floating ">=" bounds in requirements.txt) can just
        # as easily raise AttributeError/TypeError/etc. at import time.
        # Narrowing this to ImportError would let that crash the whole
        # server at startup, contradicting the graceful-degradation
        # contract every other reference to AVAILABLE_FEATURES relies on
        # (README's "the server still starts... instead of the whole app
        # crashing", the setup wizard's summary text) - one bad optional
        # module should disable itself, not take everything down with it.
        _optional[_name] = None
        AVAILABLE_FEATURES[_name] = False
        FEATURE_ERRORS[_name] = str(exc)
        log.warning("Optional feature %r unavailable: %s", _name, exc)

# Bind the usual module-level names (None if unavailable) so code below can
# still write e.g. `orbits.SATELLITES` the same way a plain `from . import
# orbits` would - `importlib.import_module` alone doesn't create these.
orbits = _optional["orbits"]
events = _optional["events"]
likelihood = _optional["likelihood"]
imagery = _optional["imagery"]
terrain = _optional["terrain"]
validation = _optional["validation"]
industrial = _optional["industrial"]
structures = _optional["structures"]
eumetsat = _optional["eumetsat"]

# Columns of the compact payload the map uses (smaller and faster than GeoJSON).
# scan/track (pixel footprint size in km, along-scan / along-track) let the
# frontend draw a real footprint ellipse instead of a fixed-radius dot.
COMPACT_COLUMNS = [
    "lat",
    "lon",
    "ts",
    "frp",
    "conf",
    "src",
    "sat",
    "dn",
    "bright",
    "scan",
    "track",
]


def _parse_bbox(raw: str | None) -> tuple[float, float, float, float] | None:
    if not raw:
        return None
    parts = raw.split(",")
    if len(parts) != 4:
        raise HTTPException(400, "bbox must be 'west,south,east,north'")
    try:
        west, south, east, north = (float(p) for p in parts)
    except ValueError as exc:
        raise HTTPException(400, "bbox values must be numbers") from exc
    return clamp_bbox((west, south, east, north))


def _parse_sources(raw: str | None, settings: Settings) -> list[str]:
    if not raw:
        return list(settings.sources)
    wanted = [s.strip().upper() for s in raw.split(",") if s.strip()]
    valid = [s for s in wanted if s in SOURCES]
    if not valid:
        raise HTTPException(400, "no valid sources requested")
    return valid


def _parse_levels(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    levels = [s.strip().lower() for s in raw.split(",") if s.strip()]
    valid = [s for s in levels if s in ("low", "nominal", "high")]
    return valid or None


def _json(payload: Any, status_code: int = 200) -> Response:
    """Compact JSON without FastAPI's per-field validation overhead."""
    return Response(
        content=json.dumps(payload, separators=(",", ":"), allow_nan=False),
        media_type="application/json",
        status_code=status_code,
    )


async def _submit_job(jobs: "JobManager", kind: str, params: dict[str, Any]) -> int:
    """jobs.submit() as a clean 503 instead of an unhandled ValueError when
    the kind isn't registered - normally because an optional dependency for
    that feature failed to import (see AVAILABLE_FEATURES/FEATURE_ERRORS)."""
    try:
        return await jobs.submit(kind, params)
    except ValueError as exc:
        raise HTTPException(
            503,
            f"{exc} - this feature's dependencies may not be installed. "
            "See /api/config's 'features' field or run the setup wizard.",
        ) from exc


class EnsureRequest(BaseModel):
    bbox: str = Field(..., description="west,south,east,north")
    days: int = Field(7, ge=1, le=60)
    sources: list[str] | None = None


class JobSubmitRequest(BaseModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class EventDetectRequest(BaseModel):
    bbox: str = Field(..., description="west,south,east,north")
    days: int = Field(30, ge=1, le=60)
    sources: list[str] | None = None
    v_max_kmh: float = Field(8.0, gt=0, le=200)
    max_dt_hours: float = Field(168.0, gt=0, le=24 * 60)
    min_detections: int = Field(2, ge=1, le=1000)


class EventAnalyzeRequest(BaseModel):
    tau_hours: float = Field(6.0, gt=0, le=168)
    resolution_m: float = Field(100.0, ge=30, le=2000)
    reference_ts: float | None = None


class BurnScarRequest(BaseModel):
    resolution_m: float = Field(20.0, ge=10, le=200)


class PropagationRequest(BaseModel):
    resolution_m: float = Field(60.0, ge=20, le=500)
    reference_ts: float | None = None


class EnsembleRequest(BaseModel):
    resolution_m: float = Field(60.0, ge=20, le=500)
    reference_ts: float | None = None
    n_members: int = Field(60, ge=5, le=300)
    random_seed: int | None = None


class ValidationRequest(BaseModel):
    n_splits: int = Field(3, ge=1, le=10)
    threshold: float = Field(0.3, gt=0, lt=1)


# Event clustering only ever reads already-cached detections (no external
# API call, unlike the industrial scan's live Overpass fetch) - the cap
# here exists so a whole-world zoomed-out view doesn't cluster hundreds of
# thousands of points on every pan, not to be polite to a third party.
EVENTS_AUTOFETCH_MAX_AREA_DEG2 = 100.0


# A ~2x2 degree viewport - beyond this, auto-scanning on every pan would
# mean increasingly large/slow live Overpass queries queued just from
# looking at the map. The manual "scan this view" button has no such cap.
INDUSTRIAL_AUTOFETCH_MAX_AREA_DEG2 = 4.0


# A defense-in-depth backstop, not the primary control - the frontend's own
# MIN_ACTIVE_ZOOM gate (app.js) is what normally stops a world-view pan from
# ever sending an autofetch request in the first place. This just makes sure
# a request that somehow bypasses that (a bookmarked/hand-built URL, an old
# tab) can't queue an unbounded, whole-world FIRMS coverage-grid fetch.
DETECTIONS_AUTOFETCH_MAX_AREA_DEG2 = 900.0


class IndustrialScanRequest(BaseModel):
    bbox: str = Field(..., description="west,south,east,north")
    days: int = Field(30, ge=1, le=60)
    window_days: int = Field(30, ge=1, le=60)
    event_id: int | None = None


class StructureScanRequest(BaseModel):
    bbox: str = Field(..., description="west,south,east,north")


class IncidentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    incident_number: str | None = Field(None, max_length=100)
    timezone: str = Field("UTC", max_length=80)
    center_lat: float | None = Field(None, ge=-90, le=90)
    center_lon: float | None = Field(None, ge=-180, le=180)
    notes: str = Field("", max_length=10000)


class IncidentUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    name: str | None = Field(None, min_length=1, max_length=300)
    incident_number: str | None = Field(None, max_length=100)
    status: str | None = Field(None, pattern="^(active|contained|closed)$")
    timezone: str | None = Field(None, min_length=1, max_length=80)
    center_lat: float | None = Field(None, ge=-90, le=90)
    center_lon: float | None = Field(None, ge=-180, le=180)
    notes: str | None = Field(None, max_length=10000)


class PeriodCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    starts_at: str
    ends_at: str
    objectives: str = Field("", max_length=10000)


class PeriodUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    name: str | None = Field(None, min_length=1, max_length=300)
    starts_at: str | None = None
    ends_at: str | None = None
    status: str | None = Field(None, pattern="^(draft|active|closed)$")
    objectives: str | None = Field(None, max_length=10000)


class ScenarioCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    kind: str = Field("primary", pattern="^(primary|contingency|alternative|worst_case)$")
    description: str = Field("", max_length=10000)
    assumptions: str = Field("", max_length=10000)


class ScenarioUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    name: str | None = Field(None, min_length=1, max_length=300)
    kind: str | None = Field(None, pattern="^(primary|contingency|alternative|worst_case)$")
    status: str | None = Field(None, pattern="^(draft|retired)$")
    description: str | None = Field(None, max_length=10000)
    assumptions: str | None = Field(None, max_length=10000)


class TacticalFeatureCreateRequest(BaseModel):
    period_id: str | None = None
    scenario_id: str | None = None
    feature_type: str
    title: str = Field("", max_length=300)
    status: str = "observed"
    geometry: dict[str, Any]
    properties: dict[str, Any] = Field(default_factory=dict)
    observed_at: str | None = None
    source: str | None = Field(None, max_length=200)
    observer: str | None = Field(None, max_length=200)
    confidence: str | None = Field(None, max_length=50)
    valid_from: str | None = None
    valid_to: str | None = None


class TacticalFeatureUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    title: str | None = Field(None, max_length=300)
    status: str | None = None
    geometry: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None
    observed_at: str | None = None
    source: str | None = Field(None, max_length=200)
    observer: str | None = Field(None, max_length=200)
    confidence: str | None = Field(None, max_length=50)
    valid_from: str | None = None
    valid_to: str | None = None


class SafetyUpdateRequest(BaseModel):
    scenario_id: str | None = None
    checks: list[dict[str, Any]]


class ScenarioApproveRequest(BaseModel):
    approver: str = Field(..., min_length=1, max_length=200)
    acknowledge_warnings: bool = False
    expected_revision: int | None = Field(None, ge=1)


class ResourceCreateRequest(BaseModel):
    callsign: str = Field(..., min_length=1, max_length=100)
    unit_type: str = Field(..., min_length=1, max_length=100)
    status: str = Field("available", max_length=40)
    crew_size: int | None = Field(None, ge=0, le=1000)
    water_capacity_l: float | None = Field(None, ge=0)
    capabilities: str = Field("", max_length=10000)
    assignment: str = Field("", max_length=10000)
    contact_channel: str = Field("", max_length=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    position_at: str | None = None


class ResourceUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    callsign: str | None = Field(None, min_length=1, max_length=100)
    unit_type: str | None = Field(None, min_length=1, max_length=100)
    status: str | None = Field(None, pattern="^(available|assigned|working|returning|unavailable)$")
    crew_size: int | None = Field(None, ge=0, le=1000)
    water_capacity_l: float | None = Field(None, ge=0)
    capabilities: str | None = Field(None, max_length=10000)
    assignment: str | None = Field(None, max_length=10000)
    contact_channel: str | None = Field(None, max_length=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    position_at: str | None = None


class SnapshotCreateRequest(BaseModel):
    name: str = Field("", max_length=300)
    period_id: str | None = None
    classification: str = Field("operational", pattern="^(draft|operational|public)$")


class IncidentPackageRequest(BaseModel):
    bundle: dict[str, Any]


class MapPackCreateRequest(BaseModel):
    name: str = Field("Offline AOI check", max_length=200)
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    layers: list[str] = Field(..., min_length=1, max_length=20)
    min_zoom: int = Field(..., ge=0, le=22)
    max_zoom: int = Field(..., ge=0, le=22)


class RecoveryCreateRequest(BaseModel):
    backup_name: str = Field(..., min_length=1, max_length=200)


class FieldImportRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=300)
    content: str = ""
    content_base64: str = ""
    format: str = Field("", max_length=20)
    period_id: str | None = None
    source: str = Field("", max_length=500)
    observer: str = Field("", max_length=200)
    default_feature_type: str = Field("", max_length=80)
    mapping: dict[str, str] = Field(default_factory=dict)
    aoi_bbox: list[float] = Field(..., min_length=4, max_length=4)
    acknowledge_outside_aoi: bool = False
    confirmation_reason: str = Field("", max_length=1000)


class TacticalCalculatorRequest(BaseModel):
    kind: str = Field(..., min_length=1, max_length=80)
    inputs: dict[str, Any]


class TacticalWarningAckRequest(BaseModel):
    period_id: str
    scenario_id: str | None = None
    warning_id: str = Field(..., min_length=1, max_length=100)
    reason: str = Field(..., min_length=1, max_length=1000)


class ScenarioCopyRequest(BaseModel):
    target_period_id: str
    name: str = Field("", max_length=300)


class ProductCreateRequest(BaseModel):
    format: str = Field(..., pattern="^(geojson|json|csv|gpx|kml|kmz|pdf|geopdf|geotiff|gpkg)$")
    classification: str = Field(..., pattern="^(draft|operational|public)$")
    product_type: str
    snapshot_id: str | None = None
    title: str = Field("", max_length=300)


class ModelAttachRequest(BaseModel):
    job_id: int = Field(..., ge=1)


class PositionReportRequest(BaseModel):
    resource_id: str | None = None
    callsign: str = Field(..., min_length=1, max_length=200)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    observed_at: str
    report_source: str = Field(..., pattern="^(gps|radio|manual)$")
    accuracy_m: float | None = Field(None, ge=0, le=100000)
    period_id: str | None = None
    scenario_id: str | None = None


class PositionFeedCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    provider: str = Field("", max_length=200)
    device_kind: str = Field("vehicle_gps", max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PositionFeedUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    name: str | None = Field(None, min_length=1, max_length=200)
    provider: str | None = Field(None, max_length=200)
    device_kind: str | None = Field(None, max_length=80)
    active: bool | None = None
    metadata: dict[str, Any] | None = None


class DroneMissionCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    aircraft: str = Field("", max_length=200)
    operator: str = Field("", max_length=200)
    started_at: str | None = None
    ended_at: str | None = None
    notes: str = Field("", max_length=10000)


class DroneMosaicCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    asset_ids: list[str] = Field(..., min_length=1, max_length=500)


class MergeStageRequest(BaseModel):
    bundle: dict[str, Any]


class MergeResolveRequest(BaseModel):
    choices: dict[str, str]


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=1000)


class AccountCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    role: str = Field(..., min_length=1, max_length=30)
    password: str = Field(..., min_length=12, max_length=1000)


def _model_payload(model: BaseModel, *, exclude: set[str] | None = None) -> dict[str, Any]:
    """Pydantic v1/v2-compatible request extraction."""
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True, exclude=exclude or set())
    return model.dict(exclude_unset=True, exclude=exclude or set())


def _operator(request: Request) -> str:
    """Use the authenticated identity in LAN mode; never trust a spoofable header."""
    identity = getattr(request.state, "identity", None)
    if identity:
        return str(identity["username"])
    return request.headers.get("X-Operator", "local operator")


def _require_administrator(request: Request) -> None:
    """Loopback single-user mode is trusted; authenticated LAN mode is not."""
    identity = getattr(request.state, "identity", None)
    if identity and identity.get("role") != "administrator":
        raise HTTPException(403, "administrator role required")


def _event_row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    for field_name in ("sources_json", "params_json"):
        raw = data.pop(field_name, None)
        key = field_name.replace("_json", "")
        data[key] = json.loads(raw) if raw else None
    return data


def _job_row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    for field_name in ("params_json", "result_json"):
        raw = data.pop(field_name, None)
        key = field_name.replace("_json", "")
        data[key] = json.loads(raw) if raw else None
    return data


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.lan_mode and len(settings.admin_password) < 12:
            raise RuntimeError("LAN mode requires NEXFIREMAP_ADMIN_PASSWORD with at least 12 characters")
        db = Database(settings.db_path)
        cache = CacheManager(settings, db)
        tiles = TileCache(settings)
        jobs = JobManager(settings, db)
        backups = BackupManager(settings, db)
        geocode = GeocodeService(settings)
        app.state.settings = settings
        app.state.db = db
        app.state.cache = cache
        app.state.tiles = tiles
        app.state.jobs = jobs
        app.state.backups = backups
        app.state.geocode = geocode
        app.state.offline_sources = OfflineSourceManager(settings.tile_cache_dir)
        app.state.map_packs = MapPackManager(tiles, app.state.offline_sources)
        app.state.operations = OperationsStore(db)
        app.state.telemetry = TelemetryManager(app.state.operations, settings)
        app.state.drone = DroneManager(app.state.operations, app.state.offline_sources, settings)
        app.state.wind = WindManager(app.state.operations)
        app.state.field_imports = FieldImportManager(db, app.state.operations)
        app.state.tactics = TacticsManager(app.state.operations)
        app.state.products = ProductManager(db, app.state.operations)
        app.state.merges = MergeManager(app.state.operations)
        app.state.security = SecurityManager(settings, db)
        app.state.key_status = {"checked_at": 0.0, "payload": None}
        await cache.start()
        await tiles.start()
        await jobs.start()
        await backups.start()
        log.info(
            "NexFiremap ready on http://%s:%d (%d job worker%s)",
            settings.host,
            settings.port,
            jobs.worker_count,
            "" if jobs.worker_count == 1 else "s",
        )
        try:
            yield
        finally:
            await backups.stop()
            await cache.stop()
            await tiles.stop()
            await jobs.stop()
            await geocode.stop()
            db.close()

    app = FastAPI(
        title="NexFiremap",
        description="Local NASA FIRMS fire-detection cache and map server.",
        version="1.0.0",
        lifespan=lifespan,
    )

    class SecurityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            security: SecurityManager | None = getattr(request.app.state, "security", None)
            path = request.url.path
            feed_ingest = request.method == "POST" and path.startswith("/api/feeds/positions/")
            public_path = feed_ingest or path in {"/", "/health", "/service-worker.js", "/api/auth/login", "/api/config"} or path.startswith("/static/")
            if security and security.enabled and not public_path:
                session = security.session(request.cookies.get("nexfiremap_session"))
                if session is None:
                    return _json({"detail": "authentication required"}, 401)
                request.state.identity = {"username": session.username, "role": session.role}
                if path.startswith("/api/auth/accounts") and session.role != "administrator":
                    return _json({"detail": "administrator role required"}, 403)
                if request.method in {"GET", "HEAD"} and not security.may_read(session.role, path):
                    return _json({"detail": "role is not permitted to read this resource"}, 403)
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session.csrf):
                        return _json({"detail": "CSRF token required"}, 403)
                    if path != "/api/auth/logout" and not security.may_write(session.role, path):
                        return _json({"detail": "role is not permitted for this operation"}, 403)
            response = await call_next(request)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; worker-src 'self' blob:")
            if settings.tls_cert_file:
                # Only when TLS is actually configured - HSTS on a plain-HTTP
                # LAN deployment would make browsers refuse to ever connect
                # over HTTP again, breaking exactly the deployment this
                # header must stay silent for. When TLS is on, it still
                # matters on a LAN: an attacker on the same network segment
                # (ARP spoofing, a rogue AP) can otherwise strip TLS on a
                # repeat visit by intercepting the first plain-HTTP redirect.
                response.headers.setdefault("Strict-Transport-Security", "max-age=15552000; includeSubDomains")
            return response

    app.add_middleware(SecurityMiddleware)

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> Response:
        # Without this, any bug this router doesn't already turn into an
        # HTTPException falls through to Starlette's default plain-text 500
        # - breaking the "thin JSON client" contract the frontend assumes
        # for every other response (see app.js's fetch helpers, which all
        # expect a JSON body even on failure). HTTPException itself keeps
        # using FastAPI's own handler (matched by the more specific type
        # first), which already returns this same {"detail": ...} shape.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _json({"detail": "Internal server error"}, status_code=500)

    @app.exception_handler(OperationsError)
    async def _operations_exception(request: Request, exc: OperationsError) -> Response:
        if isinstance(exc, PackageConflict):
            return _json(
                {"detail": str(exc), "code": "package_conflict", "report": exc.report},
                status_code=409,
            )
        if isinstance(exc, NotFoundError):
            return _json({"detail": str(exc)}, status_code=404)
        if isinstance(exc, RevisionConflict):
            return _json(
                {"detail": str(exc), "code": "revision_conflict", "current": exc.entity},
                status_code=409,
            )
        return _json({"detail": str(exc)}, status_code=400)

    @app.exception_handler(TelemetryError)
    @app.exception_handler(DroneError)
    @app.exception_handler(WindError)
    async def _sensor_exception(request: Request, exc: Exception) -> Response:
        return _json({"detail": str(exc)}, status_code=400)

    @app.exception_handler(TelemetryRateLimit)
    async def _telemetry_rate_limit(request: Request, exc: TelemetryRateLimit) -> Response:
        return _json({"detail": str(exc)}, status_code=429)

    @app.exception_handler(JobQueueFull)
    async def _job_queue_full(request: Request, exc: JobQueueFull) -> Response:
        return _json({"detail": str(exc)}, status_code=429)

    @app.exception_handler(MapPackError)
    async def _map_pack_exception(request: Request, exc: MapPackError) -> Response:
        return _json({"detail": str(exc)}, status_code=400)

    @app.exception_handler(OfflineSourceError)
    async def _offline_source_exception(request: Request, exc: OfflineSourceError) -> Response:
        return _json({"detail": str(exc)}, status_code=400)

    # ------------------------------------------------------------------ meta

    @app.post("/api/auth/login")
    async def api_login(request: Request, body: LoginRequest) -> Response:
        security: SecurityManager = request.app.state.security
        if not security.enabled:
            return _json({"enabled": False, "username": "local operator", "role": "administrator", "csrf": ""})
        client = request.client.host if request.client else "unknown"
        session = await asyncio.to_thread(security.login, body.username, body.password, client)
        if session is None:
            raise HTTPException(401, "invalid credentials or login rate limit reached")
        response = _json({"enabled": True, "username": session.username, "role": session.role,
                          "csrf": session.csrf, "expires_at": session.expires_at})
        response.set_cookie("nexfiremap_session", session.token, httponly=True, samesite="strict",
                            secure=bool(settings.tls_cert_file), max_age=security.session_seconds, path="/")
        return response

    @app.get("/api/auth/session")
    async def api_auth_session(request: Request) -> Response:
        security: SecurityManager = request.app.state.security
        if not security.enabled:
            return _json({"enabled": False, "username": "local operator", "role": "administrator", "csrf": ""})
        session = security.session(request.cookies.get("nexfiremap_session"))
        if session is None: raise HTTPException(401, "authentication required")
        return _json({"enabled": True, "username": session.username, "role": session.role,
                      "csrf": session.csrf, "expires_at": session.expires_at})

    @app.post("/api/auth/logout")
    async def api_logout(request: Request) -> Response:
        security: SecurityManager = request.app.state.security
        security.logout(request.cookies.get("nexfiremap_session"))
        response = _json({"logged_out": True}); response.delete_cookie("nexfiremap_session", path="/")
        return response

    @app.get("/api/auth/accounts")
    async def api_accounts(request: Request) -> Response:
        security: SecurityManager = request.app.state.security
        return _json(await asyncio.to_thread(security.accounts))

    @app.post("/api/auth/accounts")
    async def api_account_create(request: Request, body: AccountCreateRequest) -> Response:
        security: SecurityManager = request.app.state.security
        try:
            account = await asyncio.to_thread(
                security.create_account, body.username, body.role, body.password
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "username already exists") from exc
        return _json(account, 201)

    @app.get("/api/public/products")
    async def api_public_products(request: Request) -> Response:
        rows = request.app.state.db.conn.execute(
            "SELECT p.id,p.incident_id,p.product_type,p.filename,p.sha256,p.size_bytes,p.created_at,"
            "i.name AS incident_name FROM incident_products p JOIN incidents i ON i.id=p.incident_id "
            "WHERE p.classification='public' ORDER BY p.created_at DESC"
        ).fetchall()
        return _json([dict(row) for row in rows])

    @app.get("/api/public/products/{product_id}/download")
    async def api_public_product_download(request: Request, product_id: str) -> Response:
        row = request.app.state.db.conn.execute(
            "SELECT filename,metadata_json,content_blob FROM incident_products WHERE id=? AND classification='public'",
            (product_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "public product not found")
        media_type = json.loads(row["metadata_json"])["media_type"]
        return Response(content=bytes(row["content_blob"]), media_type=media_type,
                        headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"',
                                 "Cache-Control": "no-store"})

    @app.get("/api/config")
    async def api_config(request: Request) -> Response:
        cache: CacheManager = request.app.state.cache
        return _json(
            {
                "app": "NexFiremap",
                "cache_days": settings.cache_days,
                "has_map_key": settings.has_map_key,
                "has_eumetsat_key": settings.has_eumetsat_key,
                "sources": [
                    {
                        "id": key,
                        **meta,
                        "enabled": key in settings.sources,
                        "disabled_reason": cache.disabled_sources.get(key),
                    }
                    for key, meta in SOURCES.items()
                ],
                "basemaps": [public_layer(bm) for bm in BASEMAPS]
                + request.app.state.offline_sources.public_layers(),
                "overlays": [public_layer(ov) for ov in OVERLAYS],
                "terrain_dem": public_layer(TERRAIN_DEM),
                "refresh_minutes": settings.refresh_interval_minutes,
                "features": AVAILABLE_FEATURES,
                # Used by the frontend as the initial view only when the URL carries
                # no #zoom/lat/lon of its own (see app.js's initMap) - lets an
                # operator configure a sensible non-world-view default without
                # every visitor needing to pan/zoom in by hand first.
                "startup_bbox": list(settings.startup_bbox) if settings.startup_bbox else None,
            }
        )

    @app.get("/api/geocode")
    async def api_geocode(request: Request, q: str = Query("")) -> Response:
        geocode: GeocodeService = request.app.state.geocode
        return _json(await geocode.search(q))

    @app.get("/api/status")
    async def api_status(request: Request, key: bool = Query(True)) -> Response:
        db: Database = request.app.state.db
        cache: CacheManager = request.app.state.cache

        stats = await asyncio.to_thread(db.stats)

        key_payload = None
        if key and settings.has_map_key:
            cached = request.app.state.key_status
            if time.time() - cached["checked_at"] > 60:
                cached["payload"] = await cache.client.key_status()
                cached["checked_at"] = time.time()
            key_payload = cached["payload"]

        tiles: TileCache = request.app.state.tiles
        tile_stats = await asyncio.to_thread(tiles.stats)

        jobs: JobManager = request.app.state.jobs
        job_stats = await asyncio.to_thread(jobs.status)

        return _json(
            {
                "has_map_key": settings.has_map_key,
                "cache": stats,
                "fetcher": cache.status(),
                "tiles": tile_stats,
                "jobs": job_stats,
                "map_key": key_payload,
                "server_time": datetime.now(timezone.utc).isoformat(),
            }
        )

    @app.get("/health")
    async def health() -> Response:
        return _json({"status": "ok"})

    # --------------------------------------------------------- operations

    @app.get("/api/operations/backups")
    async def api_backups(request: Request) -> Response:
        # A backup is a full-database export - every incident, every
        # account's password hash, everything regardless of classification -
        # not the incident-scoped data may_read's generic "any non-public
        # role" rule was written for. List and download both need the
        # explicit administrator check the generic role/path gating doesn't
        # give them.
        _require_administrator(request)
        backups: BackupManager = request.app.state.backups
        return _json({"status": backups.status(), "backups": backups.list_backups()})

    @app.post("/api/operations/backups")
    async def api_backup_create(request: Request) -> Response:
        _require_administrator(request)
        backups: BackupManager = request.app.state.backups
        result = await asyncio.to_thread(backups.create_backup, "manual")
        return _json(result, 201)

    @app.post("/api/operations/backups/{name}/verify")
    async def api_backup_verify(request: Request, name: str) -> Response:
        _require_administrator(request)
        backups: BackupManager = request.app.state.backups
        try:
            result = await asyncio.to_thread(backups.verify, name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "backup not found") from exc
        return _json(result)

    @app.get("/api/operations/backups/{name}/download")
    async def api_backup_download(request: Request, name: str) -> Response:
        _require_administrator(request)
        backups: BackupManager = request.app.state.backups
        try:
            path = backups.path_for_download(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "backup not found") from exc
        return FileResponse(
            path, media_type="application/vnd.sqlite3",
            filename=path.name,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/operations/recoveries")
    async def api_recoveries(request: Request) -> Response:
        _require_administrator(request)
        backups: BackupManager = request.app.state.backups
        return _json(await asyncio.to_thread(backups.list_recoveries))

    @app.post("/api/operations/recoveries")
    async def api_recovery_create(request: Request, body: RecoveryCreateRequest) -> Response:
        _require_administrator(request)
        backups: BackupManager = request.app.state.backups
        try:
            result = await asyncio.to_thread(backups.create_recovery, body.backup_name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "backup not found") from exc
        return _json(result, 201)

    @app.get("/api/operations/recoveries/{name}/download")
    async def api_recovery_download(request: Request, name: str) -> Response:
        _require_administrator(request)
        backups: BackupManager = request.app.state.backups
        try:
            path = backups.recovery_path_for_download(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "recovery database not found") from exc
        return FileResponse(path, media_type="application/vnd.sqlite3", filename=path.name,
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/operations/offline-sources")
    async def api_offline_sources(request: Request) -> Response:
        manager: OfflineSourceManager = request.app.state.offline_sources
        return _json(await asyncio.to_thread(manager.list))

    @app.post("/api/operations/offline-sources")
    async def api_offline_source_import(
        request: Request,
        name: str = Query(..., min_length=1, max_length=200),
        source: str = Query(..., min_length=1, max_length=500),
        attribution: str = Query(..., min_length=1, max_length=2000),
        acquired_at: str = Query(..., min_length=1, max_length=100),
        licence: str = Query(..., min_length=1, max_length=1000),
        limitations: str = Query("", max_length=5000),
        source_format: str = Query("mbtiles", pattern="^(mbtiles|geotiff|gpkg)$"),
    ) -> Response:
        manager: OfflineSourceManager = request.app.state.offline_sources
        raw_length = request.headers.get("content-length")
        try:
            content_length = int(raw_length) if raw_length is not None else None
        except ValueError as exc:
            raise HTTPException(400, "invalid Content-Length") from exc
        source_id, partial = manager.begin_upload(content_length)
        total = 0
        try:
            with partial.open("wb") as handle:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise OfflineSourceError(f"offline source upload exceeds {MAX_UPLOAD_BYTES} bytes")
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            finalize = manager.finalize_upload if source_format == "mbtiles" else manager.finalize_raster_upload
            result = await asyncio.to_thread(
                finalize, source_id, partial, name=name, source=source,
                attribution=attribution, acquired_at=acquired_at, licence=licence,
                limitations=limitations,
            )
        except Exception:
            manager.abort_upload(partial)
            raise
        return _json(result, 201)

    @app.post("/api/operations/offline-sources/{source_id}/terrain-package")
    async def api_terrain_package(
        request: Request, source_id: str, interval_m: float = Query(20, ge=1, le=1000),
    ) -> Response:
        manager: OfflineSourceManager = request.app.state.offline_sources
        path, manifest = await asyncio.to_thread(manager.derive_terrain_package, source_id, interval_m)
        return FileResponse(path, media_type="application/zip", filename=path.name,
                            headers={"X-Content-SHA256": manifest["sha256"], "Cache-Control": "no-store"})

    @app.get("/api/operations/meta")
    async def api_operations_meta() -> Response:
        return _json({
            "feature_types": sorted(FEATURE_TYPES),
            "geometry": {
                "Point": sorted(POINT_TYPES),
                "LineString": sorted(LINE_TYPES),
                "Polygon": sorted(AREA_TYPES),
            },
            "safety_checks": [{"key": key, "label": label} for key, label in SAFETY_CHECKS],
            "schema": "nexfiremap-incident/1",
            "observation_stale_hours": settings.observation_stale_hours,
        })

    @app.get("/api/operations/map-packs")
    async def api_map_packs(request: Request) -> Response:
        manager: MapPackManager = request.app.state.map_packs
        return _json(await asyncio.to_thread(manager.list))

    @app.post("/api/operations/map-packs")
    async def api_map_pack_create(request: Request, body: MapPackCreateRequest) -> Response:
        manager: MapPackManager = request.app.state.map_packs
        result = await asyncio.to_thread(
            manager.create, body.name, body.bbox, body.layers, body.min_zoom, body.max_zoom
        )
        return _json(result, 201)

    @app.get("/api/operations/map-packs/{manifest_id}")
    async def api_map_pack_get(request: Request, manifest_id: str) -> Response:
        manager: MapPackManager = request.app.state.map_packs
        try:
            result = await asyncio.to_thread(manager.load, manifest_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "map-pack manifest not found") from exc
        return _json(result)

    @app.post("/api/operations/map-packs/{manifest_id}/verify")
    async def api_map_pack_verify(request: Request, manifest_id: str) -> Response:
        manager: MapPackManager = request.app.state.map_packs
        try:
            result = await asyncio.to_thread(manager.verify, manifest_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "map-pack manifest not found") from exc
        return _json(result)

    @app.delete("/api/operations/map-packs/{manifest_id}")
    async def api_map_pack_delete(request: Request, manifest_id: str) -> Response:
        manager: MapPackManager = request.app.state.map_packs
        try:
            result = await asyncio.to_thread(manager.delete, manifest_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "map-pack manifest not found") from exc
        return _json(result)

    @app.post("/api/operations/import/preview")
    async def api_import_preview(request: Request, body: IncidentPackageRequest) -> Response:
        store: OperationsStore = request.app.state.operations
        return _json(await asyncio.to_thread(store.preview_import, body.bundle))

    @app.post("/api/operations/import/apply")
    async def api_import_apply(request: Request, body: IncidentPackageRequest) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        result = await asyncio.to_thread(store.import_bundle, body.bundle, actor)
        return _json(result, 201)

    @app.post("/api/operations/merge/stage")
    async def api_merge_stage(request: Request, body: MergeStageRequest) -> Response:
        manager: MergeManager = request.app.state.merges
        actor = _operator(request)
        return _json(await asyncio.to_thread(manager.stage, body.bundle, actor), 201)

    @app.get("/api/operations/incidents/{incident_id}/merge-packages")
    async def api_merge_packages(request: Request, incident_id: str) -> Response:
        manager: MergeManager = request.app.state.merges
        return _json(await asyncio.to_thread(manager.list, incident_id))

    @app.post("/api/operations/merge/{package_id}/resolve")
    async def api_merge_resolve(
        request: Request, package_id: str, body: MergeResolveRequest,
    ) -> Response:
        manager: MergeManager = request.app.state.merges
        actor = _operator(request)
        return _json(await asyncio.to_thread(manager.resolve, package_id, body.choices, actor))

    @app.post("/api/operations/incidents/{incident_id}/field-imports/preview")
    async def api_field_import_preview(
        request: Request, incident_id: str, body: FieldImportRequest,
    ) -> Response:
        manager: FieldImportManager = request.app.state.field_imports
        report, _ = await asyncio.to_thread(manager.prepare, incident_id, _model_payload(body))
        return _json(report)

    @app.post("/api/operations/incidents/{incident_id}/field-imports/apply")
    async def api_field_import_apply(
        request: Request, incident_id: str, body: FieldImportRequest,
    ) -> Response:
        manager: FieldImportManager = request.app.state.field_imports
        actor = _operator(request)
        result = await asyncio.to_thread(manager.apply, incident_id, _model_payload(body), actor)
        return _json(result, 201)

    @app.get("/api/operations/incidents/{incident_id}/field-imports")
    async def api_field_imports(request: Request, incident_id: str) -> Response:
        manager: FieldImportManager = request.app.state.field_imports
        rows = await asyncio.to_thread(manager.list_imports, incident_id)
        for row in rows:
            row["report"] = json.loads(row.pop("report_json"))
        return _json(rows)

    @app.get("/api/operations/incidents/{incident_id}/field-imports/{import_id}/original")
    async def api_field_import_original(
        request: Request, incident_id: str, import_id: str,
    ) -> Response:
        manager: FieldImportManager = request.app.state.field_imports
        filename, data = await asyncio.to_thread(manager.original, incident_id, import_id)
        safe_name = Path(filename).name.replace('"', "") or "source-data"
        return Response(content=data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{safe_name}"',
                                 "Cache-Control": "no-store"})

    @app.get("/api/operations/incidents")
    async def api_incidents(request: Request, include_closed: bool = False) -> Response:
        store: OperationsStore = request.app.state.operations
        return _json(await asyncio.to_thread(store.list_incidents, include_closed))

    @app.post("/api/operations/incidents")
    async def api_incident_create(request: Request, body: IncidentCreateRequest) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        incident = await asyncio.to_thread(store.create_incident, _model_payload(body), actor)
        period = await asyncio.to_thread(store.create_period, incident["id"], default_period(), actor)
        scenario = await asyncio.to_thread(
            store.create_scenario, incident["id"], period["id"],
            {"name": "Plan A", "kind": "primary", "description": "Primary operational plan"}, actor,
        )
        return _json({"incident": incident, "period": period, "scenario": scenario}, 201)

    @app.get("/api/operations/incidents/{incident_id}")
    async def api_incident_workspace(request: Request, incident_id: str) -> Response:
        store: OperationsStore = request.app.state.operations
        return _json(await asyncio.to_thread(store.export_bundle, incident_id))

    @app.patch("/api/operations/incidents/{incident_id}")
    async def api_incident_update(
        request: Request, incident_id: str, body: IncidentUpdateRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        payload = _model_payload(body, exclude={"expected_revision"})
        result = await asyncio.to_thread(
            store.update_incident, incident_id, payload, body.expected_revision, actor
        )
        return _json(result)

    @app.post("/api/operations/incidents/{incident_id}/periods")
    async def api_period_create(request: Request, incident_id: str, body: PeriodCreateRequest) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        result = await asyncio.to_thread(store.create_period, incident_id, _model_payload(body), actor)
        return _json(result, 201)

    @app.patch("/api/operations/incidents/{incident_id}/periods/{period_id}")
    async def api_period_update(
        request: Request, incident_id: str, period_id: str, body: PeriodUpdateRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        payload = _model_payload(body, exclude={"expected_revision"})
        result = await asyncio.to_thread(
            store.update_period, incident_id, period_id, payload, body.expected_revision, actor
        )
        return _json(result)

    @app.post("/api/operations/incidents/{incident_id}/periods/{period_id}/scenarios")
    async def api_scenario_create(
        request: Request, incident_id: str, period_id: str, body: ScenarioCreateRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        result = await asyncio.to_thread(
            store.create_scenario, incident_id, period_id, _model_payload(body), actor
        )
        return _json(result, 201)

    @app.post("/api/operations/incidents/{incident_id}/scenarios/{scenario_id}/copy")
    async def api_scenario_copy(
        request: Request, incident_id: str, scenario_id: str, body: ScenarioCopyRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        result = await asyncio.to_thread(
            store.copy_scenario, incident_id, scenario_id, body.target_period_id, body.name, actor
        )
        return _json(result, 201)

    @app.patch("/api/operations/incidents/{incident_id}/scenarios/{scenario_id}")
    async def api_scenario_update(
        request: Request, incident_id: str, scenario_id: str, body: ScenarioUpdateRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        payload = _model_payload(body, exclude={"expected_revision"})
        result = await asyncio.to_thread(
            store.update_scenario, incident_id, scenario_id, payload, body.expected_revision, actor
        )
        return _json(result)

    @app.get("/api/operations/incidents/{incident_id}/model-runs")
    async def api_model_runs(
        request: Request, incident_id: str, scenario_id: str | None = None,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        store.get_incident(incident_id)
        return _json(await asyncio.to_thread(store.list_model_runs, incident_id, scenario_id))

    @app.post("/api/operations/incidents/{incident_id}/scenarios/{scenario_id}/model-runs")
    async def api_model_run_attach(
        request: Request, incident_id: str, scenario_id: str, body: ModelAttachRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        result = await asyncio.to_thread(
            store.attach_model_run, incident_id, scenario_id, body.job_id, _operator(request)
        )
        return _json(result, 201)

    @app.get("/api/operations/incidents/{incident_id}/features")
    async def api_features(
        request: Request, incident_id: str, period_id: str | None = None,
        scenario_id: str | None = None, include_deleted: bool = False,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        features = await asyncio.to_thread(
            store.list_features, incident_id, period_id, scenario_id, include_deleted
        )
        return _json({"type": "FeatureCollection", "features": features})

    @app.get("/api/operations/incidents/{incident_id}/progression")
    async def api_progression(
        request: Request, incident_id: str, from_time: str, to_time: str,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        return _json(await asyncio.to_thread(store.progression, incident_id, from_time, to_time))

    @app.get("/api/operations/incidents/{incident_id}/tactical-assessment")
    async def api_tactical_assessment(
        request: Request, incident_id: str, period_id: str, scenario_id: str | None = None,
    ) -> Response:
        manager: TacticsManager = request.app.state.tactics
        return _json(await asyncio.to_thread(manager.assessment, incident_id, period_id, scenario_id))

    @app.post("/api/operations/tactical-calculator")
    async def api_tactical_calculator(body: TacticalCalculatorRequest) -> Response:
        return _json(TacticsManager.calculate(body.kind, body.inputs))

    @app.post("/api/operations/incidents/{incident_id}/tactical-warning-acknowledgements")
    async def api_tactical_warning_ack(
        request: Request, incident_id: str, body: TacticalWarningAckRequest,
    ) -> Response:
        manager: TacticsManager = request.app.state.tactics
        result = await asyncio.to_thread(
            manager.acknowledge, incident_id, body.period_id, body.scenario_id,
            body.warning_id, body.reason, _operator(request),
        )
        return _json(result, 201)

    @app.post("/api/operations/incidents/{incident_id}/features")
    async def api_feature_create(
        request: Request, incident_id: str, body: TacticalFeatureCreateRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        result = await asyncio.to_thread(store.create_feature, incident_id, _model_payload(body), actor)
        return _json(result, 201)

    @app.patch("/api/operations/incidents/{incident_id}/features/{feature_id}")
    async def api_feature_update(
        request: Request, incident_id: str, feature_id: str, body: TacticalFeatureUpdateRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        payload = _model_payload(body, exclude={"expected_revision"})
        result = await asyncio.to_thread(
            store.update_feature, incident_id, feature_id, payload, body.expected_revision, actor
        )
        return _json(result)

    @app.delete("/api/operations/incidents/{incident_id}/features/{feature_id}")
    async def api_feature_delete(
        request: Request, incident_id: str, feature_id: str,
        expected_revision: int = Query(..., ge=1),
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        result = await asyncio.to_thread(
            store.delete_feature, incident_id, feature_id, expected_revision, actor
        )
        return _json(result)

    @app.get("/api/operations/incidents/{incident_id}/periods/{period_id}/safety")
    async def api_safety_get(
        request: Request, incident_id: str, period_id: str, scenario_id: str | None = None,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        store.get_incident(incident_id)
        return _json(await asyncio.to_thread(store.get_safety_checks, period_id, scenario_id))

    @app.put("/api/operations/incidents/{incident_id}/periods/{period_id}/safety")
    async def api_safety_update(
        request: Request, incident_id: str, period_id: str, body: SafetyUpdateRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        result = await asyncio.to_thread(
            store.set_safety_checks, incident_id, period_id, body.scenario_id, body.checks, actor
        )
        return _json(result)

    @app.post("/api/operations/incidents/{incident_id}/scenarios/{scenario_id}/approve")
    async def api_scenario_approve(
        request: Request, incident_id: str, scenario_id: str, body: ScenarioApproveRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        result = await asyncio.to_thread(
            store.approve_scenario, incident_id, scenario_id, body.approver,
            body.acknowledge_warnings, body.expected_revision,
        )
        return _json(result)

    @app.get("/api/operations/incidents/{incident_id}/resources")
    async def api_resources(request: Request, incident_id: str) -> Response:
        store: OperationsStore = request.app.state.operations
        store.get_incident(incident_id)
        return _json(await asyncio.to_thread(store.list_resources, incident_id))

    @app.post("/api/operations/incidents/{incident_id}/position-reports")
    async def api_position_report(
        request: Request, incident_id: str, body: PositionReportRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        resource_row = None
        if body.resource_id:
            resource_row = store.db.conn.execute(
                "SELECT revision FROM incident_resources WHERE id=? AND incident_id=?",
                (body.resource_id, incident_id),
            ).fetchone()
            if resource_row is None:
                raise HTTPException(404, "resource not found")
        report = await asyncio.to_thread(store.create_feature, incident_id, {
            "period_id": body.period_id, "scenario_id": body.scenario_id,
            "feature_type": "resource_position", "title": body.callsign,
            "status": "observed", "observed_at": body.observed_at,
            "source": body.report_source, "observer": actor,
            "geometry": {"type": "Point", "coordinates": [body.longitude, body.latitude]},
            "properties": {"resource_id": body.resource_id, "report_source": body.report_source,
                           "horizontal_accuracy_m": body.accuracy_m},
        }, actor)
        if body.resource_id and resource_row is not None:
            await asyncio.to_thread(
                store.update_resource, incident_id, body.resource_id,
                {"latitude": body.latitude, "longitude": body.longitude, "position_at": body.observed_at},
                int(resource_row["revision"]), actor,
            )
        return _json(report, 201)

    @app.get("/api/operations/incidents/{incident_id}/position-feeds")
    async def api_position_feeds(request: Request, incident_id: str) -> Response:
        manager: TelemetryManager = request.app.state.telemetry
        return _json(await asyncio.to_thread(manager.list_sources, incident_id))

    @app.post("/api/operations/incidents/{incident_id}/position-feeds")
    async def api_position_feed_create(
        request: Request, incident_id: str, body: PositionFeedCreateRequest,
    ) -> Response:
        _require_administrator(request)
        manager: TelemetryManager = request.app.state.telemetry
        result = await asyncio.to_thread(manager.create_source, incident_id, _model_payload(body), _operator(request))
        return _json(result, 201)

    @app.patch("/api/operations/incidents/{incident_id}/position-feeds/{source_id}")
    async def api_position_feed_update(
        request: Request, incident_id: str, source_id: str, body: PositionFeedUpdateRequest,
    ) -> Response:
        _require_administrator(request)
        manager: TelemetryManager = request.app.state.telemetry
        result = await asyncio.to_thread(
            manager.update_source, incident_id, source_id, _model_payload(body), _operator(request)
        )
        return _json(result)

    @app.post("/api/operations/incidents/{incident_id}/position-feeds/{source_id}/rotate-token")
    async def api_position_feed_rotate(request: Request, incident_id: str, source_id: str) -> Response:
        _require_administrator(request)
        manager: TelemetryManager = request.app.state.telemetry
        return _json(await asyncio.to_thread(manager.rotate_token, incident_id, source_id, _operator(request)))

    @app.post("/api/feeds/positions/{source_id}")
    async def api_position_feed_ingest(request: Request, source_id: str) -> Response:
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > 2 * 1024 * 1024:
                    raise HTTPException(413, "telemetry batch exceeds 2 MiB")
            except ValueError as exc:
                raise HTTPException(400, "invalid Content-Length") from exc
        raw = await request.body()
        if len(raw) > 2 * 1024 * 1024:
            raise HTTPException(413, "telemetry batch exceeds 2 MiB")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(400, "telemetry body must be JSON") from exc
        reports = payload.get("positions") if isinstance(payload, dict) else None
        manager: TelemetryManager = request.app.state.telemetry
        try:
            result = await asyncio.to_thread(
                manager.ingest, source_id, request.headers.get("X-Feed-Token", ""), reports
            )
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        return _json(result, 202)

    @app.get("/api/operations/incidents/{incident_id}/vehicle-positions/latest")
    async def api_vehicle_latest(request: Request, incident_id: str) -> Response:
        manager: TelemetryManager = request.app.state.telemetry
        return _json(await asyncio.to_thread(manager.latest, incident_id))

    @app.get("/api/operations/incidents/{incident_id}/wind-field")
    async def api_incident_wind_field(
        request: Request, incident_id: str, bbox: str, at: str | None = None,
        window_hours: float = Query(6, ge=.25, le=72), grid: int = Query(12, ge=2, le=30),
        scenario_id: str | None = None,
    ) -> Response:
        parsed = _parse_bbox(bbox)
        if parsed is None:
            raise HTTPException(400, "bbox is required")
        manager: WindManager = request.app.state.wind
        return _json(await asyncio.to_thread(
            manager.field, incident_id, parsed, at=at, window_hours=window_hours,
            grid=grid, scenario_id=scenario_id,
        ))

    @app.get("/api/operations/incidents/{incident_id}/vehicle-tracks")
    async def api_vehicle_tracks(
        request: Request, incident_id: str, start: str | None = None, end: str | None = None,
        gap_seconds: int = Query(900, ge=30, le=86400),
    ) -> Response:
        manager: TelemetryManager = request.app.state.telemetry
        return _json(await asyncio.to_thread(manager.tracks, incident_id, start, end, gap_seconds))

    @app.get("/api/operations/incidents/{incident_id}/vehicle-positions/interpolate")
    async def api_vehicle_interpolate(
        request: Request, incident_id: str, at: str, source_id: str, callsign: str,
        maximum_gap_seconds: int = Query(600, ge=1, le=86400),
    ) -> Response:
        manager: TelemetryManager = request.app.state.telemetry
        return _json(await asyncio.to_thread(
            manager.interpolate, incident_id, at, source_id=source_id, callsign=callsign,
            maximum_gap_seconds=maximum_gap_seconds,
        ))

    @app.get("/api/operations/incidents/{incident_id}/drone-missions")
    async def api_drone_missions(request: Request, incident_id: str) -> Response:
        manager: DroneManager = request.app.state.drone
        return _json(await asyncio.to_thread(manager.list_missions, incident_id))

    @app.get("/api/operations/incidents/{incident_id}/sensor-files-manifest")
    async def api_sensor_files_manifest(request: Request, incident_id: str) -> Response:
        manager: DroneManager = request.app.state.drone
        return _json(await asyncio.to_thread(manager.sensor_manifest, incident_id))

    @app.post("/api/operations/incidents/{incident_id}/drone-missions")
    async def api_drone_mission_create(
        request: Request, incident_id: str, body: DroneMissionCreateRequest,
    ) -> Response:
        manager: DroneManager = request.app.state.drone
        result = await asyncio.to_thread(manager.create_mission, incident_id, _model_payload(body), _operator(request))
        return _json(result, 201)

    @app.get("/api/operations/incidents/{incident_id}/drone-missions/{mission_id}/assets")
    async def api_drone_assets(request: Request, incident_id: str, mission_id: str) -> Response:
        manager: DroneManager = request.app.state.drone
        return _json(await asyncio.to_thread(manager.list_assets, incident_id, mission_id))

    @app.post("/api/operations/incidents/{incident_id}/drone-missions/{mission_id}/assets")
    async def api_drone_asset_create(
        request: Request, incident_id: str, mission_id: str,
        filename: str = Query(..., min_length=1, max_length=300),
        captured_at: str | None = None,
        classification: str = Query("operational", max_length=40),
        corners: str | None = None,
        georef_kind: str = Query("", max_length=40),
        metadata: str | None = None,
        attribution: str = Query("", max_length=1000),
    ) -> Response:
        limit = request.app.state.settings.drone_max_upload_mb * 1024 * 1024
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > limit:
                    raise HTTPException(413, "drone image exceeds configured upload limit")
            except ValueError as exc:
                raise HTTPException(400, "invalid Content-Length") from exc
        content = await request.body()
        if len(content) > limit:
            raise HTTPException(413, "drone image exceeds configured upload limit")
        try:
            parsed_corners = json.loads(corners) if corners else None
            parsed_metadata = json.loads(metadata) if metadata else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "corners and metadata must be valid JSON") from exc
        manager: DroneManager = request.app.state.drone
        result = await asyncio.to_thread(
            manager.ingest_image, incident_id, mission_id, filename, content,
            {"captured_at": captured_at, "classification": classification, "corners": parsed_corners,
             "georef_kind": georef_kind, "metadata": parsed_metadata, "attribution": attribution}, _operator(request),
        )
        return _json(result, 201)

    @app.get("/api/operations/incidents/{incident_id}/drone-missions/{mission_id}/assets/{asset_id}/{rendition}")
    async def api_drone_asset_file(
        request: Request, incident_id: str, mission_id: str, asset_id: str, rendition: str,
    ) -> Response:
        manager: DroneManager = request.app.state.drone
        path, media_type, filename = await asyncio.to_thread(
            manager.asset_file, incident_id, mission_id, asset_id, rendition
        )
        return FileResponse(path, media_type=media_type, filename=filename)

    @app.get("/api/operations/incidents/{incident_id}/drone-missions/{mission_id}/mosaics")
    async def api_drone_mosaics(request: Request, incident_id: str, mission_id: str) -> Response:
        manager: DroneManager = request.app.state.drone
        return _json(await asyncio.to_thread(manager.list_mosaics, incident_id, mission_id))

    @app.post("/api/operations/incidents/{incident_id}/drone-missions/{mission_id}/mosaics")
    async def api_drone_mosaic_create(
        request: Request, incident_id: str, mission_id: str, body: DroneMosaicCreateRequest,
    ) -> Response:
        manager: DroneManager = request.app.state.drone
        result = await asyncio.to_thread(
            manager.create_mosaic, incident_id, mission_id, body.name, body.asset_ids, _operator(request)
        )
        return _json(result, 201)

    @app.post("/api/operations/incidents/{incident_id}/resources")
    async def api_resource_create(
        request: Request, incident_id: str, body: ResourceCreateRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        result = await asyncio.to_thread(store.create_resource, incident_id, _model_payload(body), actor)
        return _json(result, 201)

    @app.patch("/api/operations/incidents/{incident_id}/resources/{resource_id}")
    async def api_resource_update(
        request: Request, incident_id: str, resource_id: str, body: ResourceUpdateRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        payload = _model_payload(body, exclude={"expected_revision"})
        result = await asyncio.to_thread(
            store.update_resource, incident_id, resource_id, payload, body.expected_revision, actor
        )
        return _json(result)

    @app.get("/api/operations/incidents/{incident_id}/snapshots")
    async def api_snapshots(request: Request, incident_id: str) -> Response:
        store: OperationsStore = request.app.state.operations
        return _json(await asyncio.to_thread(store.list_snapshots, incident_id))

    @app.post("/api/operations/incidents/{incident_id}/snapshots")
    async def api_snapshot_create(
        request: Request, incident_id: str, body: SnapshotCreateRequest,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        actor = _operator(request)
        result = await asyncio.to_thread(
            store.create_snapshot, incident_id, body.name, body.period_id,
            body.classification, actor,
        )
        return _json(result, 201)

    @app.get("/api/operations/incidents/{incident_id}/snapshots/{left_snapshot_id}/compare")
    async def api_snapshot_compare(
        request: Request, incident_id: str, left_snapshot_id: str,
        right_snapshot_id: str | None = None,
    ) -> Response:
        store: OperationsStore = request.app.state.operations
        result = await asyncio.to_thread(
            store.compare_snapshots, incident_id, left_snapshot_id, right_snapshot_id
        )
        return _json(result)

    @app.get("/api/operations/incidents/{incident_id}/products")
    async def api_products(request: Request, incident_id: str) -> Response:
        manager: ProductManager = request.app.state.products
        rows = await asyncio.to_thread(manager.list, incident_id)
        for row in rows: row["metadata"] = json.loads(row.pop("metadata_json"))
        return _json(rows)

    @app.post("/api/operations/incidents/{incident_id}/products")
    async def api_product_create(
        request: Request, incident_id: str, body: ProductCreateRequest,
    ) -> Response:
        manager: ProductManager = request.app.state.products
        actor = _operator(request)
        result = await asyncio.to_thread(
            manager.create, incident_id, fmt=body.format, classification=body.classification,
            product_type=body.product_type, snapshot_id=body.snapshot_id, actor=actor, title=body.title,
        )
        return _json(result, 201)

    @app.get("/api/operations/incidents/{incident_id}/products/{product_id}/download")
    async def api_product_download(request: Request, incident_id: str, product_id: str) -> Response:
        manager: ProductManager = request.app.state.products
        filename, media_type, content = await asyncio.to_thread(manager.content, incident_id, product_id)
        return Response(content=content, media_type=media_type,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                 "Cache-Control": "no-store"})

    @app.get("/api/operations/incidents/{incident_id}/export")
    async def api_incident_export(request: Request, incident_id: str) -> Response:
        store: OperationsStore = request.app.state.operations
        bundle = await asyncio.to_thread(store.export_bundle, incident_id)
        return Response(
            content=json.dumps(bundle, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="nexfiremap-{incident_id}.json"'},
        )

    # ------------------------------------------------------------ detections

    @app.get("/api/detections")
    async def api_detections(
        request: Request,
        bbox: str | None = Query(None, description="west,south,east,north"),
        days: int = Query(3, ge=1, le=60),
        start: str | None = Query(None, description="ISO date, overrides days"),
        end: str | None = Query(None, description="ISO date"),
        sources: str | None = Query(None),
        confidence: str | None = Query(None, description="low,nominal,high"),
        min_frp: float = Query(0.0, ge=0.0),
        daynight: str | None = Query(None, pattern="^[DNdn]$"),
        limit: int = Query(30000, ge=1, le=250000),
        fmt: str = Query("compact", pattern="^(compact|geojson)$"),
        autofetch: bool = Query(False),
    ) -> Response:
        db: Database = request.app.state.db
        cache: CacheManager = request.app.state.cache

        box = _parse_bbox(bbox)
        wanted_sources = _parse_sources(sources, settings)
        levels = _parse_levels(confidence)

        now = datetime.now(timezone.utc)
        if end:
            try:
                end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise HTTPException(400, "end must be an ISO date") from exc
        else:
            end_dt = now
        if start:
            try:
                start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise HTTPException(400, "start must be an ISO date") from exc
        else:
            start_dt = end_dt - timedelta(days=days)
        # `days`'s own ge=1/le=60 bound only applies when start/end are
        # absent - passing them directly used to impose no range limit at
        # all. Nothing older than cache_days survives retention anyway, so
        # this doesn't lose any real data, just rejects a request that
        # could never have returned more than an empty tail regardless.
        max_span = timedelta(days=max(60, settings.cache_days))
        if end_dt - start_dt > max_span:
            raise HTTPException(
                400, f"start/end span too wide (max {max_span.days} days)"
            )

        queued = None
        if autofetch and box and settings.has_map_key:
            west, south, east, north = box
            area_deg2 = max(0.0, east - west) * max(0.0, north - south)
            if area_deg2 <= DETECTIONS_AUTOFETCH_MAX_AREA_DEG2:
                span_days = max(1, min(settings.cache_days, (now - start_dt).days + 1))
                queued = await cache.ensure_cached(box, span_days, wanted_sources)
            else:
                log.info(
                    "Detections autofetch skipped: viewport too large (%.0f deg^2 > %.0f)",
                    area_deg2,
                    DETECTIONS_AUTOFETCH_MAX_AREA_DEG2,
                )

        rows = await asyncio.to_thread(
            db.query_detections,
            bbox=box,
            start_ts=int(start_dt.timestamp()),
            end_ts=int(end_dt.timestamp()),
            sources=wanted_sources,
            confidence_levels=levels,
            min_frp=min_frp,
            daynight=(daynight or "").upper() or None,
            limit=limit,
        )

        meta = {
            "count": len(rows),
            "truncated": len(rows) >= limit,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "fetch": queued,
            "pending": cache.pending,
        }

        if fmt == "geojson":
            features = [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [row["longitude"], row["latitude"]],
                    },
                    "properties": {
                        "source": row["source"],
                        "satellite": row["satellite"],
                        "instrument": row["instrument"],
                        "acq_date": row["acq_date"],
                        "acq_time": row["acq_time"],
                        "acq_ts": row["acq_ts"],
                        "brightness": row["brightness"],
                        "brightness2": row["brightness2"],
                        "confidence": row["confidence_level"],
                        "confidence_pct": row["confidence_pct"],
                        "frp": row["frp"],
                        "daynight": row["daynight"],
                        "scan": row["scan"],
                        "track": row["track"],
                    },
                }
                for row in rows
            ]
            return _json(
                {"type": "FeatureCollection", "features": features, "meta": meta}
            )

        compact = [
            [
                row["latitude"],
                row["longitude"],
                row["acq_ts"],
                row["frp"],
                row["confidence_level"],
                row["source"],
                row["satellite"],
                row["daynight"],
                row["brightness"],
                row["scan"],
                row["track"],
            ]
            for row in rows
        ]
        return _json({"columns": COMPACT_COLUMNS, "rows": compact, "meta": meta})

    @app.get("/api/summary")
    async def api_summary(
        request: Request,
        bbox: str | None = Query(None),
        days: int = Query(30, ge=1, le=60),
        sources: str | None = Query(None),
    ) -> Response:
        db: Database = request.app.state.db
        box = _parse_bbox(bbox)
        wanted_sources = _parse_sources(sources, settings)

        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=days)
        rows = await asyncio.to_thread(
            db.daily_counts,
            bbox=box,
            start_ts=int(start_dt.timestamp()),
            end_ts=int(end_dt.timestamp()),
            sources=wanted_sources,
        )
        return _json(
            {
                "days": [
                    {
                        "day": row["day"],
                        "count": row["count"],
                        "frp_total": round(row["frp_total"] or 0.0, 1),
                    }
                    for row in rows
                ]
            }
        )

    # -------------------------------------------------------------- coverage

    @app.get("/api/coverage")
    async def api_coverage(
        request: Request,
        bbox: str = Query(..., description="west,south,east,north"),
        day: str | None = Query(None, description="ISO date, default today (UTC)"),
        autofetch: bool = Query(False),
    ) -> Response:
        """Satellite swath coverage for a viewport/day - a geometric
        approximation of "was this area observed", not cloud/quality masked.
        See orbits.py's module docstring for what this does and doesn't mean.
        """
        db: Database = request.app.state.db
        jobs: JobManager = request.app.state.jobs

        box = _parse_bbox(bbox)
        if box is None:
            raise HTTPException(400, "bbox is required")
        day_str = day or datetime.now(timezone.utc).date().isoformat()
        try:
            date.fromisoformat(day_str)
        except ValueError as exc:
            raise HTTPException(400, "day must be an ISO date") from exc

        cell_size = settings.cell_size_deg
        rows = await asyncio.to_thread(db.swath_cells_for_bbox, box, day_str, cell_size)

        # Wide-swath polar orbiters cover nearly the whole globe within a
        # day, so "was this cell seen at all today" is almost always yes -
        # the informative aggregate is the most recent look across every
        # tracked satellite, not the boolean.
        cells: dict[tuple[int, int], dict[str, Any]] = {}
        for row in rows:
            key = (row["cell_x"], row["cell_y"])
            entry = cells.setdefault(
                key, {"satellites": set(), "last_ts": None, "pass_count": 0}
            )
            entry["satellites"].add(row["satellite"])
            entry["pass_count"] += row["pass_count"]
            if entry["last_ts"] is None or row["last_ts"] > entry["last_ts"]:
                entry["last_ts"] = row["last_ts"]

        job_id = None
        if autofetch and orbits is None:
            log.warning(
                "Coverage autofetch requested but the 'orbits' feature is unavailable: %s",
                FEATURE_ERRORS.get("orbits"),
            )
        elif autofetch:
            # "Today" keeps accumulating passes as the day goes on, so a
            # cached computation from an hour ago is stale, not wrong - only
            # today gets a TTL. A fully elapsed past day is cached forever.
            today_str = datetime.now(timezone.utc).date().isoformat()
            missing = []
            for sat in orbits.SATELLITES:
                computed_at = await asyncio.to_thread(db.swath_computed_at, sat, day_str)
                if computed_at is None:
                    missing.append(sat)
                elif day_str == today_str and time.time() - computed_at > 1800:
                    missing.append(sat)
            if missing:
                job_id = await _submit_job(
                    jobs,
                    "swath_coverage",
                    {"day": day_str, "satellites": missing, "cell_size_deg": cell_size},
                )

        now_ts = int(datetime.now(timezone.utc).timestamp())
        features = []
        for (cx, cy), entry in cells.items():
            west = -180.0 + cx * cell_size
            south = -90.0 + cy * cell_size
            east = west + cell_size
            north = south + cell_size
            last_ts = entry["last_ts"]
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [west, south],
                                [east, south],
                                [east, north],
                                [west, north],
                                [west, south],
                            ]
                        ],
                    },
                    "properties": {
                        "satellites": sorted(entry["satellites"]),
                        "pass_count": entry["pass_count"],
                        "last_ts": last_ts,
                        "hours_ago": round((now_ts - last_ts) / 3600, 1)
                        if last_ts
                        else None,
                    },
                }
            )

        return _json(
            {
                "type": "FeatureCollection",
                "features": features,
                "meta": {"day": day_str, "job_id": job_id, "cell_size_deg": cell_size},
            }
        )

    # -------------------------------------------------------------- events

    @app.post("/api/events/detect")
    async def api_events_detect(request: Request, body: EventDetectRequest) -> Response:
        jobs: JobManager = request.app.state.jobs
        box = _parse_bbox(body.bbox)
        if box is None:
            raise HTTPException(400, "bbox is required")

        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=body.days)
        sources = [s.upper() for s in body.sources] if body.sources else None

        job_id = await _submit_job(
            jobs,
            "detect_events",
            {
                "bbox": list(box),
                "start_ts": int(start_dt.timestamp()),
                "end_ts": int(end_dt.timestamp()),
                "sources": sources,
                "v_max_kmh": body.v_max_kmh,
                "max_dt_hours": body.max_dt_hours,
                "min_detections": body.min_detections,
            },
        )
        return _json({"job_id": job_id}, status_code=202)

    @app.get("/api/events")
    async def api_events_list(
        request: Request,
        bbox: str | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
        autofetch: bool = Query(False),
    ) -> Response:
        """Cached events in bbox, returned immediately. With
        ``autofetch=true`` (the map's own viewport reload), also queues
        clustering for a view that's never been checked - "find in view"
        becomes automatic for the common case, same pattern as
        /api/coverage and /api/industrial/sources. Only triggers when this
        bbox has *no* cached events yet: detect_events creates fresh rows
        each run rather than merging into existing ones (see its own
        docstring), so re-triggering over an area that already has events
        would just pile up duplicates, not refresh them."""
        db: Database = request.app.state.db
        jobs: JobManager = request.app.state.jobs
        box = _parse_bbox(bbox)
        rows = await asyncio.to_thread(db.list_events, bbox=box, limit=limit)

        job_id = None
        if autofetch and events is None:
            log.warning(
                "Event autofetch requested but the 'events' feature is unavailable: %s",
                FEATURE_ERRORS.get("events"),
            )
        elif autofetch and box is not None and not rows:
            west, south, east, north = box
            area_deg2 = max(0.0, east - west) * max(0.0, north - south)
            if area_deg2 <= EVENTS_AUTOFETCH_MAX_AREA_DEG2:
                now = datetime.now(timezone.utc)
                start_dt = now - timedelta(days=settings.cache_days)
                job_id = await _submit_job(
                    jobs,
                    "detect_events",
                    {"bbox": list(box), "start_ts": int(start_dt.timestamp()), "end_ts": int(now.timestamp())},
                )

        return _json({"events": [_event_row_to_dict(row) for row in rows], "meta": {"job_id": job_id}})

    @app.get("/api/events/{event_id}")
    async def api_events_get(request: Request, event_id: int) -> Response:
        db: Database = request.app.state.db
        row = await asyncio.to_thread(db.get_event, event_id)
        if row is None:
            raise HTTPException(404, "no such event")
        members = await asyncio.to_thread(db.event_detections, event_id)
        data = _event_row_to_dict(row)
        data["detections"] = [
            {
                "id": m["id"],
                "lat": m["latitude"],
                "lon": m["longitude"],
                "ts": m["acq_ts"],
                "source": m["source"],
                "satellite": m["satellite"],
                "frp": m["frp"],
                "confidence": m["confidence_level"],
            }
            for m in members
        ]
        return _json(data)

    @app.post("/api/events/{event_id}/analyze")
    async def api_events_analyze(
        request: Request, event_id: int, body: EventAnalyzeRequest
    ) -> Response:
        db: Database = request.app.state.db
        jobs: JobManager = request.app.state.jobs
        if await asyncio.to_thread(db.get_event, event_id) is None:
            raise HTTPException(404, "no such event")
        job_id = await _submit_job(
            jobs,
            "analyze_event",
            {
                "event_id": event_id,
                "tau_hours": body.tau_hours,
                "resolution_m": body.resolution_m,
                "reference_ts": body.reference_ts,
            },
        )
        return _json({"job_id": job_id}, status_code=202)

    @app.post("/api/events/{event_id}/burn-scar")
    async def api_events_burn_scar(
        request: Request, event_id: int, body: BurnScarRequest
    ) -> Response:
        db: Database = request.app.state.db
        jobs: JobManager = request.app.state.jobs
        if await asyncio.to_thread(db.get_event, event_id) is None:
            raise HTTPException(404, "no such event")
        job_id = await _submit_job(
            jobs, "analyze_burn_scar", {"event_id": event_id, "resolution_m": body.resolution_m}
        )
        return _json({"job_id": job_id}, status_code=202)

    @app.post("/api/events/{event_id}/propagate")
    async def api_events_propagate(
        request: Request, event_id: int, body: PropagationRequest
    ) -> Response:
        db: Database = request.app.state.db
        jobs: JobManager = request.app.state.jobs
        if await asyncio.to_thread(db.get_event, event_id) is None:
            raise HTTPException(404, "no such event")
        job_id = await _submit_job(
            jobs,
            "run_propagation",
            {
                "event_id": event_id,
                "resolution_m": body.resolution_m,
                "reference_ts": body.reference_ts,
            },
        )
        return _json({"job_id": job_id}, status_code=202)

    @app.post("/api/events/{event_id}/ensemble")
    async def api_events_ensemble(
        request: Request, event_id: int, body: EnsembleRequest
    ) -> Response:
        db: Database = request.app.state.db
        jobs: JobManager = request.app.state.jobs
        if await asyncio.to_thread(db.get_event, event_id) is None:
            raise HTTPException(404, "no such event")
        job_id = await _submit_job(
            jobs,
            "run_ensemble_assimilation",
            {
                "event_id": event_id,
                "resolution_m": body.resolution_m,
                "reference_ts": body.reference_ts,
                "n_members": body.n_members,
                "random_seed": body.random_seed,
            },
        )
        return _json({"job_id": job_id}, status_code=202)

    @app.post("/api/events/{event_id}/validate")
    async def api_events_validate(
        request: Request, event_id: int, body: ValidationRequest
    ) -> Response:
        db: Database = request.app.state.db
        jobs: JobManager = request.app.state.jobs
        if await asyncio.to_thread(db.get_event, event_id) is None:
            raise HTTPException(404, "no such event")
        job_id = await _submit_job(
            jobs,
            "validate_event",
            {"event_id": event_id, "n_splits": body.n_splits, "threshold": body.threshold},
        )
        return _json({"job_id": job_id}, status_code=202)

    # ------------------------------------------------------------ structures

    @app.post("/api/structures/scan")
    async def api_structures_scan(request: Request, body: StructureScanRequest) -> Response:
        if structures is None:
            raise HTTPException(503, "structure exposure module is unavailable")
        box = _parse_bbox(body.bbox)
        if box is None:
            raise HTTPException(400, "bbox is required")
        west, south, east, north = structures.snap_bbox(box)
        area = max(0.0, east - west) * max(0.0, north - south)
        if area > structures.MAX_SCAN_AREA_DEG2:
            raise HTTPException(400, f"structure scan area exceeds {structures.MAX_SCAN_AREA_DEG2} square degrees")
        jobs: JobManager = request.app.state.jobs
        job_id = await _submit_job(jobs, "scan_structures", {"bbox": list(box)})
        return _json({"job_id": job_id}, status_code=202)

    @app.get("/api/structures/exposure")
    async def api_structures_exposure(
        request: Request, job_id: int = Query(..., ge=1), autofetch: bool = Query(False),
    ) -> Response:
        """Evaluate locally cached building footprints against a numeric
        spread-arrival surface. With autofetch, queue one cache-fill job for
        the model bounds; the immediate response remains a local-only read."""
        if structures is None:
            raise HTTPException(503, "structure exposure module is unavailable")
        db: Database = request.app.state.db
        jobs: JobManager = request.app.state.jobs
        job_row = await asyncio.to_thread(db.get_job, job_id)
        if job_row is None:
            raise HTTPException(404, "no such analysis job")
        raw_job = dict(job_row)
        result = json.loads(raw_job.get("result_json") or "{}")
        bounds = result.get("bounds")
        if not bounds or len(bounds) != 2:
            raise HTTPException(400, "analysis job has no spatial bounds")
        box = (float(bounds[0][1]), float(bounds[0][0]), float(bounds[1][1]), float(bounds[1][0]))

        scan_job_id = None
        if autofetch:
            fresh = await asyncio.to_thread(structures.query_cache_is_fresh, db.conn, box)
            if not fresh:
                snapped = structures.snap_bbox(box)
                area = max(0.0, snapped[2] - snapped[0]) * max(0.0, snapped[3] - snapped[1])
                if area <= structures.MAX_SCAN_AREA_DEG2:
                    scan_job_id = await _submit_job(jobs, "scan_structures", {"bbox": list(box)})
        try:
            payload = await asyncio.to_thread(
                structures.assess_exposure, db.conn, raw_job, jobs.job_dir
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        payload["meta"]["scan_job_id"] = scan_job_id
        payload["meta"]["structure_cache_fresh"] = await asyncio.to_thread(
            structures.query_cache_is_fresh, db.conn, box
        )
        return _json(payload)

    # ------------------------------------------------------------ industrial

    @app.post("/api/industrial/scan")
    async def api_industrial_scan(request: Request, body: IndustrialScanRequest) -> Response:
        jobs: JobManager = request.app.state.jobs
        box = _parse_bbox(body.bbox)
        if box is None:
            raise HTTPException(400, "bbox is required")

        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=body.days)
        job_id = await _submit_job(
            jobs,
            "scan_industrial_sources",
            {
                "bbox": list(box),
                "start_ts": int(start_dt.timestamp()),
                "end_ts": int(end_dt.timestamp()),
                "window_days": body.window_days,
                "event_id": body.event_id,
            },
        )
        return _json({"job_id": job_id}, status_code=202)

    @app.get("/api/industrial/sources")
    async def api_industrial_sources(
        request: Request, bbox: str = Query(...), autofetch: bool = Query(False)
    ) -> Response:
        """Cached candidates in bbox, always returned immediately. With
        ``autofetch=true`` (what the map's own viewport reload uses), also
        queues a scan for areas that have never been checked or whose check
        has expired - same "read what's cached, queue what's missing"
        pattern as /api/coverage, so viewing the map is enough - no manual
        button required for the common case. Capped to a modest viewport
        size so panning out to a whole-continent view doesn't quietly queue
        an enormous Overpass query - the manual "scan this view" button
        still works at any size, that's an explicit choice, not a default."""
        db: Database = request.app.state.db
        jobs: JobManager = request.app.state.jobs
        box = _parse_bbox(bbox)
        if box is None:
            raise HTTPException(400, "bbox is required")

        job_id = None
        if autofetch and industrial is None:
            log.warning(
                "Industrial autofetch requested but the 'industrial' feature is unavailable: %s",
                FEATURE_ERRORS.get("industrial"),
            )
        elif autofetch:
            west, south, east, north = box
            area_deg2 = max(0.0, east - west) * max(0.0, north - south)
            if area_deg2 <= INDUSTRIAL_AUTOFETCH_MAX_AREA_DEG2:
                fresh = await asyncio.to_thread(industrial.query_cache_is_fresh, db.conn, box)
                if not fresh:
                    job_id = await _submit_job(jobs, "scan_industrial_sources", {"bbox": list(box)})

        rows = await asyncio.to_thread(db.industrial_sources_in_bbox, box)
        return _json(
            {
                "sources": [
                    {
                        "id": r["id"],
                        "osm_type": r["osm_type"],
                        "osm_id": r["osm_id"],
                        "lat": r["latitude"],
                        "lon": r["longitude"],
                        "evidence_class": r["evidence_class"],
                        "tags": json.loads(r["tags_json"]),
                        "score": r["score"],
                        "classification": r["classification"],
                        "detection_count": r["detection_count"],
                        "match_radius_km": r["match_radius_km"],
                        "computed_at": r["computed_at"],
                    }
                    for r in rows
                ],
                "meta": {"job_id": job_id},
            }
        )

    # ------------------------------------------------------------ eumetsat

    @app.get("/api/eumetsat/fires")
    async def api_eumetsat_fires(
        request: Request, bbox: str = Query(...), autofetch: bool = Query(False)
    ) -> Response:
        """Cached EUMETSAT MTG/FCI active-fire pixels in bbox, always
        returned immediately. With ``autofetch=true``, also queues a scan
        when the most recently ingested product is stale (no area cap: every
        product covers the whole disk regardless of bbox, so cost doesn't
        scale with viewport size the way Overpass/FIRMS fetches do - see
        eumetsat.py's module docstring)."""
        db: Database = request.app.state.db
        jobs: JobManager = request.app.state.jobs
        box = _parse_bbox(bbox)
        if box is None:
            raise HTTPException(400, "bbox is required")

        job_id = None
        if autofetch and eumetsat is None:
            log.warning(
                "EUMETSAT autofetch requested but the 'eumetsat' feature is unavailable: %s",
                FEATURE_ERRORS.get("eumetsat"),
            )
        elif autofetch and not settings.has_eumetsat_key:
            pass  # no account configured - not an error, just nothing to fetch
        elif autofetch:
            latest = await asyncio.to_thread(db.eumetsat_latest_product_end_ts)
            stale = latest is None or (time.time() - latest) > eumetsat.FRESHNESS_WINDOW_S
            if stale:
                job_id = await _submit_job(jobs, "scan_eumetsat_fires", {"bbox": list(box)})

        rows = await asyncio.to_thread(db.eumetsat_fires_in_bbox, box, time.time() - 6 * 3600.0)
        return _json(
            {
                "fires": [
                    {
                        "id": r["id"],
                        "lat": r["latitude"],
                        "lon": r["longitude"],
                        "acq_ts": r["acq_ts"],
                        "confidence": r["confidence"],
                        "probability": r["probability"],
                    }
                    for r in rows
                ],
                "meta": {
                    "job_id": job_id,
                    "available": settings.has_eumetsat_key and eumetsat is not None,
                },
            }
        )

    # ----------------------------------------------------------------- cache

    @app.post("/api/cache/ensure")
    async def api_cache_ensure(request: Request, body: EnsureRequest) -> Response:
        cache: CacheManager = request.app.state.cache
        if not settings.has_map_key:
            raise HTTPException(400, "No FIRMS map key configured (set FIRMS_MAP_KEY).")
        box = _parse_bbox(body.bbox)
        if box is None:
            raise HTTPException(400, "bbox is required")
        sources = body.sources or None
        if sources:
            sources = [s.upper() for s in sources if s.upper() in SOURCES]
        result = await cache.ensure_cached(box, body.days, sources)
        return _json(result)

    @app.post("/api/cache/refresh")
    async def api_cache_refresh(request: Request) -> Response:
        cache: CacheManager = request.app.state.cache
        if not settings.has_map_key:
            raise HTTPException(400, "No FIRMS map key configured (set FIRMS_MAP_KEY).")
        queued = await cache.refresh_cached_regions()
        return _json({"queued": queued, "pending": cache.pending})

    @app.post("/api/cache/purge")
    async def api_cache_purge(request: Request) -> Response:
        cache: CacheManager = request.app.state.cache
        detections, coverage = await asyncio.to_thread(cache.purge_now)
        return _json({"detections_removed": detections, "coverage_removed": coverage})

    # ------------------------------------------------------------------- tiles

    @app.get("/offline-tiles/{source_id}/{z}/{x}/{y}")
    async def offline_tile(request: Request, source_id: str, z: int, x: int, y: int) -> Response:
        manager: OfflineSourceManager = request.app.state.offline_sources
        try:
            result = await asyncio.to_thread(manager.tile, source_id, z, x, y)
        except FileNotFoundError as exc:
            raise HTTPException(404, "offline source not found") from exc
        if result is None:
            raise HTTPException(404, "tile not present in offline source")
        data, media_type = result
        return Response(content=data, media_type=media_type,
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/tiles/{layer_id}/{z}/{x}/{y}.{ext}")
    async def tile(request: Request, layer_id: str, z: int, x: int, y: int, ext: str) -> Response:
        tiles: TileCache = request.app.state.tiles
        meta = tiles.layer(layer_id)
        if meta is None:
            raise HTTPException(404, "unknown layer")
        if not (0 <= z <= 22) or not (0 <= x < (1 << z)) or not (0 <= y < (1 << z)):
            raise HTTPException(400, "tile coordinates out of range")

        # Most tile providers here are PNG - a couple (e.g. Esri's terrain
        # base) actually serve JPEG - per-layer, not guessed from the
        # request, since a mismatched Content-Type is exactly the bug this
        # fixes (see basemaps.py's "tile_ext" comment on esri-terrain).
        media_type = "image/jpeg" if meta.get("tile_ext") == "jpg" else "image/png"

        data = await tiles.get(layer_id, z, x, y)
        if data is None:
            # No stale copy and upstream failed - a transparent PNG still
            # reads as "no tile" over a JPEG layer despite the mismatched
            # type in this one fallback case - a real broken-image icon
            # would be worse.
            return Response(content=TRANSPARENT_PNG, media_type="image/png")

        max_age = min(86400, settings.tile_cache_days * 86400)
        return Response(
            content=data,
            media_type=media_type,
            headers={"Cache-Control": f"public, max-age={max_age}"},
        )

    @app.post("/api/tiles/purge")
    async def api_tiles_purge(request: Request) -> Response:
        tiles: TileCache = request.app.state.tiles
        result = await asyncio.to_thread(tiles.prune_now)
        return _json(result)

    # ------------------------------------------------------------------- jobs

    @app.post("/api/jobs")
    async def api_jobs_submit(request: Request, body: JobSubmitRequest) -> Response:
        jobs: JobManager = request.app.state.jobs
        try:
            job_id = await jobs.submit(body.kind, body.params)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _json({"job_id": job_id}, status_code=202)

    @app.get("/api/jobs/{job_id}")
    async def api_jobs_get(request: Request, job_id: int) -> Response:
        db: Database = request.app.state.db
        row = await asyncio.to_thread(db.get_job, job_id)
        if row is None:
            raise HTTPException(404, "no such job")
        return _json(_job_row_to_dict(row))

    @app.post("/api/jobs/{job_id}/cancel")
    async def api_jobs_cancel(request: Request, job_id: int) -> Response:
        """Cancel a queued or running job. A queued job stops promptly. A
        job already executing in a worker process is marked cancelled once
        its current step finishes (see JobManager.cancel's docstring) - the
        underlying executor future can't be interrupted mid-flight, only
        made to not matter once it does complete."""
        db: Database = request.app.state.db
        jobs: JobManager = request.app.state.jobs
        row = await asyncio.to_thread(db.get_job, job_id)
        if row is None:
            raise HTTPException(404, "no such job")
        cancelled = await jobs.cancel(job_id)
        if not cancelled:
            return _json(
                {
                    "cancelled": False,
                    "reason": (
                        "job is not tracked as queued/running - it already finished, "
                        "or the server restarted since it was submitted"
                    ),
                }
            )
        return _json({"cancelled": True})

    @app.get("/api/jobs")
    async def api_jobs_list(
        request: Request,
        status: str | None = Query(None),
        kind: str | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
    ) -> Response:
        db: Database = request.app.state.db
        rows = await asyncio.to_thread(db.list_jobs, status=status, kind=kind, limit=limit)
        return _json([_job_row_to_dict(row) for row in rows])

    @app.get("/api/jobs/{job_id}/files/{filename}")
    async def api_job_file(request: Request, job_id: int, filename: str) -> Response:
        """Generic static file server for a job's outputs (rasters,
        GeoJSON, ...) - every analysis phase writes to its own job
        directory and links to files here rather than inlining large
        payloads in the job's JSON result."""
        jobs: JobManager = request.app.state.jobs
        safe_name = Path(filename).name  # strip any path components
        # Path.name doesn't strip ".." itself (Path("..").name == ".."), so
        # a bare ".." would otherwise pass through unchanged - reject it
        # explicitly rather than relying only on that pathlib quirk. The
        # is_relative_to check below is the actual backstop - this just
        # gives a clearer 400 for the obvious case instead of a 404.
        if not safe_name or safe_name in (".", ".."):
            raise HTTPException(400, "invalid filename")
        job_root = (jobs.job_dir / str(job_id)).resolve()
        path = (job_root / safe_name).resolve()
        if not path.is_relative_to(job_root):
            raise HTTPException(400, "invalid filename")
        if not path.is_file():
            raise HTTPException(404, "no such job output file")
        if safe_name.endswith(".png"):
            media_type = "image/png"
        elif safe_name.endswith(".geojson") or safe_name.endswith(".json"):
            media_type = "application/geo+json"
        else:
            media_type = "application/octet-stream"
        return FileResponse(
            path, media_type=media_type, headers={"Cache-Control": "public, max-age=604800"}
        )

    # ---------------------------------------------------------------- static

    @app.get("/")
    async def index() -> FileResponse:
        # No explicit header here used to mean "whatever the browser's own
        # heuristic freshness guesses" (FileResponse sets Last-Modified but
        # no Cache-Control), so a client could keep serving a stale app
        # shell after a server update with no signal anything changed,
        # discovered live: editing this exact file mid-session still showed
        # the old markup until a cache-busted reload. no-cache (not
        # no-store) still lets the browser revalidate cheaply via a
        # conditional GET, it just cannot skip asking - same rule already
        # applied to /service-worker.js below for the same reason.
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/service-worker.js")
    async def service_worker() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "service-worker.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


app = create_app()
