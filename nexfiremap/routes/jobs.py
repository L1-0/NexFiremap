"""Background job submission, polling, cancellation, and output files.

Every long analysis in the app (event detection, propagation, swath
coverage, Overpass scans) is queued through `JobManager` and polled here
- the domain routers return a job id, the frontend watches
`/api/jobs/{id}` until it completes, then fetches whatever the result
references from `/api/jobs/{id}/files/{filename}`.

Note the generic `POST /api/jobs` calls `jobs.submit` directly rather
than via `_submit_job`: an unknown `kind` from a hand-written request is
a 400 (the caller asked for something that doesn't exist), whereas the
same failure reached through a domain route is a 503 (the feature exists
but its dependencies didn't import).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from ..db import Database
from ..jobs import JobManager
from ..schemas import JobSubmitRequest
from .common import _job_row_to_dict, _json

router = APIRouter()


@router.post("/api/jobs")
async def api_jobs_submit(request: Request, body: JobSubmitRequest) -> Response:
    jobs: JobManager = request.app.state.jobs
    try:
        job_id = await jobs.submit(body.kind, body.params)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _json({"job_id": job_id}, status_code=202)


@router.get("/api/jobs/{job_id}")
async def api_jobs_get(request: Request, job_id: int) -> Response:
    db: Database = request.app.state.db
    row = await asyncio.to_thread(db.get_job, job_id)
    if row is None:
        raise HTTPException(404, "no such job")
    return _json(_job_row_to_dict(row))


@router.post("/api/jobs/{job_id}/cancel")
async def api_jobs_cancel(request: Request, job_id: int) -> Response:
    """Cancel a queued or running job. A queued job stops promptly. A
    job already executing in a worker process is marked cancelled once
    its current step finishes (see JobManager.cancel's docstring) - the
    underlying executor future can't be interrupted mid-flight, only
    made to not matter once it does complete."""
    db: Database = request.app.state.db
    jobs: JobManager = request.app.state.jobs
    row = await asyncio.to_thread(db.get_job, job_id)
    if row is None:
        raise HTTPException(404, "no such job")
    cancelled = await jobs.cancel(job_id)
    if not cancelled:
        return _json(
            {
                "cancelled": False,
                "reason": (
                    "job is not tracked as queued/running - it already finished, "
                    "or the server restarted since it was submitted"
                ),
            }
        )
    return _json({"cancelled": True})


@router.get("/api/jobs")
async def api_jobs_list(
    request: Request,
    status: str | None = Query(None),
    kind: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> Response:
    db: Database = request.app.state.db
    rows = await asyncio.to_thread(db.list_jobs, status=status, kind=kind, limit=limit)
    return _json([_job_row_to_dict(row) for row in rows])


@router.get("/api/jobs/{job_id}/files/{filename}")
async def api_job_file(request: Request, job_id: int, filename: str) -> Response:
    """Generic static file server for a job's outputs (rasters,
    GeoJSON, ...) - every analysis phase writes to its own job
    directory and links to files here rather than inlining large
    payloads in the job's JSON result."""
    jobs: JobManager = request.app.state.jobs
    safe_name = Path(filename).name  # strip any path components
    # Path.name doesn't strip ".." itself (Path("..").name == ".."), so
    # a bare ".." would otherwise pass through unchanged - reject it
    # explicitly rather than relying only on that pathlib quirk. The
    # is_relative_to check below is the actual backstop - this just
    # gives a clearer 400 for the obvious case instead of a 404.
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(400, "invalid filename")
    job_root = (jobs.job_dir / str(job_id)).resolve()
    path = (job_root / safe_name).resolve()
    if not path.is_relative_to(job_root):
        raise HTTPException(400, "invalid filename")
    if not path.is_file():
        raise HTTPException(404, "no such job output file")
    if safe_name.endswith(".png"):
        media_type = "image/png"
    elif safe_name.endswith(".geojson") or safe_name.endswith(".json"):
        media_type = "application/geo+json"
    else:
        media_type = "application/octet-stream"
    return FileResponse(
        path, media_type=media_type, headers={"Cache-Control": "public, max-age=604800"}
    )
