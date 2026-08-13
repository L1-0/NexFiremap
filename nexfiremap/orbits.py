"""Satellite swath coverage - "was this area even observed?"

FIRMS only ever tells you where a fire *was* detected. It says nothing about
everywhere else: a quiet region on the map might have had no fire, or might
simply not have been under any satellite that day. This module answers that
second question with orbital mechanics: propagate each sensor's known orbit
(from a public TLE) across a day and mark which cells of the coverage grid
its swath passed over.

This is a **geometric approximation** - a circle of the sensor's nominal
half-swath width swept along the ground track, not the true bow-tie-shaped
scan and not cloud/quality masked. It answers "could this spot have been
seen" not "was it seen clearly". That distinction is spelled out in the API
response and the map UI.

Free data: TLEs from Celestrak (celestrak.org), no account needed.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import time as time_module
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from .cache import cells_for_bbox, clamp_bbox
from .jobs import JobContext, register_kind

log = logging.getLogger("nexfiremap.orbits")

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE"

# The five NRT fire-detection satellites this project pulls from FIRMS.
# Half-swath width is nominal (VIIRS/MODIS both bow-tie near scan edges) -
# treating it as a constant is the geometric approximation noted above.
SATELLITES: dict[str, dict[str, Any]] = {
    "NOAA-20": {"norad_id": 43013, "sensor": "VIIRS", "half_width_km": 1500.0},
    "NOAA-21": {"norad_id": 54234, "sensor": "VIIRS", "half_width_km": 1500.0},
    "SUOMI-NPP": {"norad_id": 37849, "sensor": "VIIRS", "half_width_km": 1500.0},
    "TERRA": {"norad_id": 25994, "sensor": "MODIS", "half_width_km": 1165.0},
    "AQUA": {"norad_id": 27424, "sensor": "MODIS", "half_width_km": 1165.0},
}

TLE_MAX_AGE_SECONDS = 7 * 86400
SAMPLE_STEP_SECONDS = 60
EARTH_RADIUS_KM = 6371.0


# ------------------------------------------------------------ TLE fetching


def parse_tle_text(text: str) -> tuple[str, str] | None:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    for i in range(len(lines) - 1):
        if lines[i].startswith("1 ") and lines[i + 1].startswith("2 "):
            return lines[i], lines[i + 1]
    return None


def fetch_tle_sync(client: httpx.Client, norad_id: int) -> tuple[str, str] | None:
    url = CELESTRAK_URL.format(norad_id=norad_id)
    try:
        response = client.get(url, timeout=20.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("TLE fetch failed for NORAD %d: %s", norad_id, exc)
        return None
    return parse_tle_text(response.text)


async def fetch_tle_async(client: httpx.AsyncClient, norad_id: int) -> tuple[str, str] | None:
    url = CELESTRAK_URL.format(norad_id=norad_id)
    try:
        response = await client.get(url, timeout=20.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("TLE fetch failed for NORAD %d: %s", norad_id, exc)
        return None
    return parse_tle_text(response.text)


def _tle_from_db(conn: sqlite3.Connection, satellite: str, max_age: int) -> tuple[str, str] | None:
    row = conn.execute(
        "SELECT line1, line2, fetched_at FROM tle WHERE satellite = ?", (satellite,)
    ).fetchone()
    if row is None:
        return None
    line1, line2, fetched_at = row
    if time_module.time() - fetched_at > max_age:
        return None
    return line1, line2


def _tle_to_db(conn: sqlite3.Connection, satellite: str, line1: str, line2: str) -> None:
    conn.execute(
        "INSERT INTO tle (satellite, line1, line2, fetched_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (satellite) DO UPDATE SET "
        "line1 = excluded.line1, line2 = excluded.line2, fetched_at = excluded.fetched_at",
        (satellite, line1, line2, int(time_module.time())),
    )
    conn.commit()


def _tle_is_parseable(line1: str, line2: str) -> bool:
    """Best-effort check that skyfield/sgp4 can actually propagate a
    position from this TLE, not just that the lines have the right prefix
    (parse_tle_text's check, which only looks at "1 "/"2 "). Celestrak
    returning a malformed-but-prefix-matching response would otherwise
    overwrite a previously-good cached TLE and poison it for up to
    TLE_MAX_AGE_SECONDS - every ground_track call in that window would then
    raise uncaught, which is exactly what this guards against.

    ``EarthSatellite(...)`` alone doesn't catch this: sgp4 parses whatever
    numeric fields it can find at fixed column positions and silently
    accepts the rest, setting an internal error code and returning NaN
    positions rather than raising - confirmed live against literal garbage
    input. This checks both: a nonzero sgp4 error code, and that an actual
    propagated position comes back finite.
    """
    try:
        from skyfield.api import EarthSatellite, load

        ts = load.timescale(builtin=True)
        sat = EarthSatellite(line1, line2, "validation", ts)
        if getattr(sat.model, "error", 0) != 0:
            return False
        position = sat.at(ts.now()).position.km
        return all(math.isfinite(v) for v in position)
    except Exception:
        return False


def ensure_tle(conn: sqlite3.Connection, satellite: str) -> tuple[str, str] | None:
    """Cached TLE if fresh enough, else fetch from Celestrak and cache it."""
    cached = _tle_from_db(conn, satellite, TLE_MAX_AGE_SECONDS)
    if cached is not None:
        return cached
    norad_id = SATELLITES[satellite]["norad_id"]
    with httpx.Client(headers={"User-Agent": "NexFiremap/1.0 (local orbit propagation)"}) as client:
        fetched = fetch_tle_sync(client, norad_id)
    if fetched is not None and not _tle_is_parseable(*fetched):
        log.warning("Fetched TLE for %s failed validation, discarding it", satellite)
        fetched = None
    if fetched is None:
        # Fall back to a stale TLE rather than nothing - a few days old is
        # still a fine approximation for a coverage indicator. This is the
        # same fallback used whether the fetch itself failed or it returned
        # something that didn't validate.
        stale = conn.execute(
            "SELECT line1, line2 FROM tle WHERE satellite = ?", (satellite,)
        ).fetchone()
        return tuple(stale) if stale else None
    line1, line2 = fetched
    _tle_to_db(conn, satellite, line1, line2)
    return line1, line2


def tle_age_days(conn: sqlite3.Connection, satellite: str) -> float | None:
    """Age of the currently cached TLE for ``satellite``, in days - ``None``
    if nothing is cached at all. A persistently-failing Celestrak fetch
    quietly keeps riding ensure_tle's stale-fallback path with no age
    ceiling - surfacing this (see compute_swath_coverage's job summary)
    means that isn't completely invisible to the API/UI."""
    row = conn.execute("SELECT fetched_at FROM tle WHERE satellite = ?", (satellite,)).fetchone()
    if row is None:
        return None
    return (time_module.time() - row[0]) / 86400.0


# ------------------------------------------------------------- propagation


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def ground_track(
    line1: str, line2: str, name: str, day: date, step_seconds: int = SAMPLE_STEP_SECONDS
) -> list[tuple[int, float, float]]:
    """Subsatellite point every ``step_seconds`` across a UTC day.

    Returns (unix_ts, lat, lon) tuples. Imports skyfield lazily so the rest
    of the app doesn't pay its import cost unless this feature is used.
    """
    from skyfield.api import EarthSatellite, load, wgs84

    ts = load.timescale(builtin=True)
    sat = EarthSatellite(line1, line2, name, ts)

    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    n_steps = 86400 // step_seconds
    times = [start + timedelta(seconds=i * step_seconds) for i in range(n_steps)]

    t = ts.from_datetimes(times)
    subpoint = wgs84.subpoint(sat.at(t))
    lats = subpoint.latitude.degrees
    lons = subpoint.longitude.degrees

    return [
        (int(times[i].timestamp()), float(lats[i]), float(lons[i])) for i in range(n_steps)
    ]


def cells_covered_by_track(
    track: list[tuple[int, float, float]], half_width_km: float, cell_size_deg: float
) -> dict[tuple[int, int], tuple[int, int, int]]:
    """Grid cells within ``half_width_km`` of any ground-track sample.

    Returns {(cell_x, cell_y): (pass_count, first_ts, last_ts)}. A loose
    bounding box around each sample (via the existing coverage-grid helper)
    narrows candidates cheaply - a haversine check then keeps only cells
    genuinely within swath distance, so this approximates a corridor swept
    along the track rather than a blocky bounding square.
    """
    covered: dict[tuple[int, int], tuple[int, int, int]] = {}
    # A cell up to half its own diagonal beyond half_width_km can still have
    # its *center* just outside the true swept width while genuinely being
    # grazed by it - this margin keeps the approximation from having a hard,
    # visually obvious inner edge.
    margin_km = cell_size_deg * 111.32 * 0.5
    threshold = half_width_km + margin_km

    for ts, lat, lon in track:
        lat_pad = half_width_km / 111.32
        lon_pad = half_width_km / max(1e-6, 111.32 * math.cos(math.radians(lat)))
        bbox = clamp_bbox((lon - lon_pad, lat - lat_pad, lon + lon_pad, lat + lat_pad))
        for cx, cy in cells_for_bbox(bbox, cell_size_deg):
            cell_lat = -90.0 + (cy + 0.5) * cell_size_deg
            cell_lon = -180.0 + (cx + 0.5) * cell_size_deg
            if haversine_km(lat, lon, cell_lat, cell_lon) > threshold:
                continue
            prev = covered.get((cx, cy))
            if prev is None:
                covered[(cx, cy)] = (1, ts, ts)
            else:
                count, first_ts, last_ts = prev
                covered[(cx, cy)] = (count + 1, min(first_ts, ts), max(last_ts, ts))
    return covered


# ------------------------------------------------------- tri-state fusion


def aoi_clear_passes(
    conn: sqlite3.Connection,
    bbox: tuple[float, float, float, float],
    detection_times: list[float],
    start_ts: float,
    end_ts: float,
    coincidence_window_s: float = 1800.0,
) -> list[dict[str, Any]]:
    """Satellite overpasses that swept this (small) AOI between ``start_ts``
    and ``end_ts`` without a coincident detection - further_plan.md's
    tri-state observation model (doc section 2): a valid clear pass is
    positive evidence of absence and should suppress nearby likelihood,
    while a cell no satellite ever looked at ("unknown") is left alone.

    Because every tracked sensor's half-swath (1165-1500 km) is far wider
    than one fire event's AOI (typically well under 150 km), whether the
    AOI was in-swath at all is decided from a single point at the AOI's
    centre rather than per grid cell - the along-track sliver where only
    part of a ~150 km AOI would be outside a >1000 km-wide swath is
    negligible. Same geometric-approximation spirit as the Coverage layer
    above, and inherits the same caveat: a current TLE propagated back
    across the event window accumulates error the further back it goes.

    A single overpass typically keeps the AOI in-swath for several minutes
    of 60-second samples - consecutive in-range samples are collapsed into
    one representative pass rather than counted as separate observations,
    which would otherwise badly overcount a single pass.

    Returns a list of ``{"satellite": str, "ts": int}``, one entry per
    clear pass.
    """
    west, south, east, north = bbox
    center_lat = (south + north) / 2.0
    center_lon = (west + east) / 2.0

    start_day = datetime.fromtimestamp(start_ts, tz=timezone.utc).date()
    end_day = datetime.fromtimestamp(end_ts, tz=timezone.utc).date()

    clear: list[dict[str, Any]] = []
    day = start_day
    while day <= end_day:
        for name, meta in SATELLITES.items():
            tle = ensure_tle(conn, name)
            if tle is None:
                continue
            try:
                track = ground_track(tle[0], tle[1], name, day)
            except Exception:
                # ensure_tle validates parseability before caching now, but
                # this stays defensive against any other skyfield/sgp4
                # failure (an extreme date, a genuinely degenerate orbit,
                # ...) - one satellite's propagation failing shouldn't drop
                # the tri-state fusion for every other tracked satellite.
                log.exception("ground_track failed for %s on %s", name, day)
                continue
            half_width_km = meta["half_width_km"]
            in_range_ts = [
                ts
                for ts, lat, lon in track
                if start_ts <= ts <= end_ts
                and haversine_km(lat, lon, center_lat, center_lon) <= half_width_km
            ]
            if not in_range_ts:
                continue
            runs: list[list[int]] = [[in_range_ts[0]]]
            for ts in in_range_ts[1:]:
                if ts - runs[-1][-1] <= SAMPLE_STEP_SECONDS * 2:
                    runs[-1].append(ts)
                else:
                    runs.append([ts])
            for run in runs:
                pass_ts = run[len(run) // 2]
                coincident = any(
                    abs(pass_ts - dt) <= coincidence_window_s for dt in detection_times
                )
                if not coincident:
                    clear.append({"satellite": name, "ts": pass_ts})
        day += timedelta(days=1)
    return clear


# -------------------------------------------------------------- job kind


def compute_swath_coverage(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Job body: propagate every tracked satellite across one UTC day and
    persist which coverage-grid cells its swath touched.

    Runs in a worker process - only plain sqlite3 + skyfield, no shared
    Database object, per the module-level docstring in jobs.py.
    """
    day = date.fromisoformat(params["day"])
    # Mirrors config.py's own NEXFIREMAP_CELL_SIZE_DEG clamp - this job is
    # normally called with the server's configured (already-clamped) cell
    # size, but a raw POST /api/jobs can pass anything, and an unbounded
    # small value here blows up the same way a zero/near-zero configured
    # value used to (a huge or divide-by-zero coverage grid).
    cell_size_deg = max(0.5, min(45.0, float(params.get("cell_size_deg", 10.0))))
    satellites = params.get("satellites") or list(SATELLITES)

    conn = sqlite3.connect(ctx.db_path, timeout=30.0)
    try:
        total = len(satellites)
        summary = {}
        for i, name in enumerate(satellites):
            meta = SATELLITES[name]
            tle = ensure_tle(conn, name)
            if tle is None:
                summary[name] = {"error": "no TLE available"}
                ctx.report_progress(int((i + 1) / total * 100), note=f"{name}: no TLE")
                continue
            age_days = tle_age_days(conn, name)

            try:
                track = ground_track(tle[0], tle[1], name, day)
            except Exception as exc:
                log.exception("ground_track failed for %s on %s", name, day)
                summary[name] = {"error": f"orbit propagation failed: {exc}"}
                ctx.report_progress(int((i + 1) / total * 100), note=f"{name}: propagation failed")
                continue
            # A TLE predicts future position just fine, but "coverage" means
            # data was actually collected - drop samples later than now so a
            # still-in-progress UTC day doesn't claim satellites have already
            # been somewhere they won't reach for several more hours.
            now_ts = int(time_module.time())
            track = [s for s in track if s[0] <= now_ts]
            cells = cells_covered_by_track(track, meta["half_width_km"], cell_size_deg)

            conn.execute("BEGIN")
            now = int(time_module.time())
            conn.executemany(
                "INSERT INTO swath_coverage "
                "(satellite, cell_x, cell_y, day, pass_count, first_ts, last_ts, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (satellite, cell_x, cell_y, day) DO UPDATE SET "
                "pass_count = excluded.pass_count, first_ts = excluded.first_ts, "
                "last_ts = excluded.last_ts, computed_at = excluded.computed_at",
                [
                    (name, x, y, day.isoformat(), count, first_ts, last_ts, now)
                    for (x, y), (count, first_ts, last_ts) in cells.items()
                ],
            )
            conn.commit()
            summary[name] = {
                "cells": len(cells),
                # Surfaces ensure_tle's stale-fallback path (no age ceiling
                # of its own) rather than leaving a persistently-failing
                # Celestrak fetch invisible behind an apparently-normal
                # result - see tle_age_days's docstring.
                "tle_age_days": round(age_days, 1) if age_days is not None else None,
            }
            ctx.report_progress(int((i + 1) / total * 100), note=f"{name}: {len(cells)} cells")

        return {"day": day.isoformat(), "satellites": summary}
    finally:
        conn.close()


register_kind("swath_coverage", compute_swath_coverage)
