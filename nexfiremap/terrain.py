"""Physics-based fire spread modelling - further_plan.md's Stage 4 (no ML).

Combines terrain (Copernicus DEM GLO-30), fuel (ESA WorldCover 10m, crosswalked to
the standard Anderson 13 fuel models), weather (Open-Meteo, free/keyless) and dead-
fuel moisture conditioning (`nexfiremap.moisture`) into a real Rothermel (1972,
Albini 1976-corrected) surface-fire spread kernel (`nexfiremap.rothermel`), then
solves for arrival time via an **anisotropic** graph-based fast-marching solve (see
`solve_travel_time_anisotropic`) - fire genuinely spreads faster downwind/upslope and
slower into the wind, not a uniform expanding circle.

This module is the orchestration layer (data fetch + job wiring) - the actual fire-
behaviour physics live in `nexfiremap.rothermel` (surface-fire spread rate, direction,
elliptical shape) and `nexfiremap.moisture` (dead-fuel moisture conditioning) - see
those modules' docstrings for the equations used and the simplifications each makes
relative to firemodel.md's full FlamMap-compatible spec (crown fire, spotting, and
the full Nelson moisture PDE are all explicitly out of scope - see the fire-
propagation upgrade plan for why).

**This module's own remaining simplifications**:

* Fuel behaviour uses one Anderson-13 fuel model per ESA WorldCover class via a
  best-fit crosswalk (`WORLDCOVER_TO_FUEL_MODEL`), not a locally-calibrated or
  Scott-Burgan-40 fuel map - WorldCover's 11 global land-cover classes can't
  distinguish e.g. chaparral from open pine forest, so this crosswalk is itself an
  approximation, just a standards-based one instead of an invented one.
* Wind is one value for the whole event AOI (an Open-Meteo point sample,
  circular-averaged over the event's active window) - no WindNinja-grade
  terrain-channelled downscaling - slope/aspect still locally deflect the *fire's*
  spread direction (via Rothermel's own slope term), just not the wind field itself.
* Dead-fuel moisture is one value per size class for the whole AOI (from the same
  Open-Meteo window, run through `nexfiremap.moisture`'s time-lag conditioning), not
  spatially varying.

The result is labelled "Modelled" throughout, in further_plan.md's own
Observed/Estimated/Modelled/Uncertainty framing - a physics-based estimate, not an
observation and not a statistical fit to observations either.
"""

from __future__ import annotations

import heapq
import json
import logging
import math
import sqlite3
import time as time_module
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import numpy as np
import planetary_computer as pc
import pystac_client
from rasterio.warp import Resampling
from skimage import measure

from . import moisture as moisture_model
from . import rothermel
from .geo import (
    LAT_M_PER_DEG,
    ROW_ORIGIN_NORTH,
    grid_geometry,
    lon_m_per_deg,
    row_to_lat,
    to_north_first,
)
from .imagery import read_band_on_grid
from .jobs import JobContext, register_kind
from .likelihood import (
    probability_envelopes,
    render_probability_png,
    render_recency_png,
)
from .rasterpng import encode_png

log = logging.getLogger("nexfiremap.terrain")

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
# The archive endpoint is a reanalysis product with a multi-day finalisation
# lag - reliable for anything settled, but exactly the hours that matter
# most for an *actively* analysed event (reference_ts defaulting to "now")
# can come back null. The forecast endpoint blends recent observations with
# its own near-real-time analysis instead of waiting on reanalysis, so
# fetch_weather uses it to backfill just the recent tail the archive left
# empty, rather than silently letting moisture.py carry the seed forward
# for what could be the event's most current hours.
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# How far back the forecast endpoint's own "recent past" window reaches -
# Open-Meteo supports up to past_days=92, but recent-and-actually-useful
# freshness (vs. archive) only really matters within the last couple of
# weeks - capping it keeps the extra request small and fast.
FORECAST_BACKFILL_MAX_DAYS = 14

# Named distinctly from geo.py's own MAX_GRID_DIM/DEFAULT_RESOLUTION_M
# (used by the likelihood/validation/imagery grid) - this module's physics
# solve grid has different resolution needs and used to shadow those names
# with different values under the same identifiers, which was a latent
# name collision (harmless only because every call site here always passes
# its own value explicitly rather than relying on the shared default).
TERRAIN_DEFAULT_RESOLUTION_M = 60.0
TERRAIN_MAX_GRID_DIM = 300

# ESA WorldCover class -> best-fit Anderson 13 fuel model number (0 = nonburnable).
# A crosswalk, not a measurement - see module docstring. Each choice documented since
# there's rarely one obviously-correct mapping from an 11-class global land-cover
# product to a 13-class North American fuel-structure model.
WORLDCOVER_TO_FUEL_MODEL: dict[int, int] = {
    10: 10,  # Tree cover -> "Timber litter and understory" (generic closed-canopy default)
    20: 5,  # Shrubland -> "Brush" (moderate shrub; denser chaparral would be model 4)
    30: 1,  # Grassland -> "Short grass" (most common wildfire grass default)
    40: 1,  # Cropland -> "Short grass" (conservative general default; highly seasonal in reality)
    50: 0,  # Built-up -> nonburnable
    60: 1,  # Bare / sparse vegetation -> "Short grass" (its light load ~= sparse spreadable fuel)
    70: 0,  # Snow and ice -> nonburnable
    80: 0,  # Permanent water -> nonburnable
    90: 3,  # Herbaceous wetland -> "Tall grass" (reed/marsh fuel when dry)
    95: 10,  # Mangroves -> "Timber litter and understory" (woody structure proxy)
    100: 1,  # Moss and lichen -> "Short grass" (light, sparse fuel proxy)
}

# Barrier floor for nonburnable cells, in the anisotropic solver's ROS units
# (m/min) - "essentially impassable" without a literal zero, matching the previous
# isotropic model's same convention (a true zero would make the barrier
# unconditionally impassable even when a real fire *can* cross a road/firebreak).
BARRIER_ROS_M_MIN = 1e-4

