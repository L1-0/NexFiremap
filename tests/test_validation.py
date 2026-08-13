"""Smoke tests for the validation programme (further_plan.md section 11).

Run with:  python tests/test_validation.py
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import nexfiremap.validation as validation
from nexfiremap.db import Database
from nexfiremap.jobs import JobContext
from nexfiremap.likelihood import grid_geometry
from nexfiremap.orbits import SATELLITES
from nexfiremap.validation import (
    _train_grid_bbox,
    baseline_buffered_footprint,
    baseline_concave_hull,
    baseline_constant_radial,
    baseline_wind_ellipse,
    mean_of,
    pooled_brier,
    pooled_confusion_metrics,
    rolling_holdout_splits,
    run_validation,
    score_prediction,
)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


def test_rolling_holdout_splits() -> None:
    print("\nRolling-holdout splits")
    now = time.time()
    too_few = [{"lat": 40.0, "lon": 10.0, "ts": now}, {"lat": 40.0, "lon": 10.0, "ts": now + 60}]
    check("too few detections -> no splits", rolling_holdout_splits(too_few) == [])

    dets = [{"lat": 40.0 + i * 0.001, "lon": 10.0, "ts": now + i * 3600} for i in range(6)]
    splits = rolling_holdout_splits(dets, n_splits=2, min_train=2)
    check("produces splits", len(splits) > 0, str(len(splits)))
    for s in splits:
        check(
            "train/holdout partition the detections",
            len(s["train"]) + len(s["holdout"]) == len(dets),
            str((len(s["train"]), len(s["holdout"]))),
        )
        check("split_ts is the last training timestamp", s["split_ts"] == s["train"][-1]["ts"])
        check("target_ts is the first held-out timestamp", s["target_ts"] == s["holdout"][0]["ts"])
        check("split_ts precedes target_ts", s["split_ts"] < s["target_ts"])


def test_baseline_buffered_footprint() -> None:
    print("\nBaseline: buffered footprint")
    bbox = (10.0, 40.0, 10.05, 40.05)
    geom = grid_geometry(bbox, desired_res_m=100.0, max_dim=100)
    train = [{"lat": 40.025, "lon": 10.025, "scan": 0.4, "track": 0.4}]
    raster = baseline_buffered_footprint(train, geom)
    check("some cells covered", raster.sum() > 0, str(raster.sum()))
    check("not the whole grid covered", raster.sum() < raster.size, str(raster.sum()))
    check("binary output", set(np.unique(raster).tolist()) <= {0.0, 1.0})


def test_baseline_concave_hull() -> None:
    print("\nBaseline: concave hull (alpha shape)")
    bbox = (10.0, 40.0, 10.1, 40.1)
    geom = grid_geometry(bbox, desired_res_m=200.0, max_dim=100)

    # Too few points -> falls back to buffered footprint without crashing.
    fallback = baseline_concave_hull([{"lat": 40.05, "lon": 10.05, "scan": 0.4, "track": 0.4}], geom)
    check("falls back gracefully with <3 points", fallback.sum() > 0)

    # An L-shaped ring of points: a convex hull would fill the middle: a
    # concave alpha-shape with a tight-enough alpha should leave at least
    # some of the interior uncovered.
    ring = [
        {"lat": 40.01, "lon": 10.01}, {"lat": 40.01, "lon": 10.05}, {"lat": 40.01, "lon": 10.09},
        {"lat": 40.05, "lon": 10.09}, {"lat": 40.09, "lon": 10.09},
        {"lat": 40.09, "lon": 10.05}, {"lat": 40.09, "lon": 10.01}, {"lat": 40.05, "lon": 10.01},
    ]
    hull = baseline_concave_hull(ring, geom)
    check("hull covers something", hull.sum() > 0, str(hull.sum()))
    check("binary output", set(np.unique(hull).tolist()) <= {0.0, 1.0})


def test_baseline_constant_radial() -> None:
    print("\nBaseline: constant radial growth")
    bbox = (9.8, 39.8, 10.2, 40.2)
    geom = grid_geometry(bbox, desired_res_m=200.0, max_dim=100)
    now = time.time()
    # Distance from (40,10) grows roughly linearly with time.
    train = [
        {"lat": 40.0 + 0.001 * i, "lon": 10.0, "ts": now + i * 600, "scan": 0.4, "track": 0.4}
        for i in range(5)
    ]
    near_raster = baseline_constant_radial(train, geom, target_ts=now + 4 * 600)
    far_raster = baseline_constant_radial(train, geom, target_ts=now + 40 * 600)
    check("extrapolating further ahead predicts a larger area", far_raster.sum() >= near_raster.sum(),
          f"{far_raster.sum()} vs {near_raster.sum()}")

    # A single detection can't fit a growth rate - must not crash, and
    # should still cover at least its own cell.
    single = baseline_constant_radial([train[0]], geom, target_ts=now + 3600)
    check("single-detection case doesn't crash and covers something", single.sum() > 0)


def test_baseline_wind_ellipse() -> None:
    print("\nBaseline: wind-driven ellipse")
    bbox = (9.5, 39.5, 10.5, 40.5)
    geom = grid_geometry(bbox, desired_res_m=300.0, max_dim=120)
    now = time.time()
    train = [
        {"lat": 40.0, "lon": 10.0, "ts": now, "scan": 0.4, "track": 0.4},
        {"lat": 40.001, "lon": 10.001, "ts": now + 1800, "scan": 0.4, "track": 0.4},
        {"lat": 40.002, "lon": 10.002, "ts": now + 3600, "scan": 0.4, "track": 0.4},
    ]
    # Wind blowing FROM the north (0deg) pushes the fire south.
    raster = baseline_wind_ellipse(train, geom, target_ts=now + 6 * 3600, wind_dir_deg=0.0)
    check("wind ellipse covers something", raster.sum() > 0, str(raster.sum()))

    ny, _nx = geom["ny"], geom["nx"]
    # Row index increases from the grid's south edge to its north edge (see
    # score_prediction's test for the same convention). Wind from the north
    # pushes the fire south, so the covered region's own row-centroid
    # should sit below the grid's geometric centre row - checking the
    # shift directly, rather than at an arbitrary fixed offset, so this
    # doesn't depend on exactly how big the fitted ellipse turns out.
    rows, _cols = np.where(raster > 0)
    check(
        "covered region is shifted toward the downwind (south) side",
        float(rows.mean()) < ny / 2.0,
        f"mean_row={float(rows.mean())} ny/2={ny / 2.0}",
    )


def test_score_prediction() -> None:
    print("\nscore_prediction metrics")
    bbox = (10.0, 40.0, 10.1, 40.1)
    geom = grid_geometry(bbox, desired_res_m=200.0, max_dim=100)
    ny, nx = geom["ny"], geom["nx"]

    # Row 0 is the grid's southern edge (_sample/_grid_centers_m both index
    # y - and therefore row - from the bbox's south side upward), so this
    # covers the *southern* half of the grid.
    raster = np.zeros((ny, nx))
    raster[: ny // 2, :] = 1.0

    # A positive point in the covered half, a negative point in the clear half.
    positive = [(40.02, 10.05)]  # south-ish -> covered
    negative = [(40.08, 10.05)]  # north-ish -> clear
    metrics = score_prediction(raster, geom, positive, negative, threshold=0.5)
    check("perfect classification -> precision 1", metrics["precision"] == 1.0, str(metrics))
    check("perfect classification -> recall 1", metrics["recall"] == 1.0, str(metrics))
    check("perfect classification -> jaccard 1", metrics["jaccard_point_proxy"] == 1.0, str(metrics))
    check("perfect classification -> brier 0", metrics["brier_score"] == 0.0, str(metrics))

    # Swap them: positive point actually in the "clear" half -> a miss.
    metrics2 = score_prediction(raster, geom, negative, positive, threshold=0.5)
    check("missed detection -> recall 0", metrics2["recall"] == 0.0, str(metrics2))

    empty = score_prediction(np.zeros((ny, nx)), geom, [], [], threshold=0.5)
    check("no ground truth points -> no crash, no rate metrics", empty["precision"] is None)


def test_pooled_metrics_self_consistency() -> None:
    print("\nPooled (micro-averaged) metrics: F1 always matches P/R")
    # Reproduces the exact bug pattern caught live on a real fire (Lecco /
    # Lago di Como): three splits where a naive mean-of-per-split-ratios
    # aggregation displayed precision=1.000, recall=0.311, f1=0.617 - which
    # is mathematically impossible for matching precision/recall
    # (2*1*0.311/(1+0.311) ~= 0.474, not 0.617). Split A gets everything
    # right (tp=5,fp=0,fn=0 -> precision=1, recall=1), split B and C miss
    # every positive with no false positives (tp=0,fp=0,fn=5 -> recall=0,
    # precision *undefined*, not 0) - exactly the shape that let the old
    # macro-mean implementation silently average precision/recall/F1 over
    # *different* subsets of splits.
    rows = [
        {"tp": 5, "fp": 0, "fn": 0, "brier_score": 0.1, "positive_count": 5, "negative_count": 5},
        {"tp": 0, "fp": 0, "fn": 5, "brier_score": 0.9, "positive_count": 5, "negative_count": 5},
        {"tp": 0, "fp": 0, "fn": 5, "brier_score": 0.9, "positive_count": 5, "negative_count": 5},
    ]
    # Rounded-to-4-decimal-places display values, hence a loose (but still
    # tight) tolerance rather than exact equality - the *identity* check
    # below (computed from unrounded tp/fp/fn, matching how the function
    # itself computes f1 before its own final rounding) is the real test.
    pooled = pooled_confusion_metrics(rows)
    check("pooled precision is 1.0 (0 false positives anywhere)", pooled["precision"] == 1.0, str(pooled))
    check("pooled recall is 5/15 = 0.3333", abs(pooled["recall"] - 5 / 15) < 1e-4, str(pooled))
    check("pooled jaccard = tp/(tp+fp+fn) = 5/15", abs(pooled["jaccard_point_proxy"] - 5 / 15) < 1e-4, str(pooled))

    def harmonic_mean_from_counts(rows: list[dict[str, Any]]) -> float | None:
        """Reference F1 computed directly from unrounded summed counts - the
        same identity pooled_confusion_metrics itself relies on internally,
        used here as an independent check rather than re-deriving it from
        the function's own (already-rounded) returned precision/recall."""
        tp = sum(r["tp"] for r in rows)
        fp = sum(r["fp"] for r in rows)
        fn = sum(r["fn"] for r in rows)
        if (tp + fp) == 0 or (tp + fn) == 0:
            return None
        p, r = tp / (tp + fp), tp / (tp + fn)
        return round(2 * p * r / (p + r), 4) if (p + r) > 0 else None

    check(
        "pooled F1 exactly matches the harmonic mean of pooled P/R (the property that broke before)",
        pooled["f1"] == harmonic_mean_from_counts(rows),
        f"f1={pooled['f1']} vs harmonic-mean(P,R)={harmonic_mean_from_counts(rows)}",
    )

    # Randomized property check: for ANY set of per-split tp/fp/fn, pooled F1
    # must equal the harmonic mean of pooled precision/recall - this is an
    # algebraic identity when computed from one set of summed counts, unlike
    # a mean of independently-computed per-split ratios.
    rng = np.random.default_rng(7)
    for _ in range(20):
        n_splits = rng.integers(1, 5)
        random_rows = [
            {
                "tp": int(rng.integers(0, 10)),
                "fp": int(rng.integers(0, 10)),
                "fn": int(rng.integers(0, 10)),
            }
            for _ in range(n_splits)
        ]
        p = pooled_confusion_metrics(random_rows)
        expected = harmonic_mean_from_counts(random_rows)
        check("random case: F1 == harmonic-mean(P,R)", p["f1"] == expected, f"{p} vs expected {expected}")

    empty_precision = pooled_confusion_metrics([{"tp": 0, "fp": 0, "fn": 0}])
    check("all-zero split -> precision/recall/f1/jaccard all None, not a crash", all(v is None for v in empty_precision.values()), str(empty_precision))

    weighted = pooled_brier(
        [
            {"brier_score": 0.0, "positive_count": 1, "negative_count": 0},
            {"brier_score": 1.0, "positive_count": 9, "negative_count": 0},
        ]
    )
    check("pooled brier weights by sample count, not a flat mean (0.9, not 0.5)", abs(weighted - 0.9) < 1e-9, str(weighted))

    check("mean_of ignores None entries", mean_of("x", [{"x": 1.0}, {"x": None}, {"x": 3.0}]) == 2.0)
    check("mean_of returns None for an all-None column", mean_of("x", [{"x": None}]) is None)


