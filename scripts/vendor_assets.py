#!/usr/bin/env python
"""Download the map libraries into nexfiremap/static/vendor.

Vendoring keeps NexFiremap working without a CDN (and without internet at all,
if you already have cached detections). Re-run after changing a version below.

Every asset is pinned to an exact version (MapLibre used to float on just a
major version, "4" - unlike everything else here, that meant re-running this
script months apart could silently vendor a different minor/patch with no
record of which one) and verified against a known-good SHA-256 after
download. A mismatch means either the pinned version changed upstream
without this script's hash being updated (expected right after a version
bump - re-run with --update-hashes once you've reviewed the new content), or
the same version's content actually changed at the same URL (unexpected -
investigate before trusting it, don't just silently vendor it).

    python scripts/vendor_assets.py
    python scripts/vendor_assets.py --update-hashes   # after a deliberate version bump
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "nexfiremap" / "static" / "vendor"

LEAFLET = "1.9.4"
MARKERCLUSTER = "1.5.3"
HEAT = "0.2.0"
MAPLIBRE = "4.7.1"  # exact patch, matching every other pin here (used to float on "4" alone)
PROJ4 = "2.15.0"  # UTM/national-grid coordinate conversion (coords.js) - MIT
MGRS = "2.2.0"  # Military Grid Reference System conversion (coords.js) - MIT, same maintainers as proj4js

# SHA-256 of each currently-vendored file, keyed by its target path below -
# the known-good baseline this script verifies every download against.
KNOWN_HASHES: dict[str, str] = {
    "leaflet/leaflet.js": "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a",
    "leaflet/leaflet.css": "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6",
    "leaflet/images/marker-icon.png": "574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437",
    "leaflet/images/marker-icon-2x.png": "00179c4c1ee830d3a108412ae0d294f55776cfeb085c60129a39aa6fc4ae2528",
    "leaflet/images/marker-shadow.png": "264f5c640339f042dd729062cfc04c17f8ea0f29882b538e3848ed8f10edb4da",
    "leaflet/images/layers.png": "1dbbe9d028e292f36fcba8f8b3a28d5e8932754fc2215b9ac69e4cdecf5107c6",
    "leaflet/images/layers-2x.png": "066daca850d8ffbef007af00b06eac0015728dee279c51f3cb6c716df7c42edf",
    "markercluster/leaflet.markercluster.js": "1e4e1d22972a3926f48598e0caf14e3fe7049835d428a344fed4f9e3665b3508",
    "markercluster/MarkerCluster.css": "614dea0a98ff3f4ead74f04918f6b1d1b9ba435c25b5fc23b21a394d1e3e4d87",
    "markercluster/MarkerCluster.Default.css": "61258232d98d64dc2a7b1e02130d67421bc5b9bda5994eef70228ff97570c170",
    "heat/leaflet-heat.js": "eb952aae5806a1102729f291bab887dde783ace859819a354827a776e73e486a",
    "maplibre/maplibre-gl.js": "be9633c4d870e26fb37f1cfe5c5a77181667114003ea16207ac7850d8da8add1",
    "maplibre/maplibre-gl.css": "576b085fdd9487a65a19215328c1e086c07ce5bf6da09b666b3806d3d008dae9",
    "proj4/proj4.js": "5c73f2719b0c33c8d8e709fc3d71056b39623d5f9182fb06a7b8e5173cfd8651",
    "mgrs/mgrs.min.js": "4a220d1f198582c451d565ea7a686e0be5118a721291202f9dc095cdbc1f6cf4",
}

ASSETS: list[tuple[str, str]] = [
    (f"https://unpkg.com/leaflet@{LEAFLET}/dist/leaflet.js", "leaflet/leaflet.js"),
    (f"https://unpkg.com/leaflet@{LEAFLET}/dist/leaflet.css", "leaflet/leaflet.css"),
    (
        f"https://unpkg.com/leaflet@{LEAFLET}/dist/images/marker-icon.png",
        "leaflet/images/marker-icon.png",
    ),
    (
        f"https://unpkg.com/leaflet@{LEAFLET}/dist/images/marker-icon-2x.png",
        "leaflet/images/marker-icon-2x.png",
    ),
    (
        f"https://unpkg.com/leaflet@{LEAFLET}/dist/images/marker-shadow.png",
        "leaflet/images/marker-shadow.png",
    ),
    (
        f"https://unpkg.com/leaflet@{LEAFLET}/dist/images/layers.png",
        "leaflet/images/layers.png",
    ),
    (
        f"https://unpkg.com/leaflet@{LEAFLET}/dist/images/layers-2x.png",
        "leaflet/images/layers-2x.png",
    ),
    (
        f"https://unpkg.com/leaflet.markercluster@{MARKERCLUSTER}/dist/leaflet.markercluster.js",
        "markercluster/leaflet.markercluster.js",
    ),
    (
        f"https://unpkg.com/leaflet.markercluster@{MARKERCLUSTER}/dist/MarkerCluster.css",
        "markercluster/MarkerCluster.css",
    ),
    (
        f"https://unpkg.com/leaflet.markercluster@{MARKERCLUSTER}/dist/MarkerCluster.Default.css",
        "markercluster/MarkerCluster.Default.css",
    ),
    (
        f"https://unpkg.com/leaflet.heat@{HEAT}/dist/leaflet-heat.js",
        "heat/leaflet-heat.js",
    ),
    (
        f"https://unpkg.com/maplibre-gl@{MAPLIBRE}/dist/maplibre-gl.js",
        "maplibre/maplibre-gl.js",
    ),
    (
        f"https://unpkg.com/maplibre-gl@{MAPLIBRE}/dist/maplibre-gl.css",
        "maplibre/maplibre-gl.css",
    ),
    (
        f"https://unpkg.com/proj4@{PROJ4}/dist/proj4.js",
        "proj4/proj4.js",
    ),
    (
        f"https://unpkg.com/mgrs@{MGRS}/dist/mgrs.min.js",
        "mgrs/mgrs.min.js",
    ),
]


def main() -> int:
    """Downloads every asset in ``ASSETS``, verifying each against
    ``KNOWN_HASHES`` before writing it to disk. With ``--update-hashes``, a
    mismatch is treated as an accepted new baseline (printed at the end for
    pasting back into ``KNOWN_HASHES``) instead of being rejected - meant
    for the deliberate-version-bump workflow described in the module
    docstring, not routine runs."""
    update_hashes = "--update-hashes" in sys.argv[1:]
    failures = 0
    mismatches: list[str] = []
    new_hashes: dict[str, str] = {}

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        for url, target in ASSETS:
            path = VENDOR / target
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                response = client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                print(f"  FAILED {target}: {exc}")
                failures += 1
                continue

            digest = hashlib.sha256(response.content).hexdigest()
            expected = KNOWN_HASHES.get(target)
            if expected is None:
                print(f"  ok   {target} ({len(response.content) / 1024:.1f} KiB) - no pinned hash yet")
                new_hashes[target] = digest
            elif digest != expected:
                if update_hashes:
                    print(f"  ok   {target} ({len(response.content) / 1024:.1f} KiB) - hash updated")
                    new_hashes[target] = digest
                else:
                    print(
                        f"  MISMATCH {target}: expected {expected[:12]}..., got {digest[:12]}... "
                        "- not writing this file. If this is a deliberate version bump, review the "
                        "content then re-run with --update-hashes. If not, treat this as suspicious."
                    )
                    mismatches.append(target)
                    continue
            else:
                print(f"  ok   {target} ({len(response.content) / 1024:.1f} KiB) - hash verified")

            path.write_bytes(response.content)

    if new_hashes:
        print("\nUpdated/new hashes to paste into KNOWN_HASHES:")
        for target, digest in new_hashes.items():
            print(f'    "{target}": "{digest}",')

    if failures or mismatches:
        if mismatches:
            print(f"\n{len(mismatches)} asset(s) failed integrity verification (not written).")
        if failures:
            print(f"\n{failures} asset(s) failed to download.")
        return 1
    print(f"\nVendored {len(ASSETS)} assets into {VENDOR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
