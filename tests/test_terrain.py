"""Offline tests for the physics-propagation orchestration in `nexfiremap.terrain` -
slope/aspect from a synthetic DEM, circular wind averaging, the fuel crosswalk, the
anisotropic graph-based arrival-time solve, and isochrone contours. The actual
Rothermel/moisture physics have their own test modules (`test_rothermel.py`,
`test_moisture.py`); this module covers what's specific to terrain.py's
orchestration. Network-dependent pieces (DEM/WorldCover fetch, Open-Meteo) are
covered by live verification instead, the same split used for imagery.py's
STAC-dependent code.

Run with:  python tests/test_terrain.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from nexfiremap.likelihood import grid_geometry
from nexfiremap.terrain import (
    BARRIER_ROS_M_MIN,
    WORLDCOVER_TO_FUEL_MODEL,
    aspect_from_dem,
    build_directional_behavior,
    circular_mean_deg,
    fuel_model_grid,
    isochrone_contours,
    sample_ensemble,
    slope_from_dem,
    solve_travel_time_anisotropic,
    weighted_percentile_axis0,
)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


MODERATE_MOISTURE = np.array([0.08, 0.09, 0.10, 0.60, 0.60])


def test_circular_mean() -> None:
    print("\nCircular mean wind direction")
    check("straightforward case", abs(circular_mean_deg([90, 90, 90]) - 90) < 0.01)
    # A naive arithmetic mean of 350 and 10 gives 180 (wrong); circular mean gives ~0/360.
    mean = circular_mean_deg([350, 10])
    check("wraps correctly across 0/360", mean < 5 or mean > 355, str(mean))
    check("empty-ish single value", abs(circular_mean_deg([45]) - 45) < 0.01)


def test_slope_from_dem() -> None:
    print("\nSlope from a synthetic DEM")
    flat = np.zeros((20, 20))
    slope_flat = slope_from_dem(flat, res_m=30.0)
    check("flat terrain has ~zero slope", np.allclose(slope_flat, 0.0, atol=1e-6))

    # A ramp rising 100m over 20 cells * 30m = 600m run -> slope = atan(100/600).
    ramp = np.tile(np.arange(20) * 5.0, (20, 1))  # 5m per cell -> 100m over 20 cells
    slope_ramp = slope_from_dem(ramp, res_m=30.0)
    expected = np.arctan(5.0 / 30.0)
    check(
        "ramp slope matches the analytic expectation",
        np.allclose(slope_ramp[:, 1:-1], expected, atol=0.05),
        f"{slope_ramp[10, 10]} vs {expected}",
    )

    with_nan = flat.copy()
    with_nan[5, 5] = np.nan
    slope_nan = slope_from_dem(with_nan, res_m=30.0)
    check("NaN cells don't propagate NaN through the whole result", not np.any(np.isnan(slope_nan)))


def test_aspect_from_dem() -> None:
    print("\nAspect (downhill compass bearing) from a synthetic DEM")
    flat = np.zeros((10, 10))
    aspect_flat = aspect_from_dem(flat, res_m=30.0)
    check("flat terrain produces a finite (arbitrary) aspect, not NaN", np.all(np.isfinite(aspect_flat)))

    # Elevation increases going south (down rows) -> downhill faces North (0 deg).
    south_high = np.tile(np.arange(10) * 5.0, (10, 1)).T
    aspect_south_high = aspect_from_dem(south_high, res_m=30.0)
    check(
        "terrain rising to the south faces downhill North (~0 deg)",
        abs(aspect_south_high[5, 5] - 0.0) < 5.0 or abs(aspect_south_high[5, 5] - 360.0) < 5.0,
        str(aspect_south_high[5, 5]),
    )

    # Elevation increases going east (across cols) -> downhill faces West (270 deg).
    east_high = np.tile(np.arange(10) * 5.0, (10, 1))
    aspect_east_high = aspect_from_dem(east_high, res_m=30.0)
    check(
        "terrain rising to the east faces downhill West (~270 deg)",
        abs(aspect_east_high[5, 5] - 270.0) < 5.0,
        str(aspect_east_high[5, 5]),
    )


def test_fuel_model_grid_crosswalk() -> None:
    print("\nWorldCover -> Anderson13 crosswalk")
    codes = np.array(list(WORLDCOVER_TO_FUEL_MODEL.keys()), dtype=np.float64).reshape(1, -1)
    fuel_ids = fuel_model_grid(codes)
    check(
        "every WorldCover code maps to a valid Anderson13 number or 0 (nonburnable)",
        all(0 <= int(v) <= 13 for v in fuel_ids.flatten()),
        str(fuel_ids),
    )
    check("built-up (50) is nonburnable", int(fuel_ids[0, list(WORLDCOVER_TO_FUEL_MODEL.keys()).index(50)]) == 0)
    check("water (80) is nonburnable", int(fuel_ids[0, list(WORLDCOVER_TO_FUEL_MODEL.keys()).index(80)]) == 0)
    check("tree cover (10) is burnable", int(fuel_ids[0, list(WORLDCOVER_TO_FUEL_MODEL.keys()).index(10)]) > 0)

    unknown_code = np.array([[999.0]])
    check("an unrecognized code defaults to nonburnable, not a guess", fuel_model_grid(unknown_code)[0, 0] == 0)


def test_build_directional_behavior() -> None:
    print("\nDirectional behaviour: fuel x wind x slope, via the real Rothermel kernel")
    ny, nx = 5, 5
    grassland = np.full((ny, nx), 30.0)  # WorldCover grassland code
    flat_slope = np.zeros((ny, nx))
    flat_aspect = np.zeros((ny, nx))

    calm = build_directional_behavior(grassland, flat_slope, flat_aspect, 0.0, 0.0, MODERATE_MOISTURE)
    windy = build_directional_behavior(grassland, flat_slope, flat_aspect, 15.0, 0.0, MODERATE_MOISTURE)
    check(
        "wind increases max spread rate",
        float(windy["max_ros_m_min"].mean()) > float(calm["max_ros_m_min"].mean()),
        f"{windy['max_ros_m_min'].mean()} vs {calm['max_ros_m_min'].mean()}",
    )
    check("wind introduces direction (eccentricity > 0)", float(windy["eccentricity"].mean()) > 0)
    check("calm, flat conditions stay isotropic (eccentricity ~ 0)", np.allclose(calm["eccentricity"], 0.0))

    steep_slope = np.full((ny, nx), math.radians(30))
    steep = build_directional_behavior(grassland, steep_slope, flat_aspect, 0.0, 0.0, MODERATE_MOISTURE)
    check("steeper slope increases max spread rate", float(steep["max_ros_m_min"].mean()) > float(calm["max_ros_m_min"].mean()))

    water = np.full((ny, nx), 80.0)  # permanent water class code
    water_behavior = build_directional_behavior(water, flat_slope, flat_aspect, 10.0, 0.0, MODERATE_MOISTURE)
    check(
        "water is a barrier regardless of wind",
        np.allclose(water_behavior["max_ros_m_min"], BARRIER_ROS_M_MIN),
        str(water_behavior["max_ros_m_min"][0, 0]),
    )

    residual = build_directional_behavior(
        grassland, flat_slope, flat_aspect, 0.0, 0.0, MODERATE_MOISTURE, fuel_mult=2.0, spread_mult=1.0
    )
    check(
        "fuel_mult scales the physical spread rate (residual uncertainty multiplier)",
        np.allclose(residual["max_ros_m_min"], calm["max_ros_m_min"] * 2.0),
    )


def test_solve_travel_time_anisotropic() -> None:
    print("\nAnisotropic (Dijkstra) fast-marching solve")
    ny, nx = 40, 40
    uniform_ros = np.full((ny, nx), 3.0)  # m/min, isotropic (ecc=0)
    zero_dir = np.zeros((ny, nx))
    zero_ecc = np.zeros((ny, nx))
    travel = solve_travel_time_anisotropic(uniform_ros, zero_dir, zero_ecc, [ny // 2], [nx // 2], res_m=10.0)

    check("ignition cell's own travel time is zero", travel[ny // 2, nx // 2] == 0.0, str(travel[ny // 2, nx // 2]))
    check("travel time increases with distance from ignition", travel[0, 0] > travel[ny // 2, nx // 2 + 2])

    # Analytic check: uniform isotropic speed -> travel time ~= distance / speed.
    speed_m_s = 3.0 / 60.0
    dist_m = 10 * 10.0  # 10 cells * 10m
    expected_s = dist_m / speed_m_s
    actual_s = travel[ny // 2, nx // 2 + 10]
    check(
        "orthogonal travel matches distance/speed closely (8-connected grid, axis-aligned)",
        abs(actual_s - expected_s) / expected_s < 0.05,
        f"{actual_s} vs {expected_s}",
    )

    raised = False
    try:
        solve_travel_time_anisotropic(uniform_ros, zero_dir, zero_ecc, [1000], [1000], res_m=10.0)
    except ValueError:
        raised = True
    check("out-of-grid-only ignition points raise", raised)


def test_solve_travel_time_is_actually_anisotropic() -> None:
    print("\nAnisotropic solve genuinely elongates in the max-spread direction")
    ny, nx = 60, 60
    max_ros = np.full((ny, nx), 5.0)
    max_dir = np.full((ny, nx), 90.0)  # fire prefers spreading East
    ecc = np.full((ny, nx), 0.7)  # strongly elongated
    travel = solve_travel_time_anisotropic(max_ros, max_dir, ecc, [ny // 2], [nx // 2], res_m=10.0)

    east_time = travel[ny // 2, nx // 2 + 15]
    west_time = travel[ny // 2, nx // 2 - 15]
    north_time = travel[ny // 2 - 15, nx // 2]
    check(
        "fire reaches the preferred (East) direction faster than directly behind it (West)",
        east_time < west_time,
        f"east={east_time} west={west_time}",
    )
    check(
        "fire reaches the preferred (East) direction faster than a flank (North)",
        east_time < north_time,
        f"east={east_time} north={north_time}",
    )


def test_isochrone_contours() -> None:
    print("\nIsochrone contours")
    bbox = (10.0, 40.0, 10.05, 40.05)
    geom = grid_geometry(bbox, desired_res_m=30.0, max_dim=100)
    ny, nx = geom["ny"], geom["nx"]

    max_ros = np.full((ny, nx), 6.0)  # 6 m/min = 0.1 m/s
    zero_dir, zero_ecc = np.zeros((ny, nx)), np.zeros((ny, nx))
    travel = solve_travel_time_anisotropic(max_ros, zero_dir, zero_ecc, [ny // 2], [nx // 2], res_m=geom["res_m"])

    contours = isochrone_contours(travel, geom, hours=(0.5, 1.0, 100.0))
    check("some contours produced for reachable hours", len(contours) > 0, str(len(contours)))
    check(
        "no contour for a time level far beyond the reachable range",
        all(f["properties"]["hours"] != 100.0 for f in contours),
    )
    for feat in contours:
        check("contour is a LineString with >=3 points", feat["geometry"]["type"] == "LineString" and len(feat["geometry"]["coordinates"]) >= 3)
        break

    # Each ring now also produces a closed-Polygon "fill" twin alongside the
    # original LineString (see the fix for the spread-over-time color
    # encoding) - same count, same hour values, geometry actually closed.
    lines = [f for f in contours if f["geometry"]["type"] == "LineString"]
    fills = [f for f in contours if f["properties"].get("kind") == "fill"]
    check("every line ring has a matching fill polygon", len(fills) == len(lines), f"{len(fills)} vs {len(lines)}")
    check(
        "fill polygons carry the same hour values as their line twins",
        {f["properties"]["hours"] for f in fills} == {f["properties"]["hours"] for f in lines},
    )
    for feat in fills:
        ring = feat["geometry"]["coordinates"][0]
        check("fill polygon geometry is actually closed", ring[0] == ring[-1], str(ring[:2]) + " ... " + str(ring[-2:]))
        break

    empty = isochrone_contours(np.full((5, 5), np.nan), {"bbox": bbox, "nx": 5, "ny": 5})
    check("all-NaN raster produces no contours", empty == [])


def test_isochrone_contours_excludes_barrier_cells() -> None:
    print("\nIsochrone contours: barrier cells excluded from the max_hours cutoff")
    # Reproduces a real bug found live: a coastal event AOI with a lot of
    # open water reported a multi-*century* "typical max travel time"
    # because barrier cells (BARRIER_ROS_M_MIN - water/urban/snow) still get
    # a tiny non-zero travel time, and enough of them can drag even a
    # 99th-percentile cutoff deep into that meaningless tail.
    bbox = (10.0, 40.0, 10.1, 40.1)
    geom = grid_geometry(bbox, desired_res_m=50.0, max_dim=80)
    ny, nx = geom["ny"], geom["nx"]

    max_ros = np.full((ny, nx), 12.0)  # burnable, reasonably fast (m/min)
    max_ros[:, nx // 2 :] = BARRIER_ROS_M_MIN  # right half is a barrier, e.g. a lake
    zero_dir, zero_ecc = np.zeros((ny, nx)), np.zeros((ny, nx))
    travel = solve_travel_time_anisotropic(max_ros, zero_dir, zero_ecc, [ny // 2], [0], res_m=geom["res_m"])

    probe_hours = (0.5, 1.0, 100000.0)
    unmasked = isochrone_contours(travel, geom, hours=probe_hours)
    masked = isochrone_contours(travel, geom, hours=probe_hours, max_ros_m_min=max_ros)

    unmasked_hours = {f["properties"]["hours"] for f in unmasked}
    masked_hours = {f["properties"]["hours"] for f in masked}
    check(
        "without the ROS mask, the barrier-dominated tail admits an absurd hour level",
        100000.0 in unmasked_hours,
        str(unmasked_hours),
    )
    check(
        "with the ROS mask, that absurd level is correctly excluded",
        100000.0 not in masked_hours,
        str(masked_hours),
    )
    check(
        "ordinary reachable hours are unaffected by the mask",
        {0.5, 1.0} <= masked_hours,
        str(masked_hours),
    )


def test_sample_ensemble() -> None:
    print("\nEnsemble sampling (Phase 4b)")
    rng = np.random.default_rng(42)
    members = sample_ensemble(50, rng)
    check("requested member count produced", len(members) == 50)
    check(
        "wind_speed_bias stays positive (clamped)",
        all(m["wind_speed_bias"] > 0 for m in members),
    )
    fuel_mults = [m["fuel_mult"] for m in members]
    check(
        "fuel_mult spans a real range, not a constant",
        max(fuel_mults) - min(fuel_mults) > 0.1,
        str((min(fuel_mults), max(fuel_mults))),
    )
    check(
        "wind-direction bias IS sampled now (the anisotropic solve can use it)",
        "wind_dir_bias_deg" in members[0],
        str(list(members[0].keys())),
    )
    dir_biases = [m["wind_dir_bias_deg"] for m in members]
    check("wind_dir_bias_deg spans a real range", max(dir_biases) - min(dir_biases) > 5.0, str((min(dir_biases), max(dir_biases))))

    # Same seed -> same draws (reproducibility matters for a documented run).
    rng2 = np.random.default_rng(42)
    members2 = sample_ensemble(50, rng2)
    check(
        "seeded RNG reproduces the same ensemble",
        all(m1 == m2 for m1, m2 in zip(members, members2)),
    )


def test_weighted_percentile() -> None:
    print("\nWeighted per-cell percentile")
    # 3 members, 2x2 grid. Member 0 has all the weight -> percentile should
    # just reproduce member 0's values everywhere.
    values = np.array(
        [
            [[10.0, 20.0], [30.0, 40.0]],
            [[100.0, 200.0], [300.0, 400.0]],
            [[1000.0, 2000.0], [3000.0, 4000.0]],
        ]
    )
    weights = np.array([1.0, 0.0, 0.0])
    median = weighted_percentile_axis0(values, weights, 50.0)
    check("all weight on one member reproduces its values", np.allclose(median, values[0]), str(median))

    # Equal weights: median of [10,100,1000] with 3 points via this
    # cumulative-crossing method lands on the middle sorted value.
    equal_weights = np.full(3, 1.0 / 3.0)
    median_equal = weighted_percentile_axis0(values, equal_weights, 50.0)
    check(
        "equal weights pick the middle value at each cell",
        np.allclose(median_equal, values[1]),
        str(median_equal),
    )

    p10 = weighted_percentile_axis0(values, equal_weights, 1.0)
    check("low percentile picks the smallest member", np.allclose(p10, values[0]), str(p10))
    p99 = weighted_percentile_axis0(values, equal_weights, 99.0)
    check("high percentile picks the largest member", np.allclose(p99, values[2]), str(p99))


def test_model_caveats_name_missing_inputs() -> None:
    """A missing model input must be reported, not silently made reassuring.

    With no wind samples, `fetch_weather` still returns a number because the
    kernel needs one - but 0 m/s is the *optimistic* end for a spread model: it
    slows the head fire and pushes arrival times later, which is the direction
    that gets a crew placed too close. Nothing distinguished that from a
    genuinely still day, so the run has to say which it was.
    """
    print("\nModel caveats for missing inputs")
    from nexfiremap.terrain import _model_caveats

    absent = _model_caveats({"wind_observed": False, "wind_speed_ms": 0.0,
                             "relative_humidity_pct": 45.0})
    check("no wind data is reported as a caveat", len(absent) == 1, str(absent))
    check("the caveat says the run assumes calm", "calm" in absent[0].lower(), str(absent))
    check("...and names the consequence, not just the gap",
          "further" in absent[0] or "faster" in absent[0], str(absent))

    observed = _model_caveats({"wind_observed": True, "wind_speed_ms": 4.2,
                               "relative_humidity_pct": 45.0})
    check("a fully observed window carries no caveats", observed == [], str(observed))

    humidity = _model_caveats({"wind_observed": True, "relative_humidity_pct": None})
    check("missing humidity is reported too", any("humidity" in c for c in humidity), str(humidity))

    backfilled = _model_caveats({"wind_observed": True, "relative_humidity_pct": 40.0,
                                 "hours_backfilled_recent": 3})
    check("forecast-backfilled hours are disclosed",
          any("forecast" in c for c in backfilled), str(backfilled))


def main() -> int:
    test_circular_mean()
    test_slope_from_dem()
    test_aspect_from_dem()
    test_fuel_model_grid_crosswalk()
    test_build_directional_behavior()
    test_solve_travel_time_anisotropic()
    test_solve_travel_time_is_actually_anisotropic()
    test_isochrone_contours()
    test_isochrone_contours_excludes_barrier_cells()
    test_sample_ensemble()
    test_weighted_percentile()
    test_model_caveats_name_missing_inputs()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
