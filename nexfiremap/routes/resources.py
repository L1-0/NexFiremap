"""Resource roster (crews, engines, aircraft) and manual position reports.

`/position-reports` is the manual counterpart to the automated feed
ingest in `feeds.py`: same end state, but entered by a dispatcher off a
radio call rather than pushed by a device. It writes in two places on
purpose - see the comments in the handler for why the immutable report
and the mutable "current position" are not in one transaction.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..operations import OperationsStore
from ..schemas import PositionReportRequest, ResourceCreateRequest, ResourceUpdateRequest
from .common import _json, _model_payload, _operator

router = APIRouter()


@router.get("/api/operations/incidents/{incident_id}/resources")
async def api_resources(request: Request, incident_id: str) -> Response:
    store: OperationsStore = request.app.state.operations
    store.get_incident(incident_id)
    return _json(await asyncio.to_thread(store.list_resources, incident_id))


@router.post("/api/operations/incidents/{incident_id}/resources")
async def api_resource_create(
    request: Request, incident_id: str, body: ResourceCreateRequest,
) -> Response:
    store: OperationsStore = request.app.state.operations
    actor = _operator(request)
    result = await asyncio.to_thread(store.create_resource, incident_id, _model_payload(body), actor)
    return _json(result, 201)


@router.patch("/api/operations/incidents/{incident_id}/resources/{resource_id}")
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


@router.post("/api/operations/incidents/{incident_id}/position-reports")
async def api_position_report(
    request: Request, incident_id: str, body: PositionReportRequest,
) -> Response:
    store: OperationsStore = request.app.state.operations
    actor = _operator(request)
    # A position report is always recorded as its own immutable
    # tactical feature (the history of where things have been), and -
    # only when it's tied to a known resource - *also* moves that
    # resource's current position. The revision is read up front (not
    # re-read after create_feature) so the update_resource call below
    # uses an expected_revision from right before this request's own
    # writes, same optimistic-concurrency contract every other
    # PATCH in this file relies on.
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
        # Not wrapped in a single transaction with create_feature above -
        # if another writer bumped this resource's revision in between
        # (e.g. a concurrent manual edit), this raises RevisionConflict
        # and the position report from above has already been
        # committed. That's an accepted tradeoff, not an oversight: the
        # report itself (the append-only history) is what matters most
        # and is never lost even if the "current position" convenience
        # update loses a race.
        await asyncio.to_thread(
            store.update_resource, incident_id, body.resource_id,
            {"latitude": body.latitude, "longitude": body.longitude, "position_at": body.observed_at},
            int(resource_row["revision"]), actor,
        )
    return _json(report, 201)
