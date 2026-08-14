"""Offline operational workspace tests. No network or FIRMS key required.

Beyond the workspace walkthrough, this file carries the optimistic-concurrency
drills. Every mutable record in the operational store carries a `revision`, and
callers must send back the revision they last read; the store's promise is that
a stale write is *refused*, never silently applied over someone else's newer
edit. Because that promise is what stops two operators quietly overwriting each
other during an incident, it is exercised here as a real two-editor scenario
(both load, one commits, the other tries) across every aggregate that can raise
`RevisionConflict` - plus the narrow race the guarded `WHERE revision=?` clause
exists to catch, where the competing write lands *between* the revision check
and the update.
"""

from __future__ import annotations

import contextlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.operations import (
    NotFoundError,
    OperationsError,
    OperationsStore,
    RevisionConflict,
    SAFETY_CHECKS,
    _id,
    default_period,
)


# --------------------------------------------------------------- helpers


LINE = {"type": "LineString", "coordinates": [[11.5, 48.1], [11.6, 48.2]]}


def _conflict(call, *args) -> RevisionConflict:
    """Assert a write is refused as a revision conflict, and hand back the
    exception so the caller can inspect the authoritative record it carries."""
    try:
        call(*args)
    except RevisionConflict as exc:
        return exc
    raise AssertionError(f"{call.__name__} accepted a stale revision")


def _not_found(call, *args) -> None:
    try:
        call(*args)
    except NotFoundError:
        return
    raise AssertionError(f"{call.__name__} accepted an unknown record")


class _RaceLock:
    """Stands in for `Database._write_lock` so a competing operator's write can
    land in the window between the revision SELECT and the guarded UPDATE.

    The store re-reads and re-checks the revision inside the UPDATE's own
    WHERE clause precisely because that window exists; firing the competing
    write from the lock's `__enter__` reproduces it deterministically instead of
    hoping two threads interleave the right way."""

    def __init__(self, real, action) -> None:
        self._real, self._action, self.fired = real, action, False

    def __enter__(self):
        if not self.fired:
            self.fired = True
            self._action()
        return self._real.__enter__()

    def __exit__(self, *exc_info):
        return self._real.__exit__(*exc_info)


@contextlib.contextmanager
def _racing(db: Database, action):
    """Run a block with one competing write interposed into the next guarded
    write's critical section."""
    real = db._write_lock
    lock = _RaceLock(real, action)
    db._write_lock = lock
    try:
        yield lock
    finally:
        db._write_lock = real


def _workspace(db: Database) -> dict:
    """One incident with a period, scenario, resource and feature to fight over."""
    store = OperationsStore(db)
    incident = store.create_incident({"name": "Shared Fire", "center_lat": 48.1, "center_lon": 11.5}, "IC-A")
    period = store.create_period(incident["id"], default_period(), "IC-A")
    scenario = store.create_scenario(incident["id"], period["id"], {"name": "Plan A", "kind": "primary"}, "IC-A")
    resource = store.create_resource(incident["id"], {"callsign": "Engine 1", "unit_type": "engine"}, "LOG")
    feature = store.create_feature(incident["id"], {
        "period_id": period["id"], "scenario_id": scenario["id"], "feature_type": "tactical_line",
        "title": "North machine line", "status": "proposed", "geometry": LINE,
    }, "DIV-A")
    return {"store": store, "incident": incident, "period": period,
            "scenario": scenario, "resource": resource, "feature": feature}


# ----------------------------------------------------------------- drills


