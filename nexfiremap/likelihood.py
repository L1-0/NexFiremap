"""Active-heat likelihood, arrival-time estimate, and uncertainty envelopes
for a single fire event - further_plan.md's "Estimated affected extent" and
"Uncertainty" layers, kept visually and technically separate from the raw
"Observed" detections (Phase 1's job) and any future "Modelled propagation"
(Phase 4).

Four classical, non-learned techniques, each doing one job:

* **Active-heat likelihood** (doc section 6): a rasterised probability that
  a spot was thermally active near a reference time, built from a Gaussian
  kernel per detection (sized by its sensor footprint), weighted by sensor/
  confidence/FRP, and exponentially decayed with time since detection.
* **Tri-state suppression** (doc section 2/6): a valid satellite pass over
  the event's AOI that did *not* coincide with a detection is positive
  evidence of absence and pulls the likelihood down. A cell no satellite
  ever looked at ("unknown") is left untouched. See
  ``orbits.aoi_clear_passes`` for the geometry and ``_clear_pass_suppression``
  below for how it's folded in here. Soft-dependent on the optional
  ``orbits`` feature (skyfield) - silently skipped, not an error, if that
  module isn't installed, so "likelihood" and "orbits" stay independently
  optional per api.py's feature-availability design.
* **Arrival-time estimate**: a weighted (inverse-distance) spatial average
  of *when* nearby detections happened, with a weighted standard deviation
  as an honest earliest/median/latest spread - not a single fake-precise
  timestamp.
* **Uncertainty envelopes**: 50/80/90% *probability-mass* contours - the
  smallest region whose total likelihood mass reaches that fraction, found
  by sorting cells by value and walking the cumulative sum - not an
  arbitrary fixed threshold.

Rasters are pre-rendered to PNG server-side (see rasterpng.py) so the
browser only ever loads an image + its geographic bounds, never raw grids.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time as time_module
from datetime import datetime, timezone
from typing import Any

import numpy as np
from skimage import measure

from .geo import (
    DEFAULT_RESOLUTION_M,
    LAT_M_PER_DEG,
    MAX_GRID_DIM,
    MIN_RESOLUTION_M,
    detections_xy_m,
    grid_geometry,
    grid_xy_m,
)
from .jobs import JobContext, register_kind
from .rasterpng import encode_png

log = logging.getLogger("nexfiremap.likelihood")

# Trust weight by instrument resolution (VIIRS 375m finest of the NRT trio,
# MODIS 1km coarsest, Landsat OLI 30m but slower revisit / less mature NRT).
SENSOR_WEIGHT = {"VIIRS": 1.0, "MODIS": 0.7, "OLI": 0.9}
CONFIDENCE_WEIGHT = {"high": 1.0, "nominal": 0.7, "low": 0.35, None: 0.5}

DEFAULT_TAU_HOURS = 6.0
DEFAULT_FOOTPRINT_RADIUS_KM = 1.0
MIN_SIGMA_M = 150.0
# MAX_GRID_DIM/MIN_RESOLUTION_M/DEFAULT_RESOLUTION_M/LAT_M_PER_DEG/
# grid_geometry/grid_xy_m/detections_xy_m all live in geo.py now (shared
# with validation.py/imagery.py/terrain.py, which used to each carry their
# own near-identical copy) and are imported above rather than redefined
# here - re-exported under these same names since imagery.py/terrain.py/
# validation.py and their tests already do `from .likelihood import
# grid_geometry`.

# How much a single valid clear (no-fire) satellite pass discounts the
# active-heat likelihood, recency-weighted by the same tau as the positive
# kernel. Undocumented/uncalibrated against real historical fires - the doc
# itself says these weights need empirical calibration (section 6), which
# needs the validation programme (section 11, not yet built - see README).
# A conservative default that never fully zeroes a cell (floor below) is
# used until then.
CLEAR_PASS_SUPPRESSION_WEIGHT = 0.6
CLEAR_PASS_MIN_FACTOR = 0.05
CLEAR_PASS_COINCIDENCE_S = 1800.0

# further_plan.md section 2 is explicit that cloud/smoke-obscured imagery is
# "unknown", not "confirmed clear" - but orbits.aoi_clear_passes only checks
# geometric swath coverage (no cloud/quality mask exists in any free data
# source this project reaches), so on its own a "clear pass" here can't
# actually distinguish a genuinely cloud-free look from a cloudy one, and
# suppressing on the latter would damp real fire signal on exactly the kind
# of day the doc warns about. _clear_pass_suppression softens this with an
# Open-Meteo historical cloud-cover sample (free, keyless, already this
# project's weather source elsewhere) rather than trusting geometry alone.
# VIIRS/MODIS are thermal-IR sensors that can often still see a fire through
# thin-to-moderate cloud, so full cloud cover reduces confidence rather than
# zeroing it out - hence a floor, not zero.
CLEAR_PASS_CLOUD_MIN_CLARITY = 0.3
# A cloud sample more than this far from the pass's own timestamp is
# treated as not usably close (full clarity assumed) rather than penalising
# a pass for a data gap that isn't its fault.
CLEAR_PASS_CLOUD_MAX_GAP_S = 2 * 3600.0
OPEN_METEO_CLOUD_URL = "https://archive-api.open-meteo.com/v1/archive"

# Same OKLCH-validated single-hue fire ramp used for point styling in
# app.css (dark-tone steps) - one visual language for "how hot/how recent"
# across the whole app, point marks and rasters alike. Re-validated with
# wider per-step lightness gaps (~0.10-0.115 vs. the ~0.06 bare minimum)
# after real-world feedback that the original steps were too close to
# read as distinct at small marker sizes over a busy basemap.
FIRE_RAMP_RGB = [
    (0x91, 0x39, 0x00),
    (0xBB, 0x52, 0x00),
    (0xDF, 0x73, 0x33),
    (0xFB, 0x9A, 0x66),
    (0xFF, 0xC6, 0xA4),
]


# ------------------------------------------------------------- likelihood


def active_heat_raster(
    detections: list[dict[str, Any]],
    geom: dict[str, Any],
    reference_ts: float,
    tau_hours: float = DEFAULT_TAU_HOURS,
) -> np.ndarray:
    """P_active(x, reference_ts) per doc section 6, as a (ny, nx) array."""
    ny, nx = geom["ny"], geom["nx"]
    survival = np.ones((ny, nx), dtype=np.float64)
    if not detections:
        return 1.0 - survival

    gx, gy = grid_xy_m(geom)
    lats = np.array([d["lat"] for d in detections])
    lons = np.array([d["lon"] for d in detections])
    dx_all, dy_all = detections_xy_m(geom, lats, lons)

    for i, det in enumerate(detections):
        radius_km = (math.hypot(det.get("scan") or 0.0, det.get("track") or 0.0) / 2.0) or (
            DEFAULT_FOOTPRINT_RADIUS_KM
        )
        sigma = max(MIN_SIGMA_M, radius_km * 1000.0)
        ddx = gx - dx_all[i]
        ddy = gy - dy_all[i]
        kernel = np.exp(-(ddx**2 + ddy**2) / (2.0 * sigma**2))

        dt_hours = (reference_ts - det["ts"]) / 3600.0
        decay = math.exp(-dt_hours / tau_hours) if dt_hours > 0 else 1.0

        q = SENSOR_WEIGHT.get(det.get("instrument"), 0.6) * CONFIDENCE_WEIGHT.get(
            det.get("confidence"), 0.5
        )
        frp_boost = 1.0 + min(1.5, math.log1p(max(0.0, det.get("frp") or 0.0)) / 6.0)

        contribution = np.clip(q * kernel * decay * frp_boost, 0.0, 0.999)
        survival *= 1.0 - contribution

    return 1.0 - survival


# --------------------------------------------------------- tri-state fusion


def _fetch_cloud_cover(lat: float, lon: float, start_ts: float, end_ts: float) -> dict[float, float]:
    """Hourly cloud-cover percentage (0-100), keyed by unix timestamp, from
    Open-Meteo's free historical archive - the same free/keyless source
    terrain.py already uses for wind/humidity. Returns ``{}`` on any
    failure (network, malformed response, ...): the caller falls back to
    full suppression weight rather than raising, matching the "a
    refinement, not a requirement" pattern the orbits import above uses -
    this is explicitly a best-effort softening, not something event
    analysis should ever fail over.
    """
    try:
        import httpx

        start = datetime.fromtimestamp(start_ts, tz=timezone.utc).date()
        end = datetime.fromtimestamp(end_ts, tz=timezone.utc).date()
        response = httpx.get(
            OPEN_METEO_CLOUD_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "hourly": "cloud_cover",
                "timezone": "UTC",
            },
            timeout=15.0,
        )
        response.raise_for_status()
        hourly = response.json()["hourly"]
        out: dict[float, float] = {}
        for t, cover in zip(hourly["time"], hourly["cloud_cover"]):
            if cover is None:
                continue
            ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp()
            out[ts] = float(cover)
        return out
    except Exception:
        log.warning(
            "Cloud-cover lookup failed - clear passes keep full suppression weight", exc_info=True
        )
        return {}


def _clear_pass_suppression(
    conn: sqlite3.Connection,
    bbox: tuple[float, float, float, float],
    detections: list[dict[str, Any]],
    reference_ts: float,
    tau_hours: float = DEFAULT_TAU_HOURS,
) -> tuple[float, int]:
    """Aggregate suppression factor from valid clear satellite passes over
    this event's AOI (further_plan.md doc section 2/6's tri-state model).
    Applied uniformly across the raster: every tracked sensor's swath
    (1165-1500 km) is far wider than one event's AOI, so a pass that swept
    the AOI's centre swept the whole small grid - see
    ``orbits.aoi_clear_passes``.

    Lazily imports the optional ``orbits`` module (skyfield) and degrades
    to "no suppression" rather than raising if it isn't installed, so a
    missing orbits dependency narrows this one refinement rather than
    breaking event analysis entirely.

    ``orbits.aoi_clear_passes`` only checks geometric swath coverage - a
    pass counted "clear" there could just as easily have been cloud- or
    smoke-obscured, which the doc's own tri-state model calls "unknown",
    not "confirmed clear" (section 2). Each pass's weight is scaled by a
    clear-sky clarity factor from Open-Meteo's historical cloud cover
    (``_fetch_cloud_cover``) before the strongest one is picked below, so a
    pass during heavy cloud contributes much less suppression than one
    under a clear sky - a real softening of this gap, not just a caveat in
    a docstring. Never makes suppression *stronger* than the purely
    geometric version did: a missing/unavailable cloud sample leaves that
    pass's weight untouched (clarity 1.0).

    Returns ``(factor, clear_pass_count)`` - factor is a scalar in
    [CLEAR_PASS_MIN_FACTOR, 1.0] to multiply the raster by.

    **Combined by strongest single pass, not multiplied across every pass.**
    An earlier version multiplied ``(1 - weight*decay)`` across *every* clear
    pass found, compounding them like independent Bayesian evidence for the
    same "is this AOI clear right now" question. That's the wrong model:
    each pass is a *snapshot*, and a fire can ignite or spread in the gap
    between two passes, so ten stale "it was clear" snapshots don't add up
    to ten times the confidence a fresh one would - they should mostly just
    lose relevance as they age (already captured by each pass's own
    ``decay``). Multiplying compounds instead: confirmed live on a real,
    908-detection, currently-active fire event with a 29-day span (Lecco /
    Lago di Como) - 163 tracked-satellite clear passes accumulated over that
    span, and even though the freshest positive detection was only ~11h old
    (a raw active-heat peak of 21%), multiplying in all 163 passes drove the
    reported peak down to ~1%, an unrepresentative near-zero for a fire that
    was, per the detection count, very much still active. Taking the single
    *strongest* (freshest/highest-weight) pass's own suppression value fixes
    this without discarding the tri-state model itself: a genuinely recent
    clear pass still suppresses normally, but a large historical count of
    increasingly stale ones no longer compounds toward the floor on its own.
    """
    if not detections:
        return 1.0, 0
    try:
        from .orbits import aoi_clear_passes
    except ImportError:
        return 1.0, 0

    det_times = [d["ts"] for d in detections]
    start_ts = min(det_times)
    try:
        clear_passes = aoi_clear_passes(
            conn, bbox, det_times, start_ts, reference_ts, CLEAR_PASS_COINCIDENCE_S
        )
    except Exception:  # noqa: BLE001 - orbit propagation touches the network/TLE cache
        log.warning(
            "Clear-pass lookup failed - continuing without negative-observation suppression",
            exc_info=True,
        )
        return 1.0, 0

    if not clear_passes:
        return 1.0, 0

    west, south, east, north = bbox
    center_lat, center_lon = (south + north) / 2.0, (west + east) / 2.0
    cloud_by_hour = _fetch_cloud_cover(center_lat, center_lon, start_ts, reference_ts)
    factor = combine_clear_passes(clear_passes, reference_ts, tau_hours, cloud_by_hour)
    return factor, len(clear_passes)


def _cloud_clarity(pass_ts: float, cloud_by_hour: dict[float, float]) -> float:
    """1.0 (fully clear) down to CLEAR_PASS_CLOUD_MIN_CLARITY (heavy cloud) -
    1.0 whenever no usably-close cloud sample exists, so a data gap never
    *adds* suppression beyond the purely geometric baseline."""
    if not cloud_by_hour:
        return 1.0
    nearest_ts = min(cloud_by_hour, key=lambda t: abs(t - pass_ts))
    if abs(nearest_ts - pass_ts) > CLEAR_PASS_CLOUD_MAX_GAP_S:
        return 1.0
    cloud_pct = cloud_by_hour[nearest_ts]
    return max(CLEAR_PASS_CLOUD_MIN_CLARITY, 1.0 - cloud_pct / 100.0)


def combine_clear_passes(
    clear_passes: list[dict[str, Any]],
    reference_ts: float,
    tau_hours: float = DEFAULT_TAU_HOURS,
    cloud_by_hour: dict[float, float] | None = None,
) -> float:
    """The strongest-single-pass combination rule (see
    _clear_pass_suppression's docstring for why not a multiplicative
    compound), factored out so validation.py's rolling-holdout backtest can
    score its ``kernel_density`` candidate through the *exact* same
    suppression math ``analyze_event`` actually serves, given a clear-pass
    list it already fetched itself (avoiding a second, redundant orbit
    propagation per split - see run_validation)."""
    if not clear_passes:
        return 1.0
    cloud_by_hour = cloud_by_hour or {}
    strongest = max(
        CLEAR_PASS_SUPPRESSION_WEIGHT
        * math.exp(-max(0.0, (reference_ts - p["ts"]) / 3600.0) / tau_hours)
        * _cloud_clarity(p["ts"], cloud_by_hour)
        for p in clear_passes
    )
    return max(CLEAR_PASS_MIN_FACTOR, 1.0 - strongest)


# ------------------------------------------------------------ arrival time


def arrival_time_estimate(
    detections: list[dict[str, Any]], geom: dict[str, Any], power: float = 2.0
) -> dict[str, np.ndarray]:
    """Weighted-IDW arrival time per cell, with an honest spread.

    Returns {"median": ..., "earliest": ..., "latest": ...}, each (ny, nx)
    arrays of unix seconds (NaN where no detection has meaningful weight).
    """
    ny, nx = geom["ny"], geom["nx"]
    if not detections:
        nan = np.full((ny, nx), np.nan)
        return {"median": nan, "earliest": nan, "latest": nan}

    gx, gy = grid_xy_m(geom)
    lats = np.array([d["lat"] for d in detections])
    lons = np.array([d["lon"] for d in detections])
    ts = np.array([d["ts"] for d in detections], dtype=np.float64)
    dx_all, dy_all = detections_xy_m(geom, lats, lons)

    weight_sum = np.zeros((ny, nx))
    weighted_ts_sum = np.zeros((ny, nx))
    for i in range(len(detections)):
        dist = np.hypot(gx - dx_all[i], gy - dy_all[i])
        w = 1.0 / (dist**power + 1e-6)
        weight_sum += w
        weighted_ts_sum += w * ts[i]

    with np.errstate(invalid="ignore", divide="ignore"):
        mean_ts = weighted_ts_sum / weight_sum

    weighted_var_sum = np.zeros((ny, nx))
    for i in range(len(detections)):
        dist = np.hypot(gx - dx_all[i], gy - dy_all[i])
        w = 1.0 / (dist**power + 1e-6)
        weighted_var_sum += w * (ts[i] - mean_ts) ** 2
    with np.errstate(invalid="ignore", divide="ignore"):
        std_ts = np.sqrt(weighted_var_sum / weight_sum)

    return {"median": mean_ts, "earliest": mean_ts - std_ts, "latest": mean_ts + std_ts}


# ---------------------------------------------------------------- contours


def probability_envelopes(
    raster: np.ndarray, geom: dict[str, Any], fractions: tuple[float, ...] = (0.5, 0.8, 0.9)
) -> list[dict[str, Any]]:
    """50/80/90% probability-*mass* contours: the boundary such that the
    cells inside it hold that fraction of the raster's total likelihood -
    a real confidence region, not an arbitrary fixed value threshold.

    Each feature also carries ``area_km2``/``area_mi2`` - the *total* area
    of every cell inside that probability-mass level (not the shoelace area
    of the drawn ring alone, which would undercount a multi-modal fire with
    several disjoint fronts at the same level - the cell count that defined
    the threshold in the first place is the exact, cheap answer)."""
    total = float(raster.sum())
    if total <= 0:
        return []

    flat_sorted = np.sort(raster.flatten())[::-1]
    cumulative = np.cumsum(flat_sorted) / total
    cell_area_km2 = (geom["width_m"] / geom["nx"]) * (geom["height_m"] / geom["ny"]) / 1e6

    features = []
    west, south, east, north = geom["bbox"]
    nx, ny = geom["nx"], geom["ny"]

    for frac in fractions:
        idx = min(int(np.searchsorted(cumulative, frac)), len(flat_sorted) - 1)
        level = float(flat_sorted[idx])
        if level <= 0:
            continue
        # Cells actually >= level, not just idx+1 - flat_sorted[idx] can be
        # tied with cells beyond idx (a single/symmetric-kernel detection,
        # the most common real case, produces exactly this), in which case
        # idx+1 undercounts how many cells the contour at this level really
        # encloses. flat_sorted is sorted descending, so the >= level set is
        # a contiguous prefix and this count is exact, not an approximation.
        cell_count = int(np.count_nonzero(flat_sorted >= level))
        area_km2 = round(cell_count * cell_area_km2, 4)
        try:
            contours = measure.find_contours(raster, level)
        except Exception:  # pragma: no cover - defensive, skimage edge cases
            continue
        for contour in contours:
            if len(contour) < 3:
                continue
            coords = []
            for row, col in contour:
                lon = west + (col / nx) * (east - west)
                lat = south + (row / ny) * (north - south)
                coords.append([round(lon, 6), round(lat, 6)])
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [coords]},
                    "properties": {
                        "probability_mass": frac,
                        "level": level,
                        "area_km2": area_km2,
                        "area_mi2": round(area_km2 * 0.386102, 4),
                    },
                }
            )
    return features


# -------------------------------------------------------------- rendering


def _ramp_color(t: float) -> tuple[int, int, int]:
    """Linear RGB interpolation between the two ``FIRE_RAMP_RGB`` stops
    bracketing ``t`` (0-1) - used once, up front, to build ``_RAMP_LUT``,
    not called per-pixel at render time."""
    t = max(0.0, min(1.0, t))
    scaled = t * (len(FIRE_RAMP_RGB) - 1)
    i = min(len(FIRE_RAMP_RGB) - 2, int(scaled))
    frac = scaled - i
    c0, c1 = FIRE_RAMP_RGB[i], FIRE_RAMP_RGB[i + 1]
    return tuple(int(c0[k] + (c1[k] - c0[k]) * frac) for k in range(3))


_RAMP_LUT = np.array([_ramp_color(t) for t in np.linspace(0, 1, 256)], dtype=np.uint8)


def render_probability_png(raster: np.ndarray, max_alpha: int = 210) -> bytes:
    """Probability -> RGBA: color from the fire ramp, alpha from magnitude
    so low-probability cells fade into whatever basemap is underneath."""
    vmax = float(raster.max()) or 1.0
    norm = np.clip(raster / vmax, 0.0, 1.0)
    idx = (norm * 255).astype(np.uint8)
    rgb = _RAMP_LUT[idx]
    alpha = (norm * max_alpha).astype(np.uint8)
    rgba = np.dstack([rgb, alpha])
    return encode_png(rgba)


def render_recency_png(hours_ago: np.ndarray, max_hours: float = 168.0, max_alpha: int = 210) -> bytes:
    """Arrival-time -> RGBA, using the same ramp but inverted (fresher =
    more saturated/opaque, matching the point markers' age colouring)."""
    valid = ~np.isnan(hours_ago)
    norm = np.zeros(hours_ago.shape, dtype=np.float64)
    norm[valid] = 1.0 - np.clip(hours_ago[valid] / max_hours, 0.0, 1.0)
    idx = (norm * 255).astype(np.uint8)
    rgb = _RAMP_LUT[idx]
    alpha = np.where(valid, (norm * max_alpha).astype(np.uint8), 0).astype(np.uint8)
    rgba = np.dstack([rgb, alpha])
    return encode_png(rgba)


# -------------------------------------------------------------- job kind


def analyze_event(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Job body: build the likelihood/arrival-time/envelope layers for one
    already-clustered event (see events.py) and write them to disk."""
    event_id = int(params["event_id"])
    tau_hours = float(params.get("tau_hours", DEFAULT_TAU_HOURS))
    resolution_m = float(params.get("resolution_m", DEFAULT_RESOLUTION_M))
    reference_ts = float(params.get("reference_ts") or time_module.time())
    # A little padding around the raw detections so the raster doesn't crop
    # right at the outermost pixel's centre.
    pad_deg = float(params.get("pad_deg", 0.05))

    conn = sqlite3.connect(ctx.db_path, timeout=30.0)
    try:
        event_row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if event_row is None:
            raise ValueError(f"no such event: {event_id}")
        columns = [d[0] for d in conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).description]
        event = dict(zip(columns, event_row))

        det_rows = conn.execute(
            "SELECT d.latitude, d.longitude, d.acq_ts, d.instrument, d.confidence_level, "
            "d.frp, d.scan, d.track FROM detections d "
            "JOIN event_members m ON m.detection_id = d.id WHERE m.event_id = ?",
            (event_id,),
        ).fetchall()
        detections = [
            {
                "lat": r[0],
                "lon": r[1],
                "ts": r[2],
                "instrument": r[3],
                "confidence": r[4],
                "frp": r[5],
                "scan": r[6],
                "track": r[7],
            }
            for r in det_rows
        ]
        ctx.report_progress(10, note=f"{len(detections)} detections")

        bbox = (
            event["bbox_west"] - pad_deg,
            event["bbox_south"] - pad_deg,
            event["bbox_east"] + pad_deg,
            event["bbox_north"] + pad_deg,
        )
        geom = grid_geometry(bbox, desired_res_m=resolution_m)
        ctx.report_progress(20, note=f"grid {geom['nx']}x{geom['ny']} @ {geom['res_m']:.0f}m")

        active = active_heat_raster(detections, geom, reference_ts, tau_hours)
        ctx.report_progress(45, note="active-heat raster done")

        suppression, clear_pass_count = _clear_pass_suppression(
            conn, bbox, detections, reference_ts, tau_hours
        )
        if clear_pass_count:
            active = active * suppression
        ctx.report_progress(
            50,
            note=(
                f"{clear_pass_count} clear pass(es) applied" if clear_pass_count
                else "no clear-pass suppression (orbits unavailable or none found)"
            ),
        )

        arrival = arrival_time_estimate(detections, geom)
        now = time_module.time()
        hours_ago = (now - arrival["median"]) / 3600.0
        ctx.report_progress(70, note="arrival-time estimate done")

        envelopes = probability_envelopes(active, geom)
        ctx.report_progress(85, note=f"{len(envelopes)} envelope contours")

        active_png = render_probability_png(active)
        arrival_png = render_recency_png(hours_ago)

        result_dir = ctx.result_path
        (result_dir / "active_heat.png").write_bytes(active_png)
        (result_dir / "arrival_time.png").write_bytes(arrival_png)
        (result_dir / "envelopes.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": envelopes})
        )

        ctx.report_progress(100, note="done")
        return {
            "event_id": event_id,
            "bounds": [[bbox[1], bbox[0]], [bbox[3], bbox[2]]],  # [[south,west],[north,east]]
            "grid": {"nx": geom["nx"], "ny": geom["ny"], "res_m": geom["res_m"]},
            "reference_ts": reference_ts,
            "tau_hours": tau_hours,
            "detection_count": len(detections),
            "max_probability": round(float(active.max()) if active.size else 0.0, 4),
            "envelope_count": len(envelopes),
            "clear_pass_count": clear_pass_count,
            "files": {
                "active_heat": "active_heat.png",
                "arrival_time": "arrival_time.png",
                "envelopes": "envelopes.geojson",
            },
        }
    finally:
        conn.close()


register_kind("analyze_event", analyze_event)
