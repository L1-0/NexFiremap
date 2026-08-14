"""Helpers and optional-feature bindings shared by every router module.

Two things live here rather than in `api.py`:

1. The graceful-degradation import of the optional analysis modules
   (`orbits`, `events`, ... - see `_OPTIONAL_MODULES`). Several routers
   need to know whether a given feature imported successfully, and
   `api.py` imports the routers, so putting the table here is what keeps
   the dependency one-directional (`api` -> `routes` -> `routes.common`)
   instead of circular.
2. The small request/response helpers (`_json`, `_parse_bbox`, ...) that
   more than one router uses.

Routers reach the optional modules through this module's namespace -
``from . import common`` then ``common.orbits`` - never by importing the
names directly. That is deliberate and applies uniformly: several router
modules are themselves named after a feature (`routes/events.py`,
`routes/structures.py`, `routes/industrial.py`), so a bare ``from
.common import events`` would bind a global that reads exactly like the
router's own module name. Going through `common.` makes every such
reference unambiguous at the point of use.
"""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ..cache import clamp_bbox
from ..config import SOURCES, Settings
from ..jobs import JobManager

# Deliberately the same logger name the routes used when they all lived in
# api.py, so existing log filters/handlers keyed on "nexfiremap.api" keep
# matching the same messages.
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
        # ".." because this module lives in `nexfiremap.routes` but the
        # optional modules are siblings of `nexfiremap.api`, one level up.
        _optional[_name] = importlib.import_module(f"..{_name}", __package__)
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
# still write e.g. `orbits.SATELLITES` the same way a plain `from .. import
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