def check_operational_workspace(root: Path) -> None:
    """Workspace walkthrough: create, edit, plan, approve, snapshot and diff."""
    db = Database(root / "operations.sqlite3")
    try:
        store = OperationsStore(db)
        incident = store.create_incident({"name": "Pine Ridge", "center_lat": 48.1, "center_lon": 11.5}, "IC-1")
        period = store.create_period(incident["id"], default_period(), "IC-1")
        scenario = store.create_scenario(incident["id"], period["id"], {"name": "Plan A", "kind": "primary"}, "IC-1")
        feature = store.create_feature(incident["id"], {
            "period_id": period["id"], "scenario_id": scenario["id"], "feature_type": "tactical_line",
            "title": "North machine line", "status": "proposed",
            "geometry": LINE,
            "properties": {"method": "machine_line", "priority": "high"},
        }, "DIV-N")
        assert feature["properties"]["revision"] == 1
        assert store.list_features(incident["id"], period["id"], scenario["id"])[0]["id"] == feature["id"]
        updated = store.update_feature(incident["id"], feature["id"], {"status": "under_construction"}, 1, "DIV-N")
        assert updated["properties"]["revision"] == 2
        updated = store.update_feature(incident["id"], feature["id"], {
            "valid_from": "2026-08-12T20:00:00+00:00", "valid_to": "2026-08-13T02:00:00+00:00",
            "properties": {"objective": "Hold the north flank", "responsible_unit": "Division North",
                           "assigned_resources": "Dozer 1, Engine 4", "priority": "high",
                           "required_equipment": "Dozer, lookout", "water_requirement": "Engine relay",
                           "prerequisites": "Evacuation confirmed", "hazards": "Rolling material",
                           "escape_route": "Quarry road", "safety_zone": "Quarry",
                           "communications_channel": "TAC 3", "notes": "Review at 22:00"},
        }, 2, "DIV-N")
        assert updated["properties"]["revision"] == 3
        assert updated["properties"]["objective"] == "Hold the north flank"
        assert updated["properties"]["communications_channel"] == "TAC 3"
        _conflict(store.update_feature, incident["id"], feature["id"], {"status": "completed"}, 1, "stale")
        try:
            store.create_feature(incident["id"], {"period_id": period["id"], "feature_type": "spot_fire",
                "status": "confirmed", "geometry": {"type": "LineString", "coordinates": [[11, 48], [12, 49]]}})
            raise AssertionError("wrong geometry accepted")
        except OperationsError:
            pass
        try:
            store.approve_scenario(incident["id"], scenario["id"], "IC-1")
            raise AssertionError("unreviewed plan approved")
        except OperationsError:
            pass
        store.set_safety_checks(incident["id"], period["id"], scenario["id"],
                                [{"key": key, "checked": True} for key, _ in SAFETY_CHECKS], "SO-1")
        approved = store.approve_scenario(incident["id"], scenario["id"], "IC-1")
        assert approved["status"] == "approved" and approved["safety_warnings"] == []
        incident = store.update_incident(incident["id"], {"notes": "Night shift", "status": "contained"}, 1, "IC-1")
        assert incident["revision"] == 2 and incident["status"] == "contained"
        period = store.update_period(incident["id"], period["id"], {"objectives": "Hold north flank", "status": "active"}, 1, "PLANS-1")
        assert period["revision"] == 2 and period["status"] == "active"
        _conflict(store.update_period, incident["id"], period["id"], {"objectives": "stale"}, 1, "stale")
        scenario = store.update_scenario(incident["id"], scenario["id"], {"assumptions": "Wind backs after midnight"}, approved["revision"], "FBAN-1")
        assert scenario["revision"] == approved["revision"] + 1 and scenario["status"] == "draft"
        assert scenario["approved_by"] is None and scenario["approved_at"] is None
        _conflict(store.update_scenario, incident["id"], scenario["id"], {"assumptions": "stale"}, approved["revision"], "stale")
        _conflict(store.update_incident, incident["id"], {"notes": "stale overwrite"}, 1, "stale")
        resource = store.create_resource(incident["id"], {"callsign": "Engine 4", "unit_type": "engine", "crew_size": 4})
        resource = store.update_resource(incident["id"], resource["id"], {
            "status": "working", "assignment": "North flank", "water_capacity_l": 2500,
        }, 1, "DIV-N")
        assert resource["revision"] == 2 and resource["status"] == "working"
        _conflict(store.update_resource, incident["id"], resource["id"], {"status": "returning"}, 1, "stale")
        bundle = store.export_bundle(incident["id"])
        assert bundle["schema"] == "nexfiremap-incident/1" and len(bundle["features"]["features"]) == 1
        assert len(bundle["audit_log"]) >= 7
        assert any(item["entity_type"] == "resource" and item["action"] == "update" and item["actor"] == "DIV-N"
                   for item in bundle["audit_log"])
        snapshot = store.create_snapshot(incident["id"], "Handover 1", period["id"], "operational", "IC-1")
        assert snapshot["name"] == "Handover 1"
        unchanged = store.compare_snapshots(incident["id"], snapshot["id"])
        assert unchanged["counts"]["added"] == 0 and unchanged["counts"]["changed"] == 0
        deleted = store.delete_feature(incident["id"], feature["id"], 3, "DIV-N")
        assert deleted["properties"]["deleted_at"] is not None
        comparison = store.compare_snapshots(incident["id"], snapshot["id"])
        assert comparison["counts"]["changed"] >= 1
        assert any(item["entity_type"] == "feature" and item["id"] == feature["id"] and
                   item["classification"] == "changed" for item in comparison["changes"])
        snapshot2 = store.create_snapshot(incident["id"], "Handover 2", period["id"], "operational", "IC-1")
        between = store.compare_snapshots(incident["id"], snapshot["id"], snapshot2["id"])
        assert between["right_is_current"] is False
        assert any(item["entity_type"] == "feature" for item in between["changes"])
        other_incident = store.create_incident({"name": "Other incident"}, "test")
        other_snapshot = store.create_snapshot(other_incident["id"], "Other", None, "operational", "test")
        try:
            store.compare_snapshots(incident["id"], snapshot["id"], other_snapshot["id"])
            raise AssertionError("cross-incident snapshot comparison accepted")
        except OperationsError:
            pass
        assert store.list_features(incident["id"]) == []
        assert len(store.list_features(incident["id"], include_deleted=True)) == 1
    finally:
        db.close()


