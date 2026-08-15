"""The append-only incident audit trail.

Every create/update/delete/approve/import/copy in the incident domain
lands here, which makes this the one component genuinely shared by all
the aggregate stores - and by six managers outside this package
(`telemetry`, `drone`, `field_import`, `tactics`, `products`, `merge`),
which record their own entity types (`position_feed`, `drone_mission`,
`product`, `package`, ...) into the same log so an incident has exactly
one history rather than one per subsystem. Those callers reach it as
`store._audit(...)`, which `OperationsStore` keeps as a thin delegate to
`AuditLog.record()` for precisely that reason.

Why it is a collaborator rather than a base class or a mixin: an audit
row is a fact about the incident, not about the aggregate that produced
it, so every store holding the *same* `AuditLog` instance is a more
honest model than each store inheriting its own copy of the behaviour.
It also keeps the trail writable from outside the package without
exposing anything else the stores can do.

Transaction contract (load-bearing, do not change casually): `record()`
never begins, commits or rolls back. It writes on the connection it is
handed and expects the caller to already hold `db._write_lock` inside an
open transaction, so the audit row and the change it describes commit
together or not at all. A mutation that succeeded with no audit row (or
an audit row for a mutation that was rolled back) would be worse than a
failed write in an incident-command tool.
"""

from __future__ import annotations

import json
from typing import Any

from ..db import Database
from .common import _clean_text, _id, utcnow


class AuditLog:
    """Writer for `incident_audit_log`, bound to one `Database`."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
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

    def recent(self, incident_id: str, *, entity_types: tuple[str, ...] | None = None,
               since: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Read back the trail, newest first.

        This class was write-only until now, which is why the most safety-
        relevant output in the product was invisible: `safety.record_warnings`
        writes "this crew is inside the forecast perimeter" here, and
        `safety.record_watch` writes "a new detection landed in this incident's
        AOI" - and nothing could read either one back. Both were computed on
        every accepted position, tested, and then addressed to nobody. The only
        way to see them was to download the whole handover export and read the
        JSON.

        `entity_types` narrows to the kinds a caller cares about rather than
        making it filter a whole incident's history client-side; the
        `(incident_id, changed_at DESC)` index makes both shapes cheap.
        """
        clauses = ["incident_id = ?"]
        params: list[Any] = [incident_id]
        if entity_types:
            clauses.append(f"entity_type IN ({','.join('?' for _ in entity_types)})")
            params.extend(entity_types)
        if since:
            clauses.append("changed_at > ?")
            params.append(since)
        params.append(max(1, min(int(limit), 1000)))
        rows = self.db.conn.execute(
            f"SELECT id, incident_id, entity_type, entity_id, action, revision, actor, "
            f"changed_at, payload_json FROM incident_audit_log "
            f"WHERE {' AND '.join(clauses)} ORDER BY changed_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json") or "{}")
            except (TypeError, ValueError):
                # A malformed payload must not hide the fact that the event
                # happened - the row itself is the record.
                item["payload"] = {}
                item.pop("payload_json", None)
            out.append(item)
        return out
