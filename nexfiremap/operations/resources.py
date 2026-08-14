"""Incident resources: crews, engines, aircraft and what they are doing.

The smallest and most independent aggregate in the package. A resource
belongs to an incident and to nothing else - it is not scoped to a
period or a scenario - so this store's only collaborator is the
`IncidentStore` it uses to confirm the parent incident exists before
creating one.

Resource *positions* are deliberately not here: a moving unit's track is
recorded as tactical features and telemetry rows by `telemetry.py`, which
writes through its own manager. This store holds the last known position
columns only, as attributes of the resource record itself.
"""

from __future__ import annotations

from typing import Any

from ..db import Database
from .audit import AuditLog
from .base import AggregateStore
from .common import _clean_text, _id, utcnow
from .errors import NotFoundError, OperationsError
from .incidents import IncidentStore
from .vocab import RESOURCE_STATUSES


class ResourceStore(AggregateStore):
    """CRUD for `incident_resources`."""

    def __init__(self, db: Database, audit: AuditLog, incidents: IncidentStore) -> None:
        super().__init__(db, audit)
        self.incidents = incidents

    def create_resource(self, incident_id: str, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Create a new incident resource (crew, engine, aircraft, ...) at
        revision 1. `callsign` and `unit_type` are the only required fields;
        everything else (position, capabilities, assignment) can be filled
        in as it becomes known."""
        self.incidents.get_incident(incident_id)
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
            self.audit.record(incident_id, "resource", resource_id, "create", 1, values, actor)
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