def check_two_editors_never_lose_a_write(root: Path) -> None:
    """Two operators load the same record; the slower one's write is refused.

    Runs the same scenario against every aggregate that can raise
    RevisionConflict - incident, operational period, scenario, resource, and
    both feature mutators - and checks the three things that make the refusal
    trustworthy: the loser's edit is rejected, the winner's edit is what the
    store still holds afterwards, and the exception carries the authoritative
    record so the loser can be shown what actually changed."""
    db = Database(root / "two-editors.sqlite3")
    try:
        built = _workspace(db)
        # Two independent stores over the one database, exactly as two
        # concurrent requests in the running server would be.
        alice, bob = OperationsStore(db), OperationsStore(db)
        iid = built["incident"]["id"]
        period_id, scenario_id = built["period"]["id"], built["scenario"]["id"]
        resource_id, feature_id = built["resource"]["id"], built["feature"]["id"]

        # --- incident -------------------------------------------------
        seen = alice.get_incident(iid)["revision"]
        bob.update_incident(iid, {"notes": "Bob: wind shift expected 21:00"}, seen, "IC-B")
        exc = _conflict(alice.update_incident, iid, {"notes": "Alice: quiet night expected"}, seen, "IC-A")
        assert exc.entity["revision"] == seen + 1
        assert exc.entity["notes"] == "Bob: wind shift expected 21:00"
        assert alice.get_incident(iid)["notes"] == "Bob: wind shift expected 21:00"

        # --- operational period ---------------------------------------
        seen = alice.list_periods(iid)[0]["revision"]
        bob.update_period(iid, period_id, {"objectives": "Bob: hold the ridge road"}, seen, "PLANS-B")
        exc = _conflict(alice.update_period, iid, period_id, {"objectives": "Alice: fall back"}, seen, "PLANS-A")
        assert exc.entity["revision"] == seen + 1
        assert exc.entity["objectives"] == "Bob: hold the ridge road"
        assert alice.list_periods(iid)[0]["objectives"] == "Bob: hold the ridge road"

        # --- scenario -------------------------------------------------
        seen = alice.list_scenarios(period_id)[0]["revision"]
        bob.update_scenario(iid, scenario_id, {"assumptions": "Bob: wind backs after midnight"}, seen, "FBAN-B")
        exc = _conflict(alice.update_scenario, iid, scenario_id, {"assumptions": "Alice: wind holds"}, seen, "FBAN-A")
        assert exc.entity["revision"] == seen + 1
        assert exc.entity["assumptions"] == "Bob: wind backs after midnight"
        assert alice.list_scenarios(period_id)[0]["assumptions"] == "Bob: wind backs after midnight"

        # --- resource -------------------------------------------------
        seen = alice.list_resources(iid)[0]["revision"]
        bob.update_resource(iid, resource_id, {"assignment": "Bob: structure protection"}, seen, "DIV-B")
        exc = _conflict(alice.update_resource, iid, resource_id, {"assignment": "Alice: rehab"}, seen, "DIV-A")
        assert exc.entity["revision"] == seen + 1
        assert exc.entity["assignment"] == "Bob: structure protection"
        assert alice.list_resources(iid)[0]["assignment"] == "Bob: structure protection"

        # --- feature update (its own inline revision check) -----------
        seen = alice.list_features(iid)[0]["properties"]["revision"]
        bob.update_feature(iid, feature_id, {"title": "Bob: north dozer line"}, seen, "DIV-B")
        exc = _conflict(alice.update_feature, iid, feature_id, {"title": "Alice: north handline"}, seen, "DIV-A")
        assert exc.entity["properties"]["revision"] == seen + 1
        assert exc.entity["properties"]["title"] == "Bob: north dozer line"
        assert alice.list_features(iid)[0]["properties"]["title"] == "Bob: north dozer line"

        # --- feature delete -------------------------------------------
        seen = alice.list_features(iid)[0]["properties"]["revision"]
        bob.update_feature(iid, feature_id, {"status": "under_construction"}, seen, "DIV-B")
        exc = _conflict(alice.delete_feature, iid, feature_id, seen, "DIV-A")
        assert exc.entity["properties"]["revision"] == seen + 1
        # The stale delete did not take effect: the feature is still live.
        live = alice.list_features(iid)
        assert len(live) == 1 and live[0]["properties"]["deleted_at"] is None
        assert live[0]["properties"]["status"] == "under_construction"

        # Every refusal is silent in the audit log - only the writes that
        # actually landed were recorded.
        audit = alice.export_bundle(iid)["audit_log"]
        actors = {(item["entity_type"], item["actor"]) for item in audit if item["action"] in {"update", "delete"}}
        assert not any(actor.endswith("-A") for _, actor in actors), actors
        assert ("incident", "IC-B") in actors and ("feature", "DIV-B") in actors
    finally:
        db.close()


