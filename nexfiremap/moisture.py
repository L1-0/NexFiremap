"""Dead-fuel moisture conditioning for the Rothermel kernel (`nexfiremap/rothermel.py`).

Approximates Nelson's (2000) dead-fuel-moisture model (firemodel.md sec.35-44) with a
much simpler and much more tractable two-piece model, in the same spirit as - but not
the same computation as - FlamMap's own treatment of its fastest-responding fuel class
(firemodel.md sec.44 notes FlamMap uses "equilibrium-moisture calculation instead of
Nelson" there): this module still exponentially *relaxes toward* equilibrium moisture
with a 1-hour time constant for the 1h class, rather than snapping straight to the
equilibrium value each step the way FlamMap's literal 1h treatment does. Close enough
in practice for a fuel class that responds within an hour either way, but worth being
precise about since it isn't literally the same formula:

1. **Equilibrium moisture** - Nelson's own closed-form formula (sec.38), driven by
   hourly temperature/relative-humidity.
2. **Time-lag conditioning** - a classic NFDRS-style exponential relaxation toward
   that hour's equilibrium moisture (``dm/dt = (m_e - m) / tau``), with time
   constants of 1/10/100 hours for the three dead fuel-size classes, plus a simple
   "rain saturates quickly" rule.

This is explicitly *not* Nelson's full radial heat/moisture-diffusion PDE through a
cylindrical fuel stick (which firemodel.md sec.35-44 itself calls "the hardest
component mathematically" of the whole FlamMap stack, needing a stateful
temperature+moisture solve at every radial node of a representative fuel particle).
It captures the practically important behaviour - moisture that responds to real
weather history instead of an instantaneous snapshot - at a fraction of the
implementation and validation risk. See the fire-propagation upgrade plan for why
this scope was chosen over the full stick model.

Live-fuel moisture has no free/global data source this project can reach (no live
vegetation-moisture product), so it stays a fixed seasonal-ish default -
``DEFAULT_LIVE_HERB_MOISTURE``/``DEFAULT_LIVE_WOODY_MOISTURE`` - clearly a
placeholder, not a measurement.
"""

from __future__ import annotations

import math

import numpy as np

# Class order matches nexfiremap.rothermel: [dead_1h, dead_10h, dead_100h, live_herb, live_woody]
TIME_LAG_HOURS = (1.0, 10.0, 100.0)

# No live-moisture data source (see module docstring) - typical mid-season, not-yet-
# cured defaults, used unconditionally until a real source is available.
DEFAULT_LIVE_HERB_MOISTURE = 1.00  # 100% (fraction of oven-dry mass)
DEFAULT_LIVE_WOODY_MOISTURE = 0.90  # 90%

# A simple stand-in for Nelson's rain-priority surface state machine (firemodel.md
# sec.41): above this hourly total, dead fuel is assumed to wet up quickly rather than
# following the normal equilibrium-moisture target.
RAIN_MM_THRESHOLD = 0.5
RAIN_TARGET_MOISTURE = 0.35


def equilibrium_moisture(relative_humidity_pct: float, temperature_c: float) -> float:
    """Nelson's (2000) equilibrium-moisture-content formula (fraction), firemodel.md
    sec.38. Substitutes 0.99 for saturation (``relative_humidity_pct >= 100``) to
    avoid the ``ln(1 - H_s)`` singularity, exactly as Nelson's own paper specifies."""
    h_s = min(0.99, max(0.0, relative_humidity_pct / 100.0))
    u_scale = 0.1617 - 0.001419 * temperature_c
    u_exp = 0.4657 - 0.003578 * temperature_c
    # -log(1 - h_s) is exactly 0.0 at h_s == 0 (0% RH, a real value, not an
    # edge case a live weather feed can't produce), and u_exp goes negative
    # above ~130degC - 0.0 ** a negative exponent raises ZeroDivisionError in
    # Python rather than returning the physically-sensible "~bone dry"
    # answer. Flooring the base just above zero keeps the result close to 0
    # (correct at 0% RH regardless of temperature) without the crash.
    base = max(1e-9, -math.log(1.0 - h_s))
    return max(0.0, u_scale * base**u_exp)


def condition_dead_fuel_moisture(
    hourly_temperature_c: list[float],
    hourly_relative_humidity_pct: list[float],
    hourly_precip_mm: list[float | None],
    initial_moisture: tuple[float, float, float] = (0.10, 0.12, 0.14),
    dt_hours: float = 1.0,
) -> tuple[float, float, float]:
    """Time-lag-conditions 1h/10h/100h dead-fuel moisture through an hourly weather
    series (oldest first), returning the moisture fractions at the *end* of the
    series - i.e. at the reference time the caller cares about. See module docstring
    for the exponential-relaxation-toward-equilibrium approach used.

    ``initial_moisture`` seeds the conditioning - a handful of hours of history is
    usually enough for the 1h class to converge, but the 100h class needs a much
    longer run-up to be meaningful - short weather windows will under-condition it,
    which is why the default seed leans slightly damp rather than guessing dry.
    """
    n = len(hourly_temperature_c)
    if n == 0:
        return initial_moisture
    m = list(initial_moisture)
    for i in range(n):
        temp, rh = hourly_temperature_c[i], hourly_relative_humidity_pct[i]
        if temp is None or rh is None:
            continue  # missing hour - carry moisture forward unchanged rather than guess
        m_e = equilibrium_moisture(rh, temp)
        precip = hourly_precip_mm[i]
        raining = precip is not None and precip >= RAIN_MM_THRESHOLD
        for k, tau in enumerate(TIME_LAG_HOURS):
            if raining:
                # Simple rule (module docstring): rain drives moisture toward a wet
                # target quickly instead of solving Nelson's full rain-boundary state
                # machine (sec.41). Faster classes respond faster even under this rule.
                target, local_tau = RAIN_TARGET_MOISTURE, max(1.0, tau * 0.25)
            else:
                target, local_tau = m_e, tau
            m[k] = target + (m[k] - target) * math.exp(-dt_hours / local_tau)
    return (m[0], m[1], m[2])


def moisture_vector(
    dead_1h: float,
    dead_10h: float,
    dead_100h: float,
    live_herb: float = DEFAULT_LIVE_HERB_MOISTURE,
    live_woody: float = DEFAULT_LIVE_WOODY_MOISTURE,
) -> np.ndarray:
    """Assembles the 5-class moisture array `nexfiremap.rothermel` expects, ordered
    [dead_1h, dead_10h, dead_100h, live_herb, live_woody]."""
    return np.array([dead_1h, dead_10h, dead_100h, live_herb, live_woody], dtype=np.float64)
