"""Offline-first incident-command records for NexFiremap.

The analytical map is useful without this module, but operational use needs a
durable distinction between observations, plans and resources.  This store is
intentionally SQLite-only and dependency-free.  UUID identifiers and integer
revisions allow incident packages edited on disconnected machines to be
compared without silently accepting "last file copied wins".
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import uuid4

from .db import Database
from .provenance import from_job


# ------------------------------------------------------------ domain vocabulary
# The fixed vocabularies below are enforced on every write (see the validators
# in OperationsStore and the module-level helpers) so that a package produced
# on one machine can be merged into another without silently inventing new
# enum values. Treat these tuples/sets as the single source of truth: add a
# feature type, status, etc. here first, then teach the UI about it.
SAFETY_CHECKS: tuple[tuple[str, str], ...] = (
    ("hazards", "Hazards identified"),
    ("lookouts", "Lookouts assigned"),
    ("communications", "Communications plan recorded"),
    ("escape_routes", "Escape routes identified"),
    ("safety_zones", "Safety zones identified"),
    ("medical", "Medical extraction route recorded"),
    ("alternative_access", "Alternative access and egress checked"),
    ("withdrawal_triggers", "Withdrawal trigger points defined"),
    ("weather_update", "Weather and fire-behaviour update is current"),
)

POINT_TYPES = {
    "anchor_point", "lookout", "safety_zone", "trigger_point", "hazard",
    "staging_area", "command_post", "drop_point", "water_source",
    "restricted_water_source", "helibase", "helispot", "dip_site",
    "weather_station", "communications_repeater", "road_closure",
    "resource_position",
    "critical_value", "spot_fire", "smoke_report", "wind_observation",
}
LINE_TYPES = {
    "fire_perimeter", "active_edge", "inactive_edge", "tactical_line",
    "escape_route", "division_boundary", "branch_boundary", "spread_arrow",
    "arrival_time_line", "road_restriction",
}
AREA_TYPES = {
    "confirmed_perimeter", "forecast_perimeter", "evacuation_area",
    "structure_protection_area", "safety_zone_area", "burn_area",
    "uncertainty_area",
}
FEATURE_TYPES = POINT_TYPES | LINE_TYPES | AREA_TYPES
# Subset of feature types that represent an observed fact about fire behaviour
# (as opposed to a plan/resource marker); used by progression() to build a
# time-sliced view of what was known to be true at a given moment.
OBSERVATION_TYPES = {
    "confirmed_perimeter", "fire_perimeter", "active_edge", "inactive_edge",
    "spot_fire", "smoke_report", "wind_observation", "burn_area",
}

SCENARIO_KINDS = {"primary", "contingency", "alternative", "worst_case"}
SCENARIO_STATUSES = {"draft", "approved", "retired"}
INCIDENT_STATUSES = {"active", "contained", "closed"}
PERIOD_STATUSES = {"draft", "active", "closed"}
RESOURCE_STATUSES = {"available", "assigned", "working", "returning", "unavailable"}
FEATURE_STATUSES = {
    "unconfirmed", "observed", "confirmed", "proposed", "planned",
    "under_construction", "completed", "held", "breached", "abandoned",
    "patrol", "mop_up", "inactive", "active",
}


# ----------------------------------------------------------------------- errors
class OperationsError(ValueError):
    """A request cannot be applied to the operational record."""


class NotFoundError(OperationsError):
    """The referenced incident/period/scenario/feature/... row does not exist
    (or does not belong to the parent it was requested under)."""
    pass


class RevisionConflict(OperationsError):
    """Raised when a write's `expected_revision` no longer matches the row's
    current `revision`. This is the optimistic-concurrency signal used
    throughout the store: every mutable row carries a monotonically
    increasing `revision`, callers must load-then-send-back that number, and
    a mismatch here means someone else (another operator, another device)
    changed the record first. `entity` carries the current, authoritative
    row so the caller can show the operator what changed instead of just
    failing."""

    def __init__(self, entity: dict[str, Any]) -> None:
        super().__init__("record changed since it was loaded")
        self.entity = entity


class PackageConflict(OperationsError):
    """Raised by import_bundle() when an incoming incident package overlaps
    an existing incident and cannot be merged automatically. `report` is the
    same structure preview_import() returns, so the caller can present the
    conflicts for manual resolution rather than losing data by guessing."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("incident package cannot be applied without conflict resolution")
        self.report = report


# ---------------------------------------------------------------- small helpers
def utcnow() -> str:
    """Current UTC time as an ISO 8601 string, second precision. Used for every
    created_at/updated_at/changed_at stamp so audit trails and revision
    history sort and compare consistently regardless of the host machine's
    local timezone."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id() -> str:
    """New random identifier for a record. UUIDs (rather than autoincrement
    ids) let two disconnected installations generate records independently
    without colliding when their packages are later merged."""
    return str(uuid4())


def _clean_text(value: Any, limit: int = 10000) -> str:
    """Coerce to a trimmed string and cap its length. Applied to every
    user-supplied text field so free-text notes can't blow past sane storage
    limits or carry stray whitespace into equality/audit comparisons."""
    return str(value or "").strip()[:limit]


def _json_load(value: str | None, default: Any) -> Any:
    """Best-effort JSON decode that falls back to `default` instead of
    raising, since stored JSON blobs (geometry/properties/payload columns)
    should never be allowed to take down a read path."""
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _plain(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite3.Row to a plain dict, or pass through None."""
    return dict(row) if row is not None else None


def _feature(row: sqlite3.Row) -> dict[str, Any]:
    """Reassemble a tactical_features row into a GeoJSON Feature. Geometry and
    free-form properties are stored as JSON columns for flexibility, but
    everything else on the row (status, revision, timestamps, ...) is also
    folded into `properties` so API consumers only ever deal with one
    GeoJSON Feature shape rather than a database row shape."""
    data = dict(row)
    geometry = _json_load(data.pop("geometry_json", None), None)
    properties = _json_load(data.pop("properties_json", None), {})
    return {"type": "Feature", "id": data["id"], "geometry": geometry,
            "properties": {**properties, **data}}


def _validate_geometry(geometry: Any, feature_type: str) -> dict[str, Any]:
    """Validate that `geometry` is a well-formed GeoJSON geometry of the kind
    required by `feature_type` (point features need Point geometry, etc.),
    and that every coordinate is a plausible lon/lat(/altitude) position.
    Raises OperationsError with a human-readable reason on the first problem
    found; this is the single gate features pass through on create/update so
    bad geometry can never reach the map or a shared package."""
    if not isinstance(geometry, dict):
        raise OperationsError("geometry must be a GeoJSON geometry object")
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    expected = "Point" if feature_type in POINT_TYPES else (
        "LineString" if feature_type in LINE_TYPES else "Polygon"
    )
    if kind != expected:
        raise OperationsError(f"{feature_type} requires {expected} geometry")
    minimum = 2 if kind in {"Point", "LineString"} else 1
    if not isinstance(coordinates, list) or len(coordinates) < minimum:
        raise OperationsError(f"invalid {kind} coordinates")
    if kind == "Polygon":
        # GeoJSON polygon rings must be closed (first position == last) and
        # need at least 4 positions to describe a non-degenerate ring.
        if any(not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1] for ring in coordinates):
            raise OperationsError("polygon rings require four positions and must be closed")
        positions = [p for ring in coordinates for p in ring]
    else:
        positions = [coordinates] if kind == "Point" else coordinates
    for position in positions:
        if not isinstance(position, list) or len(position) not in {2, 3}:
            raise OperationsError("each GeoJSON position must contain longitude, latitude, and optional altitude")
        if any(not isinstance(value, (int, float)) for value in position):
            raise OperationsError("geometry coordinates must be numeric")
        if not -180 <= float(position[0]) <= 180 or not -90 <= float(position[1]) <= 90:
            raise OperationsError("geometry longitude/latitude is outside the valid range")
    return {"type": kind, "coordinates": coordinates}