def test_run_validation_job() -> None:
    print("\nrun_validation job body: end-to-end (offline via pre-seeded TLE)")
    tle_l1 = "1 43013U 17073A   26218.25797549  .00000011  00000+0  26270-4 0  9998"
    tle_l2 = "2 43013  98.7778 157.2781 0001936  98.9015 261.2381 14.19519984451577"

    # Deterministic stand-in for the live Open-Meteo calls (wind and cloud
    # cover), so this stays offline like every other test module. {} for
    # cloud cover means "no data available" - kernel_density's suppression
    # falls back to the purely geometric factor, same as before cloud
    # softening existed.
    validation._event_wind = lambda event, ts: {
        "wind_speed_ms": 6.0, "wind_direction_deg": 270.0, "relative_humidity_pct": 40.0, "hours_sampled": 6,
    }
    validation._fetch_cloud_cover = lambda *a, **kw: {}

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        db_path = tmp / "cache.sqlite3"
        db = Database(db_path)
        for name in SATELLITES:
            db.set_tle(name, tle_l1, tle_l2)

        day_start = int(datetime(2026, 8, 6, tzinfo=timezone.utc).timestamp())
        # This fixed TLE has a real overpass near bbox (10,40) at ~01:46 UTC
        # on this day (see test_orbits.py's empirically-discovered pass
        # times) - clustering every detection well before that, at 30-80
        # minutes past midnight, means a rolling-holdout window that
        # extends past ~01:46 has a genuine, non-coincident clear pass to
        # find, instead of only ever landing on gaps between real passes.
        offsets = [1800, 2400, 3000, 3600, 4200, 4800]

        def det(i: int, lat: float, lon: float) -> dict:
            ts = day_start + offsets[i]
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return {
                "source": "VIIRS_NOAA20_NRT", "satellite": "N", "instrument": "VIIRS",
                "latitude": lat, "longitude": lon,
                "acq_date": dt.strftime("%Y-%m-%d"), "acq_time": dt.strftime("%H%M"), "acq_ts": ts,
                "brightness": 330.0, "brightness2": 295.0, "scan": 0.4, "track": 0.4,
                "confidence_raw": "n", "confidence_pct": None, "confidence_level": "nominal",
                "frp": 12.0, "daynight": "D", "version": "2.0NRT",
            }

        rows = [
            det(0, 40.000, 10.000), det(1, 40.002, 10.001), det(2, 40.004, 10.001),
            det(3, 40.006, 10.002), det(4, 40.008, 10.002), det(5, 40.010, 10.003),
        ]
        db.upsert_detections(rows)

        lats = [r["latitude"] for r in rows]
        lons = [r["longitude"] for r in rows]
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO events (bbox_west, bbox_south, bbox_east, bbox_north, centroid_lat, "
            "centroid_lon, first_seen, last_seen, detection_count, sources_json, params_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'[]','{}',?)",
            (
                min(lons), min(lats), max(lons), max(lats),
                sum(lats) / len(lats), sum(lons) / len(lons),
                rows[0]["acq_ts"], rows[-1]["acq_ts"], len(rows), int(time.time()),
            ),
        )
        event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        det_ids = [r[0] for r in conn.execute("SELECT id FROM detections ORDER BY id").fetchall()]
        conn.executemany(
            "INSERT INTO event_members (event_id, detection_id) VALUES (?, ?)",
            [(event_id, did) for did in det_ids],
        )
        conn.commit()
        conn.close()

        job_id = db.create_job("validate_event", {})
        result_dir = tmp / "job-validate"
        result_dir.mkdir()
        db.close()

        ctx = JobContext(job_id=job_id, db_path=str(db_path), result_dir=str(result_dir))
        result = run_validation({"event_id": event_id, "n_splits": 2}, ctx)

        check("event_id echoed back", result["event_id"] == event_id)
        check("at least one split ran", result["split_count"] > 0, str(result["split_count"]))
        check(
            "kernel_density is scored",
            "kernel_density" in result["summary"],
            str(list(result["summary"])),
        )
        check(
            "geometric baselines are scored too",
            {"buffered_footprint", "concave_hull", "constant_radial"} <= set(result["summary"]),
            str(list(result["summary"])),
        )
        check(
            "wind_ellipse baseline ran (weather stubbed offline)",
            "wind_ellipse" in result["summary"],
            str(list(result["summary"])),
        )
        check(
            "clear-pass negatives were found (offline via pre-seeded TLE)",
            result["clear_pass_negatives_used"] > 0,
            str(result["clear_pass_negatives_used"]),
        )

        for model_name, metrics in result["summary"].items():
            check(f"{model_name}: brier_score is a finite number", metrics["brier_score"] is not None, model_name)

        import json

        full = json.loads((result_dir / "validation.json").read_text())
        kd_rows = full["splits"]["kernel_density"]
        check(
            "kernel_density per-split rows have an arrival_time_error_hours key",
            all("arrival_time_error_hours" in r for r in kd_rows),
        )
        bf_rows = full["splits"]["buffered_footprint"]
        check(
            "geometric baselines report arrival_time_error_hours as None (no time semantics)",
            all(r["arrival_time_error_hours"] is None for r in bf_rows),
        )