ISOCHRONE_HOURS = (3, 6, 12, 24, 48)


# ------------------------------------------------------------- DEM & fuel


def _catalog() -> pystac_client.Client:
    """Microsoft Planetary Computer STAC client, with the ``pc.sign_inplace``
    modifier so every search result's asset hrefs come back pre-signed -
    the DEM/WorldCover fetchers below can hand them straight to
    ``read_band_on_grid`` without a separate signing step."""
    return pystac_client.Client.open(STAC_URL, modifier=pc.sign_inplace)


def fetch_dem(bbox: tuple[float, float, float, float], geom: dict[str, Any]) -> np.ndarray:
    """Copernicus DEM GLO-30 on the shared AOI grid, mosaicking tiles if the
    AOI straddles a 1x1-degree DEM tile boundary."""
    catalog = _catalog()
    items = list(catalog.search(collections=["cop-dem-glo-30"], bbox=list(bbox)).items())
    if not items:
        raise RuntimeError("no Copernicus DEM tile covers this AOI")

    dem = np.full((geom["ny"], geom["nx"]), np.nan, dtype=np.float32)
    for item in items:
        tile = read_band_on_grid(pc.sign(item.assets["data"].href), geom, resampling=Resampling.bilinear, src_nodata=None)
        gap = np.isnan(dem) & ~np.isnan(tile)
        dem[gap] = tile[gap]
    return dem


def fetch_worldcover(bbox: tuple[float, float, float, float], geom: dict[str, Any]) -> np.ndarray:
    """ESA WorldCover 10m class codes on the shared AOI grid, most recent
    product version, mosaicked across any tile boundary the AOI straddles.

    WorldCover items cover a whole year, so they carry start/end_datetime
    rather than a single ``datetime`` (which is None) - sort by that
    instead. Item ids look like "ESA_WorldCover_10m_2021_v200_N36E027" -
    the trailing tile code is used to de-duplicate across product versions.
    """
    catalog = _catalog()
    items = list(catalog.search(collections=["esa-worldcover"], bbox=list(bbox)).items())
    if not items:
        raise RuntimeError("no ESA WorldCover tile covers this AOI")
    items.sort(key=lambda it: it.properties.get("start_datetime") or "", reverse=True)

    seen_tiles: set[str] = set()
    classes = np.full((geom["ny"], geom["nx"]), np.nan, dtype=np.float32)
    for item in items:
        # .get(key, default) only falls back when the key is *absent* - a
        # present-but-None value (as proj:code is here) still wins, so `or`
        # is used instead of relying on the dict default.
        tile_id = item.properties.get("proj:code") or item.id.rsplit("_", 1)[-1]
        if tile_id in seen_tiles:
            continue  # keep only the newest version per tile
        seen_tiles.add(tile_id)
        tile = read_band_on_grid(
            pc.sign(item.assets["map"].href), geom, resampling=Resampling.nearest, src_nodata=0
        )
        gap = np.isnan(classes) & ~np.isnan(tile)
        classes[gap] = tile[gap]
    return classes


def slope_from_dem(dem: np.ndarray, res_m: float) -> np.ndarray:
    """Slope angle (radians) via a simple finite-difference gradient - magnitude
    only. See `aspect_from_dem` for the companion direction."""
    filled = np.where(np.isnan(dem), np.nanmean(dem) if np.any(~np.isnan(dem)) else 0.0, dem)
    dy, dx = np.gradient(filled, res_m)
    return np.arctan(np.hypot(dx, dy))


def aspect_from_dem(dem: np.ndarray, res_m: float) -> np.ndarray:
    """Compass bearing (degrees, 0=North, 90=East) of the downhill direction -
    the direction Rothermel's slope term pushes spread toward. Derived from the same
    finite-difference gradient as `slope_from_dem`: the gradient (dy, dx) points
    toward increasing elevation, so downhill is its negation - converting the (row,
    col) = (south, east) grid axes to a (north, east) compass frame gives
    ``atan2(-dx, dy)``. Flat cells get an arbitrary 0 deg - harmless, since
    Rothermel's slope factor is ~0 there too, so aspect doesn't affect the result."""
    filled = np.where(np.isnan(dem), np.nanmean(dem) if np.any(~np.isnan(dem)) else 0.0, dem)
    dy, dx = np.gradient(filled, res_m)
    return np.degrees(np.arctan2(-dx, dy)) % 360.0


def fuel_model_grid(worldcover_classes: np.ndarray) -> np.ndarray:
    """WorldCover class codes -> Anderson 13 fuel model numbers (0 = nonburnable),
    via `WORLDCOVER_TO_FUEL_MODEL`. NaN (no data) cells become nonburnable rather
    than guessing."""
    out = np.zeros_like(worldcover_classes, dtype=np.int32)
    for code, fuel_number in WORLDCOVER_TO_FUEL_MODEL.items():
        out[worldcover_classes == code] = fuel_number
    return out


# ------------------------------------------------------------------ weather


def circular_mean_deg(degrees: list[float]) -> float:
    """Mean compass bearing, correctly wrapping the 0/360 seam - averaging
    e.g. 350deg and 10deg naively gives 180deg (due south) instead of the
    correct 0deg, so this averages the unit vectors (sin, cos) and converts
    back rather than averaging the degree values directly."""
    radians = np.radians(degrees)
    mean_angle = math.atan2(float(np.mean(np.sin(radians))), float(np.mean(np.cos(radians))))
    return math.degrees(mean_angle) % 360.0


_HOURLY_FIELDS = "wind_speed_10m,wind_direction_10m,relative_humidity_2m,temperature_2m,precipitation"


