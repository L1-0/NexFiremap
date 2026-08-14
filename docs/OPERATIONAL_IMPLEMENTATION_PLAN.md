# NexFiremap operational implementation plan

This document converts the operational section of `further_plan.md` into a
release plan. It is deliberately stricter than a feature wish-list: every
phase has an operational outcome, acceptance criteria, failure behaviour and
verification work.

## Mission and safety boundary

NexFiremap should remain useful when the internet and mobile networks are
unavailable. The command laptop is the authoritative local server. Browsers
on the same Wi-Fi or wired LAN are clients. Loss of the public internet must
not prevent viewing cached maps, creating observations, drawing tactics,
reviewing safety items, taking snapshots or exporting a handover.

The application is decision support, not an automatic declaration that a
route, tactic or plan is safe. Observations, estimates and model scenarios
must remain visibly and structurally distinct. Approval means that a named
operator reviewed the recorded warnings. It does not certify safety.

Before operational deployment, the responsible service must validate the
software, symbology, coordinate reference systems, offline map sources,
security configuration and workflows against national doctrine and local
conditions.

## Current implementation baseline (12 August 2026)

The analytical application already provides cached FIRMS detections, cached
basemap tiles, event clustering, observation likelihood, burn-scar analysis,
terrain/weather spread modelling, ensembles, industrial-source separation,
independent EUMETSAT corroboration and model validation.

The first operational vertical slice is now implemented:

- local SQLite incident records with UUID identifiers.
- an automatically created 12-hour operational period and primary Plan A.
- primary, contingency, alternative and worst-case scenario records.
- semantic point, line and polygon drawing on the Leaflet map.
- separate scenario binding for planned objects and observation metadata for
 field objects.
- operational feature status, source, observer, confidence and validity
 fields.
- optimistic revision checks and HTTP 409 conflicts for stale edits.
- append-only audit entries for create, update, approval, deletion and
 snapshot actions.
- a nine-item safety review and explicit acknowledgement of unresolved
 warnings before plan approval.
- resource records and optional last-reported positions.
- immutable incident snapshots.
- a versioned JSON/GeoJSON handover export containing the entire workspace.
- a printable operational map header with incident, period, scenario,
 classification, producer, coordinate centre and freshness statement.
- cached OSM building footprints and a 0-48 hour structure-exposure layer for
 deterministic and ensemble spread runs, including residential/critical counts
 and ensemble early/median/late timing.
- scheduled/manual online SQLite backups with integrity verification, atomic
 publication, retention, status and download.
- full executable tactical-record fields, revisioned form editing and sketch
 vertex undo.
- dry-run incident-package validation and atomic new-incident import, with
 existing-incident packages refused instead of silently overwritten.
- a PWA app shell and last-read fallback for clients temporarily separated
 from the command server.
- explicit local-server connectivity status.
- automated store lifecycle and HTTP smoke tests.

This is a functional operational foundation, not yet a production-certified
incident management system. In particular, package import/merge, offline AOI
map-pack management, full form editing, print products, authentication and
field deployment validation remain release gates.

## Target architecture

```text
Internet available Incident LAN (always local)
FIRMS / weather / imagery / tiles Browser / installed PWA
 | |
 v v
 background fetchers ---> command-laptop FastAPI server
 |
 +----------------------+----------------------+
 | | |
 SQLite/WAL tile/map cache job outputs
 incidents + audit + previously prepared model rasters,
 cached observations AOI coverage exports
```

Operational writes always go to the command server. A service worker may
serve a stale read when the server disappears, but must never pretend that a
write was accepted. Future disconnected editors exchange versioned incident
packages whose object IDs and revisions are merged explicitly.

## Canonical domain model

```text
Incident
 +-- Operational period (time bounded, never overwritten)
 | +-- Plan scenario (primary / contingency / alternative / worst case)
 | | +-- Planned tactical features
 | | +-- Scenario-specific safety review
 | +-- Observed features (not silently scenario-derived)
 +-- Resources and last reported positions
 +-- Immutable snapshots
 +-- Audit history for every material decision
```

