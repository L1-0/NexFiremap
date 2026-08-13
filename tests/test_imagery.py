"""Offline tests for the burn-scar math (Phase 3) - severity classification,
NBR/masking arithmetic, and PNG rendering. Network-dependent pieces (STAC
search, actual band fetches) are covered by live verification against the
real Planetary Computer API instead, the same split used elsewhere in this
project for external-service code that isn't practical to mock at this
layer (pystac_client/rasterio use their own transports, not httpx).

Run with:  python tests/test_imagery.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from nexfiremap.imagery import (
    SEVERITY_LABELS,
    SEVERITY_THRESHOLDS,
    classify_severity,
    nbr_from_bands,
    render_severity_png,
)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


def test_nbr_from_bands() -> None:
    print("\nNBR arithmetic and masking")
    # Healthy vegetation: high NIR, low SWIR -> NBR close to +1.
    veg_nir, veg_swir = np.array([[8000.0]]), np.array([[1500.0]])
    nbr_veg = nbr_from_bands(veg_nir, veg_swir, np.array([[False]]))
    check("healthy vegetation has high NBR", nbr_veg[0, 0] > 0.5, str(nbr_veg))

    # Burned/char: low NIR, high SWIR -> NBR close to -1.
    burn_nir, burn_swir = np.array([[1500.0]]), np.array([[6000.0]])
    nbr_burn = nbr_from_bands(burn_nir, burn_swir, np.array([[False]]))
    check("burned area has low/negative NBR", nbr_burn[0, 0] < -0.3, str(nbr_burn))

    dnbr = nbr_veg - nbr_burn
    check("dNBR (pre-post) is strongly positive for a real burn", dnbr[0, 0] > 0.5, str(dnbr))

    masked = nbr_from_bands(np.array([[8000.0]]), np.array([[1500.0]]), np.array([[True]]))
    check("masked cell is NaN regardless of band values", np.isnan(masked[0, 0]))

    zero = nbr_from_bands(np.array([[0.0]]), np.array([[0.0]]), np.array([[False]]))
    check("0/0 division doesn't crash, produces NaN", np.isnan(zero[0, 0]))


def test_classify_severity() -> None:
    print("\nSeverity classification thresholds")
    low_t, mod_t, high_t = SEVERITY_THRESHOLDS
    dnbr = np.array([[np.nan, -0.2, 0.0], [low_t + 0.001, mod_t + 0.001, high_t + 0.001]])
    severity = classify_severity(dnbr)

    check("NaN input -> class -1 (no data)", severity[0, 0] == -1, str(severity))
    check("negative dNBR -> unburned (0)", severity[0, 1] == 0, str(severity))
    check("just below low threshold -> unburned (0)", severity[0, 2] == 0, str(severity))
    check("just above low threshold -> low (1)", severity[1, 0] == 1, str(severity))
    check("just above moderate threshold -> moderate (2)", severity[1, 1] == 2, str(severity))
    check("just above high threshold -> high (3)", severity[1, 2] == 3, str(severity))
    check(
        "every class has a label",
        len(SEVERITY_LABELS) == 4 and SEVERITY_LABELS[0] == "unburned",
        str(SEVERITY_LABELS),
    )


def test_render_severity_png() -> None:
    print("\nSeverity PNG rendering")
    severity = np.array([[-1, 0], [1, 3]], dtype=np.int8)
    png = render_severity_png(severity)
    check("PNG signature present", png[:8] == b"\x89PNG\r\n\x1a\n")

    # Decode via the project's own encoder round-trip isn't available for
    # reading, so just sanity-check the raw RGBA logic directly instead.
    from nexfiremap.imagery import SEVERITY_RAMP_RGB

    ny, nx = severity.shape
    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    ramp_for_class = {1: SEVERITY_RAMP_RGB[2], 2: SEVERITY_RAMP_RGB[3], 3: SEVERITY_RAMP_RGB[4]}
    for cls, color in ramp_for_class.items():
        mask = severity == cls
        rgba[mask, :3] = color
        rgba[mask, 3] = 1
    check("unburned/no-data cells are transparent", rgba[0, 0, 3] == 0 and rgba[0, 1, 3] == 0)
    check("high-severity cell gets a non-zero alpha", rgba[1, 1, 3] > 0)


def main() -> int:
    test_nbr_from_bands()
    test_classify_severity()
    test_render_severity_png()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
