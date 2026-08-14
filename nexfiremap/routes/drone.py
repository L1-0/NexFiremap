"""Drone missions, their captured imagery assets, and derived mosaics.

Asset upload takes its metadata as query parameters and the image itself
as the raw request body (not multipart) - that keeps the size check
cheap: the Content-Length header is rejected before the body is read at
all, and the read is bounded again afterwards in case the header lied.
The limit is per-install (`settings.drone_max_upload_mb`), which is why
it is read off app state rather than being a module constant like the
telemetry batch cap.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from ..drone import DroneManager
from ..schemas import DroneMissionCreateRequest, DroneMosaicCreateRequest
from .common import _json, _model_payload, _operator

router = APIRouter()


@router.get("/api/operations/incidents/{incident_id}/drone-missions")
async def api_drone_missions(request: Request, incident_id: str) -> Response:
    manager: DroneManager = request.app.state.drone
    return _json(await asyncio.to_thread(manager.list_missions, incident_id))


@router.get("/api/operations/incidents/{incident_id}/sensor-files-manifest")
async def api_sensor_files_manifest(request: Request, incident_id: str) -> Response:
    manager: DroneManager = request.app.state.drone
    return _json(await asyncio.to_thread(manager.sensor_manifest, incident_id))


@router.post("/api/operations/incidents/{incident_id}/drone-missions")
async def api_drone_mission_create(
    request: Request, incident_id: str, body: DroneMissionCreateRequest,
) -> Response:
    manager: DroneManager = request.app.state.drone
    result = await asyncio.to_thread(manager.create_mission, incident_id, _model_payload(body), _operator(request))
    return _json(result, 201)


@router.get("/api/operations/incidents/{incident_id}/drone-missions/{mission_id}/assets")
async def api_drone_assets(request: Request, incident_id: str, mission_id: str) -> Response:
    manager: DroneManager = request.app.state.drone
    return _json(await asyncio.to_thread(manager.list_assets, incident_id, mission_id))


@router.post("/api/operations/incidents/{incident_id}/drone-missions/{mission_id}/assets")
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


@router.get("/api/operations/incidents/{incident_id}/drone-missions/{mission_id}/assets/{asset_id}/{rendition}")
async def api_drone_asset_file(
    request: Request, incident_id: str, mission_id: str, asset_id: str, rendition: str,
) -> Response:
    manager: DroneManager = request.app.state.drone
    path, media_type, filename = await asyncio.to_thread(
        manager.asset_file, incident_id, mission_id, asset_id, rendition
    )
    return FileResponse(path, media_type=media_type, filename=filename)


@router.get("/api/operations/incidents/{incident_id}/drone-missions/{mission_id}/mosaics")
async def api_drone_mosaics(request: Request, incident_id: str, mission_id: str) -> Response:
    manager: DroneManager = request.app.state.drone
    return _json(await asyncio.to_thread(manager.list_mosaics, incident_id, mission_id))


@router.post("/api/operations/incidents/{incident_id}/drone-missions/{mission_id}/mosaics")
async def api_drone_mosaic_create(
    request: Request, incident_id: str, mission_id: str, body: DroneMosaicCreateRequest,
) -> Response:
    manager: DroneManager = request.app.state.drone
    result = await asyncio.to_thread(
        manager.create_mosaic, incident_id, mission_id, body.name, body.asset_ids, _operator(request)
    )
    return _json(result, 201)
