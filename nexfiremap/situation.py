"""What every module knows about one point on the map.

An operator points at an unfamiliar spot and asks the only question that
matters: *what is here, and how long have I got?* Answering it today means
reading five panels and doing the arithmetic yourself - nearest detections in
one, the event they belong to in another, the modelled arrival time in a third,
structures in a fourth, the next satellite pass in a fifth.

This module answers it once. Each subsystem contributes a **provider** - a
plain function `(context) -> Section | None` - and the endpoint runs them all
and returns whatever came back.

The registry shape is doing real work, not decoration:

* **A provider that fails does not fail the answer.** Providers reach into
  optional dependencies (rasterio for the arrival raster, skyfield for
  overpasses), the network (alerts), and job results that may have been pruned.
  Any of those can be missing on a given install or at a given moment, and an
  operator asking "what's here" during an incident must not get a 500 because
  the ensemble job directory was cleaned up. Same graceful-degradation contract
  as `routes/common.py`'s `AVAILABLE_FEATURES`.
* **A provider that could not run is *named*, not silently dropped.** The
  response carries an `unavailable` list, because "we did not ask" and "there
  is nothing there" are completely different answers when someone is deciding
  whether to send a crew into a valley. Reporting an empty section as
  reassurance would be the most dangerous thing this file could do.

Every number that is *modelled* rather than *observed* says so in its own
summary text, matching the derived-vs-reported discipline in `telemetry.py`.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .geo import haversine_km

log = logging.getLogger("nexfiremap.situation")

#: Default search radius. Big enough to catch the fire behaviour that concerns
#: a crew standing at the point, small enough that the answer is about *here*
#: rather than about the region.
DEFAULT_RADIUS_KM = 5.0
MAX_RADIUS_KM = 50.0


@dataclass
class SituationContext:
    """Everything a provider is allowed to reach. Passed rather than imported
    so providers stay testable in isolation and cannot quietly acquire new
    dependencies."""

    lat: float
    lon: float
    radius_km: float
    app: Any
    now: float = field(default_factory=time.time)

    @property
    def db(self) -> Any:
        return self.app.state.db

    def bbox(self) -> tuple[float, float, float, float]:
        """A degree box around the point, for indexed queries.

        Longitude is divided by cos(latitude) because a degree of longitude
        narrows towards the poles - without it the box would be too narrow
        east-west, increasingly so at higher latitudes, and would silently miss
        detections that are within the radius.
        """
        lat_span = self.radius_km / 110.574
        lon_span = self.radius_km / (111.320 * max(0.01, math.cos(math.radians(self.lat))))
        return (self.lon - lon_span, self.lat - lat_span, self.lon + lon_span, self.lat + lat_span)


#: name -> provider. Ordered by how an operator reads the answer: what is
#: burning, what it belongs to, when it reaches me, what is at stake, who is
#: nearby, when we will next see it.
_PROVIDERS: dict[str, Callable[[SituationContext], dict[str, Any] | None]] = {}


def provider(name: str):
    """Register a situation provider under `name`."""
    def register(function: Callable[[SituationContext], dict[str, Any] | None]):
        _PROVIDERS[name] = function
        return function
    return register


def _age_text(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} h ago"
    return f"{seconds / 86400:.1f} days ago"


# --------------------------------------------------------------- providers

@provider("detections")
def _detections(ctx: SituationContext) -> dict[str, Any] | None:
    """Nearest satellite detections and how old they are."""
    west, south, east, north = ctx.bbox()
    rows = ctx.db.conn.execute(
        "SELECT latitude,longitude,acq_ts,frp,source,confidence_level FROM detections "
        "WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? "
        "ORDER BY acq_ts DESC LIMIT 500",
        (south, north, west, east)).fetchall()
    nearby = []
    for row in rows:
        distance = haversine_km(ctx.lat, ctx.lon, row["latitude"], row["longitude"])
        if distance <= ctx.radius_km:
            nearby.append({"distance_km": round(distance, 2), "acq_ts": row["acq_ts"],
                           "frp": row["frp"], "source": row["source"],
                           "confidence": row["confidence_level"]})
    if not nearby:
        return {"label": "Detections", "summary": f"none within {ctx.radius_km:g} km", "count": 0}
    nearest = min(nearby, key=lambda item: item["distance_km"])
    newest = max(nearby, key=lambda item: item["acq_ts"])
    return {
        "label": "Detections",
        "summary": (f"{len(nearby)} within {ctx.radius_km:g} km; nearest "
                    f"{nearest['distance_km']:g} km, most recent "
                    f"{_age_text(max(0.0, ctx.now - newest['acq_ts']))}"),
        "count": len(nearby), "nearest_km": nearest["distance_km"],
        "most_recent_ts": newest["acq_ts"], "detections": nearby[:20],
    }


@provider("event")
def _event(ctx: SituationContext) -> dict[str, Any] | None:
    """Which clustered fire event, if any, this point falls inside."""
    rows = ctx.db.conn.execute(
        "SELECT * FROM events WHERE ? BETWEEN bbox_south AND bbox_north "
        "AND ? BETWEEN bbox_west AND bbox_east ORDER BY last_seen DESC LIMIT 5",
        (ctx.lat, ctx.lon)).fetchall()
    if not rows:
        return {"label": "Fire event", "summary": "not inside a clustered event"}
    row = rows[0]
    return {
        "label": "Fire event",
        "summary": (f"event {row['id']}: {row['detection_count']} detections, "
                    f"last seen {_age_text(max(0.0, ctx.now - row['last_seen']))}"),
        "event_id": row["id"], "detection_count": row["detection_count"],
        "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "bbox": [row["bbox_west"], row["bbox_south"], row["bbox_east"], row["bbox_north"]],
    }


@provider("incidents")
def _incidents(ctx: SituationContext) -> dict[str, Any] | None:
    """Which incidents' areas of interest contain this point."""
    covering = ctx.app.state.operations.incidents_covering(ctx.lat, ctx.lon, active_only=False)
    if not covering:
        return {"label": "Incidents", "summary": "not inside any incident's area of interest"}
    return {"label": "Incidents",
            "summary": ", ".join(f"{item['name']} ({item['status']})" for item in covering),
            "incidents": covering}


