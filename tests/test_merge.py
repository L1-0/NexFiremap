"""Disconnected divergent-package staging and named resolution checks."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.merge import MergeManager
from nexfiremap.operations import OperationsError, OperationsStore, default_period


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source_db = Database(root / "source.sqlite3"); target_db = Database(root / "target.sqlite3")
        try:
            source = OperationsStore(source_db); target = OperationsStore(target_db)
            incident = source.create_incident({"name": "Merge test"}, "SOURCE")
            period = source.create_period(incident["id"], default_period(), "SOURCE")
            feature = source.create_feature(incident["id"], {"period_id": period["id"],
                "feature_type": "spot_fire", "title": "Initial", "status": "observed",
                "geometry": {"type": "Point", "coordinates": [11.5, 48.1]}}, "SOURCE")
            base = source.export_bundle(incident["id"])
            assert target.import_bundle(base, "TARGET")["imported"] is True

            source.update_feature(incident["id"], feature["properties"]["id"], {"title": "Incoming observation"}, 1, "SOURCE")
            target.update_feature(incident["id"], feature["properties"]["id"], {"title": "Local observation"}, 1, "TARGET")
            incoming = source.export_bundle(incident["id"])
            manager = MergeManager(target)
            staged = manager.stage(incoming, "TARGET IC")
            assert staged["status"] == "pending"
            assert manager.stage(incoming, "TARGET IC")["id"] == staged["id"], "staging replay is not idempotent"
            conflicts = staged["report"]["conflicts"]
            divergent = {(item["entity"], item["id"]) for item in conflicts if item["classification"] == "divergent"}
            assert ("feature", feature["properties"]["id"]) in divergent
            try:
                manager.resolve(staged["id"], {}, "RESOLVER")
                raise AssertionError("unresolved divergent package applied")
            except OperationsError:
                pass

            choices = {f"{item['entity']}:{item['id']}": "local" for item in conflicts
                       if item["classification"] in {"divergent", "incoming_newer", "local_newer"}}
            choices[f"feature:{feature['properties']['id']}"] = "incoming"
            resolved = manager.resolve(staged["id"], choices, "RESOLVER")
            assert resolved["status"] == "resolved" and resolved["resolved_by"] == "RESOLVER"
            merged = target.list_features(incident["id"])[0]
            assert merged["properties"]["title"] == "Incoming observation"
            assert merged["properties"]["revision"] == 3
            assert manager.resolve(staged["id"], choices, "RESOLVER")["status"] == "resolved"
            audit = target.export_bundle(incident["id"])["audit_log"]
            assert any(item["action"] == "merge_resolution" and item["actor"] == "RESOLVER" for item in audit)
        finally:
            source_db.close(); target_db.close()
    print("Disconnected merge checks passed.")


if __name__ == "__main__":
    main()
