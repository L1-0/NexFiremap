"""Satellite swath coverage grid for a viewport and day.

One route, but it earns its own module: it is the only consumer of the
`orbits` optional feature and of the swath tables, and its
read-cached/queue-missing autofetch logic has a TTL rule (today keeps
accumulating passes; a fully elapsed day never changes) that none of the
other autofetch routes share.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..db import Database
from ..jobs import JobManager
from . import common
from .common import _json, _parse_bbox, _submit_job

router = APIRouter()


@router.get("/api/coverage")
async def api_coverage(
    request: Request,
    bbox: str = Query(..., description="west,south,east,north"),
    day: str | None = Query(None, description="ISO date, default today (UTC)"),
    autofetch: bool = Query(False),
) -> Response:
    """Satellite swath coverage for a viewport/day - a geometric
    approximation of "was this area observed", not cloud/quality masked.
    See orbits.py's module docstring for what this does and doesn't mean.
    """
    settings = request.app.state.settings
    db: Database = request.app.state.db
    jobs: JobManager = request.app.state.jobs

    box = _parse_bbox(bbox)
    if box is None:
        raise HTTPException(400, "bbox is required")
    day_str = day or datetime.now(timezone.utc).date().isoformat()
    try:
        date.fromisoformat(day_str)
    except ValueError as exc:
        raise HTTPException(400, "day must be an ISO date") from exc

    cell_size = settings.cell_size_deg
    rows = await asyncio.to_thread(db.swath_cells_for_bbox, box, day_str, cell_size)

    # Wide-swath polar orbiters cover nearly the whole globe within a
    # day, so "was this cell seen at all today" is almost always yes -
    # the informative aggregate is the most recent look across every
    # tracked satellite, not the boolean.
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        key = (row["cell_x"], row["cell_y"])
        entry = cells.setdefault(
            key, {"satellites": set(), "last_ts": None, "pass_count": 0}
        )
        entry["satellites"].add(row["satellite"])
        entry["pass_count"] += row["pass_count"]
        if entry["last_ts"] is None or row["last_ts"] > entry["last_ts"]:
            entry["last_ts"] = row["last_ts"]

    job_id = None
    if autofetch and common.orbits is None:
        common.log.warning(
            "Coverage autofetch requested but the 'orbits' feature is unavailable: %s",
            common.FEATURE_ERRORS.get("orbits"),
        )
    elif autofetch:
        # "Today" keeps accumulating passes as the day goes on, so a
        # cached computation from an hour ago is stale, not wrong - only
        # today gets a TTL. A fully elapsed past day is cached forever.
        today_str = datetime.now(timezone.utc).date().isoformat()
        missing = []
        for sat in common.orbits.SATELLITES:
            computed_at = await asyncio.to_thread(db.swath_computed_at, sat, day_str)
            if computed_at is None:
                missing.append(sat)
            elif day_str == today_str and time.time() - computed_at > 1800:
                missing.append(sat)
        if missing:
            job_id = await _submit_job(
                jobs,
                "swath_coverage",
                {"day": day_str, "satellites": missing, "cell_size_deg": cell_size},
            )

    now_ts = int(datetime.now(timezone.utc).timestamp())
    features = []
    for (cx, cy), entry in cells.items():
        west = -180.0 + cx * cell_size
        south = -90.0 + cy * cell_size
        east = west + cell_size
        north = south + cell_size
        last_ts = entry["last_ts"]
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [west, south],
                            [east, south],
                            [east, north],
                            [west, north],
                            [west, south],
                        ]
                    ],
                },
                "properties": {
                    "satellites": sorted(entry["satellites"]),
                    "pass_count": entry["pass_count"],
                    "last_ts": last_ts,
                    "hours_ago": round((now_ts - last_ts) / 3600, 1)
                    if last_ts
                    else None,
                },
            }
        )

    return _json(
        {
            "type": "FeatureCollection",
            "features": features,
            "meta": {"day": day_str, "job_id": job_id, "cell_size_deg": cell_size},
        }
    )
