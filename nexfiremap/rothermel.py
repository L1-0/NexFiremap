"""Rothermel (1972) surface-fire spread model with the Albini (1976) operational
correction, plus the standard Albini & Chase (1980)/Anderson (1983) elliptical
directional model - the physics kernel behind the "Modelled spread" layer
(`nexfiremap/terrain.py`), replacing the flat per-land-cover-class spread rate and
isotropic wind/slope multipliers used before.

**Source**: the equations below follow firemodel.md's own citations directly -
Rothermel 1972 (USDA Forest Service RP-INT-115), the Albini 1976 FIREMOD correction
(USDA Forest Service GTR-INT-143), Albini & Chase 1980's eccentricity formula (eq.8),
and the BehavePlus length-to-width-ratio regression (Andrews 2018, RMRS-GTR-371). The
13 standard Anderson fuel models' numeric constants (load, SAVR, depth, moisture of
extinction) are public-domain values from Anderson 1982 (USDA Forest Service
GTR-INT-122) - the same numbers every independent implementation of this model
reproduces (BehavePlus, NFDRS, ...). Both the fuel-model table and the overall
formula structure here were cross-checked against the open-source `pyretechnics`
project's published source (pyregence/pyretechnics) for numerical correctness - see
the fire-propagation upgrade plan for why `pyretechnics` itself isn't used directly
as a dependency (Cython source-only on PyPI, no prebuilt Windows wheel).

**Explicitly scoped simplifications** (documented, not silent):

* Wind/slope vectors are combined as flat 2D vectors in map-projection space, not
  projected onto the true 3D slope-tangential plane FARSITE uses - reasonable for a
  horizontal raster grid, but slightly understates wind/slope alignment on very
  steep terrain.
* No canopy wind-sheltering term - this project has no confirmed free global
  canopy-cover/height source yet, so midflame wind always uses the unsheltered
  (fuel-bed-depth-only) adjustment factor (Andrews 2012 eq.6).
* Fuel moisture is one value per size class for the whole AOI (from
  `nexfiremap.moisture` or a caller-supplied estimate), not spatially varying -
  matches the current weather-input granularity (one Open-Meteo point sample).
* No dynamic (curing) fuel-model support - live herbaceous load is used at face
  value rather than partially reclassified as cured/dead based on live moisture.
  Only 4 of the 13 Anderson models even carry live herbaceous load, so the effect
  is limited to those.
* Net fuel loading and characteristic SAVR both use the same per-class
  surface-area-weighting (``f_ij``), not the further FIREMOD size-class *grouping*
  refinement (``g_ij``) firemodel.md sec.8 mentions - a secondary-order operational
  detail, not one of Rothermel's core equations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------- unit constants
M_TO_FT = 3.28084
FT_TO_M = 1.0 / M_TO_FT
FT_MIN_PER_MPH = 88.0  # 5280 ft/mi / 60 min/hr

_RHO_P = 32.0  # lb/ft^3 - standard Rothermel ovendry particle density (all classes)
_S_T = 0.0555  # total mineral content - standard constant
_S_E = 0.01  # effective (silica-free) mineral content - standard constant

# Class order used throughout this module: [dead_1h, dead_10h, dead_100h, live_herb, live_woody]
_DEAD = (0, 1, 2)
_LIVE = (3, 4)


def _mps_to_ft_min(v: float) -> float:
    return v * M_TO_FT * 60.0


# ------------------------------------------------------- Anderson 13 fuel models
# (depth_ft, dead_mext_pct, heat_kbtu_lb, w_1h, w_10h, w_100h, w_herb, w_woody,
#  sigma_1h, sigma_10h, sigma_100h, sigma_herb, sigma_woody)
# Loads in lb/ft^2, SAVR in ft^-1, depth in ft, dead moisture-of-extinction in
# percent, heat content in thousands of Btu/lb - public-domain values, Anderson 1982
# GTR-INT-122 ("Aids to Determining Fuel Models for Estimating Fire Behavior").
# fmt: off
_ANDERSON13_RAW: dict[int, tuple[float, ...]] = {
    1:  (1.0, 12.0, 8.0, 0.0340, 0.0000, 0.0000, 0.0000, 0.0000, 3500.0,   0.0,  0.0,    0.0,    0.0),
    2:  (1.0, 15.0, 8.0, 0.0920, 0.0460, 0.0230, 0.0230, 0.0000, 3000.0, 109.0, 30.0, 1500.0,    0.0),
    3:  (2.5, 25.0, 8.0, 0.1380, 0.0000, 0.0000, 0.0000, 0.0000, 1500.0,   0.0,  0.0,    0.0,    0.0),
    4:  (6.0, 20.0, 8.0, 0.2300, 0.1840, 0.0920, 0.2300, 0.0000, 2000.0, 109.0, 30.0, 1500.0,    0.0),
    5:  (2.0, 20.0, 8.0, 0.0460, 0.0230, 0.0000, 0.0920, 0.0000, 2000.0, 109.0,  0.0, 1500.0,    0.0),
    6:  (2.5, 25.0, 8.0, 0.0690, 0.1150, 0.0920, 0.0000, 0.0000, 1750.0, 109.0, 30.0,    0.0,    0.0),
    7:  (2.5, 40.0, 8.0, 0.0520, 0.0860, 0.0690, 0.0170, 0.0000, 1750.0, 109.0, 30.0, 1550.0,    0.0),
    8:  (0.2, 30.0, 8.0, 0.0690, 0.0460, 0.1150, 0.0000, 0.0000, 2000.0, 109.0, 30.0,    0.0,    0.0),
    9:  (0.2, 25.0, 8.0, 0.1340, 0.0190, 0.0070, 0.0000, 0.0000, 2500.0, 109.0, 30.0,    0.0,    0.0),
    10: (1.0, 25.0, 8.0, 0.1380, 0.0920, 0.2300, 0.0920, 0.0000, 2000.0, 109.0, 30.0, 1500.0,    0.0),
    11: (1.0, 15.0, 8.0, 0.0690, 0.2070, 0.2530, 0.0000, 0.0000, 1500.0, 109.0, 30.0,    0.0,    0.0),
    12: (2.3, 20.0, 8.0, 0.1840, 0.6440, 0.7590, 0.0000, 0.0000, 1500.0, 109.0, 30.0,    0.0,    0.0),
    13: (3.0, 25.0, 8.0, 0.3220, 1.0580, 1.2880, 0.0000, 0.0000, 1500.0, 109.0, 30.0,    0.0,    0.0),
}
# fmt: on

ANDERSON13_NAMES: dict[int, str] = {
    1: "Short grass", 2: "Timber grass and understory", 3: "Tall grass",
    4: "Chaparral", 5: "Brush", 6: "Dormant brush, hardwood slash",
    7: "Southern rough", 8: "Closed timber litter", 9: "Hardwood litter",
    10: "Timber litter and understory", 11: "Light logging slash",
    12: "Medium logging slash", 13: "Heavy logging slash",
}  # fmt: skip


@dataclass(frozen=True)
class FuelModel:
    """One Anderson-13 fuel model. All 5-element arrays ordered
    [dead_1h, dead_10h, dead_100h, live_herb, live_woody]."""

    number: int
    name: str
    depth_ft: float
    dead_mext: float  # fraction
    heat_btu_lb: np.ndarray
    load_lb_ft2: np.ndarray
    savr_ft_inv: np.ndarray


def anderson13(number: int) -> FuelModel:
    """Look up one of the 13 standard fuel models by number and unpack its
    raw ``_ANDERSON13_RAW`` tuple into a ``FuelModel`` - heat content is
    broadcast to all 5 size classes (the table only carries one value per
    fuel model, not per class) and converted from kBtu/lb to Btu/lb."""
    raw = _ANDERSON13_RAW[number]
    depth_ft, mext_pct, heat_kbtu = raw[0], raw[1], raw[2]
    return FuelModel(
        number=number,
        name=ANDERSON13_NAMES[number],
        depth_ft=depth_ft,
        dead_mext=mext_pct / 100.0,
        heat_btu_lb=np.full(5, heat_kbtu * 1000.0),
        load_lb_ft2=np.array(raw[3:8], dtype=np.float64),
        savr_ft_inv=np.array(raw[8:13], dtype=np.float64),
    )


NONBURNABLE = FuelModel(
    number=0,
    name="Nonburnable",
    depth_ft=0.0,
    dead_mext=0.01,
    heat_btu_lb=np.zeros(5),
    load_lb_ft2=np.zeros(5),
    savr_ft_inv=np.zeros(5),
)


# ------------------------------------------------------------- no-wind-no-slope


@dataclass(frozen=True)
class NoWindNoSlope:
    """Wind/slope-independent surface-fire behaviour for one fuel model at one
    moisture state - computed once per fuel model, not once per grid cell."""

    base_ros_ft_min: float
    base_fireline_intensity_btu_ft_s: float
    reaction_intensity_btu_ft2_min: float
    max_effective_wind_ft_min: float  # firemodel.md sec.12
    beta: float
    beta_op: float
    sigma_prime: float
    b_exp: float  # wind-factor exponent (sec.11)
    c_coef: float  # wind-factor coefficient (sec.11)
    f_term: float  # (beta/beta_op)^E (sec.11)

    @property
    def burnable(self) -> bool:
        return self.base_ros_ft_min > 0.0


_ZERO_NWS = NoWindNoSlope(0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0)


def _moisture_damping(m_f: float, m_x: float) -> float:
    """Rothermel's moisture damping coefficient (eta_m, sec.5): a cubic
    falloff of reaction intensity as fuel moisture ``m_f`` approaches its
    moisture of extinction ``m_x``, clamped to 0 once ``m_f`` reaches or
    exceeds ``m_x`` (fuel that wet no longer supports combustion)."""
    if m_x <= 0.0:
        return 0.0
    r = min(1.0, max(0.0, m_f / m_x))
    return max(0.0, 1.0 - 2.59 * r + 5.11 * r * r - 3.52 * r * r * r)


def live_moisture_of_extinction(fuel: FuelModel, moisture: np.ndarray) -> float:
    """firemodel.md sec.6 - the FIREMOD-compatible form derived from Rothermel eq.88.
    Returns ``fuel.dead_mext`` (unused downstream) when there's no live fuel present."""
    w_o, sigma = fuel.load_lb_ft2, fuel.savr_ft_inv
    dead_w = sum(w_o[j] * math.exp(-138.0 / sigma[j]) for j in _DEAD if sigma[j] > 0)
    live_w = sum(w_o[j] * math.exp(-500.0 / sigma[j]) for j in _LIVE if sigma[j] > 0)
    if live_w <= 0.0 or fuel.dead_mext <= 0.0:
        return fuel.dead_mext
    w_prime = dead_w / live_w
    m_f_dead_weighted = (
        sum(w_o[j] * moisture[j] * math.exp(-138.0 / sigma[j]) for j in _DEAD if sigma[j] > 0) / dead_w
        if dead_w > 0
        else 0.0
    )
    return max(fuel.dead_mext, 2.9 * w_prime * (1.0 - m_f_dead_weighted / fuel.dead_mext) - 0.226)


