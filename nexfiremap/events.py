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

That pairwise rule alone is not enough, though - seen live, not just in
theory: plain connected-components clustering is vulnerable to *chaining*.
The rule only ever asks "is this one pair close enough", never "does the
whole resulting group still make sense as one fire" - so a chain of dozens
of individually-plausible links (widespread scattered heat, agricultural/
stubble burning across a region being the common real trigger) can connect
detections hundreds of km apart into one "event", even though no two
members at the actual extremes have anything to do with each other. A real
14,000-detection, 1,005km-spanning example showed up in this project's own
dev database.

cluster_detections is therefore *span-bounded*: no cluster it returns can
have a bounding-box diagonal over MAX_PLAUSIBLE_EVENT_SPAN_KM. The pairwise
link rule itself is untouched (test_chain_over_time_stays_one_event depends
on genuine slow drift still linking, correctly) - what changes is that an
oversized connected component gets split further, by _split_to_span_bound.

Three designs were tried and rejected before the current one, each live-
tested against a real 14,000-detection, 1,005km case pulled from this
project's own dev database:

1. A plain per-edge union-find, bound-checked at every candidate merge.
   Correct, but a real dense cluster (thousands of genuinely close-together
   detections from one well-observed fire, not a chained scatter at all) can
   have millions of candidate edges, and a Python-level loop over that many
   pairs took minutes.
2. Re-clustering the oversized subset under a *tightened v_max_kmh* (binary
   search, via the same fast scipy connected-components call the common case
   uses), repeated until every piece fit the bound or a depth budget ran
   out. Fast on a clean synthetic chain, but the real case is dense in both
   space and time, not a simple uniform chain - narrowing v_max_kmh had to
   approach zero before the dense core budged at all, since almost every
   pair in it is close in both position and time by construction (that's
   *why* it chained). Tested with a generous budget (263s, far more search
   effort than a background job should spend): the 14,000-point core still
   came back barely split, one piece still 1,011km across. The lever was
   wrong for this data, not just under-tuned.
3. Binning into a fixed-origin geometric grid, sized so one cell's own
   diagonal can't reach max_span_km. Fast (one O(n) pass) and safe by
   construction, but tested against a steadily-marching chain of
   detections (each close to its immediate neighbours, spanning a few
   hundred km end to end - a realistic pattern, not an adversarial one) and
   it isolated the two end points as singleton "cells" on the far side of
   an arbitrary grid boundary from their real neighbours, later dropped
   entirely by min_detections filtering. A fixed grid judges a point by an
   absolute coordinate boundary, not by where its actual neighbours are.

The current design bisects each oversized subset at the midpoint of
whichever axis (projected x or y) currently spans further, recursing on
each half - see _split_to_span_bound for why this avoids design 3's
boundary problem (it adapts to the data's own shape and orientation every
time, instead of an absolute grid) while keeping design 3's core
advantages over design 2: no search, no dependence on how points are
distributed in time, provably-guaranteed progress on every split
regardless of the data. The tradeoff, accepted deliberately, is the same
one design 3 already accepted: a single coherent fire that happens to
straddle a split can end up reported as two adjacent events instead of
one - a much smaller quality cost than the failure this whole fix exists
to prevent (a 1,000km scattered chain reported as one confident "fire"),
and it only ever applies to the rare oversized case to begin with; an
already-compact cluster (the overwhelming majority, including a genuinely
large, densely-observed single fire) never reaches this code at all - see
_split_to_span_bound's first line.
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
from skimage import measure

from .geo import (
    DEFAULT_FOOTPRINT_RADIUS_KM,
    LAT_KM_PER_DEG,
    detection_footprint_radius_km,
    detections_xy_m,
    grid_geometry,
    grid_xy_m,
    haversine_km,
    lon_km_per_deg,
)
from .jobs import JobContext, register_kind

log = logging.getLogger("nexfiremap.events")