All spatial objects use semantic feature types and GeoJSON geometry. A
symbology profile controls appearance without changing meaning. Times are ISO
8601 with offsets. Server audit time is UTC. Soft-deleted objects remain in
handover exports and audit history.

## Release phases

### Phase 0 - Operational record foundation (implemented)

Outcome: an operator can create an incident, work in an operational period,
draw observations and tactics, review a plan, reject stale edits, snapshot the
state and export it without internet access.

Acceptance criteria:

- no operational endpoint makes an upstream network request.
- an incident database survives a process restart.
- stale revision updates return a conflict rather than overwriting data.
- unresolved safety items require explicit acknowledgement during approval.
- deletion removes an object from the live map but preserves its history.
- exports include observations, plans, periods, resources, checks and audit.
- JavaScript/Python syntax, store lifecycle and HTTP smoke checks pass.

### Phase 1 - Hardened incident editing, backup and recovery

Outcome: the first field-testable release can recover from operator mistakes,
power loss and laptop failure.

Work:

- replace prompt-based editing with accessible forms for every operational
 field in `further_plan.md`: objective, responsible unit, assigned resources,
 timing, priority, equipment, water, prerequisites, hazards, escape route,
 safety zone, communications and notes.
- add geometry vertex editing, undo/redo and deliberate save/cancel states.
- add incident, period, scenario and resource update/close workflows with the
 same revision checks as tactical features.
- create a scheduled SQLite online backup with integrity checks, retention and
 a visible last-success time.
- add restore-to-a-new-database and snapshot comparison, never overwriting the
 only known-good database in place.
- implement automatic crash recovery for unfinished sketches in browser local
 storage, clearly marked as unsaved.
- make all timestamps, units and coordinate formats configurable by profile.
- add database migration versioning instead of relying only on idempotent
 `CREATE TABLE` statements.

Acceptance criteria:

- simulated process termination during a write leaves `PRAGMA integrity_check`
 clean and all previously committed decisions readable.
- recovery from the newest backup is documented and tested on a second laptop.
- every editable entity rejects stale revisions.
- keyboard-only drawing metadata and safety review are usable.
- audit history can reconstruct who changed what and when.

### Phase 2 - Prepared offline area-of-operations map packs

Outcome: a fresh client can navigate the complete incident AOI with the WAN
physically disconnected.

Work:

- add an AOI pack wizard with polygon/bbox selection, zoom levels, layer list,
 age, license/attribution and estimated disk size before download.
- cache topographic, orthophoto and approved operational basemaps under each
 provider's permitted offline-use terms.
- ingest GeoPackage/MBTiles and serve them locally. Never assume public OSM
 tile servers permit bulk prefetch.
- import local roads/tracks, gates, bridges, turnarounds, water, buildings,
 infrastructure, boundaries, historical perimeters, firebreaks, aviation
 obstacles and helipads.
- expose layer freshness, source, scale and coverage gaps on the map.
- add DEM-derived contours, slope and aspect that work without an upstream
 elevation service.
- verify cached tile completeness with a deterministic AOI manifest and hash.

Acceptance criteria:

- a documented test AOI is fully navigable at required scales after network
 adapters are disabled.
- missing tiles are conspicuous and never silently replaced by a blank map.
- every layer has attribution, acquisition date and operational limitations.
- storage estimates are within 10% of the finished pack.
- AOI package verification detects a deliberately removed/corrupt file.

### Phase 3 - Observation and import workflow

Outcome: the map becomes the authoritative current fire situation rather than
 only a satellite display.

Work:

- provide complete observation forms for time, source, observer, horizontal
 accuracy/confidence, confirmation state, fire activity/intensity and wind.
- render confirmed perimeter, contained/uncontained edge, active/cold edge,
 spot fires, smoke and satellite anomalies with unmistakably different
 styling and legend entries.
