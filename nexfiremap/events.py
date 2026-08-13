"""Event clustering - grouping detections into discrete fire events.

further_plan.md is explicit that a FIRMS point is a satellite pixel, not a
fire perimeter, and warns against treating a month of detections as one
blob (a convex hull "fills valleys, lakes, unburned islands and disconnected
activity"). This module is the fix: a space-time graph where two detections
are linked only if they're close enough in both position and time to
plausibly be the same fire, using classical graph clustering - no ML.

The link rule (further_plan.md, section 5):

    d(i, j) <= r_i + r_j + v_max * dt

``r_i``/``r_j`` are each detection's footprint radius (from scan/track),
``dt`` the time gap, ``v_max`` a deliberately generous plausible spread
speed. A hard cap on ``dt`` keeps two fires weeks apart from being fused
just because a huge speed times a huge gap produces a huge allowed radius -
that stopped being physically meaningful long before 30 days.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time as time_module
from typing import Any

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .geo import DEFAULT_FOOTPRINT_RADIUS_KM, LAT_KM_PER_DEG, detection_footprint_radius_km, lon_km_per_deg
from .jobs import JobContext, register_kind

log = logging.getLogger("nexfiremap.events")

DEFAULT_V_MAX_KMH = 8.0
DEFAULT_MAX_DT_HOURS = 7 * 24.0
DEFAULT_MIN_DETECTIONS = 2

# cluster_detections' coarse candidate search (cKDTree.query_pairs) costs at
# worst O(n^2) pairs - reachable in practice, not just theory: the default
# max_dt_hours (7 days) and v_max_kmh (8) alone already imply a ~1344km
# coarse radius, and both are caller-controllable up to 60 days / 200 km/h
# via the API, i.e. up to a ~288,000km radius - larger than the point cloud
# itself, at which point query_pairs degenerates to a full all-pairs scan
# regardless of how spread out the detections actually are. Capping the
# search radius itself would silently miss real links beyond it, so the
# fetch that feeds this is capped by row count instead (see
# _fetch_detections) - a country-scale view during a busy fire week is a
# real, not hypothetical, way to hit this.
MAX_CLUSTER_DETECTIONS = 40_000


def cluster_detections(
    rows: list[dict[str, Any]],
    *,
    v_max_kmh: float = DEFAULT_V_MAX_KMH,
    max_dt_hours: float = DEFAULT_MAX_DT_HOURS,
) -> list[list[int]]:
    """Group detection indices into clusters via the space-time link rule.

    ``rows`` items need ``lat``, ``lon``, ``ts`` (unix seconds), and
    optionally ``scan``/``track`` (km). Returns a list of clusters, each a
    list of indices into ``rows`` - every detection appears in exactly one
    cluster, including clusters of size 1.
    """
    n = len(rows)
    if n == 0:
        return []
    if n == 1:
        return [[0]]

    lats = np.array([r["lat"] for r in rows], dtype=np.float64)
    lons = np.array([r["lon"] for r in rows], dtype=np.float64)
    ts = np.array([r["ts"] for r in rows], dtype=np.float64)
    radii = np.array(
        [detection_footprint_radius_km(r, DEFAULT_FOOTPRINT_RADIUS_KM) for r in rows],
        dtype=np.float64,
    )

    # Local flat-earth projection centred on the batch - fine at the AOI
    # scales this operates on (a single fire event's neighbourhood), not
    # intended for anything continent-spanning.
    lat0 = float(np.mean(lats))
    lon0 = float(np.mean(lons))
    lon_km = lon_km_per_deg(lat0)
    x = (lons - lon0) * lon_km
    y = (lats - lat0) * LAT_KM_PER_DEG
    points = np.column_stack([x, y])

    tree = cKDTree(points)
    # Coarse candidate search: the largest radius the link rule could ever
    # allow within max_dt_hours. Exact pairs are re-checked below with each
    # pair's *true* dt, so this only has to be an upper bound, not exact.
    coarse_radius = float(np.max(radii)) * 2 + v_max_kmh * max_dt_hours
    pairs = tree.query_pairs(coarse_radius, output_type="ndarray")

    if len(pairs) == 0:
        labels = np.arange(n)
    else:
        i_idx, j_idx = pairs[:, 0], pairs[:, 1]
        dt_hours = np.abs(ts[i_idx] - ts[j_idx]) / 3600.0
        within_time = dt_hours <= max_dt_hours
        allowed = radii[i_idx] + radii[j_idx] + v_max_kmh * dt_hours
        actual = np.hypot(x[i_idx] - x[j_idx], y[i_idx] - y[j_idx])
        linked = within_time & (actual <= allowed)

        ii, jj = i_idx[linked], j_idx[linked]
        if len(ii) == 0:
            labels = np.arange(n)
        else:
            graph = coo_matrix((np.ones(len(ii), dtype=bool), (ii, jj)), shape=(n, n))
            _n_components, labels = connected_components(graph, directed=False)

    clusters: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        clusters.setdefault(int(label), []).append(idx)
    return list(clusters.values())


# -------------------------------------------------------------- job kind


def _fetch_detections(
    conn: sqlite3.Connection,
    bbox: tuple[float, float, float, float],
    start_ts: int,
    end_ts: int,
    sources: list[str] | None,
    limit: int = MAX_CLUSTER_DETECTIONS,
) -> list[dict[str, Any]]:
    """Raw-SQL equivalent of Database.query_detections, self-contained for
    the worker process (see jobs.py's module docstring for why).

    ``limit`` bounds how many rows cluster_detections ever has to consider -
    see MAX_CLUSTER_DETECTIONS. Ordered newest-first so a truncated result
    keeps the most current activity rather than an arbitrary/oldest slice.
    """
    west, south, east, north = bbox
    where = ["acq_ts >= ?", "acq_ts <= ?", "latitude BETWEEN ? AND ?"]
    params: list[Any] = [start_ts, end_ts, south, north]
    if west <= east:
        where.append("longitude BETWEEN ? AND ?")
        params.extend([west, east])
    else:
        where.append("(longitude >= ? OR longitude <= ?)")
        params.extend([west, east])
    if sources:
        where.append(f"source IN ({', '.join('?' for _ in sources)})")
        params.extend(sources)

    sql = (
        "SELECT id, latitude, longitude, acq_ts, source, satellite, scan, track, frp "
        f"FROM detections WHERE {' AND '.join(where)} ORDER BY acq_ts DESC LIMIT ?"
    )
    params.append(int(limit))
    cols = ["id", "lat", "lon", "ts", "source", "satellite", "scan", "track", "frp"]
    rows = [dict(zip(cols, row)) for row in conn.execute(sql, params).fetchall()]
    rows.reverse()  # back to chronological order, which cluster_detections/callers expect
    return rows


def detect_events(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Job body: cluster cached detections in a bbox/time window into
    events and persist them. Creates fresh event rows each run - re-running
    over an overlapping window does not merge into previously found events
    (a documented v1 limitation, not a correctness bug)."""
    bbox = tuple(params["bbox"])  # west, south, east, north
    start_ts = int(params["start_ts"])
    end_ts = int(params["end_ts"])
    sources = params.get("sources") or None
    v_max_kmh = float(params.get("v_max_kmh", DEFAULT_V_MAX_KMH))
    max_dt_hours = float(params.get("max_dt_hours", DEFAULT_MAX_DT_HOURS))
    min_detections = int(params.get("min_detections", DEFAULT_MIN_DETECTIONS))

    conn = sqlite3.connect(ctx.db_path, timeout=30.0)
    try:
        rows = _fetch_detections(conn, bbox, start_ts, end_ts, sources)
        truncated = len(rows) >= MAX_CLUSTER_DETECTIONS
        note = f"{len(rows)} detections to cluster"
        if truncated:
            note += f" (capped at {MAX_CLUSTER_DETECTIONS}, most recent kept)"
            log.warning(
                "detect_events: %s detections exceeded the %d cap for bbox %s - "
                "clustering the most recent %d only",
                note,
                MAX_CLUSTER_DETECTIONS,
                bbox,
                MAX_CLUSTER_DETECTIONS,
            )
        ctx.report_progress(20, note=note)
        if not rows:
            return {"event_count": 0, "event_ids": [], "detection_count": 0, "truncated": False}

        clusters = cluster_detections(rows, v_max_kmh=v_max_kmh, max_dt_hours=max_dt_hours)
        ctx.report_progress(60, note=f"{len(clusters)} candidate clusters")

        now = int(time_module.time())
        run_params = {
            "v_max_kmh": v_max_kmh,
            "max_dt_hours": max_dt_hours,
            "min_detections": min_detections,
        }
        event_ids: list[int] = []

        conn.execute("BEGIN")
        for indices in clusters:
            if len(indices) < min_detections:
                continue
            members = [rows[i] for i in indices]
            lats = [m["lat"] for m in members]
            lons = [m["lon"] for m in members]
            tss = [m["ts"] for m in members]
            sats = sorted({m["source"] for m in members})

            cur = conn.execute(
                "INSERT INTO events (bbox_west, bbox_south, bbox_east, bbox_north, "
                "centroid_lat, centroid_lon, first_seen, last_seen, detection_count, "
                "sources_json, params_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    min(lons),
                    min(lats),
                    max(lons),
                    max(lats),
                    sum(lats) / len(lats),
                    sum(lons) / len(lons),
                    min(tss),
                    max(tss),
                    len(members),
                    json.dumps(sats),
                    json.dumps(run_params),
                    now,
                ),
            )
            event_id = cur.lastrowid
            conn.executemany(
                "INSERT OR IGNORE INTO event_members (event_id, detection_id) VALUES (?, ?)",
                [(event_id, m["id"]) for m in members],
            )
            event_ids.append(event_id)
        conn.commit()

        ctx.report_progress(100, note=f"{len(event_ids)} events stored")
        return {
            "event_count": len(event_ids),
            "event_ids": event_ids,
            "detection_count": len(rows),
            "singleton_dropped": len(clusters) - len(event_ids),
            "truncated": truncated,
        }
    finally:
        conn.close()


register_kind("detect_events", detect_events)
