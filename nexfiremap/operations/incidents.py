"""The incident record and the operational periods under it.

Incidents and periods share a store because a period has no meaning
without its incident: every period write starts by resolving the incident
(`get_incident`) and every period read is scoped to one. Splitting them
would produce two stores that only ever call each other.

This is also the root of the aggregate graph - `FeatureStore`,
`ResourceStore` and `PackageStore` all take an `IncidentStore` so they
can confirm an incident exists before writing beneath it - which is why
this module imports nothing from its siblings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .base import AggregateStore
from .common import _clean_text, _id, utcnow
from .errors import NotFoundError, OperationsError
from .vocab import INCIDENT_STATUSES, PERIOD_STATUSES


class IncidentStore(AggregateStore):
    """CRUD for `incidents` and `operational_periods`."""

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
            self.audit.record(incident_id, "incident", incident_id, "create", 1, values, actor)
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
            self.audit.record(incident_id, "operational_period", period_id, "create", 1, values, actor)
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


# --------------------------------------------------------------------- defaults
def default_period() -> dict[str, str]:
    """Suggested values for a new operational period form: a 12-hour window
    starting at the top of the current UTC hour. Convenience only - callers
    are free to override any field before calling create_period()."""
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=12)
    return {"name": start.strftime("Operational period %Y-%m-%d %H:%MZ"),
            "starts_at": start.isoformat(), "ends_at": end.isoformat(), "objectives": ""}
