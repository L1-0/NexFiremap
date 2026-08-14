"""Moving whole incidents between servers: import and three-way merge.

Two flows share this module because they are the same operation at
different confidence levels. Import is for a bundle this server has never
seen (preview, then apply). Merge is for a bundle that descends from data
this server already has: `stage` records the incoming package and reports
conflicts, `resolve` applies a per-conflict choice map. `PackageConflict`
is turned into a 409 with its report attached by the app-level
`OperationsError` handler in api.py.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..merge import MergeManager
from ..operations import OperationsStore
from ..schemas import IncidentPackageRequest, MergeResolveRequest, MergeStageRequest
from .common import _json, _operator

router = APIRouter()


@router.post("/api/operations/import/preview")
async def api_import_preview(request: Request, body: IncidentPackageRequest) -> Response:
    store: OperationsStore = request.app.state.operations
    return _json(await asyncio.to_thread(store.preview_import, body.bundle))


@router.post("/api/operations/import/apply")
async def api_import_apply(request: Request, body: IncidentPackageRequest) -> Response:
    store: OperationsStore = request.app.state.operations
    actor = _operator(request)
    result = await asyncio.to_thread(store.import_bundle, body.bundle, actor)
    return _json(result, 201)


@router.post("/api/operations/merge/stage")
async def api_merge_stage(request: Request, body: MergeStageRequest) -> Response:
    manager: MergeManager = request.app.state.merges
    actor = _operator(request)
    return _json(await asyncio.to_thread(manager.stage, body.bundle, actor), 201)


@router.get("/api/operations/incidents/{incident_id}/merge-packages")
async def api_merge_packages(request: Request, incident_id: str) -> Response:
    manager: MergeManager = request.app.state.merges
    return _json(await asyncio.to_thread(manager.list, incident_id))


@router.post("/api/operations/merge/{package_id}/resolve")
async def api_merge_resolve(
    request: Request, package_id: str, body: MergeResolveRequest,
) -> Response:
    manager: MergeManager = request.app.state.merges
    actor = _operator(request)
    return _json(await asyncio.to_thread(manager.resolve, package_id, body.choices, actor))
