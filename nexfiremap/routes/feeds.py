"""Automated position feeds, the derived vehicle views, and wind field.

`POST /api/feeds/positions/{source_id}` is the one route in the whole app
that does not use the session cookie: field devices have no browser
session, so each feed carries its own `X-Feed-Token`, checked inside
`TelemetryManager.ingest`. `SecurityMiddleware` carves this exact
method+prefix out of its gate (see the `feed_ingest` variable there), so
the token check below is the *only* thing standing in front of it - which
is also why the body-size limits here are enforced before any parsing.
That ordering matters more now that the endpoint accepts formats other
than JSON: the `Content-Type` header selects a parser from
`nexfiremap.ingest` (CoT XML, NMEA, ...), and an XML parser in particular
must never be handed an unbounded body from an unauthenticated caller.
Every parser returns the same position-report shape and feeds the same
`ingest` call, so the extra formats gain no privileges the JSON path
does not already have.

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
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .. import ingest
from ..ingest import cot
from ..operations import OperationsStore
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

    # Which parser runs is decided by Content-Type; where the result goes is
    # not. Every branch below produces the same list-of-position-reports and
    # hands it to the same `TelemetryManager.ingest` call, so a CoT or NMEA
    # sender inherits the token check, per-source rate limit, replay-safety
    # hash and derived quality flags identically to a native JSON batch -
    # a new protocol is a new parser, never a new write path.
    adapter = ingest.adapter_for_media_type(
        request.headers.get("content-type", ""), contract=ingest.CONTRACT_POSITION,
    )
    if adapter is not None:
        try:
            # NMEA and MAVLink carry no identity of their own - the sentence
            # says where something is, not what - so the caller supplies one.
            # `?callsign=` lets several devices share one feed source; the
            # source id is the fallback when only one does.
            reports = adapter.parse(raw, callsign=request.query_params.get(
                "callsign", "") or f"feed-{source_id[:8]}")
        except ingest.IngestError as exc:
            raise HTTPException(400, f"{adapter.NAME} feed body is invalid: {exc}") from exc
    else:
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


@router.api_route("/api/feeds/positions/{source_id}/osmand", methods=["GET", "POST"])
async def api_position_feed_osmand(request: Request, source_id: str) -> Response:
    """The OsmAnd/Traccar tracker protocol - one position per request.

    Worth a dedicated endpoint because it is the closest thing consumer and
    low-end fleet GPS hardware has to a common protocol: OsmAnd itself, dozens
    of Android/iOS tracker apps, and a Traccar server configured to forward all
    speak it. Supporting it means a department can point existing hardware at
    NexFiremap by changing a URL, with nothing to install.

    The protocol is a bare HTTP GET with query parameters and no body, which is
    why `SecurityMiddleware` carves out GET on this exact path and why the
    credential may travel as `?token=`: this hardware cannot set a header. The
    token is still checked by `TelemetryManager.ingest` exactly as for every
    other feed - the transport is weaker, the authorisation is not.
    """
    parameters = dict(request.query_params)
    if request.method == "POST":
        # Some clients POST the same fields as a form body instead. Query
        # parameters still win, so a client sending both is unambiguous.
        raw = await request.body()
        if len(raw) > 64 * 1024:
            raise HTTPException(413, "OsmAnd position exceeds 64 KiB")
        if raw:
            parameters = {**dict(parse_qsl(raw.decode("utf-8", "replace"))), **parameters}

    try:
        reports = ingest.osmand_report(parameters, default_callsign=f"feed-{source_id[:8]}")
    except ingest.IngestError as exc:
        raise HTTPException(400, f"OsmAnd position is invalid: {exc}") from exc

    manager: TelemetryManager = request.app.state.telemetry
    token = request.headers.get("X-Feed-Token", "") or parameters.get("token", "")
    try:
        result = await asyncio.to_thread(manager.ingest, source_id, token, reports)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    return _json(result, 202)


@router.get("/api/operations/incidents/{incident_id}/cot")
async def api_incident_cot(
    request: Request, incident_id: str, include_positions: bool = True,
    include_features: bool = True, stale_seconds: int = Query(300, ge=30, le=86400),
) -> Response:
    """The incident as Cursor on Target, for a TAK client to poll.

    The outbound half of TAK interoperability: a team's ATAK devices can pull
    the current tactical picture and vehicle positions without NexFiremap
    needing a push connection to their server. Polling is deliberately the
    first-class path - it works through the NAT and firewall arrangements a
    temporary incident network actually has, where an inbound federation
    connection generally does not.

    Positions are rendered as friendly-ground-vehicle atoms and features carry
    a `__nexfiremap` detail element, so a device that later sends one back gets
    the same feature updated rather than a duplicate created (see
    `ingest/cot.py`'s `is_atom`).
    """
    store: OperationsStore = request.app.state.operations
    telemetry: TelemetryManager = request.app.state.telemetry
    events = []
    if include_features:
        features = await asyncio.to_thread(store.list_features, incident_id)
        events.extend(cot.render_feature(feature, stale_seconds=stale_seconds)
                      for feature in features)
    if include_positions:
        latest = await asyncio.to_thread(telemetry.latest, incident_id)
        events.extend(cot.render_position(feature, stale_seconds=stale_seconds)
                      for feature in latest["features"])
    return Response(content=cot.serialize(events), media_type="application/xml",
                    headers={"Cache-Control": "no-store"})


@router.get("/api/feeds/cot/status")
async def api_cot_gateway_status(request: Request) -> Response:
    """Whether the streaming CoT/MAVLink listener is up and hearing anything.

    "No markers on the map" and "nothing has ever reached the port" look
    identical to an operator otherwise, and the difference - a firewall, a
    wrong allowlist, a rotated token - is exactly what needs diagnosing.
    """
    return _json(request.app.state.cot_gateway.status())


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
