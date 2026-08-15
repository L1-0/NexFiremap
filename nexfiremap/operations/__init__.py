"""Offline-first incident-command records for NexFiremap.

The analytical map is useful without this package, but operational use
needs a durable distinction between observations, plans and resources.
This store is intentionally SQLite-only and dependency-free. UUID
identifiers and integer revisions allow incident packages edited on
disconnected machines to be compared without silently accepting "last
file copied wins".

Layout
------
This was one 1500-line `operations.py` class. It is now one module per
aggregate, with `OperationsStore` reduced to a facade that owns them:

* `errors.py`    - `OperationsError`/`NotFoundError`/`RevisionConflict`/
                   `PackageConflict`, the taxonomy `api.py` maps to HTTP
                   status codes.
* `vocab.py`     - the fixed domain vocabularies (feature types, statuses,
                   scenario kinds, `SAFETY_CHECKS`) enforced on every write.
* `common.py`    - pure helpers: `utcnow`, `_id`, `_clean_text`, `_feature`,
                   `_validate_geometry`, ...
* `audit.py`     - `AuditLog`, the append-only incident history that every
                   store (and six managers outside this package) writes to.
* `base.py`      - `AggregateStore`: shared `db`/`audit` and the
                   revision-checked update path; also documents the
                   cross-aggregate call policy in full.
* `incidents.py` - `IncidentStore`: incidents + operational periods.
* `features.py`  - `FeatureStore`: tactical features + progression.
* `scenarios.py` - `ScenarioStore`: scenarios, safety checklist, approval,
                   model-run provenance, scenario copy.
* `resources.py` - `ResourceStore`: crews, engines, aircraft.
* `packages.py`  - `PackageStore`: export bundles, snapshots, diffs, import.

Why a facade rather than exposing the stores directly
-----------------------------------------------------
`OperationsStore` is what `app.state.operations` holds, what all seven
incident-domain managers (`telemetry`, `drone`, `wind`, `field_import`,
`tactics`, `products`, `merge`) receive in their constructors, and what
every route handler and test already calls. Keeping a single object with
the same method set means the decomposition is invisible from outside:
not one call site, import or type hint had to change. New code is free to
reach a sub-store directly (`store.features`, `store.scenarios`, ...) when
it only needs one aggregate; the flat methods below stay because they are
the existing contract, not because the composition is a secret.

Everything the old module exported - the vocabulary constants, the error
classes, `utcnow`, `default_period` and the underscore-prefixed helpers
that `merge.py`, `products.py`, `tactics.py`, `field_import.py`,
`security.py`, `drone.py` and `telemetry.py` import - is re-exported here
under its original name, so `from .operations import X` keeps resolving
for every existing X.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..db import Database
from .audit import AuditLog
from .base import AggregateStore, ensure_installation_id
from .common import (
    _clean_text,
    _feature,
    _id,
    _json_load,
    _plain,
    _validate_geometry,
    utcnow,
)
from .errors import NotFoundError, OperationsError, PackageConflict, RevisionConflict
from .features import FeatureStore
from .incidents import IncidentStore, default_period
from .links import LINK_KINDS, LinkStore, aoi_bbox, normalise_aoi, point_in_polygon
from .packages import PackageStore
from .resources import ResourceStore
from .scenarios import ScenarioStore
from .vocab import (
    AREA_TYPES,
    FEATURE_STATUSES,
    FEATURE_TYPES,
    INCIDENT_STATUSES,
    LINE_TYPES,
    OBSERVATION_TYPES,
    PERIOD_STATUSES,
    POINT_TYPES,
    RESOURCE_STATUSES,
    SCENARIO_KINDS,
    SCENARIO_STATUSES,
    SAFETY_CHECKS,
)

# The underscore-prefixed names below are deliberately part of this
# package's export surface: in this codebase they mean "internal to the
# incident domain", not "private to one module", and several sibling
# modules import them by name from `nexfiremap.operations`.
__all__ = [
    # errors
    "OperationsError", "NotFoundError", "RevisionConflict", "PackageConflict",
    # vocabulary
    "SAFETY_CHECKS", "POINT_TYPES", "LINE_TYPES", "AREA_TYPES", "FEATURE_TYPES",
    "OBSERVATION_TYPES", "SCENARIO_KINDS", "SCENARIO_STATUSES", "INCIDENT_STATUSES",
    "PERIOD_STATUSES", "RESOURCE_STATUSES", "FEATURE_STATUSES",
    # helpers
    "utcnow", "default_period",
    "_id", "_clean_text", "_json_load", "_plain", "_feature", "_validate_geometry",
    # stores
    "OperationsStore", "AggregateStore", "AuditLog", "IncidentStore", "FeatureStore",
    "ScenarioStore", "ResourceStore", "PackageStore", "LinkStore",
    # cross-module links between the analytical and operational halves
    "LINK_KINDS", "aoi_bbox", "normalise_aoi", "point_in_polygon",
]


class OperationsStore:
    """SQLite-backed store for incident-command operational data: incidents,
    operational periods, plan scenarios, tactical features, resources,
    safety checklists and their audit trail.

    Composition root and facade for the per-aggregate stores. It holds no
    query logic of its own - every method below forwards to the store that
    owns that aggregate - but it does own three things that are genuinely
    global to the incident record:

    * the `Database` handle every store shares (and therefore the single
      `Database._write_lock` that serialises all operational writes),
    * one `AuditLog` instance, so a change to any aggregate lands in one
      incident history rather than one history per subsystem,
    * the `installation_id` bootstrap, run once here instead of once per
      store.

    All mutations still go through revision-checked helpers (see
    `base.AggregateStore._apply_revision_update`, and the inline variants in
    `features.FeatureStore` and `scenarios.ScenarioStore.approve_scenario`)
    so concurrent edits from different operators/devices are detected
    instead of silently clobbered, and every mutation is still mirrored into
    `incident_audit_log`.

    The sub-stores are also reachable as attributes (`store.incidents`,
    `store.features`, `store.scenarios`, `store.resources`,
    `store.packages`, `store.audit_log`) for code that only needs one
    aggregate.
    """

    # ----------------------------------------------------------------- setup
    def __init__(self, db: Database) -> None:
        self.db = db
        # Bootstrap before any store is built: `PackageStore.export_bundle`
        # reads `installation_id` on its first call and must never see a
        # database that has not got one yet.
        ensure_installation_id(db)
        self.audit_log = AuditLog(db)
        # Construction order is the dependency order (see base.py): incidents
        # depend on nothing, features on incidents, scenarios on features,
        # resources on incidents, and packages on all four.
        self.incidents = IncidentStore(db, self.audit_log)
        self.features = FeatureStore(db, self.audit_log, self.incidents)
        self.scenarios = ScenarioStore(db, self.audit_log, self.features)
        self.resources = ResourceStore(db, self.audit_log, self.incidents)
        # Links join this operational record to the analytical side
        # (events, model runs, alerts). Takes IncidentStore for the same
        # reason FeatureStore does: a link must confirm its incident exists.
        self.links = LinkStore(db, self.audit_log, self.incidents)
        self.packages = PackageStore(
            db, self.audit_log, self.incidents, self.features, self.scenarios,
            self.resources, self.links
        )

    @property
    def installation_id(self) -> str:
        """Stable identifier for this SQLite database, set once on first use.
        Recorded on every exported package so a re-imported bundle can be
        traced back to the machine it originated from."""
        return self.incidents.installation_id

    # ------------------------------------------------------------- incidents
    def create_incident(self, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Create a new incident record (revision 1) and log its creation.
        See `incidents.IncidentStore.create_incident`."""
        return self.incidents.create_incident(data, actor)

    def list_incidents(self, include_closed: bool = False) -> list[dict[str, Any]]:
        """List incidents, most recently updated first; closed ones hidden by
        default. See `incidents.IncidentStore.list_incidents`."""
        return self.incidents.list_incidents(include_closed)

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch one incident by id, or raise NotFoundError.
        See `incidents.IncidentStore.get_incident`."""
        return self.incidents.get_incident(incident_id)

    def update_incident(self, incident_id: str, data: dict[str, Any], expected_revision: int,
                        actor: str = "local operator") -> dict[str, Any]:
        """Patch the incident's editable fields, revision-checked.
        See `incidents.IncidentStore.update_incident`."""
        return self.incidents.update_incident(incident_id, data, expected_revision, actor)

    # --------------------------------------------------- operational periods
    def create_period(self, incident_id: str, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Create a new operational period (revision 1) under an incident.
        See `incidents.IncidentStore.create_period`."""
        return self.incidents.create_period(incident_id, data, actor)

    def list_periods(self, incident_id: str) -> list[dict[str, Any]]:
        """List an incident's operational periods, most recent start first.
        See `incidents.IncidentStore.list_periods`."""
        return self.incidents.list_periods(incident_id)

    def update_period(self, incident_id: str, period_id: str, data: dict[str, Any],
                      expected_revision: int, actor: str = "local operator") -> dict[str, Any]:
        """Patch an operational period, revision-checked.
        See `incidents.IncidentStore.update_period`."""
        return self.incidents.update_period(incident_id, period_id, data, expected_revision, actor)

    # ------------------------------------------------------------- scenarios
    def create_scenario(self, incident_id: str, period_id: str, data: dict[str, Any],
                        actor: str = "local operator") -> dict[str, Any]:
        """Create a new plan scenario (revision 1, status "draft").
        See `scenarios.ScenarioStore.create_scenario`."""
        return self.scenarios.create_scenario(incident_id, period_id, data, actor)

    def list_scenarios(self, period_id: str) -> list[dict[str, Any]]:
        """List a period's scenarios, primary first.
        See `scenarios.ScenarioStore.list_scenarios`."""
        return self.scenarios.list_scenarios(period_id)

    def update_scenario(self, incident_id: str, scenario_id: str, data: dict[str, Any],
                        expected_revision: int, actor: str = "local operator") -> dict[str, Any]:
        """Patch a scenario, revision-checked; a substantive edit to an
        approved scenario demotes it back to draft.
        See `scenarios.ScenarioStore.update_scenario`."""
        return self.scenarios.update_scenario(incident_id, scenario_id, data, expected_revision, actor)

    def copy_scenario(self, incident_id: str, scenario_id: str, target_period_id: str,
                      name: str, actor: str = "local operator") -> dict[str, Any]:
        """Clone a scenario and its features into another operational period.
        See `scenarios.ScenarioStore.copy_scenario`."""
        return self.scenarios.copy_scenario(incident_id, scenario_id, target_period_id, name, actor)

    # -------------------------------------------------- model run provenance
    # ------------------------------------------------- cross-module links

    def add_link(self, incident_id: str, kind: str, ref_id: str,
                 snapshot: dict[str, Any], note: str = "",
                 actor: str = "local operator") -> dict[str, Any]:
        """Link this incident to something the analytical side produced.
        See `links.LinkStore.add_link` - the snapshot is mandatory."""
        return self.links.add_link(incident_id, kind, ref_id, snapshot, note, actor)

    def list_links(self, incident_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        """See `links.LinkStore.list_links`."""
        return self.links.list_links(incident_id, kind)

    def get_link(self, incident_id: str, link_id: str) -> dict[str, Any]:
        """See `links.LinkStore.get_link`."""
        return self.links.get_link(incident_id, link_id)

    def remove_link(self, incident_id: str, link_id: str, actor: str = "local operator") -> bool:
        """See `links.LinkStore.remove_link`."""
        return self.links.remove_link(incident_id, link_id, actor)

    def set_aoi(self, incident_id: str, geometry: dict[str, Any] | None,
                actor: str = "local operator") -> dict[str, Any]:
        """See `links.LinkStore.set_aoi`."""
        return self.links.set_aoi(incident_id, geometry, actor)

    def get_aoi(self, incident_id: str) -> dict[str, Any] | None:
        """See `links.LinkStore.get_aoi`."""
        return self.links.get_aoi(incident_id)

    def incidents_covering(self, lat: float, lon: float, *, active_only: bool = True
                           ) -> list[dict[str, Any]]:
        """See `links.LinkStore.incidents_covering`."""
        return self.links.incidents_covering(lat, lon, active_only=active_only)

    def attach_model_run_linked(self, incident_id: str, scenario_id: str, job_id: int,
                                actor: str = "local operator") -> dict[str, Any]:
        """`attach_model_run`, and also record it in the unified link list.

        The scenario attachment is what the *plan* needs (which run justified
        this plan); the link is what the *incident* needs (everything analytical
        this incident is built on, in one place, each with its own frozen
        snapshot). Keeping both rather than replacing one with the other means
        `incident_model_runs`' UNIQUE(scenario_id, job_id) still guarantees one
        run per scenario, while the link list stays the single answer to "what
        is this incident based on".
        """
        record = self.attach_model_run(incident_id, scenario_id, job_id, actor)
        # A model run is re-run with new weather under the same scenario, so the
        # snapshot has to freeze *this* run's provenance - the whole reason
        # links are snapshots (see links.py).
        self.links.add_link(
            incident_id, "model_run", str(job_id),
            {"job_id": job_id, "scenario_id": scenario_id,
             "model_kind": record.get("model_kind"),
             "provenance": record.get("provenance") or {}},
            "attached to scenario", actor)
        return record

    def attach_model_run(self, incident_id: str, scenario_id: str, job_id: int,
                         actor: str = "local operator") -> dict[str, Any]:
        """Link a fire-behaviour model job's provenance to a scenario.
        See `scenarios.ScenarioStore.attach_model_run`."""
        return self.scenarios.attach_model_run(incident_id, scenario_id, job_id, actor)

    def list_model_runs(self, incident_id: str, scenario_id: str | None = None) -> list[dict[str, Any]]:
        """List model runs attached to an incident, newest first.
        See `scenarios.ScenarioStore.list_model_runs`."""
        return self.scenarios.list_model_runs(incident_id, scenario_id)

    # ---------------------------------- safety checklist & scenario approval
    def set_safety_checks(self, incident_id: str, period_id: str, scenario_id: str | None,
                          checks: Iterable[dict[str, Any]], actor: str = "local operator") -> list[dict[str, Any]]:
        """Upsert entries in a period/scenario safety checklist.
        See `scenarios.ScenarioStore.set_safety_checks`."""
        return self.scenarios.set_safety_checks(incident_id, period_id, scenario_id, checks, actor)

    def get_safety_checks(self, period_id: str, scenario_id: str | None = None) -> list[dict[str, Any]]:
        """Return the full checklist in SAFETY_CHECKS order.
        See `scenarios.ScenarioStore.get_safety_checks`."""
        return self.scenarios.get_safety_checks(period_id, scenario_id)

    def approve_scenario(self, incident_id: str, scenario_id: str, approver: str,
                         acknowledge_warnings: bool = False,
                         expected_revision: int | None = None) -> dict[str, Any]:
        """Approve a scenario; unticked safety checks require an explicit
        acknowledgement. See `scenarios.ScenarioStore.approve_scenario`."""
        return self.scenarios.approve_scenario(
            incident_id, scenario_id, approver, acknowledge_warnings, expected_revision
        )

    # ----------------------------------------------------- tactical features
    def create_feature(self, incident_id: str, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Create a tactical/observational map feature (revision 1).
        See `features.FeatureStore.create_feature`."""
        return self.features.create_feature(incident_id, data, actor)

    def list_features(self, incident_id: str, period_id: str | None = None,
                      scenario_id: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
        """List an incident's features as GeoJSON Features.
        See `features.FeatureStore.list_features`."""
        return self.features.list_features(incident_id, period_id, scenario_id, include_deleted)

    def progression(self, incident_id: str, from_time: str, to_time: str) -> dict[str, Any]:
        """Before/after/new-since view of observed fire behaviour.
        See `features.FeatureStore.progression`."""
        return self.features.progression(incident_id, from_time, to_time)

    def update_feature(self, incident_id: str, feature_id: str, data: dict[str, Any],
                       expected_revision: int, actor: str = "local operator") -> dict[str, Any]:
        """Patch a feature (and optionally its geometry), revision-checked.
        See `features.FeatureStore.update_feature`."""
        return self.features.update_feature(incident_id, feature_id, data, expected_revision, actor)

    def delete_feature(self, incident_id: str, feature_id: str, expected_revision: int,
                       actor: str = "local operator") -> dict[str, Any]:
        """Soft-delete a feature, revision-checked.
        See `features.FeatureStore.delete_feature`."""
        return self.features.delete_feature(incident_id, feature_id, expected_revision, actor)

    # ------------------------------------------------------------- resources
    def create_resource(self, incident_id: str, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Create an incident resource (revision 1).
        See `resources.ResourceStore.create_resource`."""
        return self.resources.create_resource(incident_id, data, actor)

    def list_resources(self, incident_id: str) -> list[dict[str, Any]]:
        """List an incident's resources, alphabetical by callsign.
        See `resources.ResourceStore.list_resources`."""
        return self.resources.list_resources(incident_id)

    def update_resource(self, incident_id: str, resource_id: str, data: dict[str, Any],
                        expected_revision: int, actor: str = "local operator") -> dict[str, Any]:
        """Patch a resource, revision-checked.
        See `resources.ResourceStore.update_resource`."""
        return self.resources.update_resource(incident_id, resource_id, data, expected_revision, actor)

    # ---------------------------------------- export, snapshots & comparison
    def export_bundle(self, incident_id: str) -> dict[str, Any]:
        """Serialise an entire incident into one self-contained package.
        See `packages.PackageStore.export_bundle`."""
        return self.packages.export_bundle(incident_id)

    def create_snapshot(self, incident_id: str, name: str, period_id: str | None,
                        classification: str, actor: str = "local operator") -> dict[str, Any]:
        """Freeze the current bundle into an immutable named snapshot.
        See `packages.PackageStore.create_snapshot`."""
        return self.packages.create_snapshot(incident_id, name, period_id, classification, actor)

    def list_snapshots(self, incident_id: str) -> list[dict[str, Any]]:
        """List an incident's snapshots (metadata only), newest first.
        See `packages.PackageStore.list_snapshots`."""
        return self.packages.list_snapshots(incident_id)

    def compare_snapshots(self, incident_id: str, left_snapshot_id: str,
                          right_snapshot_id: str | None = None) -> dict[str, Any]:
        """Diff a snapshot against another snapshot or the live state.
        See `packages.PackageStore.compare_snapshots`."""
        return self.packages.compare_snapshots(incident_id, left_snapshot_id, right_snapshot_id)

    # -------------------------------------------------------- package import
    def preview_import(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """Read-only dry run of an incident package import.
        See `packages.PackageStore.preview_import`."""
        return self.packages.preview_import(bundle)

    def import_bundle(self, bundle: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Apply a package; raises PackageConflict unless it can be applied
        cleanly. See `packages.PackageStore.import_bundle`."""
        return self.packages.import_bundle(bundle, actor)

    # -------------------------------------------------- compatibility surface
    # These underscore-prefixed members were part of the single-class store's
    # de-facto contract before the split and are called from *outside* this
    # package, so the facade keeps forwarding them rather than treating the
    # prefix as permission to drop them:
    #
    #   _audit            - telemetry, drone, field_import, tactics, products
    #                       and merge append their own entity types to the
    #                       incident history through this.
    #   _snapshot_bundle  - products.py builds a public product from either a
    #                       stored snapshot or the live bundle.
    #
    # `_apply_revision_update` has no external caller today; it is forwarded
    # anyway because it is the documented shared write path and forwarding it
    # costs nothing.
    def _audit(
        self, incident_id: str, entity_type: str, entity_id: str,
        action: str, revision: int, payload: dict[str, Any], actor: str,
    ) -> None:
        """Append one row to incident_audit_log and bump the parent
        incident's updated_at. Caller must already hold `db._write_lock`
        inside an open transaction. See `audit.AuditLog.record`."""
        self.audit_log.record(incident_id, entity_type, entity_id, action, revision, payload, actor)

    def _snapshot_bundle(self, incident_id: str, snapshot_id: str) -> dict[str, Any]:
        """Load and ownership-check a stored snapshot's payload.
        See `packages.PackageStore._snapshot_bundle`."""
        return self.packages._snapshot_bundle(incident_id, snapshot_id)

    def _apply_revision_update(
        self, table: str, entity_type: str, incident_id: str, entity_id: str,
        expected_revision: int, changes: dict[str, Any], *, incident_table: bool = False,
        actor: str = "local operator",
    ) -> dict[str, Any]:
        """Shared optimistic-concurrency update path.
        See `base.AggregateStore._apply_revision_update`."""
        return self.incidents._apply_revision_update(
            table, entity_type, incident_id, entity_id, expected_revision, changes,
            incident_table=incident_table, actor=actor,
        )

    _comparison_summary = staticmethod(PackageStore._comparison_summary)
    _comparison_value = staticmethod(PackageStore._comparison_value)
