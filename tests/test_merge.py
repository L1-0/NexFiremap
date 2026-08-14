"""Disconnected divergent-package staging and named resolution checks.

Merging is what happens when two installations that were both editing the same
incident meet again. `MergeManager` never merges on its own: it *stages* an
incoming package, reports the conflicts, and only writes once an operator has
named a winner for each one. The drills below cover the staging refusals, a
resolution that touches every entity kind the merge path can write, and the
atomicity guarantee - a resolution that fails partway leaves nothing behind.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.merge import MergeManager
from nexfiremap.operations import OperationsError, OperationsStore, _id, default_period, utcnow


POINT = {"type": "Point", "coordinates": [11.5, 48.1]}


def _pair(root: Path, name: str) -> tuple[Database, Database, OperationsStore, OperationsStore]:
    source_db = Database(root / f"{name}-source.sqlite3")
    target_db = Database(root / f"{name}-target.sqlite3")
    return source_db, target_db, OperationsStore(source_db), OperationsStore(target_db)


def _shared_incident(source: OperationsStore, target: OperationsStore) -> dict:
    """One incident that both installations hold, ready to diverge."""
    incident = source.create_incident({"name": "Merge test"}, "SOURCE")
    period = source.create_period(incident["id"], default_period(), "SOURCE")
    scenario = source.create_scenario(incident["id"], period["id"], {"name": "Plan A", "kind": "primary"}, "SOURCE")
    feature = source.create_feature(incident["id"], {
        "period_id": period["id"], "feature_type": "spot_fire", "title": "Initial",
        "status": "observed", "geometry": POINT}, "SOURCE")
    resource = source.create_resource(incident["id"], {"callsign": "Engine 1", "unit_type": "engine"}, "SOURCE")
    assert target.import_bundle(source.export_bundle(incident["id"]), "TARGET")["imported"] is True
    return {"incident": incident, "period": period, "scenario": scenario,
            "feature": feature, "resource": resource}


def check_divergent_feature_resolution(root: Path) -> None:
    """The original drill: both sides edit one feature, the operator picks the
    incoming version, and the choice is recorded in the audit log."""
    source_db, target_db, source, target = _pair(root, "divergent")
    try:
        built = _shared_incident(source, target)
        incident, feature = built["incident"], built["feature"]
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

        # A staged package is visible to the operator before it is applied.
        listed = manager.list(incident["id"])
        assert [item["id"] for item in listed] == [staged["id"]]
        assert listed[0]["status"] == "pending"

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


def check_staging_refuses_what_it_cannot_merge(root: Path) -> None:
    """Staging is only for a package that overlaps an incident we already hold.
    A structurally invalid package, or one describing an incident this
    installation has never seen, is refused - the latter belongs in the plain
    import path, not the conflict-resolution queue."""
    source_db, target_db, source, target = _pair(root, "staging")
    try:
        manager = MergeManager(target)
        incident = source.create_incident({"name": "Unknown here"}, "SOURCE")
        source.create_period(incident["id"], default_period(), "SOURCE")
        fresh = source.export_bundle(incident["id"])

        # Never-seen incident: not a merge, an import.
        try:
            manager.stage(fresh, "TARGET IC")
            raise AssertionError("a brand-new incident package was staged for merging")
        except OperationsError as exc:
            assert "existing-incident" in str(exc), exc

        # Structurally invalid package: refused before anything is recorded.
        broken = copy.deepcopy(fresh)
        broken["features"] = {"type": "Nonsense", "features": []}
        try:
            manager.stage(broken, "TARGET IC")
            raise AssertionError("an invalid package was staged")
        except OperationsError as exc:
            assert "invalid incident package" in str(exc), exc
        try:
            manager.stage({"schema": "unknown/9"}, "TARGET IC")
            raise AssertionError("a package with an unknown schema was staged")
        except OperationsError:
            pass

        # An unknown staged-package id is an error, not a silent empty result.
        try:
            manager.get(_id())
            raise AssertionError("unknown staged package returned")
        except OperationsError:
            pass
        try:
            manager.resolve(_id(), {}, "RESOLVER")
            raise AssertionError("unknown staged package resolved")
        except OperationsError:
            pass

        # Nothing above created an inbox row.
        target.import_bundle(fresh, "TARGET")
        assert manager.list(incident["id"]) == []
    finally:
        source_db.close(); target_db.close()


def check_resolution_applies_every_entity_kind(root: Path) -> None:
    """One resolution that exercises every branch of the merge writer.

    The incoming package carries a changed incident, period, scenario and
    resource (the shared table-driven branch), a brand-new feature (insert) and
    a changed one (update), plus an embedded source file, a model run and a
    warning acknowledgement - each of which has its own writer. Choosing
    "incoming" for all of them must land every record; the one entity marked
    "local" must stay exactly as it was."""
    source_db, target_db, source, target = _pair(root, "kinds")
    try:
        built = _shared_incident(source, target)
        incident_id = built["incident"]["id"]
        period_id, scenario_id = built["period"]["id"], built["scenario"]["id"]
        feature_id, resource_id = built["feature"]["properties"]["id"], built["resource"]["id"]

        # The other installation works the incident while disconnected.
        source.update_incident(incident_id, {"notes": "Incoming: relief crew at 06:00"}, 1, "SOURCE")
        source.update_period(incident_id, period_id, {"objectives": "Incoming: hold the road"}, 1, "SOURCE")
        source.update_scenario(incident_id, scenario_id, {"assumptions": "Incoming: wind backs"}, 1, "SOURCE")
        source.update_resource(incident_id, resource_id, {"assignment": "Incoming: south flank"}, 1, "SOURCE")
        source.update_feature(incident_id, feature_id, {"title": "Incoming: confirmed spot"}, 1, "SOURCE")
        new_feature = source.create_feature(incident_id, {
            "period_id": period_id, "feature_type": "spot_fire", "title": "Incoming: second spot",
            "status": "observed", "geometry": {"type": "Point", "coordinates": [11.55, 48.15]}}, "SOURCE")
        new_feature_id = new_feature["properties"]["id"]

        # Records whose own writers only ever run on the merge path. They ride
        # in the bundle exactly as an export would carry them.
        raw = b"name,latitude,longitude\nSpot,48.1,11.5\n" * 4
        bundle = source.export_bundle(incident_id)
        source_import_id, model_run_id, warning_ack_id = _id(), _id(), _id()
        bundle["source_imports"] = [{
            "id": source_import_id, "incident_id": incident_id, "filename": "tablet.csv", "format": "csv",
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "source": "field tablet",
            "imported_by": "SOURCE", "imported_at": utcnow(), "feature_count": 1,
            "report": {"accepted": 1}, "original_base64": base64.b64encode(raw).decode("ascii")}]
        bundle["model_runs"] = [{
            "id": model_run_id, "incident_id": incident_id, "scenario_id": scenario_id, "job_id": 7,
            "model_kind": "rothermel", "provenance": {"model_kind": "rothermel", "inputs": {"wind_kmh": 18}},
            "attached_by": "SOURCE", "attached_at": utcnow()}]
        bundle["tactical_warning_acknowledgements"] = [{
            "id": warning_ack_id, "warning_id": "w-1", "incident_id": incident_id, "period_id": period_id,
            "scenario_id": scenario_id, "warning_code": "steep_slope", "reason": "Briefed at 05:00",
            "acknowledged_by": "SOURCE", "acknowledged_at": utcnow()}]

        # Meanwhile the local side edits the resource, so one entity has a real
        # local version worth keeping.
        target.update_resource(incident_id, resource_id, {"assignment": "Local: rehab"}, 1, "TARGET")

        manager = MergeManager(target)
        staged = manager.stage(bundle, "TARGET IC")
        conflicts = staged["report"]["conflicts"]
        kinds = {item["entity"] for item in conflicts}
        assert {"incident", "period", "scenario", "resource", "feature",
                "source_import", "model_run", "warning_ack"} <= kinds, kinds

        choices = {f"{item['entity']}:{item['id']}": "incoming" for item in conflicts}
        choices[f"resource:{resource_id}"] = "local"
        resolved = manager.resolve(staged["id"], choices, "RESOLVER")
        assert resolved["status"] == "resolved"
        applied = {(item["entity"], item["id"]) for item in resolved["resolution"]["applied"]}
        assert ("resource", resource_id) not in applied
        assert {("incident", incident_id), ("period", period_id), ("scenario", scenario_id),
                ("feature", feature_id), ("feature", new_feature_id),
                ("source_import", source_import_id), ("model_run", model_run_id),
                ("warning_ack", warning_ack_id)} <= applied, applied

        merged = target.export_bundle(incident_id)
        assert merged["incident"]["notes"] == "Incoming: relief crew at 06:00"
        assert merged["operational_periods"][0]["objectives"] == "Incoming: hold the road"
        assert merged["scenarios"][0]["assumptions"] == "Incoming: wind backs"
        titles = {item["properties"]["id"]: item["properties"]["title"] for item in merged["features"]["features"]}
        assert titles[feature_id] == "Incoming: confirmed spot"
        assert titles[new_feature_id] == "Incoming: second spot", "a new incoming feature was not inserted"

        # The entity the operator kept is untouched by the merge.
        assert merged["resources"][0]["assignment"] == "Local: rehab"
        assert {item["entity"] for item in resolved["resolution"]["kept_local"]} == {"resource"}

        # The merge-only writers really wrote, and the blob survived base64.
        assert len(merged["source_imports"]) == 1
        assert base64.b64decode(merged["source_imports"][0]["original_base64"]) == raw
        assert merged["source_imports"][0]["filename"] == "tablet.csv"
        assert [item["id"] for item in merged["model_runs"]] == [model_run_id]
        assert merged["model_runs"][0]["provenance"]["inputs"]["wind_kmh"] == 18
        assert [item["id"] for item in merged["tactical_warning_acknowledgements"]] == [warning_ack_id]
        assert merged["tactical_warning_acknowledgements"][0]["warning_code"] == "steep_slope"

        # A merge write is always strictly newer than what it replaced, so a
        # later ordinary edit cannot look older than the resolution.
        assert merged["incident"]["revision"] > 1
        assert titles and merged["features"]["features"][0]["properties"]["revision"] >= 1
        later = target.update_incident(incident_id, {"notes": "After the merge"}, merged["incident"]["revision"], "TARGET")
        assert later["revision"] == merged["incident"]["revision"] + 1

        # An entity kind the merge writer does not know is refused outright.
        try:
            manager._apply("nonsense", {"id": _id()}, incident_id, staged["id"], "RESOLVER")
            raise AssertionError("an unsupported merge entity kind was applied")
        except OperationsError as exc:
            assert "unsupported merge entity" in str(exc), exc
    finally:
        source_db.close(); target_db.close()


def check_failed_resolution_applies_nothing(root: Path) -> None:
    """A resolution is one transaction. If any entity fails to apply, the whole
    resolution rolls back - no half-merged incident, and the package stays
    pending so the operator can retry rather than losing the review."""
    source_db, target_db, source, target = _pair(root, "atomic")
    try:
        built = _shared_incident(source, target)
        incident_id = built["incident"]["id"]
        feature_id = built["feature"]["properties"]["id"]

        source.update_incident(incident_id, {"notes": "Incoming: should not land"}, 1, "SOURCE")
        source.update_feature(incident_id, feature_id, {"title": "Incoming: should not land"}, 1, "SOURCE")
        bundle = source.export_bundle(incident_id)

        manager = MergeManager(target)
        staged = manager.stage(bundle, "TARGET IC")
        before = {key: value for key, value in target.export_bundle(incident_id).items()
                  if key not in {"package_id", "exported_at"}}

        # Damage the stored bundle so one entity the report promises is no
        # longer in it - the failure mode a truncated or edited inbox row would
        # produce. The report still lists the feature, so resolve() will reach
        # for it and fail partway through, after the incident has been written.
        damaged = copy.deepcopy(bundle)
        damaged["features"]["features"] = []
        target_db.conn.execute(
            "UPDATE incident_package_inbox SET bundle_json=? WHERE id=?",
            (json.dumps(damaged, separators=(",", ":")), staged["id"]))
        target_db.conn.commit()

        choices = {f"{item['entity']}:{item['id']}": "incoming" for item in staged["report"]["conflicts"]}
        try:
            manager.resolve(staged["id"], choices, "RESOLVER")
            raise AssertionError("a resolution with a missing entity was committed")
        except OperationsError as exc:
            assert "incoming entity missing" in str(exc), exc

        # Nothing was applied - not even the incident, which is written first.
        after = {key: value for key, value in target.export_bundle(incident_id).items()
                 if key not in {"package_id", "exported_at"}}
        assert after == before, "a failed resolution left changes behind"
        assert target.get_incident(incident_id)["notes"] != "Incoming: should not land"
        assert target.list_features(incident_id)[0]["properties"]["title"] == "Initial"

        # The package is still pending, so the review is not lost.
        assert manager.get(staged["id"])["status"] == "pending"
        assert manager.get(staged["id"])["resolution"] is None
    finally:
        source_db.close(); target_db.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        check_divergent_feature_resolution(root)
        check_staging_refuses_what_it_cannot_merge(root)
        check_resolution_applies_every_entity_kind(root)
        check_failed_resolution_applies_nothing(root)
    print("Disconnected merge checks passed.")


if __name__ == "__main__":
    main()