# Defaults for the link rule (see this module's docstring, section 5):
# 8 km/h is a deliberately generous plausible ground-spread speed (well above
# typical surface-fire rates of spread), so the rule errs toward linking two
# detections that might be the same fire rather than splitting one fire in
# two; 7 days is a hard cap on how large a time gap the v_max*dt term is
# allowed to inflate the allowed radius to, so two fires weeks apart can't be
# fused just because the product of a big speed and a big gap is big.
DEFAULT_V_MAX_KMH = 8.0
DEFAULT_MAX_DT_HOURS = 7 * 24.0
# A "cluster" of exactly one detection is usually sensor noise/an isolated
# pixel rather than a real fire event worth surfacing - detect_events drops
# clusters below this size instead of persisting one-off singletons.
DEFAULT_MIN_DETECTIONS = 2

# A single wildfire, even a record-setting one, is a highly local event -
# not the ~1,000km, country-spanning blob chaining can produce. The 2021
# Dixie Fire (California) is the largest single-ignition (not multi-fire
# complex) wildfire in modern US record: 3,898 sq km, burned across five
# counties over three months, the first fire ever to cross the Sierra
# Nevada crest - and even it was on the order of 100km end to end, not
# 150+. This bound is set at that scale deliberately: comfortably above
# every ordinary large fire (a 400 sq km/40,000 hectare fire, already
# "large" by most classification systems, is under 25km across even
# heavily elongated), while still treating even a Dixie-Fire-scale outlier
# as worth a second look rather than silently trusting it - see wide_span
# below. Used two ways: as a hard constraint cluster_detections' merge step
# enforces while building clusters (so chaining can no longer produce an
# oversized one at all), and as a defence-in-depth sanity flag detect_events
# still computes and stores per event afterwards (see this constant's use in
# detect_events) in case a future caller invokes cluster_detections with a
# looser bound, or a genuinely extreme real fire hits it.
MAX_PLAUSIBLE_EVENT_SPAN_KM = 100.0

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


# Worst-case runtime bound for _split_to_span_bound's recursive bisection,
# not a correctness dependency - every split there strictly shrinks both
# halves regardless of depth, so this only caps how far a single stubborn
# branch (an evenly-spread, hard-to-shrink pathological case) is allowed to
# keep going before the safety valve accepts it as-is, flagged wide_span
# downstream. 20 levels is far more than realistic data ever needs (each
# level at minimum peels off the single most extreme point from the rest,
# so even the slowest-converging real distribution reaches a compact
# remainder long before 20), while staying trivially cheap even in the
# worst case: one O(n) pass per level, well inside Python's recursion
# limit, and MAX_CLUSTER_DETECTIONS (40,000) points would need every one of
# 20 levels to individually strip off just one point each to exhaust this
# budget without converging - astronomically unlikely for real detections.
_MAX_SPLIT_DEPTH = 20