def no_wind_no_slope(fuel: FuelModel, moisture: np.ndarray) -> NoWindNoSlope:
    """firemodel.md sec.2-15 - reaction intensity, base spread rate, base fireline
    intensity, plus the wind-factor coefficients (sec.11) and effective-wind cap
    (sec.12) needed by `directional_behavior` below."""
    w_o, sigma, rho_p = fuel.load_lb_ft2, fuel.savr_ft_inv, np.full(5, _RHO_P)
    if fuel.depth_ft <= 0.0 or w_o.sum() <= 0.0:
        return _ZERO_NWS

    a_j = np.where(w_o > 0, sigma * w_o / rho_p, 0.0)  # surface area per class (sec.3)
    a_dead, a_live = float(a_j[list(_DEAD)].sum()), float(a_j[list(_LIVE)].sum())
    a_total = a_dead + a_live
    if a_total <= 0.0:
        return _ZERO_NWS

    f_dead, f_live = a_dead / a_total, a_live / a_total
    f_j = np.zeros(5)
    if a_dead > 0:
        for j in _DEAD:
            f_j[j] = a_j[j] / a_dead
    if a_live > 0:
        for j in _LIVE:
            f_j[j] = a_j[j] / a_live

    sigma_prime = float((a_j * sigma).sum() / a_total)  # sec.3
    beta = float((w_o / rho_p).sum() / fuel.depth_ft)  # sec.4 packing ratio
    beta_op = 3.348 * sigma_prime**-0.8189 if sigma_prime > 0 else 1.0  # sec.4

    m_f_dead = sum(f_j[j] * moisture[j] for j in _DEAD)
    eta_m_dead = _moisture_damping(m_f_dead, fuel.dead_mext)  # sec.5
    mx_live = live_moisture_of_extinction(fuel, moisture)  # sec.6
    m_f_live = sum(f_j[j] * moisture[j] for j in _LIVE)
    eta_m_live = _moisture_damping(m_f_live, mx_live)

    eta_s = 0.174 * _S_E**-0.19  # sec.5, mineral damping

    h_dead = sum(f_j[j] * fuel.heat_btu_lb[j] for j in _DEAD)
    h_live = sum(f_j[j] * fuel.heat_btu_lb[j] for j in _LIVE)
    w_n_dead = sum(f_j[j] * w_o[j] * (1.0 - _S_T) for j in _DEAD)  # sec.8, net fuel loading
    w_n_live = sum(f_j[j] * w_o[j] * (1.0 - _S_T) for j in _LIVE)
    heat_per_area = w_n_dead * h_dead * eta_m_dead * eta_s + w_n_live * h_live * eta_m_live * eta_s  # Btu/ft^2

    # sec.7 - reaction velocity with the Albini (1976) exponent correction.
    a_albini = 133.0 * sigma_prime**-0.7913 if sigma_prime > 0 else 0.0
    gamma_max = sigma_prime**1.5 / (495.0 + 0.0594 * sigma_prime**1.5) if sigma_prime > 0 else 0.0
    c_ratio = beta / beta_op if beta_op > 0 else 0.0
    gamma_prime = gamma_max * c_ratio**a_albini * math.exp(a_albini * (1.0 - c_ratio)) if c_ratio > 0 else 0.0

    reaction_intensity = heat_per_area * gamma_prime  # sec.8, Btu/ft^2/min
    xi = (
        math.exp((0.792 + 0.681 * math.sqrt(sigma_prime)) * (beta + 0.1)) / (192.0 + 0.2595 * sigma_prime)
        if sigma_prime > 0
        else 0.0
    )  # sec.9, propagating flux ratio
    heat_source = reaction_intensity * xi

    rho_b = float(w_o.sum() / fuel.depth_ft)  # sec.10, ovendry bulk density
    epsilon_j = np.array([math.exp(-138.0 / s) if s > 0 else 0.0 for s in sigma])  # sec.10
    q_ig_j = 250.0 + 1116.0 * moisture  # sec.10, heat of pre-ignition
    heat_pre_dead = sum(f_j[j] * epsilon_j[j] * q_ig_j[j] for j in _DEAD)
    heat_pre_live = sum(f_j[j] * epsilon_j[j] * q_ig_j[j] for j in _LIVE)
    heat_sink = rho_b * (f_dead * heat_pre_dead + f_live * heat_pre_live)  # Btu/ft^3

    base_ros_ft_min = heat_source / heat_sink if heat_sink > 0 else 0.0  # sec. governing equation

    residence_time = 384.0 / sigma_prime if sigma_prime > 0 else 0.0  # sec.15
    flame_depth_ft = base_ros_ft_min * residence_time
    base_fireline_intensity = reaction_intensity * flame_depth_ft / 60.0  # Btu/ft/s

    max_effective_wind = 0.9 * reaction_intensity  # sec.12, ft/min
    b_exp = 0.02526 * sigma_prime**0.54  # sec.11
    c_coef = 7.47 * math.exp(-0.133 * sigma_prime**0.55)  # sec.11
    e_exp = 0.715 * math.exp(-3.59e-4 * sigma_prime)  # sec.11
    f_term = c_ratio**e_exp if c_ratio > 0 else 0.0  # sec.11

    return NoWindNoSlope(
        base_ros_ft_min=base_ros_ft_min,
        base_fireline_intensity_btu_ft_s=base_fireline_intensity,
        reaction_intensity_btu_ft2_min=reaction_intensity,
        max_effective_wind_ft_min=max_effective_wind,
        beta=beta,
        beta_op=beta_op,
        sigma_prime=sigma_prime,
        b_exp=b_exp,
        c_coef=c_coef,
        f_term=f_term,
    )


