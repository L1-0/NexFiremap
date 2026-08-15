"""Cross-module links, the incident AOI, and incident-from-context creation.

These are the routes that join NexFiremap's analytical half (detections,
events, model runs, alerts) to its operational half (incidents, scenarios,
resources). Before them, the only path between the two was an operator reading
coordinates off one panel and typing them into another.

`POST /api/operations/incidents/from_context` is the one that earns its keep:
it turns a right-click on an event polygon into a fully-formed incident -
record, first operational period, first scenario, area of interest, and a
snapshot link back to what was observed - in a single transaction. That is
roughly five minutes of an incident commander's first ten replaced by one
click, at the exact moment they have least attention to spare.

Everything written here obeys the rule in `operations/links.py`: what crosses
between the two halves is an immutable snapshot with provenance, never a live
reference.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..operations import OperationsStore, default_period, normalise_aoi
from ..schemas import IncidentFromContextRequest, IncidentLinkRequest, IncidentAoiRequest
from .common import _json, _model_payload, _operator, _parse_bbox

router = APIRouter()


@router.get("/api/operations/incidents/{incident_id}/links")
async def api_incident_links(request: Request, incident_id: str, kind: str | None = None) -> Response:
    store: OperationsStore = request.app.state.operations
    return _json(await asyncio.to_thread(store.list_links, incident_id, kind))


@router.post("/api/operations/incidents/{incident_id}/links")
async def api_incident_link_create(
    request: Request, incident_id: str, body: IncidentLinkRequest,
) -> Response:
    """Attach an observation or analysis result to this incident.

    Idempotent: linking the same referent twice refreshes the snapshot rather
    than accumulating rows, because "attach to incident" is a button an
    operator will press again.
    """
    store: OperationsStore = request.app.state.operations
    link = await asyncio.to_thread(
        store.add_link, incident_id, body.kind, body.ref_id,
        body.snapshot, body.note, _operator(request))
    return _json(link, 201)


@router.delete("/api/operations/incidents/{incident_id}/links/{link_id}")
async def api_incident_link_delete(request: Request, incident_id: str, link_id: str) -> Response:
    store: OperationsStore = request.app.state.operations
    if not await asyncio.to_thread(store.remove_link, incident_id, link_id, _operator(request)):
        raise HTTPException(404, "link not found")
    return _json({"deleted": True, "id": link_id})


@router.get("/api/operations/incidents/{incident_id}/aoi")
async def api_incident_aoi(request: Request, incident_id: str) -> Response:
    store: OperationsStore = request.app.state.operations
    return _json({"incident_id": incident_id, "aoi": await asyncio.to_thread(store.get_aoi, incident_id)})


@router.put("/api/operations/incidents/{incident_id}/aoi")
async def api_incident_aoi_set(request: Request, incident_id: str, body: IncidentAoiRequest) -> Response:
    """Set or clear the incident's area of interest.

    PUT rather than PATCH because this replaces the AOI wholesale - there is no
    partial update of a polygon - and because sending `null` to clear it is
    then unambiguous rather than indistinguishable from "field omitted".
    """
    store: OperationsStore = request.app.state.operations
    geometry = body.aoi if body.aoi is not None else (body.bbox if body.bbox else None)
    return _json(await asyncio.to_thread(store.set_aoi, incident_id, geometry, _operator(request)))


@router.get("/api/operations/incidents/covering")
async def api_incidents_covering(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    active_only: bool = True,
) -> Response:
    """Which incidents' AOIs contain this point.

    The reverse lookup that makes an AOI operational rather than decorative:
    it answers "does this new detection concern anyone?" - which is what the
    watch evaluation and the situation panel both need.
    """
    store: OperationsStore = request.app.state.operations
    return _json(await asyncio.to_thread(store.incidents_covering, lat, lon, active_only=active_only))


def _event_snapshot(row: Any) -> dict[str, Any]:
    """Freeze what an event looked like at link time.

    Event ids are *not* stable across clustering runs and the row itself will
    be rewritten by the next one, so this is the only representation that
    survives - see `operations/links.py`.
    """
    return {
        "event_id": row["id"],
        "bbox": [row["bbox_west"], row["bbox_south"], row["bbox_east"], row["bbox_north"]],
        "centroid": [row["centroid_lon"], row["centroid_lat"]],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "detection_count": row["detection_count"],
    }


@router.post("/api/operations/incidents/from_context")
async def api_incident_from_context(request: Request, body: IncidentFromContextRequest) -> Response:
    """Create a fully-formed incident from something on the map.

    Accepts an `event_id`, a `bbox`, or a `lat`/`lon` with a radius, and
    produces the incident, its first operational period, its first scenario,
    its AOI and a snapshot link back to the source - mirroring what
    `api_incident_create` already seeds so the frontend never meets an empty
    workspace.

    The name is derived from the reverse geocoder when the caller does not
    supply one ("Wildfire near Ismaning"), which is the difference between an
    incident list of place names and one of "Untitled 3".
    """
    store: OperationsStore = request.app.state.operations
    actor = _operator(request)

    aoi: dict[str, Any] | None = None
    link: tuple[str, str, dict[str, Any]] | None = None
    centre: tuple[float, float] | None = None

    if body.event_id is not None:
        row = request.app.state.db.conn.execute(
            "SELECT * FROM events WHERE id=?", (int(body.event_id),)).fetchone()
        if row is None:
            raise HTTPException(404, "event not found")
        snapshot = _event_snapshot(row)
        west, south, east, north = snapshot["bbox"]
        aoi = normalise_aoi([west, south, east, north])
        centre = (float(row["centroid_lat"]), float(row["centroid_lon"]))
        link = ("event", str(row["id"]), snapshot)
    elif body.bbox:
        parsed = _parse_bbox(body.bbox)
        if parsed is None:
            raise HTTPException(400, "bbox must be 'west,south,east,north'")
        west, south, east, north = parsed
        aoi = normalise_aoi([west, south, east, north])
        centre = ((south + north) / 2, (west + east) / 2)
        # A dragged rectangle is an observation too: record the window it was
        # taken over, so the incident says what its AOI was drawn from.
        link = ("detection_window", f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}",
                {"bbox": [west, south, east, north], "days": body.days,
                 "selected_at": int(time.time())})
    elif body.lat is not None and body.lon is not None:
        # A radius in kilometres converted to a degree box. Longitude is
        # divided by cos(latitude) because a degree of longitude narrows
        # towards the poles; without it the AOI would be an ellipse squashed
        # in the wrong axis, increasingly so at higher latitudes.
        import math

        radius = max(0.1, min(200.0, float(body.radius_km or 5.0)))
        lat_span = radius / 110.574
        lon_span = radius / (111.320 * max(0.01, math.cos(math.radians(body.lat))))
        aoi = normalise_aoi([body.lon - lon_span, body.lat - lat_span,
                             body.lon + lon_span, body.lat + lat_span])
        centre = (body.lat, body.lon)
    else:
        raise HTTPException(400, "one of event_id, bbox, or lat+lon is required")

    name = (body.name or "").strip()
    if not name and centre is not None:
        # Best-effort only: the geocoder needs the WAN, and an incident must be
        # creatable with the link down. A failed lookup falls back to
        # coordinates rather than blocking creation.
        try:
            place = await request.app.state.geocode.reverse(centre[0], centre[1])
            label = (place.get("result") or {}).get("label") if isinstance(place, dict) else None
            if label:
                name = f"Wildfire near {str(label).split(',')[0].strip()}"
        except Exception:  # noqa: BLE001 - naming must never block creation
            name = ""
    if not name:
        name = f"Wildfire {centre[0]:.3f}, {centre[1]:.3f}" if centre else "Wildfire"

    def build() -> dict[str, Any]:
        incident = store.create_incident(
            {"name": name[:300], "center_lat": centre[0] if centre else None,
             "center_lon": centre[1] if centre else None,
             "notes": body.notes or ""}, actor)
        period = store.create_period(incident["id"], default_period(), actor)
        scenario = store.create_scenario(
            incident["id"], period["id"],
            {"name": "Plan A", "kind": "primary", "description": "Primary operational plan"}, actor)
        if aoi is not None:
            store.set_aoi(incident["id"], aoi, actor)
        if link is not None:
            store.add_link(incident["id"], link[0], link[1], link[2],
                           "created from map context", actor)
        return {"incident": {**incident, "aoi_json": None}, "period": period, "scenario": scenario,
                "aoi": aoi, "links": store.list_links(incident["id"])}

    return _json(await asyncio.to_thread(build), 201)


@router.get("/api/situation")
async def api_situation(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(5.0, gt=0, le=50),
) -> Response:
    """Everything every module knows about one point.

    The answer an operator actually wants when they right-click an unfamiliar
    spot: what is burning near here, what event it belongs to, when the model
    says fire reaches this point, what is at stake, who is nearby, and when a
    satellite will next look.

    A provider that cannot run is *named* in the response rather than silently
    omitted - see `situation.py`. "We did not ask" and "there is nothing there"
    must never look the same to someone deciding whether to send a crew.
    """
    from ..situation import report

    return _json(await asyncio.to_thread(report, request.app, lat, lon, radius_km))


@router.get("/api/operations/incidents/{incident_id}/safety-warnings")
async def api_safety_warnings(
    request: Request,
    incident_id: str,
    since: str | None = Query(None, description="ISO timestamp; only entries after it"),
    limit: int = Query(50, ge=1, le=500),
) -> Response:
    """Recent automatic safety warnings and AOI-watch hits for one incident.

    The read side of the loop `safety.py` has been running all along. Every
    accepted position is checked against hazard areas and against the arrival
    band of the scenario's adopted model, and every new detection is checked
    against active AOIs - and all of it was written to an audit log with no
    reader. The most safety-relevant code in the product was addressed to
    nobody: "L-1 is inside the forecast perimeter" existed only in a table and
    in the HTTP response handed back to the *device* that posted the position.

    Both kinds come from one query because they answer the same operator
    question - what has the system noticed that I have not? - and they are
    already interleaved in one trail in the order they happened.
    """
    store: OperationsStore = request.app.state.operations
    store.get_incident(incident_id)  # 404s rather than returning an empty list for a bad id

    # Repair the one URL-encoding slip these timestamps invite. `utcnow()` emits
    # "+00:00", and a "+" that reaches a query string unencoded arrives as a
    # space - so "…T10:00:00 00:00" is compared against stored "…T10:00:00+00:00"
    # and, because space sorts below "+", every entry at that exact instant
    # comes back again on the next poll. Silently repeating a safety warning
    # forever is worse than the caller's mistake, and a space is never valid
    # there, so the intent is unambiguous.
    if since and " " in since:
        since = since.replace(" ", "+")

    entries = await asyncio.to_thread(
        store.audit_log.recent, incident_id,
        entity_types=("safety_warning", "aoi_watch"), since=since, limit=limit,
    )
    return _json({
        "incident_id": incident_id,
        "entries": entries,
        # Split out so a caller can badge the two independently without
        # re-deriving the classification the server already knows.
        "warning_count": sum(1 for e in entries if e["entity_type"] == "safety_warning"),
        "watch_count": sum(1 for e in entries if e["entity_type"] == "aoi_watch"),
    })


@router.get("/api/operations/watch")
async def api_aoi_watch(
    request: Request,
    since_ts: float = Query(..., description="Unix seconds; detections newer than this"),
) -> Response:
    """New detections that landed inside an active incident's area of interest.

    The question an incident commander should not have to ask by staring at the
    map: has anything new appeared in my area since I last looked? Returns one
    entry per incident rather than per detection - "14 new detections inside
    your AOI" is actionable, fourteen separate notices are noise.

    The caller supplies `since_ts` (the frontend passes the timestamp of its
    last poll) so this stays a cheap incremental check on the existing refresh
    cycle rather than a rescan.
    """
    from ..safety import record_watch, watch_detections

    store: OperationsStore = request.app.state.operations

    def evaluate() -> list[dict[str, Any]]:
        hits = watch_detections(request.app.state.db, store, since_ts)
        record_watch(store, hits)
        return hits

    return _json({"since_ts": since_ts, "hits": await asyncio.to_thread(evaluate)})
