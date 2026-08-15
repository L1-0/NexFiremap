# CLAUDE.md

Working notes for an agent picking up NexFiremap cold. For feature-level detail,
architecture and the full endpoint/UI walkthrough, read `README.md` first; this file
only covers running the thing, testing it, and the constraints that keep it working.

## What this is

A local-first FastAPI + SQLite server that overlays NASA FIRMS active-fire detections
on OpenStreetMap-based basemaps, plus an incident-command/tactical layer and a
physics-based (not ML) fire-spread model. Single Python process, vanilla-JS frontend,
no build tooling.

## Running the dev server

```powershell
python -m pip install -r requirements.txt
copy .env.example .env
notepad .env   # paste FIRMS_MAP_KEY (optional - map works without it, just no fire data)
python run.py --open
```

Serves on `http://127.0.0.1:8000`. `run.py` is a thin wrapper around
`nexfiremap.__main__:main`. There's also a cross-platform interactive installer at
`scripts/setup_wizard.py` (also reachable via `install.bat`/`install.sh`/`install.command`)
that creates a venv, installs deps, writes `.env`, and checks reachability of every
external data source before first run - safe to re-run any time.

## Running the tests

There is no pytest runner, no `conftest.py`, and no `pytest.ini`/`tox.ini` in this repo.
Each file under `tests/` is a standalone script with its own `main()` and
`if __name__ == "__main__":` entry point that prints failures and returns a process exit
code (0 = pass). Run one directly:

```powershell
python tests\test_core.py
```

Or run everything and stop at the first failure:

```powershell
for f in tests/test_*.py; do python "$f" || break; done
```

(that loop is bash syntax - in PowerShell: `foreach ($f in Get-ChildItem tests\test_*.py) { python $f.FullName; if ($LASTEXITCODE -ne 0) { break } }`)

Most tests need no network and no FIRMS key (they stub HTTP via `httpx`'s
`MockTransport`). Exceptions, per README: `test_imagery.py`/parts of `test_terrain.py`
exercise math offline, but the STAC/DEM/weather-fetching code paths in `imagery.py` and
`terrain.py` were also verified live against real Sentinel-2/Copernicus DEM/ESA
WorldCover/Open-Meteo data, since `pystac_client`/`rasterio` use their own HTTP
transport rather than `httpx` and so bypass the mock.

## Core constraints (found stated in the code itself - don't invent more)