- import/export GeoJSON, GPX, KML/KMZ and CSV with a preview and field mapping.
- ingest GPS waypoints/tracks and drone perimeters/orthomosaics from USB/SD.
- retain original source files, hashes and import reports for provenance.
- add progression comparison between any two snapshots/observation times.
- flag stale observations according to configurable thresholds rather than
 deleting them.

Acceptance criteria:

- imported data is previewed and validated before commit.
- coordinates outside the incident AOI or with ambiguous CRS require review.
- a satellite anomaly cannot be accidentally converted into a confirmed
 perimeter without a named, audited action.
- progression view reproduces the same result after restart/export/import.

### Phase 4 - Executable tactical plans and measurements

Outcome: each map object is an actionable assignment, not only a graphic.

Work:

- implement the full line methods and lifecycle states from `further_plan.md`.
- add anchor/lookout/safety-zone/escape-route/trigger links between objects.
- show which divisions/resources own each action and detect double assignment.
- measure geodesic distance, polygon area, line length and elevation profile.
- add manual spread, arrival-time, wind arrows and uncertainty buffers.
- provide transparent production-rate, hose-lay, water relay and travel-time
 calculators with editable doctrine assumptions.
- warn about obvious spatial conflicts (escape route through forecast area,
 unlinked safety zone, dead-end access) while preserving commander judgment.
- copy selected scenarios/features into the next operational period with
 explicit new revisions and provenance.

Acceptance criteria:

- a sample division assignment can be briefed entirely from its attached
 record and map objects.
- calculator output displays inputs, units, source/profile and assumptions.
- warnings are reproducible, explainable and individually acknowledgeable.
- copying a plan never mutates the preceding operational period.

### Phase 5 - Map products, briefing and handover

Outcome: one incident database produces role-appropriate, reproducible outputs.

Work:

- implement print composer templates for strategic operations, field
 operations, IAP, briefing, transport, air operations, evacuation support,
 progression, public information and handover.
- automatically include incident/period, production/validity time, author,
 classification, scale, grid, CRS, north arrow, legend, freshness and page
 index.
- add PDF, georeferenced PDF, GeoPackage, GeoTIFF, KML/KMZ, GPX, CSV and
 GeoJSON exporters as appropriate.
- enforce an audience/classification filter so operational hazards, resource
 positions and tactics cannot leak into public products by default.
- hash and record every exported product in the audit log.

Acceptance criteria:

- a printed scale bar is within tolerance at the declared paper size.
- georeferenced products round-trip against control points.
- public export automated tests prove restricted fields are absent.
- recreating a product from its recorded snapshot is deterministic.

### Phase 6 - Disconnected collaboration and package merge

Outcome: two teams can exchange edits without silent data loss.

Work:

- add package import with signature/hash verification and a dry-run report.
- merge unchanged/newer objects by UUID and revision.
- surface equal-revision/different-payload and divergent-revision conflicts in
 a side-by-side resolver. Never choose by file modification time.
- preserve both geometries and audit trails until a named operator resolves a
 conflict.
- add package lineage, originating installation ID and base snapshot ID.
- support read-only briefing/public clients and controlled editor roles on the
 incident LAN.

Acceptance criteria:

- deterministic merge tests cover new, unchanged, newer, deleted and divergent
 objects.
- replaying the same package is idempotent.
- no conflict path loses either input version.
- the resulting audit log identifies origin and resolver.

### Phase 7 - Security and multi-client operations

Outcome: the command laptop can safely serve a temporary incident LAN.

Work:

- default to loopback for single-user use and require an explicit LAN mode.
- add local accounts/roles, short sessions, CSRF protection and rate limits.
- provide incident-local TLS setup and certificate trust instructions.
- implement viewer, field editor, plans, safety, administrator and public roles.
- protect exports/backups at rest where required by local policy.
- add an emergency-access procedure that is audited and works without an
 external identity provider.
- harden file imports against archives, path traversal and oversized payloads.

Acceptance criteria:

- an unauthenticated LAN client cannot read operational data.
- role tests cover every modifying endpoint and classified export.
- a security review and dependency/vulnerability scan have no unresolved
 critical findings.
