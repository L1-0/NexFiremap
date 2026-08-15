"""Pydantic request bodies for every JSON-accepting route in `routes/`.

These live in one module rather than next to their handlers because a
number of them are shared across routers (e.g. `IncidentPackageRequest` is
posted to both the import-preview and import-apply routes, which sit in
different modules) and because keeping the whole request surface in one
file makes the validation rules - the min/max lengths, the `pattern=`
enums, the `expected_revision` optimistic-concurrency fields - reviewable
side by side instead of scattered across twenty files.

The field constraints here are load-bearing: they are the *only* input
validation most handlers do before passing a payload straight through to
an `OperationsStore`/manager method, so widening one silently widens what
reaches the database.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# --------------------------------------------------------------- cache

class EnsureRequest(BaseModel):
    bbox: str = Field(..., description="west,south,east,north")
    days: int = Field(7, ge=1, le=60)
    sources: list[str] | None = None


# ---------------------------------------------------------------- jobs

class JobSubmitRequest(BaseModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


# -------------------------------------------------------------- events

class EventDetectRequest(BaseModel):
    bbox: str = Field(..., description="west,south,east,north")
    days: int = Field(30, ge=1, le=60)
    sources: list[str] | None = None
    v_max_kmh: float = Field(8.0, gt=0, le=200)
    max_dt_hours: float = Field(168.0, gt=0, le=24 * 60)
    min_detections: int = Field(2, ge=1, le=1000)
    max_span_km: float = Field(100.0, ge=10, le=2000)


class SpreadTopologyRequest(BaseModel):
    bbox: str = Field(..., description="west,south,east,north")
    days: int = Field(3, ge=1, le=60)
    sources: list[str] | None = None


class EventAnalyzeRequest(BaseModel):
    tau_hours: float = Field(6.0, gt=0, le=168)
    resolution_m: float = Field(100.0, ge=30, le=2000)
    reference_ts: float | None = None


class BurnScarRequest(BaseModel):
    resolution_m: float = Field(20.0, ge=10, le=200)


class PropagationRequest(BaseModel):
    resolution_m: float = Field(60.0, ge=20, le=500)
    reference_ts: float | None = None


class EnsembleRequest(BaseModel):
    resolution_m: float = Field(60.0, ge=20, le=500)
    reference_ts: float | None = None
    n_members: int = Field(60, ge=5, le=300)
    random_seed: int | None = None


class ValidationRequest(BaseModel):
    n_splits: int = Field(3, ge=1, le=10)
    threshold: float = Field(0.3, gt=0, lt=1)


# -------------------------------------------------- industrial / structures

class IndustrialScanRequest(BaseModel):
    bbox: str = Field(..., description="west,south,east,north")
    days: int = Field(30, ge=1, le=60)
    window_days: int = Field(30, ge=1, le=60)
    event_id: int | None = None


class StructureScanRequest(BaseModel):
    bbox: str = Field(..., description="west,south,east,north")


# ----------------------------------------------------------- incidents

class IncidentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    incident_number: str | None = Field(None, max_length=100)
    timezone: str = Field("UTC", max_length=80)
    center_lat: float | None = Field(None, ge=-90, le=90)
    center_lon: float | None = Field(None, ge=-180, le=180)
    notes: str = Field("", max_length=10000)


class IncidentUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    name: str | None = Field(None, min_length=1, max_length=300)
    incident_number: str | None = Field(None, max_length=100)
    status: str | None = Field(None, pattern="^(active|contained|closed)$")
    timezone: str | None = Field(None, min_length=1, max_length=80)
    center_lat: float | None = Field(None, ge=-90, le=90)
    center_lon: float | None = Field(None, ge=-180, le=180)
    notes: str | None = Field(None, max_length=10000)


class PeriodCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    starts_at: str
    ends_at: str
    objectives: str = Field("", max_length=10000)


class PeriodUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    name: str | None = Field(None, min_length=1, max_length=300)
    starts_at: str | None = None
    ends_at: str | None = None
    status: str | None = Field(None, pattern="^(draft|active|closed)$")
    objectives: str | None = Field(None, max_length=10000)


class ScenarioCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    kind: str = Field("primary", pattern="^(primary|contingency|alternative|worst_case)$")
    description: str = Field("", max_length=10000)
    assumptions: str = Field("", max_length=10000)


class ScenarioUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    name: str | None = Field(None, min_length=1, max_length=300)
    kind: str | None = Field(None, pattern="^(primary|contingency|alternative|worst_case)$")
    status: str | None = Field(None, pattern="^(draft|retired)$")
    description: str | None = Field(None, max_length=10000)
    assumptions: str | None = Field(None, max_length=10000)


class ScenarioApproveRequest(BaseModel):
    approver: str = Field(..., min_length=1, max_length=200)
    acknowledge_warnings: bool = False
    expected_revision: int | None = Field(None, ge=1)


class ScenarioCopyRequest(BaseModel):
    target_period_id: str
    name: str = Field("", max_length=300)


class ModelAttachRequest(BaseModel):
    job_id: int = Field(..., ge=1)


class SafetyUpdateRequest(BaseModel):
    scenario_id: str | None = None
    checks: list[dict[str, Any]]


# ------------------------------------------------- cross-module links

class IncidentLinkRequest(BaseModel):
    """Attach an observation or analysis result to an incident.

    `snapshot` is required rather than optional and is validated in
    `LinkStore.add_link` (non-empty, size-bounded): a link without one is a
    live reference, which is exactly what the links table exists to avoid -
    events get re-clustered and models re-run, so a plan justified by "the
    09:40 run" has to keep meaning that afterwards.
    """

    kind: str = Field(..., min_length=1, max_length=40)
    ref_id: str = Field(..., min_length=1, max_length=200)
    snapshot: dict[str, Any] = Field(...)
    note: str = Field("", max_length=1000)


class IncidentAoiRequest(BaseModel):
    """Set or clear an incident's area of interest.

    Either a GeoJSON Polygon or a [west,south,east,north] bbox - the two ways
    an operator produces one (drawing a shape, dragging a rectangle). Both are
    normalised to a Polygon on the way in so nothing downstream branches on
    shape. Sending neither clears the AOI.
    """

    aoi: dict[str, Any] | None = None
    bbox: list[float] | None = Field(None, min_length=4, max_length=4)


class IncidentFromContextRequest(BaseModel):
    """Create an incident from something the operator right-clicked.

    Exactly one of `event_id`, `bbox`, or `lat`+`lon` identifies the source;
    the route rejects a request carrying none of them.
    """

    event_id: int | None = None
    bbox: str | None = Field(None, max_length=100, description="west,south,east,north")
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    radius_km: float | None = Field(None, gt=0, le=200)
    days: int = Field(3, ge=1, le=60)
    name: str | None = Field(None, max_length=300)
    notes: str = Field("", max_length=10000)


# ------------------------------------------------------------ features

class TacticalFeatureCreateRequest(BaseModel):
    period_id: str | None = None
    scenario_id: str | None = None
    feature_type: str
    title: str = Field("", max_length=300)
    status: str = "observed"
    geometry: dict[str, Any]
    properties: dict[str, Any] = Field(default_factory=dict)
    observed_at: str | None = None
    source: str | None = Field(None, max_length=200)
    observer: str | None = Field(None, max_length=200)
    confidence: str | None = Field(None, max_length=50)
    valid_from: str | None = None
    valid_to: str | None = None


class TacticalFeatureUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    title: str | None = Field(None, max_length=300)
    status: str | None = None
    geometry: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None
    observed_at: str | None = None
    source: str | None = Field(None, max_length=200)
    observer: str | None = Field(None, max_length=200)
    confidence: str | None = Field(None, max_length=50)
    valid_from: str | None = None
    valid_to: str | None = None


# ------------------------------------------------------------- tactics

class TacticalCalculatorRequest(BaseModel):
    kind: str = Field(..., min_length=1, max_length=80)
    inputs: dict[str, Any]


class TacticalWarningAckRequest(BaseModel):
    period_id: str
    scenario_id: str | None = None
    warning_id: str = Field(..., min_length=1, max_length=100)
    reason: str = Field(..., min_length=1, max_length=1000)


# ----------------------------------------------------------- resources

class ResourceCreateRequest(BaseModel):
    callsign: str = Field(..., min_length=1, max_length=100)
    unit_type: str = Field(..., min_length=1, max_length=100)
    status: str = Field("available", max_length=40)
    crew_size: int | None = Field(None, ge=0, le=1000)
    water_capacity_l: float | None = Field(None, ge=0)
    capabilities: str = Field("", max_length=10000)
    assignment: str = Field("", max_length=10000)
    contact_channel: str = Field("", max_length=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    position_at: str | None = None


class ResourceUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    callsign: str | None = Field(None, min_length=1, max_length=100)
    unit_type: str | None = Field(None, min_length=1, max_length=100)
    status: str | None = Field(None, pattern="^(available|assigned|working|returning|unavailable)$")
    crew_size: int | None = Field(None, ge=0, le=1000)
    water_capacity_l: float | None = Field(None, ge=0)
    capabilities: str | None = Field(None, max_length=10000)
    assignment: str | None = Field(None, max_length=10000)
    contact_channel: str | None = Field(None, max_length=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    position_at: str | None = None


# --------------------------------------------------------------- feeds

class PositionReportRequest(BaseModel):
    resource_id: str | None = None
    callsign: str = Field(..., min_length=1, max_length=200)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    observed_at: str
    report_source: str = Field(..., pattern="^(gps|radio|manual)$")
    accuracy_m: float | None = Field(None, ge=0, le=100000)
    period_id: str | None = None
    scenario_id: str | None = None


class PositionFeedCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    provider: str = Field("", max_length=200)
    device_kind: str = Field("vehicle_gps", max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PositionFeedUpdateRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    name: str | None = Field(None, min_length=1, max_length=200)
    provider: str | None = Field(None, max_length=200)
    device_kind: str | None = Field(None, max_length=80)
    active: bool | None = None
    metadata: dict[str, Any] | None = None


# --------------------------------------------------------------- drone

class DroneMissionCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    aircraft: str = Field("", max_length=200)
    operator: str = Field("", max_length=200)
    started_at: str | None = None
    ended_at: str | None = None
    notes: str = Field("", max_length=10000)


class DroneMosaicCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    asset_ids: list[str] = Field(..., min_length=1, max_length=500)


# ------------------------------------------------ snapshots / products

class SnapshotCreateRequest(BaseModel):
    name: str = Field("", max_length=300)
    period_id: str | None = None
    classification: str = Field("operational", pattern="^(draft|operational|public)$")


class ProductCreateRequest(BaseModel):
    format: str = Field(..., pattern="^(geojson|json|csv|gpx|kml|kmz|pdf|geopdf|geotiff|gpkg)$")
    classification: str = Field(..., pattern="^(draft|operational|public)$")
    product_type: str
    snapshot_id: str | None = None
    title: str = Field("", max_length=300)


# ------------------------------------------------- transfer / map packs

class IncidentPackageRequest(BaseModel):
    bundle: dict[str, Any]


class MergeStageRequest(BaseModel):
    bundle: dict[str, Any]


class MergeResolveRequest(BaseModel):
    choices: dict[str, str]


class MapPackCreateRequest(BaseModel):
    name: str = Field("Offline AOI check", max_length=200)
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    layers: list[str] = Field(..., min_length=1, max_length=20)
    min_zoom: int = Field(..., ge=0, le=22)
    max_zoom: int = Field(..., ge=0, le=22)


# ------------------------------------------------------------- backups

class RecoveryCreateRequest(BaseModel):
    backup_name: str = Field(..., min_length=1, max_length=200)


# ------------------------------------------------------- field imports

class FieldImportRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=300)
    content: str = ""
    content_base64: str = ""
    format: str = Field("", max_length=20)
    period_id: str | None = None
    source: str = Field("", max_length=500)
    observer: str = Field("", max_length=200)
    default_feature_type: str = Field("", max_length=80)
    mapping: dict[str, str] = Field(default_factory=dict)
    aoi_bbox: list[float] = Field(..., min_length=4, max_length=4)
    acknowledge_outside_aoi: bool = False
    confirmation_reason: str = Field("", max_length=1000)


# -------------------------------------------------------- custom layers

class CustomLayerProbeRequest(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=2000)
    kind: str = Field("wms", max_length=10)


class CustomLayerCreateRequest(BaseModel):
    """One operator-added WMS/WMTS/XYZ layer.

    Almost everything is optional here and the real checking happens in
    `LayerRegistry._validate`, because the required fields depend on `kind`:
    a WMS row needs `endpoint` + `wms_layers`, a template row needs
    `url_template`. Expressing that dependency in pydantic would either mean
    two request models with a discriminator or validators duplicating the
    registry's rules - and the registry has to enforce them anyway, since it
    is also reached by `update`'s merge path.
    """

    name: str = Field(..., min_length=1, max_length=200)
    kind: str = Field(..., min_length=1, max_length=10)
    url_template: str = Field("", max_length=2000)
    endpoint: str = Field("", max_length=2000)
    wms_layers: str = Field("", max_length=500)
    wms_styles: str = Field("", max_length=300)
    wms_version: str = Field("1.3.0", max_length=10)
    wms_crs: str = Field("EPSG:3857", max_length=40)
    image_format: str = Field("image/png", max_length=60)
    transparent: bool = True
    overlay: bool = False
    attribution: str = Field("", max_length=500)
    # Not decoration: an offline map pack carries these into the field, where
    # whoever reads the map has no other way to learn what they may do with a
    # third party's cadastre or orthophoto.
    licence: str = Field("", max_length=500)
    limitations: str = Field("", max_length=1000)
    min_zoom: int = Field(0, ge=0, le=22)
    max_zoom: int = Field(19, ge=0, le=22)


class CustomLayerUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    kind: str | None = Field(None, min_length=1, max_length=10)
    url_template: str | None = Field(None, max_length=2000)
    endpoint: str | None = Field(None, max_length=2000)
    wms_layers: str | None = Field(None, max_length=500)
    wms_styles: str | None = Field(None, max_length=300)
    wms_version: str | None = Field(None, max_length=10)
    wms_crs: str | None = Field(None, max_length=40)
    image_format: str | None = Field(None, max_length=60)
    transparent: bool | None = None
    overlay: bool | None = None
    attribution: str | None = Field(None, max_length=500)
    licence: str | None = Field(None, max_length=500)
    limitations: str | None = Field(None, max_length=1000)
    min_zoom: int | None = Field(None, ge=0, le=22)
    max_zoom: int | None = Field(None, ge=0, le=22)
    active: bool | None = None


# ------------------------------------------------------------- webhooks

class WebhookCreateRequest(BaseModel):
    """One inbound CAD/dispatch webhook.

    `mapping` is validated by `ingest/webhook.validate_mapping` rather than by
    pydantic: the rules are about the *expressions* (dotted paths and literal
    templates only, never anything evaluable) and about which target fields
    exist, neither of which a type annotation can express.
    """

    name: str = Field(..., min_length=1, max_length=200)
    incident_id: str = Field(..., min_length=1, max_length=64)
    source_id: str = Field(..., min_length=1, max_length=64)
    mapping: dict[str, object] = Field(...)


class WebhookUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    mapping: dict[str, object] | None = None
    active: bool | None = None


# ---------------------------------------------------------------- auth

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=1000)


class AccountCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    role: str = Field(..., min_length=1, max_length=30)
    password: str = Field(..., min_length=12, max_length=1000)


# ------------------------------------------------------------ settings

class SettingsUpdateRequest(BaseModel):
    """A partial settings update: only the names present are touched.

    `values` is an open dict rather than one optional field per setting,
    because the set of editable settings is defined once in
    `settings_store.EDITABLE` and duplicating it here would create two lists to
    keep in step. Validation - unknown names, type coercion, the closed
    symbology vocabulary - therefore happens in `settings_store.write`, which
    rejects rather than silently storing.
    """

    values: dict[str, object] = Field(..., min_length=1)