- **No CDN calls.** README (`## Everything runs locally`): *"FastAPI + SQLite + vendored
  Leaflet/MapLibre - no CDN calls. Only FIRMS needs a free account."* All map libraries
  (Leaflet, its plugins, MapLibre GL JS, proj4, mgrs) are vendored into
  `nexfiremap/static/vendor/` and re-downloaded only via `scripts/vendor_assets.py`,
  which pins each library to an exact version and verifies it against a known-good
  SHA-256 after download (module docstring: *"Vendoring keeps NexFiremap working
  without a CDN (and without internet at all, if you already have cached
  detections)."*).
- **No build step.** The frontend is vanilla JS/CSS served as-is from
  `nexfiremap/static/` - no bundler, no transpiler, no npm.
- **No ML.** The fire-behaviour and clustering code is classical statistics/geometry/
  physics, explicitly not machine learning - e.g. `events.py`: *"...using classical
  graph clustering - no ML."*; `terrain.py`: *"Physics-based fire spread modelling -
  ...(no ML)."* Rothermel (1972)/Albini (1976) surface-fire kernel in `rothermel.py`,
  Nelson (2000) dead-fuel-moisture conditioning in `moisture.py`.
- **Everything works offline-first.** SQLite cache, vendored assets, and offline
  tile/MBTiles support exist specifically so the tool keeps working with the WAN down
  during an incident. Don't introduce a code path that silently requires live internet.

## Raster row orientation (the one that bit us)

Two grid conventions coexist and both are staying, because each is natural in
its own module:

- **`likelihood.py` and anything built on `geo.grid_xy_m` is south-first** -
  row 0 is the bbox's south edge, because `grid_xy_m`'s y axis grows north from
  that corner and `detections_xy_m` measures against the same origin.
- **`terrain.py` is north-first** - `to_rowcol` indexes with `(north - lat)`,
  matching raster/GeoTIFF convention.

What is *not* optional is saying which one you mean. Both mirroring bugs that
shipped came from an implicit assumption: terrain's isochrones read a
north-first grid with the south-first formula, and likelihood's PNGs encoded a
south-first grid into a format whose first row is drawn at the **north** edge.
In each case the module's *other* output was correct, so nothing looked wrong.

So: every row→latitude conversion goes through `geo.row_to_lat(...,
origin=...)`, and every raster handed to a renderer goes through
`geo.to_north_first(..., origin=...)`. Both take `origin` as a **mandatory
keyword** - there is deliberately no default to inherit. `render_probability_png`
/ `render_recency_png` serve both modules, which is exactly why they cannot
assume.

`tests/test_orientation.py` is the guard. Property tests cannot catch this
class of bug: a mirrored isochrone is still monotone in time and a mirrored
raster still sums to the same mass. Those checks put a signal in one hemisphere
and assert it comes out on the same side.

## Standards interoperability (`nexfiremap/ingest/`)

External formats (CoT/TAK, CAP, NMEA, MAVLink, Shapefile, mapped CAD JSON) are
handled by **stateless adapters** in `nexfiremap/ingest/`. The rule the package
enforces, stated in its own docstring: an adapter only ever *translates* bytes
into one of a few contracts that existing managers already accept —

- position report → `TelemetryManager.ingest` (`telemetry.py`)
- overlay feature → `FieldImportManager.prepare` (`field_import.py`)
- alert → `AlertManager` (`alerts.py`)

No adapter touches the database, opens a socket, or reads a setting. That is
what guarantees a new standard cannot bypass the token auth, rate limiting,
replay-safety, preview-before-apply or provenance the receiving managers
enforce. **A new standard is a new parser, never a new write path.**

Stateful pieces are separate managers wired in `api.py`'s `lifespan()` next to
`cache`/`tiles`/`jobs`: `cot_gateway.py` (TCP/UDP listener), `alerts.py` (CAP
poller), `mqtt.py` (broker subscriber), `webhooks.py` (CAD registry),
`layers.py` (WMS/WMTS registry), `federation.py` (OIDC/LDAP).

Two things to keep in mind when editing here:

- `TelemetryManager` has **two** entry points. `ingest(source_id, token, reports)`
  checks a token; `ingest_authenticated(source_id, reports)` does not and exists
  for server-side callers that authenticated some other way (the webhook
  receiver, the MQTT bridge). Both funnel into `_accept`, so they cannot drift.
  Never expose `ingest_authenticated` to a route.
- An adapter's `MEDIA_TYPES` decides what `adapter_for_media_type` hands it on
  the shared feed endpoint. `ingest/webhook.py` deliberately declares **none** —
  it once claimed `application/json` and silently hijacked every native JSON
  batch. There is a regression test for exactly that.

## Analytical ↔ operational integration

The app has two halves - detections/events/models, and incidents/scenarios/
resources - and until recently the only thing joining them was an operator
retyping. `nexfiremap/operations/links.py` is that join, and it enforces one
rule that must not be relaxed:

**Cross-module data crosses as an immutable snapshot with provenance, never as
a live reference.** Events are re-clustered (their integer ids are not stable),
models are re-run, detections are purged on the retention window. A plan line
justified by "the 09:40 propagation run" has to keep meaning that after the
model is re-run at 11:00. So `incident_links.snapshot_json` is mandatory and
non-empty, and `add_link` refuses a link without one.

Pieces to know:

- `operations/links.py` - `LinkStore` (links + the incident AOI), plus
  `normalise_aoi`/`point_in_polygon`, hand-rolled so an AOI test needs no
  geometry dependency. `incidents_covering()` is the reverse lookup ("does this
  new detection concern anyone?").
- `situation.py` - `GET /api/situation` provider registry. A provider that
  raises, or whose optional dependency is missing, is **named in
  `unavailable`** rather than silently dropped: "we did not ask" and "there is
  nothing there" are different answers when someone is deciding whether to send
  a crew.
- `safety.py` - the loop that makes the physics protective. Runs inside
  `TelemetryManager._accept` **after** the transaction commits, and never
  raises: a failed evaluation must not cost the position that triggered it, or
  a unit vanishes off the map. Rasters are cached per job id because this is
  called once per position per vehicle.
- `routes/links.py` - links/AOI CRUD, `POST .../incidents/from_context`
  (incident + period + scenario + AOI + link in one transaction), `GET
  /api/situation`, `GET /api/operations/watch`.

Two ordering constraints that will bite if disturbed:

- **`links.router` must be included before `incidents.router`** in
  `routes/__init__.py`. It declares `/api/operations/incidents/covering` where
  incidents declares `/{incident_id}`; Starlette is first-match-wins, so the
  other order answers a confusing 404. That package's docstring used to say
  include order was purely cosmetic - it no longer is.
- **`HAZARD_FEATURE_TYPES` contains only AREA types.** `hazard` is a *point*
  and has no interior; warning on it would mean inventing a safety radius
  nobody entered. `structure_protection_area` is excluded too - it marks
  something worth defending, not somewhere dangerous to stand.

`CONTROL_LINE_BUILT` similarly excludes `proposed`/`planned`: feeding a planned
line into the propagation model as a barrier would produce a forecast that
flatters the plan.

## Project layout

See `README.md`'s "Project layout" section for the full per-module table
(`nexfiremap/*.py` responsibilities, `static/`, `scripts/`, `tests/`). Design/planning
documents live in `docs/` (see below) rather than the repo root.

## Docs directory

Working/design docs were moved out of the repo root into `docs/` to de-clutter it
(README.md stays at the root):

- `docs/further_plan.md` - the original design/planning notes; sections are cited
  throughout the codebase as `further_plan.md §N`.
- `docs/firemodel.md` - the FlamMap/FARSITE-compatible fire-behaviour model reference
  cited as `firemodel.md sec.N` in `rothermel.py`, `moisture.py`, and their tests.
- `docs/OPERATIONAL_IMPLEMENTATION_PLAN.md`, `docs/TELEMETRY_DRONE_PLAN.md`,
  `docs/TILE_CACHE_PLAN.md`, `docs/WIND_MAP_PLAN.md` - feature-specific implementation
  plans.
- `docs/END_TO_END_TODO.md` - the authoritative completion/release-gate checklist.
- `docs/INCIDENT_LAN_RUNBOOK.md` - deployment/runbook for shared incident-LAN use.
- `docs/realrun.md` - a captured real server run/session transcript.
- `docs/trace.svg` - a trace/diagram asset.

Existing citations to these files inside `.py`/`.js` source comments and docstrings
(e.g. `further_plan.md §13`, `firemodel.md sec.54`) still use the bare filename, not the
`docs/` path - that's expected; they're prose citations, not clickable links.

## Known concurrent-work note

Other files may be mid-edit by other agents in this repo at any given time
(`nexfiremap/api.py`, `nexfiremap/operations.py`, `nexfiremap/db.py`,
`nexfiremap/static/**` in particular). Check `git status` before assuming the working
tree is clean.
