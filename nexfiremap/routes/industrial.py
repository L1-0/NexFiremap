"""Industrial heat sources (flares, kilns, ...) matched against detections.

These are the persistent thermal anomalies that would otherwise read as
fires. Candidates come from a live Overpass query, which is why the
autofetch path is area-capped: the manual scan route has no cap, because
that is an operator's explicit choice rather than a side effect of
panning the map.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..db import Database
from ..jobs import JobManager
from ..schemas import IndustrialScanRequest
from . import common
from .common import INDUSTRIAL_AUTOFETCH_MAX_AREA_DEG2, _json, _parse_bbox, _submit_job

router = APIRouter()


@router.post("/api/industrial/scan")
async def api_industrial_scan(request: Request, body: IndustrialScanRequest) -> Response:
    jobs: JobManager = request.app.state.jobs
    box = _parse_bbox(body.bbox)
    if box is None:
        raise HTTPException(400, "bbox is required")

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=body.days)
    job_id = await _submit_job(
        jobs,
        "scan_industrial_sources",
        {
            "bbox": list(box),
            "start_ts": int(start_dt.timestamp()),
            "end_ts": int(end_dt.timestamp()),
            "window_days": body.window_days,
            "event_id": body.event_id,
        },
    )
    return _json({"job_id": job_id}, status_code=202)


@router.get("/api/industrial/sources")
async def api_industrial_sources(
    request: Request, bbox: str = Query(...), autofetch: bool = Query(False)
) -> Response:
    """Cached candidates in bbox, always returned immediately. With
    ``autofetch=true`` (what the map's own viewport reload uses), also
    queues a scan for areas that have never been checked or whose check
    has expired - same "read what's cached, queue what's missing"
    pattern as /api/coverage, so viewing the map is enough - no manual
    button required for the common case. Capped to a modest viewport
    size so panning out to a whole-continent view doesn't quietly queue
    an enormous Overpass query - the manual "scan this view" button
    still works at any size, that's an explicit choice, not a default."""
    db: Database = request.app.state.db
    jobs: JobManager = request.app.state.jobs
    box = _parse_bbox(bbox)
    if box is None:
        raise HTTPException(400, "bbox is required")

    job_id = None
    if autofetch and common.industrial is None:
        common.log.warning(
            "Industrial autofetch requested but the 'industrial' feature is unavailable: %s",
            common.FEATURE_ERRORS.get("industrial"),
        )
    elif autofetch:
        west, south, east, north = box
        area_deg2 = max(0.0, east - west) * max(0.0, north - south)
        if area_deg2 <= INDUSTRIAL_AUTOFETCH_MAX_AREA_DEG2:
            fresh = await asyncio.to_thread(common.industrial.query_cache_is_fresh, db.conn, box)
            if not fresh:
                job_id = await _submit_job(jobs, "scan_industrial_sources", {"bbox": list(box)})

    rows = await asyncio.to_thread(db.industrial_sources_in_bbox, box)
    return _json(
        {
            "sources": [
                {
                    "id": r["id"],
                    "osm_type": r["osm_type"],
                    "osm_id": r["osm_id"],
                    "lat": r["latitude"],
                    "lon": r["longitude"],
                    "evidence_class": r["evidence_class"],
                    "tags": json.loads(r["tags_json"]),
                    "score": r["score"],
                    "classification": r["classification"],
                    "detection_count": r["detection_count"],
                    "match_radius_km": r["match_radius_km"],
                    "computed_at": r["computed_at"],
                }
                for r in rows
            ],
            "meta": {"job_id": job_id},
        }
    )