def _connected_components_on(
    indices: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    ts: np.ndarray,
    radii: np.ndarray,
    v_max_kmh: float,
    max_dt_hours: float,
    max_span_km: float,
) -> list[np.ndarray]:
    """Fast connected-components clustering (scipy, C-level - no per-pair
    Python loop) of just the given subset of global indices, re-projected
    and re-searched around that subset alone. Returns groups as arrays of
    *global* indices (positions into the full lats/lons/ts/radii arrays,
    not into ``indices`` itself), so callers can recurse without ever
    renumbering."""
    m = len(indices)
    if m <= 1:
        return [indices]

    sub_lats, sub_lons, sub_ts, sub_radii = lats[indices], lons[indices], ts[indices], radii[indices]

    # Re-centred on this subset each call, not the original full batch -
    # more accurate the smaller/tighter the subset gets, which is exactly
    # when recursive splitting is calling this on a shrinking piece.
    lat0 = float(np.mean(sub_lats))
    lon0 = float(np.mean(sub_lons))
    lon_km = lon_km_per_deg(lat0)
    x = (sub_lons - lon0) * lon_km
    y = (sub_lats - lat0) * LAT_KM_PER_DEG
    points = np.column_stack([x, y])

    tree = cKDTree(points)
    # Coarse candidate search, capped at max_span_km - provably safe, not
    # just a speed hack: two points more than max_span_km apart can never
    # end up in the same span-bounded cluster regardless of what else joins
    # them first (any set containing both has a bbox diagonal at least
    # their own pairwise distance), so excluding that pair here changes
    # nothing about the final span-bounded result, only how many candidate
    # pairs query_pairs/connected_components have to look at - the default
    # v_max_kmh/max_dt_hours alone already imply a coarse radius over
    # 1300km (see MAX_CLUSTER_DETECTIONS' comment) otherwise.
    coarse_radius = min(float(np.max(sub_radii)) * 2 + v_max_kmh * max_dt_hours, max_span_km)
    pairs = tree.query_pairs(coarse_radius, output_type="ndarray")

    if len(pairs) == 0:
        labels = np.arange(m)
    else:
        i_idx, j_idx = pairs[:, 0], pairs[:, 1]
        dt_hours = np.abs(sub_ts[i_idx] - sub_ts[j_idx]) / 3600.0
        within_time = dt_hours <= max_dt_hours
        allowed = sub_radii[i_idx] + sub_radii[j_idx] + v_max_kmh * dt_hours
        actual = np.hypot(x[i_idx] - x[j_idx], y[i_idx] - y[j_idx])
        linked = within_time & (actual <= allowed)

        ii, jj = i_idx[linked], j_idx[linked]
        if len(ii) == 0:
            labels = np.arange(m)
        else:
            graph = coo_matrix((np.ones(len(ii), dtype=bool), (ii, jj)), shape=(m, m))
            _n_components, labels = connected_components(graph, directed=False)

    groups: dict[int, list[int]] = {}
    for local_idx, label in enumerate(labels):
        groups.setdefault(int(label), []).append(local_idx)
    return [indices[np.array(members, dtype=np.intp)] for members in groups.values()]


def _span_km_of(indices: np.ndarray, lats: np.ndarray, lons: np.ndarray) -> float:
    """Real great-circle diagonal of ``indices``' lat/lon bounding box - the
    actual span-bound check used throughout this module (as opposed to the
    flat-projected distance the clustering search uses internally for
    speed), so a cluster that passes this check is the true distance the UI
    and detect_events' own defence-in-depth recheck will report."""
    sub_lats, sub_lons = lats[indices], lons[indices]
    return haversine_km(
        float(sub_lats.min()), float(sub_lons.min()), float(sub_lats.max()), float(sub_lons.max())
    )


