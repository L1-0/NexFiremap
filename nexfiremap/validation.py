"""Validation programme - further_plan.md section 11: don't trust a fancier
model until it reliably beats simple, classical baselines on real held-out
data ("Do not proceed to the visually polished model unless it reliably
beats these baselines").

**Rolling-holdout backtest** (the doc's own recipe, section 11.1): for an
event's detections ordered by time, repeatedly (a) give every candidate
model everything through some split time ``t_k``, (b) hide everything
after it, (c) ask each model to predict the state a short horizon ahead,
around the next real observation time ``t_(k+1)``, (d) score the
prediction against what was actually seen - and, via the tri-state model
in [[orbits]].aoi_clear_passes, what was actually confirmed *not* seen.

**Candidate models**, scored identically so they're directly comparable:

* ``kernel_density``     - this project's own active-heat raster
                            ([[likelihood]].active_heat_raster) - the model
                            actually served to users
* ``buffered_footprint``  - static union of each training detection's own
                            sensor footprint (the doc's simplest baseline)
* ``concave_hull``        - an alpha-shape around the training points
                            (classical computational geometry via scipy's
                            Delaunay triangulation - not learned)
* ``constant_radial``     - centroid + a radial growth rate fit by ordinary
                            least squares on training points' distance-vs-time
* ``wind_ellipse``        - the same fitted growth rate, applied
                            anisotropically along one Open-Meteo wind sample
                            for the event (best-effort: silently omitted if
                            the optional ``terrain`` feature is unavailable)

**Ground truth**:

* positive points - the held-out detections themselves
* negative points - confirmed clear satellite passes over the event's AOI
  during the held-out window (best-effort: omitted, not fabricated, if the
  optional ``orbits`` feature is unavailable). Because that fusion already
  treats a clear pass as covering the *whole* AOI uniformly (every tracked
  sensor's swath is far wider than one event - see
  [[orbits]].aoi_clear_passes), each clear pass contributes one negative
  point at the AOI's centroid, not a fabricated grid of them.

**Metrics** (doc section 11.2), computed per split per model:

* precision / recall / F1, and a point-classification Jaccard index - a
  genuine but *point-based* stand-in for area IoU. This project has no
  curated reference perimeters (e.g. EFFIS) to compute true-perimeter IoU,
  95th-percentile boundary distance, or area bias against - that remains
  future work, not faked here.

  Across-split aggregation is **pooled (micro-averaged)**: TP/FP/FN are
  summed across every split first, and precision/recall/F1/Jaccard are then
  computed once from those sums - not a mean of each split's own
  precision/recall/F1. That choice isn't cosmetic: a *macro* mean of
  independently-computed per-split ratios is not required to satisfy
  F1 == harmonic-mean(precision, recall) on the displayed numbers (Jensen's
  inequality - F1 is concave in recall at fixed precision), and, worse,
  precision/recall/F1 can each end up averaged over a *different subset* of
  splits (whichever splits happened to have that particular metric defined
  - e.g. a split with zero true positives has an undefined precision but a
  well-defined, zero, recall), so the three macro means aren't even
  guaranteed to describe the same underlying predictions. A live rolling-
  holdout run against a real fire surfaced exactly this: a displayed
  precision of 1.000 and recall of 0.311 alongside an F1 of 0.617, which is
  mathematically impossible for matching P/R (the correct value is
  2*0.311/1.311 ≈ 0.474) - the macro-mean-of-ratios approach previously
  used here. Pooled metrics don't have this failure mode: they're computed
  from one set of summed counts, so the identity always holds exactly.
  Per-split macro means are still available in ``summary[model]["*_macro"]``
  for anyone who wants them, clearly separate from the headline pooled
  numbers.
* Brier score (threshold-independent - the primary "were the actual
  probability values any good" signal, since the probability-mass
  thresholds elsewhere in this project were never calibrated against
  historical fires either)
* centroid displacement: predicted probability-weighted centroid vs. the
  held-out points' own centroid
* arrival-time error: only for ``kernel_density``, the one candidate with a
  native arrival-time estimate ([[likelihood]].arrival_time_estimate) -
  the geometric baselines have no arrival-time semantics of their own
* missed disconnected fronts: held-out detections clustered via
  [[events]].cluster_detections that the prediction's positive region never
  touches at all

**Explicitly not built, and why:**

* True-perimeter IoU / boundary distance / area bias - need curated
  reference perimeters (EFFIS etc.) - flagged in further_plan.md itself as
  a research-grade data-collection effort of its own.
* "False bridging across barriers" - needs explicit barrier geometry
  cross-referenced with the prediction footprint - nothing in this project
  models barriers yet.
* A "physical model without assimilation" baseline (the doc's sixth
  baseline) - [[terrain]].run_propagation *is* that model, but it's not wired
  into this comparison loop because doing so would mean a live DEM/fuel/
  weather fetch per split of every validated event. Worthwhile, but a
  separate follow-up rather than something bolted on here.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time as time_module
from typing import Any

import numpy as np

from .events import DEFAULT_V_MAX_KMH, cluster_detections
from .geo import (
    LAT_KM_PER_DEG,
    LAT_M_PER_DEG,
    detection_footprint_radius_km,
    detections_xy_m,
    grid_geometry,
    grid_xy_m,
    haversine_km,
    lon_km_per_deg,
    lon_m_per_deg,
)
from .jobs import JobContext, register_kind
from .likelihood import (
    _fetch_cloud_cover,
    active_heat_raster,
    arrival_time_estimate,
    combine_clear_passes,
)

log = logging.getLogger("nexfiremap.validation")

DEFAULT_N_SPLITS = 3
DEFAULT_MIN_TRAIN = 2
DEFAULT_THRESHOLD = 0.3
HORIZON_S = 3 * 3600.0  # "predict through t_(k+1)": score against detections
# within this many seconds of the next observation, not the whole remaining
# future of the event.


def _xy_m(geom: dict[str, Any], lat: float, lon: float) -> tuple[float, float]:
    x, y = detections_xy_m(geom, lat, lon)
    return float(x), float(y)


def _sample(raster: np.ndarray, geom: dict[str, Any], lat: float, lon: float) -> float:
    """Nearest-cell value of ``raster`` at ``(lat, lon)`` - clamped to the
    grid's own extent rather than raising, since a held-out detection can
    legitimately fall just outside a per-split training-only grid built by
    ``_train_grid_bbox``."""
    x, y = _xy_m(geom, lat, lon)
    col = int(np.clip(x / geom["width_m"] * geom["nx"], 0, geom["nx"] - 1))
    row = int(np.clip(y / geom["height_m"] * geom["ny"], 0, geom["ny"] - 1))
    return float(raster[row, col])


# ------------------------------------------------------------- data splits


def rolling_holdout_splits(
    detections: list[dict[str, Any]],
    n_splits: int = DEFAULT_N_SPLITS,
    min_train: int = DEFAULT_MIN_TRAIN,
) -> list[dict[str, Any]]:
    """Time-ordered train/holdout splits per further_plan.md section 11.1.

    Each split reveals the earliest ``k`` detections as training data and
    treats the remainder as held out, with ``target_ts`` set to the very
    next detection's timestamp (the "predict through t_(k+1)" horizon).
    Split points are spread evenly across the event's timeline. Returns
    ``[]`` if there aren't enough detections to hold anything out.
    """
    ordered = sorted(detections, key=lambda d: d["ts"])
    n = len(ordered)
    if n < min_train + 1:
        return []

    splits: list[dict[str, Any]] = []
    seen_k: set[int] = set()
    step = max(1, (n - min_train) // n_splits)
    k = min_train
    while k < n and len(splits) < n_splits:
        if k not in seen_k:
            train = ordered[:k]
            holdout = ordered[k:]
            splits.append(
                {
                    "train": train,
                    "holdout": holdout,
                    "split_ts": train[-1]["ts"],
                    "target_ts": holdout[0]["ts"],
                }
            )
            seen_k.add(k)
        k += step
    return splits


# ------------------------------------------------------------------ models


def _weighted_growth_rate_km_per_s(train: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    """Fit ``distance_from_centroid = a + b * (t - t0)`` by ordinary least
    squares (classical linear regression, not ML). Returns
    ``(centroid_lat, centroid_lon, a_km, b_km_per_s)`` with ``b`` clamped to
    be non-negative (this simple model only grows)."""
    lats = np.array([d["lat"] for d in train])
    lons = np.array([d["lon"] for d in train])
    lat0, lon0 = float(lats.mean()), float(lons.mean())
    lon_km = lon_m_per_deg(lat0) / 1000.0
    lat_km = LAT_M_PER_DEG / 1000.0
    x = (lons - lon0) * lon_km
    y = (lats - lat0) * lat_km
    dist_km = np.hypot(x, y)
    ts = np.array([d["ts"] for d in train], dtype=np.float64)
    t0 = float(ts.min())

    if len(train) < 2 or float(ts.max() - t0) <= 0:
        return lat0, lon0, float(dist_km.max(initial=0.0)), 0.0

    b, a = np.polyfit(ts - t0, dist_km, 1)
    return lat0, lon0, max(0.0, float(a)), max(0.0, float(b))


def baseline_buffered_footprint(train: list[dict[str, Any]], geom: dict[str, Any]) -> np.ndarray:
    """Static union of each training detection's own sensor footprint - the
    doc's simplest baseline: "the fire is wherever we've already seen it,
    nothing more."""
    ny, nx = geom["ny"], geom["nx"]
    gx, gy = grid_xy_m(geom)
    mask = np.zeros((ny, nx), dtype=bool)
    for d in train:
        x, y = _xy_m(geom, d["lat"], d["lon"])
        radius_m = max(150.0, detection_footprint_radius_km(d) * 1000.0)
        mask |= (gx - x) ** 2 + (gy - y) ** 2 <= radius_m**2
    return mask.astype(np.float64)


def baseline_concave_hull(train: list[dict[str, Any]], geom: dict[str, Any]) -> np.ndarray:
    """Alpha-shape around the training points via Delaunay triangulation -
    classical computational geometry (not ML): triangulate, drop triangles
    whose circumradius is large relative to the point spacing (those are
    the ones bridging real gaps - valleys, unburned islands, disconnected
    activity - that a convex hull would wrongly fill per further_plan.md
    section 5), then rasterize what's left. Falls back to the buffered-
    footprint baseline when there are too few points to triangulate."""
    if len(train) < 3:
        return baseline_buffered_footprint(train, geom)

    from scipy.spatial import Delaunay, QhullError  # noqa: PLC0415 - optional-feature-adjacent, keep import local

    pts = np.array([_xy_m(geom, d["lat"], d["lon"]) for d in train])
    try:
        tri = Delaunay(pts)
    except QhullError:
        return baseline_buffered_footprint(train, geom)

    verts = pts[tri.simplices]  # (n_tri, 3, 2)
    a = np.linalg.norm(verts[:, 1] - verts[:, 2], axis=1)
    b = np.linalg.norm(verts[:, 0] - verts[:, 2], axis=1)
    c = np.linalg.norm(verts[:, 0] - verts[:, 1], axis=1)
    area2 = np.abs(
        (verts[:, 1, 0] - verts[:, 0, 0]) * (verts[:, 2, 1] - verts[:, 0, 1])
        - (verts[:, 2, 0] - verts[:, 0, 0]) * (verts[:, 1, 1] - verts[:, 0, 1])
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        circumradius = np.where(area2 > 1e-6, (a * b * c) / (2.0 * area2 + 1e-9), np.inf)

    edge_lengths = np.concatenate([a, b, c])
    alpha = float(np.median(edge_lengths)) * 2.0 or 1.0
    keep = circumradius <= alpha

    ny, nx = geom["ny"], geom["nx"]
    gx, gy = grid_xy_m(geom)
    mask = np.zeros((ny, nx), dtype=bool)
    for p0, p1, p2 in verts[keep]:
        d1 = (gx - p1[0]) * (p0[1] - p1[1]) - (p0[0] - p1[0]) * (gy - p1[1])
        d2 = (gx - p2[0]) * (p1[1] - p2[1]) - (p1[0] - p2[0]) * (gy - p2[1])
        d3 = (gx - p0[0]) * (p2[1] - p0[1]) - (p2[0] - p0[0]) * (gy - p0[1])
        has_neg = (d1 < 0) | (d2 < 0) | (d3 < 0)
        has_pos = (d1 > 0) | (d2 > 0) | (d3 > 0)
        mask |= ~(has_neg & has_pos)
    if not mask.any():
        return baseline_buffered_footprint(train, geom)
    return mask.astype(np.float64)


def baseline_constant_radial(
    train: list[dict[str, Any]], geom: dict[str, Any], target_ts: float
) -> np.ndarray:
    """Centroid + a radial growth rate fit from the training points
    themselves (ordinary least squares, not a hand-picked constant),
    extrapolated to ``target_ts`` as a filled disk."""
    lat0, lon0, a_km, b_km_s = _weighted_growth_rate_km_per_s(train)
    t0 = min(d["ts"] for d in train)
    # Floored at one grid cell's pitch, not an arbitrary small constant - a
    # radius below the grid's own resolution would (depending on exactly
    # where the centroid lands relative to cell centres) rasterize to
    # nothing at all, which isn't "a small predicted area", it's a bug.
    radius_km = max(geom["res_m"] / 1000.0, a_km + b_km_s * max(0.0, target_ts - t0))

    ny, nx = geom["ny"], geom["nx"]
    gx, gy = grid_xy_m(geom)
    cx, cy = _xy_m(geom, lat0, lon0)
    mask = (gx - cx) ** 2 + (gy - cy) ** 2 <= (radius_km * 1000.0) ** 2
    return mask.astype(np.float64)


def _train_grid_bbox(
    train: list[dict[str, Any]], pad_deg: float, elapsed_hours: float
) -> tuple[float, float, float, float]:
    """Grid bbox sized from the *training* points' own extent for this
    split, not the event's eventual final extent.

    An earlier version computed one grid, once, from the fully-clustered
    event's bbox (built from every member detection, including everything
    later splits hold out) and reused it for every split. Any baseline that
    would naturally overshoot beyond the true final footprint -
    ``constant_radial``, ``wind_ellipse``, ``concave_hull`` - had that
    overshoot silently clipped because there was no grid cell to render it
    on, since the raster never extended past that bbox. That flattered the
    extrapolating baselines relative to ``kernel_density`` (whose kernels
    stay local to the training points anyway) and undermined the exact
    comparison the validation programme exists to make honest.

    Padded by ``pad_deg`` plus a growth allowance for how far the fire
    could plausibly have spread by the time this split is scored, at
    ``events.DEFAULT_V_MAX_KMH`` - the same "deliberately generous
    plausible spread speed" events.py's own clustering link rule uses, so a
    baseline's own overshoot has genuine room to show up (and be penalised
    for it) rather than being clipped by a box sized with knowledge only
    available in hindsight.
    """
    lats = [d["lat"] for d in train]
    lons = [d["lon"] for d in train]
    lat0 = sum(lats) / len(lats)
    extra_km = DEFAULT_V_MAX_KMH * max(0.0, elapsed_hours)
    pad_lat = pad_deg + extra_km / LAT_KM_PER_DEG
    pad_lon = pad_deg + extra_km / lon_km_per_deg(lat0)
    return (
        min(lons) - pad_lon,
        min(lats) - pad_lat,
        max(lons) + pad_lon,
        max(lats) + pad_lat,
    )


def _event_wind(event: dict[str, Any], reference_ts: float) -> dict[str, float] | None:
    """Best-effort single wind sample for the event's AOI/window, via the
    optional ``terrain`` feature. Returns ``None`` (not an error) if that
    feature isn't installed or the fetch fails - the wind_ellipse baseline
    is simply omitted in that case."""
    try:
        from .terrain import fetch_weather  # noqa: PLC0415 - optional feature, lazy on purpose
    except ImportError:
        return None
    try:
        return fetch_weather(
            event["centroid_lat"], event["centroid_lon"], event["first_seen"], reference_ts
        )
    except Exception:  # noqa: BLE001 - network/API call, degrade rather than fail the whole job
        log.warning("Wind fetch for validation's wind_ellipse baseline failed", exc_info=True)
        return None


def baseline_wind_ellipse(
    train: list[dict[str, Any]],
    geom: dict[str, Any],
    target_ts: float,
    wind_dir_deg: float,
) -> np.ndarray:
    """The same fitted radial growth rate as ``baseline_constant_radial``,
    applied anisotropically: elongated downwind, narrower crosswind - a
    "wind matters for direction, data fit for magnitude" baseline rather
    than an invented absolute growth constant.

    The ellipse is centred downwind of the training centroid (not on top
    of it) so the ignition point sits near its upwind edge and the shape
    genuinely favours the downwind side, rather than growing as a wind-
    agnostic symmetric disc with a different aspect ratio."""
    lat0, lon0, a_km, b_km_s = _weighted_growth_rate_km_per_s(train)
    t0 = min(d["ts"] for d in train)
    res_km = geom["res_m"] / 1000.0
    base_radius_km = max(res_km, a_km + b_km_s * max(0.0, target_ts - t0))
    major_km = max(res_km, base_radius_km * 1.6)
    minor_km = max(res_km, base_radius_km * 0.6)

    ny, nx = geom["ny"], geom["nx"]
    gx, gy = grid_xy_m(geom)
    cx, cy = _xy_m(geom, lat0, lon0)
    # Meteorological "wind from" direction -> the fire is pushed in the
    # direction the wind blows *towards* (+180deg), measured clockwise from
    # north like the wind_dir_deg convention itself.
    theta = math.radians((wind_dir_deg + 180.0) % 360.0)
    downwind_x, downwind_y = math.sin(theta), math.cos(theta)
    # Shift the ellipse centre downwind so the original ignition point sits
    # near the back (upwind) edge rather than dead centre.
    shift_km = major_km * 0.6
    cx += shift_km * 1000.0 * downwind_x
    cy += shift_km * 1000.0 * downwind_y

    dx, dy = gx - cx, gy - cy
    along = dx * downwind_x + dy * downwind_y
    across = dx * math.cos(theta) - dy * math.sin(theta)
    mask = (along / (major_km * 1000.0)) ** 2 + (across / (minor_km * 1000.0)) ** 2 <= 1.0
    return mask.astype(np.float64)


# ----------------------------------------------------------------- scoring


def score_prediction(
    probability: np.ndarray,
    geom: dict[str, Any],
    positive_points: list[tuple[float, float]],
    negative_points: list[tuple[float, float]],
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Doc section 11.2's metrics, scored against real held-out evidence -
    see the module docstring for exactly which of the doc's metrics this
    covers and which need data this project doesn't have."""
    pos_probs = [_sample(probability, geom, lat, lon) for lat, lon in positive_points]
    neg_probs = [_sample(probability, geom, lat, lon) for lat, lon in negative_points]

    tp = sum(1 for p in pos_probs if p >= threshold)
    fn = len(pos_probs) - tp
    tn = sum(1 for p in neg_probs if p < threshold)
    fp = len(neg_probs) - tn

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )
    jaccard_proxy = tp / (tp + fp + fn) if (tp + fp + fn) else None

    all_probs = pos_probs + neg_probs
    all_outcomes = [1.0] * len(pos_probs) + [0.0] * len(neg_probs)
    brier = (
        float(np.mean([(p - o) ** 2 for p, o in zip(all_probs, all_outcomes)]))
        if all_probs
        else None
    )

    total = float(probability.sum())
    if total > 0 and positive_points:
        gx, gy = grid_xy_m(geom)
        west, south, _, _ = geom["bbox"]
        cx = float((probability * gx).sum() / total)
        cy = float((probability * gy).sum() / total)
        pred_lat = south + cy / LAT_M_PER_DEG
        pred_lon = west + cx / geom["lon_m_per_deg"]
        obs_lat = sum(p[0] for p in positive_points) / len(positive_points)
        obs_lon = sum(p[1] for p in positive_points) / len(positive_points)
        centroid_displacement_km = haversine_km(pred_lat, pred_lon, obs_lat, obs_lon)
    else:
        centroid_displacement_km = None

    cell_area_km2 = (geom["width_m"] / geom["nx"]) * (geom["height_m"] / geom["ny"]) / 1e6
    predicted_area_km2 = float((probability >= threshold).sum()) * cell_area_km2

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "jaccard_point_proxy": jaccard_proxy,
        "brier_score": brier,
        "centroid_displacement_km": centroid_displacement_km,
        "predicted_area_km2": round(predicted_area_km2, 3),
        "positive_count": len(positive_points),
        "negative_count": len(negative_points),
    }


