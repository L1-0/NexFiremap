"""Unified model provenance attachment, freshness and handover checks."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.operations import OperationsStore, default_period
from nexfiremap.products import ProductManager


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); source_db = Database(root / "source.sqlite3"); target_db = Database(root / "target.sqlite3")
        try:
            store = OperationsStore(source_db)
            incident = store.create_incident({"name": "Provenance"}, "IC")
            period = store.create_period(incident["id"], default_period(), "IC")
            scenario = store.create_scenario(incident["id"], period["id"], {"name": "Wind scenario"}, "PLANS")
            old = int(time.time()) - 8 * 3600
            job_id = source_db.create_job("run_propagation", {"event_id": 9, "reference_ts": old})
            source_db.update_job(job_id, status="done", finished_at=old + 60, result_json=json.dumps({
                "reference_ts": old, "weather": {"hours_sampled": 24, "hours_backfilled_recent": 2}
            }))
            attached = store.attach_model_run(incident["id"], scenario["id"], job_id, "FBAN")
            assert attached["provenance"]["is_stale"] is True
            assert any("backfilled" in warning for warning in attached["provenance"]["warnings"])
            assert store.attach_model_run(incident["id"], scenario["id"], job_id, "FBAN")["id"] == attached["id"]

            snapshot = store.create_snapshot(incident["id"], "With model", period["id"], "operational", "PLANS")
            product = ProductManager(source_db, store).create(
                incident["id"], fmt="json", classification="operational", product_type="briefing",
                snapshot_id=snapshot["id"], actor="PLANS",
            )
            content = ProductManager(source_db, store).content(incident["id"], product["id"])[2]
            assert b"nexfiremap-model-provenance/1" in content

            bundle = store.export_bundle(incident["id"])
            target = OperationsStore(target_db)
            assert target.import_bundle(bundle, "HANDOVER")["imported"] is True
            restored = target.list_model_runs(incident["id"], scenario["id"])
            assert restored[0]["provenance"]["job_id"] == job_id
        finally:
            source_db.close(); target_db.close()
    print("Model provenance checks passed.")


if __name__ == "__main__":
    main()