- emergency access is usable with the WAN disconnected.

### Phase 8 - Validated decision support and optional tracking

Outcome: modelling and live position inputs augment, but never replace, the
observation/plan record.

Work:

- attach model version, inputs, freshness, validity, assumptions and uncertainty
 to every forecast scenario.
- add terrain/fuel/weather staleness warnings and local calibration profiles.
- validate forecast arrival/perimeter skill on representative historical
 incidents and publish limitations.
- optionally ingest GPS/radio position reports with explicit last-report time.
- add range/travel/access warnings only after local doctrine validation.
- keep live tracking and automatic optimization out of the critical path.

Acceptance criteria:

- every model layer is labelled `scenario`, never `observation`.
- stale inputs are visible on the map and exports.
- validation metrics and failure cases are available to operators.
- disabling all modelling leaves the complete incident workflow operational.

## Cross-cutting verification programme

Every phase must add unit, HTTP contract and browser interaction tests. Release
candidates also require:

1. WAN-off and command-server-disconnect drills.
2. Power-loss/database recovery drills.
3. Two-client stale-edit and conflict exercises.
4. Coordinate/CRS and printed-scale checks with known control data.
5. Accessibility checks for keyboard use, contrast and outdoor/touch use.
6. Load tests at expected incident feature/resource/client counts.
7. Backup restore and handover to a clean second machine.
8. Tabletop review with incident command, operations, plans, safety, air ops
 and GIS users.
9. Field exercise followed by documented corrective actions.

## Immediate next sprint

The next work should stay focused on making Phase 0 field-testable:

1. Full tactical feature editor and geometry undo/redo.
2. Incident/period/scenario/resource revision-controlled updates.
3. Automated SQLite backup, integrity status and restore workflow.
4. Versioned package import with dry-run conflict reporting.
5. AOI map-pack manifest and completeness checker.
6. Playwright browser tests for drawing, switching periods, approval and
 reconnection.

Print composition and additional prediction features should follow these
reliability items, because preservation and reproducibility of command
decisions are the first operational requirement.

## Field-hardening sprint 2 - implemented contract

This sprint actions the first four reliability items above as one coherent
handover-and-recovery workflow.

### A. Verified local backups

- Use SQLite's online backup API so WAL-backed writes remain consistent.
- Write to a temporary file, run `PRAGMA integrity_check`, then atomically
 publish the finished backup. An interrupted backup must never replace a
 known-good one.
- Create backups on a configurable schedule and on explicit operator request.
- Retain a configurable number, expose age/size/integrity status, and allow
 download. Do not restore over the live database from a web request.

Acceptance: backup integrity is `ok`, retention is deterministic, the backup
opens as an independent NexFiremap database, and failures leave no published
corrupt file.

### B. Executable tactical records and drawing recovery

- Replace prompt-only editing with a form for objective, responsible unit,
 assignments, timing, priority, equipment, water, prerequisites, hazards,
 escape route, safety zone, channel and notes.
- Persist the record in the feature's versioned properties and preserve the
 existing stale-revision conflict response.
- Add last-vertex undo by button and Ctrl/Cmd+Z without affecting browser
 history outside an active sketch.

Acceptance: create and edit round-trip every operational field. A stale form
cannot overwrite a newer record. Line/polygon undo redraws the sketch and
cannot produce invalid geometry.

### C. Safe incident-package intake

- Preview every package before applying it and report schema errors, counts,
 new records, identical records, local-newer records and conflicts.
- Automatically import only a completely new incident package. A package for
 an existing incident must never overwrite by timestamp or revision alone.
 it is rejected with a deterministic conflict report until a future
 side-by-side resolver is implemented.
- Import related entities in foreign-key order inside one transaction, retain
 source UUIDs/revisions/audit entries, and make replay idempotent.

Acceptance: malformed and existing-incident packages make no changes. A new
package imports atomically. Replay reports the existing incident rather than
duplicating it. Exported and re-imported workspaces are semantically equal.

## Field-hardening sprint 3 - implementation contract

