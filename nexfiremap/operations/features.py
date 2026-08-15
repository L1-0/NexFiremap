"""Tactical and observational map features - the drawn geometry of an incident.

This is the busiest write path in the operational record, and the only
aggregate whose rows are *soft*-deleted: `delete_feature` stamps
`deleted_at` and flips the status to "inactive" rather than removing the
row, because an id that appears in the audit log or in an already-shared
package must keep resolving to something forever.

The store takes an `IncidentStore` because a feature can only exist under
an incident that exists (see `base.AggregateStore` for why collaborators
are injected rather than reached through the facade). Its checks that a
feature's `period_id`/`scenario_id` belong to the same incident are
deliberately plain `SELECT 1` probes rather than calls into
`IncidentStore`/`ScenarioStore`: they are existence *constraints* on this
write, not reads of those aggregates, and expressing them as SQL keeps
this store from depending on the scenario aggregate at all (which in turn
lets `ScenarioStore` depend on *this* one for `copy_scenario`, with no
cycle).

`update_feature` and `delete_feature` re-implement the optimistic
concurrency protocol inline instead of using
`AggregateStore._apply_revision_update`, exactly as the original
single-class store did. That is not an oversight: `update_feature` has to
re-validate incoming geometry against the row's stored `feature_type`
before it can build the change set, and both methods return a GeoJSON
Feature (via `_feature`) rather than a raw row - including inside the
`RevisionConflict` they raise, so a conflicting caller gets the same
shape it asked for.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..db import Database
from .audit import AuditLog
from .base import AggregateStore
from .common import _clean_text, _feature, _id, _validate_geometry, utcnow
from .errors import NotFoundError, OperationsError, RevisionConflict
from .incidents import IncidentStore
from .vocab import FEATURE_STATUSES, FEATURE_TYPES, OBSERVATION_TYPES


def _normalise_symbology(props: dict[str, Any]) -> dict[str, Any]:
    """Coerce a feature's symbology hints to known values.

    `symbology_profile` decides which tactical symbol set a feature is drawn
    and legended with (DV 102, ICS/NFPA 170, or the neutral house set - see
    `nexfiremap/symbology.py`). It lives in `properties_json` rather than its
    own column, so this needs no migration, and the frontend has been writing
    it since before the profiles existed.

    An unknown profile is normalised to the default rather than rejected. That
    is deliberate: incident packages are merged between installations
    (`import_bundle`), so a package from a build that knows a profile this one
    does not must still import - a feature that renders in a neutral style is
    fine, a refused handover during an incident is not.
    """
    if not props:
        return {}
    result = dict(props)
    if "symbology_profile" in result:
        from ..symbology import normalise_profile
        result["symbology_profile"] = normalise_profile(result["symbology_profile"])
    if "symbol" in result:
        result["symbol"] = _clean_text(result["symbol"], 40) or None
    return result


class FeatureStore(AggregateStore):
    """CRUD and time-slicing over `tactical_features`."""

    def __init__(self, db: Database, audit: AuditLog, incidents: IncidentStore) -> None:
        super().__init__(db, audit)
        self.incidents = incidents

    def create_feature(self, incident_id: str, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Create a new tactical/observational map feature (revision 1) - an
        anchor point, perimeter line, evacuation area, etc. Geometry is
        validated against `feature_type` (see _validate_geometry) and, if a
        period/scenario is given, both must actually belong to this incident
        (and the scenario to the given period) so a feature can never point
        at a parent outside its own incident."""
        self.incidents.get_incident(incident_id)
        feature_type = _clean_text(data.get("feature_type"), 80)
        if feature_type not in FEATURE_TYPES:
            raise OperationsError("invalid feature_type")
        geometry = _validate_geometry(data.get("geometry"), feature_type)
        status = _clean_text(data.get("status"), 50) or "observed"
        if status not in FEATURE_STATUSES:
            raise OperationsError("invalid feature status")
        period_id = data.get("period_id") or None
        scenario_id = data.get("scenario_id") or None
        if period_id and not self.db.conn.execute(
            "SELECT 1 FROM operational_periods WHERE id=? AND incident_id=?", (period_id, incident_id)
        ).fetchone():
            raise OperationsError("period does not belong to incident")
        if scenario_id and not self.db.conn.execute(
            "SELECT 1 FROM plan_scenarios WHERE id=? AND incident_id=? AND period_id=?",
            (scenario_id, incident_id, period_id),
        ).fetchone():
            raise OperationsError("scenario does not belong to incident/period")
        now, feature_id = utcnow(), _id()
        props = _normalise_symbology(data.get("properties") if isinstance(data.get("properties"), dict) else {})
        values = {
            "id": feature_id, "incident_id": incident_id, "period_id": period_id,
            "scenario_id": scenario_id, "feature_type": feature_type,
            "title": _clean_text(data.get("title"), 300), "status": status,
            "geometry_json": json.dumps(geometry, separators=(",", ":")),
            "properties_json": json.dumps(props, separators=(",", ":")),
            "observed_at": _clean_text(data.get("observed_at"), 80) or None,
            "source": _clean_text(data.get("source"), 200) or None,
            "observer": _clean_text(data.get("observer"), 200) or None,
            "confidence": _clean_text(data.get("confidence"), 50) or None,
            "valid_from": _clean_text(data.get("valid_from"), 80) or None,
            "valid_to": _clean_text(data.get("valid_to"), 80) or None,
            "created_by": _clean_text(actor, 200) or "local operator",
            "created_at": now, "updated_at": now, "revision": 1,
        }
        with self.db._write_lock:
            self.db.conn.execute(
                "INSERT INTO tactical_features (id,incident_id,period_id,scenario_id,feature_type,title,status,geometry_json,properties_json,observed_at,source,observer,confidence,valid_from,valid_to,created_by,created_at,updated_at,revision) "
                "VALUES (:id,:incident_id,:period_id,:scenario_id,:feature_type,:title,:status,:geometry_json,:properties_json,:observed_at,:source,:observer,:confidence,:valid_from,:valid_to,:created_by,:created_at,:updated_at,:revision)", values,
            )
            payload = {**values, "geometry": geometry, "properties": props}
            payload.pop("geometry_json"); payload.pop("properties_json")
            self.audit.record(incident_id, "feature", feature_id, "create", 1, payload, actor)
            self.db.conn.commit()
        row = self.db.conn.execute("SELECT * FROM tactical_features WHERE id=?", (feature_id,)).fetchone()
        return _feature(row)

    def list_features(self, incident_id: str, period_id: str | None = None,
                      scenario_id: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
        """List an incident's features as GeoJSON Features, optionally
        narrowed to one period/scenario. Deleted features (see
        delete_feature) are soft-deleted rows and excluded by default so the
        normal map/API views never see them."""
        where, params = ["incident_id=?"], [incident_id]
        if period_id:
            where.append("period_id=?"); params.append(period_id)
        if scenario_id:
            where.append("scenario_id=?"); params.append(scenario_id)
        if not include_deleted:
            where.append("deleted_at IS NULL")
        rows = self.db.conn.execute(
            f"SELECT * FROM tactical_features WHERE {' AND '.join(where)} ORDER BY updated_at DESC", params
        ).fetchall()
        return [_feature(r) for r in rows]

    def progression(self, incident_id: str, from_time: str, to_time: str) -> dict[str, Any]:
        """Build a before/after/new-since view of observed fire behaviour
        (see OBSERVATION_TYPES) between two points in time, for animating or
        reviewing how the incident developed. `from_time`/`to_time` must be
        timezone-aware ISO 8601 strings so comparisons against stored
        timestamps are unambiguous regardless of the reporting timezone."""
        self.incidents.get_incident(incident_id)
        try:
            start = datetime.fromisoformat(from_time.replace("Z", "+00:00"))
            end = datetime.fromisoformat(to_time.replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError
        except (AttributeError, ValueError) as exc:
            raise OperationsError("progression times must be timezone-aware ISO 8601") from exc
        if end <= start:
            raise OperationsError("progression end must be after start")
        observations = [item for item in self.list_features(incident_id) if
                        item["properties"].get("feature_type") in OBSERVATION_TYPES]

        def timestamp(item: dict[str, Any]) -> datetime:
            # Prefer the reported observation time; fall back to when the
            # record was entered if the observer didn't supply one.
            raw = item["properties"].get("observed_at") or item["properties"].get("created_at")
            try:
                value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            except ValueError:
                return datetime.min.replace(tzinfo=timezone.utc)

        before = [item for item in observations if timestamp(item) <= start]
        after = [item for item in observations if timestamp(item) <= end]
        new = [item for item in observations if start < timestamp(item) <= end]
        return {
            "schema": "nexfiremap-progression/1", "incident_id": incident_id,
            "from_time": start.isoformat(), "to_time": end.isoformat(),
            "from": {"type": "FeatureCollection", "features": before},
            "to": {"type": "FeatureCollection", "features": after},
            "new_since": {"type": "FeatureCollection", "features": new},
            "counts": {"from": len(before), "to": len(after), "new_since": len(new)},
        }

    def update_feature(self, incident_id: str, feature_id: str, data: dict[str, Any],
                       expected_revision: int, actor: str = "local operator") -> dict[str, Any]:
        """Patch a feature's editable fields (and optionally its geometry/
        properties), revision-checked against `expected_revision`. This
        method inlines its own optimistic-concurrency UPDATE rather than
        going through _apply_revision_update because it needs to re-validate
        geometry against the feature's existing feature_type first."""
        current_row = self.db.conn.execute(
            "SELECT * FROM tactical_features WHERE id=? AND incident_id=?", (feature_id, incident_id)
        ).fetchone()
        if current_row is None:
            raise NotFoundError("feature not found")
        if int(current_row["revision"]) != expected_revision:
            raise RevisionConflict(_feature(current_row))
        current = dict(current_row)
        allowed = {"title", "status", "observed_at", "source", "observer", "confidence", "valid_from", "valid_to"}
        changes: dict[str, Any] = {}
        for key in allowed:
            if key in data:
                value = _clean_text(data[key], 300 if key == "title" else 200)
                if key != "title":
                    value = value or None
                changes[key] = value
        if "status" in changes and changes["status"] not in FEATURE_STATUSES:
            raise OperationsError("invalid feature status")
        if "properties" in data:
            if not isinstance(data["properties"], dict):
                raise OperationsError("properties must be an object")
            changes["properties_json"] = json.dumps(
                _normalise_symbology(data["properties"]), separators=(",", ":"))
        if "geometry" in data:
            geometry = _validate_geometry(data["geometry"], current["feature_type"])
            changes["geometry_json"] = json.dumps(geometry, separators=(",", ":"))
        if not changes:
            return _feature(current_row)
        changes["updated_at"], changes["revision"] = utcnow(), expected_revision + 1
        set_sql = ",".join(f"{key}=?" for key in changes)
        with self.db._write_lock:
            cur = self.db.conn.execute(
                f"UPDATE tactical_features SET {set_sql} WHERE id=? AND revision=?",
                [*changes.values(), feature_id, expected_revision],
            )
            if cur.rowcount != 1:
                # Another writer updated (and bumped) the row between our
                # SELECT and this UPDATE - roll back before raising, same as
                # the shared _apply_revision_update path (base.py), so this
                # doesn't leave an open deferred-write transaction holding a
                # RESERVED lock that could block another writer connection
                # until this one's next commit/rollback.
                fresh = self.db.conn.execute("SELECT * FROM tactical_features WHERE id=?", (feature_id,)).fetchone()
                self.db.conn.rollback()
                raise RevisionConflict(_feature(fresh))
            fresh = self.db.conn.execute("SELECT * FROM tactical_features WHERE id=?", (feature_id,)).fetchone()
            self.audit.record(incident_id, "feature", feature_id, "update", expected_revision + 1, _feature(fresh), actor)
            self.db.conn.commit()
        return _feature(fresh)

    def delete_feature(self, incident_id: str, feature_id: str, expected_revision: int,
                       actor: str = "local operator") -> dict[str, Any]:
        """Soft-delete a feature: mark it inactive and stamp deleted_at rather
        than removing the row, so the audit trail and any package that
        already references this feature's id stay consistent. Revision-
        checked like the other mutators; see RevisionConflict."""
        row = self.db.conn.execute(
            "SELECT * FROM tactical_features WHERE id=? AND incident_id=?", (feature_id, incident_id)
        ).fetchone()
        if row is None:
            raise NotFoundError("feature not found")
        if int(row["revision"]) != expected_revision:
            raise RevisionConflict(_feature(row))
        now, revision = utcnow(), expected_revision + 1
        with self.db._write_lock:
            cur = self.db.conn.execute(
                "UPDATE tactical_features SET status='inactive',deleted_at=?,updated_at=?,revision=? WHERE id=? AND revision=?",
                (now, now, revision, feature_id, expected_revision),
            )
            if cur.rowcount != 1:
                # See update_feature's identical comment above.
                fresh = self.db.conn.execute("SELECT * FROM tactical_features WHERE id=?", (feature_id,)).fetchone()
                self.db.conn.rollback()
                raise RevisionConflict(_feature(fresh))
            fresh = self.db.conn.execute("SELECT * FROM tactical_features WHERE id=?", (feature_id,)).fetchone()
            self.audit.record(incident_id, "feature", feature_id, "delete", revision, _feature(fresh), actor)
            self.db.conn.commit()
        return _feature(fresh)
