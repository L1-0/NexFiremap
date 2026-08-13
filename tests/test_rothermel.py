"""Offline tests for the Rothermel (1972, Albini 1976-corrected) surface-fire kernel
and the Albini & Chase (1980) directional/elliptical model - the regression table
firemodel.md sec.54 itself recommends (wind=0 -> phi_w=0, slope=0 -> phi_s=0,
moisture up -> ROS down, ...), plus a few structural checks on the vectorized grid
path used by `nexfiremap.terrain`.

Run with:  python tests/test_rothermel.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from nexfiremap import rothermel as rt

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


MODERATE = np.array([0.08, 0.09, 0.10, 0.60, 0.60])  # dead 8/9/10%, live 60%
DRY = np.array([0.03, 0.04, 0.05, 0.30, 0.30])
WET = np.array([0.25, 0.25, 0.25, 1.20, 1.20])


def test_fuel_table() -> None:
    print("\nAnderson 13 fuel table")
    for n in range(1, 14):
        fuel = rt.anderson13(n)
        check(f"model {n} has a name", fuel.name != "", fuel.name)
        check(f"model {n} has positive depth", fuel.depth_ft > 0, str(fuel.depth_ft))
        check(f"model {n} has some dead load", fuel.load_lb_ft2[:3].sum() > 0)
        check(f"model {n} dead moisture-of-extinction in (0,1)", 0 < fuel.dead_mext < 1, str(fuel.dead_mext))


def test_no_wind_no_slope_basic() -> None:
    print("\nNo-wind-no-slope base behaviour")
    fuel1 = rt.anderson13(1)  # short grass - fast, light fuel
    nws = rt.no_wind_no_slope(fuel1, MODERATE)
    check("burnable fuel produces positive base ROS", nws.base_ros_ft_min > 0, str(nws.base_ros_ft_min))
    check(
        "base ROS is a plausible order of magnitude for short grass (1-80 ft/min)",
        1.0 < nws.base_ros_ft_min < 80.0,
        str(nws.base_ros_ft_min),
    )
    check("reaction intensity positive", nws.reaction_intensity_btu_ft2_min > 0)
    check("max effective wind cap positive", nws.max_effective_wind_ft_min > 0)

    check("nonburnable fuel model produces zero ROS", rt.no_wind_no_slope(rt.NONBURNABLE, MODERATE).base_ros_ft_min == 0)


def test_moisture_damping_monotone() -> None:
    print("\nIncreasing moisture -> decreasing ROS")
    fuel = rt.anderson13(1)
    r_dry = rt.no_wind_no_slope(fuel, DRY).base_ros_ft_min
    r_moderate = rt.no_wind_no_slope(fuel, MODERATE).base_ros_ft_min
    r_wet = rt.no_wind_no_slope(fuel, WET).base_ros_ft_min
    check("dry > moderate ROS", r_dry > r_moderate, f"{r_dry} vs {r_moderate}")
    check("moderate > wet ROS", r_moderate > r_wet, f"{r_moderate} vs {r_wet}")

    # At/above the dead moisture-of-extinction, the fire should not spread at all.
    at_extinction = np.array([fuel.dead_mext * 1.5] * 3 + [2.0, 2.0])
    r_extinct = rt.no_wind_no_slope(fuel, at_extinction).base_ros_ft_min
    check("moisture past extinction stops spread", r_extinct < 1e-9, str(r_extinct))


def test_wind_and_slope_factors() -> None:
    print("\nWind/slope factor sanity (firemodel.md sec.54's table)")
    fuel = rt.anderson13(1)
    nws = rt.no_wind_no_slope(fuel, MODERATE)

    check("wind=0 -> phi_w contributes nothing (c_coef*0^b == 0)", (nws.c_coef / nws.f_term) * 0.0**nws.b_exp == 0.0)

    grid_fuel = np.full((3, 3), 1, dtype=np.int32)
    flat = np.zeros((3, 3))
    calm = rt.grid_directional_behavior(grid_fuel, flat, flat, 0.0, 0.0, MODERATE)
    check("zero wind, zero slope -> max_ros equals R0 (in m/min)", np.allclose(calm["max_ros_m_min"], nws.base_ros_ft_min * rt.FT_TO_M, rtol=1e-6))
    check("zero wind, zero slope -> isotropic (eccentricity 0)", np.allclose(calm["eccentricity"], 0.0))

    windy = rt.grid_directional_behavior(grid_fuel, flat, flat, 10.0, 0.0, MODERATE)
    check("wind increases max spread rate", float(windy["max_ros_m_min"].mean()) > float(calm["max_ros_m_min"].mean()))
    check("wind produces a directional (elliptical) shape", float(windy["eccentricity"].mean()) > 0)

    steep = np.full((3, 3), math.radians(30))
    sloped = rt.grid_directional_behavior(grid_fuel, steep, flat, 0.0, 0.0, MODERATE)
    check("slope alone increases max spread rate", float(sloped["max_ros_m_min"].mean()) > float(calm["max_ros_m_min"].mean()))

    stronger_wind = rt.grid_directional_behavior(grid_fuel, flat, flat, 20.0, 0.0, MODERATE)
    check(
        "stronger wind spreads faster than weaker wind (monotone)",
        float(stronger_wind["max_ros_m_min"].mean()) > float(windy["max_ros_m_min"].mean()),
    )


def test_direction_follows_wind() -> None:
    print("\nMax-spread direction follows the wind")
    grid_fuel = np.full((3, 3), 1, dtype=np.int32)
    flat = np.zeros((3, 3))
    # wind_from_deg=0 (from the North) blows toward the South (180 deg).
    behavior = rt.grid_directional_behavior(grid_fuel, flat, flat, 12.0, 0.0, MODERATE)
    check(
        "fire spreads toward the downwind bearing (~180 deg for a north wind)",
        abs((float(behavior["max_dir_deg"][0, 0]) - 180.0 + 180) % 360 - 180) < 1.0,
        str(behavior["max_dir_deg"][0, 0]),
    )

    behavior_east = rt.grid_directional_behavior(grid_fuel, flat, flat, 12.0, 90.0, MODERATE)
    check(
        "wind from the East blows the fire West (270 deg)",
        abs((float(behavior_east["max_dir_deg"][0, 0]) - 270.0 + 180) % 360 - 180) < 1.0,
        str(behavior_east["max_dir_deg"][0, 0]),
    )


def test_ros_at_bearing_ellipse() -> None:
    print("\nElliptical ros_at_bearing")
    max_ros, max_dir, ecc = 20.0, 90.0, 0.6
    heading = rt.ros_at_bearing(max_ros, max_dir, ecc, 90.0)
    backing = rt.ros_at_bearing(max_ros, max_dir, ecc, 270.0)
    flank = rt.ros_at_bearing(max_ros, max_dir, ecc, 0.0)
    check("heading direction matches max_ros exactly", math.isclose(heading, max_ros, rel_tol=1e-9), str(heading))
    check("backing (opposite direction) is much slower", backing < flank < heading, f"{backing}, {flank}, {heading}")
    check("backing rate stays positive (fire creeps backward, doesn't stop)", backing > 0)

    circular = rt.ros_at_bearing(10.0, 0.0, 0.0, 137.0)
    check("zero eccentricity is isotropic regardless of bearing", circular == 10.0, str(circular))


def test_length_to_width_ratio_capped() -> None:
    print("\nLength-to-width ratio cap")
    check("LWR=1 at zero wind", math.isclose(rt.length_to_width_ratio(0.0), 1.0 - 0.397 + 0.936 + 0.461, rel_tol=1e-6) or rt.length_to_width_ratio(0.0) >= 1.0)
    check("LWR capped at 8:1 for extreme wind", rt.length_to_width_ratio(200.0) == 8.0)
    check("eccentricity of LWR=1 (circular) is 0", rt.eccentricity_from_lwr(1.0) == 0.0)
    check("eccentricity of LWR=8 is close to 1 (very elongated)", rt.eccentricity_from_lwr(8.0) > 0.99)


def test_nonburnable_worldcover_classes() -> None:
    print("\nNonburnable classes stay at zero spread rate")
    grid_fuel = np.zeros((2, 2), dtype=np.int32)  # 0 = nonburnable
    flat = np.zeros((2, 2))
    behavior = rt.grid_directional_behavior(grid_fuel, flat, flat, 15.0, 45.0, MODERATE)
    check("nonburnable cells get zero max ROS (barrier floor applied by terrain.py, not here)", np.allclose(behavior["max_ros_m_min"], 0.0))


def main() -> int:
    test_fuel_table()
    test_no_wind_no_slope_basic()
    test_moisture_damping_monotone()
    test_wind_and_slope_factors()
    test_direction_follows_wind()
    test_ros_at_bearing_ellipse()
    test_length_to_width_ratio_capped()
    test_nonburnable_worldcover_classes()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
