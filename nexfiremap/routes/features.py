"""Tactical map features - the drawn/observed geometry of an incident.

Everything an operator puts on the map (fire perimeters, control lines,
hazards, resource positions) is a feature row, which is why this is the
busiest write path in the incident domain. Deletes are soft (the store
marks the row rather than removing it), so `include_deleted=true` on the
list route is what makes progression replay possible - and why
`/progression` lives here rather than with the incident metadata: it is a
time-sliced read over exactly this table.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ..operations import OperationsStore
from ..schemas import TacticalFeatureCreateRequest, TacticalFeatureUpdateRequest
from .common import _json, _model_payload, _operator

router = APIRouter()


@router.get("/api/operations/incidents/{incident_id}/features")
async def api_features(
    request: Request, incident_id: str, period_id: str | None = None,
    scenario_id: str | None = None, include_deleted: bool = False,
) -> Response:
    store: OperationsStore = request.app.state.operations
    features = await asyncio.to_thread(
        store.list_features, incident_id, period_id, scenario_id, include_deleted
    )
    return _json({"type": "FeatureCollection", "features": features})


@router.get("/api/operations/incidents/{incident_id}/progression")
async def api_progression(
    request: Request, incident_id: str, from_time: str, to_time: str,
) -> Response:
    store: OperationsStore = request.app.state.operations
    return _json(await asyncio.to_thread(store.progression, incident_id, from_time, to_time))


@router.post("/api/operations/incidents/{incident_id}/features")
async def api_feature_create(
    request: Request, incident_id: str, body: TacticalFeatureCreateRequest,
) -> Response:
    store: OperationsStore = request.app.state.operations
    actor = _operator(request)
    result = await asyncio.to_thread(store.create_feature, incident_id, _model_payload(body), actor)
    return _json(result, 201)


@router.patch("/api/operations/incidents/{incident_id}/features/{feature_id}")
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


@router.delete("/api/operations/incidents/{incident_id}/features/{feature_id}")
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
