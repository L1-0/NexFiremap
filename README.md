# NexFiremap

**A self-contained wildfire operations desk.** One local server that takes an incident
from first satellite detection through planning, tasking, safety review, live unit
tracking, and formal handover - and keeps working when the WAN does not.

It runs on a command laptop or a small incident-LAN box: FastAPI + SQLite, a vanilla-JS
frontend, no build step, no cloud tenancy, no vendor account. Only NASA FIRMS needs a
(free) key, and the map works without it.

Deployment and emergency-recovery procedure: [`INCIDENT_LAN_RUNBOOK.md`](docs/INCIDENT_LAN_RUNBOOK.md).
Remaining release gates: [`END_TO_END_TODO.md`](docs/END_TO_END_TODO.md).

## Four workspaces, one map

The header switcher changes which desk you are working at. All four share one map, one
database and one incident context, so a detection seen in one is the same object planned
against in another.

| Workspace | The job it does |
|---|---|
| **NexFiremap** | Satellite fire detections, satellite coverage, industrial-source screening, cache and filters |
| **NexEventView** | Grouping detections into discrete fires and running behaviour analysis on one of them |
| **NexIncidentCommand** | Incidents, operational periods, scenarios, tactical drawing, safety review, resources, products, handover |
| **NexIngest** | Live telemetry, drone imagery, field observation import, wind observations, offline map packages |

## Quick start

**Easiest**: run the setup wizard. It creates a virtual environment, installs
dependencies package-by-package with fallbacks, walks you through the FIRMS key, and
tests reachability of every external source it knows about before starting the server
(FIRMS, Celestrak, Planetary Computer, Open-Meteo, OSM tiles, EUMETSAT, Overpass,
AWS Terrarium and Nominatim).

| Platform | Install (once) | Start |
|---|---|---|
| Windows | double-click `install.bat` | double-click `start.bat` |
| macOS | double-click `install.command` | double-click `start.command` |
| Linux | `./install.sh` | `./start.sh` |

(On macOS/Linux, if double-clicking does not work the first time, run `chmod +x
install.sh install.command start.sh start.command` once, or just `bash install.sh` -
the wizard sets the executable bit on the others afterwards.)

**Manual**:

```powershell
python -m pip install -r requirements.txt
copy .env.example .env
notepad .env   # paste your FIRMS_MAP_KEY
python run.py --open
```

Open http://127.0.0.1:8000. The wizard is re-runnable at any time
(`python scripts/setup_wizard.py`) to add a key later or re-check connectivity; it reuses
the existing virtual environment and never overwrites a setting you did not change.