def check_lost_update_race_is_refused(root: Path) -> None:
    """The narrow race: a competing write commits *after* the revision was
    checked but *before* the guarded UPDATE runs.

    The UPDATE's own `WHERE revision=?` is what catches this, and the drill
    proves the important half - the competing write survives, and the write that
    lost the race is reported as a conflict rather than silently discarding it."""
    db = Database(root / "race.sqlite3")
    try:
        built = _workspace(db)
        store = built["store"]
        iid = built["incident"]["id"]
        resource_id, feature_id = built["resource"]["id"], built["feature"]["id"]

        # --- shared revision-checked path (incidents/periods/scenarios/resources)
        def competing_resource() -> None:
            db.conn.execute(
                "UPDATE incident_resources SET assignment=?,revision=? WHERE id=?",
                ("Bob won the race", 2, resource_id),
            )
            db.conn.commit()

        seen = store.list_resources(iid)[0]["revision"]
        assert seen == 1
        with _racing(db, competing_resource) as lock:
            exc = _conflict(store.update_resource, iid, resource_id, {"assignment": "Alice lost the race"}, seen, "DIV-A")
        assert lock.fired, "the competing write never ran - the race was not reproduced"
        assert exc.entity["revision"] == 2 and exc.entity["assignment"] == "Bob won the race"
        settled = store.list_resources(iid)[0]
        assert settled["assignment"] == "Bob won the race", "the racing write was rolled back"
        assert settled["revision"] == 2
        # The shared path rolls its failed statement back, so the connection is
        # left clean rather than holding an open write transaction.
        assert db.conn.in_transaction is False

        # --- feature update's inline path
        def competing_feature_title() -> None:
            db.conn.execute(
                "UPDATE tactical_features SET title=?,revision=? WHERE id=?",
                ("Bob won the race", 2, feature_id),
            )
            db.conn.commit()

        seen = store.list_features(iid)[0]["properties"]["revision"]
        assert seen == 1
        with _racing(db, competing_feature_title) as lock:
            exc = _conflict(store.update_feature, iid, feature_id, {"title": "Alice lost the race"}, seen, "DIV-A")
        assert lock.fired
        assert exc.entity["properties"]["revision"] == 2
        assert exc.entity["properties"]["title"] == "Bob won the race"
        assert store.list_features(iid)[0]["properties"]["title"] == "Bob won the race"

        # --- feature delete's inline path
        def competing_feature_status() -> None:
            db.conn.execute(
                "UPDATE tactical_features SET status=?,revision=? WHERE id=?",
                ("completed", 3, feature_id),
            )
            db.conn.commit()

        seen = store.list_features(iid)[0]["properties"]["revision"]
        assert seen == 2
        with _racing(db, competing_feature_status) as lock:
            exc = _conflict(store.delete_feature, iid, feature_id, seen, "DIV-A")
        assert lock.fired
        assert exc.entity["properties"]["revision"] == 3
        # The competing write stands and the feature was not soft-deleted.
        live = store.list_features(iid)
        assert len(live) == 1 and live[0]["properties"]["deleted_at"] is None
        assert live[0]["properties"]["status"] == "completed"

        # The store is still usable afterwards: a correctly-revisioned write
        # applies cleanly on top of the record the races left behind.
        applied = store.update_feature(iid, feature_id, {"title": "Agreed north line"}, 3, "DIV-B")
        assert applied["properties"]["revision"] == 4 and applied["properties"]["title"] == "Agreed north line"
    finally:
        db.close()