@provider("modelled_arrival")
def _modelled_arrival(ctx: SituationContext) -> dict[str, Any] | None:
    """When the fire is modelled to reach this point.

    Reported as an **earliest/median/latest band** rather than one number.
    A single figure invites an operator to treat a model as a timetable; the
    band is what the ensemble actually produces and what a withdrawal decision
    should be made against. The summary text says "modelled" for the same
    reason.

    Sampled from the most recent propagation/ensemble job whose result
    directory still holds an `impact_surface.npz` - the same file
    `structures.py` samples for structures-at-risk, via the same helper.
    """
    import numpy as np

    from .structures import _sample_hours

    settings = ctx.app.state.settings
    rows = ctx.db.conn.execute(
        "SELECT id,kind,result_json,finished_at FROM jobs "
        "WHERE status='done' AND kind IN ('propagation','ensemble') "
        "ORDER BY finished_at DESC LIMIT 10").fetchall()
    for row in rows:
        surface_path = settings.job_dir / str(row["id"]) / "impact_surface.npz"
        if not surface_path.is_file():
            continue
        try:
            result = json.loads(row["result_json"] or "{}")
            bounds = result.get("bounds")
            if not bounds:
                continue
            with np.load(surface_path) as data:
                bands = {}
                for key, label in (("earliest_hours", "earliest"), ("median_hours", "median"),
                                   ("latest_hours", "latest")):
                    if key in data:
                        hours = _sample_hours(data[key], {"lat": ctx.lat, "lon": ctx.lon}, bounds)
                        if hours is not None:
                            bands[label] = round(float(hours), 2)
        except Exception as exc:  # noqa: BLE001 - a pruned/corrupt result is not an error here
            log.debug("Arrival sample failed for job %s: %s", row["id"], exc)
            continue
        if not bands:
            # The point is outside this run's grid, or on a cell the fire never
            # reaches. Both are real answers and worth saying, since "no arrival
            # time" reads as reassurance otherwise.
            return {"label": "Modelled arrival",
                    "summary": f"not reached by model run {row['id']} (or outside its grid)",
                    "job_id": row["id"]}
        median = bands.get("median", bands.get("earliest"))
        span = (f" (earliest {bands['earliest']:g} h, latest {bands['latest']:g} h)"
                if "earliest" in bands and "latest" in bands and bands["earliest"] != bands["latest"] else "")
        return {
            "label": "Modelled arrival",
            "summary": f"modelled to arrive in ~{median:g} h{span} - job {row['id']}, not an observation",
            "job_id": row["id"], "kind": row["kind"], "hours": bands,
        }
    return {"label": "Modelled arrival", "summary": "no completed propagation run available"}


