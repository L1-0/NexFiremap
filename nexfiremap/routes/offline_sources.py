"""Import and inspection of operator-supplied offline basemap sources.

The import route streams its body straight to disk rather than reading it
into memory - these uploads are whole MBTiles/GeoTIFF files, which is
also why the size cap is enforced chunk-by-chunk as the stream arrives
instead of trusting Content-Length alone.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from ..offline_sources import MAX_UPLOAD_BYTES, OfflineSourceError, OfflineSourceManager
from .common import _json

router = APIRouter()


@router.get("/api/operations/offline-sources")
async def api_offline_sources(request: Request) -> Response:
    manager: OfflineSourceManager = request.app.state.offline_sources
    return _json(await asyncio.to_thread(manager.list))


@router.post("/api/operations/offline-sources")
async def api_offline_source_import(
    request: Request,
    name: str = Query(..., min_length=1, max_length=200),
    source: str = Query(..., min_length=1, max_length=500),
    attribution: str = Query(..., min_length=1, max_length=2000),
    acquired_at: str = Query(..., min_length=1, max_length=100),
    licence: str = Query(..., min_length=1, max_length=1000),
    limitations: str = Query("", max_length=5000),
    source_format: str = Query("mbtiles", pattern="^(mbtiles|geotiff|gpkg)$"),
) -> Response:
    manager: OfflineSourceManager = request.app.state.offline_sources
    raw_length = request.headers.get("content-length")
    try:
        content_length = int(raw_length) if raw_length is not None else None
    except ValueError as exc:
        raise HTTPException(400, "invalid Content-Length") from exc
    source_id, partial = manager.begin_upload(content_length)
    total = 0
    try:
        with partial.open("wb") as handle:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise OfflineSourceError(f"offline source upload exceeds {MAX_UPLOAD_BYTES} bytes")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        finalize = manager.finalize_upload if source_format == "mbtiles" else manager.finalize_raster_upload
        result = await asyncio.to_thread(
            finalize, source_id, partial, name=name, source=source,
            attribution=attribution, acquired_at=acquired_at, licence=licence,
            limitations=limitations,
        )
    except Exception:
        manager.abort_upload(partial)
        raise
    return _json(result, 201)


@router.post("/api/operations/offline-sources/{source_id}/terrain-package")
async def api_terrain_package(
    request: Request, source_id: str, interval_m: float = Query(20, ge=1, le=1000),
) -> Response:
    manager: OfflineSourceManager = request.app.state.offline_sources
    path, manifest = await asyncio.to_thread(manager.derive_terrain_package, source_id, interval_m)
    return FileResponse(path, media_type="application/zip", filename=path.name,
                        headers={"X-Content-SHA256": manifest["sha256"], "Cache-Control": "no-store"})
