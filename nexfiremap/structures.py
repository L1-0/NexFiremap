"""Cached building footprints and temporal fire-spread exposure.

OpenStreetMap supplies structure geometry when a connection is available.
Once scanned, the records live in SQLite and exposure assessment is entirely
local.  Results describe *modelled arrival at a structure footprint*. They do
not assert damage, ignition, evacuation status, or structural survivability.

Two independent steps:

* **Scanning** (`scan_structures`/`ensure_structures`): fetch building
  footprints for an area of interest from the Overpass API (OpenStreetMap's
  query engine) and cache them in SQLite, keyed by a snapped/rounded bbox so
  repeated scans of roughly the same area reuse one cached fetch instead of
  re-querying Overpass every time (`query_cache_is_fresh`).
* **Exposure assessment** (`assess_exposure`): given a *completed* spread
  model job's saved arrival-time surface (median/earliest/latest hours to
  reach each cell), samples that surface at each cached structure's location
  (and footprint, if known) to say roughly when fire modelled arrival there.
  Entirely offline/local - it never re-fetches from Overpass or re-runs the
  spread model, only reads what scanning and a prior model run already
  produced.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from .jobs import JobContext, register_kind

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
QUERY_CACHE_MAX_AGE_S = 30 * 86400
MAX_SCAN_AREA_DEG2 = 0.5
MAX_STRUCTURES_PER_SCAN = 50_000

RESIDENTIAL_BUILDINGS = {
    "house", "residential", "apartments", "detached", "semidetached_house",
    "terrace", "bungalow", "cabin", "farm", "ger", "static_caravan",
}
CRITICAL_AMENITIES = {
    "hospital", "clinic", "fire_station", "police", "school", "kindergarten",
    "nursing_home", "social_facility", "shelter",
}


def snap_bbox(bbox: tuple[float, float, float, float], grid_deg: float = 0.02) -> tuple[float, float, float, float]:
    """Rounds a bbox outward to a coarse grid (west/south floored,
    east/north ceiled - always grows, never shrinks the requested area) so
    the query cache key is stable across near-identical bboxes (e.g. the map
    panned/zoomed by a few pixels) instead of missing the cache on every
    slightly different viewport."""
    west, south, east, north = bbox
    return (
        math.floor(west / grid_deg) * grid_deg,
        math.floor(south / grid_deg) * grid_deg,
        math.ceil(east / grid_deg) * grid_deg,
        math.ceil(north / grid_deg) * grid_deg,
    )


def build_overpass_query(bbox: tuple[float, float, float, float]) -> str:
    """Overpass QL requesting every ``way``/``relation`` tagged ``building``
    in the bbox, with ``out tags center geom`` so the response carries each
    element's full ring geometry (for ways) and a fallback center point (for
    relations/anything geometry-less) in one round trip."""
    west, south, east, north = bbox
    clause = f"({south},{west},{north},{east})"
    return (
        "[out:json][timeout:60];\n(\n"
        f'  way["building"]{clause};\n'
        f'  relation["building"]{clause};\n'
        ");\nout tags center geom;"
    )


def _polygon_from_element(element: dict[str, Any]) -> dict[str, Any] | None:
    """Converts one Overpass ``geometry`` array into a GeoJSON Polygon ring,
    closing it (repeating the first point as the last) if Overpass didn't
    already - GeoJSON requires a closed ring, Overpass doesn't guarantee
    one. Returns None for anything with fewer than 3 usable points (not a
    real polygon), so the caller falls back to the element's center point
    instead."""
    points = element.get("geometry") or []
    coords = [[p.get("lon"), p.get("lat")] for p in points if p.get("lon") is not None and p.get("lat") is not None]
    if len(coords) < 3:
        return None
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return {"type": "Polygon", "coordinates": [coords]}


def parse_overpass_structures(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extracts building structures from a raw Overpass JSON response,
    computing each one's representative lat/lon as its ring centroid when
    a polygon was built, falling back to Overpass's own ``center`` field
    otherwise. Silently caps at MAX_STRUCTURES_PER_SCAN rather than raising,
    since a legitimately dense urban scan hitting the cap is expected, not
    an error condition."""
    structures: list[dict[str, Any]] = []
    for element in payload.get("elements", []):
        if element.get("type") not in {"way", "relation"} or "id" not in element:
            continue
        tags = element.get("tags") or {}
        if not tags.get("building"):
            continue
        geometry = _polygon_from_element(element)
        if geometry:
            ring = geometry["coordinates"][0][:-1]
            lon = sum(p[0] for p in ring) / len(ring)
            lat = sum(p[1] for p in ring) / len(ring)
        else:
            center = element.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        structures.append({
            "osm_type": element["type"], "osm_id": int(element["id"]),
            "lat": float(lat), "lon": float(lon), "geometry": geometry, "tags": tags,
        })
        if len(structures) >= MAX_STRUCTURES_PER_SCAN:
            break
    return structures