def _split_to_span_bound(
    indices: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    max_span_km: float,
    depth: int = 0,
) -> list[np.ndarray]:
    """Split ``indices`` (already one connected component of the pairwise
    link rule) until every returned piece's real great-circle bounding-box
    diagonal fits within ``max_span_km`` - the mechanism that actually stops
    chaining (see this module's docstring). Only ever called on the rare
    oversized component; an already-compact cluster (the overwhelming
    majority, including a genuinely large, densely-observed single fire)
    returns immediately on the first span check, at zero extra cost beyond
    that one haversine call.

    Splits *geometrically*, not by re-running the link rule under a
    tightened parameter (an earlier version binary-searched a tightened
    v_max_kmh - see this module's docstring for why that was tried and
    rejected: it depends on how the points happen to be distributed in
    time, which a real dense-in-both-space-and-time cluster defeats).

    Bisects at the midpoint of whichever axis (projected x or y) currently
    spans further, recursing on each half - a kd-tree-style split, not a
    fixed absolute grid. An earlier version of this binned points into a
    fixed-origin grid instead: fast and simple, but it judges a point by
    where it falls relative to an arbitrary absolute boundary rather than
    relative to its actual neighbours, so it could isolate a point from
    immediate neighbours a few km away on the far side of a cell edge while
    grouping far corners of the same cell together. Splitting at each
    subset's own midpoint instead adapts to the data's actual shape and
    orientation every time (an elongated chain gets cut across its narrow
    axis, wherever that chain actually sits, not at a fixed coordinate) -
    verified directly against a case a fixed grid handled badly: a chain of
    detections marching steadily in one direction, where the fixed grid
    stranded the two end points as isolated singletons (later dropped
    entirely by min_detections) while the midpoint bisection keeps every
    detection grouped with its real neighbours.

    Always makes progress: the midpoint of a range with distinct min and
    max strictly separates them (the min-valued point never reaches the
    midpoint, the max-valued point always does), so both halves are
    non-empty and strictly smaller in the split axis's extent - no fixed
    iteration budget is being relied on for correctness, only for a
    worst-case runtime bound. One O(n) pass per level, and each branch
    stops recursing the moment its own span fits, so the common case (a
    lopsided split that quickly isolates a few outliers from an already-
    compact bulk) finishes in only a handful of levels; the depth cap below
    is sized for the rare, evenly-spread pathological case instead.
    """
    if len(indices) <= 1 or _span_km_of(indices, lats, lons) <= max_span_km:
        return [indices]
    if depth >= _MAX_SPLIT_DEPTH:
        return [indices]  # safety valve - detect_events' own wide_span flag says so honestly instead

    sub_lats, sub_lons = lats[indices], lons[indices]
    lat0 = float(np.mean(sub_lats))
    lon0 = float(np.mean(sub_lons))
    lon_km = lon_km_per_deg(lat0)
    x = (sub_lons - lon0) * lon_km
    y = (sub_lats - lat0) * LAT_KM_PER_DEG

    x_lo, x_hi = float(x.min()), float(x.max())
    y_lo, y_hi = float(y.min()), float(y.max())
    if (x_hi - x_lo) >= (y_hi - y_lo):
        coord, mid = x, (x_lo + x_hi) / 2.0
    else:
        coord, mid = y, (y_lo + y_hi) / 2.0

    left_mask = coord < mid
    left = indices[left_mask]
    right = indices[~left_mask]

    return _split_to_span_bound(left, lats, lons, max_span_km, depth + 1) + _split_to_span_bound(
        right, lats, lons, max_span_km, depth + 1
    )


