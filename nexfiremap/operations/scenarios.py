"""Plan scenarios and everything that certifies or annotates one.

A scenario is one hypothesis for how an operational period will play out.
Three things that could each look like a separate concern live here
because they are all really *about* the scenario's approval state:

* **Safety checks** (`SAFETY_CHECKS`) are stored per period+scenario and
  exist to gate `approve_scenario`. `get_safety_checks` is called by
  `approve_scenario` on every approval, and an approved scenario that is
  substantively edited is demoted back to draft by `update_scenario`.
  Splitting the checklist into its own store would put one half of the
  approval rule on each side of a seam that has no independent callers.
* **Model runs** attach a fire-behaviour job's provenance to a scenario;
  their only parent is a scenario and their only lifecycle event is being
  attached to one.
* **Scenario copy** clones a scenario *and its features* into another
  period, which is why this store takes a `FeatureStore`.

The dependency runs scenarios -> features (never the other way): features
validate their scenario parent with a plain `SELECT 1`, so there is no
cycle. See `base.AggregateStore` for why collaborators are injected.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ..db import Database
from ..provenance import from_job
from .audit import AuditLog
from .base import AggregateStore
from .common import _clean_text, _id, utcnow
from .errors import NotFoundError, OperationsError, RevisionConflict
from .features import FeatureStore
from .vocab import SAFETY_CHECKS, SCENARIO_KINDS, SCENARIO_STATUSES


class ScenarioStore(AggregateStore):
    """`plan_scenarios`, `safety_checks`, `incident_model_runs` and the
    approval workflow that ties them together."""

    def __init__(self, db: Database, audit: AuditLog, features: FeatureStore) -> None:
        super().__init__(db, audit)
        self.features = features

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
            self.audit.record(incident_id, "scenario", scenario_id, "create", 1, values, actor)
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
            self.audit.record(incident_id, "model_run", record_id, "attach", 1, provenance, actor)
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
            self.audit.record(incident_id, "scenario", scenario_id, "approve", revision,
                              {**fresh, "missing_safety_checks": [c["key"] for c in missing]}, approver)
            self.db.conn.commit()
        fresh["safety_warnings"] = missing
        return fresh

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
        # Read the features to clone through the feature store (the aggregate
        # that owns them); the INSERTs below are written here rather than
        # delegated so the whole copy - scenario row, every feature row and
        # every audit entry - lives in one explicit transaction.
        source_features = self.features.list_features(incident_id, source["period_id"], scenario_id)
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
                    self.audit.record(incident_id, "feature", new_id, "copy", 1,
                                      {"copied_from_feature_id": props["id"], "source_scenario_id": scenario_id}, actor)
                self.audit.record(incident_id, "scenario", new_scenario_id, "copy", 1,
                                  {"source_scenario_id": scenario_id, "target_period_id": target_period_id,
                                   "feature_count": len(source_features)}, actor)
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback(); raise
        return {"scenario": dict(self.db.conn.execute("SELECT * FROM plan_scenarios WHERE id=?", (new_scenario_id,)).fetchone()),
                "feature_ids": list(id_map.values()), "source_feature_ids": list(id_map)}
