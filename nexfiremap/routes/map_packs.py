"""Offline map-pack manifests: build, list, verify, delete.

`MapPackError` is turned into a 400 by an app-level exception handler in
api.py, so nothing here catches it; only `FileNotFoundError` (an unknown
manifest id) needs mapping, to a 404.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..map_packs import MapPackManager
from ..schemas import MapPackCreateRequest
from .common import _json

router = APIRouter()


@router.get("/api/operations/map-packs")
async def api_map_packs(request: Request) -> Response:
    manager: MapPackManager = request.app.state.map_packs
    return _json(await asyncio.to_thread(manager.list))


@router.post("/api/operations/map-packs")
async def api_map_pack_create(request: Request, body: MapPackCreateRequest) -> Response:
    manager: MapPackManager = request.app.state.map_packs
    result = await asyncio.to_thread(
        manager.create, body.name, body.bbox, body.layers, body.min_zoom, body.max_zoom
    )
    return _json(result, 201)


@router.get("/api/operations/map-packs/{manifest_id}")
async def api_map_pack_get(request: Request, manifest_id: str) -> Response:
    manager: MapPackManager = request.app.state.map_packs
    try:
        result = await asyncio.to_thread(manager.load, manifest_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "map-pack manifest not found") from exc
    return _json(result)


@router.post("/api/operations/map-packs/{manifest_id}/verify")
async def api_map_pack_verify(request: Request, manifest_id: str) -> Response:
    manager: MapPackManager = request.app.state.map_packs
    try:
        result = await asyncio.to_thread(manager.verify, manifest_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "map-pack manifest not found") from exc
    return _json(result)


@router.delete("/api/operations/map-packs/{manifest_id}")
async def api_map_pack_delete(request: Request, manifest_id: str) -> Response:
    manager: MapPackManager = request.app.state.map_packs
    try:
        result = await asyncio.to_thread(manager.delete, manifest_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "map-pack manifest not found") from exc
    return _json(result)