def _fetch_open_meteo_hourly(
    url: str, lat: float, lon: float, extra_params: dict[str, Any]
) -> dict[str, list[Any]] | None:
    """Shared GET against either Open-Meteo endpoint (archive or forecast)
    for the fixed ``_HOURLY_FIELDS`` set - ``extra_params`` carries
    whichever date/day-range parameters distinguish the two. Returns
    ``None`` (not a raised error) on any request/parse failure, so
    ``fetch_weather`` can treat the forecast backfill as best-effort."""
    try:
        response = httpx.get(
            url,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": _HOURLY_FIELDS,
                "wind_speed_unit": "ms",
                "timezone": "UTC",
                **extra_params,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        return response.json()["hourly"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("Open-Meteo request to %s failed: %s", url, exc)
        return None


def fetch_weather(lat: float, lon: float, start_ts: float, end_ts: float) -> dict[str, Any]:
    """Mean wind speed/direction (circular mean, not a naive degree average) over
    the window, from Open-Meteo's free historical archive, plus the raw hourly
    temperature/humidity/precipitation series over that same window for
    `nexfiremap.moisture`'s time-lag conditioning - reusing this one fetch rather
    than a second Open-Meteo call.

    The archive endpoint is a reanalysis product with a multi-day
    finalisation lag, so when the window reaches close to "now" (the
    default for an actively-analysed event), its most recent hours can come
    back null even though real data exists - the forecast endpoint's own
    recent-past window is used to backfill exactly those gaps, since it
    doesn't wait on reanalysis. Older hours are left as the archive
    reported them - the forecast endpoint isn't a wholesale replacement,
    just a source for the tail the archive hasn't settled yet.
    """
    start = datetime.fromtimestamp(start_ts, tz=timezone.utc).date()
    end = datetime.fromtimestamp(end_ts, tz=timezone.utc).date()
    hourly = _fetch_open_meteo_hourly(
        OPEN_METEO_URL,
        lat,
        lon,
        {"start_date": start.isoformat(), "end_date": end.isoformat()},
    )
    if hourly is None:
        raise RuntimeError("Open-Meteo archive request failed")

    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp() for t in hourly["time"]]
    in_window = [i for i, t in enumerate(times) if start_ts <= t <= end_ts]
    if not in_window:
        in_window = list(range(len(times)))  # fall back to the whole fetched range

    backfilled = 0
    now = datetime.now(timezone.utc)
    if end_ts >= (now - timedelta(days=FORECAST_BACKFILL_MAX_DAYS)).timestamp():
        missing = [
            i for i in in_window if any(hourly[field][i] is None for field in _HOURLY_FIELDS.split(","))
        ]
        if missing:
            recent = _fetch_open_meteo_hourly(
                OPEN_METEO_FORECAST_URL,
                lat,
                lon,
                {"past_days": FORECAST_BACKFILL_MAX_DAYS, "forecast_days": 1},
            )
            if recent is not None:
                recent_by_ts = {
                    datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp(): j
                    for j, t in enumerate(recent["time"])
                }
                for i in missing:
                    j = recent_by_ts.get(times[i])
                    if j is None:
                        continue
                    for field in _HOURLY_FIELDS.split(","):
                        if hourly[field][i] is None and recent[field][j] is not None:
                            hourly[field][i] = recent[field][j]
                    backfilled += 1

    speeds = [hourly["wind_speed_10m"][i] for i in in_window if hourly["wind_speed_10m"][i] is not None]
    dirs = [hourly["wind_direction_10m"][i] for i in in_window if hourly["wind_direction_10m"][i] is not None]
    humidity = [hourly["relative_humidity_2m"][i] for i in in_window if hourly["relative_humidity_2m"][i] is not None]

    return {
        "wind_speed_ms": float(np.mean(speeds)) if speeds else 0.0,
        "wind_direction_deg": circular_mean_deg(dirs) if dirs else 0.0,
        "relative_humidity_pct": float(np.mean(humidity)) if humidity else None,
        "hours_sampled": len(in_window),
        # How many of those hours were null from the archive and had to be
        # filled from the forecast endpoint's recent-past window - a large
        # count here means the archive was unusually behind for this
        # request, worth knowing even though the values themselves are
        # already merged in below.
        "hours_backfilled_recent": backfilled,
        # Chronological (oldest first, since `hourly[...]` and `in_window` both
        # preserve the API's original order) - fed straight into
        # `nexfiremap.moisture.condition_dead_fuel_moisture`.
        "hourly_temperature_c": [hourly["temperature_2m"][i] for i in in_window],
        "hourly_relative_humidity_pct": [hourly["relative_humidity_2m"][i] for i in in_window],
        "hourly_precip_mm": [hourly["precipitation"][i] for i in in_window],
    }


def dead_fuel_moisture_from_weather(weather: dict[str, Any]) -> np.ndarray:
    """Runs the fetched hourly weather through `nexfiremap.moisture`'s time-lag
    conditioning and assembles the 5-class moisture vector `nexfiremap.rothermel`
    expects (live-fuel moisture stays at `nexfiremap.moisture`'s documented
    defaults - no live-moisture data source, see that module's docstring)."""
    dead_1h, dead_10h, dead_100h = moisture_model.condition_dead_fuel_moisture(
        weather["hourly_temperature_c"],
        weather["hourly_relative_humidity_pct"],
        weather["hourly_precip_mm"],
    )
    return moisture_model.moisture_vector(dead_1h, dead_10h, dead_100h)


# ------------------------------------------------------- directional behaviour


def build_directional_behavior(
    worldcover_classes: np.ndarray,
    slope_rad: np.ndarray,
    aspect_deg: np.ndarray,
    wind_speed_ms: float,
    wind_from_deg: float,
    moisture: np.ndarray,
    fuel_mult: float = 1.0,
    spread_mult: float = 1.0,
    control_lines: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Per-cell directional fire behaviour (max spread rate/direction + elliptical
    eccentricity) from the real Rothermel kernel (`nexfiremap.rothermel`), replacing
    the old flat-multiplier speed field. ``fuel_mult``/``spread_mult`` are residual
    Monte-Carlo uncertainty multipliers applied *on top of* the physical spread rate
    (1.0 for the single deterministic run in `run_propagation`, while `run_ensemble_
    assimilation` samples them per member) - the moisture input already carries the
    "how dry is this fuel" physics, so these represent remaining model uncertainty,
    not a stand-in for it.
    """
    fuel_ids = fuel_model_grid(worldcover_classes)
    fuel_ids[np.isnan(worldcover_classes)] = 0
    behavior = rothermel.grid_directional_behavior(
        fuel_ids, slope_rad, aspect_deg, wind_speed_ms, wind_from_deg, moisture
    )
    max_ros = behavior["max_ros_m_min"] * fuel_mult * spread_mult
    nonburnable = fuel_ids <= 0
    # Completed control lines are barriers the operator built, folded into the
    # same mask as land-cover barriers rather than modelled separately. This is
    # the feedback edge that closes the loop: until now the model only knew what
    # the *land* was, never what the crews had done to it, so a run made after a
    # dozer line went in still projected fire straight through it. A line the
    # operator drew is ground truth of a kind no land-cover raster carries.
    #
    # It is deliberately the same treatment as a lake or a road: floored at
    # BARRIER_ROS_M_MIN rather than set to zero, so fire can still cross a line
    # given enough time - which is what real control lines do, and what the
    # solver's own comment at the barrier branch already assumes.
    if control_lines is not None:
        nonburnable = nonburnable | control_lines.astype(bool)
    max_ros[nonburnable] = BARRIER_ROS_M_MIN
    behavior["eccentricity"][nonburnable] = 0.0
    return {"max_ros_m_min": np.maximum(max_ros, BARRIER_ROS_M_MIN), "max_dir_deg": behavior["max_dir_deg"], "eccentricity": behavior["eccentricity"]}


# --------------------------------------------------------------- solving


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def _stencil(offsets: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int, float, float], ...]:
    """(row_delta, col_delta) -> (row_delta, col_delta, bearing_deg, distance_multiplier).

    Row 0 is north, so -row is north and the compass bearing of a step is
    ``atan2(dcol, -drow)``. Deriving both the bearing and the length from the
    offset rather than writing them out keeps a longer stencil from acquiring a
    transcription error in one of 16 hand-typed constants.
    """
    return tuple(
        (dr, dc, math.degrees(math.atan2(dc, -dr)) % 360.0, math.hypot(dr, dc))
        for dr, dc in offsets
    )


# The 8 unit steps: N, NE, E, SE, S, SW, W, NW.
_STEPS_8: tuple[tuple[int, int], ...] = (
    (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1),
)
# Plus the 8 knight's moves, giving 16 directions at 22.5 degrees apart.
#
# Why: a graph solve can only travel along its stencil's directions, so a head
# fire whose true bearing falls between two of them has to zigzag. With an
# 8-direction stencil the worst case is 22.5 degrees off-axis, and on a strongly
# wind-driven ellipse that is punishing - spread rate off the major axis
# collapses fast. Measured on a uniform field at the model's own eccentricity
# ceiling (LWR capped at 8:1, so e = sqrt(63)/8 = 0.992), the 8-direction solve
# reports head-fire arrival **11.5x later than the exact ellipse** at a 22.5
# degree wind; 16 directions cuts that to 4.5x, and the isotropic error from
# 8.2% to 1.4%. Crucially the error does *not* shrink as the grid is refined -
# it is the fixed set of directions, not the cell size - so a finer AOI never
# escapes it. See tests/test_solver_accuracy.py, which measures both.
#
# 4.5x is still a real overestimate at the extreme, and this is not a claim to
# have made the solve exact: a continuous anisotropic Eikonal solver is what
# would do that. It is a large, cheap, verified reduction in a bias that always
# points the same way - fire arriving *earlier* than forecast.
_STEPS_16: tuple[tuple[int, int], ...] = _STEPS_8 + (
    (-2, 1), (-1, 2), (1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1),
)

_NEIGHBORS: tuple[tuple[int, int, float, float], ...] = _stencil(_STEPS_16)

#: For each knight's move, the two unit-step cells it passes between. A long
#: step is only allowed when at least one of them is itself passable, so the
#: stencil can never connect two cells that the 8-neighbour graph could not
#: already connect. Without this a (-2,+1) hop would straddle a one-cell-thick
#: barrier - a rasterised control line is exactly that - and fire would tunnel
#: through a line the plan says was built, which is the one thing a barrier in
#: this model exists to prevent.
#: A knight's move spans one diagonal step and one axial step along its long
#: side; those are the two cells it passes between.
_KNIGHT_MIDPOINTS: dict[tuple[int, int], tuple[tuple[int, int], tuple[int, int]]] = {
    (dr, dc): (
        (_sign(dr), _sign(dc)),                                    # the diagonal neighbour
        (_sign(dr), 0) if abs(dr) == 2 else (0, _sign(dc)),        # the axial one, long side
    )
    for dr, dc in _STEPS_16[8:]
}


def solve_travel_time_anisotropic(
    max_ros_m_min: np.ndarray,
    max_dir_deg: np.ndarray,
    eccentricity: np.ndarray,
    ignition_rows: list[int],
    ignition_cols: list[int],
    res_m: float,
) -> np.ndarray:
    """Anisotropic arrival-time solve via Dijkstra over an 8-connected grid graph -
    replacing the old isotropic `scikit-fmm` solve. See the fire-propagation upgrade
    plan for why a discrete graph solve was chosen over a continuous anisotropic
    Eikonal solver (much lower implementation/verification risk, and handles a
    spatially-varying preferred direction - slope aspect isn't uniform across an AOI
    even when wind is).

    The edge cost from a burning cell A to a neighbour B is
    ``distance(A, B) / ROS_A(bearing(A->B))``, where ``ROS_A(theta)`` is A's own
    elliptical (Albini & Chase 1980) spread-rate function
    (`nexfiremap.rothermel.ros_at_bearing`) - so a cell's own fuel/wind/slope state
    determines how fast fire leaves it in *every* direction, not one scalar speed.
    Returns arrival time in seconds, same units/shape contract the old isotropic
    solver had.
    """
    ny, nx = max_ros_m_min.shape

    dist = np.full((ny, nx), np.inf)
    visited = np.zeros((ny, nx), dtype=bool)
    heap: list[tuple[float, int, int]] = []
    for r, c in zip(ignition_rows, ignition_cols):
        if 0 <= r < ny and 0 <= c < nx and dist[r, c] > 0:
            dist[r, c] = 0.0
            heapq.heappush(heap, (0.0, r, c))
    if not heap:
        raise ValueError("no ignition points fall inside the grid")

    while heap:
        d, r, c = heapq.heappop(heap)
        if visited[r, c]:
            continue
        visited[r, c] = True
        # No early-exit for barrier ("nonburnable") cells here: every cell's speed
        # is floored at BARRIER_ROS_M_MIN, never literally zero (a real fire *can*
        # eventually cross a road or a narrow water gap, just very slowly) - a real
        # bug during development short-circuited propagation right at a barrier's
        # first cell, silently leaving everything beyond it at `inf` regardless of
        # how far away it actually was. Barrier cells still propagate, just slowly.
        ros_here, dir_here, ecc_here = max_ros_m_min[r, c], max_dir_deg[r, c], eccentricity[r, c]
        for dr, dc, bearing, dist_mult in _NEIGHBORS:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < ny and 0 <= nc < nx) or visited[nr, nc]:
                continue
            midpoints = _KNIGHT_MIDPOINTS.get((dr, dc))
            if midpoints is not None:
                # A long step is a straighter *cost estimate* for a route the
                # 8-neighbour graph already has; it must never be a new route.
                # Requiring one passable cell between keeps a rasterised control
                # line - one cell thick - genuinely impassable instead of being
                # hopped over. See _KNIGHT_MIDPOINTS.
                passable = False
                for mr, mc in midpoints:
                    ar, ac = r + mr, c + mc
                    if 0 <= ar < ny and 0 <= ac < nx and max_ros_m_min[ar, ac] > BARRIER_ROS_M_MIN:
                        passable = True
                        break
                if not passable:
                    continue
            ros_dir = rothermel.ros_at_bearing(ros_here, dir_here, ecc_here, bearing) / 60.0  # m/s
            ros_dir = max(ros_dir, BARRIER_ROS_M_MIN / 60.0)
            nd = d + (res_m * dist_mult) / ros_dir
            if nd < dist[nr, nc]:
                dist[nr, nc] = nd
                heapq.heappush(heap, (nd, nr, nc))
    return dist


def isochrone_contours(
    travel_time_s: np.ndarray,
    geom: dict[str, Any],
    hours: tuple[float, ...] = ISOCHRONE_HOURS,
    max_ros_m_min: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    """GeoJSON contours of ``travel_time_s`` at each hour mark in ``hours``
    that's actually reached within the (barrier-excluded) grid - marching-
    squares isolines, same technique as ``likelihood.probability_envelopes``
    but on arrival time rather than probability mass. Hours beyond what the
    grid's own 99th-percentile travel time covers are skipped rather than
    drawn as an empty or degenerate contour.

    Each level produces two features from the *same* underlying rings:
    the original open LineString (unchanged, so every existing/cached
    caller keeps working) and a closed Polygon (``properties.kind ==
    "fill"``) of the "already reached by this hour" region
    (``travel_time_s <= level``) - closed the same naive way
    ``events._contour_band`` closes spread_topology's bands (append the
    first point back onto the end), which is a fine approximation here
    for the same reason it's fine there: a travel-time field's level set
    is one boundary per connected region, same shape either way. Isochrone
    regions nest cumulatively (hour 3's reached-area is a superset of hour
    1's) exactly like spread_topology's bands, so the frontend paints them
    with the identical latest-first/opaque technique for the two "spread
    over time" surfaces to read consistently - see app.js's
    drawIsochrones. Old cached job results predate the fill polygons and
    simply have no ``kind: "fill"`` features; the renderer falls back to
    its original line-only styling for those."""
    west, south, east, north = geom["bbox"]
    nx, ny = geom["nx"], geom["ny"]
    features = []
    mask = np.isfinite(travel_time_s)
    # Barrier cells (water/urban/snow - BARRIER_ROS_M_MIN) get a tiny but non-zero
    # speed so the solver has a finite field to route through - their own travel time
    # reflects how long the solver took to route *around* them, not the fire's
    # actual reach, and can dwarf every genuinely burnable cell (confirmed live: a
    # coastal AOI with enough open water pushed this percentile past a
    # barrier-dominated tail worth centuries). Excluded outright when the caller has
    # the ROS field to identify them, not just percentile-clipped.
    if max_ros_m_min is not None:
        mask &= max_ros_m_min > BARRIER_ROS_M_MIN * 10
    finite = travel_time_s[mask]
    if finite.size == 0:
        return features
    # 99th percentile of the (now barrier-free) remainder, not the true
    # max - still a robustness margin against any other outliers.
    max_hours = float(np.percentile(finite, 99)) / 3600.0

    for hrs in hours:
        if hrs > max_hours:
            continue
        level = hrs * 3600.0
        try:
            contours = measure.find_contours(np.nan_to_num(travel_time_s, nan=1e18), level)
        except Exception:  # pragma: no cover - defensive
            continue
        for contour in contours:
            if len(contour) < 3:
                continue
            # Row 0 of this grid is NORTH - `run_propagation`'s `to_rowcol`
            # indexes with (north - lat), matching raster convention. This once
            # read `south + (row / ny) * (north - south)`, which is the
            # south-first formula, so every isochrone rendered mirrored about
            # the AOI's centre latitude while the PNG built from the same array
            # rendered correctly - the two layers disagreed, and a crew placed
            # relative to the vector one stood on the wrong side of the fire.
            coords = [
                [round(west + (col / nx) * (east - west), 6),
                 round(row_to_lat(geom["bbox"], row, ny, origin=ROW_ORIGIN_NORTH), 6)]
                for row, col in contour
            ]
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {"hours": hrs},
                }
            )
            fill_coords = coords if coords[0] == coords[-1] else [*coords, coords[0]]
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [fill_coords]},
                    "properties": {"hours": hrs, "kind": "fill"},
                }
            )
    return features


# -------------------------------------------------------------- job kind


def run_propagation(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Job body: terrain + fuel + weather + moisture -> per-cell directional
    Rothermel fire behaviour -> anisotropic arrival-time raster for one event,
    seeded from its detections."""
    event_id = int(params["event_id"])
    resolution_m = float(params.get("resolution_m", TERRAIN_DEFAULT_RESOLUTION_M))
    pad_deg = float(params.get("pad_deg", 0.08))
    reference_ts = float(params.get("reference_ts") or time_module.time())

    conn = sqlite3.connect(ctx.db_path, timeout=30.0)
    try:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise ValueError(f"no such event: {event_id}")
        cols = [d[0] for d in conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).description]
        event = dict(zip(cols, row))

        det_rows = conn.execute(
            "SELECT d.latitude, d.longitude FROM detections d "
            "JOIN event_members m ON m.detection_id = d.id WHERE m.event_id = ?",
            (event_id,),
        ).fetchall()
    finally:
        conn.close()

    if not det_rows:
        raise ValueError(f"event {event_id} has no member detections to seed ignition from")

    bbox = (
        event["bbox_west"] - pad_deg,
        event["bbox_south"] - pad_deg,
        event["bbox_east"] + pad_deg,
        event["bbox_north"] + pad_deg,
    )
    geom = grid_geometry(bbox, desired_res_m=resolution_m, max_dim=TERRAIN_MAX_GRID_DIM, min_res_m=20.0)
    ctx.report_progress(10, note=f"grid {geom['nx']}x{geom['ny']} @ {geom['res_m']:.0f}m")

    dem = fetch_dem(bbox, geom)
    slope_rad = slope_from_dem(dem, geom["res_m"])
    aspect_deg = aspect_from_dem(dem, geom["res_m"])
    ctx.report_progress(25, note="terrain fetched")

    fuel_classes = fetch_worldcover(bbox, geom)
    ctx.report_progress(40, note="fuel map fetched")

    weather_start = min(event["first_seen"], reference_ts - 24 * 3600)
    weather = fetch_weather(event["centroid_lat"], event["centroid_lon"], weather_start, reference_ts)
    moisture = dead_fuel_moisture_from_weather(weather)
    ctx.report_progress(60, note=f"wind {weather['wind_speed_ms']:.1f} m/s, dead-1h moisture {moisture[0]:.0%}")

    behavior = build_directional_behavior(
        fuel_classes, slope_rad, aspect_deg, weather["wind_speed_ms"], weather["wind_direction_deg"], moisture
    )

    west, south, _, _ = bbox
    ignition_rows, ignition_cols = [], []
    for lat, lon in det_rows:
        col = int((lon - west) / (bbox[2] - bbox[0]) * geom["nx"])
        row = int((bbox[3] - lat) / (bbox[3] - bbox[1]) * geom["ny"])
        ignition_rows.append(row)
        ignition_cols.append(col)

    travel_time_s = solve_travel_time_anisotropic(
        behavior["max_ros_m_min"], behavior["max_dir_deg"], behavior["eccentricity"],
        ignition_rows, ignition_cols, geom["res_m"],
    )
    ctx.report_progress(85, note="anisotropic solve done")

    isochrones = isochrone_contours(travel_time_s, geom, max_ros_m_min=behavior["max_ros_m_min"])

    hours_to_arrival = travel_time_s / 3600.0
    hours_to_arrival[~np.isfinite(hours_to_arrival)] = np.nan
    png = render_recency_png(hours_to_arrival, max_hours=48.0, origin=ROW_ORIGIN_NORTH)

    (ctx.result_path / "spread_time.png").write_bytes(png)
    (ctx.result_path / "isochrones.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": isochrones})
    )
    impact_hours = hours_to_arrival.astype(np.float32)
    np.savez_compressed(
        ctx.result_path / "impact_surface.npz",
        median_hours=impact_hours,
        earliest_hours=impact_hours,
        latest_hours=impact_hours,
    )

    ctx.report_progress(100, note="done")
    burnable = behavior["max_ros_m_min"] > BARRIER_ROS_M_MIN * 10
    finite = travel_time_s[np.isfinite(travel_time_s) & burnable]
    typical_max_hours = round(float(np.percentile(finite, 95)) / 3600.0, 1) if finite.size else None
    return {
        "event_id": event_id,
        "bounds": [[bbox[1], bbox[0]], [bbox[3], bbox[2]]],
        "grid": {"nx": geom["nx"], "ny": geom["ny"], "res_m": geom["res_m"]},
        "reference_ts": reference_ts,
        "weather": {k: v for k, v in weather.items() if not k.startswith("hourly_")},
        "dead_fuel_moisture": {"1h": round(float(moisture[0]), 3), "10h": round(float(moisture[1]), 3), "100h": round(float(moisture[2]), 3)},
        "typical_max_travel_hours": typical_max_hours,
        # Distinct hour levels actually drawn, not the raw feature count -
        # isochrone_contours now emits two features (line + fill) per
        # contour ring, and a single hour level can already split into
        # several disjoint rings for a non-convex front, so counting
        # features was never quite "how many hour cutoffs" either; this is.
        "isochrone_count": len({f["properties"]["hours"] for f in isochrones}),
        "ignition_points": len(det_rows),
        "files": {
            "spread_time": "spread_time.png",
            "isochrones": "isochrones.geojson",
            "impact_surface": "impact_surface.npz",
        },
    }


