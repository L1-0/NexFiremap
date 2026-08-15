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


# --------------------------------------------------------- Web Mercator
#
# EPSG:3857's own constants. The "radius" is the sphere GIS software agreed to
# pretend the Earth is for this projection - it is the WGS84 *semi-major* axis
# used with spherical formulae, which is what makes 3857 a non-conformal
# approximation of 4326 rather than a true reprojection of it. Using the real
# ellipsoid here would put tiles a few hundred metres out of alignment with
# every other client's, so the "wrong" sphere is the correct choice.
WEB_MERCATOR_RADIUS_M = 6378137.0
#: Half the projected world's width in metres; the extent runs -this..+this on
#: both axes, which is why a tile grid at zoom z is exactly 2**z squares wide.
WEB_MERCATOR_ORIGIN_M = math.pi * WEB_MERCATOR_RADIUS_M
#: The latitude where the projection is truncated to make the world square.
#: Mercator sends the poles to infinity, so every tiled map cuts off here.
WEB_MERCATOR_MAX_LAT = 85.0511287798066


def lonlat_to_3857(lon_deg: float, lat_deg: float) -> tuple[float, float]:
    """Project WGS84 lon/lat to EPSG:3857 metres.

    Latitude is clamped rather than rejected: Mercator's ``tan`` term diverges
    at the poles, so a request touching 90 degrees would otherwise produce
    infinity and propagate a NaN bbox into an outgoing WMS request.
    """
    lat = max(-WEB_MERCATOR_MAX_LAT, min(WEB_MERCATOR_MAX_LAT, lat_deg))
    x = math.radians(lon_deg) * WEB_MERCATOR_RADIUS_M
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * WEB_MERCATOR_RADIUS_M
    return x, y


def mercator_3857_to_lonlat(x_m: float, y_m: float) -> tuple[float, float]:
    """Inverse of ``lonlat_to_3857``: EPSG:3857 metres back to WGS84 lon/lat.

    Needed by the shapefile importer, which has to accept layers a county GIS
    exported in Web Mercator without taking a pyproj dependency for one formula.
    """
    lon = math.degrees(x_m / WEB_MERCATOR_RADIUS_M)
    lat = math.degrees(2 * math.atan(math.exp(y_m / WEB_MERCATOR_RADIUS_M)) - math.pi / 2)
    return lon, lat


def tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """The EPSG:3857 extent of one XYZ tile, as ``(minx, miny, maxx, maxy)``.

    This is what turns an XYZ tile request into a WMS ``GetMap`` bbox, which is
    what lets a WMS layer be cached, pinned and packed by exactly the same
    ``layer/z/x/y`` machinery as a native tile layer (see `tiles.TileCache`).

    Note the Y flip: XYZ counts rows downward from the north-west corner, while
    projected coordinates increase northward. Getting this backwards produces a
    map that looks plausible but is mirrored top-to-bottom, which is precisely
    the kind of error that survives a casual glance - hence the explicit test.
    """
    if z < 0:
        raise ValueError("tile zoom must not be negative")
    span = (2.0 * WEB_MERCATOR_ORIGIN_M) / (1 << z)
    minx = -WEB_MERCATOR_ORIGIN_M + x * span
    maxy = WEB_MERCATOR_ORIGIN_M - y * span
    return minx, maxy - span, minx + span, maxy


