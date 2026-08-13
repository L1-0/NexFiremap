"""Structure cache and temporal exposure tests; no network required."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.structures import (
    assess_exposure,
    build_overpass_query,
    ensure_structures,
    parse_overpass_structures,
    query_cache_is_fresh,
    snap_bbox,
)


def main() -> int:
    query = build_overpass_query((10.0, 48.0, 10.1, 48.1))
    assert 'way["building"]' in query and 'relation["building"]' in query and "out tags center geom" in query

    parsed = parse_overpass_structures({"elements": [
        {"type": "way", "id": 1, "tags": {"building": "house", "addr:street": "Oak Road", "addr:housenumber": "7"},
         "geometry": [{"lon": 0.02, "lat": 0.38}, {"lon": 0.08, "lat": 0.38}, {"lon": 0.08, "lat": 0.32}, {"lon": 0.02, "lat": 0.32}]},
        {"type": "relation", "id": 2, "tags": {"building": "hospital", "amenity": "hospital"}, "center": {"lon": 0.25, "lat": 0.25}},
        {"type": "node", "id": 3, "tags": {"building": "yes"}, "lat": 1, "lon": 1},
    ]})
    assert len(parsed) == 2
    assert parsed[0]["geometry"]["coordinates"][0][0] == parsed[0]["geometry"]["coordinates"][0][-1]
    assert parsed[1]["geometry"] is None

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = Database(root / "structures.sqlite3")
        now = int(time.time())
        snapped = snap_bbox((0.0, 0.0, 0.4, 0.4))
        db.conn.execute(
            "INSERT INTO structure_query_cache (west,south,east,north,fetched_at,row_count) VALUES (?,?,?,?,?,2)",
            (*snapped, now),
        )
        for item in parsed:
            db.conn.execute(
                "INSERT INTO structures (osm_type,osm_id,latitude,longitude,geometry_json,tags_json,fetched_at) VALUES (?,?,?,?,?,?,?)",
                (item["osm_type"], item["osm_id"], item["lat"], item["lon"],
                 json.dumps(item["geometry"]) if item["geometry"] else None, json.dumps(item["tags"]), now),
            )
        db.conn.commit()
        assert query_cache_is_fresh(db.conn, (0.0, 0.0, 0.4, 0.4))
        cached, fetched = ensure_structures(db.conn, (0.0, 0.0, 0.4, 0.4))
        assert len(cached) == 2 and fetched is False

        job_dir = root / "jobs"; output = job_dir / "8"; output.mkdir(parents=True)
        median = np.array([[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, np.nan, np.nan], [np.nan] * 4], dtype=np.float32)
        np.savez_compressed(output / "impact_surface.npz", median_hours=median,
                            earliest_hours=np.maximum(0, median - 0.5), latest_hours=median + 1.0)
        job = {"id": 8, "kind": "run_ensemble_assimilation", "status": "done", "result_json": json.dumps({
            "bounds": [[0.0, 0.0], [0.4, 0.4]], "reference_ts": 1_700_000_000,
            "files": {"impact_surface": "impact_surface.npz"},
        })}
        exposure = assess_exposure(db.conn, job, job_dir)
        assert exposure["meta"]["structure_count"] == 2
        house = next(f for f in exposure["features"] if f["properties"]["category"] == "residential")
        hospital = next(f for f in exposure["features"] if f["properties"]["category"] == "critical")
        assert house["properties"]["impact_hours"] == 1.0
        assert house["properties"]["earliest_hours"] == 0.5
        assert hospital["properties"]["impact_hours"] == 7.0
        assert exposure["meta"]["summary"]["within_2h"] == 1
        assert exposure["meta"]["summary"]["within_12h"] == 1
        assert exposure["meta"]["affected_categories_48h"] == {"residential": 1, "critical": 1, "other": 0}
        db.close()

    print("Structure exposure checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