def mean_of(key: str, rows: list[dict[str, Any]]) -> float | None:
    """Mean of ``rows[*][key]``, skipping rows where it's ``None`` (a metric
    that wasn't defined for that split, e.g. precision with zero predicted
    positives) rather than letting one undefined split poison the average."""
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(float(np.mean(vals)), 4) if vals else None


def pooled_confusion_metrics(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """Precision/recall/F1/Jaccard from TP/FP/FN *summed* across every split,
    not a mean of each split's own ratio - see the module docstring's
    "pooled (micro-averaged)" note for why a macro mean of ratios is
    mathematically not guaranteed self-consistent (and, on a real event, was
    caught producing exactly that inconsistency)."""
    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )
    jaccard = tp / (tp + fp + fn) if (tp + fp + fn) else None
    return {
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "jaccard_point_proxy": round(jaccard, 4) if jaccard is not None else None,
    }


def pooled_brier(rows: list[dict[str, Any]]) -> float | None:
    """Sample-count-weighted mean of each split's Brier score - exactly equal
    to pooling every split's raw (prediction - outcome)^2 terms together and
    averaging once (no per-point data needs storing to get this: weighting
    each split's own mean by its point count is the same computation),
    unlike a plain unweighted mean of per-split means."""
    weighted_total, n_total = 0.0, 0
    for r in rows:
        if r.get("brier_score") is None:
            continue
        n = r["positive_count"] + r["negative_count"]
        weighted_total += r["brier_score"] * n
        n_total += n
    return round(weighted_total / n_total, 4) if n_total else None