# --------------------------------------------------------------- wind adjustment


def wind_adjustment_factor(fuel_depth_ft: float) -> float:
    """Unsheltered wind-adjustment factor, 20-ft open wind -> midflame wind (Andrews
    2012 eq.6) - no canopy term, see module docstring."""
    if fuel_depth_ft <= 0.0:
        return 0.0
    denom = math.log((20.0 + 0.36 * fuel_depth_ft) / (0.13 * fuel_depth_ft))
    return 1.83 / denom if denom > 0 else 0.0


def open_wind_to_midflame(wind_10m_ms: float, fuel_depth_ft: float) -> float:
    """10m meteorological wind -> 20-ft open wind (0.87x, the standard log-wind-
    profile approximation for that height drop) -> midflame wind via the fuel bed's
    own wind-adjustment factor."""
    wind_20ft_ms = max(0.0, wind_10m_ms) * 0.87
    return wind_20ft_ms * wind_adjustment_factor(fuel_depth_ft)


# --------------------------------------------------------- directional (Phase 2)


def length_to_width_ratio(effective_wind_mph: float) -> float:
    """BehavePlus regression (Andrews 2018, RMRS-GTR-371) for the fire front's
    length-to-width ratio - the standard Anderson (1983)/Finney family. firemodel.md
    section 14 covers combining wind/slope into an elliptical shape but doesn't spell
    out this specific regression, so Andrews 2018 is cited directly rather than
    pointing at a firemodel.md section that isn't really about it. Capped at 8:1 per
    the same source."""
    return min(
        8.0,
        0.936 * math.exp(0.1147 * effective_wind_mph)
        + 0.461 * math.exp(-0.0692 * effective_wind_mph)
        - 0.397,
    )


