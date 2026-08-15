"""Smoke tests for the likelihood/arrival-time/envelope module (Phase 2).

Run with:  python tests/test_likelihood.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import nexfiremap.likelihood as likelihood_module
from nexfiremap.db import Database
from nexfiremap.jobs import JobContext
from nexfiremap.likelihood import (
    CLEAR_PASS_MIN_FACTOR,
    CLEAR_PASS_SUPPRESSION_WEIGHT,
    DEFAULT_TAU_HOURS,
    _clear_pass_suppression,
    active_heat_raster,
    analyze_event,
    arrival_time_estimate,
    grid_geometry,
    probability_envelopes,
    render_probability_png,
    render_recency_png,
)
from nexfiremap.geo import ROW_ORIGIN_SOUTH

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


def test_grid_geometry() -> None:
    print("\nGrid geometry sizing")
    small = grid_geometry((10.0, 40.0, 10.05, 40.05), desired_res_m=100.0, max_dim=260)
    check("small AOI stays near desired resolution", 90 <= small["res_m"] <= 110, str(small["res_m"]))
    check("small AOI grid is modest", small["nx"] < 100 and small["ny"] < 100, str(small))

    huge = grid_geometry((0.0, 0.0, 10.0, 10.0), desired_res_m=100.0, max_dim=260)
    check("huge AOI is capped at max_dim", huge["nx"] <= 260 and huge["ny"] <= 260, str(huge))
    check("huge AOI resolution coarsened", huge["res_m"] > 100.0, str(huge["res_m"]))


def test_active_heat_raster() -> None:
    print("\nActive-heat raster")
    bbox = (10.0, 40.0, 10.02, 40.02)
    geom = grid_geometry(bbox, desired_res_m=50.0, max_dim=100)
    now = time.time()

    empty = active_heat_raster([], geom, now)
    check("no detections -> all zero", float(empty.max()) == 0.0, str(empty.max()))

    det = [
        {
            "lat": 40.01,
            "lon": 10.01,
            "ts": now,
            "instrument": "VIIRS",
            "confidence": "high",
            "frp": 20.0,
            "scan": 0.4,
            "track": 0.4,
        }
    ]
    raster = active_heat_raster(det, geom, now)
    check("peak probability is high near a fresh high-confidence detection", raster.max() > 0.6, str(raster.max()))
    check("probability stays within [0,1]", 0.0 <= raster.min() and raster.max() <= 1.0)

    # Peak should be near the detection's grid cell, not at a corner.
    peak_row, peak_col = np.unravel_index(np.argmax(raster), raster.shape)
    ny, nx = geom["ny"], geom["nx"]
    check(
        "peak located near the detection, not at an edge",
        0.2 * nx < peak_col < 0.8 * nx and 0.2 * ny < peak_row < 0.8 * ny,
        f"peak at ({peak_row},{peak_col}) of {ny}x{nx}",
    )

    old_det = [{**det[0], "ts": now - 48 * 3600}]  # 48h old, tau defaults to 6h
    old_raster = active_heat_raster(old_det, geom, now)
    check(
        "an old detection has decayed to a much lower probability",
        old_raster.max() < raster.max() * 0.1,
        f"{old_raster.max()} vs {raster.max()}",
    )


def test_arrival_time_estimate() -> None:
    print("\nArrival-time IDW estimate")
    bbox = (10.0, 40.0, 10.02, 40.02)
    geom = grid_geometry(bbox, desired_res_m=50.0, max_dim=100)
    now = time.time()

    empty = arrival_time_estimate([], geom)
    check("no detections -> all NaN", bool(np.all(np.isnan(empty["median"]))))

    single = [{"lat": 40.01, "lon": 10.01, "ts": now, "scan": 0.4, "track": 0.4}]
    result = arrival_time_estimate(single, geom)
    check(
        "single detection: median equals its own timestamp everywhere",
        np.allclose(result["median"], now),
        str(result["median"].flatten()[:3]),
    )
    check(
        "single detection: zero spread (can't estimate uncertainty from one point)",
        np.allclose(result["earliest"], result["latest"]),
    )

    two = [
        {"lat": 40.005, "lon": 10.005, "ts": now - 3600, "scan": 0.4, "track": 0.4},
        {"lat": 40.015, "lon": 10.015, "ts": now, "scan": 0.4, "track": 0.4},
    ]
    result2 = arrival_time_estimate(two, geom)
    check("two detections: some spread exists", float(np.nanmax(result2["latest"] - result2["earliest"])) > 0)
    check(
        "median stays within the two detections' time range",
        bool(np.all((result2["median"] >= now - 3601) & (result2["median"] <= now + 1))),
    )


def test_probability_envelopes() -> None:
    print("\nProbability-mass envelopes")
    bbox = (10.0, 40.0, 10.02, 40.02)
    geom = grid_geometry(bbox, desired_res_m=50.0, max_dim=100)

    zero_raster = np.zeros((geom["ny"], geom["nx"]))
    check("all-zero raster -> no envelopes", probability_envelopes(zero_raster, geom) == [])

    now = time.time()
    det = [{"lat": 40.01, "lon": 10.01, "ts": now, "instrument": "VIIRS", "confidence": "high", "frp": 20.0, "scan": 0.4, "track": 0.4}]
    raster = active_heat_raster(det, geom, now)
    envelopes = probability_envelopes(raster, geom, fractions=(0.5, 0.9))
    check("envelopes produced for a real raster", len(envelopes) > 0, str(len(envelopes)))
    for feat in envelopes:
        check(
            "envelope is a closed polygon",
            feat["geometry"]["coordinates"][0][0] == feat["geometry"]["coordinates"][0][-1],
        )
        break

    by_frac = {f["properties"]["probability_mass"]: f for f in envelopes}
    check("50% and 90% envelopes both present", 0.5 in by_frac and 0.9 in by_frac, str(list(by_frac)))
    check("area_km2 is a positive number", by_frac[0.5]["properties"]["area_km2"] > 0, str(by_frac[0.5]))
    check(
        "area_mi2 is the km2->mi2 conversion",
        # both values are independently rounded to 4dp server-side, so allow
        # for that rounding rather than expecting exact floating-point equality
        abs(by_frac[0.5]["properties"]["area_mi2"] - by_frac[0.5]["properties"]["area_km2"] * 0.386102) < 1e-3,
    )
    check(
        "90% mass covers at least as much area as 50% mass",
        by_frac[0.9]["properties"]["area_km2"] >= by_frac[0.5]["properties"]["area_km2"],
        str((by_frac[0.9]["properties"]["area_km2"], by_frac[0.5]["properties"]["area_km2"])),
    )

    test_probability_envelopes_area_counts_ties(geom)


def test_probability_envelopes_area_counts_ties(geom: dict) -> None:
    # A real raster rarely lines up ties exactly at a float64 level, so this
    # constructs one deliberately: a 36-cell plateau at 1.0 (with a small
    # 2.0 peak above it, so the 1.0 level isn't the raster's max - a level
    # exactly at the max finds no contour at all) - the 50% cutoff lands
    # inside the tied plateau, so the old `idx + 1` area undercounted by
    # every tied cell beyond the first one to sort ahead of it.
    ny, nx = geom["ny"], geom["nx"]
    check("grid is large enough for this fixture", ny >= 12 and nx >= 9, f"{ny}x{nx}")
    raster = np.zeros((ny, nx))
    raster[4:10, 3:9] = 1.0  # 6x6 = 36 tied cells
    raster[6:8, 5:7] = 2.0  # 2x2 = 4 cells strictly above the plateau

    flat_sorted = np.sort(raster.flatten())[::-1]
    cumulative = np.cumsum(flat_sorted) / flat_sorted.sum()
    idx = int(np.searchsorted(cumulative, 0.5))
    level = flat_sorted[idx]
    check("the 50% level lands inside the tied plateau", level == 1.0, level)
    tied_count = int(np.count_nonzero(flat_sorted >= level))
    check("the plateau really is tied across more cells than idx+1", tied_count > idx + 1, str((tied_count, idx + 1)))

    envelopes = probability_envelopes(raster, geom, fractions=(0.5,))
    check("a contour was found at the tied level", len(envelopes) == 1, str(len(envelopes)))
    cell_area_km2 = (geom["width_m"] / nx) * (geom["height_m"] / ny) / 1e6
    expected_area = round(tied_count * cell_area_km2, 4)
    check(
        "area_km2 counts every tied cell, not just idx+1",
        envelopes[0]["properties"]["area_km2"] == expected_area,
        f"reported {envelopes[0]['properties']['area_km2']} vs expected {expected_area}",
    )


def test_png_rendering() -> None:
    print("\nPNG rendering")
    # This module's grids are south-first; `origin` is mandatory so no call
    # site can inherit the wrong convention silently. See test_orientation.py
    # for the checks that the flip actually happens.
    raster = np.array([[0.0, 0.5], [0.9, 0.1]])
    png_bytes = render_probability_png(raster, origin=ROW_ORIGIN_SOUTH)
    check("PNG signature present", png_bytes[:8] == b"\x89PNG\r\n\x1a\n")

    hours_ago = np.array([[0.0, 24.0], [np.nan, 100.0]])
    png2 = render_recency_png(hours_ago, origin=ROW_ORIGIN_SOUTH)
    check("recency PNG signature present", png2[:8] == b"\x89PNG\r\n\x1a\n")


def test_analyze_event_job() -> None:
    print("\nanalyze_event job body: end-to-end")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        db_path = tmp / "cache.sqlite3"
        db = Database(db_path)

        now = int(time.time())

        def det(lat, lon, ts_offset):
            ts = now + ts_offset
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return {
                "source": "VIIRS_NOAA20_NRT", "satellite": "N", "instrument": "VIIRS",
                "latitude": lat, "longitude": lon,
                "acq_date": dt.strftime("%Y-%m-%d"), "acq_time": dt.strftime("%H%M"), "acq_ts": ts,
                "brightness": 330.0, "brightness2": 295.0, "scan": 0.4, "track": 0.4,
                "confidence_raw": "n", "confidence_pct": None, "confidence_level": "nominal",
                "frp": 12.0, "daynight": "D", "version": "2.0NRT",
            }

        rows = [det(38.0, -5.0, -7200), det(38.002, -5.002, -3600), det(38.004, -5.004, 0)]
        db.upsert_detections(rows)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO events (bbox_west, bbox_south, bbox_east, bbox_north, centroid_lat, "
            "centroid_lon, first_seen, last_seen, detection_count, sources_json, params_json, created_at) "
            "VALUES (-5.004, 38.0, -5.0, 38.004, 38.002, -5.002, ?, ?, 3, '[]', '{}', ?)",
            (now - 7200, now, now),
        )
        event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        det_ids = [r[0] for r in conn.execute("SELECT id FROM detections ORDER BY id").fetchall()]
        conn.executemany(
            "INSERT INTO event_members (event_id, detection_id) VALUES (?, ?)",
            [(event_id, did) for did in det_ids],
        )
        conn.commit()
        conn.close()

        job_id = db.create_job("analyze_event", {})
        result_dir = tmp / "job1"
        result_dir.mkdir()
        db.close()

        ctx = JobContext(job_id=job_id, db_path=str(db_path), result_dir=str(result_dir))
        result = analyze_event({"event_id": event_id, "reference_ts": now}, ctx)

        check("event_id echoed back", result["event_id"] == event_id)
        check("detection_count matches", result["detection_count"] == 3, str(result))
        check("max_probability is meaningfully high", result["max_probability"] > 0.5, str(result))
        check("bounds is [[south,west],[north,east]]", len(result["bounds"]) == 2 and len(result["bounds"][0]) == 2)

        for fname in result["files"].values():
            path = result_dir / fname
            check(f"{fname} written to disk", path.is_file(), str(path))

        png_data = (result_dir / "active_heat.png").read_bytes()
        check("active_heat.png is a valid PNG", png_data[:8] == b"\x89PNG\r\n\x1a\n")

        envelopes = json.loads((result_dir / "envelopes.geojson").read_text())
        check("envelopes.geojson is a FeatureCollection", envelopes["type"] == "FeatureCollection")
        # analyze_event doesn't update the jobs row itself - that's
        # JobManager's job in the real pipeline (see test_jobs.py) - here we
        # only need the function to complete and return a well-formed result.


def test_analyze_event_clear_pass_suppression() -> None:
    print("\nanalyze_event: tri-state clear-pass suppression (offline via pre-seeded TLE)")
    from nexfiremap.orbits import SATELLITES

    # Same fixed real NOAA-20 TLE used in test_orbits.py, pre-seeded for
    # every tracked satellite so orbit propagation never touches the
    # network here.
    tle_l1 = "1 43013U 17073A   26218.25797549  .00000011  00000+0  26270-4 0  9998"
    tle_l2 = "2 43013  98.7778 157.2781 0001936  98.9015 261.2381 14.19519984451577"

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        db_path = tmp / "cache.sqlite3"
        db = Database(db_path)
        for name in SATELLITES:
            db.set_tle(name, tle_l1, tle_l2)

        day = datetime(2026, 8, 6, tzinfo=timezone.utc)
        day_start = int(day.timestamp())
        # A real pass over this AOI on this day (discovered empirically -
        # see the module docstring convention in test_orbits.py): placing
        # the detection right on it means that specific pass is coincident
        # and excluded, while later passes (also real, ~11-13h on) remain
        # as clear evidence.
        det_ts = day_start + 6360
        reference_ts = day_start + 47340

        det_row = {
            "source": "VIIRS_NOAA20_NRT", "satellite": "N", "instrument": "VIIRS",
            "latitude": 40.01, "longitude": 10.01,
            "acq_date": "2026-08-06", "acq_time": "0146", "acq_ts": det_ts,
            "brightness": 330.0, "brightness2": 295.0, "scan": 0.4, "track": 0.4,
            "confidence_raw": "n", "confidence_pct": None, "confidence_level": "high",
            "frp": 20.0, "daynight": "N", "version": "2.0NRT",
        }
        db.upsert_detections([det_row])

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO events (bbox_west, bbox_south, bbox_east, bbox_north, centroid_lat, "
            "centroid_lon, first_seen, last_seen, detection_count, sources_json, params_json, created_at) "
            "VALUES (10.0, 40.0, 10.02, 40.02, 40.01, 10.01, ?, ?, 1, '[]', '{}', ?)",
            (det_ts, det_ts, det_ts),
        )
        event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        det_id = conn.execute("SELECT id FROM detections").fetchone()[0]
        conn.execute("INSERT INTO event_members (event_id, detection_id) VALUES (?, ?)", (event_id, det_id))
        conn.commit()
        conn.close()

        job_id = db.create_job("analyze_event", {})
        result_dir = tmp / "job-suppression"
        result_dir.mkdir()
        db.close()

        ctx = JobContext(job_id=job_id, db_path=str(db_path), result_dir=str(result_dir))
        # This test is offline-by-design (see its print banner) - stub out
        # the cloud-cover lookup (a real Open-Meteo call otherwise) so it
        # stays that way; {} means "no data available", which is exactly
        # the old (pre-cloud-softening) behaviour, so the assertions below
        # keep meaning what they say.
        original_cloud = likelihood_module._fetch_cloud_cover
        likelihood_module._fetch_cloud_cover = lambda *a, **kw: {}
        try:
            result = analyze_event({"event_id": event_id, "reference_ts": reference_ts}, ctx)
        finally:
            likelihood_module._fetch_cloud_cover = original_cloud

        check("clear_pass_count key present", "clear_pass_count" in result, str(result))
        check(
            "clear passes found (own detection's pass excluded, later real passes counted)",
            result["clear_pass_count"] == 10,
            str(result["clear_pass_count"]),
        )

        # Prove suppression actually lowered the raster rather than being
        # wired in but inert: recompute the same raster with no
        # suppression applied and confirm the served result is lower.
        bbox = (10.0 - 0.05, 40.0 - 0.05, 10.02 + 0.05, 40.02 + 0.05)
        geom = grid_geometry(bbox, desired_res_m=100.0)
        unsuppressed = active_heat_raster(
            [{"lat": 40.01, "lon": 10.01, "ts": det_ts, "instrument": "VIIRS",
              "confidence": "high", "frp": 20.0, "scan": 0.4, "track": 0.4}],
            geom, reference_ts,
        )
        check(
            "suppressed peak is meaningfully lower than the unsuppressed peak",
            result["max_probability"] < float(unsuppressed.max()) * 0.9,
            f"{result['max_probability']} vs {float(unsuppressed.max())}",
        )


def test_clear_pass_suppression_does_not_compound_over_many_passes() -> None:
    print("\n_clear_pass_suppression: many stale passes don't compound toward the floor")
    # Reproduces a real bug found live on an active, 908-detection, 29-day-
    # spanning fire event (Lecco / Lago di Como): the *previous*
    # implementation multiplied (1 - weight*decay) across every clear pass
    # found, so 163 accumulated passes drove the reported peak confidence
    # down to ~1% via runaway multiplicative compounding, even though the
    # freshest detection was only ~11h old. Monkeypatches
    # nexfiremap.orbits.aoi_clear_passes (real orbit propagation isn't the
    # point of this test) with a controlled, large set of passes at known
    # ages to check the combination rule itself.
    import nexfiremap.orbits as orbits

    reference_ts = 1_000_000.0
    tau_hours = DEFAULT_TAU_HOURS

    def fake_passes(_conn, _bbox, _det_times, _start_ts, _end_ts, _coincidence_s=None):
        # 163 passes, ages 1h .. 163h before reference_ts - deliberately a
        # wide, realistic spread rather than all-fresh or all-stale.
        return [{"ts": reference_ts - age_h * 3600.0} for age_h in range(1, 164)]

    # {} = "no cloud data available", i.e. the old (pre-cloud-softening)
    # behaviour - this test is specifically about the pass-combination
    # rule, not the cloud clarity refinement (see
    # test_clear_pass_cloud_cover_softens_suppression for that). Patched
    # for the whole function, not just one call, so it can't accidentally
    # fall through to a real Open-Meteo request on the second call below.
    original_cloud = likelihood_module._fetch_cloud_cover
    likelihood_module._fetch_cloud_cover = lambda *a, **kw: {}
    original = orbits.aoi_clear_passes
    try:
        orbits.aoi_clear_passes = fake_passes
        factor, count = _clear_pass_suppression(
            conn=None, bbox=(0, 0, 1, 1), detections=[{"ts": reference_ts}],
            reference_ts=reference_ts, tau_hours=tau_hours,
        )

        check("all 163 synthetic passes counted", count == 163, str(count))
        # The freshest pass (age 1h) alone determines the suppression
        # strength - its own weight*decay, not a product accumulated
        # across all 163.
        strongest_only = 1.0 - CLEAR_PASS_SUPPRESSION_WEIGHT * np.exp(-1.0 / tau_hours)
        check(
            "factor matches the single strongest pass, not a product across all passes",
            abs(factor - max(CLEAR_PASS_MIN_FACTOR, strongest_only)) < 1e-9,
            f"{factor} vs {max(CLEAR_PASS_MIN_FACTOR, strongest_only)}",
        )
        check(
            "factor did NOT collapse to the floor just from having many passes",
            factor > CLEAR_PASS_MIN_FACTOR + 0.01,
            str(factor),
        )

        # Adding a pile of additional, much staler passes must not move the
        # factor at all - they're dominated by the same freshest pass either way.
        def fake_passes_plus_stale(_conn, _bbox, _det_times, _start_ts, _end_ts, _coincidence_s=None):
            return fake_passes(None, None, None, None, None) + [
                {"ts": reference_ts - age_h * 3600.0} for age_h in range(200, 400)
            ]

        orbits.aoi_clear_passes = fake_passes_plus_stale
        factor_plus_stale, count_plus_stale = _clear_pass_suppression(
            conn=None, bbox=(0, 0, 1, 1), detections=[{"ts": reference_ts}],
            reference_ts=reference_ts, tau_hours=tau_hours,
        )
    finally:
        orbits.aoi_clear_passes = original
        likelihood_module._fetch_cloud_cover = original_cloud
    check("stale-pass count grew", count_plus_stale == 363, str(count_plus_stale))
    check(
        "factor is unchanged by 200 additional stale passes (no runaway compounding)",
        abs(factor_plus_stale - factor) < 1e-9,
        f"{factor_plus_stale} vs {factor}",
    )


def test_clear_pass_cloud_cover_softens_suppression() -> None:
    print("\n_clear_pass_suppression: a cloudy 'clear' pass suppresses less than a clear one")
    # further_plan.md section 2's tri-state model treats cloud as "unknown",
    # not "confirmed clear" - orbits.aoi_clear_passes only checks geometric
    # swath coverage, so this is what actually closes that gap. Monkeypatch
    # both the pass source and the cloud-cover source so the test is exact
    # and offline.
    import nexfiremap.orbits as orbits

    reference_ts = 1_000_000.0
    tau_hours = DEFAULT_TAU_HOURS
    pass_ts = reference_ts - 3600.0  # 1h old, on the hour so it matches a sample exactly

    def one_pass(_conn, _bbox, _det_times, _start_ts, _end_ts, _coincidence_s=None):
        return [{"ts": pass_ts}]

    original_passes = orbits.aoi_clear_passes
    original_cloud = likelihood_module._fetch_cloud_cover
    orbits.aoi_clear_passes = one_pass
    try:
        likelihood_module._fetch_cloud_cover = lambda *a, **kw: {}
        factor_no_data, _ = _clear_pass_suppression(
            conn=None, bbox=(0, 0, 1, 1), detections=[{"ts": reference_ts}],
            reference_ts=reference_ts, tau_hours=tau_hours,
        )

        likelihood_module._fetch_cloud_cover = lambda *a, **kw: {pass_ts: 0.0}
        factor_clear_sky, _ = _clear_pass_suppression(
            conn=None, bbox=(0, 0, 1, 1), detections=[{"ts": reference_ts}],
            reference_ts=reference_ts, tau_hours=tau_hours,
        )

        likelihood_module._fetch_cloud_cover = lambda *a, **kw: {pass_ts: 100.0}
        factor_overcast, _ = _clear_pass_suppression(
            conn=None, bbox=(0, 0, 1, 1), detections=[{"ts": reference_ts}],
            reference_ts=reference_ts, tau_hours=tau_hours,
        )

        # Far from the pass's timestamp - not usably close, so this must
        # behave exactly like no data at all (never penalise a pass for a
        # data gap that isn't its fault).
        likelihood_module._fetch_cloud_cover = lambda *a, **kw: {pass_ts - 100_000.0: 100.0}
        factor_far_sample, _ = _clear_pass_suppression(
            conn=None, bbox=(0, 0, 1, 1), detections=[{"ts": reference_ts}],
            reference_ts=reference_ts, tau_hours=tau_hours,
        )
    finally:
        orbits.aoi_clear_passes = original_passes
        likelihood_module._fetch_cloud_cover = original_cloud

    check(
        "0% cloud cover leaves suppression unchanged from no-data",
        abs(factor_clear_sky - factor_no_data) < 1e-9,
        f"{factor_clear_sky} vs {factor_no_data}",
    )
    check(
        "100% cloud cover suppresses less than a clear sky (higher factor)",
        factor_overcast > factor_clear_sky,
        f"{factor_overcast} vs {factor_clear_sky}",
    )
    check(
        "even full cloud still suppresses somewhat (floor, not zeroed out)",
        factor_overcast < 1.0,
        str(factor_overcast),
    )
    check(
        "a cloud sample too far away doesn't affect the factor at all",
        abs(factor_far_sample - factor_no_data) < 1e-9,
        f"{factor_far_sample} vs {factor_no_data}",
    )


def main() -> int:
    test_grid_geometry()
    test_active_heat_raster()
    test_arrival_time_estimate()
    test_probability_envelopes()
    test_png_rendering()
    test_analyze_event_job()
    test_analyze_event_clear_pass_suppression()
    test_clear_pass_suppression_does_not_compound_over_many_passes()
    test_clear_pass_cloud_cover_softens_suppression()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
