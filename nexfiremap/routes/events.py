"""Fire events: spatiotemporal clusters of detections, and their analyses.

Detection is a queued job (clustering is too slow for a request), so
`/detect` and every `/{event_id}/...` analysis route return 202 with a
job id rather than a result - the frontend polls `/api/jobs/{id}`. Each
analysis route checks the event exists first so a bad id fails as a 404
now instead of a failed job later.

The analysis routes depend on optional modules that may not have
imported (see `common.AVAILABLE_FEATURES`); rather than checking that
here, they let `_submit_job` turn the unregistered job kind into a 503
that names the missing dependency.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..db import Database
from ..jobs import JobManager
from ..schemas import (
    BurnScarRequest,
    EnsembleRequest,
    EventAnalyzeRequest,
    EventDetectRequest,
    PropagationRequest,
    ValidationRequest,
)
from . import common
from .common import (
    EVENTS_AUTOFETCH_MAX_AREA_DEG2,
    _event_row_to_dict,
    _json,
    _parse_bbox,
    _submit_job,
)

router = APIRouter()


@router.post("/api/events/detect")
async def api_events_detect(request: Request, body: EventDetectRequest) -> Response:
    jobs: JobManager = request.app.state.jobs
    box = _parse_bbox(body.bbox)
    if box is None:
        raise HTTPException(400, "bbox is required")

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=body.days)
    sources = [s.upper() for s in body.sources] if body.sources else None

    job_id = await _submit_job(
        jobs,
        "detect_events",
        {
            "bbox": list(box),
            "start_ts": int(start_dt.timestamp()),
            "end_ts": int(end_dt.timestamp()),
            "sources": sources,
            "v_max_kmh": body.v_max_kmh,
            "max_dt_hours": body.max_dt_hours,
            "min_detections": body.min_detections,
            "max_span_km": body.max_span_km,
        },
    )
    return _json({"job_id": job_id}, status_code=202)


@router.get("/api/events")
async def api_events_list(
    request: Request,
    bbox: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    autofetch: bool = Query(False),
) -> Response:
    """Cached events in bbox, returned immediately. With
    ``autofetch=true`` (the map's own viewport reload), also queues
    clustering for a view that's never been checked - "find in view"
    becomes automatic for the common case, same pattern as
    /api/coverage and /api/industrial/sources. Only triggers when this
    bbox has *no* cached events yet: detect_events creates fresh rows
    each run rather than merging into existing ones (see its own
    docstring), so re-triggering over an area that already has events
    would just pile up duplicates, not refresh them."""
    settings = request.app.state.settings
    db: Database = request.app.state.db
    jobs: JobManager = request.app.state.jobs
    box = _parse_bbox(bbox)
    rows = await asyncio.to_thread(db.list_events, bbox=box, limit=limit)

    job_id = None
    if autofetch and common.events is None:
        common.log.warning(
            "Event autofetch requested but the 'events' feature is unavailable: %s",
            common.FEATURE_ERRORS.get("events"),
        )
    elif autofetch and box is not None and not rows:
        west, south, east, north = box
        area_deg2 = max(0.0, east - west) * max(0.0, north - south)
        if area_deg2 <= EVENTS_AUTOFETCH_MAX_AREA_DEG2:
            now = datetime.now(timezone.utc)
            start_dt = now - timedelta(days=settings.cache_days)
            job_id = await _submit_job(
                jobs,
                "detect_events",
                {"bbox": list(box), "start_ts": int(start_dt.timestamp()), "end_ts": int(now.timestamp())},
            )

    return _json({"events": [_event_row_to_dict(row) for row in rows], "meta": {"job_id": job_id}})


@router.get("/api/events/{event_id}")
async def api_events_get(request: Request, event_id: int) -> Response:
    db: Database = request.app.state.db
    row = await asyncio.to_thread(db.get_event, event_id)
    if row is None:
        raise HTTPException(404, "no such event")
    members = await asyncio.to_thread(db.event_detections, event_id)
    data = _event_row_to_dict(row)
    data["detections"] = [
        {
            "id": m["id"],
            "lat": m["latitude"],
            "lon": m["longitude"],
            "ts": m["acq_ts"],
            "source": m["source"],
            "satellite": m["satellite"],
            "frp": m["frp"],
            "confidence": m["confidence_level"],
        }
        for m in members
    ]
    return _json(data)


@router.post("/api/events/{event_id}/analyze")
async def api_events_analyze(
    request: Request, event_id: int, body: EventAnalyzeRequest
) -> Response:
    db: Database = request.app.state.db
    jobs: JobManager = request.app.state.jobs
    if await asyncio.to_thread(db.get_event, event_id) is None:
        raise HTTPException(404, "no such event")
    job_id = await _submit_job(
        jobs,
        "analyze_event",
        {
            "event_id": event_id,
            "tau_hours": body.tau_hours,
            "resolution_m": body.resolution_m,
            "reference_ts": body.reference_ts,
        },
    )
    return _json({"job_id": job_id}, status_code=202)


@router.post("/api/events/{event_id}/burn-scar")
async def api_events_burn_scar(
    request: Request, event_id: int, body: BurnScarRequest
) -> Response:
    db: Database = request.app.state.db
    jobs: JobManager = request.app.state.jobs
    if await asyncio.to_thread(db.get_event, event_id) is None:
        raise HTTPException(404, "no such event")
    job_id = await _submit_job(
        jobs, "analyze_burn_scar", {"event_id": event_id, "resolution_m": body.resolution_m}
    )
    return _json({"job_id": job_id}, status_code=202)


@router.post("/api/events/{event_id}/propagate")
async def api_events_propagate(
    request: Request, event_id: int, body: PropagationRequest
) -> Response:
    db: Database = request.app.state.db
    jobs: JobManager = request.app.state.jobs
    if await asyncio.to_thread(db.get_event, event_id) is None:
        raise HTTPException(404, "no such event")
    job_id = await _submit_job(
        jobs,
        "run_propagation",
        {
            "event_id": event_id,
            "resolution_m": body.resolution_m,
            "reference_ts": body.reference_ts,
        },
    )
    return _json({"job_id": job_id}, status_code=202)


@router.post("/api/events/{event_id}/ensemble")
async def api_events_ensemble(
    request: Request, event_id: int, body: EnsembleRequest
) -> Response:
    db: Database = request.app.state.db
    jobs: JobManager = request.app.state.jobs
    if await asyncio.to_thread(db.get_event, event_id) is None:
        raise HTTPException(404, "no such event")
    job_id = await _submit_job(
        jobs,
        "run_ensemble_assimilation",
        {
            "event_id": event_id,
            "resolution_m": body.resolution_m,
            "reference_ts": body.reference_ts,
            "n_members": body.n_members,
            "random_seed": body.random_seed,
        },
    )
    return _json({"job_id": job_id}, status_code=202)


@router.post("/api/events/{event_id}/validate")
async def api_events_validate(
    request: Request, event_id: int, body: ValidationRequest
) -> Response:
    db: Database = request.app.state.db
    jobs: JobManager = request.app.state.jobs
    if await asyncio.to_thread(db.get_event, event_id) is None:
        raise HTTPException(404, "no such event")
    job_id = await _submit_job(
        jobs,
        "validate_event",
        {"event_id": event_id, "n_splits": body.n_splits, "threshold": body.threshold},
    )
    return _json({"job_id": job_id}, status_code=202)
