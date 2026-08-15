"""Operator-added map layers: discovery, registration and removal.

All four routes are administrator-only. That is a stronger gate than most
write routes get, and it is deliberate: `POST /api/layers/probe` makes the
*server* fetch an arbitrary operator-supplied URL, and a registered layer
makes it keep doing so on every tile request. That is a server-side
request-forgery surface, so it is restricted to the one role that is already
trusted with account management. `LayerRegistry._require_http_url` is the
second half of that defence - the role gate decides who may ask, the scheme
check decides what the server will connect to.

Note what is *not* here: nothing to serve a custom layer's tiles. That is
`routes/tiles.py`'s existing `/tiles/{layer_id}/{z}/{x}/{y}.{ext}` route,
unchanged - a registered layer is resolved through the same
`TileCache.layer()` lookup as a built-in one, so it inherits the proxy, the
disk cache, the offline map packs and the LRU pinning without a route of its
own.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..layers import LayerRegistry
from ..schemas import CustomLayerCreateRequest, CustomLayerProbeRequest, CustomLayerUpdateRequest
from .common import _json, _model_payload, _operator, _require_administrator

router = APIRouter()


@router.get("/api/layers")
async def api_layers(request: Request, include_inactive: bool = False) -> Response:
    """Every registered layer, in full definition form.

    Administrator-only even though it is a read: the stored rows carry
    endpoint URLs that may embed an access token in their query string, which
    the sanitised `/api/config` view deliberately does not expose.
    """
    _require_administrator(request)
    registry: LayerRegistry = request.app.state.layers
    return _json(await asyncio.to_thread(registry.list, include_inactive=include_inactive))


@router.post("/api/layers/probe")
async def api_layer_probe(request: Request, body: CustomLayerProbeRequest) -> Response:
    """Fetch a service's GetCapabilities and list what it offers.

    Exists so the UI can present a picker rather than asking an operator to
    transcribe a layer name out of an XML document - a typo there produces
    blank tiles with no error message, which is close to undebuggable in the
    field.
    """
    _require_administrator(request)
    registry: LayerRegistry = request.app.state.layers
    return _json(await registry.probe(body.endpoint, kind=body.kind))


@router.post("/api/layers")
async def api_layer_create(request: Request, body: CustomLayerCreateRequest) -> Response:
    _require_administrator(request)
    registry: LayerRegistry = request.app.state.layers
    record = await asyncio.to_thread(registry.create, _model_payload(body), _operator(request))
    return _json(record, 201)


@router.patch("/api/layers/{layer_id}")
async def api_layer_update(request: Request, layer_id: str, body: CustomLayerUpdateRequest) -> Response:
    _require_administrator(request)
    registry: LayerRegistry = request.app.state.layers
    return _json(await asyncio.to_thread(registry.update, layer_id, _model_payload(body)))


@router.delete("/api/layers/{layer_id}")
async def api_layer_delete(request: Request, layer_id: str) -> Response:
    """Forget a layer definition.

    Its already-cached tiles stay on disk: they are bounded by the cache's own
    TTL and size budget, and a map pack built for a running incident may still
    be pinning them. Removing a layer from the switcher must not delete
    operational data as a side effect.
    """
    _require_administrator(request)
    registry: LayerRegistry = request.app.state.layers
    if not await asyncio.to_thread(registry.delete, layer_id):
        raise HTTPException(404, "custom layer not found")
    return _json({"deleted": True, "id": layer_id})
