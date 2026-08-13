"""Target-scale workspace read and two-client optimistic-conflict checks."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.api import create_app
from nexfiremap.config import Settings
from nexfiremap.operations import _id, utcnow

TARGET_FEATURES = 5_000


def settings(root: Path) -> Settings:
    return Settings(map_key="", host="127.0.0.1", port=8000, db_path=root / "load.sqlite3",
                    cache_days=30, tile_cache_dir=root / "tiles", job_dir=root / "jobs", job_workers=1,
                    backup_dir=root / "backups", backup_interval_minutes=0)


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
      root = Path(temp)
      with TestClient(create_app(settings(root))) as client_a:
        created = client_a.post("/api/operations/incidents", json={"name": "Load test"}).json()
        incident, period = created["incident"], created["period"]
        db = client_a.app.state.db; now = utcnow()
        rows = []
        for index in range(TARGET_FEATURES):
            lon, lat = 11 + (index % 100) * .0001, 48 + (index // 100) * .0001
            rows.append((_id(), incident["id"], period["id"], None, "spot_fire", f"Observation {index}",
                         "observed", json.dumps({"type": "Point", "coordinates": [lon, lat]}), "{}",
                         now, "load fixture", "test", None, None, None, "test", now, now, 1))
        with db._write_lock:
            db.conn.executemany(
                "INSERT INTO tactical_features (id,incident_id,period_id,scenario_id,feature_type,title,status,geometry_json,properties_json,observed_at,source,observer,confidence,valid_from,valid_to,created_by,created_at,updated_at,revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            ); db.conn.commit()
        started = time.perf_counter(); workspace = client_a.get(f"/api/operations/incidents/{incident['id']}")
        elapsed = time.perf_counter() - started
        assert workspace.status_code == 200 and len(workspace.json()["features"]["features"]) == TARGET_FEATURES
        assert elapsed < 10, f"5,000-object workspace read took {elapsed:.2f}s"

        # Two independent browser sessions read revision 1. The first write
        # succeeds and the second is rejected instead of silently overwriting.
        with TestClient(create_app(settings(root))) as client_b:
            assert client_b.get(f"/api/operations/incidents/{incident['id']}").json()["incident"]["revision"] == 1
            first = client_a.patch(f"/api/operations/incidents/{incident['id']}",
                                   json={"expected_revision": 1, "notes": "client A"})
            second = client_b.patch(f"/api/operations/incidents/{incident['id']}",
                                    json={"expected_revision": 1, "notes": "client B"})
            assert first.status_code == 200 and second.status_code == 409
    print("Target incident load and two-client conflict checks passed.")


if __name__ == "__main__":
    main()