def check_unknown_records_are_not_found(root: Path) -> None:
    """An unknown id - or a real id addressed under the wrong incident - is a
    not-found, never an accidental write against someone else's incident."""
    db = Database(root / "not-found.sqlite3")
    try:
        built = _workspace(db)
        store = built["store"]
        iid = built["incident"]["id"]
        other = store.create_incident({"name": "Unrelated Fire"}, "IC-Z")["id"]
        ghost = _id()

        _not_found(store.update_incident, ghost, {"notes": "x"}, 1)
        _not_found(store.update_period, iid, ghost, {"objectives": "x"}, 1)
        _not_found(store.update_scenario, iid, ghost, {"assumptions": "x"}, 1)
        _not_found(store.update_resource, iid, ghost, {"assignment": "x"}, 1)
        _not_found(store.update_feature, iid, ghost, {"status": "confirmed"}, 1)
        _not_found(store.delete_feature, iid, ghost, 1)

        # Real records, addressed under an incident that does not own them.
        _not_found(store.update_period, other, built["period"]["id"], {"objectives": "x"}, 1)
        _not_found(store.update_scenario, other, built["scenario"]["id"], {"assumptions": "x"}, 1)
        _not_found(store.update_resource, other, built["resource"]["id"], {"assignment": "x"}, 1)
        _not_found(store.update_feature, other, built["feature"]["id"], {"status": "confirmed"}, 1)
        _not_found(store.delete_feature, other, built["feature"]["id"], 1)

        # None of that touched the records themselves.
        assert store.list_periods(iid)[0]["revision"] == 1
        assert store.list_resources(iid)[0]["revision"] == 1
        feature = store.list_features(iid)[0]["properties"]
        assert feature["revision"] == 1 and feature["deleted_at"] is None
        assert store.list_features(other) == []
    finally:
        db.close()


def check_no_op_update_keeps_the_revision(root: Path) -> None:
    """An update carrying nothing the store recognises is not a write: the
    revision must not advance (or every no-op save would invalidate every other
    operator's loaded copy) and no audit entry may be invented for it."""
    db = Database(root / "no-op.sqlite3")
    try:
        built = _workspace(db)
        store = built["store"]
        iid = built["incident"]["id"]
        feature_id = built["feature"]["id"]
        before_audit = len(store.export_bundle(iid)["audit_log"])

        before = store.get_incident(iid)
        same = store.update_incident(iid, {}, before["revision"], "IC-A")
        assert same["revision"] == before["revision"]
        assert same["updated_at"] == before["updated_at"]

        feature_before = store.list_features(iid)[0]["properties"]
        feature_same = store.update_feature(
            iid, feature_id, {"unrecognised_field": "ignored"}, feature_before["revision"], "DIV-A")
        assert feature_same["properties"]["revision"] == feature_before["revision"]
        assert feature_same["properties"]["updated_at"] == feature_before["updated_at"]

        assert len(store.export_bundle(iid)["audit_log"]) == before_audit

        # A no-op is still revision-checked: sending a stale number is refused
        # even when there is nothing to write.
        _conflict(store.update_incident, iid, {}, before["revision"] + 5, "IC-A")
        _conflict(store.update_feature, iid, feature_id, {}, feature_before["revision"] + 5, "DIV-A")
    finally:
        db.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        check_operational_workspace(root)
        check_two_editors_never_lose_a_write(root)
        check_lost_update_race_is_refused(root)
        check_unknown_records_are_not_found(root)
        check_no_op_update_keeps_the_revision(root)
    print("Operational workspace checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
