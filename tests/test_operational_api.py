"""End-to-end API checks for field backup and incident handover workflows."""

from __future__ import annotations

import json
import sys
import sqlite3
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.api import create_app
from nexfiremap.config import Settings
from nexfiremap.db import Database
from nexfiremap.operations import OperationsStore, default_period
from nexfiremap.tiles import TRANSPARENT_PNG


def settings_for(root: Path) -> Settings:
    return Settings(
        map_key="",
        host="127.0.0.1",
        port=8000,
        db_path=root / "target.sqlite3",
        cache_days=30,
        tile_cache_dir=root / "tiles",
        job_dir=root / "jobs",
        job_workers=1,
        backup_dir=root / "backups",
        backup_interval_minutes=0,
        backup_keep=3,
    )


def source_bundle(root: Path) -> dict:
    database = Database(root / "source.sqlite3")
    try:
        store = OperationsStore(database)
        incident = store.create_incident(
            {
                "name": "API handover test",
                "incident_number": "TEST-42",
                "timezone": "Europe/Berlin",
                "center_lat": 48.1,
                "center_lon": 11.5,
            },
            "source operator",
        )
        period = store.create_period(incident["id"], default_period(), "source operator")
        store.create_scenario(
            incident["id"],
            period["id"],
            {"name": "Primary", "kind": "primary", "description": "Field handover"},
            "source operator",
        )
        store.create_resource(
            incident["id"], {"callsign": "Engine 42", "unit_type": "engine", "crew_size": 4},
            "source operator",
        )
        return store.export_bundle(incident["id"])
    finally:
        database.close()


