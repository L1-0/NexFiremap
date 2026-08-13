"""Offline operational workspace tests. No network or FIRMS key required."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.operations import OperationsError, OperationsStore, RevisionConflict, SAFETY_CHECKS, default_period


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "operations.sqlite3")
        store = OperationsStore(db)
        incident = store.create_incident({"name": "Pine Ridge", "center_lat": 48.1, "center_lon": 11.5}, "IC-1")
        period = store.create_period(incident["id"], default_period(), "IC-1")
        scenario = store.create_scenario(incident["id"], period["id"], {"name": "Plan A", "kind": "primary"}, "IC-1")
        feature = store.create_feature(incident["id"], {
            "period_id": period["id"], "scenario_id": scenario["id"], "feature_type": "tactical_line",
            "title": "North machine line", "status": "proposed",
            "geometry": {"type": "LineString", "coordinates": [[11.5, 48.1], [11.6, 48.2]]},
            "properties": {"method": "machine_line", "priority": "high"},
        }, "DIV-N")
        assert feature["properties"]["revision"] == 1
        assert store.list_features(incident["id"], period["id"], scenario["id"])[0]["id"] == feature["id"]
        updated = store.update_feature(incident["id"], feature["id"], {"status": "under_construction"}, 1, "DIV-N")
        assert updated["properties"]["revision"] == 2
        updated = store.update_feature(incident["id"], feature["id"], {
            "valid_from": "2026-08-12T20:00:00+00:00", "valid_to": "2026-08-13T02:00:00+00:00",
            "properties": {"objective": "Hold the north flank", "responsible_unit": "Division North",
                           "assigned_resources": "Dozer 1, Engine 4", "priority": "high",
                           "required_equipment": "Dozer, lookout", "water_requirement": "Engine relay",
                           "prerequisites": "Evacuation confirmed", "hazards": "Rolling material",
                           "escape_route": "Quarry road", "safety_zone": "Quarry",
                           "communications_channel": "TAC 3", "notes": "Review at 22:00"},
        }, 2, "DIV-N")
        assert updated["properties"]["revision"] == 3
        assert updated["properties"]["objective"] == "Hold the north flank"
        assert updated["properties"]["communications_channel"] == "TAC 3"
        try:
            store.update_feature(incident["id"], feature["id"], {"status": "completed"}, 1, "stale")
            raise AssertionError("stale revision accepted")
        except RevisionConflict:
            pass
        try:
            store.create_feature(incident["id"], {"period_id": period["id"], "feature_type": "spot_fire",
                "status": "confirmed", "geometry": {"type": "LineString", "coordinates": [[11, 48], [12, 49]]}})
            raise AssertionError("wrong geometry accepted")
        except OperationsError:
            pass
        try:
            store.approve_scenario(incident["id"], scenario["id"], "IC-1")
            raise AssertionError("unreviewed plan approved")
        except OperationsError:
            pass
        store.set_safety_checks(incident["id"], period["id"], scenario["id"],
                                [{"key": key, "checked": True} for key, _ in SAFETY_CHECKS], "SO-1")
        approved = store.approve_scenario(incident["id"], scenario["id"], "IC-1")
        assert approved["status"] == "approved" and approved["safety_warnings"] == []
        incident = store.update_incident(incident["id"], {"notes": "Night shift", "status": "contained"}, 1, "IC-1")
        assert incident["revision"] == 2 and incident["status"] == "contained"
        period = store.update_period(incident["id"], period["id"], {"objectives": "Hold north flank", "status": "active"}, 1, "PLANS-1")
        assert period["revision"] == 2 and period["status"] == "active"
        try:
            store.update_period(incident["id"], period["id"], {"objectives": "stale"}, 1, "stale")
            raise AssertionError("stale period revision accepted")
        except RevisionConflict:
            pass
        scenario = store.update_scenario(incident["id"], scenario["id"], {"assumptions": "Wind backs after midnight"}, approved["revision"], "FBAN-1")
        assert scenario["revision"] == approved["revision"] + 1 and scenario["status"] == "draft"
        assert scenario["approved_by"] is None and scenario["approved_at"] is None
        try:
            store.update_scenario(incident["id"], scenario["id"], {"assumptions": "stale"}, approved["revision"], "stale")
            raise AssertionError("stale scenario revision accepted")
        except RevisionConflict:
            pass
        try:
            store.update_incident(incident["id"], {"notes": "stale overwrite"}, 1, "stale")
            raise AssertionError("stale incident revision accepted")
        except RevisionConflict:
            pass
        resource = store.create_resource(incident["id"], {"callsign": "Engine 4", "unit_type": "engine", "crew_size": 4})
        resource = store.update_resource(incident["id"], resource["id"], {
            "status": "working", "assignment": "North flank", "water_capacity_l": 2500,
        }, 1, "DIV-N")
        assert resource["revision"] == 2 and resource["status"] == "working"
        try:
            store.update_resource(incident["id"], resource["id"], {"status": "returning"}, 1, "stale")
            raise AssertionError("stale resource revision accepted")
        except RevisionConflict:
            pass
        bundle = store.export_bundle(incident["id"])
        assert bundle["schema"] == "nexfiremap-incident/1" and len(bundle["features"]["features"]) == 1
        assert len(bundle["audit_log"]) >= 7
        assert any(item["entity_type"] == "resource" and item["action"] == "update" and item["actor"] == "DIV-N"
                   for item in bundle["audit_log"])
        snapshot = store.create_snapshot(incident["id"], "Handover 1", period["id"], "operational", "IC-1")
        assert snapshot["name"] == "Handover 1"
        unchanged = store.compare_snapshots(incident["id"], snapshot["id"])
        assert unchanged["counts"]["added"] == 0 and unchanged["counts"]["changed"] == 0
        deleted = store.delete_feature(incident["id"], feature["id"], 3, "DIV-N")
        assert deleted["properties"]["deleted_at"] is not None
        comparison = store.compare_snapshots(incident["id"], snapshot["id"])
        assert comparison["counts"]["changed"] >= 1
        assert any(item["entity_type"] == "feature" and item["id"] == feature["id"] and
                   item["classification"] == "changed" for item in comparison["changes"])
        snapshot2 = store.create_snapshot(incident["id"], "Handover 2", period["id"], "operational", "IC-1")
        between = store.compare_snapshots(incident["id"], snapshot["id"], snapshot2["id"])
        assert between["right_is_current"] is False
        assert any(item["entity_type"] == "feature" for item in between["changes"])
        other_incident = store.create_incident({"name": "Other incident"}, "test")
        other_snapshot = store.create_snapshot(other_incident["id"], "Other", None, "operational", "test")
        try:
            store.compare_snapshots(incident["id"], snapshot["id"], other_snapshot["id"])
            raise AssertionError("cross-incident snapshot comparison accepted")
        except OperationsError:
            pass
        assert store.list_features(incident["id"]) == []
        assert len(store.list_features(incident["id"], include_deleted=True)) == 1
        db.close()
    print("Operational workspace checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
