"""SQLite storage for cached FIRMS detections and fetch coverage.

Two tables carry the cache:

``detections``  one row per fire detection, deduplicated on the natural key
                FIRMS gives us (source + satellite + position + timestamp).
``coverage``    bookkeeping: which (source, grid cell, day) combinations have
                already been pulled, and when. This is what lets the server
                fetch only the gaps instead of re-downloading everything.

Schema changes are versioned. `SCHEMA` (idempotent CREATE ... IF NOT EXISTS)
covers additive changes; anything that has to alter or rewrite an existing
table is an entry in the `MIGRATIONS` ladder, applied one version at a time
from whatever `PRAGMA user_version` the file reports up to `SCHEMA_VERSION`.
Opening a database older than the code migrates it forward (after taking a
pre-migration backup of the whole file); opening one *newer* than the code
refuses outright rather than writing a schema it doesn't understand. See the
"migrations" section below for the contract each step honours.
"""

from __future__ import annotations

import json
import math
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .geo import lon_range_sql

log = logging.getLogger("nexfiremap.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS detections (
    id               INTEGER PRIMARY KEY,
    source           TEXT    NOT NULL,
    satellite        TEXT,
    instrument       TEXT,
    latitude         REAL    NOT NULL,
    longitude        REAL    NOT NULL,
    acq_date         TEXT    NOT NULL,
    acq_time         TEXT    NOT NULL,
    acq_ts           INTEGER NOT NULL,
    brightness       REAL,
    brightness2      REAL,
    scan             REAL,
    track            REAL,
    confidence_raw   TEXT,
    confidence_pct   INTEGER,
    confidence_level TEXT,
    frp              REAL,
    daynight         TEXT,
    version          TEXT,
    raw_json         TEXT,
    UNIQUE (source, satellite, latitude, longitude, acq_date, acq_time)
);

