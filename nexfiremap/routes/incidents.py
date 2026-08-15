"""The incident record itself and the planning hierarchy under it.

An incident owns operational periods; a period owns scenarios; a scenario
is what gets approved and what model runs attach to. Creating an incident
seeds one of each so the frontend never has to render an empty workspace.

Every mutating route here takes an `expected_revision` and passes it to
the store, which raises `RevisionConflict` (a 409 carrying the current
entity, via the app-level `OperationsError` handler in api.py) when
another operator has written since the caller last read. That optimistic
-concurrency contract is the same one the features/resources routers use.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..operations import OperationsStore, default_period
from ..schemas import (
    IncidentCreateRequest,
    IncidentUpdateRequest,
    ModelAttachRequest,
    PeriodCreateRequest,
    PeriodUpdateRequest,
    SafetyUpdateRequest,
    ScenarioApproveRequest,
    ScenarioCopyRequest,
    ScenarioCreateRequest,
    ScenarioUpdateRequest,
)
from .common import _json, _model_payload, _operator

router = APIRouter()


@router.get("/api/operations/incidents")
async def api_incidents(request: Request, include_closed: bool = False) -> Response:
    store: OperationsStore = request.app.state.operations
    return _json(await asyncio.to_thread(store.list_incidents, include_closed))


@router.post("/api/operations/incidents")
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


@router.get("/api/operations/incidents/{incident_id}")
async def api_incident_workspace(request: Request, incident_id: str) -> Response:
    store: OperationsStore = request.app.state.operations
    return _json(await asyncio.to_thread(store.export_bundle, incident_id))


@router.patch("/api/operations/incidents/{incident_id}")
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


@router.post("/api/operations/incidents/{incident_id}/periods")
async def api_period_create(request: Request, incident_id: str, body: PeriodCreateRequest) -> Response:
    store: OperationsStore = request.app.state.operations
    actor = _operator(request)
    result = await asyncio.to_thread(store.create_period, incident_id, _model_payload(body), actor)
    return _json(result, 201)


@router.patch("/api/operations/incidents/{incident_id}/periods/{period_id}")
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


@router.post("/api/operations/incidents/{incident_id}/periods/{period_id}/scenarios")
async def api_scenario_create(
    request: Request, incident_id: str, period_id: str, body: ScenarioCreateRequest,
) -> Response:
    store: OperationsStore = request.app.state.operations
    actor = _operator(request)
    result = await asyncio.to_thread(
        store.create_scenario, incident_id, period_id, _model_payload(body), actor
    )
    return _json(result, 201)


@router.post("/api/operations/incidents/{incident_id}/scenarios/{scenario_id}/copy")
async def api_scenario_copy(
    request: Request, incident_id: str, scenario_id: str, body: ScenarioCopyRequest,
) -> Response:
    store: OperationsStore = request.app.state.operations
    actor = _operator(request)
    result = await asyncio.to_thread(
        store.copy_scenario, incident_id, scenario_id, body.target_period_id, body.name, actor
    )
    return _json(result, 201)


@router.patch("/api/operations/incidents/{incident_id}/scenarios/{scenario_id}")
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


@router.get("/api/operations/incidents/{incident_id}/model-runs")
async def api_model_runs(
    request: Request, incident_id: str, scenario_id: str | None = None,
) -> Response:
    store: OperationsStore = request.app.state.operations
    store.get_incident(incident_id)
    return _json(await asyncio.to_thread(store.list_model_runs, incident_id, scenario_id))


@router.post("/api/operations/incidents/{incident_id}/scenarios/{scenario_id}/model-runs")
async def api_model_run_attach(
    request: Request, incident_id: str, scenario_id: str, body: ModelAttachRequest,
) -> Response:
    store: OperationsStore = request.app.state.operations
    result = await asyncio.to_thread(
        # ..._linked also records the run in the incident's unified link list,
        # so "what is this incident based on" has one answer rather than two.
        store.attach_model_run_linked, incident_id, scenario_id, body.job_id, _operator(request)
    )
    return _json(result, 201)


@router.get("/api/operations/incidents/{incident_id}/periods/{period_id}/safety")
async def api_safety_get(
    request: Request, incident_id: str, period_id: str, scenario_id: str | None = None,
) -> Response:
    store: OperationsStore = request.app.state.operations
    store.get_incident(incident_id)
    return _json(await asyncio.to_thread(store.get_safety_checks, period_id, scenario_id))


@router.put("/api/operations/incidents/{incident_id}/periods/{period_id}/safety")
async def api_safety_update(
    request: Request, incident_id: str, period_id: str, body: SafetyUpdateRequest,
) -> Response:
    store: OperationsStore = request.app.state.operations
    actor = _operator(request)
    result = await asyncio.to_thread(
        store.set_safety_checks, incident_id, period_id, body.scenario_id, body.checks, actor
    )
    return _json(result)


@router.post("/api/operations/incidents/{incident_id}/scenarios/{scenario_id}/approve")
async def api_scenario_approve(
    request: Request, incident_id: str, scenario_id: str, body: ScenarioApproveRequest,
) -> Response:
    store: OperationsStore = request.app.state.operations
    result = await asyncio.to_thread(
        store.approve_scenario, incident_id, scenario_id, body.approver,
        body.acknowledge_warnings, body.expected_revision,
    )
    return _json(result)


@router.get("/api/operations/incidents/{incident_id}/export")
async def api_incident_export(request: Request, incident_id: str) -> Response:
    store: OperationsStore = request.app.state.operations
    bundle = await asyncio.to_thread(store.export_bundle, incident_id)
    return Response(
        content=json.dumps(bundle, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="nexfiremap-{incident_id}.json"'},
    )