A free FIRMS map key comes from
[firms.modaps.eosdis.nasa.gov/api/map_key](https://firms.modaps.eosdis.nasa.gov/api/map_key/).
Without one the map, the whole incident-command side, and every non-FIRMS feed still work -
a banner explains what is missing until the key is set.

---

# Operational procedures

Everything below runs against the local SQLite database and needs no upstream account or
public internet. This is the part of the application that is doing the work during an
incident; the analytical layers exist to inform it.

## 1. Standing up an incident

Two routes in, depending on whether you are starting from something you can see:

- **From a detection or an area**: right-click the map (or right-drag a rectangle) and
  choose **Create incident here**. One request creates the incident, its first
  operational period, its first scenario, an area of interest from the event bounding box
  or the dragged rectangle, and a snapshot link recording what was observed at that
  moment. The incident is named by reverse geocoding, e.g. "Wildfire near Ismaning".
- **Manually**: **new incident** in the Incident command block. The map centre is
  recorded and a 12-hour operational period with a primary **Plan A** is created.

Set the **operator / unit** name early. It is attributed to every record you write, every
acknowledgement, and the audit trail.

## 2. The operational cycle

The intended rhythm is **See → Assess → Commit → Plan → Execute → Verify → Feed back**,
with no retyping step in the middle.

1. **Assess** a point with right-click → **Situation here**. Every module answers about
   that one spot: nearest detections and their age, the event they belong to, modelled
   arrival band, structures at risk, warnings in force, nearest tracked unit, next
   satellite overpass.
2. **Plan** by drawing. Select an object type and status, choose **draw on map**, then
   click once for a point, or click vertices for a line/area and press Enter or
   **finish**. **undo vertex** or Ctrl/Cmd+Z removes the last vertex mid-sketch. Planned
   objects attach to the selected scenario, so primary and contingency geometry stay
   distinct and comparable.
3. **Record the intent, not just the shape.** Expand **Operational record** before
   drawing (or **edit** an existing object) to capture objective, responsible unit,
   assignments, timing, priority, method, equipment, water, prerequisites, hazards,
   escape and safety links, communications and notes. This is what makes a line a task
   rather than a drawing.
4. **Advance the period** with **next period** rather than overwriting the preceding
   plan. Add alternatives with **new scenario** (`primary`, `contingency`,
   `alternative`, `worst_case`).
5. **Verify** against what actually happened: new detections inside the incident AOI
   surface via **Observed progression comparison** and `GET /api/operations/watch`.

Every save carries the revision it was loaded at. A stale edit is rejected with the
current record rather than silently overwriting a colleague's newer one.

## 3. Safety review

Complete the safety review before approving a scenario. Nine checks are tracked
explicitly:

`hazards identified` · `lookouts assigned` · `communications plan recorded` ·
`escape routes identified` · `safety zones identified` · `medical extraction route
recorded` · `alternative access and egress checked` · `withdrawal trigger points
defined` · `weather and fire-behaviour update is current`

Unresolved warnings require explicit acknowledgement by a named operator with a stated
mitigation reason, and that acknowledgement stays in the audit and handover record.
**This is a completeness checklist, not an automated safety certification** - it records
that a human considered each item, and nothing more.

**Tactical assessment** runs a deterministic scan over the period: broken links,
double-assigned resources, escape routes crossing a forecast or uncertainty boundary.
Each warning has a stable content hash, so it survives recomputation without losing a
prior acknowledgement.

## 4. Automatic protection of tracked units

This is the loop that makes the physics protective rather than decorative. On **every**
accepted position report, two evaluations run:

- Is this unit inside a hazard area, burn area or evacuation area?
- Does the model attached to the active scenario expect fire to reach this position
  within the warning horizon?

Either raises a warning into the audit trail. Withdrawal warnings key off the
**earliest** arrival in the modelled band, never the median. The evaluation runs after
the position commits and can never raise, because a failed evaluation must not cost the
position that triggered it - a unit vanishing off the map is the worse failure.

Only **built** control lines rasterise into the propagation model as barriers.
`proposed` and `planned` lines are deliberately excluded: feeding a line that does not
exist yet into the model would produce a forecast that flatters the plan.

## 5. Resources and logistics

Resources are revision-controlled records with status
(`available` · `assigned` · `working` · `returning` · `unavailable`), assignment, and
their own audit history. They are separate from telemetry: a resource is what you have
committed, a position report is where a device says it is.

## 6. Snapshots, products and handover

- **Immutable snapshots** freeze the workspace for briefing or handover. Two snapshots,
  or a snapshot and current records, can be compared as a deterministic semantic change
  report.
- **Products** are classified deliverables generated from a snapshot or live export:
  strategic, field, IAP, briefing, transport, air operations, evacuation, progression,
  public information, handover, plus **ICS 201 / 202 / 204** and German **Lagekarte**
  rendered as the real form layouts. Formats: PDF, GeoPDF, GeoJSON, JSON, CSV, GPX, KML,
  KMZ, GeoTIFF, GeoPackage (raster or real vector features).
  Generation is deterministic - the same bundle and format produce byte-identical output,
  so a product's stored SHA-256 is a real integrity check.
- **Public products** are locked down structurally: only the `public_information`
  template can produce a `public` classification, only an allowlist of feature types
  survives redaction, and public accounts can reach only that filtered catalogue.
- **export handover** writes the versioned workspace, including audit history, as
  JSON/GeoJSON.

## 7. Backups and recovery

- Scheduled and manual **verified backups**: SQLite online backup, integrity-checked
  *before* atomic publication, retained locally (default every 15 min, keep 24).
- **Recovery** materialises a verified backup as a *separate* database for download or
  comparison. It never switches or overwrites the running database.
- Use **verified backup** before any handover or material planning change.

## 8. Importing another team's work

**Import handover** always dry-runs first.

- A genuinely new incident imports atomically.
- An incident that already exists locally is **refused with a conflict report** rather
  than overwritten. Every record is classified `new` / `unchanged` / `incoming_newer` /
  `local_newer` / `divergent`, decided by revision, not timestamps.
- To actually reconcile, **stage** the package (parsed, hashed, deduplicated by content,
  so re-staging the same file is a no-op) and **resolve** it entity by entity, choosing
  local or incoming. There is no automatic three-way field merge, because guessing which
  side is right is exactly the decision a human has to own.

## 9. Working disconnected

The WAN going down is an expected operating state, not an error.

1. Before disconnecting, open **Offline AOI coverage**, pick a cached layer and zoom
   range, and check the current view. Only a result marked **READY FOR OFFLINE USE** is
   complete, and every per-zoom row must pass. The manifest records every expected tile
   with attribution, size and SHA-256, so repeat verification detects files removed or
   modified since. A complete manifest also pins those tiles against ordinary TTL and
   size-budget eviction.
2. Public tile providers are **not** bulk-prefetched. For deliberate all-zoom coverage,
   import an authorised MBTiles, GeoTIFF or raster GeoPackage under **Import local
   MBTiles basemap**, recording attribution, acquisition date, permitted use and
   limitations. It then appears in the normal basemap list.
3. LAN clients show whether the command server is reachable. The PWA shell and last
   successful reads are cached; **writes are never fabricated or silently queued.**
4. Unfinished tactical sketches are stored in the browser after each vertex and material
   field change. After an interrupted session, NexFiremap offers to restore the matching
   sketch, explicitly labelled unsaved. Saving or deliberately cancelling clears it.

## 10. Multi-seat incident LAN

Single-user on loopback needs no login and no configuration. Shared operation is opt-in
and fails closed - see [`INCIDENT_LAN_RUNBOOK.md`](docs/INCIDENT_LAN_RUNBOOK.md) for the
full procedure: enable LAN mode explicitly, configure TLS, create least-privilege named
accounts, run a WAN-off drill, and keep backups on separate encrypted media.

Roles: `viewer` · `field_editor` · `plans` · `safety` · `administrator` · `public`.
LAN mode refuses to start with an admin password under 12 characters. Sessions are opaque
bearer tokens held in memory only, so nothing survives a restart by design.
The local admin password always remains as break-glass, because an identity provider is
often on the far side of the link that just failed.

---

# Data and feeds

## Fire detection sources

| Source | What it gives |
|---|---|
| **VIIRS** NOAA-20 / NOAA-21 / Suomi-NPP (NRT) | 375 m active-fire detections, the primary working product |
| **MODIS** (NRT) | 1 km detections, longer heritage |
| **LANDSAT** (NRT) | Available via `NEXFIREMAP_SOURCES`, higher resolution, sparser revisit |
| **EUMETSAT MTG/FCI** *(optional, free account)* | Independent geostationary corroboration, ~10-minute revisit, full disk -67.5° to +67.5°. Fire-confidence class and probability per pixel, **not** a radiative-power value |

Cached in SQLite for a rolling 30 days (configurable), so history stays available without
re-downloading and without hammering NASA's API. NRT products are provisional and may be
revised upstream.

## Inbound operational feeds

Every one of these is **off unless configured**, and every one normalises into the *same*
internal contracts. A Cursor on Target position and a JSON one take the identical write
path, with the same token authentication, rate limiting, replay-safety hashing and
provenance. Adding a standard means adding a parser in `nexfiremap/ingest/`, never a
second write path.

| Standard | How it connects |
|---|---|
| **Cursor on Target (TAK)** | POST CoT XML to the feed endpoint, or enable the TCP/UDP gateway for ATAK multicast and TAK Server federation |
| **NMEA 0183** | POST sentences as `text/plain` to the feed endpoint |
| **OsmAnd / Traccar** | `GET /api/feeds/positions/{id}/osmand?lat=…&lon=…&token=…` - the protocol most phone trackers already speak |
| **MAVLink v2** | `GLOBAL_POSITION_INT` over UDP, via the same gateway as CoT |
| **MQTT** | `NEXFIREMAP_MQTT_URL` plus a topic→feed table. Survives brief link outages where a stream of HTTP POSTs would fail. Needs optional `aiomqtt` |
| **CAD / dispatch webhooks** | `POST /api/webhooks` with a dotted-path field mapping - no vendor module needed. Failed mappings go to a dead-letter record |
| **CAP warnings** | `NEXFIREMAP_CAP_FEEDS` with presets `dwd`, `mowas` (Germany), `nws`, `ipaws` (US), or any CAP URL. Official warnings drawn beside the fire picture |
| **WMS / WMTS / XYZ** | `POST /api/layers` - a Kreis or county GIS layer becomes a row, then caches, pins and packs offline exactly like a built-in basemap |
| **Shapefile** | Zipped `.shp/.dbf/.prj`. WGS84, Web Mercator and WGS84 UTM are reprojected; anything needing a datum shift is refused **by name** rather than silently misplaced |
| **OIDC / LDAP** | Federated sign-in that mints the ordinary local session |

Two limits worth knowing before deploying any of it:

- **The CoT/MAVLink gateway is unauthenticated by protocol design.** CoT over UDP has no
  token and no session, and MAVLink v2 signing is not verified. It is off by default,
  binds loopback, and enforces a CIDR allowlist. See the runbook.
- **The OIDC flow does not verify ID-token signatures locally.** It reads claims from the
  provider's token and userinfo endpoints over TLS, so the provider's certificate is the
  trust anchor.

## Field observation import

GeoJSON, GPX, KML, KMZ, CSV, GeoPackage and Shapefile are parsed **locally** (XXE blocked
on all XML paths), previewed against the current-view AOI, and applied only after the
required outside-AOI and confirmation acknowledgements. A JSON field mapping handles
arbitrary CSV column names. Original bytes, SHA-256 and the import report stay in the
incident handover. Old observations remain visible with a configurable stale warning
(default 6 h).

## Live telemetry and drone imagery

- **Vehicle telemetry** is provider-neutral: latitude, longitude, observed-at, plus
  optional speed, heading and accuracy. Raw observations are **immutable** - never edited
  or deleted, only superseded. Everything derived (track segmentation at gaps or
  implausible jumps, interpolation, stale and quality flags) is explicitly labelled as
  derived. Stale positions render grey, quality warnings red. Automatic positions remain
  **unconfirmed** and are shown separately from manual position reports.
- **Drone imagery** retains the original plus a thumbnail with SHA-256 provenance, tied
  to a mission. Images stay **evidence-only** unless an operator affirms `nadir` or
  `orthorectified` and supplies ordered TL/TR/BR/BL WGS84 corners. Georeferencing is a
  deliberate four-corner GCP warp - no feature matching, no camera model, not
  survey-grade photogrammetry. Selected frames combine into a deterministic
  "first wins" mosaic so overlap never depends on filesystem ordering. EXIF/DJI-XMP
  flight metadata can *suggest* a footprint, which the operator still has to affirm.
- **Wind observations** are recorded as points with speed in m/s and meteorological
  **from** direction. **Temporal wind situation** renders downwind arrows for the
  selected time and view from retained measurements and attached model provenance only.
  It makes no network request and does not claim terrain-resolved wind.

## Basemaps and map layers

OpenStreetMap, Humanitarian OSM, OpenTopoMap, CyclOSM, Carto Positron/Dark, Esri World
Imagery/Terrain - plus anything an operator adds as WMS/WMTS/XYZ, and any validated local
MBTiles. Every tile is proxied and cached through `/tiles/{basemap}/{z}/{x}/{y}.png`, so
panning back over an area costs no repeat download and already-viewed tiles keep working
offline.

Coordinates read and write in WGS84 decimal, DMS, DDM, UTM, MGRS, British National Grid,
Irish Grid, Swiss LV95, French Lambert-93, Dutch RD New, NZTM, SWEREF99 TM, GDA2020 MGA,
or a custom proj4 string. The search box also jumps straight to a typed `lat, lon` pair
with **no network call at all**, so that shortcut still works with the WAN down.

## Terrain, weather and imagery (analysis inputs)

All free and keyless: Copernicus DEM GLO-30 and ESA WorldCover (via Microsoft Planetary
Computer STAC, anonymous signed access), Open-Meteo (weather history and near-term
backfill), Celestrak (orbital elements), OpenStreetMap Overpass (industrial sources and
building footprints), AWS Open Data Terrarium tiles (3D elevation), Nominatim (place
search, server-proxied per its usage policy). Every fetch is windowed to the event's own
small AOI - never a whole-scene download.

## Tactical symbology

Three profiles carried simultaneously, selected with `NEXFIREMAP_SYMBOLOGY_PROFILE`:
`simplified_multinational` (default), `dv102` (German *Taktische Zeichen*), and
`nfpa170_ics` (US). One table keyed by feature type holds a code in every scheme at once,
and every rendered map names the profile it was drawn in.

38 feature types are modelled semantically rather than as free drawing - control and
tactical lines, perimeters (confirmed, forecast, uncertainty), active and inactive edge,
safety zones, escape routes, lookouts, anchor and trigger points, division and branch
boundaries, staging, helibase, helispot, drop and dip sites, water sources and
restrictions, road closures, evacuation and structure-protection areas, hazards, critical
values, spot fires, smoke reports, weather stations and wind observations.

---

# Analysis that feeds the desk

Click **find in view** in *Fire events* to cluster cached detections into discrete fires
via a space-time graph (not a bounding box). Pick one to analyse, which unlocks
progressively heavier layers, each run as a background job.

| Layer | What it is | Module |
|---|---|---|
| **Satellite coverage** | How recently a tracked satellite actually passed over each area - real orbit propagation from Celestrak TLEs, not cloud-masked | `orbits.py` |
| **Spread over time** | Cumulative detection footprint contoured at each distinct satellite overpass, as nested rings from earliest to latest | `events.py` |
| **Likelihood** | Active-heat probability raster: a Gaussian kernel per detection weighted by sensor/confidence/FRP, time-decayed, then discounted where a valid pass confirmed no fire | `likelihood.py` |
| **Arrival time** | Inverse-distance-weighted estimate of when fire likely reached each cell, with an honest earliest/median/latest spread | `likelihood.py` |
| **Uncertainty envelopes** | Real 50/80/90% probability-*mass* contours, filled as concentric bands with a km²/mi² callout - not an arbitrary threshold | `likelihood.py` |
| **Burn scar** | Sentinel-2/Landsat NBR/dNBR severity, pre- vs post-fire - optical ground truth, not thermal inference | `imagery.py` |
| **Modelled spread** | Rothermel (1972, Albini 1976) surface-fire kernel with Anderson-13 fuel physics and real dead-fuel-moisture conditioning, solved **anisotropically** so fire spreads faster downwind and upslope | `rothermel.py`, `moisture.py`, `terrain.py` |
| **Ensemble** | ~40-member Monte Carlo over wind speed/direction, fuel moisture and spread-rate uncertainty, each member scored against the event's own detections | `terrain.py` |
| **Structures at risk** | Cached OSM building footprints against a 0-48 h arrival slider, counting residential and critical facilities separately | `structures.py` |
| **Validate model** | Rolling-holdout backtest against held-out detections and classical baselines | `validation.py` |
| **3D terrain** | The active layer draped over real elevation via MapLibre GL JS | frontend |

**Tri-state observation model**: a valid satellite pass that saw *no* fire is real
negative evidence and measurably suppresses the likelihood there. A cell no satellite ever
looked at is left untouched, never treated as "confirmed clear." A free historical
cloud-cover sample softens each pass's weight toward "unknown" when the sky likely was not
clear, rather than trusting swath geometry alone.

**Validation programme**: `docs/further_plan.md` §11 says do not ship the polished model
unless it beats the baselines. **validate model** scores the served kernel-density model
against `buffered_footprint`, `concave_hull`, `constant_radial` and `wind_ellipse` on
precision/recall/F1, a point-based Jaccard proxy, Brier score, centroid displacement,
predicted area, missed disconnected fronts, and arrival-time error.

**Persistent thermal-source classifier**: not every hot pixel is a wildfire.
`industrial.py` implements "OSM creates a candidate, recurrence confirms it, stationarity
strengthens it, propagation or burn evidence overrides it" as a transparent fixed-weight
score - not a trained classifier. It flags, never deletes, and a wildfire-overrides-
industrial check downgrades matches when detections clearly grow beyond a source's own
footprint.

## Documented simplifications

Stated so results are read for what they are:

- One Anderson-13 fuel model per ESA WorldCover class via a best-fit crosswalk, not a
  locally calibrated or Scott-Burgan-40 fuel map.
- Wind is one Open-Meteo point sample for the whole AOI, and dead-fuel moisture is one
  value per size class - no WindNinja-grade terrain-channelled downscaling. Slope and
  aspect still locally deflect the fire's own spread direction.
- No crown fire, no spotting, no full Nelson moisture PDE.
- The ensemble's assimilation is batch weighted calibration, not a fully recursive
  particle filter.
- Burn-scar dNBR can be a cross-sensor difference with no bandpass harmonisation -
  flagged in the job's own `cross_sensor` field, not silently absorbed.
- "Persistence" for industrial screening reflects only this server's retention window
  (default 30 days), not years of behaviour.
- The event-analysis layers are research-grade estimates, not an authoritative
  fire-behaviour product. They say so in their own status text.

---

# Design rules that hold the desk together

Four rules are enforced in code, not just documented, and are worth knowing before
changing anything:

**Cross-module data crosses as an immutable snapshot with provenance, never a live
reference.** Events get re-clustered (their ids are not stable), models get re-run, and
detections age out on the retention window. A plan line justified by "the 09:40
propagation run" has to keep meaning that at 11:00 - and the snapshot is the only thing
that carries meaning across a handover, where the receiving installation has none of the
source events or jobs. `add_link` refuses a link without one.

**"We did not ask" and "there is nothing there" must never look the same.** A situation
provider that fails, or whose optional dependency is missing, is **named** in the
response's `unavailable` list rather than quietly dropped. Someone deciding whether to
send a crew has to be able to tell those apart.

**Modelled arrival is reported as an earliest/median/latest band, never one number**, and
is always labelled modelled rather than observed.

**A new standard is a new parser, never a new write path.** Adapters in
`nexfiremap/ingest/` only translate bytes into an existing contract. They never touch the
database, open a socket, or read a setting - which is what guarantees a new format cannot
bypass the authentication, rate limiting, replay-safety, preview-before-apply or
provenance the receiving managers enforce.

Graceful degradation follows the same spirit: the heavier analysis modules pull in
optional dependencies, and if one fails to install (`rasterio` has historically had the
most platform friction), the server still starts, the core fire map still works, and that
one feature reports itself unavailable via `/api/config` with its buttons disabled and
explained - instead of the whole app crashing.

---

# How the caches work

## Fire detections

The FIRMS *area* API answers only a few days at a time per request. NexFiremap turns
"30 days for this viewport" into chunked requests automatically:

1. The world is divided into a coverage grid (10° cells by default). For every
   `(source, cell, day)` the server remembers whether it has been fetched.
2. On pan, zoom or range change, only the still-missing combinations are queued. Cached
   days are served straight from SQLite with no network call.
3. Missing days are grouped into contiguous runs and split into the largest chunk FIRMS
   accepts, auto-detected and stepped down if a source proves stricter.
4. The last 2 days are "hot" and re-checked periodically (default 45 min) because
   satellites keep adding to them. Older days are effectively final and never re-fetched.
5. An hourly job purges beyond the retention window.
6. Zooming out far enough switches to a single whole-world request instead of one per
   cell.

## Basemap tiles

Every tile is served from disk if cached and within TTL (default 30 days), otherwise
fetched once and saved. Simultaneous requests for the same tile coalesce into one upstream
fetch; concurrent upstream requests are capped (default 8) so a fast pan cannot burst a
provider; oldest tiles are evicted past a size budget (default 1 GB). Tiles pinned by a
complete offline manifest are exempt from eviction.

Set `NEXFIREMAP_CONTACT` to add contact info to the tile User-Agent, per
[OSM's tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

## Background jobs

Event clustering, likelihood rasters, imagery, physics propagation and structure exposure
all run through a shared queue (`nexfiremap/jobs.py`):

- Work runs in separate OS **processes** (`ProcessPoolExecutor`), so numpy/scipy code is
  not GIL-bound.
- Each worker drops to **idle OS priority**, so this only spends cycles nothing else
  wants.
- Jobs are SQLite rows with live progress, so status survives a restart. A stalled job
  past `NEXFIREMAP_JOB_TIMEOUT_S` is abandoned and reported rather than occupying a slot
  forever, and a killed worker breaks the pool once before the queue self-heals.

Registered kinds: `detect_events`, `spread_topology`, `analyze_event`,
`analyze_burn_scar`, `run_propagation`, `run_ensemble_assimilation`, `validate_event`,
`swath_coverage`, `scan_industrial_sources`, `scan_structures`, `scan_eumetsat_fires`.

---

# Configuration

All settings are environment variables, or put them in `.env` - see
[.env.example](.env.example) for the full annotated list. Operators can additionally
override many of them at runtime through an admin-only settings API stored in the
database, because asking someone to edit a dotfile and restart at 03:00 is not a workable
answer. API keys are write-only: never read back out.

| Variable | Default | Meaning |
|---|---|---|
| `FIRMS_MAP_KEY` | *(required for fire data)* | Free FIRMS map key |
| `NEXFIREMAP_SOURCES` | VIIRS ×3 + MODIS | Which satellite products to pull |
| `NEXFIREMAP_CACHE_DAYS` | `30` | Days of detection history retained |
| `NEXFIREMAP_HOST` / `_PORT` | `127.0.0.1` / `8000` | Bind address |
| `NEXFIREMAP_TILE_CACHE_DAYS` | `30` | Tile freshness before re-fetch |
| `NEXFIREMAP_TILE_CACHE_MAX_MB` | `1024` | Tile cache size budget |
| `NEXFIREMAP_JOB_WORKERS` | auto (cpu-1) | Background worker processes |
| `NEXFIREMAP_JOB_TIMEOUT_S` | `1800` | Abandon a job past this |
| `NEXFIREMAP_BACKUP_INTERVAL_MINUTES` | `15` | Scheduled backups (`0` disables) |
| `NEXFIREMAP_BACKUP_KEEP` | `24` | Verified backups retained |
| `NEXFIREMAP_OBSERVATION_STALE_HOURS` | `6` | Field observation stale warning |
| `NEXFIREMAP_POSITION_STALE_SECONDS` | `300` | Automatic position labelled stale |
| `NEXFIREMAP_POSITION_MAX_SPEED_KMH` | `180` | Segment speed that splits a track |
| `NEXFIREMAP_LAN_MODE` | `false` | Multi-seat mode (fails closed) |
| `NEXFIREMAP_ADMIN_PASSWORD` | *(none)* | Required, min 12 chars, in LAN mode |
| `NEXFIREMAP_TLS_CERT_FILE` / `_KEY_FILE` | *(none)* | Direct HTTPS, both required together |
| `NEXFIREMAP_SYMBOLOGY_PROFILE` | `simplified_multinational` | `dv102`, `nfpa170_ics` |
| `NEXFIREMAP_CAP_FEEDS` | *(none)* | `dwd`, `mowas`, `nws`, `ipaws`, or URLs |
| `NEXFIREMAP_COT_ENABLED` | `false` | TAK gateway (see runbook before enabling) |
| `NEXFIREMAP_MQTT_URL` | *(none)* | MQTT broker for telemetry |
| `NEXFIREMAP_OIDC_ISSUER` / `_LDAP_HOST` | *(none)* | Federated sign-in |
| `EUMETSAT_CONSUMER_KEY` / `_SECRET` | *(none)* | Optional geostationary corroboration |

---

# API

152 endpoints across 29 router modules. The frontend is a thin client over it, and it is
useful on its own. `nexfiremap/api.py` is the composition root only - settings, lifespan,
security middleware, exception handlers and router registration.

**Fire data and cache**
- `GET /api/config` - sources, basemaps, cache settings, and `features` (which optional
  modules loaded)
- `GET /api/detections?bbox=W,S,E,N&days=7&sources=…&confidence=…&min_frp=…` - cached
  detections as compact rows or `&fmt=geojson`; `&autofetch=true` queues missing data
- `GET /api/summary` · `GET /api/status` · `GET /api/coverage`
- `POST /api/cache/ensure` · `POST /api/cache/refresh` · `POST /api/cache/purge` ·
  `POST /api/tiles/purge`
- `GET /api/geocode?q=…` · `GET /api/geocode/reverse?lat=…&lon=…`

**Events and analysis** (POST routes return `{job_id}`)
- `POST /api/events/detect` · `GET /api/events` · `GET /api/events/{id}`
- `POST /api/detections/spread_topology`
- `POST /api/events/{id}/analyze` · `/burn-scar` · `/propagate` · `/ensemble` ·
  `/validate`
- `POST /api/industrial/scan` · `GET /api/industrial/sources`
- `POST /api/structures/scan` · `GET /api/structures/exposure`

**Jobs**
- `POST /api/jobs` · `GET /api/jobs/{id}` · `GET /api/jobs?status=&kind=` ·
  `GET /api/jobs/{id}/files/{name}`

**Incident command**
- `GET/POST /api/operations/incidents` · `GET/PATCH …/{id}`
- Period and scenario create/update, `POST …/scenarios/{id}/copy`
- `GET/POST …/features` · `PATCH/DELETE …/features/{feature}` - revision-controlled
- `GET/PUT …/periods/{id}/safety` · `POST …/scenarios/{id}/approve`
- `GET/POST/PATCH …/resources` · `GET/POST …/snapshots` ·
  `GET …/snapshots/{left}/compare`
- `GET/POST …/model-runs` - attach a completed job as scenario provenance
- `GET/POST …/products` · `GET …/products/{id}/download`
- `GET …/export` · `POST /api/operations/import/preview` · `/import/apply`
- `POST /api/operations/merge/stage` · `GET …/merge-packages` ·
  `POST /api/operations/merge/{id}/resolve`

**The operator's loop**
- `GET /api/situation?lat=…&lon=…` - every module's answer about one point
- `POST /api/operations/incidents/from_context` - incident + period + scenario + AOI +
  link in one transaction
- `GET/POST/DELETE …/links` · `GET/PUT …/aoi` ·
  `GET /api/operations/incidents/covering` · `GET /api/operations/watch`

**Feeds, telemetry and ingest**
- `GET/POST/PATCH …/position-feeds` · `POST …/position-feeds/{id}/rotate-token`
- `POST /api/feeds/positions/{source_id}` - CoT, NMEA, OsmAnd/Traccar or native JSON
- `GET …/vehicle-positions/latest` · `…/vehicle-tracks` · `…/vehicle-positions/interpolate`
- `GET /api/operations/incidents/{id}/cot` - render the incident for ATAK to poll ·
  `GET /api/feeds/cot/status`
- `GET/POST/PATCH/DELETE /api/webhooks` · `POST /api/ingest/webhook/{hook_id}` ·
  `GET /api/webhooks/{id}/failures`
- `GET /api/alerts` · `/alerts/status` · `/alerts/{id}/original` · `POST /api/alerts/refresh`
- `GET/POST/PATCH/DELETE /api/layers` · `POST /api/layers/probe`
- `GET/POST …/drone-missions` · `…/assets` · `…/suggest-georeference` · `…/mosaics`
- `GET …/wind-field` · field-import `preview`/`apply`/`progression`/provenance routes

**Resilience and offline**
- `GET/POST /api/operations/backups` · `POST …/{name}/verify` · `GET …/{name}/download`
- `GET/POST /api/operations/recoveries` · `GET …/{name}/download`
- `GET/POST /api/operations/map-packs` · `GET …/{manifest}` · `POST …/{manifest}/verify`
- `GET/POST /api/operations/offline-sources` · `GET /offline-tiles/…`

---

# Project layout

```
nexfiremap/
  api.py               Composition root: settings, lifespan, middleware, router registration
  schemas.py           Pydantic request bodies for every JSON-accepting route
  routes/              29 APIRouter modules (incidents, feeds, drone, links, products, …)
  operations/          Per-aggregate stores behind one facade
    incidents.py         Incidents, periods, scenarios
    features.py          Revision-controlled tactical/observation features
    scenarios.py         Scenarios, safety review, attached model runs
    resources.py         Logistics records
    packages.py          Export bundles, import preview/apply
    links.py             Analytical↔operational links and the incident AOI
    audit.py  base.py  vocab.py  errors.py  common.py
  ingest/              Stateless format adapters - translate only, never write
    cot.py  cap.py  nmea.py  mavlink.py  shapefile.py  webhook.py

  --- operational ---
  telemetry.py         Replay-safe provider-neutral position ingest
  safety.py            Evaluates every tracked unit against hazards and the model
  situation.py         "What is here, and how long have I got?" provider registry
  drone.py             Drone evidence, four-corner georeferencing, deterministic mosaics
  tactics.py           Measurements, deterministic warning scan, field calculators
  products.py          Deterministic classified PDF/vector/raster products
  symbology.py         Feature-type symbol codes in three profiles at once
  wind.py              Offline wind field from retained observations
  merge.py             Staged side-by-side disconnected package resolution
  provenance.py        Model lineage, reference-vs-generated time, freshness
  security.py          Local accounts, sessions, CSRF, role policy
  federation.py        OIDC/LDAP minting the ordinary local session
  settings_store.py    Operator-editable settings over the environment
  field_import.py      Previewed GeoJSON/GPX/KML/KMZ/CSV/GPKG/SHP intake
  backups.py           Verified atomic SQLite backups
  map_packs.py         Offline AOI manifests, completeness, SHA-256 verification
  offline_sources.py   Validated local MBTiles ingestion and XYZ serving
  photogrammetry.py    Footprint *suggestion* from EXIF/DJI-XMP flight metadata

  --- feeds ---
  cot_gateway.py       TCP/UDP listener for CoT and MAVLink
  alerts.py            CAP polling, storage and expiry
  mqtt.py              MQTT broker subscriber
  webhooks.py          CAD/dispatch hook registry and dead-letter record
  layers.py            Operator-added WMS/WMTS/XYZ layers

  --- analytical ---
  cache.py             Coverage-grid planner, fetch queue, retention
  firms.py             FIRMS area API client
  tiles.py             On-disk tile cache and proxy
  jobs.py              Background job queue (idle-priority processes)
  orbits.py            Satellite swath coverage (Celestrak + skyfield)
  events.py            Space-time clustering, spread-over-time contours
  likelihood.py        Active-heat raster, arrival IDW, uncertainty envelopes
  imagery.py           Sentinel-2/Landsat STAC, NBR/dNBR severity
  rothermel.py         Rothermel kernel + Anderson-13 fuel table
  moisture.py          Dead-fuel moisture conditioning
  terrain.py           DEM/fuel/weather orchestration, anisotropic solve, ensemble
  validation.py        Rolling-holdout backtests vs classical baselines
  industrial.py        Persistent thermal-source classifier
  structures.py        Building footprints and temporal exposure
  eumetsat.py          MTG/FCI corroboration (optional)

  --- shared ---
  db.py                SQLite schema and versioned migrations
  geo.py               Shared geodesy/projection helpers
  config.py            Settings from environment/.env
  basemaps.py          Basemap, overlay and terrain-DEM definitions
  geocode.py           Nominatim search and reverse proxy
  rasterpng.py         Stdlib-only PNG encoder
  static/              Frontend: ES modules, no bundler
    js/                app.js, operations.js, structures.js, coords.js, context.js
    vendor/            Vendored Leaflet, plugins, MapLibre, proj4, mgrs - no CDN
scripts/
  setup_wizard.py      Cross-platform installer (venv, deps, .env, connectivity tests)
  vendor_assets.py     Re-download vendored assets, pinned and SHA-256 verified
docs/                  Design notes, runbook, model reference, implementation plans
tests/                 48 standalone test scripts
```

## Tests

There is no pytest runner. Each file under `tests/` is a standalone script with its own
`main()` that prints failures and returns an exit code.

```powershell
python tests\test_core.py
```

Run everything, stopping at the first failure:

```powershell
foreach ($f in Get-ChildItem tests\test_*.py) { python $f.FullName; if ($LASTEXITCODE -ne 0) { break } }
```

Coverage spans the fetch pipeline, tile cache, job queue, clustering, likelihood, the
Rothermel regression table from `docs/firemodel.md`, moisture, terrain, validation,
industrial screening, structures, every ingest adapter (CoT, CAP, NMEA, MAVLink,
Shapefile, webhook), the CoT gateway, MQTT, OIDC, security, operations, incident
import/merge, backups, map packs, offline sources, products, provenance, tactics,
symbology, settings, migrations, accessibility, and browser workflows.

Most tests need no network and no FIRMS key (HTTP is stubbed via `httpx`'s
`MockTransport`). The exceptions are documented: `test_imagery.py` and parts of
`test_terrain.py` exercise the math offline, while the STAC/DEM/weather fetch paths were
verified live against real Sentinel-2, Copernicus DEM, ESA WorldCover and Open-Meteo data,
because `pystac_client`/`rasterio` use their own HTTP transport and bypass the mock.

---

# Notes

## Optional: EUMETSAT corroboration

`docs/further_plan.md` §13 suggests EUMETSAT LSA SAF FRP as a European corroboration
source. The classic MSG/SEVIRI FRP-PIXEL product needs a separate LSA SAF registration on
top of a EUMETSAT account, which is why it stayed out. The **EUMETSAT Data Store** itself
(same account, no extra registration) serves an actively produced successor: "Active Fire
Monitoring (netCDF) - MTG - 0 degree" from MTG-I1/FCI.

1. Register free and generate a consumer key/secret at
   [api.eumetsat.int/api-key](https://api.eumetsat.int/api-key/).
2. Add `EUMETSAT_CONSUMER_KEY` / `EUMETSAT_CONSUMER_SECRET` to `.env`. Only the
   key/secret are stored; the server exchanges them for a short-lived access token itself
   rather than storing one that would go stale.
3. Restart. An "EUMETSAT confirmation" toggle appears (hidden entirely without an
   account), drawing small blue markers distinct from FIRMS detections because this is a
   second, independently sourced instrument.

**Not built, and why**: Copernicus Sentinel-3 SLSTR FRP needs its own Copernicus Data
Space account and OAuth2 client - a second registration this project declines to ask for.
EFFIS's burned-area WFS is free and keyless and would be genuinely valuable (it is the
curated reference perimeter source the validation Jaccard caveat points at), but was
returning a JRC-side backend outage when investigated - worth retrying, not worth building
blind against an unverified schema.

## Data sources and attribution

[NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) (NRT products are provisional),
[Celestrak](https://celestrak.org), [Microsoft Planetary
Computer](https://planetarycomputer.microsoft.com) (Sentinel-2, Landsat, Copernicus DEM,
ESA WorldCover), [Open-Meteo](https://open-meteo.com), [OpenStreetMap Overpass
API](https://overpass-api.de/), [AWS Open Data terrain
tiles](https://registry.opendata.aws/terrain-tiles/), [EUMETSAT Data
Store](https://data.eumetsat.int) (optional), [Nominatim](https://nominatim.org)
(server-proxied per its usage policy). Basemap tiles are © OpenStreetMap contributors and
the respective providers listed in the layer switcher; attribution is preserved in the
map's attribution control.

## License

[MIT](LICENSE) - the copyright holder in that file is a placeholder ("NexFiremap
contributors"). Swap it for your own name before publishing.

Vendored libraries keep their upstream licenses, all permissive and MIT-compatible:
Leaflet (BSD-2-Clause), Leaflet.markercluster (MIT), Leaflet.heat/simpleheat
(BSD-2-Clause), MapLibre GL JS (BSD-3-Clause), proj4js (MIT), mgrs (MIT).

**Code license is a separate question from data terms.** MIT on the code is fine
regardless; these matter only if you redistribute the *data* this server caches, not the
software that fetches it:

- **OpenStreetMap** data is [ODbL](https://opendatacommons.org/licenses/odbl/). The
  in-map attribution covers displaying it. Redistributing cached tiles or data as a
  dataset needs ODbL's share-alike terms satisfied separately.
- **NASA FIRMS** has its own [use
  policy](https://www.earthdata.nasa.gov/data/tools/firms/faq) - credit NASA/FIRMS, do
  not imply endorsement.
- Copernicus/ESA and Open-Meteo are openly licensed for reuse with attribution, already
  credited here and in the app's own source-attribution text.

**Before you publish**: keep operational credentials only in the gitignored `.env`, and
run a repository secret scan. Design notes, exported incident packages, source files, map
packs and backups can all contain sensitive operational information and need their own
release review. The public-information product filter is the supported public export path.
