"""Server-describing endpoints plus the two unauthenticated public reads.

`/api/config` and `/health` are on `SecurityMiddleware`'s public
allowlist: the frontend fetches config to render the login screen before
any session exists, and the health check is polled by process
supervisors that never log in. `/api/public/products` is not on that
allowlist but is deliberately scoped to rows already marked
``classification='public'`` - the query itself is the access control.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..basemaps import BASEMAPS, OVERLAYS, TERRAIN_DEM
from ..cache import CacheManager
from ..config import SOURCES
from ..db import Database
from ..geocode import GeocodeService
from ..jobs import JobManager
from ..operations import AREA_TYPES, FEATURE_TYPES, LINE_TYPES, POINT_TYPES, SAFETY_CHECKS
from ..tiles import TileCache, public_layer
from . import common
from .common import _json

router = APIRouter()


@router.get("/api/public/products")
async def api_public_products(request: Request) -> Response:
    rows = request.app.state.db.conn.execute(
        "SELECT p.id,p.incident_id,p.product_type,p.filename,p.sha256,p.size_bytes,p.created_at,"
        "i.name AS incident_name FROM incident_products p JOIN incidents i ON i.id=p.incident_id "
        "WHERE p.classification='public' ORDER BY p.created_at DESC"
    ).fetchall()
    return _json([dict(row) for row in rows])


@router.get("/api/public/products/{product_id}/download")
async def api_public_product_download(request: Request, product_id: str) -> Response:
    row = request.app.state.db.conn.execute(
        "SELECT filename,metadata_json,content_blob FROM incident_products WHERE id=? AND classification='public'",
        (product_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "public product not found")
    media_type = json.loads(row["metadata_json"])["media_type"]
    return Response(content=bytes(row["content_blob"]), media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"',
                             "Cache-Control": "no-store"})


@router.get("/api/config")
async def api_config(request: Request) -> Response:
    settings = request.app.state.settings
    cache: CacheManager = request.app.state.cache
    return _json(
        {
            "app": "NexFiremap",
            "cache_days": settings.cache_days,
            "has_map_key": settings.has_map_key,
            "has_eumetsat_key": settings.has_eumetsat_key,
            "sources": [
                {
                    "id": key,
                    **meta,
                    "enabled": key in settings.sources,
                    "disabled_reason": cache.disabled_sources.get(key),
                }
                for key, meta in SOURCES.items()
            ],
            "basemaps": [public_layer(bm) for bm in BASEMAPS]
            + request.app.state.offline_sources.public_layers(),
            "overlays": [public_layer(ov) for ov in OVERLAYS],
            "terrain_dem": public_layer(TERRAIN_DEM),
            "refresh_minutes": settings.refresh_interval_minutes,
            "features": common.AVAILABLE_FEATURES,
            # Used by the frontend as the initial view only when the URL carries
            # no #zoom/lat/lon of its own (see app.js's initMap) - lets an
            # operator configure a sensible non-world-view default without
            # every visitor needing to pan/zoom in by hand first.
            "startup_bbox": list(settings.startup_bbox) if settings.startup_bbox else None,
        }
    )


@router.get("/api/operations/meta")
async def api_operations_meta(request: Request) -> Response:
    settings = request.app.state.settings
    return _json({
        "feature_types": sorted(FEATURE_TYPES),
        "geometry": {
            "Point": sorted(POINT_TYPES),
            "LineString": sorted(LINE_TYPES),
            "Polygon": sorted(AREA_TYPES),
        },
        "safety_checks": [{"key": key, "label": label} for key, label in SAFETY_CHECKS],
        "schema": "nexfiremap-incident/1",
        "observation_stale_hours": settings.observation_stale_hours,
    })


@router.get("/api/geocode")
async def api_geocode(request: Request, q: str = Query("")) -> Response:
    geocode: GeocodeService = request.app.state.geocode
    return _json(await geocode.search(q))


@router.get("/api/geocode/reverse")
async def api_geocode_reverse(
    request: Request, lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)
) -> Response:
    geocode: GeocodeService = request.app.state.geocode
    return _json(await geocode.reverse(lat, lon))


@router.get("/api/status")
async def api_status(request: Request, key: bool = Query(True)) -> Response:
    settings = request.app.state.settings
    db: Database = request.app.state.db
    cache: CacheManager = request.app.state.cache

    stats = await asyncio.to_thread(db.stats)

    key_payload = None
    if key and settings.has_map_key:
        cached = request.app.state.key_status
        if time.time() - cached["checked_at"] > 60:
            cached["payload"] = await cache.client.key_status()
            cached["checked_at"] = time.time()
        key_payload = cached["payload"]

    tiles: TileCache = request.app.state.tiles
    tile_stats = await asyncio.to_thread(tiles.stats)

    jobs: JobManager = request.app.state.jobs
    job_stats = await asyncio.to_thread(jobs.status)

    return _json(
        {
            "has_map_key": settings.has_map_key,
            "cache": stats,
            "fetcher": cache.status(),
            "tiles": tile_stats,
            "jobs": job_stats,
            "map_key": key_payload,
            "server_time": datetime.now(timezone.utc).isoformat(),
        }
    )


@router.get("/health")
async def health() -> Response:
    return _json({"status": "ok"})
