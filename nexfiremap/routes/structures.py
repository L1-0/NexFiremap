"""Building-footprint exposure against a modelled spread surface.

Both routes hard-fail with a 503 when the optional `structures` module
didn't import, rather than deferring to `_submit_job`'s generic message:
they need `structures.snap_bbox`/`MAX_SCAN_AREA_DEG2` *before* any job is
submitted, so there is no job to fail against.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..db import Database
from ..jobs import JobManager
from ..schemas import StructureScanRequest
from . import common
from .common import _json, _parse_bbox, _submit_job

router = APIRouter()


@router.post("/api/structures/scan")
async def api_structures_scan(request: Request, body: StructureScanRequest) -> Response:
    if common.structures is None:
        raise HTTPException(503, "structure exposure module is unavailable")
    box = _parse_bbox(body.bbox)
    if box is None:
        raise HTTPException(400, "bbox is required")
    west, south, east, north = common.structures.snap_bbox(box)
    area = max(0.0, east - west) * max(0.0, north - south)
    if area > common.structures.MAX_SCAN_AREA_DEG2:
        raise HTTPException(400, f"structure scan area exceeds {common.structures.MAX_SCAN_AREA_DEG2} square degrees")
    jobs: JobManager = request.app.state.jobs
    job_id = await _submit_job(jobs, "scan_structures", {"bbox": list(box)})
    return _json({"job_id": job_id}, status_code=202)


@router.get("/api/structures/exposure")
async def api_structures_exposure(
    request: Request, job_id: int = Query(..., ge=1), autofetch: bool = Query(False),
) -> Response:
    """Evaluate locally cached building footprints against a numeric
    spread-arrival surface. With autofetch, queue one cache-fill job for
    the model bounds; the immediate response remains a local-only read."""
    if common.structures is None:
        raise HTTPException(503, "structure exposure module is unavailable")
    db: Database = request.app.state.db
    jobs: JobManager = request.app.state.jobs
    job_row = await asyncio.to_thread(db.get_job, job_id)
    if job_row is None:
        raise HTTPException(404, "no such analysis job")
    raw_job = dict(job_row)
    result = json.loads(raw_job.get("result_json") or "{}")
    bounds = result.get("bounds")
    if not bounds or len(bounds) != 2:
        raise HTTPException(400, "analysis job has no spatial bounds")
    box = (float(bounds[0][1]), float(bounds[0][0]), float(bounds[1][1]), float(bounds[1][0]))

    scan_job_id = None
    if autofetch:
        fresh = await asyncio.to_thread(common.structures.query_cache_is_fresh, db.conn, box)
        if not fresh:
            snapped = common.structures.snap_bbox(box)
            area = max(0.0, snapped[2] - snapped[0]) * max(0.0, snapped[3] - snapped[1])
            if area <= common.structures.MAX_SCAN_AREA_DEG2:
                scan_job_id = await _submit_job(jobs, "scan_structures", {"bbox": list(box)})
    try:
        payload = await asyncio.to_thread(
            common.structures.assess_exposure, db.conn, raw_job, jobs.job_dir
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    payload["meta"]["scan_job_id"] = scan_job_id
    payload["meta"]["structure_cache_fresh"] = await asyncio.to_thread(
        common.structures.query_cache_is_fresh, db.conn, box
    )
    return _json(payload)