@provider("structures")
def _structures(ctx: SituationContext) -> dict[str, Any] | None:
    """Buildings and other structures near the point."""
    from .structures import _category

    west, south, east, north = ctx.bbox()
    # The table stores raw OSM rows: latitude/longitude, and tags rather than a
    # category column - the category is derived by `structures._category`, which
    # is reused here so a "school" means the same thing in this panel as it does
    # in the structures-at-risk analysis.
    rows = ctx.db.conn.execute(
        "SELECT latitude,longitude,tags_json FROM structures "
        "WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? LIMIT 2000",
        (south, north, west, east)).fetchall()
    nearby = [row for row in rows
              if haversine_km(ctx.lat, ctx.lon, row["latitude"], row["longitude"]) <= ctx.radius_km]
    if not nearby:
        # "None scanned" rather than "none present": structures only exist here
        # once someone has run a scan over this area, and claiming an unscanned
        # area is empty would be a dangerous reading.
        return {"label": "Structures", "summary": f"none scanned within {ctx.radius_km:g} km"}
    categories: dict[str, int] = {}
    for row in nearby:
        try:
            name = _category(json.loads(row["tags_json"] or "{}"))
        except (TypeError, ValueError):
            name = "other"
        categories[name] = categories.get(name, 0) + 1
    return {"label": "Structures",
            "summary": f"{len(nearby)} within {ctx.radius_km:g} km ("
                       + ", ".join(f"{count} {name}" for name, count in sorted(categories.items())) + ")",
            "count": len(nearby), "categories": categories}


@provider("alerts")
def _alerts(ctx: SituationContext) -> dict[str, Any] | None:
    """Official CAP warnings in force at this point."""
    manager = getattr(ctx.app.state, "alerts", None)
    if manager is None or not manager.enabled:
        return None
    west, south, east, north = ctx.bbox()
    payload = manager.query((west, south, east, north))
    if not payload["features"]:
        return {"label": "Official warnings", "summary": "none in force here"}
    events = [f["properties"].get("event") or f["properties"].get("headline")
              for f in payload["features"][:3]]
    return {"label": "Official warnings",
            "summary": f"{len(payload['features'])} in force: " + ", ".join(str(e) for e in events),
            "count": len(payload["features"])}


@provider("units")
def _units(ctx: SituationContext) -> dict[str, Any] | None:
    """The nearest tracked vehicle or crew, across every active incident."""
    rows = ctx.db.conn.execute(
        "SELECT callsign,latitude,longitude,observed_at,observed_epoch FROM ("
        "  SELECT r.*, ROW_NUMBER() OVER (PARTITION BY source_id,callsign "
        "    ORDER BY observed_epoch DESC) AS rn FROM vehicle_position_reports r"
        ") WHERE rn=1").fetchall()
    if not rows:
        return {"label": "Units", "summary": "no tracked positions"}
    ranked = sorted(
        ({"callsign": row["callsign"],
          "distance_km": round(haversine_km(ctx.lat, ctx.lon, row["latitude"], row["longitude"]), 2),
          "age_seconds": max(0.0, ctx.now - row["observed_epoch"])} for row in rows),
        key=lambda item: item["distance_km"])
    nearest = ranked[0]
    return {"label": "Units",
            "summary": (f"nearest {nearest['callsign']} at {nearest['distance_km']:g} km "
                        f"({_age_text(nearest['age_seconds'])})"),
            "units": ranked[:5]}