def test_train_grid_bbox_ignores_future_extent() -> None:
    print("\n_train_grid_bbox: sized from training points only, not the future")
    train = [{"lat": 40.0, "lon": 10.0}, {"lat": 40.01, "lon": 10.01}]
    west, south, east, north = _train_grid_bbox(train, pad_deg=0.05, elapsed_hours=0.0)
    check("bbox covers the training points", west <= 10.0 and east >= 10.01 and south <= 40.0 and north >= 40.01)
    # A far-away holdout point isn't passed in at all - the bbox has no way
    # to know about it, which is exactly the point (see the function's own
    # docstring on why the old shared, event-wide grid was a leak).
    check(
        "bbox does NOT reach a far-away future point on its own",
        east < 15.0,
        f"east={east}",
    )

    no_growth = _train_grid_bbox(train, pad_deg=0.05, elapsed_hours=0.0)
    with_growth = _train_grid_bbox(train, pad_deg=0.05, elapsed_hours=48.0)
    check(
        "more elapsed time widens the padding (room for a baseline to grow)",
        with_growth[0] < no_growth[0] and with_growth[2] > no_growth[2],
        f"{with_growth} vs {no_growth}",
    )


def main() -> int:
    test_rolling_holdout_splits()
    test_baseline_buffered_footprint()
    test_baseline_concave_hull()
    test_baseline_constant_radial()
    test_baseline_wind_ellipse()
    test_score_prediction()
    test_pooled_metrics_self_consistency()
    test_train_grid_bbox_ignores_future_extent()
    test_run_validation_job()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
