"""Staged side-by-side resolution for disconnected incident packages.

When two copies of the same incident have been worked on independently
while disconnected (e.g. two field laptops, or a laptop reconnecting after
time offline) and a package export from one is imported into the other,
``operations.preview_import`` already detects which entities differ
(``report["conflicts"]``, classified new/divergent/incoming_newer/
local_newer/unchanged). This module is what turns that report into an
actual merge: a package is first *staged* (parsed, hashed, deduplicated by
content so re-staging the same file twice is a no-op) and then *resolved*,
where an operator picks "local" or "incoming" per conflicting entity -
never an automatic 3-way merge of field values, since guessing which
half of a conflicting tactical feature is correct is exactly the kind of
silent data loss this module exists to prevent.

Every entity kind (feature, incident, period, scenario, resource,
source_import, model_run, warning_ack) is applied with the same
insert-or-update-plus-revision-bump pattern, ordered so a feature is never
written before the period/scenario it references exists (see ``resolve``'s
sort key) - a structural dependency, not just a data-correctness nicety,
since a dangling foreign key would otherwise be possible in the middle of
an in-progress resolution.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .operations import OperationsError, OperationsStore, _clean_text, _feature, _id, utcnow


class MergeManager:
    def __init__(self, store: OperationsStore) -> None:
        self.store, self.db = store, store.db

    def stage(self, bundle: dict[str, Any], actor: str) -> dict[str, Any]:
        """Record an incoming package for operator review without applying
        anything yet. Deduplicated by the bundle's own content hash
        (``digest``) - staging the exact same file twice (e.g. a re-sent
        handover package) returns the existing staged entry instead of
        creating a second pending review for identical content."""
        report = self.store.preview_import(bundle)
        if not report.get("valid"):
            raise OperationsError("invalid incident package cannot be staged")
        if report.get("mode") != "existing_incident":
            raise OperationsError("only an existing-incident package requires merge staging")
        serialized = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        existing = self.db.conn.execute("SELECT * FROM incident_package_inbox WHERE sha256=?", (digest,)).fetchone()
        if existing:
            return self._row(existing)
        package_id, now = _id(), utcnow()
        with self.db._write_lock:
            self.db.conn.execute(
                "INSERT INTO incident_package_inbox (id,incident_id,sha256,origin_id,received_by,received_at,status,report_json,bundle_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (package_id, report["incident_id"], digest,
                 _clean_text(bundle.get("origin_installation_id"), 100) or "unknown",
                 _clean_text(actor, 200), now, "pending", json.dumps(report, separators=(",", ":")), serialized),
            )
            self.store._audit(report["incident_id"], "package", package_id, "stage", 1,
                              {"sha256": digest, "origin_id": bundle.get("origin_installation_id"),
                               "conflicts": report.get("conflicts", [])}, actor)
            self.db.conn.commit()
        return self.get(package_id)

    @staticmethod
    def _row(row) -> dict[str, Any]:
        item = dict(row); item["report"] = json.loads(item.pop("report_json")); item.pop("bundle_json", None)
        item["resolution"] = json.loads(item.pop("resolution_json")) if item.get("resolution_json") else None
        return item

    def get(self, package_id: str) -> dict[str, Any]:
        row = self.db.conn.execute("SELECT * FROM incident_package_inbox WHERE id=?", (package_id,)).fetchone()
        if row is None: raise OperationsError("staged package not found")
        return self._row(row)

    def list(self, incident_id: str) -> list[dict[str, Any]]:
        self.store.get_incident(incident_id)
        return [self._row(row) for row in self.db.conn.execute(
            "SELECT * FROM incident_package_inbox WHERE incident_id=? ORDER BY received_at DESC", (incident_id,)
        ).fetchall()]

    @staticmethod
    def _entities(bundle: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        """Flatten a package bundle into a ``(kind, id) -> raw entity``
        lookup, so ``resolve`` can fetch "the incoming version of this
        conflict" by the same (entity, id) key the conflict report already
        uses, regardless of which top-level bundle list/section that entity
        kind actually lives in."""
        result = {("incident", str(bundle["incident"]["id"])): bundle["incident"]}
        for kind, key in (("period", "operational_periods"), ("scenario", "scenarios"), ("resource", "resources"),
                          ("source_import", "source_imports"), ("model_run", "model_runs")):
            for item in bundle.get(key, []): result[(kind, str(item["id"]))] = item
        for item in bundle.get("tactical_warning_acknowledgements", []):
            result[("warning_ack", str(item["id"]))] = item
        for item in bundle.get("features", {}).get("features", []):
            result[("feature", str(item["properties"]["id"]))] = item
        return result

    def resolve(self, package_id: str, choices: dict[str, str], actor: str) -> dict[str, Any]:
        """Apply an operator's per-conflict "local" or "incoming" choices
        and commit the whole resolution as one transaction. Already-resolved
        packages return their prior result unchanged (idempotent, so a
        retried resolve request is harmless) rather than re-resolving or
        erroring.

        Only genuine conflicts (``divergent``/``incoming_newer``/
        ``local_newer``) require an explicit choice - entities the incoming
        package introduces fresh (``new``) are applied automatically since
        there's no local version to weigh against, and anything
        ``unchanged`` needs no choice at all (not even present in
        ``conflicts``).
        """
        row = self.db.conn.execute("SELECT * FROM incident_package_inbox WHERE id=?", (package_id,)).fetchone()
        if row is None: raise OperationsError("staged package not found")
        if row["status"] == "resolved": return self._row(row)
        bundle, report = json.loads(row["bundle_json"]), json.loads(row["report_json"])
        incoming = self._entities(bundle); incident_id = row["incident_id"]
        required = [item for item in report.get("conflicts", []) if item["classification"] in {"divergent", "incoming_newer", "local_newer"}]
        for item in required:
            key = f"{item['entity']}:{item['id']}"
            if choices.get(key) not in {"local", "incoming"}:
                raise OperationsError(f"resolution choice required for {key}")

        applied, kept = [], []
        with self.db._write_lock:
            try:
                self.db.conn.execute("BEGIN IMMEDIATE")
                # Apply in dependency order, not report order: a feature can
                # reference a period/scenario, a warning acknowledgement can
                # reference a period/scenario, and so on - writing a parent
                # entity before anything that might reference it keeps the
                # database consistent at every intermediate point in this
                # loop, not just once the whole resolution has committed.
                ordered = sorted(report.get("conflicts", []), key=lambda item: {"incident": 0, "period": 1, "scenario": 2, "source_import": 3, "model_run": 4, "warning_ack": 5, "resource": 6, "feature": 7}.get(item["entity"], 9))
                for conflict in ordered:
                    kind, entity_id, classification = conflict["entity"], str(conflict["id"]), conflict["classification"]
                    choice = choices.get(f"{kind}:{entity_id}")
                    if classification == "new": choice = "incoming"
                    if choice != "incoming": kept.append({"entity": kind, "id": entity_id}); continue
                    item = incoming.get((kind, entity_id))
                    if item is None: raise OperationsError(f"incoming entity missing: {kind}:{entity_id}")
                    self._apply(kind, item, incident_id, package_id, actor)
                    applied.append({"entity": kind, "id": entity_id})
                resolution = {"choices": choices, "applied": applied, "kept_local": kept}
                now = utcnow()
                self.db.conn.execute(
                    "UPDATE incident_package_inbox SET status='resolved',resolution_json=?,resolved_by=?,resolved_at=? WHERE id=?",
                    (json.dumps(resolution, separators=(",", ":")), _clean_text(actor, 200), now, package_id),
                )
                self.store._audit(incident_id, "package", package_id, "resolve", 2, resolution, actor)
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback(); raise
        return self.get(package_id)

    def _apply(self, kind: str, item: dict[str, Any], incident_id: str, package_id: str, actor: str) -> None:
        """Insert-or-update one chosen "incoming" entity into the live
        tables. Four entity kinds (incident/period/scenario/resource) share
        one generic table-driven branch below since their rows are close
        enough to a flat dict; feature/source_import/model_run/warning_ack
        each get their own branch because they need custom handling
        (feature's GeoJSON properties split into core columns vs. an
        arbitrary custom-properties JSON blob; source_import's blob is
        base64-encoded in the bundle and needs decoding back to bytes).

        Every branch bumps ``revision`` the same way: the greater of the
        incoming item's own claimed revision and the local row's current
        revision (if one exists), incremented by one when overwriting an
        existing row. That means a merge-resolution write always produces a
        revision strictly newer than whatever it's replacing, so a later,
        unrelated update can never look older than a resolution that
        already happened."""
        tables = {"incident": "incidents", "period": "operational_periods", "scenario": "plan_scenarios",
                  "resource": "incident_resources"}
        if kind == "feature":
            props = item["properties"]; existing = self.db.conn.execute("SELECT revision FROM tactical_features WHERE id=?", (props["id"],)).fetchone()
            # tactical_features stores a fixed set of "core" columns plus an
            # open-ended properties_json blob for everything else (per-type
            # custom fields) - split the incoming GeoJSON properties back
            # into that same shape rather than storing the whole properties
            # object as one blob, so ordinary column-level queries/indexes
            # still work on the merged row exactly as they do for a locally
            # created feature.
            core = {"id", "incident_id", "period_id", "scenario_id", "feature_type", "title", "status", "observed_at", "source", "observer", "confidence", "valid_from", "valid_to", "created_by", "created_at", "updated_at", "revision", "deleted_at"}
            custom = {key: value for key, value in props.items() if key not in core}
            revision = max(int(props.get("revision") or 1), int(existing[0]) if existing else 0) + (1 if existing else 0)
            values = (props["id"], incident_id, props.get("period_id"), props.get("scenario_id"), props["feature_type"], props.get("title") or "", props["status"],
                      json.dumps(item["geometry"], separators=(",", ":")), json.dumps(custom, separators=(",", ":")), props.get("observed_at"), props.get("source"), props.get("observer"), props.get("confidence"), props.get("valid_from"), props.get("valid_to"), props.get("created_by") or actor, props.get("created_at") or utcnow(), utcnow(), revision, props.get("deleted_at"))
            columns = ("id","incident_id","period_id","scenario_id","feature_type","title","status","geometry_json","properties_json","observed_at","source","observer","confidence","valid_from","valid_to","created_by","created_at","updated_at","revision","deleted_at")
            if existing:
                update_columns = columns[1:]
                self.db.conn.execute(f"UPDATE tactical_features SET {','.join(f'{column}=?' for column in update_columns)} WHERE id=?", [*values[1:], values[0]])
            else:
                self.db.conn.execute(f"INSERT INTO tactical_features ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
        elif kind in tables:
            table = tables[kind]; columns = [row[1] for row in self.db.conn.execute(f"PRAGMA table_info({table})")]
            existing = self.db.conn.execute(f"SELECT revision FROM {table} WHERE id=?", (item["id"],)).fetchone()
            value = dict(item); value["updated_at"] = utcnow()
            if "revision" in columns: value["revision"] = max(int(item.get("revision") or 1), int(existing[0]) if existing else 0) + (1 if existing else 0)
            if existing:
                update_columns = [column for column in columns if column not in {"id", "created_at"}]
                self.db.conn.execute(f"UPDATE {table} SET {','.join(f'{column}=?' for column in update_columns)} WHERE id=?",
                                     [value.get(column) for column in update_columns] + [item["id"]])
            else:
                self.db.conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [value.get(column) for column in columns])
        elif kind == "source_import":
            import base64
            columns = ("id", "incident_id", "filename", "format", "sha256", "size_bytes", "source",
                       "imported_by", "imported_at", "feature_count", "report_json", "original_blob")
            values = {
                **item,
                "incident_id": incident_id,
                "report_json": json.dumps(item.get("report", {}), separators=(",", ":")),
                # A package bundle is JSON, so the original binary source
                # file (KMZ, GeoPackage, ...) travels as base64 text -
                # decode it back to real bytes before it goes into the blob
                # column, the same shape apply()/original() expect locally.
                "original_blob": base64.b64decode(item.get("original_base64", ""), validate=True),
            }
            existing = self.db.conn.execute(
                "SELECT 1 FROM incident_source_imports WHERE id=?", (item["id"],)
            ).fetchone()
            if existing:
                update_columns = columns[1:]
                self.db.conn.execute(
                    f"UPDATE incident_source_imports SET {','.join(f'{column}=?' for column in update_columns)} WHERE id=?",
                    [values.get(column) for column in update_columns] + [item["id"]],
                )
            else:
                self.db.conn.execute(
                    f"INSERT INTO incident_source_imports ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    [values.get(column) for column in columns],
                )
        elif kind == "model_run":
            columns = ("id", "incident_id", "scenario_id", "job_id", "model_kind", "provenance_json", "attached_by", "attached_at")
            values = {**item, "incident_id": incident_id,
                      "provenance_json": json.dumps(item.get("provenance") or {}, separators=(",", ":"))}
            existing = self.db.conn.execute("SELECT 1 FROM incident_model_runs WHERE id=?", (item["id"],)).fetchone()
            if existing:
                update_columns = columns[1:]
                self.db.conn.execute(
                    f"UPDATE incident_model_runs SET {','.join(f'{column}=?' for column in update_columns)} WHERE id=?",
                    [values.get(column) for column in update_columns] + [item["id"]],
                )
            else:
                self.db.conn.execute(
                    f"INSERT INTO incident_model_runs ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    [values.get(column) for column in columns],
                )
        elif kind == "warning_ack":
            columns = ("id", "warning_id", "incident_id", "period_id", "scenario_id", "warning_code",
                       "reason", "acknowledged_by", "acknowledged_at")
            values = {**item, "incident_id": incident_id}
            existing = self.db.conn.execute(
                "SELECT 1 FROM tactical_warning_acknowledgements WHERE id=?", (item["id"],)
            ).fetchone()
            if existing:
                update_columns = columns[1:]
                self.db.conn.execute(
                    f"UPDATE tactical_warning_acknowledgements SET {','.join(f'{column}=?' for column in update_columns)} WHERE id=?",
                    [values.get(column) for column in update_columns] + [item["id"]],
                )
            else:
                self.db.conn.execute(
                    f"INSERT INTO tactical_warning_acknowledgements ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    [values.get(column) for column in columns],
                )
        else:
            raise OperationsError(f"unsupported merge entity: {kind}")
        # Every applied entity - regardless of which branch above handled it
        # - gets one audit entry recording the full incoming payload, so a
        # later "what did this merge actually change" review doesn't need
        # to diff the database before/after; the incoming data that won is
        # captured directly.
        revision = int((item.get("properties") or item).get("revision") or 1)
        self.store._audit(incident_id, kind, str((item.get("properties") or item).get("id")), "merge_resolution",
                          revision, {"package_id": package_id, "incoming": item}, actor)
