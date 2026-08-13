Yes. The important point is that these are not six independent equations you can simply evaluate in sequence. They form a **coupled fire-behavior system**, and the operational versions used by FlamMap/FARSITE contain implementation choices that differ in several places from the literal equations in the original papers.

The list you quoted is specifically the FlamMap/FARSITE model stack. FlamMap calculates potential fire behavior independently at each landscape pixel, while FARSITE adds a spatial/temporal propagation layer around essentially the same family of fire-behavior calculations. ([US Forest Service][1])

The architecture I would implement is:

```text
 LANDSCAPE
 ┌─────────────────────────────────┐
 │ fuel model │
 │ slope / aspect / elevation │
 │ canopy cover / height │
 │ canopy base height (CBH) │
 │ canopy bulk density (CBD) │
 └─────────────────────────────────┘
 │
 ▼
WEATHER ───────► Local forcing / conditioning
T, RH, rain, │
solar, wind │
 ▼
 Nelson dead-fuel
 moisture model
 │
 ▼
 1h / 10h / 100h moisture
 │
 ▼
 Rothermel 1972 surface
 fire behavior kernel
 │
 ┌────────┴───────────┐
 │ │
 ▼ ▼
 surface ROS/intensity FM10 pseudo-fuel run
 │ for crown prediction
 │ │
 ▼ ▼
 Van Wagner 1977 Rothermel 1991
 crown initiation potential crown ROS
 │ │
 └────────┬───────────┘
 ▼
 Finney 1998 OR
 Scott & Reinhardt 2001
 transition model
 │
 ┌──────────┼─────────┐
 ▼ ▼ ▼
 fire type final ROS CFB/intensity
 │
 ▼
 Albini spotting
 │
 ▼
 spot ignitions
```

The Forest Service's current FlamMap documentation explicitly identifies this model combination and says that Nelson conditioning accounts for local elevation, slope, aspect, canopy cover/shading, and weather. ([US Forest Service][1])

---

# 1. The unit problem comes first

This is probably the single most important software-engineering decision.

The original Rothermel 1972 correlation is defined using the historical FIREMOD/BEHAVE units:

| Quantity | Rothermel internal unit |
| ------------------ | ------------------------: |
| spread rate | ft/min |
| wind | ft/min |
| fuel depth | ft |
| loading | lb/ft² |
| SAVR (σ) | ft⁻¹ |
| particle density | lb/ft³ |
| heat content | Btu/lb |
| reaction intensity | Btu/ft²/min |
| moisture | fraction of oven-dry mass |

Rothermel's summary equation and empirical constants were fitted in that system. ([US Forest Service][2])

Do **not** simply put SI values into formulas containing constants such as `7.47`, `0.02526`, `133`, `138`, `495`, etc.

I would expose an SI API:

```python
result = surface_fire(
 wind_mps=...,
 slope_rise_run=...,
 fuel=...,
 moisture=...
)
```

but convert to canonical Rothermel units inside the kernel. Then convert results back to SI.

That will make reproducing Behave/FlamMap substantially easier.

Also note that operational U.S. implementations don't use the completely untouched 1972 equations. FIREMOD/Behave/FARSITE-family implementations incorporated corrections/adjustments documented by Albini in 1976. A transparent modern implementation, Pyretechnics, explicitly adopts the Rothermel model with those Albini adjustments specifically to remain compatible with government fire models. ([pyregence.github.io][3])

So I would support:

```python
RothermelVariant.ORIGINAL_1972
RothermelVariant.FIREMOD_ALBINI_1976
```

and use the second one for FlamMap-like behavior.

---

# 2. Rothermel 1972 surface fire model

## Governing equation

The fundamental rate-of-spread equation is

[
R=
\frac{
I_R \xi (1+\phi_W+\phi_S)
}{
\rho_b \epsilon Q_{ig}
}
]

where

[
R_0 =
\frac{I_R\xi}
{\rho_b\epsilon Q_{ig}}
]

is the no-wind/no-slope spread rate.

The terms represent:

| Symbol | Meaning |
| ---------- | ------------------------ |
| (I_R) | reaction intensity |
| (\xi) | propagating flux ratio |
| (\phi_W) | wind multiplier |
| (\phi_S) | slope multiplier |
| (\rho_b) | oven-dry bulk density |
| (\epsilon) | effective heating number |
| (Q_{ig}) | heat of pre-ignition |

This is the model's central energy-balance statement: the numerator represents heat reaching unburned fuel. The denominator represents energy required to ignite a unit volume of that fuel. ([US Forest Service][2])

## Fuel classes

For a FlamMap-compatible model, don't represent the surface bed with one average fuel.

Represent at least:

```python
DEAD_1H
DEAD_10H
DEAD_100H
DEAD_HERBACEOUS
LIVE_HERBACEOUS
LIVE_WOODY
```

A useful data structure is:

```python
from dataclasses import dataclass
import numpy as np

@dataclass(frozen=True)
class FuelModel:
 depth_ft: float

 load_lbft2: np.ndarray # w_o[j]
 savr_ft_inv: np.ndarray # sigma[j]
 density_lbft3: np.ndarray # rho_p[j]
 heat_btu_lb: np.ndarray # h[j]

 total_mineral: np.ndarray # S_T[j]
 effective_mineral: np.ndarray # S_e[j]

 dead_mext: float
 dynamic: bool = False
```

The important part is preserving **individual size classes** until the appropriate surface-area-weighting stage.

---

# 3. Surface-area weighting

For each particle class (j),

[
A_j = \frac{\sigma_j w_{o,j}}{\rho_{p,j}}
]

is proportional to its surface area.

Separate classes into dead and live categories.

Within each category:

[
f_{ij} = \frac{A_{ij}}{\sum_j A_{ij}}
]

and between dead/live categories:

[
f_i=\frac{A_i}{A_T}
]

The characteristic fuel-bed SAVR becomes

[
\sigma'
=======

\sum_i f_i
\sum_j f_{ij}\sigma_{ij}
]

This is why doing a naive load-weighted average of SAVR gives wrong results.

A modern FIREMOD-compatible implementation performs exactly this category/size-class weighting. ([pyregence.github.io][3])

---

# 4. Packing ratio

Compute dry bulk density:

[
\rho_b = \frac{\sum_j w_{o,j}}{\delta}
]

where (\delta) is fuel-bed depth.

For heterogeneous particle densities:

[
\beta
=====

\frac{1}{\delta}
\sum_j
\frac{w_{o,j}}{\rho_{p,j}}
]