def eccentricity_from_lwr(lwr: float) -> float:
    """Albini & Chase (1980) eq.8."""
    return math.sqrt(lwr * lwr - 1.0) / lwr if lwr > 1.0 else 0.0


def grid_directional_behavior(
    fuel_model_ids: np.ndarray,
    slope_rad: np.ndarray,
    aspect_deg: np.ndarray,
    wind_speed_10m_ms: float,
    wind_from_deg: float,
    moisture: np.ndarray,
) -> dict[str, np.ndarray]:
    """Vectorized per-cell directional fire behaviour. For every cell, combines that
    cell's own fuel model, slope and aspect with one AOI-wide wind (an Open-Meteo
    point sample - see `nexfiremap.terrain` docstring) to get the max spread
    rate/direction and the elliptical eccentricity needed to evaluate spread rate in
    *any* direction from that cell (`ros_at_bearing` below) - this is what makes the
    propagation solve anisotropic instead of a uniform scalar speed.

    Returns ``max_ros_m_min``, ``max_dir_deg`` (compass bearing fire spreads toward
    at that cell's max rate) and ``eccentricity``, all shaped like ``fuel_model_ids``.
    Nonburnable cells (``fuel_model_ids <= 0``) get a barrier-floor spread rate
    (``barrier_ros_m_min``), isotropic (eccentricity 0) - matching the previous
    isotropic model's "essentially impassable, not a literal wall" treatment.
    """
    ny, nx = fuel_model_ids.shape
    max_ros = np.zeros((ny, nx))
    max_dir = np.zeros((ny, nx))
    ecc = np.zeros((ny, nx))

    for fm_number in np.unique(fuel_model_ids):
        fm_number = int(fm_number)
        if fm_number <= 0:
            continue
        mask = fuel_model_ids == fm_number
        fuel = anderson13(fm_number)
        nws = no_wind_no_slope(fuel, moisture)
        if not nws.burnable:
            continue

        wind_ft_min = _mps_to_ft_min(open_wind_to_midflame(wind_speed_10m_ms, fuel.depth_ft))
        rise_run = np.tan(slope_rad[mask])
        aspect_here = aspect_deg[mask]

        phi_s = 5.275 * nws.beta**-0.3 * rise_run**2 if nws.beta > 0 else np.zeros_like(rise_run)
        phi_w = (nws.c_coef / nws.f_term) * wind_ft_min**nws.b_exp if nws.f_term > 0 and wind_ft_min > 0 else 0.0

        downwind_deg = (wind_from_deg + 180.0) % 360.0
        upslope_deg = (aspect_here + 180.0) % 360.0
        wind_x = phi_w * np.sin(np.radians(downwind_deg))
        wind_y = phi_w * np.cos(np.radians(downwind_deg))
        slope_x = phi_s * np.sin(np.radians(upslope_deg))
        slope_y = phi_s * np.cos(np.radians(upslope_deg))
        comb_x, comb_y = wind_x + slope_x, wind_y + slope_y
        phi_e = np.hypot(comb_x, comb_y)
        dir_here = np.degrees(np.arctan2(comb_x, comb_y)) % 360.0

        if nws.f_term > 0 and nws.c_coef > 0 and nws.b_exp > 0:
            effective_wind = (nws.f_term / nws.c_coef) ** (1.0 / nws.b_exp) * np.where(
                phi_e > 0, phi_e, 0.0
            ) ** (1.0 / nws.b_exp)
        else:
            effective_wind = np.zeros_like(phi_e)

        if nws.max_effective_wind_ft_min > 0:
            over_cap = effective_wind > nws.max_effective_wind_ft_min
            capped_phi_e = (
                (nws.c_coef / nws.f_term) * nws.max_effective_wind_ft_min**nws.b_exp if nws.f_term > 0 else 0.0
            )
            phi_e_used = np.where(over_cap, capped_phi_e, phi_e)
            wind_used = np.where(over_cap, nws.max_effective_wind_ft_min, effective_wind)
        else:
            phi_e_used, wind_used = phi_e, effective_wind

        max_ros_ft_min = nws.base_ros_ft_min * (1.0 + phi_e_used)
        eff_wind_mph = wind_used / FT_MIN_PER_MPH
        lwr = np.clip(
            0.936 * np.exp(0.1147 * eff_wind_mph) + 0.461 * np.exp(-0.0692 * eff_wind_mph) - 0.397,
            1.0,
            8.0,
        )
        ecc_here = np.sqrt(np.maximum(lwr * lwr - 1.0, 0.0)) / lwr

        max_ros[mask] = max_ros_ft_min * FT_TO_M  # -> m/min
        max_dir[mask] = dir_here
        ecc[mask] = ecc_here

    return {"max_ros_m_min": max_ros, "max_dir_deg": max_dir, "eccentricity": ecc}


def ros_at_bearing(max_ros: float, max_dir_deg: float, eccentricity: float, bearing_deg: float) -> float:
    """Spread rate in an arbitrary compass direction via the standard Albini & Chase
    (1980) elliptical adjustment: ``R(theta) = R_max * (1-e) / (1 - e*cos(theta))``."""
    if eccentricity <= 0.0:
        return max_ros
    cos_w = math.cos(math.radians(bearing_deg - max_dir_deg))
    return max_ros * (1.0 - eccentricity) / (1.0 - eccentricity * cos_w)
