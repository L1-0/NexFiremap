"""Automated position feeds, the derived vehicle views, and wind field.

`POST /api/feeds/positions/{source_id}` is the one route in the whole app
that does not use the session cookie: field devices have no browser
session, so each feed carries its own `X-Feed-Token`, checked inside
`TelemetryManager.ingest`. `SecurityMiddleware` carves this exact
method+prefix out of its gate (see the `feed_ingest` variable there), so
the token check below is the *only* thing standing in front of it - which
is also why the body-size limits here are enforced before any parsing.

Feed administration (create/update/rotate-token) is administrator-only:
handing out or rotating an ingest credential is an account-management
action, not incident data entry, so those three call
`_require_administrator` on top of the middleware's role gate. The read
routes are not restricted that way.

`/wind-field` sits here rather than in its own module because it is a
consumer of the same observation stream - `WindManager` interpolates
recorded wind observations over a grid, the same way `tracks` and
`interpolate` reconstruct movement between recorded fixes.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..telemetry import TelemetryManager
from ..wind import WindManager
from ..schemas import PositionFeedCreateRequest, PositionFeedUpdateRequest
from .common import _json, _model_payload, _operator, _parse_bbox, _require_administrator

router = APIRouter()


@router.get("/api/operations/incidents/{incident_id}/position-feeds")
async def api_position_feeds(request: Request, incident_id: str) -> Response:
    manager: TelemetryManager = request.app.state.telemetry
    return _json(await asyncio.to_thread(manager.list_sources, incident_id))


@router.post("/api/operations/incidents/{incident_id}/position-feeds")
async def api_position_feed_create(
    request: Request, incident_id: str, body: PositionFeedCreateRequest,
) -> Response:
    _require_administrator(request)
    manager: TelemetryManager = request.app.state.telemetry
    result = await asyncio.to_thread(manager.create_source, incident_id, _model_payload(body), _operator(request))
    return _json(result, 201)


@router.patch("/api/operations/incidents/{incident_id}/position-feeds/{source_id}")
async def api_position_feed_update(
    request: Request, incident_id: str, source_id: str, body: PositionFeedUpdateRequest,
) -> Response:
    _require_administrator(request)
    manager: TelemetryManager = request.app.state.telemetry
    result = await asyncio.to_thread(
        manager.update_source, incident_id, source_id, _model_payload(body), _operator(request)
    )
    return _json(result)


@router.post("/api/operations/incidents/{incident_id}/position-feeds/{source_id}/rotate-token")
async def api_position_feed_rotate(request: Request, incident_id: str, source_id: str) -> Response:
    _require_administrator(request)
    manager: TelemetryManager = request.app.state.telemetry
    return _json(await asyncio.to_thread(manager.rotate_token, incident_id, source_id, _operator(request)))


@router.post("/api/feeds/positions/{source_id}")
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


@router.get("/api/operations/incidents/{incident_id}/vehicle-positions/latest")
async def api_vehicle_latest(request: Request, incident_id: str) -> Response:
    manager: TelemetryManager = request.app.state.telemetry
    return _json(await asyncio.to_thread(manager.latest, incident_id))


@router.get("/api/operations/incidents/{incident_id}/wind-field")
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


@router.get("/api/operations/incidents/{incident_id}/vehicle-tracks")
async def api_vehicle_tracks(
    request: Request, incident_id: str, start: str | None = None, end: str | None = None,
    gap_seconds: int = Query(900, ge=30, le=86400),
) -> Response:
    manager: TelemetryManager = request.app.state.telemetry
    return _json(await asyncio.to_thread(manager.tracks, incident_id, start, end, gap_seconds))


@router.get("/api/operations/incidents/{incident_id}/vehicle-positions/interpolate")
async def api_vehicle_interpolate(
    request: Request, incident_id: str, at: str, source_id: str, callsign: str,
    maximum_gap_seconds: int = Query(600, ge=1, le=86400),
) -> Response:
    manager: TelemetryManager = request.app.state.telemetry
    return _json(await asyncio.to_thread(
        manager.interpolate, incident_id, at, source_id=source_id, callsign=callsign,
        maximum_gap_seconds=maximum_gap_seconds,
    ))