The optimum packing ratio is

[
\beta_{op}
==========

3.348(\sigma')^{-0.8189}
]

which is one of the fundamental empirical relationships in the model. ([US Forest Service][2])

---

# 5. Moisture damping

For each dead/live category first determine representative moisture (M_f) and moisture of extinction (M_x).

Set

[
r_M = \min\left(1,\frac{M_f}{M_x}\right)
]

then

[
\eta_M
======

1
-2.59r_M
+5.11r_M^2
-3.52r_M^3
]

Once (M_f\ge M_x), that fuel category no longer contributes to flaming spread.

Mineral damping is

[
\eta_S=0.174 S_e^{-0.19}
]

for nonzero effective mineral content. ([US Forest Service][2])

A clean Python helper is:

```python
def moisture_damping(mf: float, mx: float) -> float:
 if mx <= 0.0:
 return 0.0

 r = min(1.0, max(0.0, mf / mx))
 return max(
 0.0,
 1.0 - 2.59*r + 5.11*r*r - 3.52*r*r*r
 )


def mineral_damping(se: float) -> float:
 if se <= 0.0:
 return 1.0
 return 0.174 * se**-0.19
```

Keep moisture internally as a **fraction**:

```python
6% -> 0.06
90% -> 0.90
```

except in equations such as Van Wagner's crown-initiation formula where the model explicitly requires percent.

---

# 6. Live moisture of extinction

This is easy to omit and can noticeably change results.

The FIREMOD-compatible form derived from Rothermel Eq. 88 is

[
M_x^l =
\max
\left[
M_x^d,,
2.9W'
\left(
1-\frac{M_f^d}{M_x^d}
\right)-0.226
\right]
]

with

[
W'
==

\frac{
\sum_{j\in D}w_{o,j}e^{-138/\sigma_j}
}{
\sum_{j\in L}w_{o,j}e^{-500/\sigma_j}
}
]

and

[
M_f^d =
\frac{
\sum_{j\in D} w_{o,j}M_{f,j}e^{-138/\sigma_j}
}{
\sum_{j\in D} w_{o,j}e^{-138/\sigma_j}
}.
]

This is the form documented in the FIREMOD-compatible Pyretechnics implementation. ([pyregence.github.io][3])

Treat this as a **fuel-model preparation step**, not something downstream crown code should recompute.

---

# 7. Reaction velocity: an important 1972-vs-operational difference

The maximum reaction velocity is

[
\Gamma'_{\max}
==============

\frac{
(\sigma')^{1.5}
}{
495+0.0594(\sigma')^{1.5}
}.
]

The original Rothermel 1972 exponent parameter was

[
A =
\frac{1}
{4.774(\sigma')^{0.1}-7.27}.
]

That equation appears in the original paper. ([US Forest Service][2])

But the later FIREMOD/Albini-compatible implementation uses

[
\boxed{
A = 133(\sigma')^{-0.7913}
}
]

instead.

Then

[
\Gamma'
=======

\Gamma'*{\max}
\left(\frac{\beta}{\beta*{op}}\right)^A
\exp
\left[
A\left(1-\frac{\beta}{\beta_{op}}\right)
\right].
]

The 1976 adjustment is explicitly documented in an operational implementation. ([pyregence.github.io][3])

For FlamMap compatibility, use the latter.

---

# 8. Reaction intensity

Conceptually:

[
I_R =
\Gamma'
\sum_i
W_{n,i}
h_i
\eta_{M,i}
\eta_{S,i}.
]

Operational FIREMOD weighting uses its class-grouping coefficients (g_{ij}) to obtain net load. A reference implementation computes

[
W_{n,i}
=======

\sum_j
g_{ij}w_{o,ij}(1-S_{T,ij})
]

and then combines the category heat, mineral damping and moisture damping. ([pyregence.github.io][3])

I recommend calculating and exposing all these intermediate variables:

```python
@dataclass
class RothermelDiagnostics:
 sigma_prime: float
 beta: float
 beta_opt: float
 reaction_velocity: float
 reaction_intensity: float
 propagating_flux_ratio: float
 heat_sink: float
 phi_w: float
 phi_s: float
 effective_wind: float
```

This will save enormous debugging time when comparing your implementation against FlamMap/Behave.

---

# 9. Propagating flux ratio

[
\xi=
\frac{
\exp[
(0.792+0.681\sqrt{\sigma'})(\beta+0.1)
]
}{
192+0.2595\sigma'
}.
]

([US Forest Service][2])

---

# 10. Ignition heat sink

For particle class (j):

[
\epsilon_j = e^{-138/\sigma_j}
]

and

[
Q_{ig,j}
========

250+1116M_{f,j}.
]

Again, those constants expect traditional Rothermel units.

For a heterogeneous fuel bed, calculate the surface-area-weighted effective heat sink rather than substituting one average moisture. An operational implementation performs this per category and class. ([pyregence.github.io][3])

---

# 11. Wind factor

The Rothermel wind factor is

[
\phi_W
======

C U^B
\left(\frac{\beta}{\beta_{op}}\right)^{-E}
]

where

[
C =
7.47e^{-0.133(\sigma')^{0.55}}
]

[
B =
0.02526(\sigma')^{0.54}
]

[
E =
0.715e^{-3.59\times10^{-4}\sigma'}.
]

These are visible in the original equation summary. ([US Forest Service][2])

Here (U) is **mid-flame wind**, not the normal meteorological 10-m wind and not automatically the 20-ft open wind.

Your wind subsystem should therefore look like:

```text
10 m / weather-model wind
 ↓
reference-height conversion
 ↓
20-ft open wind
 ↓
wind adjustment factor
 ↓
MIDFLAME WIND
 ↓
Rothermel surface ROS
```

Keep those transformations out of the Rothermel kernel itself.

---

# 12. Effective-wind-speed cap

Operational FIREMOD-family implementations impose:

[
U_{\mathrm{eff,max}}=0.9I_R
]

with (U) in ft/min and (I_R) in its traditional Btu/ft²/min units. ([pyregence.github.io][3])

This is another reason that converting the entire Rothermel kernel to SI by casually replacing units is dangerous.

---

# 13. Slope factor

Rothermel's slope multiplier is

[
\phi_S
======

5.275\beta^{-0.3}\tan^2\theta.
]

([US Forest Service][2])

If your DEM gives percent slope:

[
\tan\theta = \frac{\text{slope percent}}{100}.
]

Do not accidentally take `tan(radians(slope_percent))`.

---

# 14. Wind and slope direction

The simple

[
1+\phi_W+\phi_S
]

expression assumes wind and slope act in the same spread direction.

For raster fire simulation you should **not blindly add them as positive scalars**.

Wind may be:

```text
upslope -> reinforcing
cross-slope -> rotating the maximum-spread vector
downslope -> opposing the slope contribution
```

FARSITE explicitly combines wind and slope spatially and then uses the resulting direction to determine an elliptical wavelet. The underlying Rothermel model predicts essentially a heading rate. FARSITE infers lateral/backing behavior geometrically. ([US Forest Service][4])

That means your design should distinguish:

```python
surface_behavior_heading(...)
```

from:

```python
directional_fire_behavior(...)
```

and from:

```python
propagate_fire_front(...)
```

They are three separate problems.

---

# 15. Fireline intensity

Residence time is commonly calculated as

[
t_r=\frac{384}{\sigma'}
]

minutes.

Flaming-zone depth:

[
D=Rt_r.
]

Fireline intensity:

[
I =
\frac{I_R D}{60}
]

when using the canonical Rothermel units. ([pyregence.github.io][3])

FARSITE gives the corresponding SI formulation as

[
I_b
===

\frac{I_R(12.6)R}
{60\sigma}
]

with its SI-formatted variables. ([US Forest Service][4])

You absolutely need surface fireline intensity because it drives crown-fire initiation.

---

# 16. Van Wagner 1977 crown-fire initiation

This is significantly simpler than Rothermel.

Critical surface intensity is

[
\boxed{
I_{\mathrm{init}}
=================

[
0.010
CBH
(460+25.9FMC)
]^{3/2}
}
]

where

```text
Iinit = kW/m
CBH = m
FMC = foliar moisture in percent dry-weight basis
```

FARSITE uses this exact formulation. ([US Forest Service][4])

Python:

```python
def crown_initiation_intensity(
 canopy_base_height_m: float,
 foliar_moisture_percent: float,
) -> float:
 x = (
 0.010
 * canopy_base_height_m
 * (460.0 + 25.9 * foliar_moisture_percent)
 )
 return max(0.0, x) ** 1.5
```

Crown fire can initiate when:

[
I_{\mathrm{surface}}
\ge
I_{\mathrm{init}}.
]

For example:

```python
CBH = 3.0 m
FMC = 100 %
```

gives approximately

[
I_{\mathrm{init}}\approx875;\mathrm{kW/m}.
]

That's a good unit test.

A crucial semantics issue: canopy base height is supposed to represent an **effective pathway to canopy fuels**. FARSITE notes that ladder fuels can effectively lower the relevant CBH. ([US Forest Service][4])

---

# 17. Converting the initiation threshold to a surface ROS threshold

Since Byram intensity is proportional to spread rate for the same surface fuel state,

[
R_{\mathrm{init}}
=================

R_s
\frac{I_{\mathrm{init}}}{I_s}.
]

Equivalently Scott & Reinhardt use

[
R'_{\mathrm{init}}
==================

\frac{60I_{\mathrm{init}}}
{HPA_{\mathrm{surface}}}.
]

This quantity becomes extremely useful in both the Finney and Scott/Reinhardt transition models.

---

# 18. Criterion for sustained active crown fire

Van Wagner's critical canopy mass-flow condition becomes

[
\boxed{
R'_{\mathrm{active}}
====================

\frac{3.0}{CBD}
}
]

where

```text
CBD = kg/m³
R = m/min
```

The `3.0` represents the critical mass-flow rate converted to per-minute units. ([US Forest Service][4])

So:

```python
def critical_active_crown_ros(cbd_kg_m3: float) -> float:
 if cbd_kg_m3 <= 0.0:
 return float("inf")
 return 3.0 / cbd_kg_m3
```

Example:

```text
CBD = 0.20 kg/m³
```

produces

[
R'_{\mathrm{active}}=15;m/min.
]

Scott & Reinhardt give this exact example. ([US Forest Service][5])

---

# 19. Basic crown-fire classification

Once you have surface intensity and potential crown rate, the conceptual classification is:

| Condition | Classification |
| ---------------------------------------------------- | -------------- |
| (I_s<I_{init}) | surface |
| (I_s\ge I_{init}) and crown rate below (R'_{active}) | passive crown |
| (I_s\ge I_{init}) and crown rate ≥ (R'_{active}) | active crown |

FARSITE omits Van Wagner's independent-crown-fire category from its normal implementation. ([US Forest Service][4])

---

# 20. Rothermel 1991 crown spread

This model is often misunderstood.

It is **not a new physical canopy combustion model**.

Rothermel took observed crown-fire rates and correlated them to results from the existing 1972 surface-spread model using **Fuel Model 10**.

The relationship obtained for average crown-fire spread was approximately

[
\boxed{
R_{\mathrm{active}}
===================

3.34R_{10}
}
]

where (R_{10}) is a Rothermel surface-model calculation using **Fuel Model 10**.

The original analysis used 20-ft wind reduced by a factor of

[
0.4
]

for this FM10 calculation. ([US Forest Service][6])

So your implementation should be conceptually:

```python
def rothermel_1991_crown_ros(
 open_wind_20ft,
 slope,
 current_moisture,
):
 pseudo_midflame_wind = 0.40 * open_wind_20ft

 r10 = rothermel_surface(
 fuel=FUEL_MODEL_10,
 wind=pseudo_midflame_wind,
 slope=slope,
 moisture=current_moisture,
 ).ros

 return 3.34 * r10
```

### Very important

Do **not** do this:

```python
midflame = open_wind * ordinary_surface_WAF
crown_wind = midflame * 0.4
```

The `0.4` in the Rothermel 1991 relation is already the special wind reduction used to construct the crown-fire correlation.

Surface-fire and crown-fire wind processing should therefore be separate paths.

---

# 21. What does the 1.7 number mean?

Rothermel found the observed average crown ROS to be 3.34 times the FM10 prediction. For five wind-driven fires, observed maximum spread rates were approximately **1.7 times the average**. ([US Forest Service][6])

That does **not** mean FlamMap-compatible code should automatically do:

```python
R = 1.7 * 3.34 * R10
```

FARSITE deliberately uses `3.34 * R10` as its crown maximum in this coupling, partly to avoid counting spotting effects twice. ([US Forest Service][4])

So default:

```python
R_active = 3.34 * R10
```

and make the 1.7 multiplier an explicit experimental option if you ever expose it.

Another major limitation: the Rothermel 1991 predicted crown ROS itself is essentially independent of canopy structure. CBD enters the **sustainability threshold**, but not the 3.34 regression. FARSITE explicitly points out this limitation. ([US Forest Service][4])

---

# 22. Finney 1998 crown-fire coupling

Finney's method connects:

```text
surface ROS
Van Wagner initiation
Van Wagner active threshold
Rothermel 1991 crown ROS
crown fraction burned
```

into something usable by FARSITE.

Define

[
R_C^{max}=3.34R_{10}E_i
]

where (E_i\in[0,1]) is a directional ellipse factor. For a head-fire/static pixel calculation:

[
E_i=1.
]

Then Finney computes

[
\boxed{
R_{C,\mathrm{actual}}
=====================

R_s
+
CFB(R_C^{max}-R_s)
}
]

but treats it as active crown spread only if the resulting rate satisfies the active-crown threshold. ([US Forest Service][4])

---

# 23. Finney's crown fraction burned

Finney uses an exponential transition:

[
CFB
===

1-e^{-a_c(R_s-R_o)}
]

where

[
R_o
===

\frac{I_oR_s}{I_b}.
]

That (R_o) is the surface spread rate corresponding to the crown-initiation intensity.

The scale coefficient is chosen so that the transition is tied to the interval between initiation and the active-crown threshold:

[
a_c=
\frac{-\ln(0.1)}
{0.9(R_{AC}-R_o)}.
]

FARSITE documents both this CFB formulation and the resulting discrete transition when actual crown ROS reaches the critical active-crown ROS. ([US Forest Service][4])

A practical implementation:

```python
import math

def finney_cfb(
 surface_ros: float,
 surface_intensity: float,
 critical_intensity: float,
 critical_active_ros: float,
) -> float:

 if surface_intensity <= 0.0:
 return 0.0

 r0 = (
 surface_ros
 * critical_intensity
 / surface_intensity
 )

 if surface_ros <= r0:
 return 0.0

 span = critical_active_ros - r0

 # Degenerate / conditional-crown case needs
 # explicit handling by the calling model.
 if span <= 0.0:
 return 1.0

 a = -math.log(0.1) / (0.9 * span)

 return min(
 1.0,
 max(
 0.0,
 1.0 - math.exp(-a * (surface_ros - r0))
 )
 )
```

I would not silently hide the `span <= 0` case because it corresponds to an interesting crown-fire regime rather than merely a numerical error.

---

# 24. Finney has a discontinuity

This matters for propagation.

FARSITE preserves a relatively rapid/discrete transition:

```text
passive crown
 ↓
candidate crown ROS reaches Rcritical
 ↓
ACTIVE CROWN
```

rather than allowing crown ROS to continuously increase for all intermediate CFB values. Finney explicitly documents this choice. ([US Forest Service][4])

That means when you reproduce FARSITE exactly, your ROS curve may contain a jump.

Do not "fix" it by smoothing unless you're deliberately implementing a different crown model.

---

# 25. Scott & Reinhardt 2001 method

Scott & Reinhardt made the linkage more internally consistent for crown-hazard assessment.

They define two particularly useful wind thresholds:

**Torching Index (TI)** = open 6.1-m/20-ft wind at which crown fire can initiate.

**Crowning Index (CI)** = open wind at which active crown spread can be sustained.

Their conceptual chart is:

```text
wind < TI
 surface

TI <= wind < CI
 passive crown

wind >= CI
 active crown
```

when TI < CI. ([US Forest Service][5])

You don't actually need to hard-code their cumbersome closed-form wind equations.

A much cleaner implementation is numerical root finding.

For TI solve:

[
I_{\mathrm{surface}}(U)-I_{\mathrm{init}}=0.
]

For CI solve:

[
R_{\mathrm{active}}(U)-\frac{3}{CBD}=0.
]

For example:

```python
from scipy.optimize import brentq

def solve_torching_index(surface_behavior_at_open_wind,
 i_crit,
 u_max=100.0):

 def f(u):
 return (
 surface_behavior_at_open_wind(u).intensity_kw_m
 - i_crit
 )

 return brentq(f, 0.0, u_max)
```

This has another advantage: if you later alter wind adjustment or fuel parameters, TI and CI remain internally consistent with *your actual fire kernels*.

---

# 26. Scott & Reinhardt CFB

This is a major difference from Finney.

Scott & Reinhardt explicitly choose a straight-line crown fraction burned because there was no empirical evidence requiring a more elaborate curve. ([US Forest Service][5])

Their equation is

[
\boxed{
CFB=
\frac{
R_{\mathrm{surface}}-R'*{\mathrm{init}}
}{
R'*{SA}-R'_{\mathrm{init}}
}
}
]

clamped to `[0,1]`.

Here

[
R'_{SA}
]

does **not** mean the active crown ROS.

It means:

> the predicted **surface** ROS under the environmental conditions where the potential active-crown ROS reaches the critical active-crown rate.

That is a subtle but critical implementation detail. ([US Forest Service][5])

Then

[
\boxed{
R_{\mathrm{final}}
==================

R_{\mathrm{surface}}
+
CFB
(R_{\mathrm{active}}-R_{\mathrm{surface}})
}
]

([US Forest Service][5])

Implementation:

```python
def scott_reinhardt_cfb(
 r_surface: float,
 r_init_surface: float,
 r_surface_at_ci: float,
) -> float:

 d = r_surface_at_ci - r_init_surface

 if d <= 0.0:
 # conditional-crown / hysteresis regime
 # should be resolved at a higher level
 return 0.0

 return min(
 1.0,
 max(
 0.0,
 (r_surface - r_init_surface) / d
 )
 )


def mixed_crown_ros(r_surface, r_active, cfb):
 return r_surface + cfb * (r_active - r_surface)
```

---

# 27. The "conditional surface fire" problem

Suppose:

[
CI < TI.
]

That means the environment could sustain an active crown fire **before** a locally originating surface fire has enough intensity to ignite the canopy.

Scott & Reinhardt call this a **conditional surface fire**.

If the pixel receives a surface fire:

```text
surface remains surface
```

but if an active crown fire enters the pixel from a neighboring stand:

```text
active crown may continue
```

This is a hysteresis/state-history problem, not something you can infer solely from instantaneous local environmental variables. Scott & Reinhardt explicitly describe this case. ([US Forest Service][5])

Therefore if your project eventually simulates an advancing fire, don't define:

```python
fire_type = f(pixel_environment)
```

Define:

```python
fire_type = f(
 pixel_environment,
 incoming_fire_state,
)
```

This architectural choice matters.

---

# 28. Which crown method should you implement?

For a good Python library, don't choose one globally.

Use a strategy interface:

```python
class CrownTransitionModel(Protocol):
 def evaluate(
 self,
 surface,
 canopy,
 potential_crown,
 ) -> CrownBehavior:
 ...
```

and implement:

```python
Finney1998Transition
ScottReinhardt2001Transition
```

Use Finney if FARSITE compatibility is the primary objective.

Use Scott/Reinhardt when you want its TI/CI/CFB crown-hazard framework.

This mirrors the fact that FlamMap itself exposes Finney or Scott/Reinhardt crown-fire calculation methods. ([US Forest Service][1])

---

# 29. Albini 1979 spotting model

Albini's model is another place where it's important to understand what it actually predicts.

The original work predicts the **maximum potential spotting distance from burning individual trees or small groups of trees**. Albini explicitly said important processes were incompletely understood and that no validation data were then available. ([frames.gov][7])

It isn't a stochastic ember-transport CFD model.

Conceptually:

```text
tree torching
 ↓
flame/plume model
 ↓
largest viable ember lofting height
 ↓
burning / shrinking particle
 ↓
terminal fall velocity
 ↓
horizontal transport through wind profile
 ↓
ground intersection
 ↓
possible spot ignition
```

FARSITE adapts the model operationally.

---

# 30. Albini ember representation

The firebrand is idealized as a cylindrical woody particle.

FARSITE uses approximately:

```text
particle/char density = 0.3 g/cm³
air density = 1.2×10⁻³ g/cm³
drag coefficient = 1.2
burning constant K = 0.0064
```

and assumes vertical lofting during the initial loft phase. ([US Forest Service][4])

Initial terminal velocity is

[
v_0
===

\sqrt{
\frac{
\pi g\rho_sD_p
}{
2C_D\rho_a
}
}.
]

The burn time scale is

[
\tau
====

\frac{
4C_Dv_0
}{
K\pi g
}.
]

([US Forest Service][4])

During descent, terminal velocity decreases approximately linearly as the particle burns:

[
v(t)=v_0\left(1-\frac{t}{\tau}\right).
]

Integrating:

[
z(t)
====

## z(0)

v_0
\left[
t-\frac{t^2}{2\tau}
\right].
]

This differential form is what I would implement. It's less susceptible to transcription mistakes.

---

# 31. Horizontal ember transport

Albini/FARSITE assumes a logarithmic wind profile.

FARSITE expresses horizontal motion as

[
\frac{dX}{dt}
=============

U_H
\frac{\ln(z/z_0)}
{\ln(H/z_0)}.
]

([US Forest Service][4])

This gives you a very straightforward numerical implementation:

```python
def derivatives(t, state, params):
 x, z = state

 v_fall = max(
 0.0,
 params.v0 * (1.0 - t / params.tau)
 )

 u = (
 params.u_canopy
 * np.log(max(z, params.z0) / params.z0)
 / np.log(params.canopy_height / params.z0)
 )

 return (
 u,
 -v_fall,
 )
```

Integrate until `z <= terrain_height(x)`.

For nonflat terrain, perform ground intersection against your DEM rather than simply `z=0`.

---

# 32. An Albini compatibility trap: roughness length

The original Albini treatment identifies a friction-length scale around

[
z_0=0.1313H
]

for its flat-terrain formulation. ([frames.gov][7])

The later FARSITE implementation documents

[
z_0=0.4306H.
]

([US Forest Service][4])

Therefore don't mix the original Albini trajectory constants with the later FARSITE wind-profile implementation without explicitly deciding which variant you're cloning.

I would implement:

```python
class SpottingVariant(Enum):
 ALBINI_1979_ORIGINAL = ...
 FARSITE_1998 = ...
```

and store the wind-profile conventions inside that strategy.

---

# 33. Lofting is actually the harder half

Before descent, FARSITE estimates how high the firebrand can rise in a flame/buoyant plume.

It does that by comparing:

```text
duration of plume/flame structure
vs.
particle travel time to altitude z
```

and solving for the maximum feasible (z).

The buoyant-plume constants include

[
a_x=5.963,
\qquad
b_x=4.563
]

and a dimensionless particle parameter involving

[
B=40.
]

FARSITE documents the plume-travel relationship and the associated simplifying assumptions. ([US Forest Service][4])

Rather than algebraically rearranging every loft-height expression, I recommend implementing it as:

```python
def loft_residual(z, particle, flame):
 return (
 flame_structure_duration(z, flame)
 - particle_travel_time(z, particle, flame)
 )

z_loft = scipy.optimize.brentq(
 loft_residual,
 z_min,
 z_max,
)
```

That follows the physical construction of Albini's model and tends to be more numerically maintainable.

---

# 34. Spotting should be its own subsystem

Don't put it inside your crown ROS function.

Use something like:

```python
@dataclass
class SpotSource:
 x: float
 y: float
 canopy_height_m: float
 tree_species: int | None
 source_intensity_kw_m: float
 source_type: str


@dataclass
class SpotResult:
 distance_m: float
 flight_time_s: float
 landing_x: float
 landing_y: float
 still_burning: bool
```

This lets you later replace deterministic Albini maximum distance with a probability distribution without touching your crown code.

Also remember that an ember landing somewhere is not equivalent to successful ignition. FARSITE separately tests whether it lands outside burned area, on combustible fuel, and remains capable of ignition. ([US Forest Service][4])

---

# 35. Nelson 2000 dead-fuel moisture

This is the most numerically sophisticated part of the stack.

Unlike a simple time-lag exponential model, Nelson models heat and moisture transport through a cylindrical woody fuel stick.

Its forcing inputs are essentially:

```text
air temperature
relative humidity
solar radiation
rainfall
```

and its state includes moisture and temperature inside the stick. ([frames.gov][8])

Slope, aspect and elevation do **not** appear directly as magic coefficients in Nelson's moisture PDE.

Instead:

```text
topography
 ↓
changes local T/RH/solar/shading
 ↓
those local meteorological conditions
 ↓
Nelson stick model
```

That separation is extremely important.

---

# 36. Nelson heat equation

For a radial cylindrical stick:

[
\rho c
\frac{\partial T}{\partial t}
=============================

\frac{1}{r}
\frac{\partial}{\partial r}
\left(
rk\frac{\partial T}{\partial r}
\right).
]

The original 10-h model used a 1.27-cm-diameter ponderosa-pine stick and numerically solves heat and moisture transfer throughout it. Nelson notes that the model can be adapted to sticks of other practical sizes, although precipitation behavior particularly requires care. ([frames.gov][8])

---

# 37. Nelson moisture diffusion

For moisture fraction (m),

[
\frac{\partial m}{\partial t}
=============================

\frac{1}{r}
\frac{\partial}{\partial r}
\left(
rD
\frac{\partial m}{\partial r}
\right)
]

where total diffusivity includes bound-water and vapor components.

Above fiber saturation there is additional capillary/liquid-water behavior.

This is a **stateful PDE**.

You therefore cannot correctly write:

```python
moisture = f(current_weather)
```

You need:

```python
new_state = f(
 previous_state,
 weather_since_previous_state
)
```

That's exactly why FlamMap describes each fuel particle as retaining a moisture/temperature "memory." ([owfflammaphelp62.firenet.gov][9])

---

# 38. Equilibrium moisture boundary

Nelson gives equilibrium moisture fraction as

[
m_e =
U[-\ln(1-H_s)]^u
]

with

[
U=0.1617-0.001419T_s
]

[
u=0.4657-0.003578T_s
]

where (H_s) is fractional relative humidity at the stick surface and (T_s) is surface temperature in °C.

When (H_s=1), Nelson substitutes `0.99` to avoid the logarithmic singularity. ([frames.gov][8])

---

# 39. Surface mass-transfer boundary

Nelson uses

[
-D_s
\left(
\frac{\partial m}{\partial r}
\right)_s
=========

h_m(m_s-m_e).
]

Finite-difference form:

[
-D_s(m_s-m_2)
=============

h_m\Delta r(m_s-m_e).
]

Define the mass-transfer Biot number

[
Bi=
\frac{h_m\Delta r}{D_s}
]

and obtain

[
\boxed{
m_s=
\frac{m_2+Bi,m_e}{1+Bi}
}.
]

These are directly given in Nelson's paper. ([frames.gov][8])

That's a useful boundary update to unit-test independently.

---

# 40. Adsorption and desorption are asymmetric

Nelson uses substantially different surface mass-transfer behavior for drying and wetting.

The original model's effective coefficient is roughly:

```text
desorption ≈ 25 m/h
adsorption ≈ 0.0003 m/h
```

with the parameters tuned empirically. ([frames.gov][8])

So don't simplify the surface boundary to one symmetric exponential equilibrium equation if your objective is actual Nelson behavior.

---

# 41. Rain and dew need explicit boundary states

The moisture surface logic is closer to a state machine than a single formula.

A useful design is:

```python
class SurfaceMoistureRegime(Enum):
 RAIN = 1
 DEW = 2
 FREE_WATER = 3
 ADSORPTION = 4
 DESORPTION = 5
```

The Nelson numerical procedure gives rain priority over the other surface processes, then evaluates the appropriate surface temperature/moisture boundary before advancing the internal stick state. Weather variables are interpolated between observations. ([frames.gov][8])

That control flow matters almost as much as the PDE.

---

# 42. Numerical discretization for Nelson

I would use a conservative radial finite-volume scheme rather than a naive Cartesian second derivative.

For radial cells:

```text
r=0 r=a
 │ │
 ├── cell0 ─ cell1 ... cellN
 │ │
 symmetry boundary
```

At face (i+\tfrac12):

[
J_{i+1/2}
=========

-D_{i+1/2}
\frac{m_{i+1}-m_i}{\Delta r}.
]

The cell update follows the cylindrical shell area/volume:

[
\frac{dm_i}{dt}
===============

*

\frac{
A_{i+1/2}J_{i+1/2}
------------------

A_{i-1/2}J_{i-1/2}
}{
V_i
}.
]

At the center:

[
\frac{\partial m}{\partial r}=0.
]

At the exterior use Nelson's boundary regime.

That gives you mass conservation and avoids the numerical awkwardness of the explicit (1/r) singularity at `r=0`.

---

# 43. Averaging the stick moisture

The output required by Rothermel isn't normally surface moisture.

You want volume-weighted mean moisture:

[
\bar m
======

\frac{2}{a^2}
\int_0^a m(r)r,dr.
]

For a discretized cylinder:

```python
mean_m = np.sum(
 moisture * shell_volume
) / np.sum(shell_volume)
```

Do not use:

```python
np.mean(radial_nodes)
```

because uniformly spaced radial nodes represent unequal cylindrical volumes.

---

# 44. FlamMap's Nelson implementation is not simply the original paper

This matters considerably.

Current FlamMap technical documentation says it uses:

```text
1-h : equilibrium-moisture calculation instead of Nelson
10-h : Nelson, dt = 0.1 h
100-h: modified Nelson, dt = 0.2 h
```

and says Nelson-derived modifications are also associated with other fuel-size classes. ([owfflammaphelp62.firenet.gov][9])

The original Nelson paper itself warns that directly transferring the calibrated 10-h rain formulation to 1-, 100- or 1000-h fuels would be problematic without further work. ([frames.gov][8])

So for **FlamMap reproduction**, follow FlamMap's implementation parameters.

For a **scientific Nelson implementation**, reproduce the original 10-h model first and validate it independently before extending particle sizes.

Those should be two configuration profiles.

---

# 45. Per-pixel topographic conditioning

I'd implement this as a forcing preprocessing layer.

For pixel `(y, x)` and timestamp `t`:

```python
forcing = localize_weather(
 weather=weather[t],
 elevation=dem[y, x],
 slope=slope[y, x],
 aspect=aspect[y, x],
 canopy_cover=canopy[y, x],
 latitude=lat[y, x],
 longitude=lon[y, x],
)
```

Then:

```python
nelson.advance(forcing, dt)
```

not:

```python
nelson.advance(
 slope=slope,
 aspect=aspect,
 ...
)
```

FlamMap documents essentially this architecture: temperature/RH are modified with elevation, incident solar radiation with slope/aspect, shading with canopy/cloud cover, and the resulting local conditions drive representative fuel particles. ([owfflammaphelp62.firenet.gov][9])

---

# 46. Solar radiation on terrain

For your own clean implementation, compute a sun-direction unit vector (\mathbf{s}) and terrain normal (\mathbf{n}).

Direct-beam incidence is approximately

[
\cos i
======

\max(0,\mathbf n\cdot\mathbf s).
]

Then

[
Q_{\mathrm{direct}}
===================

DNI\cos i.
]

Add whatever diffuse component your weather/radiation model provides and apply canopy/cloud/horizon shading.

Conceptually:

```python
solar_local = (
 direct_normal_irradiance * incidence
 + diffuse_irradiance
)

solar_local *= canopy_transmittance
solar_local *= terrain_horizon_mask
```

This is a cleaner design than embedding aspect or slope empirical terms in the moisture solver.

Be extremely explicit about aspect convention. GIS aspect is commonly:

```text
0° = north
90° = east
180° = south
270° = west
```

whereas many mathematical formulas measure counter-clockwise from +x.

---

# 47. FlamMap has a clever optimization you should copy

Running a radial moisture PDE independently for every raster pixel and every fuel class can become very expensive.

FlamMap instead builds a **catalogue of representative particles** for combinations of:

```text
elevation category
slope category
aspect category
canopy-cover category
initial moisture
fuel class
```

It evolves those particles through the conditioning period, then interpolates to the actual landscape location. ([owfflammaphelp62.firenet.gov][9])

That's an excellent optimization.

A simple Python version could hash environmental classes:

```python
key = (
 elevation_bin,
 slope_bin,
 aspect_bin,
 canopy_bin,
 initial_moisture_bin,
 timelag,
)

particle = moisture_catalog[key]
```

This could reduce millions of grid-cell simulations to thousands of representative state trajectories.

---

# 48. Suggested Python package architecture

I would structure the project approximately like this:

```text
fire_behavior/
│
├── units.py
├── constants.py
│
├── fuels/
│ ├── model.py
│ ├── anderson13.py
│ └── scott_burgan40.py
│
├── moisture/
│ ├── equilibrium.py
│ ├── nelson.py
│ ├── boundaries.py
│ └── conditioning.py
│
├── surface/
│ ├── rothermel.py
│ ├── weighting.py
│ └── wind.py
│
├── crown/
│ ├── van_wagner.py
│ ├── rothermel1991.py
│ ├── finney1998.py
│ └── scott_reinhardt2001.py
│
├── spotting/
│ └── albini1979.py
│
├── terrain/
│ ├── solar.py
│ ├── wind.py
│ └── weather.py
│
├── propagation/
│ ├── ellipse.py
│ ├── huygens.py
│ └── raster.py
│
└── tests/
 ├── test_rothermel.py
 ├── test_crown.py
 ├── test_nelson.py
 └── test_spotting.py
```

The separation between `behavior` and `propagation` is intentional.

Rothermel/Van Wagner/etc. tell you:

```text
how fast this fire could move here
```

They do not by themselves solve:

```text
when does every pixel ignite?
```

FARSITE wraps those point calculations in a Huygens/elliptical perimeter-growth algorithm. ([US Forest Service][4])

---

# 49. A useful core result object

Don't return just ROS.

Return everything downstream models require:

```python
@dataclass
class SurfaceBehavior:
 ros_m_min: float
 intensity_kw_m: float
 flame_length_m: float

 reaction_intensity: float
 heat_per_area_kj_m2: float

 effective_wind_m_min: float
 max_spread_direction_rad: float


@dataclass
class CrownBehavior:
 fire_type: str

 initiation_intensity_kw_m: float
 critical_active_ros_m_min: float

 potential_active_ros_m_min: float
 crown_fraction_burned: float

 final_ros_m_min: float
 final_intensity_kw_m: float
```

This makes your model chain explicit and testable.

---

# 50. Crown orchestration code

Your high-level calculation can be surprisingly clean:

```python
def calculate_fire_behavior(
 env,
 fuel,
 canopy,
 crown_method,
):
 surface = rothermel_surface(
 fuel=fuel,
 moisture=env.moisture,
 midflame_wind=env.midflame_wind,
 slope=env.slope,
 )

 i_init = crown_initiation_intensity(
 canopy.canopy_base_height_m,
 canopy.foliar_moisture_percent,
 )

 r_active_crit = critical_active_crown_ros(
 canopy.bulk_density_kg_m3
 )

 r10 = rothermel_surface(
 fuel=FUEL_MODEL_10,
 moisture=env.moisture,
 midflame_wind=0.4 * env.open_wind_20ft,
 slope=env.slope,
 ).ros_m_min

 r_active = 3.34 * r10

 return crown_method.evaluate(
 surface=surface,
 critical_intensity=i_init,
 critical_active_ros=r_active_crit,
 potential_active_ros=r_active,
 canopy=canopy,
 environment=env,
 )
```

Notice the two independent Rothermel evaluations:

```text
actual fuel model → surface behavior

FM10 → potential crown behavior
```

That is intentional.

---

# 51. Inputs your raster needs

For a reasonably FlamMap-like implementation, the core raster/static layers are approximately:

| Input | Used by |
| ------------------- | ---------------------------- |
| surface fuel model | Rothermel |
| elevation | moisture/weather |
| slope | Rothermel + solar |
| aspect | solar + wind/slope direction |
| canopy cover | shading + wind adjustment |
| canopy height | wind/spotting |
| canopy base height | Van Wagner initiation |
| canopy bulk density | active-crown threshold |

Weather/state inputs include:

| Input | Used by |
| ------------------------ | --------------------------- |
| temperature | Nelson |
| relative humidity | Nelson |
| precipitation | Nelson |
| cloud/solar radiation | Nelson |
| wind speed | surface/crown/spotting |
| wind direction | directional spread/spotting |
| foliar moisture | crown initiation |
| live herbaceous moisture | Rothermel |
| live woody moisture | Rothermel |

This is why a single `FireParameters` dictionary eventually becomes painful. Typed dataclasses are worth using.

---

# 52. State versus static parameters

I'd explicitly separate:

```python
LandscapeStatic
WeatherForcing
FuelMoistureState
FireBehavior
FirePropagationState
```

because their time semantics are entirely different.

For example:

```python
@dataclass
class LandscapeStatic:
 dem: np.ndarray
 slope: np.ndarray
 aspect: np.ndarray
 fuel_model: np.ndarray
 canopy_cover: np.ndarray
 canopy_height: np.ndarray
 canopy_base_height: np.ndarray
 canopy_bulk_density: np.ndarray
```

while Nelson has evolving state:

```python
@dataclass
class NelsonState:
 temperature: np.ndarray
 moisture: np.ndarray
```

and an advancing wildfire needs yet another state:

```python
@dataclass
class FireState:
 arrival_time: np.ndarray
 fire_type: np.ndarray
 intensity: np.ndarray
```

Don't mix these.

---

# 53. NumPy/Numba strategy

The Rothermel/Van Wagner/Rothermel-91/Scott calculations are mostly algebra and are ideal for NumPy or Numba.

Nelson is the bottleneck because it adds:

```text
pixel × fuel class × radial node × time step
```

dimensions.

I would initially implement an extremely clear scalar reference version:

```python
nelson_reference.py
```

then a vectorized version:

```python
nelson_numpy.py
```

then optionally:

```python
@numba.njit(parallel=True)
def advance_catalog(...):
 ...
```

Never optimize the reference implementation away. It becomes your oracle for the optimized kernel.

---

# 54. Validation tests you really want

I would make these your first regression tests:

| Test | Expected |
| ----------------------------- | ------------------------------ |
| Van Wagner: CBH=3 m, FMC=100% | ~875 kW/m |
| Van Wagner: CBD=0.2 | 15 m/min active threshold |
| Rothermel91: FM10 ROS=5 m/min | crown ROS=16.7 m/min |
| Scott CFB at TI | 0 |
| Scott CFB at CI | 1 |
| Nelson constant environment | convergence toward equilibrium |
| Nelson radial symmetry | zero center flux |
| Nelson mass state | never negative |
| Rothermel zero wind/slope | exactly (R_0) |
| increasing fuel moisture | ROS generally decreases |
| wind=0 | (\phi_W=0) |
| slope=0 | (\phi_S=0) |
| Albini (t=\tau) | terminal velocity reaches zero |

Then make **golden-file tests** against FlamMap/Behave for standard fuel models.

Don't test only the final ROS. Compare:

```text
sigma'
beta
beta_op
Gamma
IR
xi
heat sink
R0
phi_w
phi_s
R
intensity
Iinit
Ractive_crit
R10
CFB
final ROS
```

The first diverging intermediate value tells you where your implementation differs.

---

# 55. The four biggest implementation traps

The technical literature makes four compatibility issues particularly important:

| Trap | Correct approach |
| ---------------------------------------------------------------------- | ----------------------------------------------------- |
| Using SI numbers with imperial Rothermel constants | convert at kernel boundary |
| Implementing literal 1972 (A) while expecting Behave/FlamMap results | use Albini/FIREMOD adjustment |
| Feeding ordinary surface midflame wind into Rothermel-1991 crown model | use FM10 with its prescribed 0.4 × open-wind relation |
| Treating Nelson moisture as instantaneous weather lookup | maintain internal moisture/temperature state |

A fifth one is worth adding: don't confuse Scott & Reinhardt's (R'_{SA}) with the active crown ROS. It is the **surface** ROS at the environmental state corresponding to the Crowning Index. ([US Forest Service][5])

---

# 56. What I would implement first

If the objective is eventually a trustworthy FlamMap-like Python engine, I would build it in this dependency order:

1. **Fuel-model structures and units**
2. **Rothermel surface model, no wind/no slope**
3. wind and slope
4. intensity/HPA
5. Van Wagner thresholds
6. Rothermel 1991 FM10 crown ROS
7. Scott/Reinhardt transition
8. Finney transition
9. Nelson 10-h reference solver
10. topographic conditioning/catalogue
11. Albini spotting
12. only then add 2-D propagation

That order minimizes the number of interacting bugs.

The hardest component mathematically is Nelson. The hardest component to reproduce *exactly* is probably the collection of operational Rothermel/FIREMOD conventions. The hardest component geometrically is the final spread/propagation engine.

And matching the equations numerically doesn't mean you've eliminated their scientific uncertainty: Rothermel 1972 is a steady-state surface-fire model, Rothermel 1991 is an empirical Northern Rockies crown-fire correlation, and Albini 1979 explicitly described large uncertainties in spotting. ([US Forest Service][2])

## Recommended compatibility target

For your project, I'd define one explicit profile called something like:

```python
FLAMMAP_COMPAT = FireModelConfig(
 surface="rothermel_1972_firemod_albini76",
 effective_wind_limit=True,

 crown_initiation="van_wagner_1977",
 crown_ros="rothermel_1991_fm10",
 crown_wind_factor=0.4,

 crown_transition="scott_reinhardt_2001", # selectable
 spotting="albini_1979_farsite",
 
 moisture_1h="equilibrium",
 moisture_10h="nelson_2000",
 moisture_100h="nelson_modified",
)
```

That forces every historical/operational choice to be visible instead of hiding them inside formulas. The current FlamMap documentation confirms the exact overall model family and specifically documents the 1-h equilibrium shortcut plus 10-h/100-h Nelson conditioning. ([US Forest Service][1])

If you want, send me the structure of your existing Python project - or tell me you're starting from scratch - and I can turn this analysis into the **actual Python implementation**, including the Rothermel kernel, fuel-model dataclasses, Van Wagner/Rothermel-91/Scott/Finney modules, Nelson numerical solver, and pytest validation suite.

[1]: https://research.fs.usda.gov/firelab/projects/flammap "https://research.fs.usda.gov/firelab/projects/flammap"
[2]: https://research.fs.usda.gov/download/treesearch/32533.pdf "https://research.fs.usda.gov/download/treesearch/32533.pdf"
[3]: https://pyregence.github.io/pyretechnics/ "https://pyregence.github.io/pyretechnics/"
[4]: https://research.fs.usda.gov/download/treesearch/4617.pdf "https://research.fs.usda.gov/download/treesearch/4617.pdf"
[5]: https://research.fs.usda.gov/download/treesearch/4623.pdf "https://research.fs.usda.gov/download/treesearch/4623.pdf"
[6]: https://research.fs.usda.gov/download/treesearch/26696.pdf "https://research.fs.usda.gov/download/treesearch/26696.pdf"
[7]: https://www.frames.gov/documents/behaveplus/publications/Albini_1979_INT-GTR-056_ocr.pdf "https://www.frames.gov/documents/behaveplus/publications/Albini_1979_INT-GTR-056_ocr.pdf"
[8]: https://www.frames.gov/documents/catalog/spa/nelson_2000.pdf "https://www.frames.gov/documents/catalog/spa/nelson_2000.pdf"
[9]: https://owfflammaphelp62.firenet.gov/Tech_Topics/Tech_Dead_Fuel_Moisture.htm "https://owfflammaphelp62.firenet.gov/Tech_Topics/Tech_Dead_Fuel_Moisture.htm"