def fetch_structures_sync(client: httpx.Client, bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    """One blocking Overpass fetch - synchronous because this only ever runs
    inside a job's worker thread (see `scan_structures`/`ensure_structures`),
    not on the async request-handling path."""
    response = client.post(OVERPASS_URL, data={"data": build_overpass_query(bbox)}, timeout=90.0)
    response.raise_for_status()
    return parse_overpass_structures(response.json())


def query_cache_is_fresh(
    conn: sqlite3.Connection, bbox: tuple[float, float, float, float],
    max_age_s: float = QUERY_CACHE_MAX_AGE_S,
) -> bool:
    """Whether a *snapped* bbox was already fetched from Overpass within
    ``max_age_s`` - building footprints change slowly, so a month-old cache
    entry (the default) is still considered good enough to skip re-querying."""
    row = conn.execute(
        "SELECT fetched_at FROM structure_query_cache WHERE west=? AND south=? AND east=? AND north=?",
        snap_bbox(bbox),
    ).fetchone()
    return row is not None and time.time() - float(row[0]) <= max_age_s


def structures_in_bbox(
    conn: sqlite3.Connection, bbox: tuple[float, float, float, float], limit: int = 50_000,
) -> list[dict[str, Any]]:
    """Reads cached structures whose point location falls in ``bbox`` -
    always served from SQLite, never triggers a fetch itself (that's
    `ensure_structures`'s job)."""
    west, south, east, north = bbox
    rows = conn.execute(
        "SELECT id,osm_type,osm_id,latitude,longitude,geometry_json,tags_json,fetched_at "
        "FROM structures WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? LIMIT ?",
        (south, north, west, east, int(limit)),
    ).fetchall()
    return [{
        "id": r[0], "osm_type": r[1], "osm_id": r[2], "lat": r[3], "lon": r[4],
        "geometry": json.loads(r[5]) if r[5] else None,
        "tags": json.loads(r[6]) if r[6] else {}, "fetched_at": r[7],
    } for r in rows]


def ensure_structures(
    conn: sqlite3.Connection, bbox: tuple[float, float, float, float],
    max_age_s: float = QUERY_CACHE_MAX_AGE_S,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch-if-needed-then-read: refreshes the cache from Overpass only if
    it's stale/missing for this (snapped) bbox, then always returns the
    cached rows - so callers get one consistent read path regardless of
    whether a fetch just happened. The area cap guards Overpass's own
    server-side query cost (and this project's own memory) against an
    accidentally huge scan request; fetching *at* the snapped (grown, not
    shrunk) bbox rather than the raw request keeps the cache and the actual
    fetched coverage always in agreement.
    """
    snapped = snap_bbox(bbox)
    area = max(0.0, snapped[2] - snapped[0]) * max(0.0, snapped[3] - snapped[1])
    if area > MAX_SCAN_AREA_DEG2:
        raise ValueError(f"structure scan area is too large ({area:.3f} deg²; maximum {MAX_SCAN_AREA_DEG2})")
    fetched_now = False
    if not query_cache_is_fresh(conn, bbox, max_age_s):
        with httpx.Client(headers={"User-Agent": "NexFiremap/1.0 (structure exposure scan)"}) as client:
            fetched = fetch_structures_sync(client, snapped)
        now = int(time.time())
        conn.execute("BEGIN")
        # Upsert keyed on (osm_type, osm_id): a structure already cached from
        # an overlapping earlier scan gets its geometry/tags refreshed in
        # place rather than duplicated, so scanning the same building twice
        # from two overlapping bboxes never produces two rows for it.
        conn.executemany(
            "INSERT INTO structures (osm_type,osm_id,latitude,longitude,geometry_json,tags_json,fetched_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(osm_type,osm_id) DO UPDATE SET "
            "latitude=excluded.latitude,longitude=excluded.longitude,geometry_json=excluded.geometry_json," 
            "tags_json=excluded.tags_json,fetched_at=excluded.fetched_at",
            [(s["osm_type"], s["osm_id"], s["lat"], s["lon"],
              json.dumps(s["geometry"], separators=(",", ":")) if s["geometry"] else None,
              json.dumps(s["tags"], separators=(",", ":")), now) for s in fetched],
        )
        conn.execute(
            "INSERT INTO structure_query_cache (west,south,east,north,fetched_at,row_count) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(west,south,east,north) DO UPDATE SET fetched_at=excluded.fetched_at,row_count=excluded.row_count",
            (*snapped, now, len(fetched)),
        )
        conn.commit()
        fetched_now = True
    return structures_in_bbox(conn, bbox), fetched_now


def scan_structures(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Job body: thin wrapper around `ensure_structures` for the async job
    queue - opens its own connection since jobs run in a worker thread, not
    on the request's own connection."""
    bbox = tuple(float(v) for v in params["bbox"])
    ctx.report_progress(10, note="checking local structure cache")
    conn = sqlite3.connect(ctx.db_path, timeout=30.0)
    try:
        structures, fetched = ensure_structures(conn, bbox)
    finally:
        conn.close()
    ctx.report_progress(100, note=f"{len(structures)} structures cached")
    return {"bbox": list(bbox), "structure_count": len(structures), "fetched": fetched}


def _category(tags: dict[str, Any]) -> str:
    """Amenity takes priority over building type - a building tagged both
    ``building=house`` and ``amenity=nursing_home`` (a real OSM pattern) is
    "critical", not "residential", since that's the higher-priority
    evacuation/response concern."""
    if tags.get("amenity") in CRITICAL_AMENITIES:
        return "critical"
    if tags.get("building") in RESIDENTIAL_BUILDINGS:
        return "residential"
    return "other"


def _label(tags: dict[str, Any], osm_id: int) -> tuple[str, str]:
    """Best-effort human-readable name: OSM ``name`` tag, else a composed
    street address, else a generic OSM-id fallback so every structure has
    *some* label even when OSM has neither for it."""
    address = " ".join(str(v) for v in [tags.get("addr:street"), tags.get("addr:housenumber")] if v)
    name = tags.get("name") or address or f"OSM structure {osm_id}"
    return str(name), address


def _sample_hours(surface: np.ndarray, structure: dict[str, Any], bounds: list[list[float]]) -> float | None:
    """Samples an arrival-time raster at a structure's location, taking the
    *minimum* across every sampled point (the structure's center plus its
    footprint's vertices, when known) rather than just the center - a large
    building's nearest corner to the fire is what actually determines when
    it's first exposed, not its centroid. Returns None if every sampled
    point falls outside the raster's bounds or lands on a non-finite
    (never-reached) cell."""
    south, west = bounds[0]
    north, east = bounds[1]
    points = [[structure["lon"], structure["lat"]]]
    geometry = structure.get("geometry")
    if geometry and geometry.get("type") == "Polygon":
        points.extend(geometry.get("coordinates", [[]])[0])
    values: list[float] = []
    ny, nx = surface.shape
    for lon, lat, *_ in points:
        if not (west <= lon <= east and south <= lat <= north):
            continue
        col = min(nx - 1, max(0, int((lon - west) / max(1e-12, east - west) * nx)))
        row = min(ny - 1, max(0, int((north - lat) / max(1e-12, north - south) * ny)))
        value = float(surface[row, col])
        if np.isfinite(value):
            values.append(value)
    return min(values) if values else None


def _bucket(hours: float | None) -> str:
    """Coarse, UI-friendly exposure-timing category from a raw hours value -
    the fixed thresholds (2/6/12/24/48h) match the same bands the frontend's
    exposure legend/filtering uses."""
    if hours is None:
        return "not_reached"
    if hours <= 0:
        return "reached"
    for threshold in (2, 6, 12, 24, 48):
        if hours <= threshold:
            return f"within_{threshold}h"
    return "beyond_48h"


def assess_exposure(
    conn: sqlite3.Connection, job: dict[str, Any], job_dir: Path,
) -> dict[str, Any]:
    """Builds a structures FeatureCollection with each cached structure's
    modelled exposure timing, read entirely from a *previously completed*
    spread/ensemble job's saved arrival-time surface (``impact_surface``) -
    this function never runs or re-runs a model itself, only samples what
    one already produced. Requires that job to have a saved impact surface
    at all (older job results predate this feature) and validates the
    resolved surface path stays under that job's own result directory
    before reading it."""
    if job.get("status") != "done" or job.get("kind") not in {"run_propagation", "run_ensemble_assimilation"}:
        raise ValueError("structure exposure requires a completed spread or ensemble job")
    result = json.loads(job.get("result_json") or "{}")
    filename = (result.get("files") or {}).get("impact_surface")
    if not filename:
        raise ValueError("this analysis predates structure timing support; run the spread model again")
    path = (job_dir / str(job["id"]) / Path(filename).name).resolve()
    root = (job_dir / str(job["id"])).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("analysis impact surface is missing")
    with np.load(path, allow_pickle=False) as data:
        median = data["median_hours"]
        earliest = data["earliest_hours"]
        latest = data["latest_hours"]
    bounds = result["bounds"]
    bbox = (bounds[0][1], bounds[0][0], bounds[1][1], bounds[1][0])
    structures = structures_in_bbox(conn, bbox)
    reference_ts = float(result["reference_ts"])
    features, summary = [], {key: 0 for key in ["reached", "within_2h", "within_6h", "within_12h", "within_24h", "within_48h", "beyond_48h", "not_reached"]}
    categories = {"residential": 0, "critical": 0, "other": 0}
    for structure in structures:
        median_h = _sample_hours(median, structure, bounds)
        earliest_h = _sample_hours(earliest, structure, bounds)
        latest_h = _sample_hours(latest, structure, bounds)
        bucket = _bucket(median_h)
        category = _category(structure["tags"])
        summary[bucket] += 1
        if median_h is not None and median_h <= 48:
            categories[category] += 1
        name, address = _label(structure["tags"], structure["osm_id"])
        impact_at = None if median_h is None else datetime.fromtimestamp(reference_ts + max(0.0, median_h) * 3600, timezone.utc).isoformat()
        geometry = structure["geometry"] or {"type": "Point", "coordinates": [structure["lon"], structure["lat"]]}
        features.append({"type": "Feature", "id": f"osm-{structure['osm_type']}-{structure['osm_id']}",
                         "geometry": geometry, "properties": {
            "structure_id": structure["id"], "osm_type": structure["osm_type"], "osm_id": structure["osm_id"],
            "name": name, "address": address, "building": structure["tags"].get("building"),
            "amenity": structure["tags"].get("amenity"), "category": category, "impact_bucket": bucket,
            "impact_hours": round(median_h, 2) if median_h is not None else None,
            "earliest_hours": round(earliest_h, 2) if earliest_h is not None else None,
            "latest_hours": round(latest_h, 2) if latest_h is not None else None,
            "impact_at": impact_at, "model_kind": job["kind"], "reference_ts": reference_ts,
        }})
    return {"type": "FeatureCollection", "features": features, "meta": {
        "job_id": job["id"], "model_kind": job["kind"], "reference_ts": reference_ts,
        "structure_count": len(features), "summary": summary, "affected_categories_48h": categories,
        "bounds": bounds, "cached_only": True,
        "disclaimer": "Scenario exposure timing only; not a confirmed damage or evacuation assessment.",
    }}


register_kind("scan_structures", scan_structures)