def cluster_detections(
    rows: list[dict[str, Any]],
    *,
    v_max_kmh: float = DEFAULT_V_MAX_KMH,
    max_dt_hours: float = DEFAULT_MAX_DT_HOURS,
    max_span_km: float = MAX_PLAUSIBLE_EVENT_SPAN_KM,
) -> list[list[int]]:
    """Group detection indices into clusters via the space-time link rule,
    subject to a hard span bound (see this module's docstring).

    ``rows`` items need ``lat``, ``lon``, ``ts`` (unix seconds), and
    optionally ``scan``/``track`` (km). Returns a list of clusters, each a
    list of indices into ``rows`` - every detection appears in exactly one
    cluster, including clusters of size 1. No returned cluster's real
    bounding-box diagonal exceeds ``max_span_km``, short of the depth safety
    valve above (logged loudly by detect_events, since it's expected to be
    unreachable in practice).
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

    all_indices = np.arange(n)
    clusters: list[list[int]] = []
    # Two-stage pipeline: the fast connected-components pass applies the
    # pairwise link rule but can still chain into an oversized component
    # (see this module's docstring), so every component is then run through
    # the span-bounded splitter, which is a no-op for the common already-
    # compact case and only does real work on the rare chained one.
    for group in _connected_components_on(all_indices, lats, lons, ts, radii, v_max_kmh, max_dt_hours, max_span_km):
        for bounded in _split_to_span_bound(group, lats, lons, max_span_km):
            clusters.append([int(i) for i in bounded])
    return clusters


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
        # west > east means the bbox straddles the antimeridian (e.g. a view
        # centred near +/-180deg) - "between west and east" is inverted in
        # that case, so match longitude on either side of the split instead.
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
    max_span_km = float(params.get("max_span_km", MAX_PLAUSIBLE_EVENT_SPAN_KM))

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

        clusters = cluster_detections(rows, v_max_kmh=v_max_kmh, max_dt_hours=max_dt_hours, max_span_km=max_span_km)
        ctx.report_progress(60, note=f"{len(clusters)} candidate clusters")

        now = int(time_module.time())
        run_params = {
            "v_max_kmh": v_max_kmh,
            "max_dt_hours": max_dt_hours,
            "min_detections": min_detections,
            "max_span_km": max_span_km,
        }
        event_ids: list[int] = []
        wide_span_count = 0

        # Explicit transaction: every event + its members is inserted one
        # statement at a time below, so wrapping the whole batch avoids a
        # commit-per-row round trip and keeps a run's events from becoming
        # partially visible mid-insert.
        conn.execute("BEGIN")
        for indices in clusters:
            if len(indices) < min_detections:
                continue
            members = [rows[i] for i in indices]
            lats = [m["lat"] for m in members]
            lons = [m["lon"] for m in members]
            tss = [m["ts"] for m in members]
            sats = sorted({m["source"] for m in members})

            # cluster_detections() already enforces max_span_km while
            # building clusters (see this module's docstring), so this
            # should be a no-op in practice - recomputed here via the
            # module's real haversine great-circle distance (rather than
            # trusting the clustering step's own flat-earth-projected
            # bbox check) as defence-in-depth, not as the primary
            # mechanism anymore.
            span_km = haversine_km(min(lats), min(lons), max(lats), max(lons))
            wide_span = span_km > max_span_km
            if wide_span:
                wide_span_count += 1
            event_params = {**run_params, "span_km": round(span_km, 1), "wide_span": wide_span}

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
                    json.dumps(event_params),
                    now,
                ),
            )
            event_id = cur.lastrowid
            # OR IGNORE: event_id is freshly minted above, but a detection
            # could in principle appear twice in `members` (defensive - not
            # expected from cluster_detections' own indices, which are
            # unique) without this turning into a constraint-violation abort
            # of the whole transaction.
            conn.executemany(
                "INSERT OR IGNORE INTO event_members (event_id, detection_id) VALUES (?, ?)",
                [(event_id, m["id"]) for m in members],
            )
            event_ids.append(event_id)
            if wide_span:
                # Expected to be rare: cluster_detections' recursive
                # bisection splitter (_split_to_span_bound) makes provable
                # progress on every split regardless of the data, so it
                # converges under the bound for the overwhelming majority
                # of cases well within its depth budget (_MAX_SPLIT_DEPTH).
                # Reachable in practice, not just theory, if a caller passes
                # a looser max_span_km than that splitter used, or if a
                # genuinely pathological subset needs more than 20 levels of
                # bisection to converge - in which case this flag, not a
                # silently oversized "fire", is exactly what should reach
                # the UI. Worth keeping loud either way.
                log.warning(
                    "detect_events: event %s spans %.0f km (%d detections) - past the %.0f km "
                    "plausible-single-fire bound despite span-bounded clustering; flagged "
                    "wide_span for the UI",
                    event_id, span_km, len(members), max_span_km,
                )
        conn.commit()

        ctx.report_progress(100, note=f"{len(event_ids)} events stored")
        return {
            "event_count": len(event_ids),
            "event_ids": event_ids,
            "detection_count": len(rows),
            # Despite the name, this counts every cluster filtered out by
            # the min_detections floor, not literally just size-1 clusters -
            # min_detections can be set above 2 via params.
            "singleton_dropped": len(clusters) - len(event_ids),
            "truncated": truncated,
            "wide_span_events": wide_span_count,
        }
    finally:
        conn.close()


# ---------------------------------------------------- spread-over-time contours


# Maximum number of cumulative time bands. Raised from 5 to 10 so the
# rendered rings are as fine-grained as the source passes allow, matching
# a reference nested-ring fire-progression image - the app.js renderer no
# longer snaps each band to one of --time-1..5's 5 fixed LUT stops (see
# app.css's --time-* comment); it interpolates continuously between them
# via each band's band_fraction, so this constant is free to be any size
# without needing a matching number of CSS stops. A genuine upper bound,
# not a fixed count: spread_topology only ever produces one band per
# distinct satellite overpass actually present (see _group_into_passes),
# so a sparse 2-3-pass fire returns 2-3 bands, not 10 padded-out
# duplicates.
SPREAD_TOPOLOGY_BANDS = 10

# Detections from the same satellite overpass over a small AOI land within
# a couple of minutes of each other; the next overpass (same or a
# different satellite/instrument) is typically hours away. This gap
# threshold separates "same pass" from "different pass" generously enough
# to absorb ordinary scan-to-scan timing jitter within one real pass,
# without being so loose it merges two satellites that happen to cross the
# same area within the same half hour.
SPREAD_TOPOLOGY_PASS_GAP_S = 30 * 60.0

# Each detection's Gaussian kernel never gets narrower than this on the
# grid, mirroring likelihood.py's active_heat_raster (MIN_SIGMA_M there) -
# a detection's footprint radius can be smaller than one grid cell at a
# coarse resolution, and an unclamped sigma would make its contribution
# vanish between cell centres instead of covering at least a plausible
# minimum area.
SPREAD_TOPOLOGY_MIN_SIGMA_M = 150.0

# Contour level as a fraction of a single point's own peak kernel value
# (always 1.0, at its own centre - see _spread_density_raster). Low enough
# to trace each band's full plausible footprint (roughly 2 sigma out - the
# same "how far out does one detection's own footprint plausibly extend"
# question active_heat_raster's kernel answers, not probability_envelopes'
# unrelated probability-*mass* question, so that function's mass-quantile
# threshold logic doesn't apply here).
SPREAD_TOPOLOGY_LEVEL = 0.15


def _spread_density_raster(
    detections: list[dict[str, Any]], geom: dict[str, Any]
) -> np.ndarray:
    """(ny, nx) density field: each detection contributes a Gaussian kernel
    of its own (its footprint radius becomes the kernel's sigma, floored at
    SPREAD_TOPOLOGY_MIN_SIGMA_M), combined by max rather than summed - a
    cell's value is "how close is this to the *nearest* contributing
    detection", capped at 1.0 at each detection's own centre, so
    SPREAD_TOPOLOGY_LEVEL means the same fixed distance-from-a-detection
    regardless of how many other detections happen to overlap that cell.
    Structurally the density half of active_heat_raster, without that
    function's time-decay/sensor/confidence weighting - this is a pure
    "where has anything been detected" footprint, not a decayed likelihood."""
    ny, nx = geom["ny"], geom["nx"]
    density = np.zeros((ny, nx), dtype=np.float64)
    if not detections:
        return density

    gx, gy = grid_xy_m(geom)
    lats = np.array([d["lat"] for d in detections])
    lons = np.array([d["lon"] for d in detections])
    dx_all, dy_all = detections_xy_m(geom, lats, lons)

    for i, det in enumerate(detections):
        radius_km = detection_footprint_radius_km(det, DEFAULT_FOOTPRINT_RADIUS_KM)
        sigma = max(SPREAD_TOPOLOGY_MIN_SIGMA_M, radius_km * 1000.0)
        ddx = gx - dx_all[i]
        ddy = gy - dy_all[i]
        kernel = np.exp(-(ddx**2 + ddy**2) / (2.0 * sigma**2))
        np.maximum(density, kernel, out=density)

    return density


def _contour_band(raster: np.ndarray, geom: dict[str, Any], level: float) -> list[dict[str, Any]]:
    """Polygon rings of ``raster``'s ``level`` contour, as bare coordinate
    lists (``[[lon, lat], ...]``) - same row/col -> lon/lat conversion
    likelihood.probability_envelopes uses. Returns [] if the level never
    crosses the raster (too few/too sparse detections at this band) rather
    than raising - a genuinely empty band, not an error."""
    if raster.max() <= level:
        return []
    try:
        # Pad with a one-cell border below the contour level before tracing.
        #
        # find_contours returns a *closed* path for a feature wholly inside the
        # raster, but an *open* one wherever the contour runs off an edge.
        # Closing an open path by appending its first point draws a straight
        # chord from where it left the raster back to where it re-entered -
        # which rendered as a hard-edged wedge slicing across the map, most
        # visibly on the outermost (latest) band, since that is the one that
        # actually reaches the edge.
        #
        # Padding makes every feature that touched the edge close naturally
        # just inside the border, so there are no open paths left to
        # mis-close. Cheaper and far less error-prone than walking the raster
        # perimeter to close them by hand.
        padded = np.pad(raster, 1, mode="constant", constant_values=float(level) - 1.0)
        contours = measure.find_contours(padded, level)
    except Exception:  # pragma: no cover - defensive, skimage edge cases
        return []

    west, south, east, north = geom["bbox"]
    nx, ny = geom["nx"], geom["ny"]
    rings = []
    for contour in contours:
        if len(contour) < 3:
            continue
        coords = [
            # -1 on each axis undoes the pad, putting the contour back in the
            # original raster's coordinate space.
            [round(west + ((col - 1) / nx) * (east - west), 6),
             round(south + ((row - 1) / ny) * (north - south), 6)]
            for row, col in contour
        ]
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        rings.append(coords)
    return rings


def _group_into_passes(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split ``rows`` into distinct satellite-overpass groups: consecutive-
    in-time detections within SPREAD_TOPOLOGY_PASS_GAP_S of each other
    collapse into one pass, a larger gap starts a new one.

    Real FIRMS data arrives in a handful of discrete overpasses a day (each
    tracked sensor sweeps a given small AOI in minutes, then is gone for
    hours), not a smooth continuum. Naively picking SPREAD_TOPOLOGY_BANDS
    *linear time-quantile* cutoffs over raw per-detection timestamps can
    land two cutoffs minutes apart within the very same overpass - wasting
    most of the "temporal resolution" resolving near-duplicates - while the
    real multi-hour/day gaps between actual overpasses go unrepresented.
    Confirmed live: a 97-detection real test case produced quantile cutoffs
    45 and 100 minutes apart within two clumps, each ~24h from the other -
    two of five bands were essentially redundant. Grouping into passes
    first, then picking cutoffs from *those*, gives genuinely time-spread
    snapshots instead.
    """
    ordered = sorted(rows, key=lambda r: r["ts"])
    passes: list[list[dict[str, Any]]] = []
    for row in ordered:
        if passes and row["ts"] - passes[-1][-1]["ts"] <= SPREAD_TOPOLOGY_PASS_GAP_S:
            passes[-1].append(row)
        else:
            passes.append([row])
    return passes


