"""Temporal, provenance-aware wind-field and API checks."""

from __future__ import annotations

import json
import math
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.api import create_app
from nexfiremap.config import Settings
from nexfiremap.wind import components, interpolate_vectors, meteorological


def settings(root: Path) -> Settings:
    return Settings(map_key="", host="127.0.0.1", port=8000, db_path=root / "wind.sqlite3",
                    cache_days=30, tile_cache_dir=root / "tiles", job_dir=root / "jobs", job_workers=1,
                    backup_dir=root / "backups", backup_interval_minutes=0, backup_keep=3,
                    drone_dir=root / "drone")


def main() -> None:
    north_u, north_v = components(5.0, 0.0)
    assert abs(north_u) < 1e-9 and math.isclose(north_v, -5.0)
    speed, direction = meteorological(north_u, north_v)
    assert math.isclose(speed, 5.0) and abs(direction) < 1e-9
    # Directions either side of North must average around North, not South.
    samples = []
    for index, from_deg in enumerate((350.0, 10.0)):
        u, v = components(5.0, from_deg)
        samples.append({"latitude": 48.0, "longitude": 11.0 + index * .01,
                        "observed_epoch": 1000.0, "u_east_ms": u, "v_north_ms": v})
    wrapped = interpolate_vectors(samples, 48.0, 11.005, 1000.0, 3.0)
    assert wrapped["wind_from_deg"] < 2 or wrapped["wind_from_deg"] > 358, wrapped

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        with TestClient(create_app(settings(root))) as client:
            incident_result = client.post("/api/operations/incidents", json={"name": "Wind incident"})
            assert incident_result.status_code == 201, incident_result.text
            workspace = incident_result.json(); incident = workspace["incident"]
            period, scenario = workspace["period"], workspace["scenario"]
            target = datetime.now(timezone.utc).replace(microsecond=0)

            def observation(title: str, lon: float, from_deg: float, speed_ms: float,
                            observed_at: datetime, properties: dict | None = None) -> None:
                response = client.post(f"/api/operations/incidents/{incident['id']}/features", json={
                    "period_id": period["id"], "feature_type": "wind_observation", "title": title,
                    "status": "observed", "observed_at": observed_at.isoformat(), "source": "portable station",
                    "observer": "weather desk", "confidence": "high",
                    "geometry": {"type": "Point", "coordinates": [lon, 48.0]},
                    "properties": properties or {"wind_speed_ms": speed_ms, "wind_from_deg": from_deg,
                                                   "wind_gust_ms": speed_ms + 2, "wind_height_m": 10},
                })
                assert response.status_code == 201, response.text

            observation("West station", 11.0, 350, 5, target - timedelta(hours=1))
            # Explicit km/h/cardinal import-style values are normalized.
            observation("East station", 11.1, 0, 0, target - timedelta(minutes=30),
                        {"wind_speed": "18 km/h", "wind_direction": "N"})
            # This future point must not leak into the historical requested field.
            observation("Future station", 11.05, 180, 20, target + timedelta(hours=1))
            # Ambiguous legacy notes are retained in the incident but rejected by the field.
            observation("Ambiguous station", 11.05, 0, 0, target - timedelta(minutes=10),
                        {"wind_speed": "strong", "wind_direction": "variable"})

            response = client.get(f"/api/operations/incidents/{incident['id']}/wind-field", params={
                "bbox": "10.95,47.95,11.15,48.05", "at": target.isoformat(), "window_hours": 3, "grid": 3,
                "scenario_id": scenario["id"],
            })
            assert response.status_code == 200, response.text
            field = response.json()
            assert field["method"] == "observation_idw"
            assert len(field["vectors"]["features"]) == 9
            assert len(field["observations"]["features"]) == 2
            assert "ignored" in " ".join(field["warnings"])
            assert all(feature["properties"]["wind_from_deg"] < 20 or
                       feature["properties"]["wind_from_deg"] > 340 for feature in field["vectors"]["features"])
            assert all(feature["properties"]["estimated"] for feature in field["vectors"]["features"])

            # Attached model provenance is a local, uniform fallback when the
            # requested window contains no measurements.
            model_reference = target + timedelta(hours=3)
            provenance = {
                "schema": "nexfiremap-model-provenance/1", "reference_at": model_reference.isoformat(),
                "valid_until": (model_reference + timedelta(hours=6)).isoformat(), "is_stale": False,
                "sources": ["Open-Meteo"], "parameters": {},
                "weather_summary": {"wind_speed_ms": 8.0, "wind_direction_deg": 270.0, "hours_sampled": 3},
                "warnings": [], "limitations": "AOI-wide model sample",
            }
            db = client.app.state.db
            with db._write_lock:
                db.conn.execute(
                    "INSERT INTO incident_model_runs (id,incident_id,scenario_id,job_id,model_kind,provenance_json,attached_by,attached_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (uuid4().hex, incident["id"], scenario["id"], None, "run_propagation",
                     json.dumps(provenance), "test", model_reference.isoformat()),
                )
                db.conn.commit()
            fallback_at = target + timedelta(hours=4)
            fallback = client.get(f"/api/operations/incidents/{incident['id']}/wind-field", params={
                "bbox": "10.95,47.95,11.15,48.05", "at": fallback_at.isoformat(),
                "window_hours": .25, "grid": 2, "scenario_id": scenario["id"],
            })
            assert fallback.status_code == 200, fallback.text
            fallback_field = fallback.json()
            assert fallback_field["method"] == "uniform_model_background"
            assert not fallback_field["observations"]["features"]
            assert all(math.isclose(item["properties"]["speed_ms"], 8.0)
                       for item in fallback_field["vectors"]["features"])
            assert "No local measurement" in " ".join(fallback_field["warnings"])

            unavailable = client.get(f"/api/operations/incidents/{incident['id']}/wind-field", params={
                "bbox": "10.95,47.95,11.15,48.05", "at": (target - timedelta(days=2)).isoformat(),
                "window_hours": 1, "grid": 2,
            }).json()
            assert unavailable["method"] == "unavailable" and unavailable["vectors"]["features"] == []

    print("Temporal wind-map checks passed.")


if __name__ == "__main__":
    main()