def _missed_fronts(
    near_holdout: list[dict[str, Any]],
    probability: np.ndarray,
    geom: dict[str, Any],
    threshold: float,
) -> int:
    """Count of the module docstring's "missed disconnected fronts" metric:
    cluster the held-out detections into their own spatially-distinct
    groups (via ``events.cluster_detections``) and count how many of those
    clusters have *no* member sampled above ``threshold`` in the
    prediction - a whole separate front the model missed entirely, not
    just a partial miss within a front it otherwise caught."""
    if not near_holdout:
        return 0
    clusters = cluster_detections(
        [
            {"lat": d["lat"], "lon": d["lon"], "ts": d["ts"], "scan": d.get("scan"), "track": d.get("track")}
            for d in near_holdout
        ]
    )
    missed = 0
    for indices in clusters:
        touched = any(
            _sample(probability, geom, near_holdout[i]["lat"], near_holdout[i]["lon"]) >= threshold
            for i in indices
        )
        if not touched:
            missed += 1
    return missed


# -------------------------------------------------------------- job kind


MODEL_NAMES = ("kernel_density", "buffered_footprint", "concave_hull", "constant_radial")


def run_validation(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Job body: rolling-holdout backtest of every candidate model for one
    already-clustered event, scored against real held-out detections (and,
    best-effort, confirmed clear satellite passes)."""
    event_id = int(params["event_id"])
    # Mirrors api.py's ValidationRequest bounds (ge=1, le=10) - enforced
    # here too since a raw POST /api/jobs bypasses that route's validation
    # entirely. rolling_holdout_splits self-limits by detection count
    # regardless, but an explicit cap keeps this job's cost bounded by a
    # known constant rather than by however many detections the event
    # happens to have.
    n_splits = max(1, min(10, int(params.get("n_splits", DEFAULT_N_SPLITS))))
    threshold = float(params.get("threshold", DEFAULT_THRESHOLD))
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
            "JOIN event_members m ON m.detection_id = d.id WHERE m.event_id = ? ORDER BY d.acq_ts",
            (event_id,),
        ).fetchall()
        detections = [
            {
                "lat": r[0], "lon": r[1], "ts": r[2], "instrument": r[3],
                "confidence": r[4], "frp": r[5], "scan": r[6], "track": r[7],
            }
            for r in det_rows
        ]

        splits = rolling_holdout_splits(detections, n_splits=n_splits)
        if not splits:
            raise ValueError(
                f"event {event_id} has only {len(detections)} detections - need at least "
                f"{DEFAULT_MIN_TRAIN + 1} to hold anything out for a rolling backtest"
            )
        ctx.report_progress(5, note=f"{len(splits)} rolling-holdout split(s)")

        # Still used for the clear-pass AOI query below (a legitimate,
        # non-leaking use - it only determines "was a satellite pass
        # sweeping this area", not any baseline's predicted extent) and as
        # the negative-evidence point location. The per-split *raster* grid
        # is built separately, from training data only - see
        # _train_grid_bbox.
        bbox = (
            event["bbox_west"] - pad_deg,
            event["bbox_south"] - pad_deg,
            event["bbox_east"] + pad_deg,
            event["bbox_north"] + pad_deg,
        )

        per_model: dict[str, list[dict[str, Any]]] = {name: [] for name in MODEL_NAMES}
        # Two counters for the two clear-pass lists (see the split below).
        # `clear_pass_total` is how many passes the *model* was allowed to treat
        # as evidence of absence; `clear_pass_negatives` is how many became
        # negative ground truth. The second is the smaller whenever an overpass
        # actually saw the fire, and the gap between them is exactly the
        # contamination this used to have.
        clear_pass_total = 0
        clear_pass_negatives = 0

        for i, split in enumerate(splits):
            train, target_ts = split["train"], split["target_ts"]
            near_holdout = [d for d in split["holdout"] if d["ts"] <= target_ts + HORIZON_S]
            positive_points = [(d["lat"], d["lon"]) for d in near_holdout]

            elapsed_hours = (target_ts + HORIZON_S - split["split_ts"]) / 3600.0
            geom = grid_geometry(
                _train_grid_bbox(train, pad_deg, elapsed_hours), desired_res_m=100.0, max_dim=180
            )

            negative_points: list[tuple[float, float]] = []
            clear: list[dict[str, Any]] = []
            try:
                from .orbits import aoi_clear_passes  # noqa: PLC0415 - optional feature, lazy on purpose

                # Two clear-pass lists, because the same computation is being
                # asked for two opposite things and only one of them may look
                # at the holdout.
                #
                # `clear` is a **model input** - it suppresses kernel_density
                # below exactly as analyze_event does when serving. It must see
                # training detections only, or the backtest leaks the future
                # into the prediction.
                det_times_train = [d["ts"] for d in train]
                clear = aoi_clear_passes(
                    conn, bbox, det_times_train, split["split_ts"], target_ts + HORIZON_S
                )
                clear_pass_total += len(clear)

                # `clear_truth` is **ground truth** - the negative points every
                # model is scored against. It must see *every* detection in the
                # window, holdout included, because ground truth is what
                # actually happened rather than what the model was allowed to
                # know. Computed from train-only times, the very overpass that
                # produced the held-out detections counted as a "confirmed clear
                # pass", and a negative point was then placed at the event
                # centroid - the one place the fire certainly was. Every model
                # was penalised in precision and Brier for correctly predicting
                # fire there, so the headline figures an operator reads were
                # scored against partly false negatives.
                det_times_truth = det_times_train + [d["ts"] for d in split["holdout"]]
                clear_truth = aoi_clear_passes(
                    conn, bbox, det_times_truth, split["split_ts"], target_ts + HORIZON_S
                )
                clear_pass_negatives += len(clear_truth)
                negative_points = [(event["centroid_lat"], event["centroid_lon"])] * len(clear_truth)
            except ImportError:
                pass
            except Exception:  # noqa: BLE001 - orbit propagation, degrade rather than fail the job
                log.warning("Clear-pass lookup failed during validation", exc_info=True)

            # kernel_density is described as "the model actually served to
            # users" (module docstring) - it has to be scored with the same
            # tri-state clear-pass suppression analyze_event actually
            # applies, not the bare active-heat raster alone, or this
            # backtest isn't really validating what's served. Reuses the
            # clear-pass list already fetched above rather than a second,
            # redundant orbit propagation.
            kernel_density = active_heat_raster(train, geom, target_ts)
            if clear:
                west, south, east, north = bbox
                cloud_by_hour = _fetch_cloud_cover(
                    (south + north) / 2.0, (west + east) / 2.0, split["split_ts"], target_ts + HORIZON_S
                )
                kernel_density = kernel_density * combine_clear_passes(
                    clear, target_ts, cloud_by_hour=cloud_by_hour
                )

            candidates: dict[str, np.ndarray] = {
                "kernel_density": kernel_density,
                "buffered_footprint": baseline_buffered_footprint(train, geom),
                "concave_hull": baseline_concave_hull(train, geom),
                "constant_radial": baseline_constant_radial(train, geom, target_ts),
            }
            # Fetched fresh per split (train-only centroid/window), not
            # once for the whole event - an earlier version sampled wind at
            # the *final* event centroid even for early splits, leaking a
            # sliver of future spatial-extent information into which
            # historical weather sample got fetched.
            train_lats = [d["lat"] for d in train]
            train_lons = [d["lon"] for d in train]
            pseudo_event = {
                "centroid_lat": sum(train_lats) / len(train_lats),
                "centroid_lon": sum(train_lons) / len(train_lons),
                "first_seen": min(d["ts"] for d in train),
            }
            wind = _event_wind(pseudo_event, target_ts)
            if wind is not None:
                candidates["wind_ellipse"] = baseline_wind_ellipse(
                    train, geom, target_ts, wind["wind_direction_deg"]
                )
                per_model.setdefault("wind_ellipse", [])

            for name, raster in candidates.items():
                metrics = score_prediction(raster, geom, positive_points, negative_points, threshold)
                metrics["missed_disconnected_fronts"] = _missed_fronts(
                    near_holdout, raster, geom, threshold
                )
                if name == "kernel_density" and near_holdout:
                    arrival = arrival_time_estimate(train, geom)
                    errs = []
                    for d in near_holdout:
                        pred_ts = _sample(arrival["median"], geom, d["lat"], d["lon"])
                        if pred_ts and not math.isnan(pred_ts):
                            errs.append(abs(pred_ts - d["ts"]) / 3600.0)
                    metrics["arrival_time_error_hours"] = round(float(np.mean(errs)), 2) if errs else None
                else:
                    metrics["arrival_time_error_hours"] = None
                metrics["split"] = i
                metrics["train_count"] = len(train)
                metrics["target_ts"] = target_ts
                per_model[name].append(metrics)

            ctx.report_progress(
                5 + int((i + 1) / len(splits) * 85),
                note=f"split {i + 1}/{len(splits)}: {len(positive_points)} held out, "
                f"{len(negative_points)} clear-pass negative(s)",
            )

        summary = {}
        for name, rows in per_model.items():
            if not rows:
                continue
            summary[name] = {
                **pooled_confusion_metrics(rows),
                "brier_score": pooled_brier(rows),
                "centroid_displacement_km": mean_of("centroid_displacement_km", rows),
                "predicted_area_km2": mean_of("predicted_area_km2", rows),
                "missed_disconnected_fronts": mean_of("missed_disconnected_fronts", rows),
                "arrival_time_error_hours": mean_of("arrival_time_error_hours", rows),
                # Per-split macro means, kept separate from the pooled headline
                # numbers above - useful for spotting a single bad split, but
                # NOT guaranteed to satisfy f1 == harmonic-mean(precision,
                # recall) the way the pooled numbers always do (see docstring).
                "precision_macro": mean_of("precision", rows),
                "recall_macro": mean_of("recall", rows),
                "f1_macro": mean_of("f1", rows),
                "jaccard_point_proxy_macro": mean_of("jaccard_point_proxy", rows),
            }

        result = {
            "event_id": event_id,
            "split_count": len(splits),
            "threshold": threshold,
            # The count of negatives actually scored against, which is the
            # holdout-aware list - not the model-input one. Reporting the
            # latter here would overstate how much negative evidence the
            # metrics rest on, by exactly the passes that saw the fire.
            "clear_pass_negatives_used": clear_pass_negatives,
            "clear_passes_available_to_model": clear_pass_total,
            "wind_ellipse_available": "wind_ellipse" in summary,
            "summary": summary,
            "splits": per_model,
        }
        (ctx.result_path / "validation.json").write_text(json.dumps(result, default=str))
        ctx.report_progress(100, note="done")
        return {
            "event_id": event_id,
            "split_count": len(splits),
            # The count of negatives actually scored against, which is the
            # holdout-aware list - not the model-input one. Reporting the
            # latter here would overstate how much negative evidence the
            # metrics rest on, by exactly the passes that saw the fire.
            "clear_pass_negatives_used": clear_pass_negatives,
            "clear_passes_available_to_model": clear_pass_total,
            "summary": summary,
            "files": {"validation": "validation.json"},
        }
    finally:
        conn.close()


register_kind("validate_event", run_validation)
