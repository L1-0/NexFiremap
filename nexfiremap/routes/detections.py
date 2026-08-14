"""The core fire-detection reads: point query and daily rollup.

`/api/detections` is the hottest route in the app - the map calls it on
every pan/zoom - which is why it defaults to the `compact` columnar
format (see `COMPACT_COLUMNS`) rather than GeoJSON, and why it goes
through `_json` instead of a Pydantic response model.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..cache import CacheManager
from ..db import Database
from ..jobs import JobManager
from ..schemas import SpreadTopologyRequest
from . import common
from .common import (
    COMPACT_COLUMNS,
    DETECTIONS_AUTOFETCH_MAX_AREA_DEG2,
    _json,
    _parse_bbox,
    _parse_levels,
    _parse_sources,
    _submit_job,
)

router = APIRouter()


@router.get("/api/detections")
async def api_detections(
    request: Request,
    bbox: str | None = Query(None, description="west,south,east,north"),
    days: int = Query(3, ge=1, le=60),
    start: str | None = Query(None, description="ISO date, overrides days"),
    end: str | None = Query(None, description="ISO date"),
    sources: str | None = Query(None),
    confidence: str | None = Query(None, description="low,nominal,high"),
    min_frp: float = Query(0.0, ge=0.0),
    daynight: str | None = Query(None, pattern="^[DNdn]$"),
    limit: int = Query(30000, ge=1, le=250000),
    fmt: str = Query("compact", pattern="^(compact|geojson)$"),
    autofetch: bool = Query(False),
) -> Response:
    settings = request.app.state.settings
    db: Database = request.app.state.db
    cache: CacheManager = request.app.state.cache

    box = _parse_bbox(bbox)
    wanted_sources = _parse_sources(sources, settings)
    levels = _parse_levels(confidence)

    now = datetime.now(timezone.utc)
    if end:
        try:
            end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(400, "end must be an ISO date") from exc
    else:
        end_dt = now
    if start:
        try:
            start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(400, "start must be an ISO date") from exc
    else:
        start_dt = end_dt - timedelta(days=days)
    # `days`'s own ge=1/le=60 bound only applies when start/end are
    # absent - passing them directly used to impose no range limit at
    # all. Nothing older than cache_days survives retention anyway, so
    # this doesn't lose any real data, just rejects a request that
    # could never have returned more than an empty tail regardless.
    max_span = timedelta(days=max(60, settings.cache_days))
    if end_dt - start_dt > max_span:
        raise HTTPException(
            400, f"start/end span too wide (max {max_span.days} days)"
        )

    queued = None
    if autofetch and box and settings.has_map_key:
        west, south, east, north = box
        area_deg2 = max(0.0, east - west) * max(0.0, north - south)
        if area_deg2 <= DETECTIONS_AUTOFETCH_MAX_AREA_DEG2:
            span_days = max(1, min(settings.cache_days, (now - start_dt).days + 1))
            queued = await cache.ensure_cached(box, span_days, wanted_sources)
        else:
            common.log.info(
                "Detections autofetch skipped: viewport too large (%.0f deg^2 > %.0f)",
                area_deg2,
                DETECTIONS_AUTOFETCH_MAX_AREA_DEG2,
            )

    rows = await asyncio.to_thread(
        db.query_detections,
        bbox=box,
        start_ts=int(start_dt.timestamp()),
        end_ts=int(end_dt.timestamp()),
        sources=wanted_sources,
        confidence_levels=levels,
        min_frp=min_frp,
        daynight=(daynight or "").upper() or None,
        limit=limit,
    )

    meta = {
        "count": len(rows),
        "truncated": len(rows) >= limit,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "fetch": queued,
        "pending": cache.pending,
    }

    if fmt == "geojson":
        features = [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [row["longitude"], row["latitude"]],
                },
                "properties": {
                    "source": row["source"],
                    "satellite": row["satellite"],
                    "instrument": row["instrument"],
                    "acq_date": row["acq_date"],
                    "acq_time": row["acq_time"],
                    "acq_ts": row["acq_ts"],
                    "brightness": row["brightness"],
                    "brightness2": row["brightness2"],
                    "confidence": row["confidence_level"],
                    "confidence_pct": row["confidence_pct"],
                    "frp": row["frp"],
                    "daynight": row["daynight"],
                    "scan": row["scan"],
                    "track": row["track"],
                },
            }
            for row in rows
        ]
        return _json(
            {"type": "FeatureCollection", "features": features, "meta": meta}
        )

    compact = [
        [
            row["latitude"],
            row["longitude"],
            row["acq_ts"],
            row["frp"],
            row["confidence_level"],
            row["source"],
            row["satellite"],
            row["daynight"],
            row["brightness"],
            row["scan"],
            row["track"],
        ]
        for row in rows
    ]
    return _json({"columns": COMPACT_COLUMNS, "rows": compact, "meta": meta})


@router.post("/api/detections/spread_topology")
async def api_detections_spread_topology(request: Request, body: SpreadTopologyRequest) -> Response:
    jobs: JobManager = request.app.state.jobs
    box = _parse_bbox(body.bbox)
    if box is None:
        raise HTTPException(400, "bbox is required")

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=body.days)
    sources = [s.upper() for s in body.sources] if body.sources else None

    job_id = await _submit_job(
        jobs,
        "spread_topology",
        {
            "bbox": list(box),
            "start_ts": int(start_dt.timestamp()),
            "end_ts": int(end_dt.timestamp()),
            "sources": sources,
        },
    )
    return _json({"job_id": job_id}, status_code=202)


@router.get("/api/summary")
async def api_summary(
    request: Request,
    bbox: str | None = Query(None),
    days: int = Query(30, ge=1, le=60),
    sources: str | None = Query(None),
) -> Response:
    settings = request.app.state.settings
    db: Database = request.app.state.db
    box = _parse_bbox(bbox)
    wanted_sources = _parse_sources(sources, settings)

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    rows = await asyncio.to_thread(
        db.daily_counts,
        bbox=box,
        start_ts=int(start_dt.timestamp()),
        end_ts=int(end_dt.timestamp()),
        sources=wanted_sources,
    )
    return _json(
        {
            "days": [
                {
                    "day": row["day"],
                    "count": row["count"],
                    "frp_total": round(row["frp_total"] or 0.0, 1),
                }
                for row in rows
            ]
        }
    )