def tile_bounds_4326(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """The same tile's extent in WGS84 degrees, as ``(west, south, east, north)``.

    WMS 1.3.0 servers may refuse EPSG:3857 and offer only CRS:84/EPSG:4326, so
    the WMS client needs both forms of the same tile envelope.
    """
    minx, miny, maxx, maxy = tile_bounds_3857(z, x, y)
    west, south = mercator_3857_to_lonlat(minx, miny)
    east, north = mercator_3857_to_lonlat(maxx, maxy)
    return west, south, east, north


# ------------------------------------------------------------------- UTM
#
# Enough of the inverse UTM projection to import a shapefile a county or Kreis
# GIS office exported, without taking a `pyproj` dependency for one formula.
# Only WGS84's ellipsoid is supported - a layer on a national datum (Germany's
# older Gauss-Kruger/DHDN, the US NAD27) needs a real datum shift, which is a
# genuinely different problem and is refused rather than approximated. See
# `ingest/shapefile.py`, which parses the .prj far enough to decide.
WGS84_A = 6378137.0                 # semi-major axis, metres
WGS84_F = 1 / 298.257223563         # flattening
UTM_SCALE_FACTOR = 0.9996           # k0, fixed by the UTM definition
UTM_FALSE_EASTING = 500000.0
UTM_FALSE_NORTHING = 10000000.0     # applied in the southern hemisphere only


def utm_to_lonlat(easting: float, northing: float, zone: int, *, northern: bool = True
                  ) -> tuple[float, float]:
    """Inverse UTM (WGS84 ellipsoid) to lon/lat in degrees.

    The standard footpoint-latitude series solution: undo the false
    easting/northing and scale factor, expand the meridional arc to recover the
    footpoint latitude, then correct off it. Accurate to a few millimetres
    inside a zone, which is orders of magnitude better than the data being
    imported.

    ``northern`` matters because UTM northings restart at the equator: a
    southern-hemisphere coordinate carries a 10,000 km false northing to keep
    it positive. Assuming northern for a southern layer places it in the wrong
    hemisphere entirely rather than slightly off.
    """
    if not 1 <= int(zone) <= 60:
        raise ValueError(f"UTM zone {zone} is outside 1-60")

    e2 = WGS84_F * (2 - WGS84_F)               # first eccentricity squared
    ep2 = e2 / (1 - e2)                        # second eccentricity squared
    x = easting - UTM_FALSE_EASTING
    y = northing - (0.0 if northern else UTM_FALSE_NORTHING)

    m = y / UTM_SCALE_FACTOR
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    mu = m / (WGS84_A * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    # Footpoint latitude: the latitude whose meridional arc equals `m`.
    phi1 = (mu
            + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
            + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
            + (151 * e1**3 / 96) * math.sin(6 * mu)
            + (1097 * e1**4 / 512) * math.sin(8 * mu))

    sin_phi1, cos_phi1, tan_phi1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1 = ep2 * cos_phi1**2
    t1 = tan_phi1**2
    n1 = WGS84_A / math.sqrt(1 - e2 * sin_phi1**2)          # radius of curvature in the prime vertical
    r1 = WGS84_A * (1 - e2) / (1 - e2 * sin_phi1**2) ** 1.5  # ...in the meridian
    d = x / (n1 * UTM_SCALE_FACTOR)

    latitude = phi1 - (n1 * tan_phi1 / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720)
    longitude = (d
                 - (1 + 2 * t1 + c1) * d**3 / 6
                 + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120) / cos_phi1

    central_meridian = math.radians((int(zone) - 1) * 6 - 180 + 3)
    return math.degrees(central_meridian + longitude), math.degrees(latitude)


def unwrap_lons(lons: np.ndarray, reference: float | None = None) -> tuple[np.ndarray, float]:
    """Longitudes re-expressed as one continuous run, plus the reference used.

    Longitude is circular, and every flat-projection distance calculation in
    this codebase subtracts raw degrees. That is fine anywhere except across
    the antimeridian, where it fails badly rather than approximately: 179.5 and
    -179.5 are half a degree apart on the ground and 359 degrees apart in
    arithmetic. A fire crossing 180 therefore clusters as two events, and the
    arithmetic mean of its longitudes lands near 0 - the opposite side of the
    planet from every detection in it.

    The raw-detection query path already handles wraparound (`lon_range_sql`);
    this is the same correctness for everything *derived* from those
    detections. Returns values that may lie outside [-180, 180] by design - a
    run spanning the antimeridian is continuous precisely because it is allowed
    to read 179.5, 180.2, 180.8 - so callers must use it for differences and
    means, not store it as a coordinate.

    :param lons: Longitudes in degrees.
    :param reference: Centre to unwrap around. Defaults to the *circular* mean,
        which is the only mean that is meaningful for an angle.
    """
    lons = np.asarray(lons, dtype=np.float64)
    if reference is None:
        radians = np.radians(lons)
        reference = math.degrees(math.atan2(float(np.sin(radians).mean()),
                                            float(np.cos(radians).mean())))
    # Each longitude moved to the equivalent angle nearest the reference.
    return reference + ((lons - reference + 180.0) % 360.0) - 180.0, float(reference)


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


# --------------------------------------------------------- row orientation
#
# Two raster conventions genuinely coexist in this codebase and both are
# defensible on their own terms:
#
#   ROW_ORIGIN_SOUTH - row 0 is the *south* edge. `grid_xy_m` below builds its
#       y axis this way (y grows north from the bbox's south edge), so every
#       analytical grid derived from it - likelihood's kernel density, arrival
#       IDW, probability envelopes - is south-first. It is the natural choice
#       for a metric frame where y points north.
#
#   ROW_ORIGIN_NORTH - row 0 is the *north* edge. `terrain.py` indexes with
#       `row = (north - lat) / (north - south) * ny`, matching raster/GeoTIFF
#       convention and PNG's own top-to-bottom row order.
#
# What is *not* defensible is leaving the choice implicit, which is how the two
# mirroring bugs happened: terrain's isochrone contours read a north-first grid
# with a south-first formula, and likelihood's PNGs encoded a south-first grid
# into a format whose first row is drawn at the north edge. Each module's other
# output was correct, so neither looked wrong on its own.
#
# Hence: every conversion between a row index and a latitude, and every raster
# handed to a renderer, goes through a helper here that *names* which end row 0
# is - with no default, so a new call site cannot silently inherit the wrong one.
ROW_ORIGIN_NORTH = "north"
ROW_ORIGIN_SOUTH = "south"


def row_to_lat(bbox: tuple[float, float, float, float], row: float, ny: int,
               *, origin: str) -> float:
    """Latitude of a (possibly fractional) grid row.

    :param bbox: (west, south, east, north).
    :param row: Row index; fractional values are what marching-squares returns.
    :param ny: Number of rows in the grid.
    :param origin: ``ROW_ORIGIN_NORTH`` or ``ROW_ORIGIN_SOUTH`` - which edge row
        0 sits on. Keyword-only and mandatory on purpose: the entire class of
        bug this helper exists to prevent comes from guessing it.
    """
    _, south, _, north = bbox
    fraction = row / ny
    if origin == ROW_ORIGIN_NORTH:
        return north - fraction * (north - south)
    if origin == ROW_ORIGIN_SOUTH:
        return south + fraction * (north - south)
    raise ValueError(f"origin must be {ROW_ORIGIN_NORTH!r} or {ROW_ORIGIN_SOUTH!r}, not {origin!r}")


def to_north_first(raster: np.ndarray, *, origin: str) -> np.ndarray:
    """A raster reoriented so row 0 is north, ready for PNG encoding.

    PNG stores rows top-to-bottom and Leaflet draws an image overlay's first
    row at the northern edge of its bounds, so "north-first" is what any
    renderer wants regardless of how the analysis chose to index. Passing an
    already-north-first raster is a no-op, which is what makes this safe to put
    on every render path rather than only the one known to need it.
    """
    if origin == ROW_ORIGIN_NORTH:
        return raster
    if origin == ROW_ORIGIN_SOUTH:
        return np.flipud(raster)
    raise ValueError(f"origin must be {ROW_ORIGIN_NORTH!r} or {ROW_ORIGIN_SOUTH!r}, not {origin!r}")


def grid_xy_m(geom: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Cell-centre coordinates in a local metric frame, shape (ny, nx).

    Row 0 is the grid's **south** edge (``ROW_ORIGIN_SOUTH``): ys grows with the
    row index and `detections_xy_m` measures y northward from the same corner.
    """
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
