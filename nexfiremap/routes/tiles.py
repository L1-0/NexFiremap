"""Raster tile serving: proxied/cached upstream layers and offline packs.

These are the only routes that return image bytes on the hot path, and
the only ones that set a long `max-age` - tiles are immutable for a given
z/x/y, so caching aggressively in the browser is what keeps panning
smooth. Note the `Cache-Control` set here survives `SecurityMiddleware`'s
header pass, which uses `setdefault`.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..offline_sources import OfflineSourceManager
from ..tiles import TRANSPARENT_PNG, TileCache
from .common import _json

router = APIRouter()


@router.get("/offline-tiles/{source_id}/{z}/{x}/{y}")
async def offline_tile(request: Request, source_id: str, z: int, x: int, y: int) -> Response:
    manager: OfflineSourceManager = request.app.state.offline_sources
    try:
        result = await asyncio.to_thread(manager.tile, source_id, z, x, y)
    except FileNotFoundError as exc:
        raise HTTPException(404, "offline source not found") from exc
    if result is None:
        raise HTTPException(404, "tile not present in offline source")
    data, media_type = result
    return Response(content=data, media_type=media_type,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/tiles/{layer_id}/{z}/{x}/{y}.{ext}")
async def tile(request: Request, layer_id: str, z: int, x: int, y: int, ext: str) -> Response:
    settings = request.app.state.settings
    tiles: TileCache = request.app.state.tiles
    meta = tiles.layer(layer_id)
    if meta is None:
        raise HTTPException(404, "unknown layer")
    if not (0 <= z <= 22) or not (0 <= x < (1 << z)) or not (0 <= y < (1 << z)):
        raise HTTPException(400, "tile coordinates out of range")

    # Most tile providers here are PNG - a couple (e.g. Esri's terrain
    # base) actually serve JPEG - per-layer, not guessed from the
    # request, since a mismatched Content-Type is exactly the bug this
    # fixes (see basemaps.py's "tile_ext" comment on esri-terrain).
    media_type = "image/jpeg" if meta.get("tile_ext") == "jpg" else "image/png"

    data = await tiles.get(layer_id, z, x, y)
    if data is None:
        # No stale copy and upstream failed - a transparent PNG still
        # reads as "no tile" over a JPEG layer despite the mismatched
        # type in this one fallback case - a real broken-image icon
        # would be worse.
        return Response(content=TRANSPARENT_PNG, media_type="image/png")

    max_age = min(86400, settings.tile_cache_days * 86400)
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": f"public, max-age={max_age}"},
    )


@router.post("/api/tiles/purge")
async def api_tiles_purge(request: Request) -> Response:
    tiles: TileCache = request.app.state.tiles
    result = await asyncio.to_thread(tiles.prune_now)
    return _json(result)