# ------------------------------------------------- ensemble + assimilation
#
# Phase 4b: instead of one deterministic run, sample many plausible
# parameter sets (wind speed/direction bias, dead-fuel-moisture proxy, general
# spread-rate uncertainty, ignition position jitter) - classic Monte Carlo, not a
# learned model - and score each member against the event's own detections as they
# arrive, exactly like further_plan.md's section 7 describes.
#
# **Simplification from a textbook recursive particle filter**: each member
# solves its fast-marching travel time *once*, seeded only from the
# earliest detection(s), and every later detection is then scored against that
# same solve (comparing predicted vs actual arrival time at that location)
# and the member's weight accumulates that evidence. This is a batch
# weighted-ensemble calibration - it reweights parameter sets using all the
# observations, the core Bayesian idea a particle filter is built on - but
# it does not re-propagate from a resampled state between observations the
# way a fully recursive sequential Monte Carlo filter would. Resampling is
# therefore not needed to compute the outputs below (the final weights
# already summarise all the evidence) - effective sample size is reported so
# the weighting's quality is visible rather than hidden.

ENSEMBLE_SIZE_DEFAULT = 60
# Mirrors api.py's EnsembleRequest.n_members bounds (ge=5, le=300) - enforced
# here too, not just at that one route, because params reaching this job
# body don't have to come through it: POST /api/jobs {"kind":
# "run_ensemble_assimilation", ...} submits directly with no field
# validation at all. Without this, a large n_members reaches np.empty(
# (n_members, ny, nx)) below uncapped.
ENSEMBLE_SIZE_MIN = 5
ENSEMBLE_SIZE_MAX = 300
WIND_SPEED_BIAS_STD = 0.25  # multiplicative, centred on 1.0
WIND_DIR_BIAS_STD_DEG = 20.0  # additive, centred on 0 - now usable, see sample_ensemble
FUEL_MOISTURE_RANGE = (0.7, 1.3)
SPREAD_MULT_RANGE = (0.6, 1.6)
IGNITION_JITTER_M = 100.0
MIN_SCORING_SIGMA_S = 1800.0  # 30 min floor on the timing-error tolerance


