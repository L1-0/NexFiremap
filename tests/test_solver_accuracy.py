"""Arrival-time accuracy of the anisotropic solver against the exact ellipse.

Every other terrain test checks *properties* - arrival times increase with
distance, barriers slow spread, the ensemble spans a range. All of those hold
just as well for a solve that is uniformly wrong, which is how a large
directional bias survived: a graph solve can only travel along its stencil's
directions, so a head fire whose bearing falls between two of them zigzags, and
on a wind-driven ellipse the off-axis spread rate collapses.

In a uniform medium the exact answer is available in closed form - distance
divided by the elliptical spread rate at that bearing - so this file compares
against that rather than against the code's own output.

The bias is one-directional: the solver never reports fire arriving *earlier*
than the ellipse, always later. For a tool whose warnings key off the earliest
modelled arrival, "later than reality" is the dangerous direction.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from nexfiremap import rothermel
from nexfiremap.terrain import (
    BARRIER_ROS_M_MIN,
    _KNIGHT_MIDPOINTS,
    _NEIGHBORS,
    solve_travel_time_anisotropic,
)

N = 161
CENTRE = N // 2
RES_M = 30.0
ROS_MAX = 10.0  # m/min

#: The model's own eccentricity ceiling: `length_to_width_ratio` caps LWR at
#: 8:1, so e = sqrt(8^2 - 1)/8. Testing below this would understate the error -
#: measuring at 0.95 instead of 0.992 reports 2.6x where the truth is 11.5x.
ECC_MAX = math.sqrt(8.0 * 8.0 - 1.0) / 8.0

#: Worst-case tolerance for the head-fire arrival ratio at ECC_MAX. The
#: 8-direction stencil this replaced measured 11.5x; 16 directions measures
#: ~4.5x. The bound is set just above what is actually achieved so that a
#: regression to a coarser stencil fails here rather than silently returning.
MAX_HEAD_RATIO = 5.0

#: Isotropic spread has no preferred direction, so the only error is the
#: digital-distance one: 8.2% for 8 directions, 1.4% for 16.
MAX_ISOTROPIC_RATIO = 1.03


def main() -> None:
    check_stencil_wellformed()
    check_isotropic_distance()
    check_head_fire_arrival()
    check_error_is_stencil_not_resolution()
    check_barriers_are_not_tunnelled()
    print("Solver accuracy checks passed.")


def _solve(direction_deg: float, ecc: float, res_m: float = RES_M,
           ros_field: np.ndarray | None = None) -> np.ndarray:
    ros = np.full((N, N), ROS_MAX) if ros_field is None else ros_field
    return solve_travel_time_anisotropic(
        ros, np.full((N, N), direction_deg), np.full((N, N), ecc),
        [CENTRE], [CENTRE], res_m)


def _head_ratio(wind_deg: float, ecc: float, res_m: float = RES_M, reach: int = 45) -> float:
    """Modelled / exact arrival time at `reach` cells along the head bearing."""
    travel = _solve(wind_deg, ecc, res_m)
    theta = math.radians(wind_deg)
    row = int(round(CENTRE - reach * math.cos(theta)))  # row 0 is north
    col = int(round(CENTRE + reach * math.sin(theta)))
    distance_m = math.hypot((row - CENTRE) * res_m, (col - CENTRE) * res_m)
    head_ros = rothermel.ros_at_bearing(ROS_MAX, wind_deg, ecc, wind_deg)
    return float(travel[row, col]) / (distance_m / (head_ros / 60.0))


def check_stencil_wellformed() -> None:
    """Bearings and lengths must match the offsets they were derived from."""
    assert len(_NEIGHBORS) == 16, f"expected a 16-direction stencil, got {len(_NEIGHBORS)}"
    for dr, dc, bearing, mult in _NEIGHBORS:
        assert abs(mult - math.hypot(dr, dc)) < 1e-12, (dr, dc, mult)
        # Row 0 is north, so north is -row and bearing runs clockwise from it.
        expected = math.degrees(math.atan2(dc, -dr)) % 360.0
        assert abs(bearing - expected) < 1e-9, (dr, dc, bearing, expected)
    assert (-1, 0, 0.0, 1.0) in _NEIGHBORS, "due north must be a step of length 1 at bearing 0"

    bearings = sorted(b for _, _, b, _ in _NEIGHBORS)
    gaps = [b - a for a, b in zip(bearings, bearings[1:])] + [360.0 - bearings[-1] + bearings[0]]
    # 16 evenly-ish spaced directions: the widest gap decides the worst-case
    # off-axis travel, which is what the head-fire error above is made of.
    assert max(gaps) < 30.0, f"widest angular gap is {max(gaps):.1f} degrees"

    # Every knight's move must declare its midpoints, and they must really be
    # the cells it passes between (adjacent to the origin, not to each other).
    knights = [(dr, dc) for dr, dc, _, _ in _NEIGHBORS if abs(dr) + abs(dc) == 3]
    assert len(knights) == 8, knights
    for move in knights:
        assert move in _KNIGHT_MIDPOINTS, move
        for mr, mc in _KNIGHT_MIDPOINTS[move]:
            assert max(abs(mr), abs(mc)) == 1, (move, (mr, mc))
            # ...and each midpoint must be a genuine step toward the target.
            assert mr * move[0] >= 0 and mc * move[1] >= 0, (move, (mr, mc))


def check_isotropic_distance() -> None:
    """With no preferred direction the solve should be near-Euclidean."""
    worst = 0.0
    for wind in (0.0, 15.0, 22.5, 30.0, 45.0):
        ratio = _head_ratio(wind, 0.0)
        assert ratio >= 1.0 - 1e-9, f"solver beat the exact answer at {wind} deg: {ratio}"
        worst = max(worst, ratio)
    assert worst < MAX_ISOTROPIC_RATIO, f"isotropic distance error {worst:.4f} exceeds bound"


def check_head_fire_arrival() -> None:
    """The measurement that matters: head-fire arrival on a wind-driven ellipse.

    Swept across bearings because the error is zero exactly on a stencil
    direction and worst between two - testing only 0 or 45 degrees would report
    a perfect solver.
    """
    worst_ratio, worst_wind = 0.0, None
    for wind in (0.0, 5.0, 11.25, 15.0, 22.5, 30.0, 33.75, 40.0, 45.0):
        ratio = _head_ratio(wind, ECC_MAX)
        # One-directional bias: never early, which is the direction that would
        # make a withdrawal trigger fire too late.
        assert ratio >= 1.0 - 1e-9, f"solver reported fire arriving early at {wind} deg: {ratio}"
        if ratio > worst_ratio:
            worst_ratio, worst_wind = ratio, wind
    assert worst_ratio < MAX_HEAD_RATIO, \
        f"head-fire arrival overestimated {worst_ratio:.2f}x at {worst_wind} deg (bound {MAX_HEAD_RATIO})"

    # On-axis bearings must be essentially exact - if these drift, the ellipse
    # evaluation itself is wrong rather than the stencil being coarse.
    for aligned in (0.0, 45.0, 90.0, 180.0, 270.0):
        assert _head_ratio(aligned, ECC_MAX) < 1.02, aligned


def check_error_is_stencil_not_resolution() -> None:
    """Refining the grid must not be mistaken for a fix.

    This is the diagnostic that identifies the cause. A discretisation error
    shrinks as cells get smaller; a fixed-direction error does not. Recording it
    stops a future reader concluding that a finer AOI resolves the bias.
    """
    ratios = [_head_ratio(22.5, ECC_MAX, res_m=res) for res in (60.0, 30.0, 15.0)]
    assert max(ratios) - min(ratios) < 0.01, \
        f"expected resolution-independent error, got {ratios}"


def check_barriers_are_not_tunnelled() -> None:
    """A one-cell-thick barrier must stay impassable to the long steps.

    The knight's moves added for accuracy span two cells, so without a guard
    they would hop straight over a rasterised control line - the model's
    representation of a line the plan says was built. Fire crossing it in the
    forecast would flatter exactly the plan it is meant to test.
    """
    ros = np.full((N, N), ROS_MAX)
    wall_row = CENTRE - 10
    ros[wall_row, :] = BARRIER_ROS_M_MIN  # a full-width, one-cell-thick line

    travel = solve_travel_time_anisotropic(
        ros, np.full((N, N), 0.0), np.zeros((N, N)), [CENTRE], [CENTRE], RES_M)

    # Everything past the wall must be reachable only at barrier speed, which is
    # orders of magnitude slower than the open field beside it.
    beyond = float(travel[wall_row - 3, CENTRE])
    open_field = float(travel[CENTRE + 13, CENTRE])  # same distance, no wall
    assert np.isfinite(beyond), "the barrier must slow fire, not strand it at infinity"
    assert beyond > open_field * 50, \
        f"fire crossed the one-cell line too cheaply ({beyond:.0f}s vs {open_field:.0f}s open)"


if __name__ == "__main__":
    main()
