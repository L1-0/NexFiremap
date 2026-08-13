"""Classified deterministic product and public-leakage checks."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.operations import OperationsError, OperationsStore, default_period
from nexfiremap.products import ProductManager


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "products.sqlite3")
        try:
            store = OperationsStore(db); manager = ProductManager(db, store)
            incident = store.create_incident({"name": "Product test", "notes": "SECRET IC NOTE"}, "IC")
            period = store.create_period(incident["id"], default_period(), "IC")
            scenario = store.create_scenario(incident["id"], period["id"], {"name": "SECRET PLAN"}, "IC")
            store.create_resource(incident["id"], {"callsign": "SECRET ENGINE", "unit_type": "engine"}, "IC")
            store.create_feature(incident["id"], {"period_id": period["id"], "scenario_id": scenario["id"],
                "feature_type": "tactical_line", "title": "SECRET TACTIC", "status": "planned",
                "geometry": {"type": "LineString", "coordinates": [[11, 48], [11.1, 48.1]]},
                "properties": {"hazards": "SECRET HAZARD"}}, "IC")
            perimeter = store.create_feature(incident["id"], {"period_id": period["id"],
                "feature_type": "confirmed_perimeter", "title": "Published perimeter", "status": "confirmed",
                "geometry": {"type": "Polygon", "coordinates": [[[11, 48], [11.1, 48], [11.1, 48.1], [11, 48]]]},
                "observer": "SECRET OBSERVER", "source": "SECRET SOURCE"}, "IC")
            public = manager.create(incident["id"], fmt="geojson", classification="public",
                                    product_type="public_information", snapshot_id=None, actor="PIO")
            filename, media_type, content = manager.content(incident["id"], public["id"])
            assert filename.endswith(".geojson") and media_type == "application/geo+json"
            assert hashlib.sha256(content).hexdigest() == public["sha256"]
            text = content.decode()
            for secret in ("SECRET IC NOTE", "SECRET PLAN", "SECRET ENGINE", "SECRET TACTIC",
                           "SECRET HAZARD", "SECRET OBSERVER", "SECRET SOURCE", "audit_log", "source_imports"):
                assert secret not in text, f"public product leaked {secret}"
            payload = json.loads(text)
            assert payload["features"]["features"][0]["id"] == perimeter["properties"]["id"]

            snapshot = store.create_snapshot(incident["id"], "Product base", period["id"], "operational", "IC")
            first = manager.create(incident["id"], fmt="csv", classification="operational", product_type="field",
                                   snapshot_id=snapshot["id"], actor="PLANS", title="Field map data")
            second = manager.create(incident["id"], fmt="csv", classification="operational", product_type="field",
                                    snapshot_id=snapshot["id"], actor="PLANS", title="Field map data")
            assert manager.content(incident["id"], first["id"])[2] == manager.content(incident["id"], second["id"])[2]
            signatures = {"pdf": b"%PDF", "geopdf": b"%PDF", "kmz": b"PK", "geotiff": b"II*\x00", "gpkg": b"SQLite format 3\x00"}
            for fmt, signature in signatures.items():
                made = manager.create(incident["id"], fmt=fmt, classification="operational", product_type="field",
                                      snapshot_id=snapshot["id"], actor="PLANS", title=f"Field {fmt}")
                assert manager.content(incident["id"], made["id"])[2].startswith(signature), fmt
            assert len(manager.list(incident["id"])) == 8
            try:
                manager.create(incident["id"], fmt="csv", classification="public", product_type="field",
                               snapshot_id=None, actor="bad")
                raise AssertionError("public classification allowed non-public template")
            except OperationsError:
                pass
        finally:
            db.close()
    print("Classified product checks passed.")


if __name__ == "__main__":
    main()