def sample_ensemble(n: int, rng: np.random.Generator) -> list[dict[str, float]]:
    """Plain Monte Carlo sampling over the model's acknowledged uncertainties.
    ``wind_dir_bias_deg`` is now sampled (previously omitted because the isotropic
    solve had no way to use it - `nexfiremap.terrain`'s anisotropic solve does)."""
    return [
        {
            "wind_speed_bias": max(0.1, float(rng.normal(1.0, WIND_SPEED_BIAS_STD))),
            "wind_dir_bias_deg": float(rng.normal(0.0, WIND_DIR_BIAS_STD_DEG)),
            "fuel_mult": float(rng.uniform(*FUEL_MOISTURE_RANGE)),
            "spread_mult": float(rng.uniform(*SPREAD_MULT_RANGE)),
            "ignition_dx_m": float(rng.normal(0.0, IGNITION_JITTER_M)),
            "ignition_dy_m": float(rng.normal(0.0, IGNITION_JITTER_M)),
        }
        for _ in range(n)
    ]


def weighted_percentile_axis0(values: np.ndarray, weights: np.ndarray, percentile: float) -> np.ndarray:
    """Per-cell weighted percentile across the ensemble axis (axis 0).

    ``values``: (n_members, ny, nx). ``weights``: (n_members,), summing to 1.
    """
    order = np.argsort(values, axis=0)
    sorted_vals = np.take_along_axis(values, order, axis=0)
    sorted_weights = weights[order]  # fancy-indexes each cell's own member order
    cumulative = np.cumsum(sorted_weights, axis=0)
    target = percentile / 100.0 * cumulative[-1]
    idx = np.argmax(cumulative >= target, axis=0)
    return np.take_along_axis(sorted_vals, idx[None, ...], axis=0)[0]