def mbtiles_bytes(root: Path) -> bytes:
    path = root / "api.mbtiles"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            "CREATE TABLE metadata (name TEXT, value TEXT);"
            "CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);"
        )
        conn.executemany("INSERT INTO metadata VALUES (?,?)", [
            ("name", "API map"), ("format", "png"), ("bounds", "10,47,12,49"),
        ])
        conn.execute("INSERT INTO tiles VALUES (1,1,0,?)", (TRANSPARENT_PNG,))
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        bundle = source_bundle(root)
        local_map = mbtiles_bytes(root)
        app = create_app(settings_for(root))

        with TestClient(app) as client:
            preview = client.post("/api/operations/import/preview", json={"bundle": bundle})
            assert preview.status_code == 200, preview.text
            assert preview.json()["can_apply"] is True

            applied = client.post(
                "/api/operations/import/apply",
                json={"bundle": bundle},
                headers={"X-Operator": "receiving operator"},
            )
            assert applied.status_code == 201, applied.text
            assert applied.json()["imported"] is True
            assert applied.json()["workspace"]["incident"]["name"] == "API handover test"
            workspace = applied.json()["workspace"]
            incident = workspace["incident"]
            period = workspace["operational_periods"][0]
            scenario = workspace["scenarios"][0]
            resource = workspace["resources"][0]

            incident_update = client.patch(
                f"/api/operations/incidents/{incident['id']}",
                json={"expected_revision": incident["revision"], "status": "contained", "notes": "API update"},
            )
            assert incident_update.status_code == 200, incident_update.text
            assert incident_update.json()["revision"] == incident["revision"] + 1
            stale = client.patch(
                f"/api/operations/incidents/{incident['id']}",
                json={"expected_revision": incident["revision"], "notes": "stale"},
            )
            assert stale.status_code == 409 and stale.json()["code"] == "revision_conflict"

            period_update = client.patch(
                f"/api/operations/incidents/{incident['id']}/periods/{period['id']}",
                json={"expected_revision": period["revision"], "status": "active", "objectives": "API objective"},
            )
            assert period_update.status_code == 200, period_update.text
            scenario_update = client.patch(
                f"/api/operations/incidents/{incident['id']}/scenarios/{scenario['id']}",
                json={"expected_revision": scenario["revision"], "description": "Updated plan"},
            )
            assert scenario_update.status_code == 200, scenario_update.text
            resource_update = client.patch(
                f"/api/operations/incidents/{incident['id']}/resources/{resource['id']}",
                json={"expected_revision": resource["revision"], "status": "working", "assignment": "North flank"},
            )
            assert resource_update.status_code == 200, resource_update.text
            position = client.post(
                f"/api/operations/incidents/{incident['id']}/position-reports",
                json={"resource_id": resource["id"], "callsign": resource["callsign"],
                      "latitude": 48.12, "longitude": 11.52,
                      "observed_at": "2026-08-12T12:05:00+00:00", "report_source": "radio",
                      "accuracy_m": 50, "period_id": period["id"], "scenario_id": scenario["id"]},
                headers={"X-Operator": "DIV-A"},
            )
            assert position.status_code == 201, position.text
            assert position.json()["properties"]["feature_type"] == "resource_position"

            field_content = json.dumps({
                "type": "Feature", "geometry": {"type": "Point", "coordinates": [11.51, 48.11]},
                "properties": {"name": "Field spot", "time": "2026-08-12T12:00:00+00:00"},
            })
            field_request = {
                "filename": "field.geojson", "content": field_content, "period_id": period["id"],
                "source": "API field team", "mapping": {"title": "name", "observed_at": "time"},
                "aoi_bbox": [11.4, 48.0, 11.6, 48.2],
            }
            field_preview = client.post(
                f"/api/operations/incidents/{incident['id']}/field-imports/preview", json=field_request
            )
            assert field_preview.status_code == 200 and field_preview.json()["feature_count"] == 1
            field_apply = client.post(
                f"/api/operations/incidents/{incident['id']}/field-imports/apply", json=field_request,
                headers={"X-Operator": "API observer"},
            )
            assert field_apply.status_code == 201, field_apply.text
            import_id = field_apply.json()["import_id"]
            original = client.get(
                f"/api/operations/incidents/{incident['id']}/field-imports/{import_id}/original"
            )
            assert original.status_code == 200 and original.content == field_content.encode()
            progression = client.get(
                f"/api/operations/incidents/{incident['id']}/progression",
                params={"from_time": "2026-08-12T11:00:00+00:00", "to_time": "2026-08-12T13:00:00+00:00"},
            )
            assert progression.status_code == 200 and progression.json()["counts"]["new_since"] == 1

            snapshot_one = client.post(
                f"/api/operations/incidents/{incident['id']}/snapshots",
                json={"name": "Before change", "period_id": period["id"], "classification": "operational"},
            )
            assert snapshot_one.status_code == 201, snapshot_one.text
            latest_incident = incident_update.json()
            changed_again = client.patch(
                f"/api/operations/incidents/{incident['id']}",
                json={"expected_revision": latest_incident["revision"], "notes": "After snapshot"},
            )
            assert changed_again.status_code == 200, changed_again.text
            comparison = client.get(
                f"/api/operations/incidents/{incident['id']}/snapshots/{snapshot_one.json()['id']}/compare"
            )
            assert comparison.status_code == 200, comparison.text
            assert comparison.json()["counts"]["changed"] >= 1

            map_pack = client.post("/api/operations/map-packs", json={
                "name": "API AOI", "bbox": [11.45, 48.05, 11.55, 48.15],
                "layers": ["osm"], "min_zoom": 12, "max_zoom": 12,
            })
            assert map_pack.status_code == 201, map_pack.text
            assert map_pack.json()["summary"]["missing"] == map_pack.json()["summary"]["expected"]
            pack_verify = client.post(f"/api/operations/map-packs/{map_pack.json()['id']}/verify")
            assert pack_verify.status_code == 200, pack_verify.text
            assert pack_verify.json()["summary"]["complete"] is False

            replay = client.post("/api/operations/import/apply", json={"bundle": bundle})
            assert replay.status_code == 409, replay.text
            conflict = replay.json()
            assert conflict["code"] == "package_conflict"
            assert conflict["report"]["can_apply"] is False

            created = client.post("/api/operations/backups")
            assert created.status_code == 201, created.text
            backup = created.json()
            assert backup["integrity"] == "ok"

            listed = client.get("/api/operations/backups")
            assert listed.status_code == 200, listed.text
            assert listed.json()["status"]["latest"]["name"] == backup["name"]

            verified = client.post(f"/api/operations/backups/{backup['name']}/verify")
            assert verified.status_code == 200, verified.text
            assert verified.json()["ok"] is True

            downloaded = client.get(f"/api/operations/backups/{backup['name']}/download")
            assert downloaded.status_code == 200, downloaded.text
            assert downloaded.content.startswith(b"SQLite format 3\x00")

            recovery = client.post("/api/operations/recoveries", json={"backup_name": backup["name"]})
            assert recovery.status_code == 201, recovery.text
            assert recovery.json()["integrity"] == "ok"
            recovery_download = client.get(f"/api/operations/recoveries/{recovery.json()['name']}/download")
            assert recovery_download.status_code == 200
            assert recovery_download.content.startswith(b"SQLite format 3\x00")

            imported_map = client.post(
                "/api/operations/offline-sources",
                params={"name": "API offline map", "source": "test GIS", "attribution": "Test GIS",
                        "acquired_at": "2026-08-12", "licence": "test use", "limitations": "test fixture"},
                content=local_map, headers={"Content-Type": "application/vnd.mapbox-vector-tile"},
            )
            assert imported_map.status_code == 201, imported_map.text
            source_id = imported_map.json()["id"]
            offline_tile = client.get(f"/offline-tiles/{source_id}/1/1/1")
            assert offline_tile.status_code == 200 and offline_tile.content == TRANSPARENT_PNG
            config = client.get("/api/config").json()
            assert any(layer["id"] == f"mbtiles-{source_id}" and layer["offline"] for layer in config["basemaps"])
            local_pack = client.post("/api/operations/map-packs", json={
                "name": "Imported all-zoom source", "bbox": [10.0, -1.0, 11.0, -0.5],
                "layers": [f"mbtiles-{source_id}"], "min_zoom": 1, "max_zoom": 1,
            })
            assert local_pack.status_code == 201, local_pack.text
            assert local_pack.json()["summary"]["complete"] is True
            assert local_pack.json()["zoom_levels"][0]["complete"] is True
            local_verify = client.post(f"/api/operations/map-packs/{local_pack.json()['id']}/verify")
            assert local_verify.status_code == 200 and local_verify.json()["all_zoom_levels_complete"] is True

            traversal = client.get("/api/operations/backups/not-a-backup.sqlite3/download")
            assert traversal.status_code == 400, traversal.text

    print("operational API tests passed")


if __name__ == "__main__":
    main()
