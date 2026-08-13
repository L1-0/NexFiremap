# NexFiremap end-to-end execution ledger

Updated: 13 August 2026. This is the authoritative completion checklist for
turning the prototype into a field-validation candidate. A checked item has
implemented code and automated verification. It does not imply regulatory or
brigade certification.

## Foundation and recovery

- [x] Offline SQLite incident, period, scenario, feature, safety and resource records.
- [x] Optimistic revisions and audit history for every editable command record.
- [x] Tactical drawing forms, undo and browser crash recovery.
- [x] Atomic incident handover import for new incidents and conflict preview for existing ones.
- [x] Scheduled/manual verified backups and separate recovery databases.
- [x] Snapshot comparison.
- [x] Versioned database migrations, automatic pre-migration backup and rollback drill.
- [ ] Restore and handover drill on a physically separate clean machine. **External exercise gate.**

## Offline area of operations

- [x] Persistent cache and deterministic tile completeness/hash manifests.
- [x] Validated local raster MBTiles import and serving.
- [x] EPSG:4326 GeoPackage vector intake and raster GeoPackage/GeoTIFF map ingestion.
- [x] DEM-derived local contours/slope/aspect packaging with hashes.
- [ ] Licensed provider pack acquisition. **Blocked until an administrator supplies a provider and terms permitting bulk offline use.**
- [ ] WAN-disabled AOI navigation exercise on target tablets. **External hardware gate.**

## Phase 3 - authoritative observations and imports

- [x] Complete field-observation metadata and freshness warnings.
- [x] Preview/apply GeoJSON, GPX, KML and CSV imports with AOI/CRS review.
- [x] Preserve original source bytes, SHA-256 and import report.
- [x] Explicit named/audited confirmation gate for imported confirmed observations.
- [x] Temporal progression comparison on the map.
- [x] KMZ and georeferenced GeoTIFF/raster-GeoPackage drone orthomosaic ingestion.

## Phase 4 - executable tactics

- [x] Geodesic line length, perimeter and area measurements.
- [x] Typed links among anchor, lookout, escape, safety and trigger objects.
- [x] Resource double-assignment detection.
- [x] Explainable spatial safety screening warnings with named, reasoned acknowledgements.
- [x] Configurable production, hose, water-duration and travel calculators.
- [x] Copy plans into the next period without mutating the source period.

## Phase 5 - products and handover

- [x] Classified strategic, field, IAP, briefing, transport, air, evacuation, progression, public and handover data-product templates.
- [x] Deterministic PDF composition with coordinate grid, CRS, legend, freshness and page index.
- [x] Audience filtering with automated public-data leakage tests.
- [x] GeoJSON, JSON, CSV, GPX and KML exports.
- [x] Raster GeoPackage, GeoTIFF, KMZ and GDAL georeferenced PDF exports.
- [x] Product hashes and audit records.

## Phase 6 - disconnected merge

- [x] Installation/package lineage and base snapshot identifiers.
- [x] Deterministic merge of new, unchanged, newer and deleted UUID records.
- [x] Side-by-side divergent-record resolver preserving both inputs in the staged package.
- [x] Idempotent replay and resolver audit trail.

## Phase 7 - incident LAN security

- [x] Loopback default.
- [x] Explicit fail-closed LAN mode and prominent startup warning.
- [x] Local accounts, role policy, short sessions and CSRF protection.
- [x] Viewer, field editor, plans, safety, administrator and isolated public roles.
- [x] Login rate limits, security headers and hardened bounded imports.
- [x] Direct local TLS configuration and emergency-access/recovery runbook.
- [ ] Dependency/security scan without unresolved critical findings. **External release gate.**

## Phase 8 - decision support

- [x] Scenario-labelled likelihood, terrain spread, ensemble uncertainty and temporal structure exposure.
- [x] Rolling historical validation metrics and baseline comparisons.
- [x] Unified model provenance/freshness/validity record attached to scenarios and products.
- [x] Visible terrain, fuel and weather staleness/limitation warnings.
- [x] Optional audited GPS/radio position-report intake.

## Phase 9 - central-planning telemetry and drone imagery

- [x] Provider-neutral, token-isolated, replay-safe vehicle position feeds on the incident LAN.
- [x] Immutable raw observations with freshness, temporal tracks, gap/implausibility flags and labelled interpolation.
- [x] Drone mission/evidence intake with original hashes, previews, footprints and strict georeferencing gates.
- [x] Deterministic local GeoTIFF and mosaic derivation registered as offline map layers.
- [x] Role, CSRF, request-bound, audit, replay, geospatial, clean-root relocation and offline tile tests.
- [ ] Brigade gateway, aircraft/camera, privacy/retention, calibration, load and disconnected field exercise. **External operational gates.**

## Phase 10 - temporal wind situation map

- [x] Structured incident wind observations with explicit units, direction, gust and measurement height.
- [x] Offline wind field from observations and already-attached model weather provenance.
- [x] Temporal filtering, vector-component interpolation, background residuals and visible uncertainty/support.
- [x] Planning-map arrows, source markers, freshness legend and tests.
- [ ] Station adapters, instrument/terrain calibration and disconnected validation exercise. **External operational gates.**

## Phase 11 - all-zoom offline tile assurance

- [x] Deterministic tile enumeration and completeness for every selected integer zoom.
- [x] Per-layer/per-zoom missing and modified reporting. Readiness requires every row.
- [x] Complete-manifest pinning against ordinary cache TTL and size pruning.
- [x] Imported MBTiles and locally renderable raster coverage integrated into map-pack verification.
- [x] Provider-policy boundary prevents prohibited automatic public-tile prefetch.
- [ ] Verify the authorised production map package and storage budget for each deployment AOI. **External GIS gate.**
- [ ] Locally validated doctrine/calibration profiles. **External doctrine gate.**

## Release verification

- [x] Unit, API and real-browser initialization coverage.
- [ ] Automated browser interactions for drawing, recovery, approval and reconnect.
- [x] Two-client revision conflict and 5,000-object incident load tests.
- [x] Automated keyboard, form-label and contrast guardrails.
- [ ] Touch and outdoor accessibility review on target tablets. **External hardware gate.**
- [ ] Power-loss and WAN-off drills on target hardware. **External hardware gate.**
- [ ] Tabletop review and field exercise with command, operations, plans, safety, air and GIS users. **External brigade gate.**
- [ ] Corrective actions from field exercise closed and signed off. **External certification gate.**

## Completion rule

Software work is complete only when its tests pass and the relevant operator
workflow is documented. The application becomes a field release candidate only
after every non-external item above is checked. It becomes operationally
accepted only when the named external gates are completed by the responsible
authority. NexFiremap must never claim that status automatically.
