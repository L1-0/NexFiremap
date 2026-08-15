"""Whole-incident serialisation: bundles, snapshots, diffs and imports.

Everything here operates on the incident *as a unit* rather than on one
aggregate, which is why it is one store rather than four:

* `export_bundle` is the canonical serialised form of an incident. It is
  simultaneously the handover format between installations, the payload a
  snapshot freezes, and - because `preview_import` compares against it -
  the reference the merge machinery diffs against. In practice it is the
  incident's on-disk schema version: a field added to one of the other
  aggregates has to be added here too, or it silently stops travelling.
* `create_snapshot`/`compare_snapshots` freeze and diff that same shape.
* `preview_import`/`import_bundle` validate and apply it.

Because a bundle spans every aggregate, this store takes all four of the
others (see `base.AggregateStore` for why collaborators are injected
rather than reached through the facade) and *reads* through them, so the
bundle always reflects the same filters the live API applies - notably
`list_features(..., include_deleted=True)`, which is deliberate: a
soft-deleted feature must travel with the package or an importing site
would resurrect it.

Writes are the one place that deliberately does not go through the other
stores. `import_bundle` inserts rows column-for-column, preserving the
incoming ids, revisions and timestamps verbatim inside a single
`BEGIN IMMEDIATE` transaction. Routing those through `create_*` would
mint new ids and reset revisions, destroying exactly the identity the
package exists to carry - and would break the transaction into dozens of
independent commits. That is a rule about the import path, not an
accident of the original single-class layout, and it must survive any
future reshuffling of this package.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from ..db import Database
from .audit import AuditLog
from .base import AggregateStore
from .common import _clean_text, _id, _json_load, _validate_geometry, utcnow
from .errors import NotFoundError, OperationsError, PackageConflict
from .features import FeatureStore
from .incidents import IncidentStore
from .links import LinkStore
from .resources import ResourceStore
from .scenarios import ScenarioStore
from .vocab import FEATURE_TYPES


class PackageStore(AggregateStore):
    """Export, snapshot, compare and import whole incidents."""

    def __init__(self, db: Database, audit: AuditLog, incidents: IncidentStore,
                 features: FeatureStore, scenarios: ScenarioStore, resources: ResourceStore,
                 links: LinkStore | None = None) -> None:
        super().__init__(db, audit)
        self.incidents = incidents
        self.features = features
        self.scenarios = scenarios
        self.resources = resources
        # Optional so a PackageStore built by older code (or a test) still
        # constructs; a bundle then simply carries no links.
        self.links = links

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
        incident = self.incidents.get_incident(incident_id)
        periods = self.incidents.list_periods(incident_id)
        scenarios = [dict(r) for r in self.db.conn.execute(
            "SELECT * FROM plan_scenarios WHERE incident_id=? ORDER BY created_at", (incident_id,)
        ).fetchall()]
        features = self.features.list_features(incident_id, include_deleted=True)
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
                "resources": self.resources.list_resources(incident_id), "safety_checks": safety,
                "source_imports": source_imports, "model_runs": self.scenarios.list_model_runs(incident_id),
                # What this incident was linked to on the analytical side.
                # Each entry carries its own frozen snapshot, so a package
                # opened on another machine - which has none of the source
                # events, jobs or detections - still shows what was seen.
                "links": self.links.list_links(incident_id) if self.links else [],
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
            self.audit.record(incident_id, "snapshot", snapshot_id, "create", 1,
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
        self.incidents.get_incident(incident_id)
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
        # Absent for a package produced before links existed, which must still
        # import cleanly - a handover from an older installation is exactly the
        # case this format has to tolerate.
        links = bundle.get("links", [])
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
        # Absent for a package produced before links existed, which must still
        # import cleanly - a handover from an older installation is exactly the
        # case this format has to tolerate.
        links = bundle.get("links", [])
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
                for row in links:
                    # A link's *snapshot* is what survives a handover: the
                    # receiving installation has none of the source events,
                    # jobs or detections, and its own event ids mean something
                    # entirely different. Importing the frozen snapshot is
                    # therefore the only thing that carries meaning across, and
                    # it is exactly why links are snapshots rather than
                    # references (see links.py's module docstring).
                    self.db.conn.execute(
                        "INSERT OR REPLACE INTO incident_links "
                        "(id,incident_id,kind,ref_id,snapshot_json,note,actor,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (row.get("id"), row.get("incident_id"), row.get("kind"), str(row.get("ref_id") or ""),
                         json.dumps(row.get("snapshot") or {}, sort_keys=True, separators=(",", ":")),
                         row.get("note") or "", row.get("actor") or "imported",
                         row.get("created_at") or utcnow()),
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
                self.audit.record(incident["id"], "incident", incident["id"], "import", int(incident.get("revision") or 1),
                                  {"schema": bundle["schema"], "source_exported_at": bundle.get("exported_at")}, actor)
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return {"imported": True, "report": report, "workspace": self.export_bundle(incident["id"])}
