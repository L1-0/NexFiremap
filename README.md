# NexFiremap

Implementation status and remaining release gates are tracked in
[`END_TO_END_TODO.md`](docs/END_TO_END_TODO.md). Incident-LAN deployment and emergency
recovery procedures are in [`INCIDENT_LAN_RUNBOOK.md`](docs/INCIDENT_LAN_RUNBOOK.md).

A local server that overlays [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) active-fire
detections on a choice of OpenStreetMap-based basemaps, with a 30-day local cache so
history stays available without re-downloading it and without hammering NASA's API.

Beyond the map, NexFiremap does something docs/further_plan.md (this repo's own design notes)
asks for explicitly: it keeps **Observed** detections, **Estimated** likelihood,
**Modelled** propagation, and **Uncertainty** as separate, honestly-labelled layers,
using classical statistics/geostatistics and physics - no machine learning - all computed
locally, with expensive work deferred to background jobs at idle OS priority.

- **Fire data**: VIIRS (NOAA-20 / NOAA-21 / Suomi-NPP) and MODIS NRT detections, cached in
 SQLite and kept for a rolling 30 days (configurable).
- **Basemaps**: OpenStreetMap, Humanitarian OSM, OpenTopoMap, CyclOSM, Carto Positron/Dark,
 Esri World Imagery/Terrain - pick one from the panel.
- **Tile cache**: every basemap tile is proxied through the server and saved to disk, so
 panning back over an area already viewed costs no repeat download, either from NASA or
 from the tile provider.
- **Satellite coverage**: real orbit propagation (Celestrak TLEs) shows how recently each
 spot was actually watched by a tracked satellite - a geometric approximation, not
 cloud/quality-masked.
- **Fire events**: detections are grouped into discrete fires via a space-time graph, not
 just "everything in a box."
- **Estimated extent**: an active-heat likelihood raster, an inverse-distance-weighted
 arrival-time estimate with an honest earliest/median/latest spread, and real 50/80/90%
 probability-mass uncertainty envelopes.
- **Optical burn-scar anchoring**: Sentinel-2/Landsat NBR/dNBR severity mapping, pre/post
 fire, via free Microsoft Planetary Computer imagery.
- **Modelled propagation**: a real Rothermel (1972, Albini 1976-corrected) surface-fire
 spread kernel - proper fuel-model physics, dead-fuel moisture conditioned from real
 weather history, not a flat per-land-cover multiplier - solved **anisotropically**
 (fire genuinely spreads faster downwind/upslope, slower into the wind, via the
 standard Albini & Chase 1980 elliptical model), plus a Monte Carlo ensemble with
 sequential assimilation against the event's own detections.
- **Tri-state observation model**: a valid satellite pass over an event's area that saw
 *no* fire is real negative evidence and measurably suppresses the likelihood there - a
 cell no satellite ever looked at is left untouched, never treated as "confirmed clear."
 A "valid pass" is geometric (was the AOI in swath), so it can't tell a genuinely clear
 sky from a cloudy one on its own. A free historical cloud-cover sample (Open-Meteo)
 softens each pass's suppression weight toward "unknown" when the sky likely wasn't
 clear, rather than trusting swath geometry alone.
- **Validation programme**: rolling-holdout backtests score the model actually served
 (kernel-density likelihood) against real held-out detections *and* classical,
 non-learned baselines (buffered footprint, concave hull, constant radial growth, a
 wind-driven ellipse) - precision/recall/Brier/centroid-error, not vibes.
- **Persistent thermal-source classifier**: separates recurring industrial heat (flares,
 kilns, refineries, power plants - from OpenStreetMap) from genuine wildfire using this
 server's own cached detection history, a transparent rule-based score, and a
 wildfire-overrides-industrial safeguard - never deletes a detection, only flags it.
- **Filled probability-mass rendering**: an event's estimated extent draws as concentric
 filled bands (not bare outlines), with a callout showing the core area in km²/mi² and
 when it was last updated.
- **3D terrain view**: drapes the active analysis layer over real elevation
 (MapLibre GL JS + free Terrarium DEM tiles) on top of OpenTopoMap.
- **Offline incident command workspace**: incidents, operational periods,
 primary/contingency scenarios, semantic tactical drawing, safety review, resources,
 immutable snapshots, revision conflicts, audit history and portable handovers all use
 the local SQLite database and require no upstream account.
- **Verified recovery and handover**: scheduled/manual SQLite online backups are
 integrity-checked before atomic publication and retained locally. Exported incident
 packages are previewed before import. Only new incidents import automatically, while
 existing-incident packages are refused with a conflict report rather than overwriting.
- **Offline AOI readiness manifests**: the current map view can be checked against the
 local tile cache for selected zooms. NexFiremap records every expected tile, attribution,
 size and SHA-256, then detects missing or modified files on repeat verification without
 bulk-downloading from providers that have not granted offline-pack permission.
- **Local recovery, change briefing and MBTiles**: a verified backup can be materialized
 only as a separate recovery database, immutable snapshots can be compared with each
 other or current command records, and administrator-supplied MBTiles can be validated
 and served as a WAN-independent basemap with retained source/licence metadata.
- **Previewed field observation intake**: GeoJSON, GPX, KML, KMZ and CSV are parsed locally,
 previewed against the current-view AOI, and applied only after required outside-AOI and
 confirmation acknowledgements. Original bytes, SHA-256 and the import report remain in
 the incident handover. Old observations stay visible with a configurable stale warning.
- **Structures at risk over time**: completed deterministic or ensemble spread runs can
 assess locally cached OpenStreetMap building footprints, highlight structures reached
 by a 0-48 hour map slider, and separately count residential buildings and critical
 facilities. Ensemble runs expose early/median/late arrival estimates. These are
 scenario exposure times, never labels of confirmed damage or evacuation status.
- **Everything runs locally**: FastAPI + SQLite + vendored Leaflet/MapLibre - no CDN
 calls. Only FIRMS needs a free account. Every other data source (Celestrak, Planetary
 Computer, Open-Meteo, OpenStreetMap Overpass, AWS terrain tiles, Nominatim place search)
 is free and keyless.
- **Place search**: a Google-Maps-style search box resolves place names through a
 server-proxied, cached, rate-limited call to Nominatim (never a direct browser call -
 the page's own CSP wouldn't allow it), or jumps straight to a typed `lat, lon` pair with
 no network call at all, so that shortcut still works with the WAN down.

## Quick start

**Easiest**: run the setup wizard for your platform. It creates a virtual environment,
installs everything, walks you through the FIRMS map key, and tests reachability of
every data source this project uses before it ever starts the server.

| Platform | Install (once) | Start |
|---|---|---|
| Windows | double-click `install.bat` | double-click `start.bat` |
| macOS | double-click `install.command` | double-click `start.command` |
| Linux | `./install.sh` | `./start.sh` |

(On macOS/Linux, if double-clicking doesn't work the first time, `chmod +x install.sh
install.command start.sh start.command` once, or just run `bash install.sh` directly -
the wizard sets the executable bit on the others for you afterwards.)

**Manual**, if you'd rather not run the wizard:

```powershell
python -m pip install -r requirements.txt
copy .env.example .env
notepad .env # paste your FIRMS_MAP_KEY
python run.py --open
```

Either way, open http://127.0.0.1:8000 once it's running (`--open` / the wizard does
this for you). The wizard is also directly runnable as `python scripts/setup_wizard.py`
and is safe to re-run any time (e.g. to add a key later, or re-check connectivity) -
it reuses the existing virtual environment and never overwrites a setting you don't
change.

### Get a FIRMS map key

FIRMS requires a free key to use its web services:
https://firms.modaps.eosdis.nasa.gov/api/map_key/

Without one the map still loads (basemaps and the UI work), but no fire data can be
downloaded - the app shows a banner explaining this until `FIRMS_MAP_KEY` is set. The
event-analysis features (Phases 1-4b below) need no additional account.

## Incident command workflow

The **Incident command** block at the top of the map panel is independent of FIRMS and
works on the command laptop's local server without public internet access.

1. Choose **new incident**. NexFiremap records the map centre and creates a 12-hour
 operational period with a primary **Plan A**.
2. Select an object and status, choose **draw on map**, then click once for a point or
 click the vertices of a line/area and press Enter or **finish**.
3. Field observations keep their time, source and observer. Planned objects are attached
 to the selected scenario, so primary and contingency geometry stay distinct.
4. Use **next period** rather than overwriting the preceding plan. Add alternatives with
 **new scenario**.
5. Complete the safety review before approval. Unresolved warnings require explicit
 acknowledgement by a named operator. This is not an automated safety certification.
6. Create immutable snapshots for handover/briefing and use **export handover** to save
 the versioned workspace, including audit history, as JSON/GeoJSON.
7. After running **spread** or **ensemble**, open **Structures at risk over time**. Use
 **cache / refresh buildings** while connectivity is available. Those footprints remain
 in SQLite for later offline runs. Move the time slider to highlight structures entering
 the modelled footprint by each horizon.
8. Expand **Operational record** before drawing - or select **edit** on an existing object - 
 to record its objective, responsible unit, assignments, timing, priority, method,
 equipment, water, prerequisites, hazards, escape/safety links, communications and
 notes. During a line/area sketch, use **undo vertex** or Ctrl/Cmd+Z.
9. Use **verified backup** before a handover or material planning change. **Import
 handover** always performs a dry run first and will not overwrite an existing incident.
10. Expand **Incident, period and scenario records** or **Resources** to update command
 records. Every save uses the revision originally loaded and rejects stale edits.
11. Before disconnecting, open **Offline AOI coverage**, choose a cached layer and zoom
 range, and check the current view. Only a result marked **READY FOR OFFLINE USE** is
 complete. Every per-zoom row must pass. Repeat verification detects files removed or
 modified after the manifest, and complete manifests pin ordinary cached files against
 automatic TTL/size pruning. Public providers are not bulk-prefetched: use an
 authorised imported MBTiles/GeoTIFF/raster GeoPackage for deliberate all-zoom coverage.
12. Under **Recovery and snapshot comparison**, materialize the latest backup as a
 separate database for download or compare an immutable snapshot with current records.
 Recovery never switches or overwrites the running database.
13. Use **Import local MBTiles basemap** for a map package supplied by an authorized GIS
 source. Record attribution, acquisition date, permitted use and limitations, then
 reload to select it in the normal basemap list.
14. Under **Import field observations**, select GeoJSON, GPX, KML, KMZ or CSV, optionally enter
 a JSON field mapping, and preview it against the current view. Applying a reviewed
 import preserves the original source. Use **Observed progression comparison** to show
 observations present at the start and those added during an interval.
15. Under **Tactical assessment**, review deterministic measurements and warnings. An
 acknowledgement requires a named operator and mitigation reason and stays in the
 audit/handover record. It does not declare a tactic safe.
16. Attach a completed spread/ensemble job under **Scenario model provenance**. The plan,
 snapshot and classified product retain model inputs, reference/validity times,
 source limitations and visible staleness warnings.
17. Generate immutable classified products as PDF, GeoJSON/JSON, CSV, GPX, KML/KMZ,
 GeoTIFF or raster GeoPackage. Only the public-information template can create public
 products, and public accounts can access only that filtered product catalogue.
18. For shared incident-LAN use, follow [`INCIDENT_LAN_RUNBOOK.md`](docs/INCIDENT_LAN_RUNBOOK.md):
 enable LAN mode explicitly, configure TLS, create least-privilege named accounts,
 complete a WAN-off drill, and retain backups on separate encrypted media.
19. **Live vehicle telemetry** shows source-token GPS feeds independently of manual
 position reports. Stale positions are gray and quality warnings red. Temporal tracks
 break at gaps or implausible jumps. These automatic positions remain unconfirmed.
20. The **Drone imagery desk** retains mission images and SHA-256 provenance. Images
 remain evidence-only unless an operator affirms `nadir` or `orthorectified` and
 supplies ordered TL/TR/BR/BL WGS84 corners. Selected georeferenced frames create a
 deterministic local visual mosaic, not survey-grade photogrammetry.
21. Record `wind observation` points with speed in m/s and meteorological **from**
 direction. **Temporal wind situation** renders downwind arrows for the selected time
 and current view from retained measurements and attached model provenance only. It
 does not make a network request or claim terrain-resolved wind.

Unfinished tactical sketches are stored in that browser after each vertex and material
field change. After an interrupted page/session, NexFiremap offers to restore the matching
incident sketch as explicitly unsaved. Successful save or deliberate cancel removes it.

LAN clients show whether the local command server is connected. The PWA shell and last
successful reads are cached, but writes are never fabricated or silently queued while
the command server is unavailable. The field-hardening roadmap is in
[`OPERATIONAL_IMPLEMENTATION_PLAN.md`](docs/OPERATIONAL_IMPLEMENTATION_PLAN.md).

## How the cache works

### Fire detections

The FIRMS *area* API only answers a few days at a time per request. NexFiremap turns
"give me 30 days for this viewport" into a set of chunked requests automatically:

1. The world is divided into a coverage grid (10° cells by default). For every
 `(source, cell, day)` combination the server remembers whether it's already been
 fetched.
2. When you pan/zoom or pick a time range, the server works out which `(source, cell,
 day)` combinations are still missing for your current view and queues just those - 
 already-cached days are served straight from SQLite, no network call.
3. Missing days are grouped into contiguous runs and split into the largest chunk FIRMS
 will accept per request (auto-detected. Steps down if the API rejects a range).
4. The **last 2 days** are treated as "hot" and re-checked periodically (default: every
 45 minutes) because satellites keep adding detections to them. Everything older is
 effectively final and is never re-fetched once cached.
5. A background job purges anything older than the retention window (default 30 days)
 every hour, so the database doesn't grow without bound.
6. Zooming out far enough that a viewport would need lots of grid cells switches to a
 single whole-world request instead of one per cell.

This means: the first time you look at a region it may take a few seconds to fetch.
after that, revisiting it is instant, and only the last couple of days keep costing
API calls.

### Basemap tiles

Every tile request from Leaflet goes through `/tiles/{basemap}/{z}/{x}/{y}.png`, which:

- serves the tile straight from disk if it's already cached and within the tile TTL
 (default 30 days - map imagery is mostly static, but not forever),
- otherwise fetches it from the upstream tile provider once, saves it, and serves it,
- coalesces simultaneous requests for the same tile into a single upstream fetch,
- caps how many upstream tile requests run at once (default 8) so a fast pan doesn't
 burst a tile provider,
- evicts the oldest-fetched tiles once the on-disk cache exceeds a size budget (default
 1 GB), so it stays "cached to an extent" rather than growing forever.

Set `NEXFIREMAP_CONTACT` in `.env` to add contact info to the tile User-Agent, per
[OSM's tile usage policy](https://operations.osmfoundation.org/policies/tiles/) - useful
if you'll be using this a lot.

## Background jobs

Anything expensive - event clustering, likelihood rasters, satellite imagery, physics
propagation - runs through a shared job queue (`nexfiremap/jobs.py`), not inline on a
request:

- Work runs in separate OS **processes** (`ProcessPoolExecutor`), not threads, so
 numpy/scipy-heavy code isn't limited by the GIL.
- Each worker process is dropped to **idle OS priority** (`psutil`) right after it
 starts, so this only spends CPU cycles nothing else on the machine wants.
- Jobs are rows in a SQLite table with live progress, so status survives a restart:
 `POST /api/jobs {kind, params}` submits, `GET /api/jobs/{id}` polls, `GET
 /api/jobs/{id}/files/{name}` serves whatever a job wrote to disk (rasters, GeoJSON).

**Graceful degradation**: the analysis modules below (orbits/events/likelihood/imagery/
terrain) each pull in extra optional dependencies - if one fails to install (rasterio has
historically had the most platform-specific install friction of the bunch), the server
still starts and the core fire map still works. That one feature just reports itself
unavailable via `/api/config`'s `features` field, and its buttons disable themselves in
the UI with an explanation, instead of the whole app crashing. The setup wizard's
package-by-package fallback and connectivity tests exist to catch this before you ever
open the map.

## Fire event analysis (Observed → Estimated → Modelled → Uncertainty)

Click **find in view** in the *Fire events* panel to cluster cached detections into
discrete fires (a space-time graph, not a bounding box - see `nexfiremap/events.py`).
Pick one to **analyze**, which unlocks progressively heavier layers:

| Layer | What it is | Module |
|---|---|---|
| **Satellite coverage** | How recently a tracked satellite's orbit passed over each area - a real orbit propagation (Celestrak TLEs + `skyfield`), not cloud-masked | `orbits.py` |
| **Likelihood** | Active-heat probability raster: a Gaussian kernel per detection, weighted by sensor/confidence/FRP, exponentially decayed with time, then discounted wherever a valid satellite pass confirmed no fire (tri-state model) | `likelihood.py` |
| **Arrival time** | Inverse-distance-weighted estimate of when fire likely reached each cell, with an honest (not fabricated) earliest/median/latest spread | `likelihood.py` |
| **Uncertainty envelopes** | Real 50/80/90% probability-*mass* contours (the region holding that fraction of total likelihood), filled as concentric bands with an area (km²/mi²) callout - not an arbitrary threshold or a bare outline | `likelihood.py` |
| **Burn scar** | Sentinel-2/Landsat NBR/dNBR severity, pre-fire vs post-fire, via free Planetary Computer imagery - optical ground truth, not thermal inference | `imagery.py` |
| **Modelled spread** | A real Rothermel (1972, Albini 1976) surface-fire kernel - Anderson-13 fuel physics, real dead-fuel-moisture conditioning (`moisture.py`) - solved **anisotropically** via a graph-based fast march (fire spreads faster downwind/upslope, slower into the wind). Isochrones render soonest→furthest as bright→faint, not one uniform dashed line | `rothermel.py`, `moisture.py`, `terrain.py` |
| **Ensemble** | ~40-member Monte Carlo ensemble sampling wind speed/direction, fuel-moisture and spread-rate uncertainty, each member scored against the event's own detections as they arrived (sequential assimilation) | `terrain.py` |
| **Validate model** | Rolling-holdout backtest of the likelihood model against real held-out detections and classical geometric baselines - see "Validation programme" below | `validation.py` |
| **3D terrain** | The layer currently on screen, draped over real elevation via MapLibre GL JS | frontend only |

All of this is free data: Celestrak (orbits), Microsoft Planetary Computer (Sentinel-2,
Landsat, Copernicus DEM, ESA WorldCover - open STAC search, anonymous signed access, no
account), and Open-Meteo (weather, no key). Every fetch is scoped to the event's small
AOI via windowed/reprojected reads against the cloud-hosted imagery - never a whole-scene
download.

**Documented simplifications** (so results are read for what they are):
- Fuel behaviour uses one Anderson-13 fuel model per ESA WorldCover class via a best-fit
 crosswalk (`terrain.py`'s `WORLDCOVER_TO_FUEL_MODEL`), not a locally-calibrated or
 Scott-Burgan-40 fuel map - WorldCover's 11 global land-cover classes can't distinguish
 e.g. chaparral from open pine forest, so the crosswalk is itself an approximation, just
 a standards-based one instead of an invented one.
- Wind is one Open-Meteo point sample for the whole event AOI, and dead-fuel moisture is
 one value per size class for the whole AOI (see `moisture.py`) - no WindNinja-grade
 terrain-channelled wind downscaling and no spatially-varying moisture, though slope and
 aspect *do* locally deflect the fire's own spread direction (Rothermel's slope term).
- No crown-fire behaviour, spotting, or the full Nelson dead-fuel-moisture PDE - see
 `rothermel.py`'s and `moisture.py`'s module docstrings for exactly what's approximated
 and why (the full FlamMap-compatible stack these come from is documented in this repo's
 own `docs/firemodel.md`).
- The ensemble's "sequential assimilation" is a batch weighted-calibration (each member
 solves once, scored against every later detection) rather than a fully recursive
 particle filter that re-propagates from a resampled state at each observation - the
 core Bayesian reweighting idea, without the added complexity of re-solving per step.
- Burn-scar dNBR can be a cross-sensor difference: pre/post scenes are picked
 independently (each falls back Sentinel-2 → Landsat on its own), so it's possible for
 one to be Sentinel-2 and the other Landsat. No bandpass harmonisation is applied
 between their SWIR channels (the kind HLS applies), so a cross-sensor result can carry
 a systematic severity bias - flagged via the job's own `cross_sensor` result field,
 not silently absorbed into the number.

## Validation programme

"Do not proceed to the visually polished model unless it reliably beats these
baselines" - docs/further_plan.md §11. Click **validate model** on an analyzed event to run a
rolling-holdout backtest (`nexfiremap/validation.py`): reveal an event's earliest
detections, hide the rest, ask several candidate models to predict the next observation,
score them against what was actually seen (and, via the tri-state model, confirmed clear
satellite passes as negative evidence).

Every candidate is scored identically:

| Model | What it is |
|---|---|
| `kernel_density` | The model this app actually serves (`likelihood.py`'s active-heat raster) |
| `buffered_footprint` | Static union of each detection's own sensor footprint - the simplest possible baseline |
| `concave_hull` | An alpha-shape around the training points (Delaunay triangulation, classical geometry) |
| `constant_radial` | Centroid + a growth rate fit by ordinary least squares on the training points |
| `wind_ellipse` | The same fitted rate, applied anisotropically downwind (best-effort - needs one Open-Meteo sample) |

Reported metrics: precision/recall/F1, a point-based Jaccard proxy (this project has no
curated reference perimeters like EFFIS to compute true-perimeter IoU against - flagged,
not faked), Brier score, centroid displacement, predicted area, missed disconnected
fronts, and (for `kernel_density` only, since it's the one candidate with a native time
estimate) arrival-time error.

## Persistent thermal-source classifier

Not every hot pixel is a wildfire. `nexfiremap/industrial.py` implements
docs/further_plan.md §13's rule: **"OSM creates a candidate. Recurrence confirms it.
stationarity strengthens it. Propagation or burn evidence overrides it."**

Click **scan this view** in the *Industrial/static sources* panel to:

1. Query OpenStreetMap's free Overpass API for known flares/kilns/refineries/power
 plants/works in view (cached locally so repeat scans don't re-query).
2. Score each candidate against *this server's own cached detection history* - how many
 distinct days had a detection nearby, how tightly clustered they are, how consistent
 the FRP signature is - via a transparent, fixed-weight linear score (not a trained
 classifier. The doc's own first recommended step).
3. Flag nearby detections as `persistent_industrial` / `ambiguous` - high-confidence
 suppression only, per the doc's precision-first guidance (a false industrial call
 hides a real fire).
4. When run from an event, apply a **wildfire-overrides-industrial** check: if detections
 near a matched source clearly grew beyond its own footprint over the event's timeline,
 those matches are downgraded to `possible_industrial_incident` instead.

Nothing is ever deleted or hidden by default - classified sources render as their own
toggle-able map layer, distinct from (not instead of) the raw detections.

**Honest limitation**: the doc recommends multi-year recurrence maps. This project only
ever caches `NEXFIREMAP_CACHE_DAYS` (default 30) of history by design, so "persistence"
here reflects this server's own recent window, not years of behaviour - real but weaker
evidence than the doc's ideal.

## 3D terrain view

Click **view in 3D terrain** on an analyzed event to drape whatever layer is currently on
screen - likelihood, burn severity, modelled spread, ensemble - over real elevation.
Leaflet has no 3D support, so this opens a second map engine
([MapLibre GL JS](https://maplibre.org/), vendored, BSD-licensed) as a full-panel overlay:
OpenTopoMap for the surface texture, free [Terrarium-encoded](https://github.com/tilezen/joerd)
elevation tiles (AWS Open Data) for the terrain mesh, both proxied/cached through the
same `/tiles/...` machinery as every other basemap.

## Configuration

All settings are environment variables (or put them in `.env`). See
[.env.example](.env.example) for the full list and defaults. The essentials:

| Variable | Default | Meaning |
|---|---|---|
| `FIRMS_MAP_KEY` | *(required)* | Your free FIRMS map key |
| `NEXFIREMAP_CACHE_DAYS` | `30` | Days of fire-detection history to retain |
| `NEXFIREMAP_SOURCES` | VIIRS ×3 + MODIS | Which satellite products to pull |
| `NEXFIREMAP_PORT` | `8000` | Local server port |
| `NEXFIREMAP_TILE_CACHE_DAYS` | `30` | How long a basemap tile is trusted before re-fetch |
| `NEXFIREMAP_TILE_CACHE_MAX_MB` | `1024` | Tile cache size budget |
| `NEXFIREMAP_JOB_WORKERS` | auto (cpu-1) | Background job worker processes |
| `NEXFIREMAP_BACKUP_DIR` | `data/backups` | Verified operational database backups |
| `NEXFIREMAP_BACKUP_INTERVAL_MINUTES` | `15` | Scheduled backup interval (`0` disables schedule) |
| `NEXFIREMAP_BACKUP_KEEP` | `24` | Number of verified backups retained |
| `NEXFIREMAP_POSITION_STALE_SECONDS` | `300` | Age at which an automatic vehicle position is labelled stale |
| `NEXFIREMAP_POSITION_MAX_SPEED_KMH` | `180` | Segment speed above which an automatic track is split/flagged |
| `NEXFIREMAP_DRONE_DIR` | `data/drone` | Preserved drone originals and previews |
| `NEXFIREMAP_DRONE_MAX_UPLOAD_MB` | `200` | Per-image raw upload bound |

## API

The frontend is a thin client over a small JSON API, useful on its own:

**Fire data & cache**
- `GET /api/config` - sources, basemaps, cache settings, and `features` (which optional
 analysis modules actually loaded - see "Graceful degradation" above)
- `GET /api/detections?bbox=W,S,E,N&days=7&sources=...&confidence=...&min_frp=...` - 
 cached detections as compact rows (or `&fmt=geojson`). `&autofetch=true` also queues
 any missing data for that viewport
- `GET /api/summary?bbox=...&days=30` - daily detection counts, for the histogram
- `GET /api/status` - cache size, fetch queue, tile cache stats, job queue, FIRMS budget
- `POST /api/cache/ensure {bbox, days, sources}` - explicitly warm the cache for a region
- `POST /api/cache/refresh` - re-check the hot days for everywhere already cached
- `POST /api/cache/purge` / `POST /api/tiles/purge` - force retention cleanup now
- `GET /api/geocode?q=...` - place-name search, server-proxied through Nominatim (see
 `nexfiremap/geocode.py`), cached and rate-limited to Nominatim's own usage policy

**Satellite coverage**
- `GET /api/coverage?bbox=...&day=YYYY-MM-DD&autofetch=true` - swath coverage cells for a
 viewport/day, with hours-since-last-look per cell

**Fire events & analysis** (all the `POST` routes below return `{job_id}` - poll it via
the job routes)
- `POST /api/events/detect {bbox, days, ...}` - cluster cached detections into events
- `GET /api/events?bbox=...` / `GET /api/events/{id}` - list / inspect events
- `POST /api/events/{id}/analyze {tau_hours, resolution_m}` - likelihood + arrival-time +
 uncertainty envelopes
- `POST /api/events/{id}/burn-scar {resolution_m}` - Sentinel-2/Landsat dNBR severity
- `POST /api/events/{id}/propagate {resolution_m, reference_ts}` - deterministic
 Rothermel + anisotropic fast-marching spread model
- `POST /api/events/{id}/ensemble {n_members, reference_ts}` - Monte Carlo ensemble +
 sequential assimilation
- `POST /api/events/{id}/validate {n_splits, threshold}` - rolling-holdout backtest
 against real held-out detections and classical baselines

**Industrial/static thermal sources**
- `POST /api/industrial/scan {bbox, days, window_days, event_id?}` - fetch/refresh OSM
 candidates in bbox, score them, match nearby detections (applies the
 wildfire-overrides-industrial check if `event_id` is given)
- `GET /api/industrial/sources?bbox=...` - cached candidates + their latest score, a
 plain synchronous read (no job needed)

**Background jobs**
- `POST /api/jobs {kind, params}` - submit any registered job kind directly
- `GET /api/jobs/{id}` / `GET /api/jobs?status=&kind=` - poll status / list
- `GET /api/jobs/{id}/files/{name}` - fetch a job's output file (raster PNG, GeoJSON)

**Offline incident operations**
- `GET/POST /api/operations/incidents` and `PATCH .../{id}` - list, create or
 revision-update/close local incidents
- `GET /api/operations/incidents/{id}` - return the complete incident workspace
- Period and scenario `POST/PATCH` routes - create and revision-update operational
 periods and primary, contingency, alternative or worst-case plans
- `GET/POST .../{id}/features` and `PATCH/DELETE .../features/{feature}` - 
 revision-controlled tactical and observation features
- `GET/PUT .../{period}/safety` and `POST .../{scenario}/approve` - safety review and
 named plan approval
- `GET/POST/PATCH .../resources` and `GET/POST .../snapshots` - revision-controlled
 logistics and immutable states
- `GET /api/operations/incidents/{id}/export` - versioned JSON/GeoJSON handover package
- `POST /api/operations/import/preview` / `POST /api/operations/import/apply` - validate
 packages and atomically import new incidents. Existing incidents return conflicts
- `GET/POST /api/operations/backups`, `POST .../{name}/verify`, and
 `GET .../{name}/download` - scheduled/manual verified SQLite recovery copies
- `GET/POST /api/operations/map-packs`, `GET .../{manifest}`, and
 `POST .../{manifest}/verify` - deterministic cached-AOI completeness and hash checks
- `GET/POST /api/operations/recoveries` and `GET .../{name}/download` - create and
 download separate verified recovery databases without touching the live database
- `GET .../snapshots/{left}/compare?right_snapshot_id=...` - deterministic semantic
 change report between snapshots or against current command records
- `GET/POST /api/operations/offline-sources` and `GET /offline-tiles/...` - validate,
 inventory and serve locally uploaded MBTiles basemaps
- Field-import `preview`/`apply`, provenance-list/original-download, and `progression`
 routes under `/api/operations/incidents/{id}` - reviewed observation intake and replay

**Temporal structure exposure**
- `POST /api/structures/scan {bbox}` - cache OSM building footprints for a bounded AOI
- `GET /api/structures/exposure?job_id=...&autofetch=false` - assess cached structures
 against a completed spread/ensemble arrival surface. `autofetch=true` also queues a
 cache refresh while returning the immediately available local result

## Project layout

```
nexfiremap/
 api.py FastAPI app and routes
 cache.py Coverage-grid planner + fetch queue + retention (fire data)
 firms.py FIRMS area API client (CSV parsing, error handling)
 tiles.py On-disk basemap tile cache/proxy
 jobs.py Background job queue (separate idle-priority processes)
 orbits.py Satellite swath coverage (Celestrak TLEs + skyfield)
 events.py Space-time event clustering
 likelihood.py Active-heat raster, arrival-time IDW, uncertainty envelopes
 imagery.py Sentinel-2/Landsat STAC access, NBR/dNBR burn severity
 rothermel.py Rothermel (1972, Albini 1976) surface-fire kernel + Anderson-13 fuel table
 moisture.py Dead-fuel moisture conditioning (equilibrium + NFDRS time-lag)
 terrain.py DEM/fuel/weather orchestration, anisotropic graph fast-marching, ensemble
 validation.py Rolling-holdout backtests vs. classical baselines
 industrial.py Persistent thermal-source classifier (OSM + cached history)
 eumetsat.py EUMETSAT MTG/FCI active-fire corroboration (optional, needs an account)
 operations.py Offline incidents, plans, tactical features, safety, audit and handover
 backups.py Scheduled/manual atomic SQLite backups with integrity verification
 map_packs.py Offline AOI tile manifests, completeness and SHA-256 verification
 offline_sources.py Validated local MBTiles ingestion, metadata and XYZ serving
 field_import.py Previewed GeoJSON/GPX/KML/KMZ/CSV observations with source provenance
 tactics.py Tactical measurements, warning acknowledgement and calculators
 products.py Classified deterministic PDF/vector/raster portable products
 merge.py Staged side-by-side disconnected package resolution
 provenance.py Model source, freshness, validity and limitation records
 security.py Local incident-LAN accounts, sessions, CSRF and role policy
 structures.py Cached building footprints and temporal scenario-exposure assessment
 rasterpng.py Minimal stdlib-only PNG encoder (no Pillow dependency)
 db.py SQLite schema and queries
 config.py Settings from environment / .env
 basemaps.py Selectable basemap + overlay definitions + the terrain DEM source
 static/ Frontend (Leaflet + MapLibre GL JS, vanilla JS, CSS)
 img/ Logo (also the favicon)
 vendor/ Vendored Leaflet, plugins, and MapLibre GL JS - no CDN calls
scripts/
 vendor_assets.py Re-download the vendored Leaflet/plugin files
 setup_wizard.py Cross-platform install wizard (venv, deps, .env, connectivity tests)
install.bat / install.sh / install.command Platform launchers for the wizard above
start.bat / start.sh / start.command Platform launchers for the server itself
tests/
 test_core.py Parsing, chunking, coverage-grid, SQLite unit tests
 test_fetch.py Full fetch pipeline against a stubbed FIRMS server
 test_tiles.py Tile cache against a stubbed tile server
 test_jobs.py Background job queue (cross-process execution, priority)
 test_orbits.py Swath coverage propagation, coverage-grid marking, clear-pass fusion
 test_events.py Space-time clustering
 test_likelihood.py Likelihood raster, arrival-time IDW, envelopes, tri-state suppression
 test_imagery.py NBR/dNBR math, severity classification (offline)
 test_rothermel.py Rothermel kernel regression table (docs/firemodel.md sec.54), directional ellipse
 test_moisture.py Equilibrium moisture, time-lag conditioning, rain response
 test_terrain.py Slope/aspect, fuel crosswalk, anisotropic graph fast-marching, ensemble sampling
 test_validation.py Rolling-holdout splits, every baseline model, scoring metrics
 test_industrial.py Overpass parsing, persistence/scoring, wildfire-override logic
```

`test_imagery.py`/parts of `test_terrain.py` cover the math offline. The STAC/DEM/
weather-fetching code paths (imagery.py, terrain.py) were verified live against real
Sentinel-2/Copernicus DEM/ESA WorldCover/Open-Meteo data rather than mocked, since
`pystac_client`/`rasterio` use their own HTTP transport (not `httpx`, unlike the rest of
the project, so the `MockTransport` pattern below doesn't reach them).

Run the tests (no network, no API key needed, except where noted above):

```powershell
python tests\test_core.py
python tests\test_fetch.py
python tests\test_tiles.py
python tests\test_jobs.py
python tests\test_orbits.py
python tests\test_events.py
python tests\test_likelihood.py
python tests\test_imagery.py
python tests\test_terrain.py
python tests\test_validation.py
python tests\test_industrial.py
```

## Notes

- Data sources: [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) (NRT products are
 provisional and may be revised), [Celestrak](https://celestrak.org) (orbital elements),
 [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com) (Sentinel-2,
 Landsat, Copernicus DEM, ESA WorldCover), [Open-Meteo](https://open-meteo.com) (weather),
 [OpenStreetMap Overpass API](https://overpass-api.de/) (industrial-source candidates),
 [AWS Open Data terrain tiles](https://registry.opendata.aws/terrain-tiles/) (3D
 elevation), [EUMETSAT Data Store](https://data.eumetsat.int) (independent geostationary
 active-fire corroboration, optional - see below), [Nominatim](https://nominatim.org)
 (place-name search, server-proxied per its usage policy - see `nexfiremap/geocode.py`).
- Basemap tiles: © OpenStreetMap contributors and the respective tile providers listed
 in the layer switcher. Attribution is preserved in the map's attribution control.
- The event-analysis layers are research-grade estimates for a hobby/local project, not
 an authoritative fire-behaviour product - they say so in their own status text.

### EUMETSAT corroboration (optional, needs a free account)

docs/further_plan.md §13 suggests EUMETSAT LSA SAF FRP as a European corroboration source.
Its classic MSG/SEVIRI FRP-PIXEL product needs its own separate LSA SAF registration on
top of a EUMETSAT account - real added friction every other source in this project avoids,
which originally kept it out. Once a EUMETSAT account exists, though, the **EUMETSAT Data
Store** itself (the same account, no extra registration) serves an actively-produced
successor: "Active Fire Monitoring (netCDF) - MTG - 0 degree" from the newer MTG-I1/FCI
instrument - same corroboration role, reachable with credentials this project already
documents. Live-confirmed: ~10-minute revisit, full-disk coverage from -67.5° to +67.5°
longitude/latitude (Europe, Africa, the Atlantic, the Middle East), a fire-confidence
classification + probability per pixel (not a radiative-power/MW value like FIRMS - 
see `nexfiremap/eumetsat.py`'s module docstring for exactly what this source is and isn't).

To use it:

1. Register a free account and generate a consumer key/secret at
 [api.eumetsat.int/api-key](https://api.eumetsat.int/api-key/).
2. Add `EUMETSAT_CONSUMER_KEY` / `EUMETSAT_CONSUMER_SECRET` to `.env` (see
 `.env.example`) - only the key/secret are stored. The server exchanges them for a
 short-lived (~37 minutes, live-confirmed) access token itself on each use, rather than
 storing a token that would just go stale.
3. Restart the server. An "EUMETSAT confirmation" layer toggle appears in the panel
 (hidden entirely if no account is configured) - small blue markers, distinct from
 FIRMS's orange/red detections since this is a second, independently-sourced instrument.

**Still not built, and why**: Copernicus Sentinel-3 SLSTR FRP needs its own Copernicus
Data Space Ecosystem account + OAuth2 client - a separate registration from EUMETSAT's,
so it stays a documented future option rather than a second account this project asks
for. EFFIS's burned-area WFS API (`ies-ows.jrc.ec.europa.eu`) is confirmed free and
keyless and would be genuinely valuable (it's the "curated reference perimeter" source
the validation programme's own Jaccard-proxy caveat points at), but was hitting a live
backend outage on JRC's side (`OracleSpatial ... Connection failure`) at the time this
was investigated - worth retrying, not built blind against an unverified schema.

## License

[MIT](LICENSE) - the copyright holder in that file is a placeholder
("NexFiremap contributors"). Swap it for your own name before publishing if you'd
rather the license say that.

This code is free to use, modify, and redistribute, including commercially. The
vendored libraries in `nexfiremap/static/vendor/` keep their own upstream licenses -
all permissive and MIT-compatible: Leaflet (BSD-2-Clause), Leaflet.markercluster (MIT),
Leaflet.heat/simpleheat (BSD-2-Clause), MapLibre GL JS (BSD-3-Clause), proj4js (MIT),
mgrs (MIT). None of that requires anything from you beyond what's already there (their
own license headers stay in the vendored files).

**Code license is a separate question from data terms**, though, and those still apply
if you publish:
- **OpenStreetMap** data is [ODbL](https://opendatacommons.org/licenses/odbl/) - the
 attribution already shown in the map's corner covers displaying it. Redistributing the
 *cached tiles/data themselves* as a dataset (not just running this server) would need
 to satisfy ODbL's share-alike terms separately.
- **NASA FIRMS** data has its own [use policy](https://www.earthdata.nasa.gov/data/tools/firms/faq) (credit NASA/FIRMS, don't imply endorsement) - publishing the *code* that fetches it
 doesn't touch this. It only matters if you redistribute *cached fire data* itself.
- Copernicus/ESA (Sentinel-2, Landsat, Copernicus DEM, ESA WorldCover, via Planetary
 Computer) and Open-Meteo are openly licensed for reuse with attribution - already
 credited in this README and the app's own source-attribution text.

None of this affects the *code's* license - MIT on the code is fine regardless. It
matters if you start redistributing the *data* this project caches, not just the
software that fetches and displays it.

**Before you publish**: keep operational credentials only in the gitignored `.env` file
and run a repository secret scan. Design notes, exported incident packages, source files,
map packs and backups can all contain sensitive operational information and need their own
release review. The public-information product filter is the supported public export path.
