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
