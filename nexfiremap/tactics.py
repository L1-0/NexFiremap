"""Explainable tactical measurements, warnings and field calculators.

Three independent pieces, each deliberately simple and inspectable rather
than "smart":

* Geometry measurement (haversine length/spherical polygon area) for any
  drawn feature.
* A deterministic warning scan (`TacticsManager.assessment`) over a period's
  features - broken links, double-assigned resources, escape routes that
  cross a forecast/uncertainty boundary - each warning identified by a
  stable content hash so it survives being re-computed (features/links
  changing elsewhere) without losing an operator's prior acknowledgement of
  the *same* warning.
* Field calculators (`TacticsManager.calculate`) for common fireground math
  (hose lays, production time, water duration, travel time), each returning
  its own formula string alongside the numeric result so the UI can show
  the calculation, not just the answer.
"""

from __future__ import annotations

import math
import hashlib
import json
from typing import Any

from .operations import OperationsError, OperationsStore, _clean_text, _id, utcnow


EARTH_RADIUS_M = 6_371_008.8
LINK_TYPES = {
    "anchor": {"anchor_point"}, "lookout": {"lookout"},
    "escape_route": {"escape_route"}, "safety_zone": {"safety_zone", "safety_zone_area"},
    "trigger": {"trigger_point"},
}


def haversine_m(a: list[float], b: list[float]) -> float:
    """Great-circle distance in meters between two [lon, lat] points.
    ``min(1.0, ...)`` guards ``asin`` against a value fractionally above 1.0
    from floating-point rounding on near-antipodal/identical points."""
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon, dlat = lon2 - lon1, lat2 - lat1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(value)))


def line_length_m(coordinates: list[list[float]]) -> float:
    """Sum of consecutive-vertex great-circle distances - adequate for the
    short, mostly-straight incident-scale lines this measures (fire lines,
    escape routes), not a geodesic-accurate long-haul distance."""
    return sum(haversine_m(a, b) for a, b in zip(coordinates, coordinates[1:]))


def polygon_area_m2(ring: list[list[float]]) -> float:
    # Chamberlain-Duquette spherical area - adequate and deterministic for
    # incident-scale polygons without requiring a projected GIS dependency.
    if len(ring) < 4: return 0.0
    total = 0.0
    for a, b in zip(ring, ring[1:]):
        total += math.radians(b[0] - a[0]) * (2 + math.sin(math.radians(a[1])) + math.sin(math.radians(b[1])))
    return abs(total) * EARTH_RADIUS_M ** 2 / 2.0


def measure_geometry(geometry: dict[str, Any]) -> dict[str, float | str]:
    """Length/area for one GeoJSON geometry, dispatched by type - a Polygon's
    perimeter/area accounts for holes (rings after the first are subtracted,
    per the GeoJSON right-hand-rule convention for interior rings)."""
    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    if kind == "Point": return {"geometry_type": kind, "length_m": 0.0, "area_m2": 0.0}
    if kind == "LineString": return {"geometry_type": kind, "length_m": round(line_length_m(coordinates), 2), "area_m2": 0.0}
    if kind == "Polygon":
        perimeter = sum(line_length_m(ring) for ring in coordinates)
        area = polygon_area_m2(coordinates[0]) - sum(polygon_area_m2(ring) for ring in coordinates[1:])
        return {"geometry_type": kind, "length_m": round(perimeter, 2), "area_m2": round(max(0.0, area), 2)}
    raise OperationsError("unsupported geometry for measurement")


def _segments(coordinates: list[list[float]]):
    """Consecutive (point, next_point) pairs - the edges of a line/ring."""
    yield from zip(coordinates, coordinates[1:])


def _orientation(a, b, c) -> float:
    """Sign of the cross product of (b-a) and (c-a): positive/negative tells
    which side of line a->b the point c falls on, zero means collinear -
    the standard building block for a segment-intersection test."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _intersects(a, b, c, d) -> bool:
    """True if segment a-b crosses segment c-d: c and d must fall on
    opposite sides of line a-b, and vice versa - the standard orientation-
    based segment intersection test (treats touching/collinear as
    intersecting via the <= 0 rather than < 0)."""
    return (_orientation(a, b, c) * _orientation(a, b, d) <= 0 and
            _orientation(c, d, a) * _orientation(c, d, b) <= 0)


def line_crosses_polygon(line: list[list[float]], polygon: list[list[list[float]]]) -> bool:
    """True if any segment of ``line`` crosses any edge of any ring of
    ``polygon`` - a boundary-crossing check, not a point-in-polygon test:
    a line entirely inside or entirely outside the polygon (never crossing
    its boundary) reports False either way, which is what "does this escape
    route cross into/out of the forecast area" actually needs to know."""
    return any(_intersects(a, b, c, d) for a, b in _segments(line)
               for ring in polygon for c, d in _segments(ring))


