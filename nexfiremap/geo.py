"""Shared geodesy/geometry helpers used across the analysis modules.

Small, dependency-light utilities for local flat-earth projections,
great-circle distance, sensor-footprint radius, antimeridian-safe bbox
handling, and the shared likelihood/validation/imagery raster grid.
Centralised here because event clustering (events.py), the industrial
classifier (industrial.py), the likelihood/arrival-time rasters
(likelihood.py), the validation baselines (validation.py), and the
burn-scar imagery grid (imagery.py) had each grown their own slightly
different copy of the same handful of formulas. terrain.py's DEM/fuel
solve grid has different resolution needs and keeps its own constants,
but reuses ``grid_geometry`` and the projection helpers below too.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

EARTH_RADIUS_KM = 6371.0
LAT_KM_PER_DEG = 110.574
LAT_M_PER_DEG = LAT_KM_PER_DEG * 1000.0

DEFAULT_FOOTPRINT_RADIUS_KM = 1.0

# Grid-geometry defaults shared by the likelihood/arrival-time raster, the
# validation baselines, and the burn-scar imagery grid.
MIN_RESOLUTION_M = 30.0
DEFAULT_RESOLUTION_M = 100.0
MAX_GRID_DIM = 260


def lon_km_per_deg(lat_deg: float) -> float:
    """Longitude-degree width in km at a given latitude (flat-earth approximation)."""
    return 111.320 * math.cos(math.radians(lat_deg)) or 1e-6


def lon_m_per_deg(lat_deg: float) -> float:
    """Longitude-degree width in metres at a given latitude."""
    return lon_km_per_deg(lat_deg) * 1000.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def footprint_radius_km(
    scan: float | None,
    track: float | None,
    default: float = DEFAULT_FOOTPRINT_RADIUS_KM,
) -> float:
    """Sensor footprint radius from a FIRMS row's scan/track pixel size (km),
    falling back to ``default`` when either is missing or zero."""
    return (math.hypot(scan or 0.0, track or 0.0) / 2.0) or default


def detection_footprint_radius_km(
    det: dict[str, Any], default: float = DEFAULT_FOOTPRINT_RADIUS_KM
) -> float:
    """Convenience wrapper for ``footprint_radius_km`` over a detection row
    dict carrying ``scan``/``track`` keys (the shape used throughout this
    codebase's detection rows)."""
    return footprint_radius_km(det.get("scan"), det.get("track"), default)


def split_antimeridian(
    bbox: tuple[float, float, float, float]
) -> list[tuple[float, float, float, float]]:
    """Split a bbox that crosses the antimeridian (``west > east``) into one
    or two bboxes that don't. A non-crossing bbox comes back unchanged, as a
    single-element list, so callers can always just iterate the result."""
    west, south, east, north = bbox
    if west <= east:
        return [bbox]
    return [(west, south, 180.0, north), (-180.0, south, east, north)]


def lon_range_sql(column: str, west: float, east: float) -> tuple[str, list[float]]:
    """SQL fragment (with params) selecting rows whose ``column`` longitude
    falls within ``[west, east]``, correctly handling an antimeridian-crossing
    range (``west > east``) as a wraparound OR instead of an empty range."""
    if west <= east:
        return f"{column} BETWEEN ? AND ?", [west, east]
    return f"({column} >= ? OR {column} <= ?)", [west, east]


def grid_geometry(
    bbox: tuple[float, float, float, float],
    desired_res_m: float = DEFAULT_RESOLUTION_M,
    max_dim: int = MAX_GRID_DIM,
    min_res_m: float = MIN_RESOLUTION_M,
) -> dict[str, Any]:
    """Pick a grid resolution that stays under ``max_dim`` cells on a side
    regardless of the event's size, so compute cost never runs away."""
    west, south, east, north = bbox
    lat0 = (south + north) / 2.0
    lon_m = lon_m_per_deg(lat0)
    width_m = max(1.0, (east - west) * lon_m)
    height_m = max(1.0, (north - south) * LAT_M_PER_DEG)
    span_m = max(width_m, height_m)

    res_m = max(min_res_m, desired_res_m, span_m / max_dim)
    nx = max(1, min(max_dim, round(width_m / res_m)))
    ny = max(1, min(max_dim, round(height_m / res_m)))

    return {
        "bbox": bbox,
        "lat0": lat0,
        "lon_m_per_deg": lon_m,
        "res_m": res_m,
        "nx": nx,
        "ny": ny,
        "width_m": width_m,
        "height_m": height_m,
    }


def grid_xy_m(geom: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Cell-centre coordinates in a local metric frame, shape (ny, nx)."""
    nx, ny = geom["nx"], geom["ny"]
    xs = (np.arange(nx) + 0.5) / nx * geom["width_m"]
    ys = (np.arange(ny) + 0.5) / ny * geom["height_m"]
    return np.meshgrid(xs, ys)  # gx, gy each (ny, nx)


def detections_xy_m(
    geom: dict[str, Any], lats: np.ndarray, lons: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Project lat/lon (arrays or scalars) into the same local metric frame
    as ``grid_xy_m``, relative to the grid's own west/south corner."""
    west, south, _, _ = geom["bbox"]
    x = (np.asarray(lons, dtype=np.float64) - west) * geom["lon_m_per_deg"]
    y = (np.asarray(lats, dtype=np.float64) - south) * LAT_M_PER_DEG
    return x, y