class OperationsStore:
    """SQLite-backed store for incident-command operational data: incidents,
    operational periods, plan scenarios, tactical features, resources,
    safety checklists and their audit trail. All mutations go through
    revision-checked helpers (see _apply_revision_update) so concurrent edits
    from different operators/devices are detected instead of silently
    clobbered, and every mutation is mirrored into incident_audit_log so the
    incident record has a durable history of who changed what and when.
    """

    # ----------------------------------------------------------------- setup
    def __init__(self, db: Database) -> None:
        self.db = db
        with self.db._write_lock:
            row = self.db.conn.execute("SELECT value FROM meta WHERE key='installation_id'").fetchone()
            if row is None:
                # Generated once per database file and never changed; used to
                # tell "this installation's own records" apart from records
                # that arrived via an imported package (see export_bundle).
                self.db.conn.execute("INSERT INTO meta (key,value) VALUES ('installation_id',?)", (_id(),))
                self.db.conn.commit()

    @property
    def installation_id(self) -> str:
        """Stable identifier for this SQLite database, set once on first use.
        Recorded on every exported package so a re-imported bundle can be
        traced back to the machine it originated from."""
        return str(self.db.conn.execute("SELECT value FROM meta WHERE key='installation_id'").fetchone()[0])

    # ----------------------------------------------- revision-checked writes
    def _audit(
        self, incident_id: str, entity_type: str, entity_id: str,
        action: str, revision: int, payload: dict[str, Any], actor: str,
    ) -> None:
        """Append one row to incident_audit_log and bump the parent incident's
        updated_at. This is the append-only trail behind every create/update/
        delete/approve/import/copy action in this module; it is intentionally
        never rewritten, only ever appended to, so it stays a trustworthy
        record even across imported packages (see export_bundle/import_bundle).
        Caller is expected to be inside the write lock/transaction already."""
        changed_at = utcnow()
        self.db.conn.execute(
            "INSERT INTO incident_audit_log "
            "(id,incident_id,entity_type,entity_id,action,revision,actor,changed_at,payload_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (_id(), incident_id, entity_type, entity_id, action, revision,
             _clean_text(actor, 200) or "local operator", changed_at,
             json.dumps(payload, separators=(",", ":"))),
        )
        self.db.conn.execute(
            "UPDATE incidents SET updated_at=? WHERE id=?", (changed_at, incident_id)
        )

    def _apply_revision_update(
        self, table: str, entity_type: str, incident_id: str, entity_id: str,
        expected_revision: int, changes: dict[str, Any], *, incident_table: bool = False,
        actor: str = "local operator",
    ) -> dict[str, Any]:
        """Shared optimistic-concurrency update path used by most `update_*`
        methods. The caller must supply the revision it last read
        (`expected_revision`); if the stored row has since moved to a
        different revision, this raises RevisionConflict with the current row
        instead of applying the change, so an operator's edit never silently
        overwrites someone else's newer edit. The UPDATE statement itself
        also filters on `revision=?` as a second, race-proof check (belt and
        suspenders against another writer sneaking in between the SELECT
        above and this UPDATE); rowcount != 1 means that race happened, so we
        roll back and re-report the conflict against the now-current row.
        """
        owner_column = "id" if incident_table else "incident_id"
        row = self.db.conn.execute(
            f"SELECT * FROM {table} WHERE id=? AND {owner_column}=?", (entity_id, incident_id)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"{entity_type} not found")
        if int(row["revision"]) != expected_revision:
            raise RevisionConflict(dict(row))
        if not changes:
            return dict(row)
        changes["updated_at"] = utcnow()
        changes["revision"] = expected_revision + 1
        set_sql = ",".join(f"{key}=?" for key in changes)
        with self.db._write_lock:
            result = self.db.conn.execute(
                f"UPDATE {table} SET {set_sql} WHERE id=? AND revision=?",
                [*changes.values(), entity_id, expected_revision],
            )
            if result.rowcount != 1:
                # Another writer updated (and bumped) the row between our
                # SELECT and this UPDATE; surface the conflict instead of
                # silently losing that write.
                fresh = self.db.conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
                self.db.conn.rollback()
                raise RevisionConflict(dict(fresh))
            fresh = dict(self.db.conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone())
            self._audit(incident_id, entity_type, entity_id, "update", expected_revision + 1, fresh, actor)
            self.db.conn.commit()
        return fresh

    # ------------------------------------------------------------- incidents
    def create_incident(self, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Create a new incident record (revision 1) and log its creation.
        The incident is the root of everything else in this module -
        periods, scenarios, features, resources - so this is usually the
        first call made for a new fire."""
        name = _clean_text(data.get("name"), 300)
        if not name:
            raise OperationsError("incident name is required")
        now, incident_id = utcnow(), _id()
        values = {
            "id": incident_id, "name": name,
            "incident_number": _clean_text(data.get("incident_number"), 100) or None,
            "status": "active", "timezone": _clean_text(data.get("timezone"), 80) or "UTC",
            "center_lat": data.get("center_lat"), "center_lon": data.get("center_lon"),
            "notes": _clean_text(data.get("notes")), "created_at": now,
            "updated_at": now, "revision": 1,
        }
        if values["center_lat"] is not None and not -90 <= float(values["center_lat"]) <= 90:
            raise OperationsError("center_lat must be between -90 and 90")
        if values["center_lon"] is not None and not -180 <= float(values["center_lon"]) <= 180:
            raise OperationsError("center_lon must be between -180 and 180")
        with self.db._write_lock:
            self.db.conn.execute(
                "INSERT INTO incidents (id,name,incident_number,status,timezone,center_lat,center_lon,notes,created_at,updated_at,revision) "
                "VALUES (:id,:name,:incident_number,:status,:timezone,:center_lat,:center_lon,:notes,:created_at,:updated_at,:revision)", values,
            )
            self._audit(incident_id, "incident", incident_id, "create", 1, values, actor)
            self.db.conn.commit()
        return values

    def list_incidents(self, include_closed: bool = False) -> list[dict[str, Any]]:
        """List incidents, most recently updated first. Closed incidents are
        hidden by default to keep the day-to-day incident picker short."""
        clause = "" if include_closed else "WHERE status != 'closed'"
        return [dict(r) for r in self.db.conn.execute(
            f"SELECT * FROM incidents {clause} ORDER BY updated_at DESC"
        ).fetchall()]

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch one incident by id, or raise NotFoundError."""
        row = self.db.conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if row is None:
            raise NotFoundError("incident not found")
        return dict(row)

    def update_incident(self, incident_id: str, data: dict[str, Any], expected_revision: int,
                        actor: str = "local operator") -> dict[str, Any]:
        """Patch the incident's editable fields. Only keys present in `data`
        are touched; `expected_revision` must match the currently stored
        revision or this raises RevisionConflict (see _apply_revision_update)."""
        current = self.get_incident(incident_id)
        changes: dict[str, Any] = {}
        for key, limit in (("name", 300), ("incident_number", 100), ("timezone", 80), ("notes", 10000)):
            if key in data:
                value = _clean_text(data[key], limit)
                if key == "name" and not value:
                    raise OperationsError("incident name is required")
                changes[key] = value if key in {"name", "timezone", "notes"} else (value or None)
        if "status" in data:
            status = _clean_text(data["status"], 40)
            if status not in INCIDENT_STATUSES:
                raise OperationsError("invalid incident status")
            changes["status"] = status
        lat = data.get("center_lat", current["center_lat"])
        lon = data.get("center_lon", current["center_lon"])
        if lat is not None and not -90 <= float(lat) <= 90:
            raise OperationsError("center_lat must be between -90 and 90")
        if lon is not None and not -180 <= float(lon) <= 180:
            raise OperationsError("center_lon must be between -180 and 180")
        if "center_lat" in data: changes["center_lat"] = lat
        if "center_lon" in data: changes["center_lon"] = lon
        return self._apply_revision_update(
            "incidents", "incident", incident_id, incident_id, expected_revision, changes,
            incident_table=True, actor=actor,
        )

    # --------------------------------------------------- operational periods
    def create_period(self, incident_id: str, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Create a new operational period (revision 1) under an incident.
        Operational periods are the standard ICS time-boxing unit that
        scenarios, features and safety checklists are scoped to."""
        self.get_incident(incident_id)
        name = _clean_text(data.get("name"), 300)
        starts = _clean_text(data.get("starts_at"), 80)
        ends = _clean_text(data.get("ends_at"), 80)
        if not name or not starts or not ends:
            raise OperationsError("period name, starts_at and ends_at are required")
        try:
            if datetime.fromisoformat(ends.replace("Z", "+00:00")) <= datetime.fromisoformat(starts.replace("Z", "+00:00")):
                raise OperationsError("operational period must end after it starts")
        except (ValueError, TypeError) as exc:
            raise OperationsError("operational period times must be ISO 8601") from exc
        now, period_id = utcnow(), _id()
        values = {"id": period_id, "incident_id": incident_id, "name": name,
                  "starts_at": starts, "ends_at": ends, "status": "draft",
                  "objectives": _clean_text(data.get("objectives")),
                  "created_at": now, "updated_at": now, "revision": 1}
        with self.db._write_lock:
            self.db.conn.execute(
                "INSERT INTO operational_periods (id,incident_id,name,starts_at,ends_at,status,objectives,created_at,updated_at,revision) "
                "VALUES (:id,:incident_id,:name,:starts_at,:ends_at,:status,:objectives,:created_at,:updated_at,:revision)", values,
            )
            self._audit(incident_id, "operational_period", period_id, "create", 1, values, actor)
            self.db.conn.commit()
        return values

    def list_periods(self, incident_id: str) -> list[dict[str, Any]]:
        """List an incident's operational periods, most recent start first."""
        self.get_incident(incident_id)
        return [dict(r) for r in self.db.conn.execute(
            "SELECT * FROM operational_periods WHERE incident_id=? ORDER BY starts_at DESC", (incident_id,)
        ).fetchall()]

    def update_period(self, incident_id: str, period_id: str, data: dict[str, Any],
                      expected_revision: int, actor: str = "local operator") -> dict[str, Any]:
        """Patch an operational period's editable fields, revision-checked
        against `expected_revision`. Re-validates the start/end ordering
        using whichever of starts_at/ends_at end up in effect (either the
        incoming change or, if unchanged, the current stored value)."""
        row = self.db.conn.execute(
            "SELECT * FROM operational_periods WHERE id=? AND incident_id=?", (period_id, incident_id)
        ).fetchone()
        if row is None:
            raise NotFoundError("operational period not found")
        current = dict(row)
        changes: dict[str, Any] = {}
        for key, limit in (("name", 300), ("starts_at", 80), ("ends_at", 80), ("objectives", 10000)):
            if key in data:
                value = _clean_text(data[key], limit)
                if key in {"name", "starts_at", "ends_at"} and not value:
                    raise OperationsError(f"operational period {key} is required")
                changes[key] = value
        starts, ends = changes.get("starts_at", current["starts_at"]), changes.get("ends_at", current["ends_at"])
        try:
            if datetime.fromisoformat(ends.replace("Z", "+00:00")) <= datetime.fromisoformat(starts.replace("Z", "+00:00")):
                raise OperationsError("operational period must end after it starts")
        except (ValueError, TypeError) as exc:
            raise OperationsError("operational period times must be ISO 8601") from exc
        if "status" in data:
            status = _clean_text(data["status"], 40)
            if status not in PERIOD_STATUSES:
                raise OperationsError("invalid operational period status")
            changes["status"] = status
        return self._apply_revision_update(
            "operational_periods", "operational_period", incident_id, period_id,
            expected_revision, changes, actor=actor,
        )

    # ------------------------------------------------------------- scenarios
    def create_scenario(self, incident_id: str, period_id: str, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Create a new plan scenario (revision 1) under an operational
        period. A scenario is one hypothesis for how the period will play out
        (primary/contingency/alternative/worst_case, see SCENARIO_KINDS) and
        starts in "draft" status until it passes the safety approval
        workflow (see approve_scenario)."""
        period = self.db.conn.execute(
            "SELECT * FROM operational_periods WHERE id=? AND incident_id=?", (period_id, incident_id)
        ).fetchone()
        if period is None:
            raise NotFoundError("operational period not found")
        kind = _clean_text(data.get("kind"), 40) or "primary"
        if kind not in SCENARIO_KINDS:
            raise OperationsError("invalid scenario kind")
        name = _clean_text(data.get("name"), 300)
        if not name:
            raise OperationsError("scenario name is required")
        now, scenario_id = utcnow(), _id()
        values = {"id": scenario_id, "incident_id": incident_id, "period_id": period_id,
                  "name": name, "kind": kind, "status": "draft",
                  "description": _clean_text(data.get("description")),
                  "assumptions": _clean_text(data.get("assumptions")),
                  "created_at": now, "updated_at": now, "revision": 1}
        with self.db._write_lock:
            self.db.conn.execute(
                "INSERT INTO plan_scenarios (id,incident_id,period_id,name,kind,status,description,assumptions,created_at,updated_at,revision) "
                "VALUES (:id,:incident_id,:period_id,:name,:kind,:status,:description,:assumptions,:created_at,:updated_at,:revision)", values,
            )
            self._audit(incident_id, "scenario", scenario_id, "create", 1, values, actor)
            self.db.conn.commit()
        return values

    def list_scenarios(self, period_id: str) -> list[dict[str, Any]]:
        """List a period's scenarios, primary first, then contingency, then
        everything else, most recently updated within each group first."""
        return [dict(r) for r in self.db.conn.execute(
            "SELECT * FROM plan_scenarios WHERE period_id=? ORDER BY CASE kind WHEN 'primary' THEN 0 WHEN 'contingency' THEN 1 ELSE 2 END, updated_at DESC",
            (period_id,),
        ).fetchall()]

    # -------------------------------------------------- model run provenance
    def attach_model_run(self, incident_id: str, scenario_id: str, job_id: int,
                         actor: str = "local operator") -> dict[str, Any]:
        """Link a fire-behaviour model job's output to a scenario so the plan
        can show where its projected spread came from. Re-attaching the same
        (scenario_id, job_id) pair updates the existing link instead of
        duplicating it. Provenance is derived from the job via from_job() and
        raises OperationsError if the job isn't in a state that can be
        attached (e.g. still running or failed)."""
        scenario = self.db.conn.execute(
            "SELECT id FROM plan_scenarios WHERE id=? AND incident_id=?", (scenario_id, incident_id)
        ).fetchone()
        if scenario is None:
            raise NotFoundError("scenario not found")
        job = self.db.get_job(job_id)
        if job is None:
            raise NotFoundError("model job not found")
        try:
            provenance = from_job(dict(job))
        except ValueError as exc:
            raise OperationsError(str(exc)) from exc
        now, record_id = utcnow(), _id()
        with self.db._write_lock:
            existing = self.db.conn.execute(
                "SELECT id FROM incident_model_runs WHERE scenario_id=? AND job_id=?",
                (scenario_id, job_id),
            ).fetchone()
            if existing:
                record_id = str(existing["id"])
                self.db.conn.execute(
                    "UPDATE incident_model_runs SET provenance_json=?,attached_by=?,attached_at=? WHERE id=?",
                    (json.dumps(provenance, separators=(",", ":")), _clean_text(actor, 200), now, record_id),
                )
            else:
                self.db.conn.execute(
                    "INSERT INTO incident_model_runs (id,incident_id,scenario_id,job_id,model_kind,provenance_json,attached_by,attached_at) VALUES (?,?,?,?,?,?,?,?)",
                    (record_id, incident_id, scenario_id, job_id, provenance["model_kind"],
                     json.dumps(provenance, separators=(",", ":")), _clean_text(actor, 200), now),
                )
            self._audit(incident_id, "model_run", record_id, "attach", 1, provenance, actor)
            self.db.conn.commit()
        return {"id": record_id, "incident_id": incident_id, "scenario_id": scenario_id,
                "job_id": job_id, "model_kind": provenance["model_kind"], "provenance": provenance,
                "attached_by": _clean_text(actor, 200), "attached_at": now}

    def list_model_runs(self, incident_id: str, scenario_id: str | None = None) -> list[dict[str, Any]]:
        """List model runs attached to an incident, optionally filtered to one
        scenario, most recently attached first."""
        sql = "SELECT * FROM incident_model_runs WHERE incident_id=?"
        params: list[Any] = [incident_id]
        if scenario_id:
            sql += " AND scenario_id=?"; params.append(scenario_id)
        sql += " ORDER BY attached_at DESC"
        result = []
        for row in self.db.conn.execute(sql, params).fetchall():
            item = dict(row); item["provenance"] = json.loads(item.pop("provenance_json")); result.append(item)
        return result

    # -------------------------------------------------------- scenario edits
    def update_scenario(self, incident_id: str, scenario_id: str, data: dict[str, Any],
                        expected_revision: int, actor: str = "local operator") -> dict[str, Any]:
        """Patch a scenario's editable fields, revision-checked against
        `expected_revision`. Status cannot be set to "approved" here - that
        requires going through approve_scenario() so the safety checklist is
        reviewed - and any substantive edit (name/kind/description/
        assumptions) to a scenario that was already approved automatically
        demotes it back to "draft" and clears its approval, because an
        approval is a statement about the plan as it existed at approval
        time and must not silently keep covering a changed plan."""
        row = self.db.conn.execute(
            "SELECT * FROM plan_scenarios WHERE id=? AND incident_id=?", (scenario_id, incident_id)
        ).fetchone()
        if row is None:
            raise NotFoundError("scenario not found")
        current = dict(row)
        changes: dict[str, Any] = {}
        for key, limit in (("name", 300), ("description", 10000), ("assumptions", 10000)):
            if key in data:
                value = _clean_text(data[key], limit)
                if key == "name" and not value:
                    raise OperationsError("scenario name is required")
                changes[key] = value
        if "kind" in data:
            kind = _clean_text(data["kind"], 40)
            if kind not in SCENARIO_KINDS:
                raise OperationsError("invalid scenario kind")
            changes["kind"] = kind
        if "status" in data:
            status = _clean_text(data["status"], 40)
            if status not in SCENARIO_STATUSES:
                raise OperationsError("invalid scenario status")
            if status == "approved":
                raise OperationsError("use the safety approval workflow to approve a scenario")
            changes["status"] = status
        substantive = any(key in changes for key in ("name", "kind", "description", "assumptions"))
        if current["status"] == "approved" and substantive:
            # An approval certifies a specific plan; changing the plan's
            # substance invalidates that certification, so force it back
            # through the safety approval workflow rather than leaving a
            # stale "approved" label on an edited scenario.
            changes.update({"status": "draft", "approved_by": None, "approved_at": None,
                            "warning_acknowledged": 0})
        return self._apply_revision_update(
            "plan_scenarios", "scenario", incident_id, scenario_id,
            expected_revision, changes, actor=actor,
        )

    # ----------------------------------------------------- tactical features
    def create_feature(self, incident_id: str, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Create a new tactical/observational map feature (revision 1) - an
        anchor point, perimeter line, evacuation area, etc. Geometry is
        validated against `feature_type` (see _validate_geometry) and, if a
        period/scenario is given, both must actually belong to this incident
        (and the scenario to the given period) so a feature can never point
        at a parent outside its own incident."""
        self.get_incident(incident_id)
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
        props = data.get("properties") if isinstance(data.get("properties"), dict) else {}
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
            self._audit(incident_id, "feature", feature_id, "create", 1, payload, actor)
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
        self.get_incident(incident_id)
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
            changes["properties_json"] = json.dumps(data["properties"], separators=(",", ":"))
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
                fresh = self.db.conn.execute("SELECT * FROM tactical_features WHERE id=?", (feature_id,)).fetchone()
                raise RevisionConflict(_feature(fresh))
            fresh = self.db.conn.execute("SELECT * FROM tactical_features WHERE id=?", (feature_id,)).fetchone()
            self._audit(incident_id, "feature", feature_id, "update", expected_revision + 1, _feature(fresh), actor)
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
                fresh = self.db.conn.execute("SELECT * FROM tactical_features WHERE id=?", (feature_id,)).fetchone()
                raise RevisionConflict(_feature(fresh))
            fresh = self.db.conn.execute("SELECT * FROM tactical_features WHERE id=?", (feature_id,)).fetchone()
            self._audit(incident_id, "feature", feature_id, "delete", revision, _feature(fresh), actor)
            self.db.conn.commit()
        return _feature(fresh)

    # ---------------------------------- safety checklist & scenario approval
    def set_safety_checks(self, incident_id: str, period_id: str, scenario_id: str | None,
                          checks: Iterable[dict[str, Any]], actor: str = "local operator") -> list[dict[str, Any]]:
        """Upsert one or more entries in the operational-period (optionally
        scenario-scoped) safety checklist (see SAFETY_CHECKS for the fixed
        set of keys). This is plain upsert-by-key rather than
        revision-checked, since checklist items are independent booleans an
        operator ticks off rather than a single record edited as a whole."""
        if not self.db.conn.execute(
            "SELECT 1 FROM operational_periods WHERE id=? AND incident_id=?", (period_id, incident_id)
        ).fetchone():
            raise NotFoundError("operational period not found")
        valid = {key for key, _ in SAFETY_CHECKS}
        sid, now = scenario_id or "", utcnow()
        rows = []
        for item in checks:
            key = item.get("key")
            if key not in valid:
                raise OperationsError(f"unknown safety check: {key}")
            rows.append((period_id, sid, key, int(bool(item.get("checked"))),
                         _clean_text(item.get("details"), 1000), _clean_text(actor, 200), now))
        with self.db._write_lock:
            self.db.conn.executemany(
                "INSERT INTO safety_checks (period_id,scenario_id,check_key,checked,details,updated_by,updated_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(period_id,scenario_id,check_key) DO UPDATE SET checked=excluded.checked,details=excluded.details,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                rows,
            )
            self.db.conn.commit()
        return self.get_safety_checks(period_id, scenario_id)

    def get_safety_checks(self, period_id: str, scenario_id: str | None = None) -> list[dict[str, Any]]:
        """Return the full safety checklist for a period/scenario, always in
        SAFETY_CHECKS order with every key present (defaulting to unchecked)
        so callers never have to special-case a key that was never touched."""
        sid = scenario_id or ""
        stored = {r["check_key"]: dict(r) for r in self.db.conn.execute(
            "SELECT * FROM safety_checks WHERE period_id=? AND scenario_id=?", (period_id, sid)
        ).fetchall()}
        return [{"key": key, "label": label, "checked": bool(stored.get(key, {}).get("checked", 0)),
                 "details": stored.get(key, {}).get("details", "")}
                for key, label in SAFETY_CHECKS]

    def approve_scenario(self, incident_id: str, scenario_id: str, approver: str,
                         acknowledge_warnings: bool = False,
                         expected_revision: int | None = None) -> dict[str, Any]:
        """Approve a scenario, the terminal step of the safety workflow. If
        any SAFETY_CHECKS item is unticked, approval is refused unless the
        caller passes `acknowledge_warnings=True` - this forces an explicit,
        auditable decision to proceed with known gaps rather than letting an
        incomplete checklist block operations silently or be approved by
        accident. Which checks (if any) were outstanding at approval time is
        recorded both on the row (`warning_acknowledged`) and in the audit
        payload. `expected_revision` is optional here (unlike other
        mutators) since approval is commonly triggered from a checklist view
        that may not be tracking the scenario's own revision."""
        row = self.db.conn.execute(
            "SELECT * FROM plan_scenarios WHERE id=? AND incident_id=?", (scenario_id, incident_id)
        ).fetchone()
        if row is None:
            raise NotFoundError("scenario not found")
        if expected_revision is not None and int(row["revision"]) != expected_revision:
            raise RevisionConflict(dict(row))
        checks = self.get_safety_checks(row["period_id"], scenario_id)
        missing = [c for c in checks if not c["checked"]]
        if missing and not acknowledge_warnings:
            raise OperationsError("safety warnings must be reviewed and explicitly acknowledged")
        now, revision = utcnow(), int(row["revision"]) + 1
        with self.db._write_lock:
            result = self.db.conn.execute(
                "UPDATE plan_scenarios SET status='approved',approved_by=?,approved_at=?,warning_acknowledged=?,updated_at=?,revision=? WHERE id=? AND revision=?",
                (_clean_text(approver, 200) or "local operator", now, int(bool(missing)), now,
                 revision, scenario_id, int(row["revision"])),
            )
            if result.rowcount != 1:
                # Scenario moved (edited or approved elsewhere) between our
                # read and this write; don't approve a plan the operator
                # never actually saw.
                fresh = self.db.conn.execute("SELECT * FROM plan_scenarios WHERE id=?", (scenario_id,)).fetchone()
                self.db.conn.rollback()
                raise RevisionConflict(dict(fresh))
            fresh = dict(self.db.conn.execute("SELECT * FROM plan_scenarios WHERE id=?", (scenario_id,)).fetchone())
            self._audit(incident_id, "scenario", scenario_id, "approve", revision,
                        {**fresh, "missing_safety_checks": [c["key"] for c in missing]}, approver)
            self.db.conn.commit()
        fresh["safety_warnings"] = missing
        return fresh

    # ------------------------------------------------------------- resources
    def create_resource(self, incident_id: str, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Create a new incident resource (crew, engine, aircraft, ...) at
        revision 1. `callsign` and `unit_type` are the only required fields;
        everything else (position, capabilities, assignment) can be filled
        in as it becomes known."""
        self.get_incident(incident_id)
        callsign, unit_type = _clean_text(data.get("callsign"), 100), _clean_text(data.get("unit_type"), 100)
        if not callsign or not unit_type:
            raise OperationsError("callsign and unit_type are required")
        now, resource_id = utcnow(), _id()
        values = {"id": resource_id, "incident_id": incident_id, "callsign": callsign,
                  "unit_type": unit_type, "status": _clean_text(data.get("status"), 40) or "available",
                  "crew_size": data.get("crew_size"), "water_capacity_l": data.get("water_capacity_l"),
                  "capabilities": _clean_text(data.get("capabilities")), "assignment": _clean_text(data.get("assignment")),
                  "contact_channel": _clean_text(data.get("contact_channel"), 100),
                  "latitude": data.get("latitude"), "longitude": data.get("longitude"),
                  "position_at": data.get("position_at"), "created_at": now, "updated_at": now, "revision": 1}
        with self.db._write_lock:
            cols = ",".join(values); binds = ",".join(f":{k}" for k in values)
            self.db.conn.execute(f"INSERT INTO incident_resources ({cols}) VALUES ({binds})", values)
            self._audit(incident_id, "resource", resource_id, "create", 1, values, actor)
            self.db.conn.commit()
        return values

    def list_resources(self, incident_id: str) -> list[dict[str, Any]]:
        """List an incident's resources, alphabetical by callsign."""
        return [dict(r) for r in self.db.conn.execute(
            "SELECT * FROM incident_resources WHERE incident_id=? ORDER BY callsign", (incident_id,)
        ).fetchall()]

    def update_resource(self, incident_id: str, resource_id: str, data: dict[str, Any],
                        expected_revision: int, actor: str = "local operator") -> dict[str, Any]:
        """Patch a resource's editable fields, revision-checked against
        `expected_revision`. Numeric fields (crew size, water capacity,
        position) are range-checked together at the end so a single
        malformed value doesn't silently corrupt an otherwise-valid update."""
        row = self.db.conn.execute(
            "SELECT * FROM incident_resources WHERE id=? AND incident_id=?", (resource_id, incident_id)
        ).fetchone()
        if row is None:
            raise NotFoundError("resource not found")
        changes: dict[str, Any] = {}
        for key, limit in (("callsign", 100), ("unit_type", 100), ("capabilities", 10000),
                           ("assignment", 10000), ("contact_channel", 100), ("position_at", 80)):
            if key in data:
                value = _clean_text(data[key], limit)
                if key in {"callsign", "unit_type"} and not value:
                    raise OperationsError(f"resource {key} is required")
                changes[key] = value if key not in {"position_at"} else (value or None)
        if "status" in data:
            status = _clean_text(data["status"], 40)
            if status not in RESOURCE_STATUSES:
                raise OperationsError("invalid resource status")
            changes["status"] = status
        for key in ("crew_size", "water_capacity_l", "latitude", "longitude"):
            if key in data:
                changes[key] = data[key]
        try:
            if changes.get("crew_size") is not None and not 0 <= int(changes["crew_size"]) <= 1000:
                raise OperationsError("crew_size must be between 0 and 1000")
            if changes.get("water_capacity_l") is not None and float(changes["water_capacity_l"]) < 0:
                raise OperationsError("water_capacity_l cannot be negative")
            if changes.get("latitude") is not None and not -90 <= float(changes["latitude"]) <= 90:
                raise OperationsError("latitude must be between -90 and 90")
            if changes.get("longitude") is not None and not -180 <= float(changes["longitude"]) <= 180:
                raise OperationsError("longitude must be between -180 and 180")
        except (TypeError, ValueError) as exc:
            raise OperationsError("resource numeric fields are invalid") from exc
        return self._apply_revision_update(
            "incident_resources", "resource", incident_id, resource_id,
            expected_revision, changes, actor=actor,
        )

    # --------------------------------------------------------- scenario copy
    def copy_scenario(self, incident_id: str, scenario_id: str, target_period_id: str,
                      name: str, actor: str = "local operator") -> dict[str, Any]:
        """Clone a scenario (and all of its features) into a different
        operational period, e.g. to carry a contingency plan forward into the
        next period. The clone always starts fresh at revision 1 in "draft"
        status - approvals do not carry over, since the copy is a new plan
        that hasn't itself been reviewed. Runs as a single transaction so a
        partially-copied scenario can never be left behind on failure."""
        source = self.db.conn.execute(
            "SELECT * FROM plan_scenarios WHERE id=? AND incident_id=?", (scenario_id, incident_id)
        ).fetchone()
        target = self.db.conn.execute(
            "SELECT * FROM operational_periods WHERE id=? AND incident_id=?", (target_period_id, incident_id)
        ).fetchone()
        if source is None: raise NotFoundError("source scenario not found")
        if target is None: raise NotFoundError("target operational period not found")
        if source["period_id"] == target_period_id:
            raise OperationsError("target period must differ from source period")
        new_scenario_id, now = _id(), utcnow()
        source_features = self.list_features(incident_id, source["period_id"], scenario_id)
        # Pre-allocate every cloned feature's new id up front so that feature-
        # to-feature references ("links", e.g. a trigger point pointing at a
        # safety zone) can be rewritten to point at the *copies* below,
        # rather than dangling on the originals.
        id_map = {item["properties"]["id"]: _id() for item in source_features}
        with self.db._write_lock:
            try:
                self.db.conn.execute("BEGIN IMMEDIATE")
                self.db.conn.execute(
                    "INSERT INTO plan_scenarios (id,incident_id,period_id,name,kind,status,description,assumptions,created_at,updated_at,revision) VALUES (?,?,?,?,?,'draft',?,?,?,?,1)",
                    (new_scenario_id, incident_id, target_period_id, _clean_text(name, 300) or f"Copy of {source['name']}",
                     source["kind"], source["description"], source["assumptions"], now, now),
                )
                for feature in source_features:
                    props = feature["properties"]
                    # Only carry over custom/free-form properties; the "core"
                    # columns below are recomputed per-copy (new id, new
                    # parent scenario/period, fresh revision, etc.).
                    core = {"id", "incident_id", "period_id", "scenario_id", "feature_type", "title", "status",
                            "observed_at", "source", "observer", "confidence", "valid_from", "valid_to", "created_by",
                            "created_at", "updated_at", "revision", "deleted_at"}
                    custom = {key: value for key, value in props.items() if key not in core}
                    links = custom.get("links")
                    if isinstance(links, dict):
                        # Remap any link that points at another feature being
                        # copied in this same batch; leave unrelated ids as-is.
                        custom["links"] = {key: id_map.get(str(value), value) for key, value in links.items()}
                    custom["copied_from_feature_id"] = props["id"]
                    new_id = id_map[props["id"]]
                    self.db.conn.execute(
                        "INSERT INTO tactical_features (id,incident_id,period_id,scenario_id,feature_type,title,status,geometry_json,properties_json,observed_at,source,observer,confidence,valid_from,valid_to,created_by,created_at,updated_at,revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                        # A copy is a fresh plan, not a report of completed
                        # work, so most statuses reset to "proposed"; genuinely
                        # terminal statuses (completed/held) are preserved
                        # since they describe a fact rather than a plan step.
                        (new_id, incident_id, target_period_id, new_scenario_id, props["feature_type"], props["title"],
                         "proposed" if props["status"] not in {"completed", "held"} else props["status"],
                         json.dumps(feature["geometry"], separators=(",", ":")), json.dumps(custom, separators=(",", ":")),
                         props.get("observed_at"), props.get("source"), props.get("observer"), props.get("confidence"),
                         props.get("valid_from"), props.get("valid_to"), _clean_text(actor, 200), now, now),
                    )
                    self._audit(incident_id, "feature", new_id, "copy", 1,
                                {"copied_from_feature_id": props["id"], "source_scenario_id": scenario_id}, actor)
                self._audit(incident_id, "scenario", new_scenario_id, "copy", 1,
                            {"source_scenario_id": scenario_id, "target_period_id": target_period_id,
                             "feature_count": len(source_features)}, actor)
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback(); raise
        return {"scenario": dict(self.db.conn.execute("SELECT * FROM plan_scenarios WHERE id=?", (new_scenario_id,)).fetchone()),
                "feature_ids": list(id_map.values()), "source_feature_ids": list(id_map)}

    # ---------------------------------------- export, snapshots & comparison
    def export_bundle(self, incident_id: str) -> dict[str, Any]:
        """Serialise an entire incident - periods, scenarios, features,
        resources, safety checks, source imports, model-run provenance,
        warning acknowledgements and the full audit log - into one
        self-contained package. This is the shape shared between
        installations (see preview_import/import_bundle) and the shape
        stored verbatim inside a snapshot (see create_snapshot), so it is
        also, effectively, the incident's on-disk schema version: any field
        added here needs matching support on the import/validation side."""
        incident = self.get_incident(incident_id)
        periods = self.list_periods(incident_id)
        scenarios = [dict(r) for r in self.db.conn.execute(
            "SELECT * FROM plan_scenarios WHERE incident_id=? ORDER BY created_at", (incident_id,)
        ).fetchall()]
        features = self.list_features(incident_id, include_deleted=True)
        safety = [dict(r) for r in self.db.conn.execute(
            "SELECT s.* FROM safety_checks s JOIN operational_periods p ON p.id=s.period_id WHERE p.incident_id=?",
            (incident_id,),
        ).fetchall()]
        audit = []
        for row in self.db.conn.execute(
            "SELECT * FROM incident_audit_log WHERE incident_id=? ORDER BY changed_at", (incident_id,)
        ).fetchall():
            item = dict(row); item["payload"] = _json_load(item.pop("payload_json"), {})
            audit.append(item)
        source_imports = []
        for row in self.db.conn.execute(
            "SELECT * FROM incident_source_imports WHERE incident_id=? ORDER BY imported_at", (incident_id,)
        ).fetchall():
            item = dict(row)
            item["report"] = _json_load(item.pop("report_json", None), {})
            item["original_base64"] = base64.b64encode(item.pop("original_blob")).decode("ascii")
            source_imports.append(item)
        latest_snapshot = self.db.conn.execute(
            "SELECT id FROM incident_snapshots WHERE incident_id=? ORDER BY created_at DESC LIMIT 1", (incident_id,)
        ).fetchone()
        warning_acknowledgements = [dict(row) for row in self.db.conn.execute(
            "SELECT * FROM tactical_warning_acknowledgements WHERE incident_id=? ORDER BY acknowledged_at",
            (incident_id,),
        ).fetchall()]
        return {"schema": "nexfiremap-incident/1", "package_id": _id(),
                "origin_installation_id": self.installation_id,
                "base_snapshot_id": latest_snapshot[0] if latest_snapshot else None,
                "exported_at": utcnow(),
                "incident": incident, "operational_periods": periods,
                "scenarios": scenarios,
                "features": {"type": "FeatureCollection", "features": features},
                "resources": self.list_resources(incident_id), "safety_checks": safety,
                "source_imports": source_imports, "model_runs": self.list_model_runs(incident_id),
                "tactical_warning_acknowledgements": warning_acknowledgements,
                "audit_log": audit}

    def create_snapshot(self, incident_id: str, name: str, period_id: str | None,
                        classification: str, actor: str = "local operator") -> dict[str, Any]:
        """Freeze the incident's current export_bundle() into an immutable,
        named snapshot - e.g. "what the plan looked like when it was
        released to the public" - stored verbatim as JSON so it can later be
        diffed against (see compare_snapshots) or re-examined even after the
        live records have moved on. `classification` gates how freely the
        snapshot may be shared (draft/operational/public)."""
        if classification not in {"draft", "operational", "public"}:
            raise OperationsError("invalid snapshot classification")
        bundle = self.export_bundle(incident_id)
        now, snapshot_id = utcnow(), _id()
        values = {"id": snapshot_id, "incident_id": incident_id, "period_id": period_id,
                  "name": _clean_text(name, 300) or f"Snapshot {now}",
                  "classification": classification, "created_by": _clean_text(actor, 200),
                  "created_at": now, "payload_json": json.dumps(bundle, separators=(",", ":"))}
        with self.db._write_lock:
            self.db.conn.execute(
                "INSERT INTO incident_snapshots (id,incident_id,period_id,name,classification,created_by,created_at,payload_json) "
                "VALUES (:id,:incident_id,:period_id,:name,:classification,:created_by,:created_at,:payload_json)", values,
            )
            self._audit(incident_id, "snapshot", snapshot_id, "create", 1,
                        {k: v for k, v in values.items() if k != "payload_json"}, actor)
            self.db.conn.commit()
        return {k: v for k, v in values.items() if k != "payload_json"}

    def list_snapshots(self, incident_id: str) -> list[dict[str, Any]]:
        """List an incident's snapshots (metadata only, not the payload),
        newest first."""
        return [dict(r) for r in self.db.conn.execute(
            "SELECT id,incident_id,period_id,name,classification,created_by,created_at FROM incident_snapshots WHERE incident_id=? ORDER BY created_at DESC",
            (incident_id,),
        ).fetchall()]

    def _snapshot_bundle(self, incident_id: str, snapshot_id: str) -> dict[str, Any]:
        """Load and sanity-check a stored snapshot's payload_json, confirming
        it actually belongs to `incident_id` before handing it back - a
        snapshot's JSON blob is otherwise opaque to the database, so this is
        the one place that re-validates it matches its claimed owner."""
        row = self.db.conn.execute(
            "SELECT payload_json FROM incident_snapshots WHERE id=? AND incident_id=?",
            (snapshot_id, incident_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("incident snapshot not found")
        bundle = _json_load(row["payload_json"], None)
        if not isinstance(bundle, dict) or bundle.get("incident", {}).get("id") != incident_id:
            raise OperationsError("incident snapshot payload is invalid")
        return bundle

    @staticmethod
    def _comparison_summary(entity_type: str, item: dict[str, Any] | None) -> dict[str, Any] | None:
        """Reduce a full entity record to a small set of human-recognisable
        fields for display in a diff (compare_snapshots), rather than
        dumping the entire before/after record - operators reading a diff
        want "scenario X went from draft to approved", not every column."""
        if item is None:
            return None
        value = item.get("properties", {}) if entity_type == "feature" else item
        keys = ("id", "revision", "name", "title", "callsign", "status", "kind", "feature_type",
                "starts_at", "ends_at", "assignment", "check_key", "checked")
        return {key: value.get(key) for key in keys if key in value}

    @staticmethod
    def _comparison_value(entity_type: str, item: dict[str, Any]) -> dict[str, Any]:
        """Normalise an entity record into the shape used to test equality
        between two snapshots (or a snapshot and the live data) in
        compare_snapshots(). See the inline note below on why updated_at is
        stripped before comparing."""
        # updated_at is bookkeeping, not a command decision. In particular,
        # creating a snapshot updates the incident audit timestamp without
        # changing its revision - excluding it makes snapshot-vs-current stable.
        if entity_type == "feature":
            value = {"geometry": item.get("geometry"), "properties": dict(item.get("properties") or {})}
            value["properties"].pop("updated_at", None)
            return value
        value = dict(item)
        value.pop("updated_at", None)
        return value

    def compare_snapshots(self, incident_id: str, left_snapshot_id: str,
                          right_snapshot_id: str | None = None) -> dict[str, Any]:
        """Diff one snapshot against another snapshot, or (when
        `right_snapshot_id` is omitted) against the incident's current live
        state, entity-type by entity-type. Every entity present on either
        side is classified as added/removed/changed/unchanged using
        _comparison_value() for equality, and only non-unchanged entities are
        detailed in `changes` (via _comparison_summary) so the result stays
        readable even for a busy incident."""
        self.get_incident(incident_id)
        left = self._snapshot_bundle(incident_id, left_snapshot_id)
        right = self._snapshot_bundle(incident_id, right_snapshot_id) if right_snapshot_id else self.export_bundle(incident_id)

        groups = {
            "incident": lambda bundle: [bundle["incident"]],
            "operational_period": lambda bundle: bundle.get("operational_periods", []),
            "scenario": lambda bundle: bundle.get("scenarios", []),
            "feature": lambda bundle: bundle.get("features", {}).get("features", []),
            "resource": lambda bundle: bundle.get("resources", []),
            "safety_check": lambda bundle: bundle.get("safety_checks", []),
        }

        # Safety checks have no id column of their own (they're keyed by
        # period+scenario+check_key), and features nest their id under
        # "properties" as GeoJSON does; every other entity type just uses "id".
        def entity_id(entity_type: str, item: dict[str, Any]) -> str:
            if entity_type == "feature":
                return str(item.get("properties", {}).get("id"))
            if entity_type == "safety_check":
                return "|".join(str(item.get(key) or "") for key in ("period_id", "scenario_id", "check_key"))
            return str(item.get("id"))

        changes: list[dict[str, Any]] = []
        counts = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
        by_type: dict[str, dict[str, int]] = {}
        for entity_type, getter in groups.items():
            left_map = {entity_id(entity_type, item): item for item in getter(left)}
            right_map = {entity_id(entity_type, item): item for item in getter(right)}
            type_counts = {key: 0 for key in counts}
            for item_id in sorted(set(left_map) | set(right_map)):
                before, after = left_map.get(item_id), right_map.get(item_id)
                # present only on the right/left/both sides -> added/removed;
                # present on both but normalised values differ -> changed.
                if before is None:
                    classification = "added"
                elif after is None:
                    classification = "removed"
                elif self._comparison_value(entity_type, before) != self._comparison_value(entity_type, after):
                    classification = "changed"
                else:
                    classification = "unchanged"
                counts[classification] += 1; type_counts[classification] += 1
                if classification != "unchanged":
                    changes.append({
                        "entity_type": entity_type, "id": item_id, "classification": classification,
                        "before": self._comparison_summary(entity_type, before),
                        "after": self._comparison_summary(entity_type, after),
                    })
            by_type[entity_type] = type_counts
        return {
            "schema": "nexfiremap-snapshot-comparison/1", "incident_id": incident_id,
            "left_snapshot_id": left_snapshot_id,
            "right_snapshot_id": right_snapshot_id, "right_is_current": right_snapshot_id is None,
            "compared_at": utcnow(), "counts": counts, "by_type": by_type, "changes": changes,
        }

    # -------------------------------------------------------- package import
    def preview_import(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """Validate an incident package (the shape produced by export_bundle)
        and determine whether import_bundle() could apply it as-is, without
        actually writing anything. This is a read-only dry run in three
        phases: (1) structural validation of the package itself, (2) - for a
        brand-new incident id - checking every contained record id for
        collisions with existing rows, or (3) - for an incident id that
        already exists locally - classifying every incoming record against
        the local copy (new/identical/local_newer/incoming_newer/divergent)
        by comparing revisions, since only the caller can decide how to
        reconcile genuine conflicts. `can_apply` is only ever true for a
        clean new-incident import; merging into an existing incident always
        requires manual conflict resolution (see PackageConflict)."""
        errors: list[str] = []
        if not isinstance(bundle, dict) or bundle.get("schema") != "nexfiremap-incident/1":
            return {"valid": False, "can_apply": False, "mode": "invalid", "errors": ["unsupported or missing package schema"]}
        incident = bundle.get("incident")
        periods = bundle.get("operational_periods")
        scenarios = bundle.get("scenarios")
        feature_collection = bundle.get("features")
        resources = bundle.get("resources")
        safety = bundle.get("safety_checks")
        audit = bundle.get("audit_log")
        source_imports = bundle.get("source_imports", [])
        model_runs = bundle.get("model_runs", [])
        warning_acks = bundle.get("tactical_warning_acknowledgements", [])
        if not isinstance(incident, dict) or not _clean_text(incident.get("id"), 100) or not _clean_text(incident.get("name"), 300):
            errors.append("incident id and name are required")
        for label, value in (("operational_periods", periods), ("scenarios", scenarios),
                             ("resources", resources), ("safety_checks", safety), ("audit_log", audit),
                             ("source_imports", source_imports), ("model_runs", model_runs),
                             ("tactical_warning_acknowledgements", warning_acks)):
            if not isinstance(value, list):
                errors.append(f"{label} must be an array")
        if not isinstance(feature_collection, dict) or feature_collection.get("type") != "FeatureCollection" or not isinstance(feature_collection.get("features"), list):
            errors.append("features must be a GeoJSON FeatureCollection")
        if errors:
            return {"valid": False, "can_apply": False, "mode": "invalid", "errors": errors}

        # Phase 2: internal consistency - every id referenced by a child
        # record (period/scenario/feature/resource/...) must resolve to
        # something else inside this same package, and ids must be unique
        # within their table, so the package can never describe a graph that
        # points outside of itself.
        incident_id = str(incident["id"])
        period_ids = {str(p.get("id")) for p in periods if isinstance(p, dict)}
        scenario_ids = {str(s.get("id")) for s in scenarios if isinstance(s, dict)}
        if len(period_ids) != len(periods): errors.append("operational period ids must be present and unique")
        if len(scenario_ids) != len(scenarios): errors.append("scenario ids must be present and unique")
        for period in periods:
            if not isinstance(period, dict) or period.get("incident_id") != incident_id:
                errors.append("every operational period must belong to the package incident")
        for scenario in scenarios:
            if not isinstance(scenario, dict) or scenario.get("incident_id") != incident_id or str(scenario.get("period_id")) not in period_ids:
                errors.append("every scenario must belong to a packaged operational period")
        feature_ids: set[str] = set()
        for feature in feature_collection["features"]:
            try:
                props = feature["properties"]
                fid = str(props["id"])
                if fid in feature_ids: raise OperationsError("duplicate feature id")
                feature_ids.add(fid)
                if props.get("incident_id") != incident_id or (props.get("period_id") and str(props["period_id"]) not in period_ids):
                    raise OperationsError("feature relationship is outside package")
                if props.get("scenario_id") and str(props["scenario_id"]) not in scenario_ids:
                    raise OperationsError("feature scenario is outside package")
                if props.get("feature_type") not in FEATURE_TYPES:
                    raise OperationsError("invalid feature type")
                _validate_geometry(feature.get("geometry"), props["feature_type"])
            except (KeyError, TypeError, OperationsError) as exc:
                errors.append(f"invalid feature: {exc}")
        resource_ids = {str(r.get("id")) for r in resources if isinstance(r, dict)}
        if len(resource_ids) != len(resources): errors.append("resource ids must be present and unique")
        if any(not isinstance(r, dict) or r.get("incident_id") != incident_id for r in resources):
            errors.append("every resource must belong to the package incident")
        if any(not isinstance(item, dict) or str(item.get("period_id")) not in period_ids or
               (item.get("scenario_id") and str(item.get("scenario_id")) not in scenario_ids) for item in safety):
            errors.append("every safety check must belong to a packaged period")
        safety_ids = {(str(item.get("period_id")), str(item.get("scenario_id") or ""), str(item.get("check_key")))
                      for item in safety if isinstance(item, dict)}
        if len(safety_ids) != len(safety): errors.append("safety check keys must be unique per period/scenario")
        audit_ids = {str(item.get("id")) for item in audit if isinstance(item, dict) and item.get("id")}
        if len(audit_ids) != len(audit) or any(item.get("incident_id") != incident_id for item in audit if isinstance(item, dict)):
            errors.append("audit entries require unique ids and must belong to the package incident")
        source_import_ids: set[str] = set()
        for item in source_imports:
            try:
                import_id = str(item["id"])
                if not import_id or import_id in source_import_ids or item.get("incident_id") != incident_id:
                    raise ValueError("invalid id/incident")
                source_import_ids.add(import_id)
                raw = base64.b64decode(str(item["original_base64"]), validate=True)
                # Confirm the embedded original file bytes weren't truncated
                # or altered in transit/storage before trusting size_bytes/
                # sha256 that a re-import might rely on later.
                if len(raw) != int(item.get("size_bytes", -1)) or hashlib.sha256(raw).hexdigest() != item.get("sha256"):
                    raise ValueError("source bytes do not match size/hash")
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"invalid source import: {exc}")
        model_run_ids = {str(item.get("id")) for item in model_runs if isinstance(item, dict) and item.get("id")}
        if len(model_run_ids) != len(model_runs) or any(
            item.get("incident_id") != incident_id or str(item.get("scenario_id")) not in scenario_ids
            or not isinstance(item.get("provenance"), dict) for item in model_runs if isinstance(item, dict)
        ):
            errors.append("model provenance records require unique ids and packaged scenarios")
        warning_ack_ids = {str(item.get("id")) for item in warning_acks if isinstance(item, dict) and item.get("id")}
        if len(warning_ack_ids) != len(warning_acks) or any(
            item.get("incident_id") != incident_id or str(item.get("period_id")) not in period_ids
            or (item.get("scenario_id") and str(item.get("scenario_id")) not in scenario_ids)
            for item in warning_acks if isinstance(item, dict)
        ):
            errors.append("warning acknowledgements require unique ids and packaged periods/scenarios")

        counts = {"periods": len(periods), "scenarios": len(scenarios),
                  "features": len(feature_collection["features"]), "resources": len(resources),
                  "safety_checks": len(safety), "source_imports": len(source_imports), "model_runs": len(model_runs),
                  "warning_acknowledgements": len(warning_acks),
                  "audit_entries": len(audit)}
        if errors:
            return {"valid": False, "can_apply": False, "mode": "invalid", "incident_id": incident_id,
                    "incident_name": incident.get("name"), "counts": counts, "errors": sorted(set(errors))}

        # Phase 3a: brand-new incident. There is no local record to merge
        # against, so the only remaining risk is one of this package's ids
        # accidentally colliding with an unrelated existing row (e.g. two
        # installations both generated the same feature id, astronomically
        # unlikely with UUIDs but still checked). No collisions -> safe to
        # apply verbatim.
        local = self.db.conn.execute("SELECT 1 FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if local is None:
            collisions = []
            for table, ids in (("operational_periods", period_ids), ("plan_scenarios", scenario_ids),
                               ("tactical_features", feature_ids), ("incident_resources", resource_ids),
                               ("incident_source_imports", source_import_ids), ("incident_model_runs", model_run_ids),
                               ("tactical_warning_acknowledgements", warning_ack_ids),
                               ("incident_audit_log", audit_ids)):
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    found = self.db.conn.execute(f"SELECT id FROM {table} WHERE id IN ({placeholders})", list(ids)).fetchall()
                    collisions.extend({"entity": table, "id": row[0], "classification": "id_collision"} for row in found)
            return {"valid": not collisions, "can_apply": not collisions,
                    "mode": "new_incident" if not collisions else "conflict",
                    "incident_id": incident_id, "incident_name": incident["name"], "counts": counts,
                    "new_records": 1 + sum(counts.values()), "identical_records": 0,
                    "conflicts": collisions, "errors": []}

        # Phase 3b: incident already exists locally. Classify every incoming
        # record against its local counterpart by comparing revisions rather
        # than trusting timestamps (clocks across machines can't be trusted,
        # but each device's own revision counter can): a strictly higher
        # incoming revision means the other device edited a record we also
        # have, a strictly lower one means our copy is ahead, and equal
        # revisions with different content ("divergent") means both sides
        # edited from the same base independently and need a human to pick a
        # winner - none of these three are auto-applied (see can_apply below).
        local_bundle = self.export_bundle(incident_id)
        incoming_groups = {
            "incident": [incident], "period": periods, "scenario": scenarios,
            "feature": feature_collection["features"], "resource": resources,
            "source_import": source_imports, "model_run": model_runs, "warning_ack": warning_acks,
        }
        local_groups = {
            "incident": [local_bundle["incident"]], "period": local_bundle["operational_periods"],
            "scenario": local_bundle["scenarios"], "feature": local_bundle["features"]["features"],
            "resource": local_bundle["resources"], "source_import": local_bundle.get("source_imports", []),
            "model_run": local_bundle.get("model_runs", []),
            "warning_ack": local_bundle.get("tactical_warning_acknowledgements", []),
        }
        classifications: dict[str, int] = {"new": 0, "identical": 0, "local_newer": 0, "incoming_newer": 0, "divergent": 0}
        conflicts: list[dict[str, Any]] = []
        for entity_type, incoming_items in incoming_groups.items():
            def entity_id(item: dict[str, Any]) -> str:
                return str(item.get("properties", {}).get("id")) if entity_type == "feature" else str(item.get("id"))
            local_map = {entity_id(item): item for item in local_groups[entity_type]}
            for item in incoming_items:
                eid = entity_id(item); local_item = local_map.get(eid)
                if local_item is None:
                    classification = "new"
                elif json.dumps(item, sort_keys=True, separators=(",", ":")) == json.dumps(local_item, sort_keys=True, separators=(",", ":")):
                    classification = "identical"
                else:
                    incoming_revision = int((item.get("properties") or item).get("revision") or 0)
                    local_revision = int((local_item.get("properties") or local_item).get("revision") or 0)
                    classification = "incoming_newer" if incoming_revision > local_revision else (
                        "local_newer" if incoming_revision < local_revision else "divergent"
                    )
                classifications[classification] += 1
                if classification not in {"identical"}:
                    conflicts.append({"entity": entity_type, "id": eid, "classification": classification})
        return {"valid": True, "can_apply": False, "mode": "existing_incident",
                "incident_id": incident_id, "incident_name": incident["name"], "counts": counts,
                "classifications": classifications, "conflicts": conflicts,
                "errors": [], "reason": "existing incidents require side-by-side conflict resolution; no records were changed"}

    def import_bundle(self, bundle: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Apply an incident package produced by export_bundle(). Always
        re-runs preview_import() first and refuses to write anything unless
        `can_apply` is true - i.e. this only ever imports a brand-new
        incident with no id collisions; importing into an incident that
        already exists locally must go through manual conflict resolution
        first (see PackageConflict/preview_import), never through this
        method directly. All rows are inserted inside one transaction so a
        failure partway through never leaves a half-imported incident
        behind."""
        report = self.preview_import(bundle)
        if not report.get("can_apply"):
            raise PackageConflict(report)
        incident = bundle["incident"]
        periods = bundle["operational_periods"]
        scenarios = bundle["scenarios"]
        features = bundle["features"]["features"]
        resources = bundle["resources"]
        safety = bundle["safety_checks"]
        audit = bundle["audit_log"]
        source_imports = bundle.get("source_imports", [])
        model_runs = bundle.get("model_runs", [])
        warning_acks = bundle.get("tactical_warning_acknowledgements", [])
        incident_columns = ("id", "name", "incident_number", "status", "timezone", "center_lat", "center_lon", "notes", "created_at", "updated_at", "revision")
        period_columns = ("id", "incident_id", "name", "starts_at", "ends_at", "status", "objectives", "approved_by", "approved_at", "created_at", "updated_at", "revision")
        scenario_columns = ("id", "incident_id", "period_id", "name", "kind", "status", "description", "assumptions", "approved_by", "approved_at", "warning_acknowledged", "created_at", "updated_at", "revision")
        resource_columns = ("id", "incident_id", "callsign", "unit_type", "status", "crew_size", "water_capacity_l", "capabilities", "assignment", "contact_channel", "latitude", "longitude", "position_at", "created_at", "updated_at", "revision")
        feature_core = {"id", "incident_id", "period_id", "scenario_id", "feature_type", "title", "status", "observed_at", "source", "observer", "confidence", "valid_from", "valid_to", "created_by", "created_at", "updated_at", "revision", "deleted_at"}

        def insert_row(table: str, columns: tuple[str, ...], row: dict[str, Any]) -> None:
            # Straight column-for-column insert; unlike copy_scenario, import
            # preserves the incoming ids and revisions verbatim (this path is
            # only reached for a brand-new incident, so nothing local is at
            # risk of being overwritten - see preview_import's collision check).
            self.db.conn.execute(
                f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                [row.get(column) for column in columns],
            )

        with self.db._write_lock:
            try:
                self.db.conn.execute("BEGIN IMMEDIATE")
                insert_row("incidents", incident_columns, incident)
                for row in periods: insert_row("operational_periods", period_columns, row)
                for row in scenarios: insert_row("plan_scenarios", scenario_columns, row)
                for feature in features:
                    props = feature["properties"]
                    custom = {key: value for key, value in props.items() if key not in feature_core}
                    self.db.conn.execute(
                        "INSERT INTO tactical_features (id,incident_id,period_id,scenario_id,feature_type,title,status,geometry_json,properties_json,observed_at,source,observer,confidence,valid_from,valid_to,created_by,created_at,updated_at,revision,deleted_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (props.get("id"), props.get("incident_id"), props.get("period_id"), props.get("scenario_id"),
                         props.get("feature_type"), props.get("title") or "", props.get("status"),
                         json.dumps(feature["geometry"], separators=(",", ":")), json.dumps(custom, separators=(",", ":")),
                         props.get("observed_at"), props.get("source"), props.get("observer"), props.get("confidence"),
                         props.get("valid_from"), props.get("valid_to"), props.get("created_by") or actor,
                         props.get("created_at"), props.get("updated_at"), props.get("revision"), props.get("deleted_at")),
                    )
                for row in resources: insert_row("incident_resources", resource_columns, row)
                for row in source_imports:
                    raw = base64.b64decode(row["original_base64"], validate=True)
                    self.db.conn.execute(
                        "INSERT INTO incident_source_imports (id,incident_id,filename,format,sha256,size_bytes,source,imported_by,imported_at,feature_count,report_json,original_blob) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (row.get("id"), row.get("incident_id"), row.get("filename"), row.get("format"),
                         row.get("sha256"), row.get("size_bytes"), row.get("source") or "",
                         row.get("imported_by") or "imported", row.get("imported_at") or utcnow(),
                         row.get("feature_count") or 0,
                         json.dumps(row.get("report") or {}, separators=(",", ":")), raw),
                    )
                for row in model_runs:
                    self.db.conn.execute(
                        "INSERT INTO incident_model_runs (id,incident_id,scenario_id,job_id,model_kind,provenance_json,attached_by,attached_at) VALUES (?,?,?,?,?,?,?,?)",
                        (row.get("id"), row.get("incident_id"), row.get("scenario_id"), row.get("job_id"),
                         row.get("model_kind") or (row.get("provenance") or {}).get("model_kind") or "unknown",
                         json.dumps(row.get("provenance") or {}, separators=(",", ":")),
                         row.get("attached_by") or "imported", row.get("attached_at") or utcnow()),
                    )
                for row in warning_acks:
                    self.db.conn.execute(
                        "INSERT INTO tactical_warning_acknowledgements (id,warning_id,incident_id,period_id,scenario_id,warning_code,reason,acknowledged_by,acknowledged_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (row.get("id"), row.get("warning_id"), row.get("incident_id"), row.get("period_id"),
                         row.get("scenario_id") or "", row.get("warning_code"), row.get("reason") or "",
                         row.get("acknowledged_by") or "imported", row.get("acknowledged_at") or utcnow()),
                    )
                for row in safety:
                    self.db.conn.execute(
                        "INSERT INTO safety_checks (period_id,scenario_id,check_key,checked,details,updated_by,updated_at) VALUES (?,?,?,?,?,?,?)",
                        (row.get("period_id"), row.get("scenario_id") or "", row.get("check_key"), int(bool(row.get("checked"))),
                         row.get("details") or "", row.get("updated_by") or actor, row.get("updated_at") or utcnow()),
                    )
                for row in audit:
                    self.db.conn.execute(
                        "INSERT INTO incident_audit_log (id,incident_id,entity_type,entity_id,action,revision,actor,changed_at,payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
                        (row.get("id"), row.get("incident_id"), row.get("entity_type"), row.get("entity_id"), row.get("action"),
                         row.get("revision") or 1, row.get("actor") or "imported", row.get("changed_at") or utcnow(),
                         json.dumps(row.get("payload") or {}, separators=(",", ":"))),
                    )
                self._audit(incident["id"], "incident", incident["id"], "import", int(incident.get("revision") or 1),
                            {"schema": bundle["schema"], "source_exported_at": bundle.get("exported_at")}, actor)
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return {"imported": True, "report": report, "workspace": self.export_bundle(incident["id"])}


# --------------------------------------------------------------------- defaults
def default_period() -> dict[str, str]:
    """Suggested values for a new operational period form: a 12-hour window
    starting at the top of the current UTC hour. Convenience only - callers
    are free to override any field before calling create_period()."""
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=12)
    return {"name": start.strftime("Operational period %Y-%m-%d %H:%MZ"),
            "starts_at": start.isoformat(), "ends_at": end.isoformat(), "objectives": ""}
