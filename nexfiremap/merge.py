"""Staged side-by-side resolution for disconnected incident packages."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .operations import OperationsError, OperationsStore, _clean_text, _feature, _id, utcnow


class MergeManager:
    def __init__(self, store: OperationsStore) -> None:
        self.store, self.db = store, store.db

    def stage(self, bundle: dict[str, Any], actor: str) -> dict[str, Any]:
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
        tables = {"incident": "incidents", "period": "operational_periods", "scenario": "plan_scenarios",
                  "resource": "incident_resources"}
        if kind == "feature":
            props = item["properties"]; existing = self.db.conn.execute("SELECT revision FROM tactical_features WHERE id=?", (props["id"],)).fetchone()
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
        revision = int((item.get("properties") or item).get("revision") or 1)
        self.store._audit(incident_id, kind, str((item.get("properties") or item).get("id")), "merge_resolution",
                          revision, {"package_id": package_id, "incoming": item}, actor)