def run_ensemble_assimilation(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Job body: Monte Carlo ensemble of the Phase-4a physics model, each
    member scored against the event's own detections as they arrive."""
    event_id = int(params["event_id"])
    resolution_m = float(params.get("resolution_m", TERRAIN_DEFAULT_RESOLUTION_M))
    pad_deg = float(params.get("pad_deg", 0.08))
    reference_ts = float(params.get("reference_ts") or time_module.time())
    n_members = max(
        ENSEMBLE_SIZE_MIN,
        min(ENSEMBLE_SIZE_MAX, int(params.get("n_members", ENSEMBLE_SIZE_DEFAULT))),
    )
    random_seed = params.get("random_seed")

    conn = sqlite3.connect(ctx.db_path, timeout=30.0)
    try:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise ValueError(f"no such event: {event_id}")
        cols = [d[0] for d in conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).description]
        event = dict(zip(cols, row))

        det_rows = conn.execute(
            "SELECT d.latitude, d.longitude, d.acq_ts FROM detections d "
            "JOIN event_members m ON m.detection_id = d.id WHERE m.event_id = ? "
            "ORDER BY d.acq_ts",
            (event_id,),
        ).fetchall()
    finally:
        conn.close()

    if len(det_rows) < 2:
        raise ValueError(
            f"event {event_id} needs at least 2 detections at different times to assimilate "
            "against (has {len(det_rows)}) - use run_propagation for a single-snapshot estimate"
        )

    bbox = (
        event["bbox_west"] - pad_deg,
        event["bbox_south"] - pad_deg,
        event["bbox_east"] + pad_deg,
        event["bbox_north"] + pad_deg,
    )
    geom = grid_geometry(bbox, desired_res_m=resolution_m, max_dim=TERRAIN_MAX_GRID_DIM, min_res_m=20.0)
    ctx.report_progress(5, note=f"grid {geom['nx']}x{geom['ny']} @ {geom['res_m']:.0f}m")

    dem = fetch_dem(bbox, geom)
    slope_rad = slope_from_dem(dem, geom["res_m"])
    aspect_deg = aspect_from_dem(dem, geom["res_m"])
    fuel_classes = fetch_worldcover(bbox, geom)
    ctx.report_progress(15, note="terrain and fuel fetched")

    weather_start = min(event["first_seen"], reference_ts - 24 * 3600)
    weather = fetch_weather(event["centroid_lat"], event["centroid_lon"], weather_start, reference_ts)
    moisture = dead_fuel_moisture_from_weather(weather)
    ctx.report_progress(25, note=f"wind {weather['wind_speed_ms']:.1f} m/s")

    # Seed only from the earliest detection(s) - everything after that is
    # held out to score the ensemble against, matching further_plan.md
    # section 11's rolling-holdout spirit even though the full validation
    # programme described there isn't built here.
    seed_ts = det_rows[0][2]
    seed_points = [(lat, lon) for lat, lon, ts in det_rows if ts <= seed_ts + 60]
    later_points = [(lat, lon, ts) for lat, lon, ts in det_rows if ts > seed_ts + 60]
    if not later_points:
        # every detection landed in the same overpass - fall back to
        # scoring against the last one anyway so the ensemble isn't inert.
        later_points = [det_rows[-1]]

    west, south, east, north = bbox

    def to_rowcol(lat: float, lon: float, dx_m: float = 0.0, dy_m: float = 0.0) -> tuple[int, int]:
        lon_adj = lon + dx_m / lon_m_per_deg(lat)
        lat_adj = lat + dy_m / LAT_M_PER_DEG
        col = int((lon_adj - west) / (east - west) * geom["nx"])
        row = int((north - lat_adj) / (north - south) * geom["ny"])
        return max(0, min(geom["ny"] - 1, row)), max(0, min(geom["nx"] - 1, col))

    rng = np.random.default_rng(random_seed)
    ensemble = sample_ensemble(n_members, rng)

    travel_stack = np.empty((n_members, geom["ny"], geom["nx"]), dtype=np.float64)
    log_likelihood = np.zeros(n_members)

    for i, member in enumerate(ensemble):
        behavior = build_directional_behavior(
            fuel_classes,
            slope_rad,
            aspect_deg,
            weather["wind_speed_ms"] * member["wind_speed_bias"],
            (weather["wind_direction_deg"] + member["wind_dir_bias_deg"]) % 360.0,
            moisture,
            fuel_mult=member["fuel_mult"],
            spread_mult=member["spread_mult"],
        )
        ignition_rowcols = [
            to_rowcol(lat, lon, member["ignition_dx_m"], member["ignition_dy_m"])
            for lat, lon in seed_points
        ]
        rows_ = [rc[0] for rc in ignition_rowcols]
        cols_ = [rc[1] for rc in ignition_rowcols]
        travel = solve_travel_time_anisotropic(
            behavior["max_ros_m_min"], behavior["max_dir_deg"], behavior["eccentricity"],
            rows_, cols_, geom["res_m"],
        )
        travel_stack[i] = travel

        for lat, lon, ts in later_points:
            row, col = to_rowcol(lat, lon)
            predicted_s = travel[row, col]
            actual_s = ts - seed_ts
            if not np.isfinite(predicted_s):
                log_likelihood[i] += -50.0  # effectively impossible under this member
                continue
            sigma = max(MIN_SCORING_SIGMA_S, 0.5 * actual_s)
            log_likelihood[i] += -0.5 * ((predicted_s - actual_s) / sigma) ** 2

        if i % max(1, n_members // 10) == 0:
            ctx.report_progress(25 + int(55 * i / n_members), note=f"member {i + 1}/{n_members}")

    log_likelihood -= log_likelihood.max()  # numerical stability before exponentiating
    weights = np.exp(log_likelihood)
    weights /= weights.sum()
    effective_sample_size = float(1.0 / np.sum(weights**2))
    ctx.report_progress(85, note=f"assimilated, ESS={effective_sample_size:.1f}/{n_members}")

    reference_elapsed_s = reference_ts - seed_ts
    reached = (travel_stack <= reference_elapsed_s).astype(np.float64)
    probability = np.tensordot(weights, reached, axes=(0, 0))

    earliest_travel_s = weighted_percentile_axis0(travel_stack, weights, 10.0)
    median_travel_s = weighted_percentile_axis0(travel_stack, weights, 50.0)
    latest_travel_s = weighted_percentile_axis0(travel_stack, weights, 90.0)
    hours_to_median = median_travel_s / 3600.0
    hours_to_median[~np.isfinite(hours_to_median)] = np.nan

    envelopes = probability_envelopes(probability, geom)
    ctx.report_progress(95, note="rendering")

    # terrain grids are north-first (see `to_rowcol`), so these were already
    # correct - the explicit origin just stops that being a coincidence.
    prob_png = render_probability_png(probability, origin=ROW_ORIGIN_NORTH)
    median_png = render_recency_png(hours_to_median, max_hours=48.0, origin=ROW_ORIGIN_NORTH)

    (ctx.result_path / "probability.png").write_bytes(prob_png)
    (ctx.result_path / "median_arrival.png").write_bytes(median_png)
    (ctx.result_path / "envelopes.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": envelopes})
    )
    def remaining_hours(surface: np.ndarray) -> np.ndarray:
        remaining = (surface - reference_elapsed_s) / 3600.0
        remaining = np.maximum(remaining, 0.0)
        remaining[~np.isfinite(remaining)] = np.nan
        return remaining.astype(np.float32)

    np.savez_compressed(
        ctx.result_path / "impact_surface.npz",
        median_hours=remaining_hours(median_travel_s),
        earliest_hours=remaining_hours(earliest_travel_s),
        latest_hours=remaining_hours(latest_travel_s),
    )

    ctx.report_progress(100, note="done")
    return {
        "event_id": event_id,
        "bounds": [[bbox[1], bbox[0]], [bbox[3], bbox[2]]],
        "grid": {"nx": geom["nx"], "ny": geom["ny"], "res_m": geom["res_m"]},
        "reference_ts": reference_ts,
        "weather": {k: v for k, v in weather.items() if not k.startswith("hourly_")},
        "n_members": n_members,
        "effective_sample_size": round(effective_sample_size, 1),
        "seed_detections": len(seed_points),
        "assimilated_observations": len(later_points),
        "max_probability": round(float(probability.max()), 4),
        "envelope_count": len(envelopes),
        "files": {
            "probability": "probability.png",
            "median_arrival": "median_arrival.png",
            "envelopes": "envelopes.geojson",
            "impact_surface": "impact_surface.npz",
        },
    }


register_kind("run_propagation", run_propagation)
register_kind("run_ensemble_assimilation", run_ensemble_assimilation)
