"""Smoke tests for the orbital swath-coverage module (Phase 1).

Fully offline: skyfield propagation uses its builtin timescale (no network),
and TLE fetching is bypassed by pre-seeding the DB cache with a real but
fixed TLE - the same path the job takes when its cache is already warm.

Run with:  python tests/test_orbits.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.cache import cells_for_bbox
from nexfiremap.db import Database
from nexfiremap.jobs import JobContext
import nexfiremap.orbits as orbits_module
from nexfiremap.orbits import (
    SATELLITES,
    aoi_clear_passes,
    cells_covered_by_track,
    compute_swath_coverage,
    ensure_tle,
    ground_track,
    haversine_km,
    parse_tle_text,
    tle_age_days,
)
from nexfiremap.orbits import _tle_is_parseable

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


# A real NOAA-20 TLE, fixed in time - good enough for structural tests of
# propagation, not meant to represent the satellite's current position.
NOAA20_L1 = "1 43013U 17073A   26218.25797549  .00000011  00000+0  26270-4 0  9998"
NOAA20_L2 = "2 43013  98.7778 157.2781 0001936  98.9015 261.2381 14.19519984451577"


def test_parse_tle() -> None:
    print("\nTLE text parsing")
    text = f"NOAA 20 (JPSS-1)        \n{NOAA20_L1}\n{NOAA20_L2}\n"
    parsed = parse_tle_text(text)
    check("parses name+2 lines", parsed == (NOAA20_L1, NOAA20_L2), str(parsed))
    check("garbage returns None", parse_tle_text("not a tle") is None)
    check("empty returns None", parse_tle_text("") is None)


def test_haversine() -> None:
    print("\nHaversine distance")
    check("same point is zero", haversine_km(10, 20, 10, 20) == 0.0)
    # 1 degree of latitude is ~111.2 km.
    d = haversine_km(0, 0, 1, 0)
    check("1 deg latitude ~111km", 110 < d < 112, str(d))
    # Equator to pole is ~10000 km (quarter of Earth's circumference).
    d2 = haversine_km(0, 0, 90, 0)
    check("equator to pole ~10000km", 9900 < d2 < 10100, str(d2))


def test_ground_track() -> None:
    print("\nGround track propagation (offline, builtin timescale)")
    track = ground_track(NOAA20_L1, NOAA20_L2, "NOAA 20", date(2026, 8, 6), step_seconds=300)
    check("full day of samples", len(track) == 86400 // 300, str(len(track)))

    lats = [lat for _, lat, _ in track]
    lons = [lon for _, _, lon in track]
    check("latitude within polar-orbit range", max(abs(l) for l in lats) < 90.1)
    check("longitude in valid range", all(-180 <= lon <= 180 for lon in lons))
    check("latitude actually varies (not stuck)", max(lats) - min(lats) > 100, str(max(lats) - min(lats)))

    ts_values = [t for t, _, _ in track]
    check("timestamps strictly increasing", ts_values == sorted(ts_values))
    check("timestamps span ~1 day", ts_values[-1] - ts_values[0] > 86000, str(ts_values[-1] - ts_values[0]))


def test_cells_covered_by_track() -> None:
    print("\nCell coverage from a synthetic track")
    # Three samples marching east along the equator.
    track = [(1000, 0.0, 0.0), (1060, 0.0, 5.0), (1120, 0.0, 10.0)]
    covered = cells_covered_by_track(track, half_width_km=600.0, cell_size_deg=10.0)

    check("covers cells near the track", len(covered) > 0, str(covered))
    # The cell straddling (0,0) should definitely be covered.
    cell_at_origin = cells_for_bbox((-1, -1, 1, 1), 10.0)[0]
    check("origin cell covered", cell_at_origin in covered, f"{cell_at_origin} not in {list(covered)}")

    # A cell far from the whole track (other side of the planet) shouldn't be.
    far_cell = cells_for_bbox((170, -85, 179, -80), 10.0)[0]
    check("distant cell not covered", far_cell not in covered)

    for (_x, _y), (count, first_ts, last_ts) in covered.items():
        check("pass_count positive", count >= 1, str(count))
        check("first_ts <= last_ts", first_ts <= last_ts, f"{first_ts} {last_ts}")
        break


def test_ensure_tle_uses_cache(tmp: Path) -> None:
    print("\nensure_tle prefers a fresh cached TLE over the network")
    db_path = tmp / "cache.sqlite3"
    db = Database(db_path)
    db.set_tle("NOAA-20", NOAA20_L1, NOAA20_L2)
    db.close()

    conn = sqlite3.connect(db_path)
    tle = ensure_tle(conn, "NOAA-20")
    conn.close()
    check("returns cached TLE unchanged", tle == (NOAA20_L1, NOAA20_L2), str(tle))


def test_tle_validation() -> None:
    print("\n_tle_is_parseable rejects malformed-but-prefix-matching lines")
    check("a real TLE validates", _tle_is_parseable(NOAA20_L1, NOAA20_L2))
    # Same "1 "/"2 " prefixes parse_tle_text checks for, but garbage after
    # that - the exact case that used to slip past validation entirely.
    check(
        "prefix-matching garbage is rejected",
        not _tle_is_parseable("1 not actually a tle", "2 also not one"),
    )
    check(
        "empty-ish lines are rejected",
        not _tle_is_parseable("1 ", "2 "),
    )


def test_ensure_tle_rejects_malformed_fetch(tmp: Path) -> None:
    print("\nensure_tle discards a fetched TLE that fails validation")
    db_path = tmp / "cache_bad_tle.sqlite3"
    db = Database(db_path)
    db.set_tle("NOAA-20", NOAA20_L1, NOAA20_L2)
    db.close()

    conn = sqlite3.connect(db_path)
    # Force ensure_tle past its "cached is fresh enough" fast path so it
    # actually attempts a fetch, then make that fetch return something
    # structurally TLE-shaped but not really parseable - the live failure
    # mode this guards against (a malformed Celestrak response that still
    # starts with "1 "/"2 ").
    original_from_db = orbits_module._tle_from_db
    original_fetch = orbits_module.fetch_tle_sync
    orbits_module._tle_from_db = lambda *a, **kw: None
    orbits_module.fetch_tle_sync = lambda *a, **kw: ("1 garbage", "2 garbage")
    try:
        tle = ensure_tle(conn, "NOAA-20")
    finally:
        orbits_module._tle_from_db = original_from_db
        orbits_module.fetch_tle_sync = original_fetch

    check(
        "falls back to the previously-cached good TLE, not the garbage",
        tle == (NOAA20_L1, NOAA20_L2),
        str(tle),
    )
    stored = conn.execute("SELECT line1, line2 FROM tle WHERE satellite = ?", ("NOAA-20",)).fetchone()
    check(
        "the good cached TLE was never overwritten",
        tuple(stored) == (NOAA20_L1, NOAA20_L2),
        str(stored),
    )
    age = tle_age_days(conn, "NOAA-20")
    check("tle_age_days reports a small non-negative age", age is not None and age >= 0, str(age))
    conn.close()


def test_compute_swath_coverage_job(tmp: Path) -> None:
    print("\ncompute_swath_coverage job body (offline via pre-seeded TLE)")
    db_path = tmp / "cache2.sqlite3"
    db = Database(db_path)
    # Pre-seed every satellite so the job never touches the network.
    for name in SATELLITES:
        db.set_tle(name, NOAA20_L1, NOAA20_L2)
    job_id = db.create_job("swath_coverage", {})
    result_dir = tmp / "job1"
    result_dir.mkdir()
    db.close()

    ctx = JobContext(job_id=job_id, db_path=str(db_path), result_dir=str(result_dir))
    day = date.today() - timedelta(days=2)
    result = compute_swath_coverage(
        {"day": day.isoformat(), "satellites": ["NOAA-20", "TERRA"], "cell_size_deg": 10.0}, ctx
    )

    check("result names both satellites", set(result["satellites"]) == {"NOAA-20", "TERRA"}, str(result))
    check(
        "both reported nonzero cells",
        all("cells" in v for v in result["satellites"].values()),
        str(result),
    )

    db2 = Database(db_path)
    rows = db2.swath_cells_for_bbox((-20, -20, 20, 20), day.isoformat(), 10.0)
    check("coverage rows queryable near equator", len(rows) > 0, str(len(rows)))
    check("job progress reached 100", db2.get_job(job_id)["progress"] == 100)
    # A day that's already fully in the past: every recorded pass timestamp
    # must be <= now, never a "future" pass.
    now = int(time.time())
    check(
        "no pass timestamps are in the future",
        all(row["last_ts"] <= now for row in rows),
        str([row["last_ts"] for row in rows if row["last_ts"] > now]),
    )
    db2.close()


def test_compute_swath_coverage_clips_future_samples(tmp: Path) -> None:
    print("\nToday's coverage never claims a pass that hasn't happened yet")
    db_path = tmp / "cache3.sqlite3"
    db = Database(db_path)
    db.set_tle("NOAA-20", NOAA20_L1, NOAA20_L2)
    job_id = db.create_job("swath_coverage", {})
    result_dir = tmp / "job2"
    result_dir.mkdir()
    db.close()

    ctx = JobContext(job_id=job_id, db_path=str(db_path), result_dir=str(result_dir))
    # Coverage days are UTC. Using the host's local calendar date around
    # midnight can select a UTC day that has not started yet, for which zero
    # rows is the correct result rather than a failed coverage computation.
    today = datetime.now(timezone.utc).date()
    compute_swath_coverage({"day": today.isoformat(), "satellites": ["NOAA-20"]}, ctx)

    db2 = Database(db_path)
    rows = db2.swath_cells_for_bbox((-180, -90, 180, 90), today.isoformat(), 10.0)
    now = int(time.time())
    check("today's rows exist", len(rows) > 0, str(len(rows)))
    check(
        "no row claims a pass later than right now",
        all(row["last_ts"] <= now for row in rows),
        str(max((row["last_ts"] for row in rows), default=0) - now),
    )
    db2.close()


def test_aoi_clear_passes(tmp: Path) -> None:
    print("\naoi_clear_passes: tri-state fusion input (offline via pre-seeded TLE)")
    db_path = tmp / "cache4.sqlite3"
    db = Database(db_path)
    # Pre-seed every tracked satellite with the same fixed TLE - fine for a
    # structural test of the fusion logic, not meant to represent five
    # distinct real orbits (same caveat as test_compute_swath_coverage_job).
    for name in SATELLITES:
        db.set_tle(name, NOAA20_L1, NOAA20_L2)
    db.close()

    conn = sqlite3.connect(db_path)
    day = date(2026, 8, 6)
    start_ts = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
    end_ts = start_ts + 86400
    bbox = (10.0, 40.0, 10.02, 40.02)

    clear_no_det = aoi_clear_passes(conn, bbox, [], start_ts, end_ts)
    check("passes found over a full day with no detections", len(clear_no_det) > 0, str(len(clear_no_det)))

    satellites_seen = {p["satellite"] for p in clear_no_det}
    check("multiple satellites contribute passes", len(satellites_seen) > 1, str(satellites_seen))

    # A run of consecutive in-swath samples must collapse to one pass, not
    # one entry per 60-second sample.
    same_sat_same_minute = {}
    for p in clear_no_det:
        same_sat_same_minute.setdefault(p["satellite"], []).append(p["ts"])
    for sat, times_ in same_sat_same_minute.items():
        times_.sort()
        gaps = [b - a for a, b in zip(times_, times_[1:])]
        check(
            f"{sat}: consecutive passes are collapsed, not one-per-sample",
            all(g > 120 for g in gaps),
            str(gaps),
        )

    # Claiming a "detection" at every discovered pass's own timestamp
    # should make every one of them coincident, leaving zero clear passes.
    coincident_times = [p["ts"] for p in clear_no_det]
    clear_with_det = aoi_clear_passes(conn, bbox, coincident_times, start_ts, end_ts)
    check("coincident detections remove those passes", len(clear_with_det) == 0, str(len(clear_with_det)))

    # A detection far away in time from every real pass should suppress
    # nothing.
    clear_with_unrelated_det = aoi_clear_passes(conn, bbox, [start_ts + 999999], start_ts, end_ts)
    check(
        "an unrelated detection time doesn't remove real passes",
        len(clear_with_unrelated_det) == len(clear_no_det),
        f"{len(clear_with_unrelated_det)} vs {len(clear_no_det)}",
    )

    conn.close()


def main() -> int:
    test_parse_tle()
    test_haversine()
    test_ground_track()
    test_cells_covered_by_track()
    test_tle_validation()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        test_ensure_tle_uses_cache(tmp)
        test_ensure_tle_rejects_malformed_fetch(tmp)
        test_compute_swath_coverage_job(tmp)
        test_compute_swath_coverage_clips_future_samples(tmp)
        test_aoi_clear_passes(tmp)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
