"""Derived tactical judgements: assessment, calculators, warning sign-off.

The assessment route reads an incident's features/safety state and
returns the warnings that follow from them; the acknowledgement route is
how an operator records a deliberate decision to proceed despite one, with
a reason - that record is what `approve_scenario` checks against when
`acknowledge_warnings` is set.

`/api/operations/tactical-calculator` is the odd one out: it is a pure
function of its request body (`TacticsManager.calculate` is a
staticmethod), touches no incident and no database, and so takes neither
`Request` nor any app state.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..tactics import TacticsManager
from ..schemas import TacticalCalculatorRequest, TacticalWarningAckRequest
from .common import _json, _operator

router = APIRouter()


@router.get("/api/operations/incidents/{incident_id}/tactical-assessment")
async def api_tactical_assessment(
    request: Request, incident_id: str, period_id: str, scenario_id: str | None = None,
) -> Response:
    manager: TacticsManager = request.app.state.tactics
    return _json(await asyncio.to_thread(manager.assessment, incident_id, period_id, scenario_id))


@router.post("/api/operations/tactical-calculator")
async def api_tactical_calculator(body: TacticalCalculatorRequest) -> Response:
    return _json(TacticsManager.calculate(body.kind, body.inputs))


@router.post("/api/operations/incidents/{incident_id}/tactical-warning-acknowledgements")
async def api_tactical_warning_ack(
    request: Request, incident_id: str, body: TacticalWarningAckRequest,
) -> Response:
    manager: TacticsManager = request.app.state.tactics
    result = await asyncio.to_thread(
        manager.acknowledge, incident_id, body.period_id, body.scenario_id,
        body.warning_id, body.reason, _operator(request),
    )
    return _json(result, 201)
