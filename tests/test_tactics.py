"""Tactical measurements, warnings, calculators and period-copy checks."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.operations import OperationsStore, default_period
from nexfiremap.tactics import TacticsManager, measure_geometry


def main() -> None:
    line = measure_geometry({"type": "LineString", "coordinates": [[11, 48], [11.01, 48]]})
    assert 700 < line["length_m"] < 800
    area = measure_geometry({"type": "Polygon", "coordinates": [[[11, 48], [11.01, 48], [11.01, 48.01], [11, 48]]]})
    assert area["area_m2"] > 100_000 and area["length_m"] > 2_000
    assert TacticsManager.calculate("travel_time", {"distance_km": 30, "speed_kmh": 60})["output"]["minutes"] == 30
    assert TacticsManager.calculate("hose_lay", {"distance_m": 105, "section_length_m": 20, "reserve_percent": 10})["output"]["sections"] == 6

    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "tactics.sqlite3")
        try:
            store = OperationsStore(db); manager = TacticsManager(store)
            incident = store.create_incident({"name": "Tactical test"})
            p1 = store.create_period(incident["id"], default_period())
            next_data = default_period(); next_data["name"] = "Next period"
            next_data["starts_at"] = p1["ends_at"]; next_data["ends_at"] = "2026-08-20T12:00:00+00:00"
            p2 = store.create_period(incident["id"], next_data)
            scenario = store.create_scenario(incident["id"], p1["id"], {"name": "Plan A"})
            resource = store.create_resource(incident["id"], {"callsign": "E1", "unit_type": "engine"})
            safety = store.create_feature(incident["id"], {"period_id": p1["id"], "scenario_id": scenario["id"],
                "feature_type": "safety_zone", "title": "SZ", "status": "planned",
                "geometry": {"type": "Point", "coordinates": [11.5, 48.1]}})
            route = store.create_feature(incident["id"], {"period_id": p1["id"], "scenario_id": scenario["id"],
                "feature_type": "escape_route", "title": "Route", "status": "planned",
                "geometry": {"type": "LineString", "coordinates": [[11.4, 48.1], [11.6, 48.1]]},
                "properties": {"links": {"safety_zone": safety["properties"]["id"]},
                               "assigned_resource_ids": [resource["id"]]}})
            store.create_feature(incident["id"], {"period_id": p1["id"], "scenario_id": scenario["id"],
                "feature_type": "tactical_line", "title": "Line", "status": "planned",
                "geometry": {"type": "LineString", "coordinates": [[11.4, 48.0], [11.6, 48.0]]},
                "properties": {"assigned_resource_ids": [resource["id"]]}})
            store.create_feature(incident["id"], {"period_id": p1["id"], "scenario_id": scenario["id"],
                "feature_type": "forecast_perimeter", "title": "Forecast", "status": "planned",
                "geometry": {"type": "Polygon", "coordinates": [[[11.49, 48.05], [11.51, 48.05],
                    [11.51, 48.15], [11.49, 48.15], [11.49, 48.05]]]}})
            assessment = manager.assessment(incident["id"], p1["id"], scenario["id"])
            codes = {warning["code"] for warning in assessment["warnings"]}
            assert "double_assignment" in codes and "escape_route_forecast_crossing" in codes
            assert len(assessment["measurements"]) == 4
            warning = assessment["warnings"][0]
            acknowledgement = manager.acknowledge(
                incident["id"], p1["id"], scenario["id"], warning["warning_id"],
                "Reviewed by Safety; alternate route briefed", "SOFR",
            )
            assert acknowledgement["acknowledged_by"] == "SOFR"
            reviewed = manager.assessment(incident["id"], p1["id"], scenario["id"])
            assert reviewed["warning_count"] == reviewed["total_warning_count"] - 1
            assert next(item for item in reviewed["warnings"] if item["warning_id"] == warning["warning_id"])["acknowledgement"]

            copied = store.copy_scenario(incident["id"], scenario["id"], p2["id"], "Plan A carry-forward", "PLANS")
            assert len(copied["feature_ids"]) == 4 and copied["scenario"]["period_id"] == p2["id"]
            assert len(store.list_features(incident["id"], p1["id"], scenario["id"])) == 4
            copied_features = store.list_features(incident["id"], p2["id"], copied["scenario"]["id"])
            assert len(copied_features) == 4
            copied_route = next(item for item in copied_features if item["properties"]["feature_type"] == "escape_route")
            assert copied_route["properties"]["links"]["safety_zone"] in copied["feature_ids"]
            assert copied_route["properties"]["copied_from_feature_id"] == route["properties"]["id"]
        finally:
            db.close()
    print("Tactical planning checks passed.")


if __name__ == "__main__":
    main()