This sprint completes the remaining locally testable reliability work before
provider-specific offline downloads and formal field exercises.

### A. Revision-controlled command records

- Add update and close/retire workflows for incidents, operational periods,
 scenarios and resources.
- Require the revision the operator loaded for every update. A stale client
 receives the current record and cannot overwrite it.
- Audit every accepted change. Editing an approved scenario's substantive
 planning fields returns it to draft and clears the prior approval.
- Expose accessible edit forms in the incident workspace. Do not rely on
 free-form browser prompts for these records.

Acceptance: all four entity types round-trip their fields, increment exactly
one revision per accepted edit, reject stale edits, and retain the actor and
payload in the incident audit log.

### B. Recoverable unsaved sketches

- Persist the active sketch, context and entered tactical metadata in browser
 local storage after every vertex and material form change.
- On reload, offer to restore a matching incident draft and label it clearly
 as unsaved. Deliberate cancel or successful save removes the recovery copy.
- Ignore malformed, expired or incident-mismatched drafts.

Acceptance: a simulated page termination after multiple vertices can restore
the exact sketch and record fields. Cancel/save clears it. Invalid recovery
data cannot execute code or create an invalid geometry.

### C. Deterministic offline coverage manifests

- Build a manifest for an operator-selected AOI, approved cached layer and zoom
 range without triggering bulk upstream tile downloads.
- Enumerate every expected slippy-map tile, record source attribution, size,
 modification time and SHA-256 for present files, and list coverage gaps.
- Persist manifests atomically and provide repeat verification. Missing or
 modified files must be reported explicitly rather than rendered as a
 silently complete pack.
- Cap manifest size/AOI tile count to keep accidental world-scale requests
 from exhausting the command laptop.

Acceptance: the expected tile set is deterministic. Completeness is accurate.
verification detects a removed or byte-modified tile. Unknown layers, invalid
bounds and excessive tile counts are rejected without filesystem mutation.

This is a cache-readiness checker, not a provider scraper. Automated pack
acquisition is enabled only after an administrator configures a source whose
licence and service policy explicitly permit offline bulk download.

## Field-hardening sprint 4 - implementation contract

This sprint turns recovery, change briefing and administrator-supplied offline
maps into repeatable local workflows.

### A. Restore only to a new verified database

- A verified backup may be materialized as a separate recovery database. The
 running database is never replaced, renamed or reopened by an HTTP request.
- Copy through SQLite's backup API, run `PRAGMA integrity_check`, publish the
 result atomically, and expose it for download to a clean machine.
- Retain source backup name, creation time, recovery time, size and integrity.
 Reject invalid names and path traversal.

Acceptance: the recovery database opens independently and contains committed
incident records. Corrupt input publishes nothing. The live database path and
contents remain unchanged.

### B. Deterministic snapshot comparison

- Compare two immutable snapshots, or a snapshot with the current workspace,
 by semantic entity UUID and revision.
- Report added, removed and changed incidents, periods, scenarios, features,
 resources and safety checks, with concise before/after summaries.
- Comparison is read-only, stable across restart and does not use timestamps
 alone to decide equality.

Acceptance: known changes are classified consistently, unchanged workspaces
 produce zero changes, and snapshots from another incident are rejected.

### C. Administrator-supplied MBTiles basemaps

- Accept an MBTiles file uploaded directly to the local command server. No
 external provider is contacted and no URL-based server-side download exists.
- Stream into a bounded temporary file, validate SQLite integrity, required
 MBTiles tables/metadata, tile count, format and zooms, then atomically publish
 it with operator-supplied source, attribution, acquisition date and licence
 notes.
- Serve XYZ browser requests from the MBTiles TMS tile rows and include valid
 sources in the normal basemap selector after reload.
- Preserve the original package and metadata. Removal is excluded until a
 deliberate archive/delete workflow exists.

Acceptance: valid PNG/JPEG/WebP MBTiles render without WAN access. Malformed,
oversized or empty packages leave no published source. Tile lookup flips the Y
axis correctly. Every source exposes attribution and operational limitations.
