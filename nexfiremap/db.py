"""SQLite storage for cached FIRMS detections and fetch coverage.

Two tables carry the cache:

``detections``  one row per fire detection, deduplicated on the natural key
                FIRMS gives us (source + satellite + position + timestamp).
``coverage``    bookkeeping: which (source, grid cell, day) combinations have
                already been pulled, and when. This is what lets the server
                fetch only the gaps instead of re-downloading everything.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from .geo import lon_range_sql

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

# Columns added to `detections` after its original CREATE TABLE - `CREATE
# TABLE IF NOT EXISTS` doesn't retrofit them onto a database file created
# before the column existed, so _init_schema adds any that are missing.
_DETECTION_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("raw_json", "TEXT"),
)

SCHEMA_VERSION = 4


class Database:
    """Thin sqlite3 wrapper with a connection per thread."""

    def __init__(self, path: Path) -> None:
        self.path = path
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
        try:
            self._init_schema()
        except Exception:
            self.close()
            raise

    # ------------------------------------------------------------------ core

    @property
    def conn(self) -> sqlite3.Connection:
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

    def _init_schema(self) -> None:
        with self._write_lock:
            current = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {current} is newer than supported schema {SCHEMA_VERSION}"
                )
            if self._database_existed and current < SCHEMA_VERSION:
                # A byte-consistent online backup makes the pre-migration
                # state recoverable even when WAL mode is active.
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
            self.conn.executescript(SCHEMA)
            existing = {
                row["name"] for row in self.conn.execute("PRAGMA table_info(detections)")
            }
            for column, column_type in _DETECTION_MIGRATIONS:
                if column not in existing:
                    self.conn.execute(
                        f"ALTER TABLE detections ADD COLUMN {column} {column_type}"
                    )
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.conn.commit()

    def close(self) -> None:
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
        with self._write_lock:
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
        if not days:
            return
        now = int(time.time())
        payload = [
            (source, cell_x, cell_y, day, now, row_count, status, note) for day in days
        ]
        with self._write_lock:
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
        with self._write_lock:
            purged_ids = [
                row[0]
                for row in self.conn.execute(
                    "SELECT id FROM detections WHERE acq_date < ?", (cutoff_day,)
                ).fetchall()
            ]
            affected_events: list[int] = []
            if purged_ids:
                placeholders = ", ".join("?" for _ in purged_ids)
                affected_events = [
                    row[0]
                    for row in self.conn.execute(
                        f"SELECT DISTINCT event_id FROM event_members "
                        f"WHERE detection_id IN ({placeholders})",
                        purged_ids,
                    ).fetchall()
                ]
                self.conn.execute(
                    f"DELETE FROM event_members WHERE detection_id IN ({placeholders})",
                    purged_ids,
                )

            det = self.conn.execute(
                "DELETE FROM detections WHERE acq_date < ?", (cutoff_day,)
            ).rowcount
            cov = self.conn.execute(
                "DELETE FROM coverage WHERE day < ?", (cutoff_day,)
            ).rowcount

            if affected_events:
                placeholders = ", ".join("?" for _ in affected_events)
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
                    """,
                    affected_events,
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
                    """,
                    affected_events,
                )

            self.conn.commit()
        return max(det, 0), max(cov, 0)

    def vacuum(self) -> None:
        with self._write_lock:
            self.conn.execute("VACUUM")

    def stats(self) -> dict[str, Any]:
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
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self.conn.commit()

    # -------------------------------------------------------------------- tle

    def get_tle(self, satellite: str, max_age_seconds: int) -> sqlite3.Row | None:
        row = self.conn.execute(
            "SELECT * FROM tle WHERE satellite = ?", (satellite,)
        ).fetchone()
        if row is None:
            return None
        if time.time() - row["fetched_at"] > max_age_seconds:
            return None
        return row

    def set_tle(self, satellite: str, line1: str, line2: str) -> None:
        with self._write_lock:
            self.conn.execute(
                "INSERT INTO tle (satellite, line1, line2, fetched_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (satellite) DO UPDATE SET "
                "line1 = excluded.line1, line2 = excluded.line2, fetched_at = excluded.fetched_at",
                (satellite, line1, line2, int(time.time())),
            )
            self.conn.commit()

    # ---------------------------------------------------------- swath coverage

    def swath_computed(self, satellite: str, day: str) -> bool:
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
        """``cells`` maps (cell_x, cell_y) -> (pass_count, first_ts, last_ts)."""
        if not cells:
            return
        now = int(time.time())
        payload = [
            (satellite, x, y, day, count, first_ts, last_ts, now)
            for (x, y), (count, first_ts, last_ts) in cells.items()
        ]
        with self._write_lock:
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
        return self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()

    def event_detections(self, event_id: int) -> list[sqlite3.Row]:
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
        with self._write_lock:
            cur = self.conn.execute(
                "INSERT INTO jobs (kind, params_json, status, created_at) "
                "VALUES (?, ?, 'queued', ?)",
                (kind, json.dumps(params), int(time.time())),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def list_jobs(
        self, *, status: str | None = None, kind: str | None = None, limit: int = 100
    ) -> list[sqlite3.Row]:
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
        if not fields:
            return
        set_clause = ", ".join(f"{key} = ?" for key in fields)
        with self._write_lock:
            self.conn.execute(
                f"UPDATE jobs SET {set_clause} WHERE id = ?",
                [*fields.values(), job_id],
            )
            self.conn.commit()

    def purge_old_jobs(self, max_age_seconds: int, *, keep_last: int = 200) -> int:
        """Drop finished jobs older than max_age_seconds, always keeping the
        most recent ``keep_last`` regardless of age (handy history in the UI)."""
        cutoff = int(time.time()) - max_age_seconds
        with self._write_lock:
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