def spread_topology(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Job body: contour the *cumulative* footprint of cached detections in
    a bbox/time window at up to SPREAD_TOPOLOGY_BANDS cutoffs, one per
    distinct satellite overpass (see _group_into_passes) - "Spread over
    Time" on the map, a nested-rings view of where detections had
    accumulated by each point in time (earliest band innermost/smallest,
    latest outermost/largest - see this module's docstring convention
    notes and app.js's drawSpreadTopology for the rendering side).

    Each band is cumulative (every detection up to and including that
    band's chosen pass), so band i's raster is a superset of band i-1's by
    construction - the nesting is geometrically guaranteed, not just a
    drawing-order convention. Fewer than SPREAD_TOPOLOGY_BANDS distinct
    passes means fewer bands, not padded-out duplicates.

    Each feature carries both ``band_index`` (its plain 0-based slot
    among the bands actually produced this run - a stable sort key for
    draw order, nothing more) and ``band_fraction`` (that same slot
    rescaled to 0..1 across whatever band count this run produced, 0 =
    earliest, 1 = latest). The renderer colors by ``band_fraction``
    through timeSpreadColor()'s continuous interpolation across the
    --time-1..5 stops, not by snapping ``band_index`` onto a fixed LUT -
    so a sparse 2-3-band fire still spans the ramp's full earliest-to-
    latest range instead of bunching at one end, without this function
    needing to know how many CSS stops exist on the other side.
    """
    bbox = tuple(params["bbox"])  # west, south, east, north
    start_ts = int(params["start_ts"])
    end_ts = int(params["end_ts"])
    sources = params.get("sources") or None

    conn = sqlite3.connect(ctx.db_path, timeout=30.0)
    try:
        rows = _fetch_detections(conn, bbox, start_ts, end_ts, sources)
        ctx.report_progress(15, note=f"{len(rows)} detections to contour")
        if not rows:
            return {"band_count": 0, "pass_count": 0, "detection_count": 0, "files": {}}

        geom = grid_geometry(bbox)
        ctx.report_progress(25, note=f"grid {geom['nx']}x{geom['ny']} @ {geom['res_m']:.0f}m")

        passes = _group_into_passes(rows)
        n_passes = len(passes)
        n_bands = min(SPREAD_TOPOLOGY_BANDS, n_passes)
        # Evenly spread by *pass index*, not by detection count within each
        # pass - a pass with many detections shouldn't crowd out a sparser
        # but temporally distinct one. Rounding can collide two positions
        # onto the same pass index for a small n_passes - dedupe rather
        # than emit two identical bands.
        selected_indices = sorted({int(round(p)) for p in np.linspace(0, n_passes - 1, n_bands)})
        n_bands = len(selected_indices)

        features: list[dict[str, Any]] = []
        for slot, pass_idx in enumerate(selected_indices):
            # band_fraction is this slot's position across whatever band
            # count this run actually produced, 0 (earliest) .. 1 (latest)
            # - continuous, not a LUT index, so the renderer's
            # timeSpreadColor() can interpolate across the full ramp
            # regardless of n_bands, and a sparse 2-3-band fire spans the
            # whole earliest-to-latest range rather than bunching at one
            # end. band_index is just this slot's plain position, kept as
            # a stable draw-order sort key (see renderSpreadTopologyLayer).
            band_fraction = slot / max(1, n_bands - 1)
            cutoff_ts = passes[pass_idx][-1]["ts"]  # latest detection in this pass
            band_rows = [r for r in rows if r["ts"] <= cutoff_ts]
            raster = _spread_density_raster(band_rows, geom)
            rings = _contour_band(raster, geom, SPREAD_TOPOLOGY_LEVEL)
            for ring in rings:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": [ring]},
                        "properties": {
                            "band_index": slot,
                            "band_fraction": band_fraction,
                            "cutoff_ts": int(cutoff_ts),
                            "detection_count": len(band_rows),
                        },
                    }
                )
            ctx.report_progress(25 + int(65 * (slot + 1) / n_bands), note=f"band {slot} done ({n_passes} passes total)")

        result_dir = ctx.result_path
        (result_dir / "spread_topology.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": features})
        )

        ctx.report_progress(100, note=f"{len(features)} contour ring(s) across {n_bands} band(s) ({n_passes} passes total)")
        return {
            "band_count": n_bands,
            "pass_count": n_passes,
            "ring_count": len(features),
            "detection_count": len(rows),
            "cutoffs": [int(passes[i][-1]["ts"]) for i in selected_indices],
            "files": {"topology": "spread_topology.geojson"},
        }
    finally:
        conn.close()


register_kind("detect_events", detect_events)
register_kind("spread_topology", spread_topology)