@provider("next_overpass")
def _next_overpass(ctx: SituationContext) -> dict[str, Any] | None:
    """When a satellite will next look at this point.

    Answers the verification question - "when will we know if this changed?" -
    which is otherwise invisible to an operator despite being computed.
    """
    from datetime import datetime, timedelta, timezone

    from .routes import common

    if common.orbits is None:
        return None
    orbits = common.orbits

    # Propagate each tracked satellite's ground track forward and find the
    # first sample whose subsatellite point comes within half a swath of this
    # location. Built from `ground_track` rather than a dedicated forecast
    # function because none exists - and a wrapper that looked like one while
    # silently failing would be worse than none, which is exactly what an
    # earlier draft of this provider did.
    #
    # Today and tomorrow only: two days of samples is plenty to catch the next
    # pass of a polar orbiter (each revisits within hours) without propagating
    # a TLE far enough for its accuracy to matter.
    today = datetime.now(timezone.utc).date()
    soonest: dict[str, Any] | None = None
    for name, meta in orbits.SATELLITES.items():
        tle = orbits.ensure_tle(ctx.db.conn, name)
        if tle is None:
            continue
        for day in (today, today + timedelta(days=1)):
            try:
                track = orbits.ground_track(tle[0], tle[1], name, day)
            except Exception:  # noqa: BLE001 - one unusable TLE must not lose the rest
                continue
            for timestamp, lat, lon in track:
                if timestamp <= ctx.now:
                    continue
                if orbits.haversine_km(ctx.lat, ctx.lon, lat, lon) <= meta["half_width_km"]:
                    if soonest is None or timestamp < soonest["start_ts"]:
                        soonest = {"satellite": name, "sensor": meta["sensor"], "start_ts": timestamp}
                    break
            if soonest is not None and soonest["satellite"] == name:
                break

    if soonest is None:
        return {"label": "Next overpass", "summary": "none predicted in the next 48 h"}
    minutes = max(0.0, (soonest["start_ts"] - ctx.now) / 60)
    when = "under a minute" if minutes < 1 else (
        f"~{minutes:.0f} min" if minutes < 90 else f"~{minutes / 60:.1f} h")
    return {"label": "Next overpass",
            "summary": f"{soonest['satellite']} ({soonest['sensor']}) in {when}",
            **soonest}


# ---------------------------------------------------------------- assembly

def report(app: Any, lat: float, lon: float, radius_km: float = DEFAULT_RADIUS_KM) -> dict[str, Any]:
    """Run every provider and assemble the answer.

    A provider that raises is caught, logged at debug, and named in
    `unavailable` - never allowed to fail the response. During an incident this
    endpoint has to answer with whatever is knowable, and say plainly what it
    could not reach.
    """
    ctx = SituationContext(lat=lat, lon=lon,
                           radius_km=max(0.1, min(MAX_RADIUS_KM, float(radius_km))), app=app)
    sections: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for name, function in _PROVIDERS.items():
        try:
            section = function(ctx)
        except Exception as exc:  # noqa: BLE001 - see docstring
            log.debug("Situation provider %r failed: %s", name, exc)
            unavailable.append(name)
            continue
        if section is None:
            # A deliberate "this feature is not configured here" rather than a
            # failure - reported all the same, so the reader knows it was not
            # consulted.
            unavailable.append(name)
            continue
        sections.append({"provider": name, **section})
    return {"lat": lat, "lon": lon, "radius_km": ctx.radius_km,
            "sections": sections, "unavailable": unavailable,
            "generated_at": int(ctx.now)}


__all__ = ["DEFAULT_RADIUS_KM", "MAX_RADIUS_KM", "SituationContext", "provider", "report"]
