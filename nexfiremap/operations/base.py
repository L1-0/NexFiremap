"""What every aggregate store has in common: a database, an audit log, and
one optimistic-concurrency update path.

`AggregateStore` is deliberately thin. It is not "the operational store
minus some methods" - it holds only the two collaborators every aggregate
genuinely needs (`db`, `audit`) plus the revision-checked UPDATE that
incidents, periods, scenarios and resources all perform identically.
Tactical features intentionally do *not* use `_apply_revision_update`;
they re-implement the same protocol inline because they have to
re-validate geometry against the stored `feature_type` first (see
`features.FeatureStore.update_feature`), and that difference is easier to
audit when it is visible rather than hidden behind a hook.

How cross-aggregate reads work in this package (the deliberate choice
this refactor had to make): a store that needs another aggregate takes
that store as a **constructor argument** - `FeatureStore` takes an
`IncidentStore` because creating a feature must confirm the incident
exists; `ScenarioStore` takes a `FeatureStore` because copying a scenario
copies its features; `PackageStore` takes all of them because a bundle is
the whole incident. It does *not* call back into the `OperationsStore`
facade, and it does not build a second store over the same `Database`
behind the caller's back. Rationale:

* No back-reference to the facade means the dependency graph stays a DAG
  (incidents -> features -> scenarios -> packages), so there is no
  half-constructed-facade window during `OperationsStore.__init__` and no
  cycle to reason about when tracing a write.
* Passing the collaborator rather than re-instantiating one means every
  store in a process shares the same objects over the same connection.
  That matters because SQLite writes here are serialised by
  `Database._write_lock`; a second, privately-created store would still
  share the lock, but it would be a second identity for the same data,
  which is exactly the kind of thing that makes a concurrency bug hard to
  see.
* The collaborators are used for reads and for composing whole-incident
  operations only. No store mutates another aggregate's rows through its
  collaborator; cross-aggregate *writes* (scenario copy, package import)
  own their transaction explicitly and write the SQL themselves, as the
  original single-class implementation did.
"""

from __future__ import annotations

from typing import Any

from ..db import Database
from .audit import AuditLog
from .common import _id, utcnow
from .errors import NotFoundError, RevisionConflict


def ensure_installation_id(db: Database) -> None:
    """Create this database's `installation_id` if it has never had one.

    Generated once per database file and never changed; used to tell "this
    installation's own records" apart from records that arrived via an
    imported package (see `PackageStore.export_bundle`). Called once from
    `OperationsStore.__init__` rather than from every aggregate store, so
    opening a workspace still performs exactly one bootstrap write.
    """
    with db._write_lock:
        row = db.conn.execute("SELECT value FROM meta WHERE key='installation_id'").fetchone()
        if row is None:
            db.conn.execute("INSERT INTO meta (key,value) VALUES ('installation_id',?)", (_id(),))
            db.conn.commit()


class AggregateStore:
    """Base for the per-aggregate stores: holds the shared `Database` and
    `AuditLog`, and implements the revision-checked update they share."""

    def __init__(self, db: Database, audit: AuditLog) -> None:
        self.db = db
        self.audit = audit

    @property
    def installation_id(self) -> str:
        """Stable identifier for this SQLite database, set once on first use.
        Recorded on every exported package so a re-imported bundle can be
        traced back to the machine it originated from."""
        return str(self.db.conn.execute("SELECT value FROM meta WHERE key='installation_id'").fetchone()[0])

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
            self.audit.record(incident_id, entity_type, entity_id, "update", expected_revision + 1, fresh, actor)
            self.db.conn.commit()
        return fresh
