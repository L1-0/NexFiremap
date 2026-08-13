"""Incident handover preview/import tests; no network required."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.operations import OperationsStore, PackageConflict, default_period


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source_db = Database(root / "source.sqlite3")
        source = OperationsStore(source_db)
        incident = source.create_incident({"name": "Handover Fire", "incident_number": "HF-7"}, "IC-A")
        period = source.create_period(incident["id"], default_period(), "IC-A")
        scenario = source.create_scenario(incident["id"], period["id"], {"name": "Plan A", "kind": "primary"}, "IC-A")
        source.create_feature(incident["id"], {
            "period_id": period["id"], "scenario_id": scenario["id"], "feature_type": "tactical_line",
            "title": "West line", "status": "planned",
            "geometry": {"type": "LineString", "coordinates": [[10, 48], [10.01, 48.01]]},
            "properties": {"objective": "Hold west flank", "responsible_unit": "Division West",
                           "priority": "high", "water_requirement": "2 engines"},
        }, "DIV-W")
        source.create_resource(incident["id"], {"callsign": "Engine 2", "unit_type": "engine"}, "LOG")
        bundle = source.export_bundle(incident["id"])

        target_db = Database(root / "target.sqlite3")
        target = OperationsStore(target_db)
        preview = target.preview_import(bundle)
        assert preview["valid"] and preview["can_apply"] and preview["mode"] == "new_incident"
        imported = target.import_bundle(bundle, "Planning")
        assert imported["imported"] is True
        workspace = target.export_bundle(incident["id"])
        assert len(workspace["operational_periods"]) == 1
        assert len(workspace["scenarios"]) == 1 and len(workspace["features"]["features"]) == 1
        assert len(workspace["resources"]) == 1
        props = workspace["features"]["features"][0]["properties"]
        assert props["objective"] == "Hold west flank" and props["responsible_unit"] == "Division West"

        replay = target.preview_import(bundle)
        assert replay["valid"] and not replay["can_apply"] and replay["mode"] == "existing_incident"
        before = len(target.list_incidents(include_closed=True))
        try:
            target.import_bundle(bundle, "Planning")
            raise AssertionError("existing incident package was silently applied")
        except PackageConflict as exc:
            assert exc.report["mode"] == "existing_incident"
        assert len(target.list_incidents(include_closed=True)) == before

        malformed = dict(bundle); malformed["schema"] = "unknown/9"
        invalid = target.preview_import(malformed)
        assert not invalid["valid"] and not invalid["can_apply"]
        source_db.close(); target_db.close()
    print("Incident package import checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
