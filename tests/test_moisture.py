"""Offline tests for `nexfiremap.moisture` - Nelson's (2000) equilibrium-moisture
formula and the NFDRS-style time-lag conditioning built on top of it.

Run with:  python tests/test_moisture.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap import moisture

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


def test_equilibrium_moisture_bounds() -> None:
    print("\nEquilibrium moisture")
    dry = moisture.equilibrium_moisture(relative_humidity_pct=10.0, temperature_c=25.0)
    humid = moisture.equilibrium_moisture(relative_humidity_pct=90.0, temperature_c=25.0)
    check("higher humidity -> higher equilibrium moisture", humid > dry, f"{humid} vs {dry}")
    check("equilibrium moisture stays non-negative", dry >= 0 and humid >= 0)
    check(
        "equilibrium moisture is a plausible fraction (< 0.5) even near saturation",
        moisture.equilibrium_moisture(99.9, 20.0) < 0.5,
    )

    saturated = moisture.equilibrium_moisture(relative_humidity_pct=100.0, temperature_c=20.0)
    check("RH=100% doesn't raise a math domain error (0.99 substitution)", saturated > 0)

    # RH=0% makes -log(1 - h_s) exactly 0.0; above ~130.2degC the exponent
    # (0.4657 - 0.003578*T) goes negative, and 0.0 ** negative used to raise
    # ZeroDivisionError instead of the physically-sensible "bone dry" answer.
    bone_dry = moisture.equilibrium_moisture(relative_humidity_pct=0.0, temperature_c=150.0)
    check("RH=0% at high temperature doesn't raise ZeroDivisionError", bone_dry == 0.0, bone_dry)
    check(
        "RH=0% at ordinary temperature stays a tiny non-negative value",
        moisture.equilibrium_moisture(relative_humidity_pct=0.0, temperature_c=25.0) >= 0.0,
    )


def test_time_lag_conditioning_converges() -> None:
    print("\nTime-lag conditioning converges toward equilibrium under constant weather")
    n_hours = 400  # enough for even the 100h class to mostly equilibrate
    temps = [20.0] * n_hours
    rh = [40.0] * n_hours
    precip = [0.0] * n_hours
    m1, m10, m100 = moisture.condition_dead_fuel_moisture(temps, rh, precip)
    m_e = moisture.equilibrium_moisture(40.0, 20.0)
    check("1h moisture converges close to equilibrium", abs(m1 - m_e) < 0.01, f"{m1} vs {m_e}")
    check("10h moisture converges close to equilibrium given enough hours", abs(m10 - m_e) < 0.02, f"{m10} vs {m_e}")
    check("100h moisture moves toward equilibrium (slower, needn't fully converge)", abs(m100 - m_e) < abs(0.12 - m_e))


def test_time_lag_ordering_after_a_dry_step() -> None:
    print("\nFaster classes respond faster to a step change")
    # Start damp, then a long dry/hot spell - 1h should have moved furthest toward
    # the new (lower) equilibrium, 100h the least, after the same elapsed time.
    n_hours = 48
    temps = [30.0] * n_hours
    rh = [15.0] * n_hours
    precip = [0.0] * n_hours
    initial = (0.20, 0.20, 0.20)
    m1, m10, m100 = moisture.condition_dead_fuel_moisture(temps, rh, precip, initial_moisture=initial)
    check("1h dried out the most", m1 < m10 < m100, f"{m1}, {m10}, {m100}")
    check("all classes dried out at least somewhat from the 20% start", m1 < 0.20 and m100 <= 0.20)


def test_rain_wets_fuel_quickly() -> None:
    print("\nRain drives moisture up quickly")
    n_hours = 6
    temps = [20.0] * n_hours
    rh = [30.0] * n_hours
    precip = [5.0] * n_hours  # well above RAIN_MM_THRESHOLD every hour
    m1, m10, m100 = moisture.condition_dead_fuel_moisture(temps, rh, precip, initial_moisture=(0.05, 0.05, 0.05))
    check("1h fuel wets up substantially within a few rainy hours", m1 > 0.15, str(m1))
    check("wetting doesn't exceed the rain target moisture", m1 <= moisture.RAIN_TARGET_MOISTURE + 1e-6)


def test_missing_hours_are_skipped_not_crashed() -> None:
    print("\nMissing (None) weather hours don't crash conditioning")
    temps = [20.0, None, 22.0]
    rh = [40.0, 40.0, None]
    precip = [0.0, None, 0.0]
    result = moisture.condition_dead_fuel_moisture(temps, rh, precip)
    check("returns a 3-tuple without raising", len(result) == 3, str(result))
    check("all values finite and non-negative", all(v >= 0 for v in result), str(result))


def test_moisture_vector_ordering() -> None:
    print("\nmoisture_vector assembly")
    vec = moisture.moisture_vector(0.1, 0.12, 0.14, live_herb=0.9, live_woody=0.8)
    check("5-element vector in [1h,10h,100h,herb,woody] order", list(vec) == [0.1, 0.12, 0.14, 0.9, 0.8], str(vec))
    default_vec = moisture.moisture_vector(0.1, 0.12, 0.14)
    check(
        "live moisture defaults applied when omitted",
        default_vec[3] == moisture.DEFAULT_LIVE_HERB_MOISTURE and default_vec[4] == moisture.DEFAULT_LIVE_WOODY_MOISTURE,
    )


def main() -> int:
    test_equilibrium_moisture_bounds()
    test_time_lag_conditioning_converges()
    test_time_lag_ordering_after_a_dry_step()
    test_rain_wets_fuel_quickly()
    test_missing_hours_are_skipped_not_crashed()
    test_moisture_vector_ordering()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
