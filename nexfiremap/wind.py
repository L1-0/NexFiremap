"""Offline incident wind-field derivation from retained observations/provenance."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from .operations import NotFoundError, OperationsStore


class WindError(ValueError):
    """Raised for bad wind input/params (unparsable time, out-of-range bbox or
    grid, ...) - a ``ValueError`` subclass so callers that already catch
    ``ValueError`` around this module keep working without special-casing."""
    pass


# 16-point compass rose, in degrees clockwise from true north - the standard
# METAR/synoptic textual convention observers actually write down.
CARDINAL = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5, "E": 90.0,
    "ESE": 112.5, "SE": 135.0, "SSE": 157.5, "S": 180.0,
    "SSW": 202.5, "SW": 225.0, "WSW": 247.5, "W": 270.0,
    "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}


# ---- time/unit/direction parsing ----


def _time(value: str | None, *, default: float | None = None) -> tuple[str, float]:
    """Parse a caller-supplied ISO 8601 timestamp into ``(normalized_iso,
    epoch_seconds)``, both in UTC. With no value given, returns "now" (or
    ``default`` if provided) instead of raising - callers use this both to
    validate a required field and to fill in an optional one. A timezone is
    required on any explicit value: an incident's tactical features may be
    entered by observers in different local time zones, so a naive timestamp
    would be ambiguous rather than just "assume UTC"."""
    if not value:
        epoch = datetime.now(timezone.utc).timestamp() if default is None else default
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds"), epoch
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise WindError("wind-map time must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise WindError("wind-map time must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="seconds"), parsed.timestamp()


def _speed(value: Any) -> tuple[float | None, str | None]:
    """Parse a wind speed into m/s, converting from km/h or knots if the
    value carries an explicit unit suffix. A bare number (numeric or
    unit-less text) is assumed to already be m/s, since that's this module's
    internal/API convention - the returned ``assumption`` string records
    which case applied so callers can surface it (see
    ``unit_interpretation`` in ``_observations``) rather than silently
    guessing wrong on a misentered field. Returns ``(None, None)`` for
    anything unparsable."""
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value), "numeric_assumed_m_s"
    raw = str(value or "").strip().lower().replace(",", ".")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(m/s|ms-1|km/h|kmh|kt|kts|knot|knots)?", raw)
    if not match:
        return None, None
    number, unit = float(match.group(1)), match.group(2)
    if unit in {"km/h", "kmh"}: number /= 3.6  # km/h -> m/s
    elif unit in {"kt", "kts", "knot", "knots"}: number *= 0.514444  # kt -> m/s
    return number, "explicit_unit" if unit else "text_assumed_m_s"


def _direction(value: Any) -> float | None:
    """Parse a wind-FROM direction into degrees clockwise from true north,
    normalized to [0, 360). Accepts a raw number, a 16-point compass label
    (``CARDINAL``), or a numeric string with an optional "deg"/"°" suffix.
    Returns ``None`` if none of those match."""
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value) % 360
    raw = str(value or "").strip().upper().replace("°", "").replace("DEG", "").strip()
    if raw in CARDINAL:
        return CARDINAL[raw]
    try:
        number = float(raw)
        return number % 360 if math.isfinite(number) else None
    except ValueError:
        return None


# ---- vector math ----


def components(speed_ms: float, wind_from_deg: float) -> tuple[float, float]:
    """Meteorological FROM bearing to east/north motion components."""
    radians = math.radians(wind_from_deg)
    # Meteorological convention gives the direction wind blows FROM, but a
    # motion vector needs where the air is actually moving TO - hence the
    # negation on both components (wind from the north moves air southward).
    return -speed_ms * math.sin(radians), -speed_ms * math.cos(radians)


def meteorological(u_east_ms: float, v_north_ms: float) -> tuple[float, float]:
    """Inverse of ``components``: east/north motion vector -> (speed,
    wind-FROM bearing in degrees). Returns ``(0.0, 0.0)`` for a
    near-zero vector rather than an undefined/noisy ``atan2`` direction."""
    speed = math.hypot(u_east_ms, v_north_ms)
    if speed < 1e-12:
        return 0.0, 0.0
    return speed, math.degrees(math.atan2(-u_east_ms, -v_north_ms)) % 360


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km via the haversine formula (Earth mean
    radius 6371.0088 km) - close enough for weighting nearby observations at
    incident scale, no need for an ellipsoidal geodesic here."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    term = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.atan2(math.sqrt(term), math.sqrt(max(0, 1 - term)))


def interpolate_vectors(samples: list[dict[str, Any]], lat: float, lon: float,
                        target_epoch: float, half_life_hours: float,
                        background: tuple[float, float] | None = None) -> dict[str, Any]:
    """Estimate the wind vector at one (lat, lon, time) point from nearby
    observations, an optional AOI-wide model ``background`` vector, or both.

    With no observations, the point simply gets the background vector
    verbatim (``support_count`` 0 signals that to callers). With
    observations, this is inverse-distance-weighted (IDW) in space and
    exponentially time-decayed (half-life ``half_life_hours``) - the ``+
    0.01`` in the spatial weight keeps a sample essentially on top of the
    query point from producing a division blow-up, and the small floor on
    ``half_life_hours`` guards the same for a caller-supplied window near
    zero. When a background is present, observations are blended as
    *residuals* from it (``u - background[0]``, etc.) and the background is
    added back in afterward - this lets a handful of local observations
    nudge a broader model field rather than have to fully override it once
    they're distant/old and their weight decays away.
    """
    if not samples and background is None:
        raise WindError("no wind input is available")
    if not samples:
        speed, direction = meteorological(*background)
        return {"u_east_ms": background[0], "v_north_ms": background[1], "speed_ms": speed,
                "wind_from_deg": direction, "support_count": 0, "nearest_support_km": None,
                "vector_disagreement_ms": None, "estimated": True}
    weighted: list[tuple[float, float, float, float]] = []
    for sample in samples:
        distance = _distance_km(lat, lon, sample["latitude"], sample["longitude"])
        age_hours = max(0, target_epoch - sample["observed_epoch"]) / 3600
        weight = math.exp(-math.log(2) * age_hours / max(0.01, half_life_hours)) / (distance * distance + 0.01)
        u, v = sample["u_east_ms"], sample["v_north_ms"]
        if background is not None:
            u, v = u - background[0], v - background[1]
        weighted.append((weight, u, v, distance))
    total = sum(row[0] for row in weighted)
    mean_u = sum(w * u for w, u, _v, _d in weighted) / total
    mean_v = sum(w * v for w, _u, v, _d in weighted) / total
    # Weighted spread of the vector components around their mean - a rough
    # "how much do nearby observations disagree" signal surfaced to callers
    # as vector_disagreement_ms, not a formal confidence interval.
    disagreement = math.sqrt(sum(w * ((u - mean_u) ** 2 + (v - mean_v) ** 2)
                                     for w, u, v, _d in weighted) / total)
    if background is not None:
        mean_u += background[0]; mean_v += background[1]
    speed, direction = meteorological(mean_u, mean_v)
    return {"u_east_ms": mean_u, "v_north_ms": mean_v, "speed_ms": speed,
            "wind_from_deg": direction, "support_count": len(samples),
            "nearest_support_km": min(row[3] for row in weighted),
            "vector_disagreement_ms": disagreement, "estimated": True}


class WindManager:
    """Derives an incident's wind field entirely from data already retained
    for it - tactical wind observations/weather-station features and any
    attached model run's recorded weather provenance - never a live external
    forecast fetch. See ``field()`` for the entry point and the module
    docstring for the overall offline-derivation intent."""

    def __init__(self, store: OperationsStore) -> None:
        self.store, self.db = store, store.db

    # ---- data loading ----

    def _observations(self, incident_id: str, target_epoch: float, window_hours: float) -> tuple[list[dict[str, Any]], int, int]:
        """Load and validate wind observations for an incident within
        ``window_hours`` before ``target_epoch``, returning
        ``(accepted, rejected_count, omitted_count)``. Rejects rows with an
        unparsable time, malformed geometry, or a speed/direction that fails
        to parse or falls outside a plausible 0-150 m/s range - bad input
        from a hand-entered tactical feature shouldn't corrupt the whole
        field, so it's dropped and counted rather than raising. Sorted
        newest-first and capped at 500 rows as a processing bound; anything
        past that is reported via ``omitted_count`` rather than silently
        dropped."""
        rows = self.db.conn.execute(
            "SELECT * FROM tactical_features WHERE incident_id=? AND feature_type IN ('wind_observation','weather_station') "
            "AND deleted_at IS NULL ORDER BY observed_at,id", (incident_id,)
        ).fetchall()
        accepted: list[dict[str, Any]] = []; rejected = 0
        for raw in rows:
            row = dict(raw)
            try:
                _iso, epoch = _time(row.get("observed_at"))
            except WindError:
                rejected += 1; continue
            if epoch > target_epoch or epoch < target_epoch - window_hours * 3600:
                continue
            try:
                geometry = json.loads(row["geometry_json"]); properties = json.loads(row["properties_json"] or "{}")
                lon, lat = map(float, geometry["coordinates"][:2])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                rejected += 1; continue
            speed, assumption = _speed(properties.get("wind_speed_ms", properties.get("wind_speed")))
            direction = _direction(properties.get("wind_from_deg", properties.get("wind_direction")))
            if speed is None or direction is None or not 0 <= speed <= 150:
                rejected += 1; continue
            gust, _gust_assumption = _speed(properties.get("wind_gust_ms"))
            height = properties.get("wind_height_m")
            try: height = float(height) if height not in (None, "") else None
            except (TypeError, ValueError): height = None
            u, v = components(speed, direction)
            accepted.append({"id": row["id"], "title": row["title"], "latitude": lat, "longitude": lon,
                             "observed_at": row["observed_at"], "observed_epoch": epoch,
                             "speed_ms": speed, "wind_from_deg": direction, "gust_ms": gust,
                             "measurement_height_m": height, "u_east_ms": u, "v_north_ms": v,
                             "status": row["status"], "confidence": row["confidence"], "source": row["source"],
                             "observer": row["observer"], "unit_interpretation": assumption})
        accepted.sort(key=lambda item: (item["observed_epoch"], item["id"]), reverse=True)
        omitted = max(0, len(accepted) - 500)
        return accepted[:500], rejected, omitted

    def _background(self, incident_id: str, target_epoch: float, scenario_id: str | None) -> dict[str, Any] | None:
        """Find the most recent attached model run (optionally restricted to
        ``scenario_id``) whose recorded weather provenance is valid at or
        before ``target_epoch``, and return its wind as a background vector
        for ``field()`` to blend local observations against. A run whose
        ``reference_at`` is in the future relative to ``target_epoch`` is
        excluded outright (not just penalized) - it can't describe the
        requested moment. Among eligible runs, the most recent one wins.
        Returns ``None`` if no attached run has usable weather provenance."""
        sql = "SELECT * FROM incident_model_runs WHERE incident_id=?"
        params: list[Any] = [incident_id]
        if scenario_id:
            sql += " AND scenario_id=?"; params.append(scenario_id)
        candidates = []
        for row in self.db.conn.execute(sql, params).fetchall():
            try:
                provenance = json.loads(row["provenance_json"])
                weather = provenance["weather_summary"]
                reference_iso, reference_epoch = _time(provenance.get("reference_at"))
                speed, _ = _speed(weather.get("wind_speed_ms")); direction = _direction(weather.get("wind_direction_deg"))
                if reference_epoch <= target_epoch and speed is not None and direction is not None:
                    candidates.append((reference_epoch, row, provenance, speed, direction, reference_iso))
            except (KeyError, TypeError, json.JSONDecodeError, WindError):
                continue
        if not candidates:
            return None
        reference_epoch, row, provenance, speed, direction, reference_iso = max(candidates, key=lambda item: item[0])
        u, v = components(speed, direction)
        return {"model_run_id": row["id"], "scenario_id": row["scenario_id"], "reference_at": reference_iso,
                "reference_epoch": reference_epoch, "valid_until": provenance.get("valid_until"),
                "is_stale_at_request": bool(provenance.get("valid_until") and _time(provenance["valid_until"])[1] < target_epoch),
                "speed_ms": speed, "wind_from_deg": direction, "u_east_ms": u, "v_north_ms": v,
                "sources": provenance.get("sources", []), "warnings": provenance.get("warnings", []),
                "limitations": provenance.get("limitations", "")}

    # ---- public entry point ----

    def field(self, incident_id: str, bbox: tuple[float, float, float, float], *, at: str | None,
              window_hours: float, grid: int, scenario_id: str | None = None) -> dict[str, Any]:
        """Build a gridded wind-vector field over ``bbox`` at time ``at``
        (default now) for the map to render, plus the raw observation points
        and background model vector it was derived from. ``grid`` is the
        number of cells per side (bounded 2-30) and ``window_hours`` how far
        back observations may be drawn from (bounded 0.25-72h) - both capped
        to keep a single request's interpolation work and result size
        bounded regardless of what a client asks for. Picks a `method` label
        from whichever inputs are actually available (observations, model
        background, or both) so the response is honest about how each vector
        was derived rather than presenting every case identically."""
        incident = self.store.get_incident(incident_id)
        west, south, east, north = bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise WindError("wind bbox is invalid")
        if not 2 <= grid <= 30 or not .25 <= window_hours <= 72:
            raise WindError("wind grid/window is outside safe bounds")
        target_iso, target_epoch = _time(at)
        observations, rejected, omitted = self._observations(incident_id, target_epoch, window_hours)
        background = self._background(incident_id, target_epoch, scenario_id)
        if not observations and background is None:
            return {"schema": "nexfiremap-wind-field/1", "incident_id": incident_id, "at": target_iso,
                    "window_hours": window_hours, "method": "unavailable", "vectors": {"type": "FeatureCollection", "features": []},
                    "observations": {"type": "FeatureCollection", "features": []}, "background": None,
                    "warnings": ["No structured wind observation or eligible attached model weather is available."],
                    "limitations": "No wind field was inferred."}
        background_vector = ((background["u_east_ms"], background["v_north_ms"]) if background else None)
        method = ("background_plus_observation_residual_idw" if background and observations else
                  "uniform_model_background" if background else
                  "uniform_single_observation" if len(observations) == 1 else "observation_idw")
        # Half the observation window: recent-enough samples still carry
        # meaningful weight by the time they reach the edge of the window
        # rather than being decayed to near-zero right as they'd be excluded.
        half_life = max(.25, window_hours / 2)
        features = []
        for row_index in range(grid):
            lat = north - (row_index + .5) * (north - south) / grid
            for column_index in range(grid):
                lon = west + (column_index + .5) * (east - west) / grid
                vector = interpolate_vectors(observations, lat, lon, target_epoch, half_life, background_vector)
                properties = {**vector, "wind_from_deg": round(vector["wind_from_deg"], 1),
                              "wind_to_deg": round((vector["wind_from_deg"] + 180) % 360, 1),
                              "speed_ms": round(vector["speed_ms"], 2),
                              "u_east_ms": round(vector["u_east_ms"], 3), "v_north_ms": round(vector["v_north_ms"], 3),
                              "nearest_support_km": round(vector["nearest_support_km"], 2) if vector["nearest_support_km"] is not None else None,
                              "vector_disagreement_ms": round(vector["vector_disagreement_ms"], 2) if vector["vector_disagreement_ms"] is not None else None,
                              "method": method, "at": target_iso}
                features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                                 "properties": properties})
        observation_features = [{"type": "Feature", "id": item["id"],
                                 "geometry": {"type": "Point", "coordinates": [item["longitude"], item["latitude"]]},
                                 "properties": {key: value for key, value in item.items()
                                                if key not in {"latitude", "longitude", "u_east_ms", "v_north_ms", "observed_epoch"}}}
                                for item in observations]
        warnings = []
        if rejected: warnings.append(f"{rejected} wind record(s) were ignored because values were ambiguous or invalid")
        if omitted: warnings.append(f"{omitted} older eligible wind record(s) were omitted by the 500-record processing bound")
        if len(observations) == 1: warnings.append("Only one local observation supports this view; the observed field is uniform")
        if background and background["is_stale_at_request"]: warnings.append("Attached model wind was stale at the requested time")
        if not observations: warnings.append("No local measurement supports the displayed model background")
        return {"schema": "nexfiremap-wind-field/1", "incident_id": incident_id, "incident_name": incident["name"],
                "at": target_iso, "window_hours": window_hours, "bbox": list(bbox), "grid": grid, "method": method,
                "vectors": {"type": "FeatureCollection", "features": features},
                "observations": {"type": "FeatureCollection", "features": observation_features},
                "background": background, "warnings": warnings,
                "limitations": "Situational IDW visualization from retained point observations and/or one AOI-wide attached model input; no terrain-flow downscaling or forecast acquisition."}
