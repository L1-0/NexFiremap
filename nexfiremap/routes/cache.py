"""Manual control over the FIRMS detection cache.

The map normally fills this cache implicitly via `autofetch` on
`/api/detections`; these three routes are the explicit operator
equivalents - fetch this AOI now, re-fetch everything already cached,
drop what's past retention.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..cache import CacheManager
from ..config import SOURCES
from ..schemas import EnsureRequest
from .common import _json, _parse_bbox

router = APIRouter()


@router.post("/api/cache/ensure")
async def api_cache_ensure(request: Request, body: EnsureRequest) -> Response:
    settings = request.app.state.settings
    cache: CacheManager = request.app.state.cache
    if not settings.has_map_key:
        raise HTTPException(400, "No FIRMS map key configured (set FIRMS_MAP_KEY).")
    box = _parse_bbox(body.bbox)
    if box is None:
        raise HTTPException(400, "bbox is required")
    sources = body.sources or None
    if sources:
        sources = [s.upper() for s in sources if s.upper() in SOURCES]
    result = await cache.ensure_cached(box, body.days, sources)
    return _json(result)


@router.post("/api/cache/refresh")
async def api_cache_refresh(request: Request) -> Response:
    settings = request.app.state.settings
    cache: CacheManager = request.app.state.cache
    if not settings.has_map_key:
        raise HTTPException(400, "No FIRMS map key configured (set FIRMS_MAP_KEY).")
    queued = await cache.refresh_cached_regions()
    return _json({"queued": queued, "pending": cache.pending})


@router.post("/api/cache/purge")
async def api_cache_purge(request: Request) -> Response:
    cache: CacheManager = request.app.state.cache
    detections, coverage = await asyncio.to_thread(cache.purge_now)
    return _json({"detections_removed": detections, "coverage_removed": coverage})
