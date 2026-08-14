"""Incident handover preview/import tests; no network required.

An incident package is the handover format between installations, so the
question these drills keep asking is the same one an incident commander would:
*can this package silently change something we already have?* The answer has to
be no - `import_bundle` only ever applies a brand-new incident with no id
collisions, and every other case must be refused with a report, leaving the
local workspace bit-for-bit as it was.
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
from nexfiremap.operations import OperationsStore, PackageConflict, _id, default_period, utcnow


# --------------------------------------------------------------- helpers


def _seed_source(root: Path, name: str) -> tuple[Database, OperationsStore, dict]:
    """A source installation holding one fully-populated incident."""
    db = Database(root / f"{name}.sqlite3")
    store = OperationsStore(db)
    incident = store.create_incident({"name": "Handover Fire", "incident_number": "HF-7"}, "IC-A")
    period = store.create_period(incident["id"], default_period(), "IC-A")
    scenario = store.create_scenario(incident["id"], period["id"], {"name": "Plan A", "kind": "primary"}, "IC-A")
    feature = store.create_feature(incident["id"], {
        "period_id": period["id"], "scenario_id": scenario["id"], "feature_type": "tactical_line",
        "title": "West line", "status": "planned",
        "geometry": {"type": "LineString", "coordinates": [[10, 48], [10.01, 48.01]]},
        "properties": {"objective": "Hold west flank", "responsible_unit": "Division West",
                       "priority": "high", "water_requirement": "2 engines"},
    }, "DIV-W")
    resource = store.create_resource(incident["id"], {"callsign": "Engine 2", "unit_type": "engine"}, "LOG")
    return db, store, {"incident": incident, "period": period, "scenario": scenario,
                       "feature": feature, "resource": resource}


def _stable(bundle: dict) -> dict:
    """A bundle without the two fields that change on every export, so two
    exports of an *unchanged* incident compare equal."""
    return {key: value for key, value in bundle.items() if key not in {"package_id", "exported_at"}}


def _retarget(bundle: dict, new_incident_id: str) -> dict:
    """Copy a package onto a fresh incident id while keeping every child record
    id identical - i.e. exactly what two installations that independently
    generated the same record ids would produce."""
    old = str(bundle["incident"]["id"])
    clone = copy.deepcopy(bundle)
    clone["incident"]["id"] = new_incident_id
    for group in ("operational_periods", "scenarios", "resources", "audit_log",
                  "source_imports", "model_runs", "tactical_warning_acknowledgements"):
        for row in clone.get(group, []):
            if row.get("incident_id") == old:
                row["incident_id"] = new_incident_id
            if row.get("entity_id") == old:
                row["entity_id"] = new_incident_id
    for feature in clone["features"]["features"]:
        if feature["properties"].get("incident_id") == old:
            feature["properties"]["incident_id"] = new_incident_id
    return clone


def _source_import_record(incident_id: str, raw: bytes) -> dict:
    """A well-formed embedded source-file record, hash and size included."""
    return {"id": _id(), "incident_id": incident_id, "filename": "field.csv", "format": "csv",
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "source": "field tablet",
            "imported_by": "DIV-W", "imported_at": utcnow(), "feature_count": 1, "report": {"accepted": 1},
            "original_base64": base64.b64encode(raw).decode("ascii")}


# ----------------------------------------------------------------- drills


def check_handover_round_trip_and_replay_refusal(root: Path) -> None:
    """A package imports cleanly into an empty workspace, and replaying the same
    package into the workspace that now holds it is refused."""
    source_db, source, built = _seed_source(root, "source")
    target_db = Database(root / "target.sqlite3")
    try:
        incident = built["incident"]
        bundle = source.export_bundle(incident["id"])
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
    finally:
        source_db.close(); target_db.close()


def check_colliding_record_ids_are_refused(root: Path) -> None:
    """A package for an incident we have never seen, but carrying record ids we
    already use, must be refused rather than inserted over the top of ours.

    This is the case that would corrupt an existing incident's history: the
    incident id looks new, so nothing warns the operator, but its features and
    resources would land on rows another incident already owns."""
    source_db, source, built = _seed_source(root, "collide-source")
    target_db = Database(root / "collide-target.sqlite3")
    try:
        bundle = source.export_bundle(built["incident"]["id"])
        target = OperationsStore(target_db)
        assert target.import_bundle(bundle, "Planning")["imported"] is True
        settled = _stable(target.export_bundle(built["incident"]["id"]))
        incident_count = len(target.list_incidents(include_closed=True))

        # Same child records, different incident id - a second installation that
        # happened to mint ids we already hold.
        clashing = _retarget(bundle, _id())
        report = target.preview_import(clashing)
        assert report["mode"] == "conflict", report
        assert report["valid"] is False and report["can_apply"] is False
        collided = {item["entity"] for item in report["conflicts"]}
        assert {"operational_periods", "plan_scenarios", "tactical_features",
                "incident_resources"} <= collided, collided
        assert all(item["classification"] == "id_collision" for item in report["conflicts"])

        try:
            target.import_bundle(clashing, "Planning")
            raise AssertionError("package with colliding record ids was applied")
        except PackageConflict as exc:
            assert exc.report["mode"] == "conflict"

        # Nothing was written: no new incident, and the incident we already held
        # is byte-for-byte what it was before the refused import.
        assert len(target.list_incidents(include_closed=True)) == incident_count
        assert target.preview_import(clashing)["mode"] == "conflict"
        assert _stable(target.export_bundle(built["incident"]["id"])) == settled
    finally:
        source_db.close(); target_db.close()


def check_existing_incident_conflicts_are_classified(root: Path) -> None:
    """When the incident already exists locally, every incoming record is
    classified against our copy by revision - and none of it is applied.

    Revisions rather than timestamps are what decide "who is ahead", so the
    drill drives each classification with a real edit on one side or the other
    and then proves the local workspace never moved."""
    source_db, source, built = _seed_source(root, "classify-source")
    target_db = Database(root / "classify-target.sqlite3")
    try:
        incident_id = built["incident"]["id"]
        original = source.export_bundle(incident_id)
        target = OperationsStore(target_db)
        target.import_bundle(original, "Planning")
        settled = _stable(target.export_bundle(incident_id))

        # Identical replay: everything matches, nothing is flagged.
        replay = target.preview_import(original)
        assert replay["mode"] == "existing_incident" and replay["can_apply"] is False
        assert replay["classifications"]["identical"] >= 4, replay["classifications"]
        assert replay["classifications"]["divergent"] == 0

        # The other installation edited the feature we both hold: incoming_newer.
        source.update_feature(incident_id, built["feature"]["id"], {"status": "under_construction"}, 1, "DIV-W")
        ahead = source.export_bundle(incident_id)
        report = target.preview_import(ahead)
        assert report["mode"] == "existing_incident" and report["can_apply"] is False
        assert report["classifications"]["incoming_newer"] >= 1, report["classifications"]
        assert any(item["entity"] == "feature" and item["classification"] == "incoming_newer"
                   for item in report["conflicts"]), report["conflicts"]

        # We edited a record they still hold at the older revision: local_newer.
        target.update_resource(incident_id, built["resource"]["id"], {"assignment": "West flank"}, 1, "LOG-T")
        report = target.preview_import(original)
        assert report["classifications"]["local_newer"] >= 1, report["classifications"]
        assert any(item["entity"] == "resource" and item["classification"] == "local_newer"
                   for item in report["conflicts"]), report["conflicts"]

        # Same revision on both sides, different content: nobody can auto-resolve.
        divergent = copy.deepcopy(original)
        for row in divergent["resources"]:
            if row["id"] == built["resource"]["id"]:
                row["assignment"] = "East flank"
                row["revision"] = 2
        report = target.preview_import(divergent)
        assert any(item["entity"] == "resource" and item["classification"] == "divergent"
                   for item in report["conflicts"]), report["conflicts"]

        # A record only they have, on an incident we both have: new, still refused.
        source.create_resource(incident_id, {"callsign": "Tender 3", "unit_type": "water_tender"}, "LOG")
        extra = source.export_bundle(incident_id)
        report = target.preview_import(extra)
        assert report["classifications"]["new"] >= 1, report["classifications"]
        assert report["can_apply"] is False

        # Every one of those previews is read-only, and every apply is refused,
        # so the only local change is the one *we* made through the store.
        for candidate in (original, ahead, divergent, extra):
            try:
                target.import_bundle(candidate, "Planning")
                raise AssertionError("existing-incident package was applied")
            except PackageConflict as exc:
                assert exc.report["can_apply"] is False
        current = _stable(target.export_bundle(incident_id))
        assert len(current["resources"]) == 1, "a refused import added a resource"
        assert current["resources"][0]["assignment"] == "West flank"
        assert current["features"]["features"][0]["properties"]["status"] == "planned"
        assert len(current["operational_periods"]) == len(settled["operational_periods"])
    finally:
        source_db.close(); target_db.close()


def check_structurally_broken_packages_are_rejected(root: Path) -> None:
    """A package must describe a self-contained graph. Anything pointing outside
    itself, duplicated, or of an unknown shape is rejected before any write."""
    source_db, source, built = _seed_source(root, "broken-source")
    target_db = Database(root / "broken-target.sqlite3")
    try:
        incident_id = built["incident"]["id"]
        valid = source.export_bundle(incident_id)
        target = OperationsStore(target_db)
        assert target.preview_import(valid)["can_apply"] is True

        def broken(mutate) -> dict:
            clone = copy.deepcopy(valid)
            mutate(clone)
            return clone

        def drop_periods(bundle: dict) -> None:
            bundle["operational_periods"] = []

        def foreign_scenario(bundle: dict) -> None:
            bundle["scenarios"][0]["incident_id"] = _id()

        def duplicate_feature(bundle: dict) -> None:
            bundle["features"]["features"].append(copy.deepcopy(bundle["features"]["features"][0]))

        def unknown_feature_type(bundle: dict) -> None:
            bundle["features"]["features"][0]["properties"]["feature_type"] = "portal_to_mars"

        def geometry_mismatch(bundle: dict) -> None:
            bundle["features"]["features"][0]["geometry"] = {"type": "Point", "coordinates": [10, 48]}

        def foreign_resource(bundle: dict) -> None:
            bundle["resources"][0]["incident_id"] = _id()

        def foreign_audit(bundle: dict) -> None:
            bundle["audit_log"][0]["incident_id"] = _id()

        def resources_not_a_list(bundle: dict) -> None:
            bundle["resources"] = {"callsign": "Engine 2"}

        def features_not_a_collection(bundle: dict) -> None:
            bundle["features"] = {"type": "Nonsense", "features": []}

        def nameless_incident(bundle: dict) -> None:
            bundle["incident"]["name"] = ""

        for label, mutate, expected in (
            ("feature/scenario point at a period the package dropped", drop_periods,
             "feature relationship is outside package"),
            ("scenario belongs to another incident", foreign_scenario,
             "every scenario must belong to a packaged operational period"),
            ("duplicate feature id", duplicate_feature, "duplicate feature id"),
            ("unknown feature type", unknown_feature_type, "invalid feature type"),
            ("geometry does not match the feature type", geometry_mismatch,
             "tactical_line requires LineString geometry"),
            ("resource belongs to another incident", foreign_resource,
             "every resource must belong to the package incident"),
            ("audit entry belongs to another incident", foreign_audit,
             "audit entries require unique ids and must belong to the package incident"),
            ("resources is not an array", resources_not_a_list, "resources must be an array"),
            ("features is not a FeatureCollection", features_not_a_collection,
             "features must be a GeoJSON FeatureCollection"),
            ("incident has no name", nameless_incident, "incident id and name are required"),
        ):
            candidate = broken(mutate)
            report = target.preview_import(candidate)
            assert report["valid"] is False, f"accepted a package where {label}"
            assert report["can_apply"] is False and report["mode"] == "invalid", label
            assert any(expected in error for error in report["errors"]), (label, report["errors"])
            try:
                target.import_bundle(candidate, "Planning")
                raise AssertionError(f"applied a package where {label}")
            except PackageConflict as exc:
                assert exc.report["mode"] == "invalid", label

        # None of the rejected packages left anything behind.
        assert target.list_incidents(include_closed=True) == []
        # A package that is not even a dict of the right schema is rejected too.
        for junk in ({"schema": "nexfiremap-incident/2"}, {}, {"schema": None}):
            assert target.preview_import(junk)["mode"] == "invalid"
    finally:
        source_db.close(); target_db.close()


def check_embedded_source_files_are_hash_verified(root: Path) -> None:
    """Original field-import files travel inside the package as base64. Their
    recorded size and SHA-256 are re-checked on the way in, so a truncated or
    altered original is caught instead of being trusted later."""
    source_db, source, built = _seed_source(root, "blob-source")
    target_db = Database(root / "blob-target.sqlite3")
    try:
        incident_id = built["incident"]["id"]
        raw = b"name,latitude,longitude\nSpot fire,48.0,11.0\n" * 8
        bundle = source.export_bundle(incident_id)
        bundle["source_imports"] = [_source_import_record(incident_id, raw)]
        target = OperationsStore(target_db)
        assert target.preview_import(bundle)["can_apply"] is True

        def tampered(mutate) -> dict:
            clone = copy.deepcopy(bundle)
            mutate(clone["source_imports"][0])
            return clone

        cases = {
            "hash rewritten": lambda row: row.update({"sha256": hashlib.sha256(b"different").hexdigest()}),
            "size rewritten": lambda row: row.update({"size_bytes": len(raw) + 1}),
            "bytes truncated": lambda row: row.update(
                {"original_base64": base64.b64encode(raw[:-5]).decode("ascii")}),
            "not base64 at all": lambda row: row.update({"original_base64": "!!!! not base64 !!!!"}),
            "hash field removed": lambda row: row.pop("sha256"),
        }
        for label, mutate in cases.items():
            report = target.preview_import(tampered(mutate))
            assert report["valid"] is False, f"accepted a package whose source file had its {label}"
            assert any("source import" in error for error in report["errors"]), (label, report["errors"])
            try:
                target.import_bundle(tampered(mutate), "Planning")
                raise AssertionError(f"applied a package whose source file had its {label}")
            except PackageConflict:
                pass
        assert target.list_incidents(include_closed=True) == []

        # The untampered package still imports, and the original bytes survive
        # the round-trip through base64 exactly.
        assert target.import_bundle(bundle, "Planning")["imported"] is True
        round_tripped = target.export_bundle(incident_id)["source_imports"]
        assert len(round_tripped) == 1
        restored = base64.b64decode(round_tripped[0]["original_base64"])
        assert restored == raw
        assert hashlib.sha256(restored).hexdigest() == round_tripped[0]["sha256"]
        assert round_tripped[0]["size_bytes"] == len(raw)
        assert json.loads(json.dumps(round_tripped[0]["report"])) == {"accepted": 1}
    finally:
        source_db.close(); target_db.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        check_handover_round_trip_and_replay_refusal(root)
        check_colliding_record_ids_are_refused(root)
        check_existing_incident_conflicts_are_classified(root)
        check_structurally_broken_packages_are_rejected(root)
        check_embedded_source_files_are_hash_verified(root)
    print("Incident package import checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
