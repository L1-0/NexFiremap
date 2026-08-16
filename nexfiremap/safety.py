"""Evaluating tracked units against the hazards and the model around them.

This is where the physics stops being a picture and starts protecting people.

`terrain.py` computes when fire reaches every cell of a grid. `telemetry.py`
records where every crew and vehicle is. Until this module existed those two
facts sat in separate panels and it was the incident commander's job to notice
that one overlapped the other - during the exact period when they have least
attention to spare.

Two evaluations run on each accepted position:

1. **Geofence** - is the unit inside an *area* typed as dangerous (a burn area,
   an observed or forecast perimeter, an evacuation area)? See
   `HAZARD_FEATURE_TYPES` for why point types are deliberately excluded.
2. **Modelled arrival** - is the unit standing somewhere the attached
   propagation run expects fire to reach within the warning horizon?

The second deliberately reports a *band* (earliest/median/latest) rather than
one number, and every message says "modelled". A crew deciding whether to
withdraw should see the uncertainty the ensemble actually produced, not a
figure that reads like a timetable.

**Failure policy is the opposite of the rest of the ingest path.** Everywhere
else a bad input is rejected. Here, an evaluation that fails - a pruned job
directory, a missing optional dependency, a corrupt raster - must never reject
the *position*, because a position that is not stored is a unit that vanishes
off the map. So every entry point returns warnings-or-nothing and logs, and the
caller ingests regardless.

Performance matters because this runs inside the ingest hot path, on every
position of every vehicle: arrival rasters are cached by job id (they are
re-read otherwise on each of potentially hundreds of positions per minute), and
the geofence does a bounding-box rejection before any point-in-polygon test.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from .operations import point_in_polygon

log = logging.getLogger("nexfiremap.safety")

#: Feature types whose *interior* is dangerous to stand in. A closed set rather
#: than "anything not explicitly safe", so adding a feature type to the
#: vocabulary cannot accidentally start generating warnings.
#:
#: Every member is an AREA type. Point types are deliberately excluded even
#: where they sound hazardous - `hazard` is a point, and a point has no
#: interior to be inside. Warning on one would mean inventing a radius nobody
#: entered, and a made-up safety distance is worse than none.
#:
#: `structure_protection_area` is likewise excluded: it marks something worth
#: defending, not somewhere dangerous to be, and warning crews for standing
#: where they were sent would train them to ignore the warnings.
HAZARD_FEATURE_TYPES = {
    "confirmed_perimeter",   # inside the fire as observed
    "forecast_perimeter",    # inside the fire as modelled
    "burn_area",
    "evacuation_area",
}

#: Hours of modelled arrival time below which a unit is warned. Two hours is
#: the conventional wildland withdrawal-planning horizon and matches what the
#: `withdrawal_triggers` safety check exists to plan for.
DEFAULT_WARNING_HOURS = 2.0

#: How long a loaded arrival raster stays cached. Long enough to serve a burst
#: of positions from one fleet, short enough that a re-run model is picked up
#: without restarting the server.
RASTER_CACHE_SECONDS = 300

_raster_cache: dict[int, tuple[float, dict[str, Any] | None]] = {}
_cache_lock = threading.Lock()


def _load_arrival(job_dir: Any, job_id: int, bounds: list[list[float]] | None) -> dict[str, Any] | None:
    """Load and cache one job's arrival surface.

    Cached because this is called once per position per unit: a fleet of twenty
    vehicles reporting every ten seconds would otherwise re-read and decompress
    the same npz a hundred and twenty times a minute.

    A cached ``None`` is meaningful and deliberate - it records "this job has no
    usable surface", so a pruned or failed job is not retried on every single
    position.
    """
    now = time.time()
    with _cache_lock:
        cached = _raster_cache.get(job_id)
        if cached and now - cached[0] < RASTER_CACHE_SECONDS:
            return cached[1]

    surface: dict[str, Any] | None = None
    try:
        import numpy as np

        path = job_dir / str(job_id) / "impact_surface.npz"
        if path.is_file() and bounds:
            with np.load(path) as data:
                surface = {"bounds": bounds,
                           **{key: data[key] for key in ("earliest_hours", "median_hours", "latest_hours")
                              if key in data}}
    except Exception as exc:  # noqa: BLE001 - see module docstring on failure policy
        log.debug("Arrival surface for job %s unavailable: %s", job_id, exc)
        surface = None

    with _cache_lock:
        _raster_cache[job_id] = (now, surface)
    return surface


def clear_cache() -> None:
    """Drop cached rasters. For tests, and after a model is re-run."""
    with _cache_lock:
        _raster_cache.clear()


def _active_arrival(db: Any, settings: Any, incident_id: str) -> dict[str, Any] | None:
    """The arrival surface an incident is currently planning against.

    Chosen from the incident's *linked* model runs rather than from the newest
    job on the machine: a run nobody attached is a scratch calculation, and
    warning a crew on the strength of one would be warning them about a model
    their commander never adopted.
    """
    row = db.conn.execute(
        "SELECT ref_id, snapshot_json FROM incident_links "
        "WHERE incident_id=? AND kind='model_run' ORDER BY created_at DESC LIMIT 1",
        (incident_id,)).fetchone()
    if row is None:
        return None
    try:
        job_id = int(row["ref_id"])
    except (TypeError, ValueError):
        return None
    job = db.get_job(job_id)
    if job is None:
        return None
    try:
        bounds = json.loads(dict(job).get("result_json") or "{}").get("bounds")
    except (TypeError, ValueError):
        bounds = None
    arrival = _load_arrival(settings.job_dir, job_id, bounds)
    if arrival is None:
        return None

    # Carry the run's own staleness through to the warning. The wind module and
    # the scenario panel both track `is_stale`, and this - the one consumer
    # where being wrong is a life-safety matter - took the newest linked run
    # unconditionally. A surface computed against weather from many hours ago
    # can put the arrival band in the wrong place in either direction, and a
    # crew told "fire reaches you in 40 minutes" deserves to know the number
    # came from a model nobody has re-run since the wind changed.
    #
    # Deliberately *not* a reason to withhold the warning: a stale forecast is
    # still the best available estimate, and suppressing it would trade a
    # qualified warning for no warning at all.
    try:
        snapshot = json.loads(row["snapshot_json"] or "{}")
        provenance = snapshot.get("provenance") or {}
    except (TypeError, ValueError):
        provenance = {}
    arrival["is_stale"] = bool(provenance.get("is_stale"))
    arrival["valid_until"] = provenance.get("valid_until")
    return arrival


def evaluate_position(db: Any, settings: Any, incident_id: str, *, callsign: str,
                      latitude: float, longitude: float,
                      warning_hours: float = DEFAULT_WARNING_HOURS) -> list[dict[str, Any]]:
    """Warnings raised by one unit standing at one point.

    Returns a list (possibly empty) and never raises - see the module docstring:
    a failed evaluation must not cost the position that triggered it.
    """
    warnings: list[dict[str, Any]] = []
    try:
        warnings.extend(_geofence(db, incident_id, callsign, latitude, longitude))
    except Exception as exc:  # noqa: BLE001
        log.debug("Geofence evaluation failed for %s: %s", callsign, exc)
    try:
        warnings.extend(_arrival(db, settings, incident_id, callsign, latitude, longitude, warning_hours))
    except Exception as exc:  # noqa: BLE001
        log.debug("Arrival evaluation failed for %s: %s", callsign, exc)
    return warnings


def _geofence(db: Any, incident_id: str, callsign: str,
              latitude: float, longitude: float) -> list[dict[str, Any]]:
    """Hazard-typed features containing this position.

    Only polygons are tested: a hazard *point* has no interior to be inside,
    and treating one as a radius would be inventing a distance nobody entered.
    """
    placeholders = ",".join("?" * len(HAZARD_FEATURE_TYPES))
    rows = db.conn.execute(
        f"SELECT id,feature_type,title,geometry_json FROM tactical_features "
        f"WHERE incident_id=? AND deleted_at IS NULL AND feature_type IN ({placeholders})",
        (incident_id, *sorted(HAZARD_FEATURE_TYPES))).fetchall()
    warnings = []
    for row in rows:
        try:
            geometry = json.loads(row["geometry_json"])
        except (TypeError, ValueError):
            continue
        if geometry.get("type") != "Polygon":
            continue
        # point_in_polygon does its own bbox rejection first, which is what
        # keeps this cheap on the ingest hot path.
        if point_in_polygon(longitude, latitude, geometry):
            warnings.append({
                "code": "inside_hazard_area",
                "severity": "high",
                "callsign": callsign,
                "feature_id": row["id"],
                "feature_type": row["feature_type"],
                "message": (f"{callsign} is inside {row['title'] or row['feature_type']} "
                            f"({str(row['feature_type']).replace('_', ' ')})"),
            })
    return warnings


def _arrival(db: Any, settings: Any, incident_id: str, callsign: str,
             latitude: float, longitude: float, warning_hours: float) -> list[dict[str, Any]]:
    """Whether the attached model expects fire here within the horizon."""
    from .structures import _sample_hours

    surface = _active_arrival(db, settings, incident_id)
    if not surface:
        return []
    point = {"lat": latitude, "lon": longitude}
    bands: dict[str, float] = {}
    for key, label in (("earliest_hours", "earliest"), ("median_hours", "median"),
                       ("latest_hours", "latest")):
        if key in surface:
            hours = _sample_hours(surface[key], point, surface["bounds"])
            if hours is not None:
                bands[label] = round(float(hours), 2)
    if not bands:
        return []
    # The *earliest* band drives the warning, not the median: a withdrawal
    # decision is made against the worst credible case the ensemble produced,
    # and warning on the median would mean half the members had already
    # arrived before anyone was told.
    soonest = bands.get("earliest", bands.get("median"))
    if soonest is None or soonest > warning_hours:
        return []
    span = (f" (median {bands['median']:g} h)" if "median" in bands and bands["median"] != soonest else "")
    # Say so when the surface behind the number is stale. The warning still
    # fires - a stale forecast beats no forecast - but "in ~40 minutes" from a
    # model nobody has re-run since the wind changed is a different claim from
    # the same number off a current one, and the crew acting on it is entitled
    # to the difference.
    stale = " - from a STALE model run, re-run before relying on the timing" \
        if surface.get("is_stale") else ""
    return [{
        "code": "modelled_arrival_imminent",
        "severity": "high" if soonest <= warning_hours / 2 else "elevated",
        "callsign": callsign,
        "hours": bands,
        "model_is_stale": bool(surface.get("is_stale")),
        "model_valid_until": surface.get("valid_until"),
        "message": (f"{callsign}: fire modelled to arrive in ~{soonest:g} h{span} - "
                    f"modelled, not observed{stale}"),
    }]


def record_warnings(store: Any, incident_id: str, warnings: list[dict[str, Any]],
                    actor: str = "safety") -> None:
    """Write warnings to the incident's audit trail.

    The audit log rather than a new table, so these travel in `export_bundle`
    and appear in a handover package like every other thing that happened -
    "we were told the crew was in the path" is exactly the kind of fact an
    after-action review needs, and it should not live somewhere that has to be
    remembered separately.
    """
    if not warnings:
        return
    try:
        with store.db._write_lock:
            for warning in warnings:
                store.audit_log.record(
                    incident_id, "safety_warning", str(warning.get("feature_id") or warning["code"]),
                    "raise", 1, warning, actor)
            store.db.conn.commit()
    except Exception as exc:  # noqa: BLE001 - never cost the position
        log.warning("Could not record safety warnings for %s: %s", incident_id, exc)


__all__ = ["DEFAULT_WARNING_HOURS", "HAZARD_FEATURE_TYPES", "RASTER_CACHE_SECONDS",
           "clear_cache", "evaluate_position", "record_warnings",
           "record_watch", "watch_detections",
           "CONTROL_LINE_BUILT", "CONTROL_LINE_TYPES", "control_mask"]


# ------------------------------------------------------------- AOI watch

def watch_detections(db: Any, store: Any, since_ts: float,
                     limit: int = 5000) -> list[dict[str, Any]]:
    """New detections that landed inside an active incident's AOI.

    The reverse direction of everything above: rather than asking "is this unit
    in danger", it asks "does this new observation concern anyone". Without it,
    an incident commander finds out that the fire crossed into their area by
    looking at the map at the right moment.

    Returns one entry per (incident, detection-batch) rather than per detection,
    because "14 new detections inside your AOI" is a usable banner and fourteen
    separate ones is noise that gets dismissed.

    Cheap by construction: it only looks at detections newer than `since_ts`,
    and `incidents_covering` rejects on a bounding box before any
    point-in-polygon test. Intended to be called on the existing refresh cycle.
    """
    rows = db.conn.execute(
        "SELECT latitude,longitude,acq_ts,source FROM detections "
        "WHERE acq_ts > ? ORDER BY acq_ts DESC LIMIT ?", (since_ts, limit)).fetchall()
    if not rows:
        return []
    hits: dict[str, dict[str, Any]] = {}
    for row in rows:
        for incident in store.incidents_covering(row["latitude"], row["longitude"], active_only=True):
            entry = hits.setdefault(incident["id"], {
                "incident_id": incident["id"], "incident_name": incident["name"],
                "count": 0, "newest_ts": 0, "sources": set()})
            entry["count"] += 1
            entry["newest_ts"] = max(entry["newest_ts"], row["acq_ts"])
            entry["sources"].add(row["source"])
    results = []
    for entry in hits.values():
        entry["sources"] = sorted(entry["sources"])
        entry["message"] = (f"{entry['count']} new detection(s) inside the area of interest for "
                            f"{entry['incident_name']}")
        results.append(entry)
    return results


def record_watch(store: Any, hits: list[dict[str, Any]], actor: str = "watch") -> None:
    """Write AOI-watch hits to each incident's audit trail, so "we were told at
    03:12Z" survives into the handover package like everything else."""
    for hit in hits:
        try:
            with store.db._write_lock:
                store.audit_log.record(
                    hit["incident_id"], "aoi_watch", str(hit["newest_ts"]),
                    "detections", 1, hit, actor)
                store.db.conn.commit()
        except Exception as exc:  # noqa: BLE001 - a watch notice must never break the refresh cycle
            log.warning("Could not record AOI watch for %s: %s", hit["incident_id"], exc)