CREATE INDEX IF NOT EXISTS idx_detections_ts     ON detections (acq_ts);
CREATE INDEX IF NOT EXISTS idx_detections_bbox   ON detections (latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_detections_source ON detections (source, acq_ts);
CREATE INDEX IF NOT EXISTS idx_detections_day    ON detections (acq_date);

CREATE TABLE IF NOT EXISTS coverage (
    source     TEXT    NOT NULL,
    cell_x     INTEGER NOT NULL,
    cell_y     INTEGER NOT NULL,
    day        TEXT    NOT NULL,
    fetched_at INTEGER NOT NULL,
    row_count  INTEGER NOT NULL DEFAULT 0,
    status     TEXT    NOT NULL DEFAULT 'ok',
    note       TEXT,
    PRIMARY KEY (source, cell_x, cell_y, day)
);

CREATE INDEX IF NOT EXISTS idx_coverage_day ON coverage (day);

-- Cached TLE (orbital elements) per satellite, refreshed weekly. Used to
-- propagate ground tracks for the swath-coverage layer (Phase 1) - a
-- geometric approximation of "was this area observed", no cloud/quality
-- masking.
CREATE TABLE IF NOT EXISTS tle (
    satellite  TEXT PRIMARY KEY,
    line1      TEXT NOT NULL,
    line2      TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);

-- Which coverage-grid cells (same grid as `coverage` above, reused so the
-- two layers line up) a satellite's swath passed over on a given UTC day.
-- Computed once per (satellite, day) - like FIRMS data, a day in the past
-- never changes, so this is a pure cache from then on.
CREATE TABLE IF NOT EXISTS swath_coverage (
    satellite  TEXT    NOT NULL,
    cell_x     INTEGER NOT NULL,
    cell_y     INTEGER NOT NULL,
    day        TEXT    NOT NULL,
    pass_count INTEGER NOT NULL DEFAULT 1,
    first_ts   INTEGER,
    last_ts    INTEGER,
    computed_at INTEGER NOT NULL,
    PRIMARY KEY (satellite, cell_x, cell_y, day)
);

CREATE INDEX IF NOT EXISTS idx_swath_day ON swath_coverage (day);

-- A "fire event" is a cluster of detections grouped by a space-time graph
-- (see events.py) - the unit Phase 2+ analysis (likelihood rasters, burn
-- scar anchoring, propagation modelling) operates on, rather than the whole
-- map. Written by a worker process via a raw connection, like the tables
-- above.
CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    bbox_west        REAL    NOT NULL,
    bbox_south       REAL    NOT NULL,
    bbox_east        REAL    NOT NULL,
    bbox_north       REAL    NOT NULL,
    centroid_lat     REAL    NOT NULL,
    centroid_lon     REAL    NOT NULL,
    first_seen       INTEGER NOT NULL,
    last_seen        INTEGER NOT NULL,
    detection_count  INTEGER NOT NULL,
    sources_json     TEXT    NOT NULL,
    params_json      TEXT    NOT NULL,
    created_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_time ON events (last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_events_bbox ON events (bbox_west, bbox_south, bbox_east, bbox_north);

-- Which detections belong to which event.
CREATE TABLE IF NOT EXISTS event_members (
    event_id     INTEGER NOT NULL,
    detection_id INTEGER NOT NULL,
    PRIMARY KEY (event_id, detection_id)
);

CREATE INDEX IF NOT EXISTS idx_event_members_detection ON event_members (detection_id);

-- Background compute jobs (event clustering, likelihood rasters, burn-scar
-- anchoring, propagation modelling, ...). Workers run in separate OS
-- processes and self-report progress by writing this table directly through
-- a short-lived connection (see jobs.py) rather than sharing this Database
-- object, which doesn't survive a process spawn.
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,
    params_json TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'queued',
    progress    INTEGER NOT NULL DEFAULT 0,
    note        TEXT,
    result_json TEXT,
    error       TEXT,
    created_at  INTEGER NOT NULL,
    started_at  INTEGER,
    finished_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_kind   ON jobs (kind, created_at);

-- Persistent thermal-source classification (further_plan.md section 13):
-- separating recurring industrial heat (flares, kilns, refineries, power
-- plants, ...) from genuine wildfires, without ever deleting or hiding the
-- underlying detections - see industrial.py's module docstring for the
-- full design and its honestly-documented limitations.

-- Candidate industrial sites discovered from OpenStreetMap. Cached
-- indefinitely once fetched (geometry/tags don't need re-fetching often);
-- `industrial_query_cache` below tracks *which areas* have been queried so
-- this project stays a considerate, cache-first client of the shared,
-- free, keyless Overpass API.
CREATE TABLE IF NOT EXISTS industrial_sources (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    osm_type       TEXT    NOT NULL,
    osm_id         INTEGER NOT NULL,
    latitude       REAL    NOT NULL,
    longitude      REAL    NOT NULL,
    evidence_class TEXT    NOT NULL,  -- 'strong' | 'moderate', per the doc's OSM tag tables
    tags_json      TEXT    NOT NULL,
    fetched_at     INTEGER NOT NULL,
    UNIQUE (osm_type, osm_id)
);

CREATE INDEX IF NOT EXISTS idx_industrial_sources_bbox ON industrial_sources (latitude, longitude);

-- Buildings/structures used by temporal exposure assessment. Geometry and
-- address/use tags are cached locally after an operator scans an area, so
-- subsequent spread assessments work without an internet connection.
CREATE TABLE IF NOT EXISTS structures (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    osm_type      TEXT NOT NULL,
    osm_id        INTEGER NOT NULL,
    latitude      REAL NOT NULL,
    longitude     REAL NOT NULL,
    geometry_json TEXT,
    tags_json     TEXT NOT NULL,
    fetched_at    INTEGER NOT NULL,
    UNIQUE (osm_type, osm_id)
);
CREATE INDEX IF NOT EXISTS idx_structures_bbox ON structures (latitude, longitude);

CREATE TABLE IF NOT EXISTS structure_query_cache (
    west       REAL NOT NULL,
    south      REAL NOT NULL,
    east       REAL NOT NULL,
    north      REAL NOT NULL,
    fetched_at INTEGER NOT NULL,
    row_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (west, south, east, north)
);

CREATE TABLE IF NOT EXISTS industrial_query_cache (
    west       REAL    NOT NULL,
    south      REAL    NOT NULL,
    east       REAL    NOT NULL,
    north      REAL    NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (west, south, east, north)
);

-- The classifier's current opinion of each candidate, recomputed whenever
-- a scan covers it - kept separate from the OSM-sourced geometry/tags
-- above since this reflects *this project's own cached detection history*,
-- not OpenStreetMap.
CREATE TABLE IF NOT EXISTS industrial_source_scores (
    source_id       INTEGER PRIMARY KEY,
    score           REAL    NOT NULL,
    classification  TEXT    NOT NULL,
    detection_count INTEGER NOT NULL,
    distinct_days   INTEGER NOT NULL,
    span_days       REAL,
    spread_km       REAL,
    match_radius_km REAL    NOT NULL,
    window_days     INTEGER NOT NULL,
    computed_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Independent geostationary active-fire corroboration, EUMETSAT MTG/FCI
-- "Active Fire Monitoring" (Data Store collection EO:EUM:DAT:0682) -
-- further_plan.md's suggested LSA SAF-style European corroboration source,
-- reached via the modern EUMETSAT Data Store API instead (see
-- nexfiremap/eumetsat.py's module docstring). Each ~10-minute full-disk
-- product is parsed *once* for every fire pixel it contains, globally, not
-- clipped to whatever bbox first triggered the scan - `eumetsat_products`
-- below is what makes a later scan of a *different* bbox an instant local
-- read instead of a redundant re-download, since the same product covers
-- Europe/Africa/the Atlantic in one shot regardless of which part of it a
-- given viewport asked about.
CREATE TABLE IF NOT EXISTS eumetsat_fires (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    latitude    REAL    NOT NULL,
    longitude   REAL    NOT NULL,
    acq_ts      INTEGER NOT NULL,
    confidence  TEXT    NOT NULL,  -- 'low' | 'medium' | 'high' (the product's own fire_result classes)
    probability REAL,              -- the product's own fire_probability, 0-1
    product_id  TEXT    NOT NULL,
    UNIQUE (product_id, latitude, longitude)
);

CREATE INDEX IF NOT EXISTS idx_eumetsat_fires_bbox ON eumetsat_fires (latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_eumetsat_fires_ts ON eumetsat_fires (acq_ts);

CREATE TABLE IF NOT EXISTS eumetsat_products (
    product_id  TEXT PRIMARY KEY,
    end_ts      INTEGER NOT NULL,  -- acquisition window end (UTC epoch seconds)
    fire_pixels INTEGER NOT NULL,
    fetched_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eumetsat_products_end_ts ON eumetsat_products (end_ts);

-- Offline incident-command workspace.  These tables deliberately live in
-- the same SQLite database as cached observations: a copied database is a
-- complete local handover, and no network service is required to preserve
-- operational decisions.  UUID text keys make later disconnected merges
-- deterministic; integer revisions provide optimistic concurrency control.
CREATE TABLE IF NOT EXISTS incidents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    incident_number TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    timezone        TEXT NOT NULL DEFAULT 'UTC',
    center_lat      REAL,
    center_lon      REAL,
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    revision        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS operational_periods (
    id          TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    starts_at   TEXT NOT NULL,
    ends_at     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    objectives  TEXT NOT NULL DEFAULT '',
    approved_by TEXT,
    approved_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    revision    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_periods_incident ON operational_periods (incident_id, starts_at DESC);

CREATE TABLE IF NOT EXISTS plan_scenarios (
    id          TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    period_id   TEXT NOT NULL REFERENCES operational_periods(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'primary',
    status      TEXT NOT NULL DEFAULT 'draft',
    description TEXT NOT NULL DEFAULT '',
    assumptions TEXT NOT NULL DEFAULT '',
    approved_by TEXT,
    approved_at TEXT,
    warning_acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    revision    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_scenarios_period ON plan_scenarios (period_id, kind, updated_at DESC);

CREATE TABLE IF NOT EXISTS incident_model_runs (
    id              TEXT PRIMARY KEY,
    incident_id     TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    scenario_id     TEXT NOT NULL REFERENCES plan_scenarios(id) ON DELETE CASCADE,
    job_id          INTEGER,
    model_kind      TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    attached_by     TEXT NOT NULL,
    attached_at     TEXT NOT NULL,
    UNIQUE (scenario_id, job_id)
);
CREATE INDEX IF NOT EXISTS idx_model_runs_scenario ON incident_model_runs (scenario_id, attached_at DESC);

CREATE TABLE IF NOT EXISTS tactical_warning_acknowledgements (
    id          TEXT PRIMARY KEY,
    warning_id  TEXT NOT NULL,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    period_id   TEXT NOT NULL REFERENCES operational_periods(id) ON DELETE CASCADE,
    scenario_id TEXT NOT NULL DEFAULT '',
    warning_code TEXT NOT NULL,
    reason      TEXT NOT NULL,
    acknowledged_by TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL,
    UNIQUE (incident_id, period_id, scenario_id, warning_id)
);

CREATE TABLE IF NOT EXISTS tactical_features (
    id          TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    period_id   TEXT REFERENCES operational_periods(id) ON DELETE SET NULL,
    scenario_id TEXT REFERENCES plan_scenarios(id) ON DELETE SET NULL,
    feature_type TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'observed',
    geometry_json TEXT NOT NULL,
    properties_json TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT,
    source      TEXT,
    observer    TEXT,
    confidence  TEXT,
    valid_from  TEXT,
    valid_to    TEXT,
    created_by  TEXT NOT NULL DEFAULT 'local operator',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    revision    INTEGER NOT NULL DEFAULT 1,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tactical_incident ON tactical_features (incident_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tactical_period ON tactical_features (period_id, scenario_id, feature_type);

CREATE TABLE IF NOT EXISTS incident_resources (
    id          TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    callsign    TEXT NOT NULL,
    unit_type   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'available',
    crew_size   INTEGER,
    water_capacity_l REAL,
    capabilities TEXT NOT NULL DEFAULT '',
    assignment  TEXT NOT NULL DEFAULT '',
    contact_channel TEXT NOT NULL DEFAULT '',
    latitude    REAL,
    longitude   REAL,
    position_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    revision    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_resources_incident ON incident_resources (incident_id, status, callsign);

CREATE TABLE IF NOT EXISTS position_feed_sources (
    id              TEXT PRIMARY KEY,
    incident_id     TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT '',
    device_kind     TEXT NOT NULL DEFAULT 'vehicle_gps',
    token_hash      TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    last_received_at TEXT,
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    revision        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_position_feeds_incident ON position_feed_sources (incident_id, active, name);

CREATE TABLE IF NOT EXISTS vehicle_position_reports (
    id              TEXT PRIMARY KEY,
    incident_id     TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    source_id       TEXT NOT NULL REFERENCES position_feed_sources(id) ON DELETE CASCADE,
    resource_id     TEXT REFERENCES incident_resources(id) ON DELETE SET NULL,
    external_id     TEXT NOT NULL,
    callsign        TEXT NOT NULL,
    observed_at     TEXT NOT NULL,
    observed_epoch  REAL NOT NULL,
    received_at     TEXT NOT NULL,
    latitude        REAL NOT NULL,
    longitude       REAL NOT NULL,
    altitude_m      REAL,
    speed_kmh       REAL,
    heading_deg     REAL,
    accuracy_m      REAL,
    quality_json    TEXT NOT NULL,
    raw_json        TEXT NOT NULL,
    payload_sha256  TEXT NOT NULL,
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_vehicle_positions_incident_time ON vehicle_position_reports (incident_id, observed_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_vehicle_positions_track ON vehicle_position_reports (source_id, callsign, observed_epoch);

CREATE TABLE IF NOT EXISTS drone_missions (
    id              TEXT PRIMARY KEY,
    incident_id     TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    aircraft        TEXT NOT NULL DEFAULT '',
    operator        TEXT NOT NULL DEFAULT '',
    started_at      TEXT,
    ended_at        TEXT,
    notes           TEXT NOT NULL DEFAULT '',
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    revision        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_drone_missions_incident ON drone_missions (incident_id, started_at DESC);

CREATE TABLE IF NOT EXISTS drone_assets (
    id              TEXT PRIMARY KEY,
    incident_id     TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    mission_id      TEXT NOT NULL REFERENCES drone_missions(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    media_type      TEXT NOT NULL,
    classification  TEXT NOT NULL DEFAULT 'operational',
    sha256          TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    captured_at     TEXT,
    width           INTEGER NOT NULL,
    height          INTEGER NOT NULL,
    original_path   TEXT NOT NULL,
    thumbnail_path  TEXT NOT NULL,
    corners_json    TEXT,
    footprint_json  TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    georef_status   TEXT NOT NULL DEFAULT 'unreferenced',
    offline_source_id TEXT,
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drone_assets_mission ON drone_assets (mission_id, captured_at, id);

CREATE TABLE IF NOT EXISTS drone_mosaics (
    id              TEXT PRIMARY KEY,
    incident_id     TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    mission_id      TEXT NOT NULL REFERENCES drone_missions(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    asset_ids_json  TEXT NOT NULL,
    offline_source_id TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    metadata_json   TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drone_mosaics_mission ON drone_mosaics (mission_id, created_at DESC);

CREATE TABLE IF NOT EXISTS safety_checks (
    period_id   TEXT NOT NULL REFERENCES operational_periods(id) ON DELETE CASCADE,
    scenario_id TEXT NOT NULL DEFAULT '',
    check_key   TEXT NOT NULL,
    checked     INTEGER NOT NULL DEFAULT 0,
    details     TEXT NOT NULL DEFAULT '',
    updated_by  TEXT NOT NULL DEFAULT 'local operator',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (period_id, scenario_id, check_key)
);

CREATE TABLE IF NOT EXISTS incident_snapshots (
    id          TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    period_id   TEXT REFERENCES operational_periods(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'operational',
    created_by  TEXT NOT NULL DEFAULT 'local operator',
    created_at  TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_incident ON incident_snapshots (incident_id, created_at DESC);

CREATE TABLE IF NOT EXISTS incident_source_imports (
    id            TEXT PRIMARY KEY,
    incident_id   TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    format        TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    source        TEXT NOT NULL DEFAULT '',
    imported_by   TEXT NOT NULL DEFAULT 'local operator',
    imported_at   TEXT NOT NULL,
    feature_count INTEGER NOT NULL,
    report_json   TEXT NOT NULL,
    original_blob BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_imports_incident ON incident_source_imports (incident_id, imported_at DESC);

CREATE TABLE IF NOT EXISTS incident_products (
    id             TEXT PRIMARY KEY,
    incident_id    TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    snapshot_id    TEXT REFERENCES incident_snapshots(id) ON DELETE SET NULL,
    format         TEXT NOT NULL,
    classification TEXT NOT NULL,
    product_type   TEXT NOT NULL,
    filename       TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL,
    created_by     TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    metadata_json  TEXT NOT NULL,
    content_blob   BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_incident ON incident_products (incident_id, created_at DESC);

CREATE TABLE IF NOT EXISTS incident_package_inbox (
    id             TEXT PRIMARY KEY,
    incident_id    TEXT NOT NULL,
    sha256         TEXT NOT NULL UNIQUE,
    origin_id      TEXT NOT NULL,
    received_by    TEXT NOT NULL,
    received_at    TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    report_json    TEXT NOT NULL,
    bundle_json    TEXT NOT NULL,
    resolution_json TEXT,
    resolved_by    TEXT,
    resolved_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_package_inbox_incident ON incident_package_inbox (incident_id, received_at DESC);

CREATE TABLE IF NOT EXISTS local_accounts (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    role          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- What an incident is linked to in the analytical world.
--
-- The two halves of this application - detections/events/models on one side,
-- incidents/scenarios/resources on the other - had no edge between them except
-- an operator retyping. This table is that edge.
--
-- `snapshot_json` is mandatory and is the entire point. Events get
-- re-clustered, models get re-run, detections get purged on the retention
-- window: a live foreign key would silently rewrite history underneath a plan.
-- So a link freezes what the operator actually saw at link time (the event's
-- bbox and detection count, the job id and its generated-at, the alert's
-- severity) and keeps meaning that afterwards. Same discipline as
-- `incident_snapshots` storing a whole bundle verbatim and
-- `incident_source_imports` keeping original bytes.
--
-- `ref_id` is TEXT even though some referents (events, jobs) have integer ids,
-- because others (alerts, drone assets, imports) do not - one column beats one
-- table per referent kind. The UNIQUE constraint makes re-linking idempotent,
-- so a repeated "attach to incident" updates rather than accumulating.
CREATE TABLE IF NOT EXISTS incident_links (
    id            TEXT PRIMARY KEY,
    incident_id   TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,
    ref_id        TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    note          TEXT NOT NULL DEFAULT '',
    actor         TEXT NOT NULL DEFAULT 'local operator',
    created_at    TEXT NOT NULL,
    UNIQUE (incident_id, kind, ref_id)
);
CREATE INDEX IF NOT EXISTS idx_incident_links ON incident_links (incident_id, kind, created_at DESC);

CREATE TABLE IF NOT EXISTS incident_audit_log (
    id          TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    action      TEXT NOT NULL,
    revision    INTEGER NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'local operator',
    changed_at  TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_incident ON incident_audit_log (incident_id, changed_at DESC);

-- Operator-added map layers (WMS / WMTS / plain XYZ).
--
-- The point of this table is that adding "our Kreis-GIS hydrant layer" or
-- "the county orthophoto service" is a *row*, not a code change to
-- basemaps.py. Everything downstream - the tile proxy, the disk cache
-- layout, map packs, offline manifests, LRU pinning - keys off the layer id
-- alone, so a layer defined here is cached and packaged by exactly the same
-- machinery as a built-in one (see layers.LayerRegistry and tiles.TileCache).
--
-- `url_template` holds an XYZ/WMTS-RESTful template; `endpoint` plus
-- `wms_layers`/`wms_styles`/`wms_version`/`wms_crs` hold what a WMS GetMap
-- request needs. Only one of the two shapes is populated per row, decided by
-- `kind`, and LayerRegistry validates that on write rather than leaving a
-- half-filled row to fail later at tile-fetch time.
--
-- `licence`/`attribution`/`limitations` are not decoration: an offline map
-- pack built from these tiles carries them into the field, where whoever
-- reads the map has no other way to find out what they are allowed to do
-- with a third party's cadastre or orthophoto.
CREATE TABLE IF NOT EXISTS custom_layers (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    url_template TEXT NOT NULL DEFAULT '',
    endpoint     TEXT NOT NULL DEFAULT '',
    wms_layers   TEXT NOT NULL DEFAULT '',
    wms_styles   TEXT NOT NULL DEFAULT '',
    wms_version  TEXT NOT NULL DEFAULT '1.3.0',
    wms_crs      TEXT NOT NULL DEFAULT 'EPSG:3857',
    image_format TEXT NOT NULL DEFAULT 'image/png',
    tile_ext     TEXT NOT NULL DEFAULT 'png',
    transparent  INTEGER NOT NULL DEFAULT 1,
    overlay      INTEGER NOT NULL DEFAULT 0,
    attribution  TEXT NOT NULL DEFAULT '',
    licence      TEXT NOT NULL DEFAULT '',
    limitations  TEXT NOT NULL DEFAULT '',
    min_zoom     INTEGER NOT NULL DEFAULT 0,
    max_zoom     INTEGER NOT NULL DEFAULT 19,
    active       INTEGER NOT NULL DEFAULT 1,
    added_by     TEXT NOT NULL DEFAULT 'local operator',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_custom_layers_active ON custom_layers (active, name);

-- Operator-editable settings, overriding the environment-derived `Settings`.
--
-- Deliberately a key/value table rather than one column per setting: the set of
-- editable settings is defined in `settings_store.EDITABLE`, and adding one
-- there should not need a schema migration. The value is JSON so a list
-- (`cap_feeds`) and an integer (`cache_days`) survive the round trip as
-- themselves rather than as strings the reader has to guess the type of.
--
-- This table holds API keys. It inherits whatever protection the database file
-- itself has and nothing more - which is the same footing as `.env`, the place
-- these values live today. What it must never do is hand them back out: see
-- `settings_store.public_view`, which returns only a masked hint.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT 'local operator',
    updated_at TEXT NOT NULL
);

-- Official public warnings polled from CAP feeds (MoWaS/NINA, DWD, IPAWS...).
--
-- Keyed on the alert's own upstream `identifier`, not a generated id, so
-- re-polling a feed updates the row it already has instead of accumulating a
-- duplicate every cycle - the pull-feed equivalent of the position feed's
-- per-`external_id` replay check.
--
-- `raw_xml` keeps the document exactly as published. An official warning is
-- evidence: what was broadcast, by whom, when. Storing only our parsed
-- interpretation would make that unauditable after the fact.
--
-- The bbox columns are denormalised from the geometry purely so a viewport
-- query is an indexed range scan rather than a JSON parse per row.
CREATE TABLE IF NOT EXISTS alerts (
    identifier   TEXT PRIMARY KEY,
    feed_id      TEXT NOT NULL DEFAULT '',
    sender       TEXT NOT NULL DEFAULT '',
    sent         TEXT,
    event        TEXT NOT NULL DEFAULT '',
    headline     TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    severity     TEXT NOT NULL DEFAULT 'Unknown',
    severity_rank INTEGER NOT NULL DEFAULT 4,
    urgency      TEXT NOT NULL DEFAULT 'Unknown',
    certainty    TEXT NOT NULL DEFAULT 'Unknown',
    effective    TEXT,
    expires      TEXT,
    geometry_json TEXT,
    properties_json TEXT NOT NULL DEFAULT '{}',
    raw_xml      TEXT NOT NULL DEFAULT '',
    west         REAL,
    south        REAL,
    east         REAL,
    north        REAL,
    received_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_expires ON alerts (expires);
CREATE INDEX IF NOT EXISTS idx_alerts_bbox ON alerts (west, east, south, north);

-- Lookup for features that arrived over the CoT gateway, keyed by the
-- sender's own event uid. An expression index rather than a column, because
-- the uid lives in `properties_json` and adding a column would mean a
-- migration plus a second place for the same fact to live. ATAK refreshes a
-- marker every few seconds, so without this index every refresh would scan
-- the whole feature table (see cot_gateway.CotGateway.db_lookup).
-- Inbound webhooks from dispatch/CAD systems.
--
-- `mapping_json` is the operator-supplied translation from the vendor's own
-- JSON shape into our position contract (see ingest/webhook.py). It is a
-- table of dotted paths, never code - the endpoint is reachable by anyone who
-- learns the URL, so an evaluated mapping language would be a remote-code
-- surface.
--
-- `token_hash` uses the same SHA-256 scheme as position_feed_sources, so a
-- leaked database still does not yield a usable credential.
CREATE TABLE IF NOT EXISTS webhooks (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    incident_id  TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    token_hash   TEXT NOT NULL,
    mapping_json TEXT NOT NULL DEFAULT '{}',
    active       INTEGER NOT NULL DEFAULT 1,
    created_by   TEXT NOT NULL DEFAULT 'local operator',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    last_received_at TEXT,
    accepted     INTEGER NOT NULL DEFAULT 0,
    rejected     INTEGER NOT NULL DEFAULT 0
);

-- Payloads that arrived but could not be mapped.
--
-- Kept rather than dropped because that is the whole difference between a
-- debuggable CAD integration and a support ticket: a vendor's format is
-- usually undocumented, so the only way to fix a mapping is to look at what
-- they actually sent. Bounded by row count (see WebhookManager.prune), since
-- a misconfigured hook can otherwise fill a field laptop's disk.
CREATE TABLE IF NOT EXISTS webhook_deadletter (
    id          TEXT PRIMARY KEY,
    hook_id     TEXT NOT NULL,
    received_at TEXT NOT NULL,
    error       TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_deadletter_hook ON webhook_deadletter (hook_id, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_features_cot_uid
    ON tactical_features (incident_id, json_extract(properties_json, '$.cot_uid'));
"""

DETECTION_COLUMNS = (
    "source",
    "satellite",
    "instrument",
    "latitude",
    "longitude",
    "acq_date",
    "acq_time",
    "acq_ts",
    "brightness",
    "brightness2",
    "scan",
    "track",
    "confidence_raw",
    "confidence_pct",
    "confidence_level",
    "frp",
    "daynight",
    "version",
    "raw_json",
)

# --------------------------------------------------------------- migrations
#
# The SCHEMA script above is idempotent, so it covers purely *additive*
# changes on its own: a brand-new table or index simply appears the next
# time the process starts. What it cannot do is touch a table that already
# exists - `CREATE TABLE IF NOT EXISTS` is a no-op the moment the name is
# taken - so a column added to `detections` later, a renamed column, or a
# backfill of existing rows all need a real, ordered migration step.
#
# Hence this ladder: an ordered list of `MigrationStep`s, each carrying the
# `PRAGMA user_version` the database is at *once that step has succeeded*.
# `apply_migrations` walks it from the version the file on disk reports up
# to `SCHEMA_VERSION`, so a database at any prior version catches up by
# replaying exactly the steps it missed, in order.
#
# The contract a step has to honour:
#
#   * `apply` is either raw SQL (one or more statements) or a Python
#     callable taking the connection - real migrations eventually have to
#     rename/transform/backfill data, which SQL alone can't always express.
#   * The runner wraps each step in its own transaction and bumps
#     `user_version` *inside that same transaction*, so a step either lands
#     completely (body plus version bump) or not at all: `user_version`
#     lives in the database header and rolls back with everything else.
#     A crash or a raising step therefore leaves the file at the last
#     version whose body fully succeeded, never half-migrated-but-stamped.
#   * Because the runner owns the transaction, a step must not COMMIT or
#     ROLLBACK itself, and must avoid statements SQLite refuses inside a
#     transaction (`VACUUM`, `PRAGMA journal_mode`, `PRAGMA foreign_keys`).
#     A rebuild that needs foreign keys out of the way can use
#     `PRAGMA defer_foreign_keys=ON`, which *does* work inside one.
#   * A step must be safe to run against a table that is already in its
#     final shape. SCHEMA runs first and always creates tables as the
#     current code defines them, so on a fresh database a step that adds a
#     column would find the column already present. Check before changing -
#     `_add_missing_columns` below is the pattern.


@dataclass(frozen=True)
class MigrationStep:
    """One rung of the schema ladder.

    ``version``      the ``PRAGMA user_version`` the database reaches when
                     this step succeeds (unique across the ladder; steps run
                     in ascending version order).
    ``description``  human-readable, quoted in errors and worth keeping
                     accurate since it is the only record of intent once the
                     step is years old.
    ``apply``        raw SQL, or a callable receiving the open connection.
    """

    version: int
    description: str
    apply: str | Callable[[sqlite3.Connection], None]


def _add_missing_columns(
    table: str, columns: Sequence[tuple[str, str]]
) -> Callable[[sqlite3.Connection], None]:
    """Build a migration body that ALTERs in only the columns ``table`` is
    actually missing.

    The existence check is what makes such a step safe on a database whose
    table SCHEMA just created in its current (already-complete) shape, where
    a bare ``ADD COLUMN`` would fail with "duplicate column name"."""

    def step(conn: sqlite3.Connection) -> None:
        # PRAGMA table_info's second column is the column name; indexing by
        # position (rather than by name) works whether or not the caller's
        # connection has a row factory set.
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, column_type in columns:
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    return step


# Ordered oldest-first. Versions 1-3 predate this ladder; everything they
# introduced was additive and is therefore already covered by the SCHEMA
# script, which is why the first rung here is 4 - the version at which
# `detections` gained `raw_json`. (That column-add was previously a bare
# "(name, type)" tuple applied on every startup; it is a genuine, shipped
# migration, so it becomes the ladder's first real step rather than being
# rewritten as a synthetic example.)
MIGRATIONS: tuple[MigrationStep, ...] = (
    MigrationStep(
        version=4,
        description="detections.raw_json - retrofit onto pre-v4 databases",
        apply=_add_missing_columns("detections", (("raw_json", "TEXT"),)),
    ),
    # v6 needs a real rung, unlike v5. v5 added only brand-new tables, which the
    # idempotent SCHEMA script creates by itself; this adds a column to
    # `incidents`, which already exists, and `CREATE TABLE IF NOT EXISTS` is a
    # no-op the moment the name is taken. That asymmetry is exactly what the
    # ladder is for.
    MigrationStep(
        version=6,
        description="incidents.aoi_json - the incident's area of interest",
        apply=_add_missing_columns("incidents", (("aoi_json", "TEXT"),)),
    ),
)

# Bumped whenever SCHEMA or MIGRATIONS gain something an existing database
# file won't already have. `_init_schema` compares this against the file's
# own `PRAGMA user_version` to decide whether a pre-migration backup is
# warranted and which steps are still pending. A version that needs no step
# of its own (a purely additive SCHEMA change) simply has no entry in
# MIGRATIONS above - the runner stamps the gap once the ladder is done.
# v6 adds `incidents.aoi_json` - a column on an existing table, so it needs a
# real rung in MIGRATIONS above (see the note there) - and `incident_links`,
# which is another new table and does not.
#
# v5 added `custom_layers` (operator-added WMS/WMTS/XYZ map layers), `alerts`
# (CAP public warnings) and the `webhooks`/`webhook_deadletter` pair
# (dispatch/CAD ingest). All are brand-new tables, which the
# idempotent SCHEMA script above creates on its own, so there is deliberately
# no rung for any of them in MIGRATIONS - the runner stamps the gap once the
# ladder is done. The version still has to move, or an existing database would never
# trigger the pre-migration backup.
#
# v7 adds `app_settings` (operator-editable settings overriding the environment).
# Another brand-new table, so - like v5 - it needs the version bump but no rung.
SCHEMA_VERSION = 7


def _refuse_downgrade(current: int, target: int) -> None:
    """Raise if the file on disk is *newer* than this code understands.

    Silently proceeding would mean older code writing a schema it doesn't
    know the shape of - and the idempotent SCHEMA script would happily
    "migrate" the file backwards - so refusing to open is the only safe
    answer."""
    if current > target:
        raise RuntimeError(
            f"database schema {current} is newer than supported schema {target}"
        )


def _ordered_steps(steps: Iterable[MigrationStep]) -> tuple[MigrationStep, ...]:
    """The ladder sorted by version, rejecting duplicate/invalid versions.

    Sorting here rather than trusting declaration order means a step
    appended in the wrong place can't silently apply out of sequence; a
    repeated version is a genuine authoring bug (two different bodies
    claiming the same rung) and is refused outright."""
    ordered = tuple(sorted(steps, key=lambda step: step.version))
    seen: set[int] = set()
    for step in ordered:
        if step.version < 1:
            raise RuntimeError(
                f"migration version must be >= 1, got {step.version} ({step.description})"
            )
        if step.version in seen:
            raise RuntimeError(f"duplicate migration version {step.version}")
        seen.add(step.version)
    return ordered


def _execute_step_sql(conn: sqlite3.Connection, script: str) -> None:
    """Run a step's raw-SQL body, statement by statement.

    Deliberately not `executescript`: that helper commits whatever
    transaction is already in progress before it starts, which would tear
    the step out of the transaction the runner wrapped around it (and with
    it the atomicity of body-plus-version-bump). `sqlite3.complete_statement`
    lets us find the statement boundaries ourselves and feed them to
    `execute` one at a time, leaving the surrounding transaction intact."""
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            conn.execute(buffer)
            buffer = ""
    # A trailing statement without its closing semicolon still counts; a
    # trailing comment (or nothing at all) does not.
    tail = buffer.strip()
    if tail and any(
        line.strip() and not line.strip().startswith("--") for line in tail.splitlines()
    ):
        conn.execute(tail)


def apply_migrations(
    conn: sqlite3.Connection,
    steps: Iterable[MigrationStep] | None = None,
    target: int | None = None,
) -> int:
    """Walk ``conn`` from its current `user_version` up to ``target``,
    applying each pending step in ascending version order, and return the
    version reached.

    Each step gets its own transaction, committed before the next one
    starts, rather than the whole ladder sharing one. That is a deliberate
    trade: a failure part-way through leaves the earlier steps durably
    applied and the file honestly stamped at the last version that fully
    succeeded, so re-running resumes from there instead of replaying
    migrations that may have taken minutes over a large table. (The
    all-or-nothing option is still available to the operator, and is
    strictly better than one giant transaction for a *bad* migration rather
    than a failed one: `Database` takes a pre-migration backup of the whole
    file first, so the entire ladder can be abandoned by restoring it.)

    Callers must not have a transaction in progress: the runner switches the
    connection to manual transaction control for the duration and restores
    the previous setting afterwards."""
    ladder = _ordered_steps(MIGRATIONS if steps is None else steps)
    goal = SCHEMA_VERSION if target is None else int(target)
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    _refuse_downgrade(current, goal)
    if ladder and ladder[-1].version > goal:
        # An authoring slip: a step exists for a version the code doesn't
        # claim to be at yet. Applying it would stamp the file beyond
        # SCHEMA_VERSION and every later open would then refuse the file.
        raise RuntimeError(
            f"migration version {ladder[-1].version} exceeds schema version {goal}"
        )

    previous_isolation = conn.isolation_level
    conn.isolation_level = None  # manual BEGIN/COMMIT, see below
    try:
        for step in ladder:
            if step.version <= current:
                continue  # already on this database
            conn.execute("BEGIN IMMEDIATE")
            try:
                body = step.apply
                if isinstance(body, str):
                    _execute_step_sql(conn, body)
                else:
                    body(conn)
                # Inside the same transaction as the body above: the two
                # commit together or not at all.
                conn.execute(f"PRAGMA user_version={int(step.version)}")
                conn.execute("COMMIT")
            except BaseException as exc:
                try:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                except sqlite3.Error:  # pragma: no cover - rollback is best effort
                    pass
                raise RuntimeError(
                    f"migration to version {step.version} failed "
                    f"({step.description}): {exc}"
                ) from exc
            current = step.version
        if current < goal:
            # Versions with no step of their own - additive SCHEMA changes
            # that the CREATE ... IF NOT EXISTS script already applied.
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"PRAGMA user_version={goal}")
            conn.execute("COMMIT")
            current = goal
    finally:
        conn.isolation_level = previous_isolation
    return current


class Database:
    """Thin sqlite3 wrapper with a connection per thread."""

    def __init__(self, path: Path) -> None:
        """Open (creating if needed) the sqlite file at ``path`` and bring its
        schema up to date. Cheap to call more than once per process - each
        thread that touches this object lazily gets its own connection via
        the ``conn`` property below rather than sharing one across threads."""
        self.path = path
        # Recorded before the file is touched below: `_init_schema` needs to
        # know whether this is a brand-new database (nothing to migrate, no
        # backup needed) or a pre-existing one that might be on an older
        # schema version.
        self._database_existed = path.is_file() and path.stat().st_size > 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        # Every connection handed out, so close() can shut them all down -
        # queries run on pool threads we do not otherwise get to revisit.
        # Separate from _write_lock: write methods hold _write_lock while
        # touching self.conn, and that lock isn't reentrant.
        self._registry_lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        self._closed = False
        # Guards against a *stale* rollback: if a write method is interrupted
        # before its own connection has begun a transaction, rolling back is a
        # harmless no-op, so this needs no bookkeeping of its own.
        try:
            self._init_schema()
        except Exception:
            self.close()
            raise

    @contextmanager
    def _write(self):
        """Hold the write lock, and roll back if the body raises.

        Every write method in this class used to take `_write_lock` directly and
        commit at the end, with no rollback path. A failure partway through -
        disk full, a constraint violation, a bug - therefore left an *open
        transaction* on the shared connection. Two consequences follow, and the
        second is the dangerous one: every other writer then blocks on SQLite's
        busy timeout, and the next write on this connection to succeed commits
        the earlier partial batch along with its own, silently.

        The newer managers (`operations`, `alerts`, `telemetry`, the settings
        store) all wrap their writes in try/commit/except-rollback already; this
        class predates that discipline. Keeping the existing `commit()` calls
        inside the body means the change is one line per method rather than a
        restructuring: on success the commit has already happened and the
        rollback is unreachable; on failure it runs.

        Not reentrant, like the plain lock it replaces - no write method may
        call another.
        """
        with self._write_lock:
            try:
                yield self.conn
            except Exception:
                try:
                    self.conn.rollback()
                except Exception:  # pragma: no cover - a connection already gone
                    log.exception("Rollback failed after a write error")
                raise

    # ------------------------------------------------------------------ core

    @property
    def conn(self) -> sqlite3.Connection:
        """The calling thread's own sqlite3 connection, creating it on first
        use. sqlite3 connections aren't safe to share across threads for
        concurrent use, so each pool/worker thread gets a private one instead
        of contending on a single shared connection."""
        # Fast path: this thread already has a connection, no lock needed -
        # threading.local() itself keeps that read race-free per thread.
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        # First call on this thread: creating and registering the new
        # connection has to happen under the same lock close() uses, so the
        # two can't interleave into either a leaked (never-closed)
        # connection or a connection close() already iterated past.
        with self._registry_lock:
            if self._closed:
                raise RuntimeError("Database is closed")
            # check_same_thread=False so close() can reap pool threads' handles -
            # each thread still gets its own connection and writes hold the lock.
            conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            self._connections.append(conn)
        return conn

    def _backup_before_migration(self, current: int) -> Path:
        """Copy the database as it stands *right now*, before a single
        migration touches it, to ``<name>.pre-migration-v<current>``.

        A byte-consistent online backup (rather than a file copy) makes the
        pre-migration state recoverable even with WAL mode active and other
        connections open. This is the escape hatch for a migration that
        succeeds mechanically but turns out to be *wrong*: the per-step
        commits in `apply_migrations` can't undo those, restoring this file
        can."""
        backup_path = self.path.with_name(
            f"{self.path.stem}.pre-migration-v{current}{self.path.suffix}"
        )
        target = sqlite3.connect(backup_path)
        try:
            self.conn.backup(target)
            target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            target.commit()
        finally:
            target.close()
        return backup_path

    def _init_schema(self) -> None:
        """Create any missing tables/indexes, then run every migration step
        this database file has not seen yet, backing the file up first if
        it's an older, pre-existing database. Safe to run on every startup:
        `CREATE TABLE IF NOT EXISTS` is idempotent and the ladder skips
        steps at or below the file's own version, so a database already on
        the current schema just gets a cheap no-op pass."""
        with self._write():
            conn = self.conn
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            # Checked before anything is written: a newer file opened by
            # older code would otherwise be silently "migrated" backwards by
            # the executescript below.
            _refuse_downgrade(current, SCHEMA_VERSION)
            if self._database_existed and current < SCHEMA_VERSION:
                self._backup_before_migration(current)
            # Additive part first, so a migration step can assume every
            # table and index the current code knows about exists.
            conn.executescript(SCHEMA)
            conn.commit()
            if current < SCHEMA_VERSION:
                apply_migrations(conn, MIGRATIONS, SCHEMA_VERSION)

    def close(self) -> None:
        """Close every connection this Database has ever handed out (across
        all threads) and mark it closed so `conn` refuses to hand out new
        ones afterwards."""
        with self._registry_lock:
            self._closed = True
            for conn in self._connections:
                try:
                    conn.close()
                except sqlite3.Error:  # pragma: no cover - best effort on shutdown
                    pass
            self._connections.clear()
            # Reassigning under the same lock (rather than after releasing
            # it) means a thread blocked on this lock at the top of `conn`
            # always observes both the fresh threading.local() and
            # _closed=True together, never a state where it appears open
            # again on a stale local.
            self._local = threading.local()

    # ------------------------------------------------------------- detections

    def upsert_detections(self, rows: Sequence[dict[str, Any]]) -> int:
        """Insert detections, updating the mutable fields of ones already
        cached instead of ignoring them. Returns the number of rows touched
        (inserted or updated).

        The natural key (source, satellite, position, timestamp) never
        changes once FIRMS has assigned it, but the "hot" 2-day re-check
        window (see cache.py) exists precisely because FIRMS keeps revising
        a recent detection's confidence/FRP/brightness as it reprocesses -
        a plain INSERT OR IGNORE would fetch those revisions and then
        silently discard them because the row already existed, so hot-day
        re-checks would in practice never receive their own point.
        """
        if not rows:
            return 0
        key_columns = ("source", "satellite", "latitude", "longitude", "acq_date", "acq_time")
        mutable_columns = tuple(col for col in DETECTION_COLUMNS if col not in key_columns)
        placeholders = ", ".join("?" for _ in DETECTION_COLUMNS)
        set_clause = ", ".join(f"{col} = excluded.{col}" for col in mutable_columns)
        sql = (
            f"INSERT INTO detections ({', '.join(DETECTION_COLUMNS)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT ({', '.join(key_columns)}) DO UPDATE SET {set_clause}"
        )
        payload = [tuple(row.get(col) for col in DETECTION_COLUMNS) for row in rows]
        with self._write():
            cur = self.conn.executemany(sql, payload)
            self.conn.commit()
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    def query_detections(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        sources: Sequence[str] | None = None,
        confidence_levels: Sequence[str] | None = None,
        min_frp: float | None = None,
        daynight: str | None = None,
        limit: int = 20000,
    ) -> list[sqlite3.Row]:
        """Detections matching every supplied filter (all optional and
        AND-ed together), newest first, capped at ``limit`` rows for the
        map/table views. Builds the WHERE clause piece by piece so that an
        omitted filter costs nothing rather than turning into an always-true
        condition."""
        where: list[str] = []
        params: list[Any] = []

        if start_ts is not None:
            where.append("acq_ts >= ?")
            params.append(start_ts)
        if end_ts is not None:
            where.append("acq_ts <= ?")
            params.append(end_ts)

        if bbox is not None:
            west, south, east, north = bbox
            where.append("latitude BETWEEN ? AND ?")
            params.extend([south, north])
            # Longitude needs its own helper (rather than a plain BETWEEN)
            # because a bbox can straddle the antimeridian, where west > east.
            lon_clause, lon_params = lon_range_sql("longitude", west, east)
            where.append(lon_clause)
            params.extend(lon_params)

        if sources:
            where.append(f"source IN ({', '.join('?' for _ in sources)})")
            params.extend(sources)
        if confidence_levels:
            where.append(
                f"confidence_level IN ({', '.join('?' for _ in confidence_levels)})"
            )
            params.extend(confidence_levels)
        if min_frp is not None and min_frp > 0:
            where.append("frp >= ?")
            params.append(min_frp)
        if daynight in ("D", "N"):
            where.append("daynight = ?")
            params.append(daynight)

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = (
            "SELECT source, satellite, instrument, latitude, longitude, acq_date, "
            "acq_time, acq_ts, brightness, brightness2, confidence_pct, "
            "confidence_level, frp, daynight, scan, track "
            f"FROM detections {clause} ORDER BY acq_ts DESC LIMIT ?"
        )
        params.append(int(limit))
        return self.conn.execute(sql, params).fetchall()

    def daily_counts(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        sources: Sequence[str] | None = None,
    ) -> list[sqlite3.Row]:
        """Per-day detection counts and summed FRP for the same filters as
        ``query_detections``, used to draw the activity histogram - grouping
        in SQL instead of pulling every row into Python keeps that cheap even
        for a long time range."""
        where: list[str] = []
        params: list[Any] = []
        if start_ts is not None:
            where.append("acq_ts >= ?")
            params.append(start_ts)
        if end_ts is not None:
            where.append("acq_ts <= ?")
            params.append(end_ts)
        if bbox is not None:
            west, south, east, north = bbox
            where.append("latitude BETWEEN ? AND ?")
            params.extend([south, north])
            lon_clause, lon_params = lon_range_sql("longitude", west, east)
            where.append(lon_clause)
            params.extend(lon_params)
        if sources:
            where.append(f"source IN ({', '.join('?' for _ in sources)})")
            params.extend(sources)

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = (
            "SELECT acq_date AS day, COUNT(*) AS count, "
            "COALESCE(SUM(frp), 0) AS frp_total "
            f"FROM detections {clause} GROUP BY acq_date ORDER BY acq_date"
        )
        return self.conn.execute(sql, params).fetchall()

    # --------------------------------------------------------------- coverage

    def coverage_state(
        self, source: str, cell_x: int, cell_y: int, days: Iterable[str]
    ) -> dict[str, sqlite3.Row]:
        """Known coverage rows for one (source, cell) across ``days``, keyed
        by day. cache.py's planner calls this before enqueueing a fetch, so a
        day missing from the returned dict (never fetched) or with
        ``status`` other than ``'ok'`` (errored last time) is what tells it a
        gap still needs filling."""
        day_list = list(days)
        if not day_list:
            return {}
        sql = (
            "SELECT day, fetched_at, status, row_count FROM coverage "
            "WHERE source = ? AND cell_x = ? AND cell_y = ? "
            f"AND day IN ({', '.join('?' for _ in day_list)})"
        )
        rows = self.conn.execute(sql, [source, cell_x, cell_y, *day_list]).fetchall()
        return {row["day"]: row for row in rows}

    def mark_coverage(
        self,
        source: str,
        cell_x: int,
        cell_y: int,
        days: Sequence[str],
        *,
        row_count: int = 0,
        status: str = "ok",
        note: str | None = None,
    ) -> None:
        """Record that (source, cell) was fetched for each of ``days``,
        overwriting any prior attempt for that day rather than accumulating -
        a re-fetch (e.g. a hot-day refresh, or a retry after an earlier
        error) fully supersedes what came before, so the old row_count/
        status/note would just be stale noise if kept."""
        if not days:
            return
        now = int(time.time())
        payload = [
            (source, cell_x, cell_y, day, now, row_count, status, note) for day in days
        ]
        with self._write():
            self.conn.executemany(
                "INSERT INTO coverage "
                "(source, cell_x, cell_y, day, fetched_at, row_count, status, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (source, cell_x, cell_y, day) DO UPDATE SET "
                "fetched_at = excluded.fetched_at, row_count = excluded.row_count, "
                "status = excluded.status, note = excluded.note",
                payload,
            )
            self.conn.commit()

    def cached_cells(self, min_day: str) -> list[sqlite3.Row]:
        """Distinct (source, cell) pairs we have ever fetched, for refreshes."""
        return self.conn.execute(
            "SELECT DISTINCT source, cell_x, cell_y FROM coverage "
            "WHERE day >= ? AND status = 'ok'",
            (min_day,),
        ).fetchall()

    # ---------------------------------------------------------------- upkeep

    def purge_older_than(self, cutoff: date) -> tuple[int, int]:
        """Drop everything older than the retention window.

        A purged detection can still be referenced by ``event_members`` (an
        event clustered from it before it aged out) - there's no real
        foreign key enforcing that (SQLite only checks FKs that are
        actually declared, and this schema doesn't declare one here so a
        purge can't fail on it), so this cleans that up explicitly: drop
        the now-dangling membership rows, then recompute each affected
        event's summary (count/time span/bbox/centroid) from whatever
        members remain, or delete the event outright if none do. Without
        this, an old event's summary card and its detail view silently
        drift apart as its detections get purged out from under it.
        """
        cutoff_day = cutoff.isoformat()
        with self._write():
            # Everything below addresses the expiring rows through *subqueries*
            # rather than by pulling their ids into Python and binding one
            # placeholder each. That earlier shape had a hard ceiling: SQLite
            # allows 32766 host parameters per statement (999 before 3.32), and
            # a single expiring day of a whole-world FIRMS fetch clears that
            # easily. Past the limit the purge raised, so retention silently
            # stopped - and because `CacheManager.start()` runs one purge before
            # the lifespan yields, the server then refused to boot at all,
            # exactly after the downtime that let the backlog build. A subquery
            # has no such limit and does the join inside SQLite besides.
            #
            # The affected event ids still have to be captured *before* the
            # membership rows are deleted, or the link is gone; a temp table
            # holds them so that list is unbounded too.
            self.conn.execute("DROP TABLE IF EXISTS temp.purge_events")
            self.conn.execute(
                """
                CREATE TEMP TABLE purge_events AS
                SELECT DISTINCT event_id FROM event_members
                WHERE detection_id IN (SELECT id FROM detections WHERE acq_date < ?)
                """,
                (cutoff_day,),
            )
            affected = self.conn.execute("SELECT COUNT(*) FROM temp.purge_events").fetchone()[0]
            self.conn.execute(
                "DELETE FROM event_members WHERE detection_id IN "
                "(SELECT id FROM detections WHERE acq_date < ?)",
                (cutoff_day,),
            )

            det = self.conn.execute(
                "DELETE FROM detections WHERE acq_date < ?", (cutoff_day,)
            ).rowcount
            cov = self.conn.execute(
                "DELETE FROM coverage WHERE day < ?", (cutoff_day,)
            ).rowcount

            # Two more caches that are the same *kind* of thing as detections
            # and coverage - observations and derived geometric bookkeeping,
            # both recomputable - but which had no retention path at all.
            # `eumetsat_fires` accumulates global fire pixels every ~10 minutes
            # and `swath_coverage` a row per satellite/cell/day, forever. On a
            # week-scale incident deployment they are the realistic way a field
            # laptop's disk fills, and a full disk stops the whole application,
            # not just the layer that grew.
            #
            # Deliberately *not* extended to `incident_audit_log`: an audit
            # trail is evidence, and silently deleting it to save space is a
            # policy decision for whoever owns the incident record, not
            # something upkeep should decide on its own. Its size is reported
            # instead - see `table_sizes`.
            cutoff_epoch = datetime.combine(cutoff, datetime.min.time(), tzinfo=timezone.utc).timestamp()
            self.conn.execute("DELETE FROM eumetsat_fires WHERE acq_ts < ?", (cutoff_epoch,))
            self.conn.execute("DELETE FROM swath_coverage WHERE day < ?", (cutoff_day,))

            if affected:
                placeholders = "SELECT event_id FROM temp.purge_events"
                # Drop events with no surviving members *before* the
                # recompute below - an event with zero members has nothing
                # for those correlated MIN/MAX/AVG subqueries to aggregate,
                # so they'd come back NULL and violate the NOT NULL bbox/
                # centroid columns if this ran the other way round.
                self.conn.execute(
                    f"""
                    DELETE FROM events
                    WHERE id IN ({placeholders})
                      AND id NOT IN (SELECT DISTINCT event_id FROM event_members)
                    """
                )
                self.conn.execute(
                    f"""
                    UPDATE events SET
                        detection_count = (
                            SELECT COUNT(*) FROM event_members m WHERE m.event_id = events.id
                        ),
                        first_seen = (
                            SELECT MIN(d.acq_ts) FROM event_members m
                            JOIN detections d ON d.id = m.detection_id
                            WHERE m.event_id = events.id
                        ),
                        last_seen = (
                            SELECT MAX(d.acq_ts) FROM event_members m
                            JOIN detections d ON d.id = m.detection_id
                            WHERE m.event_id = events.id
                        ),
                        bbox_west = (
                            SELECT MIN(d.longitude) FROM event_members m
                            JOIN detections d ON d.id = m.detection_id
                            WHERE m.event_id = events.id
                        ),
                        bbox_east = (
                            SELECT MAX(d.longitude) FROM event_members m
                            JOIN detections d ON d.id = m.detection_id
                            WHERE m.event_id = events.id
                        ),
                        bbox_south = (
                            SELECT MIN(d.latitude) FROM event_members m
                            JOIN detections d ON d.id = m.detection_id
                            WHERE m.event_id = events.id
                        ),
                        bbox_north = (
                            SELECT MAX(d.latitude) FROM event_members m
                            JOIN detections d ON d.id = m.detection_id
                            WHERE m.event_id = events.id
                        ),
                        centroid_lat = (
                            SELECT AVG(d.latitude) FROM event_members m
                            JOIN detections d ON d.id = m.detection_id
                            WHERE m.event_id = events.id
                        ),
                        centroid_lon = (
                            SELECT AVG(d.longitude) FROM event_members m
                            JOIN detections d ON d.id = m.detection_id
                            WHERE m.event_id = events.id
                        )
                    WHERE id IN ({placeholders})
                    """
                )

            self.conn.execute("DROP TABLE IF EXISTS temp.purge_events")
            self.conn.commit()
        return max(det, 0), max(cov, 0)

    def purge_positions(self, cutoff: date) -> int:
        """Drop position reports from incidents closed before ``cutoff``.

        Separate from `purge_older_than` because position history is a
        different kind of record. Detections are a cache of somebody else's
        data, refetchable at will; a position report is evidence of where a
        crew was, and is not recoverable once gone. So it gets its own, far
        longer window (`settings.position_retention_days`), and two guards:

        * an incident that is not **closed** is never touched, whatever the age
          of its reports - an incident running longer than the retention window
          is a large incident, precisely the one whose history matters most;
        * age is measured from the incident's own last update, not from each
          report, so a closed incident's track is removed whole rather than
          eroded from the front into a partial trail that looks like a unit
          appeared from nowhere.

        Returns the number of reports removed.
        """
        cutoff_iso = cutoff.isoformat()
        with self._write():
            removed = self.conn.execute(
                """
                DELETE FROM vehicle_position_reports
                WHERE incident_id IN (
                    SELECT id FROM incidents
                    WHERE status = 'closed' AND substr(updated_at, 1, 10) < ?
                )
                """,
                (cutoff_iso,),
            ).rowcount
            self.conn.commit()
        return max(removed, 0)

    def table_sizes(self) -> dict[str, int]:
        """Row counts for the tables that grow without bound, for `/api/status`.

        Reported rather than purged for `incident_audit_log`: an audit trail is
        evidence, and deleting it to reclaim space is a decision for whoever
        owns the incident record. Making the size visible is what lets them
        make it, instead of discovering the problem as a full disk.
        """
        watched = ("detections", "vehicle_position_reports", "incident_audit_log",
                   "eumetsat_fires", "swath_coverage", "alerts")
        sizes: dict[str, int] = {}
        for table in watched:
            try:
                sizes[table] = int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:  # pragma: no cover - table absent on an old file
                continue
        return sizes

    def vacuum(self) -> None:
        """Reclaim space left by purged rows by rewriting the whole file.
        Exclusive and can take a while on a large database, so callers only
        run this from an explicit maintenance action, never on a hot path."""
        with self._write():
            self.conn.execute("VACUUM")

    def stats(self) -> dict[str, Any]:
        """Summary counters for the admin/status view: total detections and
        their date range, a per-source breakdown, how many coverage cells
        have been recorded, and on-disk file size."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, MIN(acq_date) AS first_day, "
            "MAX(acq_date) AS last_day FROM detections"
        ).fetchone()
        per_source = self.conn.execute(
            "SELECT source, COUNT(*) AS count FROM detections "
            "GROUP BY source ORDER BY count DESC"
        ).fetchall()
        cov = self.conn.execute(
            "SELECT COUNT(*) AS cells, MAX(fetched_at) AS last_fetch FROM coverage"
        ).fetchone()
        size_bytes = self.path.stat().st_size if self.path.exists() else 0
        return {
            "detections": row["total"] or 0,
            "first_day": row["first_day"],
            "last_day": row["last_day"],
            "per_source": {r["source"]: r["count"] for r in per_source},
            "coverage_entries": cov["cells"] or 0,
            "last_fetch": cov["last_fetch"],
            "db_size_bytes": size_bytes,
        }

    # ------------------------------------------------------------------ meta

    def get_meta(self, key: str) -> str | None:
        """A single string value from the small ``meta`` key/value table used
        for miscellaneous process-wide bookkeeping, or None if unset."""
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Set (or overwrite) a ``meta`` key/value pair."""
        with self._write():
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self.conn.commit()

    # -------------------------------------------------------------------- tle

    def get_tle(self, satellite: str, max_age_seconds: int) -> sqlite3.Row | None:
        """The cached TLE for ``satellite`` if one exists and is still fresh
        enough to trust, else None so the caller knows to re-fetch. Orbital
        elements drift in accuracy over time, so a stale row is treated the
        same as no row at all rather than being handed back anyway."""
        row = self.conn.execute(
            "SELECT * FROM tle WHERE satellite = ?", (satellite,)
        ).fetchone()
        if row is None:
            return None
        if time.time() - row["fetched_at"] > max_age_seconds:
            return None
        return row

    def set_tle(self, satellite: str, line1: str, line2: str) -> None:
        """Cache a freshly-fetched TLE, replacing whatever was stored for
        that satellite before."""
        with self._write():
            self.conn.execute(
                "INSERT INTO tle (satellite, line1, line2, fetched_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (satellite) DO UPDATE SET "
                "line1 = excluded.line1, line2 = excluded.line2, fetched_at = excluded.fetched_at",
                (satellite, line1, line2, int(time.time())),
            )
            self.conn.commit()

    # ---------------------------------------------------------- swath coverage

    def swath_computed(self, satellite: str, day: str) -> bool:
        """Whether any swath cell has been recorded for (satellite, day) -
        a cheap existence check for callers that don't need the timestamp
        `swath_computed_at` returns."""
        row = self.conn.execute(
            "SELECT 1 FROM swath_coverage WHERE satellite = ? AND day = ? LIMIT 1",
            (satellite, day),
        ).fetchone()
        return row is not None

    def swath_computed_at(self, satellite: str, day: str) -> int | None:
        """Most recent computed_at for that (satellite, day), or None if
        never computed. A finished past day is cached forever once computed -
        "today" is still accumulating passes, so the caller re-checks this
        against a short TTL rather than treating any row as final."""
        row = self.conn.execute(
            "SELECT MAX(computed_at) AS ts FROM swath_coverage WHERE satellite = ? AND day = ?",
            (satellite, day),
        ).fetchone()
        return row["ts"] if row and row["ts"] is not None else None

    def mark_swath_cells(
        self,
        satellite: str,
        day: str,
        cells: dict[tuple[int, int], tuple[int, int, int]],
    ) -> None:
        """Record a satellite's computed swath footprint for one day.
        ``cells`` maps (cell_x, cell_y) -> (pass_count, first_ts, last_ts).
        Each call replaces the whole (satellite, cell, day) row rather than
        merging - the caller (orbits.py) recomputes pass counts/timing from
        the full set of orbit passes each time, so the incoming value is
        already the complete, authoritative one for that key."""
        if not cells:
            return
        now = int(time.time())
        payload = [
            (satellite, x, y, day, count, first_ts, last_ts, now)
            for (x, y), (count, first_ts, last_ts) in cells.items()
        ]
        with self._write():
            self.conn.executemany(
                "INSERT INTO swath_coverage "
                "(satellite, cell_x, cell_y, day, pass_count, first_ts, last_ts, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (satellite, cell_x, cell_y, day) DO UPDATE SET "
                "pass_count = excluded.pass_count, first_ts = excluded.first_ts, "
                "last_ts = excluded.last_ts, computed_at = excluded.computed_at",
                payload,
            )
            self.conn.commit()

    def swath_cells_for_bbox(
        self, bbox: tuple[float, float, float, float], day: str, cell_size: float
    ) -> list[sqlite3.Row]:
        """Covered cells overlapping bbox on that day, one row per satellite
        that saw the cell, with pass timing. Wide-swath polar orbiters cover
        essentially the whole globe within a day, so *whether* a cell was
        seen is rarely the interesting question - the caller aggregates
        first_ts/last_ts across satellites so *how recently* is what the UI
        actually shows."""
        west, south, east, north = bbox
        # Same cell-indexing scheme as cache.py's cells_for_bbox: shifting by
        # +180/+90 before dividing turns lon/lat into non-negative grid
        # coordinates, so cell (0, 0) is the SW corner of the world. The
        # tiny epsilon on the upper edge keeps a bbox edge that lands exactly
        # on a cell boundary from spilling into one extra cell to the
        # east/north.
        x0 = int(math.floor((west + 180.0) / cell_size))
        x1 = int(math.floor((east + 180.0 - 1e-9) / cell_size))
        y0 = int(math.floor((south + 90.0) / cell_size))
        y1 = int(math.floor((north + 90.0 - 1e-9) / cell_size))
        return self.conn.execute(
            "SELECT cell_x, cell_y, satellite, pass_count, first_ts, last_ts "
            "FROM swath_coverage "
            "WHERE day = ? AND cell_x BETWEEN ? AND ? AND cell_y BETWEEN ? AND ?",
            (day, x0, x1, y0, y1),
        ).fetchall()

    # ------------------------------------------------------------------ events

    def list_events(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        """Fire events (see events.py), most recently active first, optionally
        limited to those whose bbox overlaps ``bbox``."""
        where: list[str] = []
        params: list[Any] = []
        if bbox is not None:
            west, south, east, north = bbox
            # Overlap test (not containment) - an event whose bbox merely
            # intersects the viewport still belongs on screen.
            where.append("bbox_west <= ? AND bbox_east >= ? AND bbox_south <= ? AND bbox_north >= ?")
            params.extend([east, west, north, south])
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(int(limit))
        return self.conn.execute(
            f"SELECT * FROM events {clause} ORDER BY last_seen DESC LIMIT ?", params
        ).fetchall()

    def get_event(self, event_id: int) -> sqlite3.Row | None:
        """A single event's summary row, or None if no such event exists."""
        return self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()

    def event_detections(self, event_id: int) -> list[sqlite3.Row]:
        """Every detection belonging to an event, oldest first, joined
        through `event_members` back to their full `detections` rows."""
        return self.conn.execute(
            "SELECT d.* FROM detections d "
            "JOIN event_members m ON m.detection_id = d.id "
            "WHERE m.event_id = ? ORDER BY d.acq_ts",
            (event_id,),
        ).fetchall()

    # ------------------------------------------------------------- industrial

    def industrial_sources_in_bbox(
        self, bbox: tuple[float, float, float, float]
    ) -> list[sqlite3.Row]:
        """Candidate industrial sources in bbox, with their latest score if
        one has been computed (a candidate can exist - fetched from OSM -
        without ever having been scored yet)."""
        west, south, east, north = bbox
        where = ["s.latitude BETWEEN ? AND ?"]
        params: list[Any] = [south, north]
        lon_clause, lon_params = lon_range_sql("s.longitude", west, east)
        where.append(lon_clause)
        params.extend(lon_params)
        sql = (
            "SELECT s.id, s.osm_type, s.osm_id, s.latitude, s.longitude, "
            "s.evidence_class, s.tags_json, "
            "sc.score, sc.classification, sc.detection_count, sc.distinct_days, "
            "sc.span_days, sc.spread_km, sc.match_radius_km, sc.window_days, sc.computed_at "
            "FROM industrial_sources s "
            "LEFT JOIN industrial_source_scores sc ON sc.source_id = s.id "
            f"WHERE {' AND '.join(where)}"
        )
        return self.conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------- eumetsat

    def eumetsat_fires_in_bbox(
        self, bbox: tuple[float, float, float, float], since_ts: float = 0.0
    ) -> list[sqlite3.Row]:
        """Cached EUMETSAT MTG/FCI active-fire pixels in ``bbox`` acquired at
        or after ``since_ts`` - a local read only, never triggers a live
        Data Store fetch (see nexfiremap/eumetsat.py's autofetch job)."""
        west, south, east, north = bbox
        where = ["latitude BETWEEN ? AND ?", "acq_ts >= ?"]
        params: list[Any] = [south, north, since_ts]
        lon_clause, lon_params = lon_range_sql("longitude", west, east)
        where.append(lon_clause)
        params.extend(lon_params)
        sql = (
            "SELECT id, latitude, longitude, acq_ts, confidence, probability, product_id "
            f"FROM eumetsat_fires WHERE {' AND '.join(where)}"
        )
        return self.conn.execute(sql, params).fetchall()

    def eumetsat_latest_product_end_ts(self) -> float | None:
        """Acquisition-window end of the most recently ingested EUMETSAT
        product, or None if none have been ingested yet - the autofetch job
        uses this as its high-water mark so it only asks the Data Store for
        products newer than what's already cached."""
        row = self.conn.execute("SELECT MAX(end_ts) FROM eumetsat_products").fetchone()
        return float(row[0]) if row and row[0] is not None else None

    # ------------------------------------------------------------------- jobs
    #
    # These are the only job-table writes made from *this* process (the API
    # server). A worker process doing the actual computation is a separate OS
    # process and cannot share this Database object or its locks, so it
    # self-reports progress through the standalone helpers at the bottom of
    # jobs.py instead, which open their own short-lived connection.

    def create_job(self, kind: str, params: dict[str, Any]) -> int:
        """Enqueue a background job (``queued`` status) and return its id.
        ``params`` is stored as JSON so job kinds can carry arbitrary
        argument shapes without schema changes; a worker process picks the
        row up and updates it via the standalone helpers in jobs.py."""
        with self._write():
            cur = self.conn.execute(
                "INSERT INTO jobs (kind, params_json, status, created_at) "
                "VALUES (?, ?, 'queued', ?)",
                (kind, json.dumps(params), int(time.time())),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        """A single job's current row (status/progress/result/error), or
        None if no such job exists. Polled by the API to report progress
        back to the client."""
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def list_jobs(
        self, *, status: str | None = None, kind: str | None = None, limit: int = 100
    ) -> list[sqlite3.Row]:
        """Jobs newest-first, optionally filtered by status and/or kind, for
        the jobs list view."""
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if kind:
            where.append("kind = ?")
            params.append(kind)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(int(limit))
        return self.conn.execute(
            f"SELECT * FROM jobs {clause} ORDER BY id DESC LIMIT ?", params
        ).fetchall()

    def update_job(self, job_id: int, **fields: Any) -> None:
        """Patch arbitrary columns on a job row, e.g.
        ``update_job(id, status="done", progress=100)``. The column names
        themselves are interpolated directly into the SQL (only the values
        are parameterised), which is only safe because every caller is
        trusted code passing hardcoded keyword names, never user input."""
        if not fields:
            return
        set_clause = ", ".join(f"{key} = ?" for key in fields)
        with self._write():
            self.conn.execute(
                f"UPDATE jobs SET {set_clause} WHERE id = ?",
                [*fields.values(), job_id],
            )
            self.conn.commit()

    def purge_old_jobs(self, max_age_seconds: int, *, keep_last: int = 200) -> int:
        """Drop finished jobs older than max_age_seconds, always keeping the
        most recent ``keep_last`` regardless of age (handy history in the UI)."""
        cutoff = int(time.time()) - max_age_seconds
        with self._write():
            cur = self.conn.execute(
                "DELETE FROM jobs WHERE status IN ('done', 'error') "
                "AND finished_at < ? AND id NOT IN ("
                "  SELECT id FROM jobs ORDER BY id DESC LIMIT ?"
                ")",
                (cutoff, keep_last),
            )
            self.conn.commit()
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def day_range(end: date, days: int) -> list[str]:
    """The ``days`` calendar days ending at ``end``, oldest first."""
    return [(end - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]