class TacticsManager:
    def __init__(self, store: OperationsStore) -> None:
        self.store = store

    def assessment(self, incident_id: str, period_id: str, scenario_id: str | None) -> dict[str, Any]:
        """Runs the full deterministic warning scan for one planning period/
        scenario: measures every feature's geometry, validates declared
        links (anchor/lookout/escape_route/safety_zone/trigger) point at a
        feature of the expected type, flags resources assigned to more than
        one tactical object, and flags escape routes that cross a forecast/
        uncertainty boundary. Each warning is content-hashed (see the
        acknowledgement loop below) and matched against any prior
        acknowledgement so a warning an operator already signed off on
        doesn't keep nagging just because the assessment re-ran."""
        features = self.store.list_features(incident_id, period_id, scenario_id)
        feature_map = {item["properties"]["id"]: item for item in features}
        measurements = [{"feature_id": item["properties"]["id"], **measure_geometry(item["geometry"])} for item in features]
        warnings: list[dict[str, Any]] = []

        assignments: dict[str, list[str]] = {}
        for item in features:
            props = item["properties"]
            ids = props.get("assigned_resource_ids", [])
            if isinstance(ids, list):
                for resource_id in ids:
                    assignments.setdefault(str(resource_id), []).append(props["id"])
            links = props.get("links", {})
            if not isinstance(links, dict):
                warnings.append({"code": "invalid_links", "feature_id": props["id"], "message": "Links record is not an object."})
                continue
            for relation, allowed_types in LINK_TYPES.items():
                target_id = links.get(relation)
                if not target_id: continue
                target = feature_map.get(str(target_id))
                if target is None or target["properties"]["feature_type"] not in allowed_types:
                    warnings.append({"code": "invalid_link", "feature_id": props["id"], "relation": relation,
                                     "target_id": target_id, "message": f"{relation} link is missing or has the wrong object type."})
            if props["feature_type"] == "escape_route" and not links.get("safety_zone"):
                warnings.append({"code": "unlinked_safety_zone", "feature_id": props["id"],
                                 "message": "Escape route has no linked safety zone."})

        resource_ids = {item["id"] for item in self.store.list_resources(incident_id)}
        for resource_id, feature_ids in assignments.items():
            if resource_id not in resource_ids:
                warnings.append({"code": "unknown_resource", "resource_id": resource_id,
                                 "feature_ids": feature_ids, "message": "Assignment references an unknown resource."})
            elif len(feature_ids) > 1:
                warnings.append({"code": "double_assignment", "resource_id": resource_id,
                                 "feature_ids": feature_ids, "message": "Resource is assigned to multiple tactical objects."})

        forecasts = [item for item in features if item["properties"]["feature_type"] in {"forecast_perimeter", "uncertainty_area"}]
        routes = [item for item in features if item["properties"]["feature_type"] == "escape_route"]
        for route in routes:
            for forecast in forecasts:
                if line_crosses_polygon(route["geometry"]["coordinates"], forecast["geometry"]["coordinates"]):
                    warnings.append({"code": "escape_route_forecast_crossing", "feature_id": route["properties"]["id"],
                                     "forecast_id": forecast["properties"]["id"],
                                     "message": "Escape route intersects a forecast/uncertainty boundary; review timing and alternatives."})

        acknowledgements = {row["warning_id"]: dict(row) for row in self.store.db.conn.execute(
            "SELECT * FROM tactical_warning_acknowledgements WHERE incident_id=? AND period_id=? AND scenario_id=?",
            (incident_id, period_id, scenario_id or ""),
        ).fetchall()}
        for warning in warnings:
            # Hash of every field except the human-readable "message" (which
            # can be reworded without changing what the warning actually
            # is), sorted keys for a stable encoding - this makes the same
            # underlying issue hash to the same warning_id across repeated
            # assessment() calls, which is what lets an acknowledgement
            # recorded against this id keep matching it later.
            identity = {key: value for key, value in warning.items() if key != "message"}
            warning_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
            warning["warning_id"] = warning_id
            warning["acknowledgement"] = acknowledgements.get(warning_id)

        return {"incident_id": incident_id, "period_id": period_id, "scenario_id": scenario_id,
                "measurements": measurements, "warnings": warnings,
                "warning_count": sum(1 for warning in warnings if not warning["acknowledgement"]),
                "total_warning_count": len(warnings),
                "limitations": "Geometry warnings are deterministic screening checks, not declarations of tactical safety."}

    def acknowledge(self, incident_id: str, period_id: str, scenario_id: str | None,
                    warning_id: str, reason: str, actor: str) -> dict[str, Any]:
        """Records a reasoned sign-off against one warning_id. Requires a
        non-empty reason (an acknowledgement without one isn't useful
        after-the-fact accountability) and re-runs the full assessment
        first to confirm the warning is still actually active - acking a
        warning_id for an issue that's already been fixed/no longer exists
        is rejected rather than silently recorded."""
        reason = _clean_text(reason, 1000)
        if not reason:
            raise OperationsError("a warning acknowledgement requires a reason")
        assessment = self.assessment(incident_id, period_id, scenario_id)
        warning = next((item for item in assessment["warnings"] if item["warning_id"] == warning_id), None)
        if warning is None:
            raise OperationsError("warning is no longer active")
        record_id, now = _id(), utcnow()
        with self.store.db._write_lock:
            self.store.db.conn.execute(
                "INSERT INTO tactical_warning_acknowledgements (id,warning_id,incident_id,period_id,scenario_id,warning_code,reason,acknowledged_by,acknowledged_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(incident_id,period_id,scenario_id,warning_id) DO UPDATE SET reason=excluded.reason,acknowledged_by=excluded.acknowledged_by,acknowledged_at=excluded.acknowledged_at",
                (record_id, warning_id, incident_id, period_id, scenario_id or "", warning["code"],
                 reason, _clean_text(actor, 200), now),
            )
            row = self.store.db.conn.execute(
                "SELECT * FROM tactical_warning_acknowledgements WHERE incident_id=? AND period_id=? AND scenario_id=? AND warning_id=?",
                (incident_id, period_id, scenario_id or "", warning_id),
            ).fetchone()
            self.store._audit(incident_id, "tactical_warning", warning_id, "acknowledge", 1,
                              {"warning": warning, "reason": reason}, actor)
            self.store.db.conn.commit()
        return dict(row)

    @staticmethod
    def calculate(kind: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Runs one named field calculator and returns both the result and
        the formula string used - explainability is the point (an operator
        needs to trust and be able to check the math, not just the number),
        which is why each branch below builds its own formula string rather
        than this being a generic expression evaluator."""

        def number(key: str, minimum: float = 0.0) -> float:
            # minimum defaults to 0.0 (reject negative) but callers pass a
            # small positive epsilon for anything used as a divisor, so a
            # caller-supplied zero rate/speed/section-length can't reach a
            # ZeroDivisionError below - it's rejected here first instead.
            try: value = float(inputs[key])
            except (KeyError, TypeError, ValueError) as exc: raise OperationsError(f"calculator input {key} is required") from exc
            if not math.isfinite(value) or value < minimum: raise OperationsError(f"calculator input {key} is invalid")
            return value

        if kind == "production_time":
            length, rate, resources = number("length_m"), number("rate_m_per_hour", 0.000001), number("resource_count", 0.000001)
            output = {"hours": round(length / (rate * resources), 3)}
            formula = "hours = length_m / (rate_m_per_hour × resource_count)"
        elif kind == "hose_lay":
            distance, section, reserve = number("distance_m"), number("section_length_m", 0.000001), number("reserve_percent")
            output = {"sections": math.ceil(distance * (1 + reserve / 100) / section),
                      "planned_length_m": round(distance * (1 + reserve / 100), 2)}
            formula = "sections = ceil(distance_m × (1 + reserve_percent/100) / section_length_m)"
        elif kind == "water_duration":
            capacity, flow = number("capacity_l"), number("flow_l_per_min", 0.000001)
            output = {"minutes": round(capacity / flow, 2)}
            formula = "minutes = capacity_l / flow_l_per_min"
        elif kind == "travel_time":
            distance, speed = number("distance_km"), number("speed_kmh", 0.000001)
            output = {"minutes": round(distance / speed * 60, 2)}
            formula = "minutes = distance_km / speed_kmh × 60"
        else:
            raise OperationsError("unknown tactical calculator")
        return {"kind": kind, "inputs": inputs, "output": output, "formula": formula,
                "profile": str(inputs.get("profile") or "operator supplied"),
                "assumptions": str(inputs.get("assumptions") or ""),
                "warning": "Planning aid only; validate rates and assumptions against local doctrine and conditions."}