# ------------------------------------------- operations feeding the model

#: Feature types that act as a barrier to spread once *built*. A planned line
#: is not a barrier - the fire does not care about intent - so `control_mask`
#: filters on status as well as type.
CONTROL_LINE_TYPES = {"tactical_line", "escape_route", "road_closure", "road_restriction"}

#: Statuses meaning the line exists on the ground. "proposed"/"planned" are
#: excluded deliberately: feeding a plan into the model as though it were built
#: would produce a forecast that flatters the plan, which is the single most
#: dangerous way this feature could be wrong.
CONTROL_LINE_BUILT = {"completed", "held", "under_construction"}


def control_mask(db: Any, incident_id: str, bounds: list[list[float]],
                 shape: tuple[int, int], width_cells: int = 1) -> Any:
    """Rasterise an incident's built control lines onto a model grid.

    Returns a boolean array of `shape` marking cells a line passes through, for
    `terrain.build_directional_behavior`'s `control_lines` argument.

    Lines are walked segment by segment and sampled densely enough that no cell
    is skipped diagonally - a barrier with a one-cell gap in it is not a barrier,
    and the fire will find the gap.
    """
    import numpy as np

    mask = np.zeros(shape, dtype=bool)
    south, west = bounds[0]
    north, east = bounds[1]
    ny, nx = shape
    if east <= west or north <= south:
        return mask

    placeholders = ",".join("?" * len(CONTROL_LINE_TYPES))
    rows = db.conn.execute(
        f"SELECT geometry_json,status FROM tactical_features "
        f"WHERE incident_id=? AND deleted_at IS NULL AND feature_type IN ({placeholders})",
        (incident_id, *sorted(CONTROL_LINE_TYPES))).fetchall()

    def plot(lon: float, lat: float) -> None:
        if not (west <= lon <= east and south <= lat <= north):
            return
        col = min(nx - 1, max(0, int((lon - west) / (east - west) * nx)))
        row = min(ny - 1, max(0, int((north - lat) / (north - south) * ny)))
        low_r, high_r = max(0, row - width_cells + 1), min(ny, row + width_cells)
        low_c, high_c = max(0, col - width_cells + 1), min(nx, col + width_cells)
        mask[low_r:high_r, low_c:high_c] = True

    for row in rows:
        if str(row["status"]) not in CONTROL_LINE_BUILT:
            continue
        try:
            geometry = json.loads(row["geometry_json"])
        except (TypeError, ValueError):
            continue
        if geometry.get("type") != "LineString":
            continue
        points = geometry.get("coordinates") or []
        for start, end in zip(points, points[1:]):
            # Sample each segment at sub-cell spacing. Stepping by whole
            # vertices would leave gaps wherever a segment is longer than a
            # cell, and a barrier with gaps is not a barrier.
            steps = max(2, int(max(
                abs(end[0] - start[0]) / max(1e-9, (east - west) / nx),
                abs(end[1] - start[1]) / max(1e-9, (north - south) / ny)) * 2) + 1)
            for index in range(steps + 1):
                fraction = index / steps
                plot(start[0] + (end[0] - start[0]) * fraction,
                     start[1] + (end[1] - start[1]) * fraction)
    return mask
