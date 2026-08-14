"""EUMETSAT MTG/FCI active-fire pixels - the geostationary complement.

Unlike the polar-orbiter FIRMS feeds, every FCI product covers the whole
visible disk, so the autofetch decision here is purely temporal (is the
newest ingested product stale?) with no area cap - fetch cost doesn't
scale with the viewport the way an Overpass or FIRMS-by-bbox fetch does.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..db import Database
from ..jobs import JobManager
from . import common
from .common import _json, _parse_bbox, _submit_job

router = APIRouter()


@router.get("/api/eumetsat/fires")
async def api_eumetsat_fires(
    request: Request, bbox: str = Query(...), autofetch: bool = Query(False)
) -> Response:
    """Cached EUMETSAT MTG/FCI active-fire pixels in bbox, always
    returned immediately. With ``autofetch=true``, also queues a scan
    when the most recently ingested product is stale (no area cap: every
    product covers the whole disk regardless of bbox, so cost doesn't
    scale with viewport size the way Overpass/FIRMS fetches do - see
    eumetsat.py's module docstring)."""
    settings = request.app.state.settings
    db: Database = request.app.state.db
    jobs: JobManager = request.app.state.jobs
    box = _parse_bbox(bbox)
    if box is None:
        raise HTTPException(400, "bbox is required")

    job_id = None
    if autofetch and common.eumetsat is None:
        common.log.warning(
            "EUMETSAT autofetch requested but the 'eumetsat' feature is unavailable: %s",
            common.FEATURE_ERRORS.get("eumetsat"),
        )
    elif autofetch and not settings.has_eumetsat_key:
        pass  # no account configured - not an error, just nothing to fetch
    elif autofetch:
        latest = await asyncio.to_thread(db.eumetsat_latest_product_end_ts)
        stale = latest is None or (time.time() - latest) > common.eumetsat.FRESHNESS_WINDOW_S
        if stale:
            job_id = await _submit_job(jobs, "scan_eumetsat_fires", {"bbox": list(box)})

    rows = await asyncio.to_thread(db.eumetsat_fires_in_bbox, box, time.time() - 6 * 3600.0)
    return _json(
        {
            "fires": [
                {
                    "id": r["id"],
                    "lat": r["latitude"],
                    "lon": r["longitude"],
                    "acq_ts": r["acq_ts"],
                    "confidence": r["confidence"],
                    "probability": r["probability"],
                }
                for r in rows
            ],
            "meta": {
                "job_id": job_id,
                "available": settings.has_eumetsat_key and common.eumetsat is not None,
            },
        }
    )
