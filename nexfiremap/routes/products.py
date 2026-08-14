"""Snapshots (frozen incident state) and the products rendered from them.

The two belong together: a snapshot is the immutable input a product is
generated from, so an operator takes a snapshot, then exports it as
GeoJSON/PDF/GeoTIFF/... at a chosen classification. `compare` diffs two
snapshots, which is what makes "what changed this shift" answerable.

Product bodies are stored as blobs in the database and streamed back from
the download route with ``Cache-Control: no-store`` - a product carries a
classification, and a cached copy in a shared browser would outlive the
role check that permitted the original fetch. Products marked
``classification='public'`` are additionally listed, unauthenticated, by
the `/api/public/products` routes in `meta.py`.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..operations import OperationsStore
from ..products import ProductManager
from ..schemas import ProductCreateRequest, SnapshotCreateRequest
from .common import _json, _operator

router = APIRouter()


@router.get("/api/operations/incidents/{incident_id}/snapshots")
async def api_snapshots(request: Request, incident_id: str) -> Response:
    store: OperationsStore = request.app.state.operations
    return _json(await asyncio.to_thread(store.list_snapshots, incident_id))


@router.post("/api/operations/incidents/{incident_id}/snapshots")
async def api_snapshot_create(
    request: Request, incident_id: str, body: SnapshotCreateRequest,
) -> Response:
    store: OperationsStore = request.app.state.operations
    actor = _operator(request)
    result = await asyncio.to_thread(
        store.create_snapshot, incident_id, body.name, body.period_id,
        body.classification, actor,
    )
    return _json(result, 201)


@router.get("/api/operations/incidents/{incident_id}/snapshots/{left_snapshot_id}/compare")
async def api_snapshot_compare(
    request: Request, incident_id: str, left_snapshot_id: str,
    right_snapshot_id: str | None = None,
) -> Response:
    store: OperationsStore = request.app.state.operations
    result = await asyncio.to_thread(
        store.compare_snapshots, incident_id, left_snapshot_id, right_snapshot_id
    )
    return _json(result)


@router.get("/api/operations/incidents/{incident_id}/products")
async def api_products(request: Request, incident_id: str) -> Response:
    manager: ProductManager = request.app.state.products
    rows = await asyncio.to_thread(manager.list, incident_id)
    for row in rows: row["metadata"] = json.loads(row.pop("metadata_json"))
    return _json(rows)


@router.post("/api/operations/incidents/{incident_id}/products")
async def api_product_create(
    request: Request, incident_id: str, body: ProductCreateRequest,
) -> Response:
    manager: ProductManager = request.app.state.products
    actor = _operator(request)
    result = await asyncio.to_thread(
        manager.create, incident_id, fmt=body.format, classification=body.classification,
        product_type=body.product_type, snapshot_id=body.snapshot_id, actor=actor, title=body.title,
    )
    return _json(result, 201)


@router.get("/api/operations/incidents/{incident_id}/products/{product_id}/download")
async def api_product_download(request: Request, incident_id: str, product_id: str) -> Response:
    manager: ProductManager = request.app.state.products
    filename, media_type, content = await asyncio.to_thread(manager.content, incident_id, product_id)
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "Cache-Control": "no-store"})
