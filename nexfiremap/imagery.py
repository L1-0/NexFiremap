"""Optical burn-scar anchoring - further_plan.md's Stage 3.

FIRMS gives timing and thermal evidence. It should not be the sole source
for where a fire's footprint actually ended up. A FIRMS pixel is a
detector's footprint, not a burned-area polygon, and NASA explicitly warns
against treating active-fire detections as burned area. Before/after optical
imagery, differenced via the Normalized Burn Ratio (NBR), is the standard
remote-sensing way to delineate what actually burned - the same logic EFFIS
uses in Europe (thermal hotspots find the event, and higher-resolution optical
imagery delineates and refines the burned perimeter).

Free data: Microsoft Planetary Computer STAC (Sentinel-2 L2A primary,
Landsat C2 L2 fallback) - open STAC search, anonymous SAS-signed asset
access, no account needed (confirmed live). Every read is a windowed/
reprojected pull of just the event's small AOI directly against the
cloud-hosted COG - never a whole-scene download (also confirmed live: ~1-2s
per band for a typical event AOI, not the minutes a full ~800MB scene
download would take).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time as time_module
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
from affine import Affine
from rasterio.warp import Resampling, reproject

from .jobs import JobContext, register_kind
from .geo import grid_geometry
from .rasterpng import encode_png

log = logging.getLogger("nexfiremap.imagery")

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
# Unlike every other network client in this codebase (httpx, all with an
# explicit timeout), neither pystac_client's STAC search nor GDAL's own HTTP
# VFS (which rasterio.open uses for an http(s) href) had one - a stalled
# connection could hang a worker process indefinitely, tying up one of the
# (small, cpu_count-1) job-worker slots with no bound.
STAC_TIMEOUT_S = 30.0
GDAL_HTTP_TIMEOUT_S = "30"
GDAL_HTTP_CONNECTTIMEOUT_S = "15"

# B8A, not B08, for the NIR half of NBR. Both are "near infrared" and B08 is
# the more familiar name, but the severity thresholds this module classifies
# against (SEVERITY_THRESHOLDS below - Key & Benson / USGS FIREMON) were
# derived on *Landsat*, whose NIR band is narrow: SR_B5 spans 851-879 nm,
# centre 865. B8A is the Sentinel-2 band built to match it - 855-875 nm,
# centre 865 - while B08 is a wide 785-900 nm band centred at 842, 22 nm off
# and four times the bandwidth, taking in wavelengths where water vapour
# absorbs. Feeding B08 into a Landsat-calibrated threshold table shifts every
# dNBR value by an amount that varies with atmospheric moisture, which then
# lands scenes in the wrong severity class rather than merely adding noise.
#
# B8A is also 20 m like B12, so the two halves of the ratio now share a native
# resolution instead of resampling a 10 m band against a 20 m one.
S2_NIR_BAND, S2_SWIR_BAND, S2_SCL_BAND = "B8A", "B12", "SCL"
LANDSAT_NIR_BAND, LANDSAT_SWIR_BAND, LANDSAT_QA_BAND = "SR_B5", "SR_B7", "qa_pixel"
LANDSAT_SCALE, LANDSAT_OFFSET = 0.0000275, -0.2

MAX_CLOUD_PCT = 30
SEARCH_WINDOW_DAYS = 90
MIN_VALID_FRACTION = 0.15  # below this, a scene is too cloud/shadow-obscured to use

# SCL classes excluded as unusable: 0 no-data, 1 saturated/defective,
# 3 cloud shadow, 6 water, 8/9 cloud medium/high probability, 10 thin cirrus.
S2_BAD_SCL = {0, 1, 3, 6, 8, 9, 10}

# Landsat C2 L2 QA_PIXEL bits: 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow.
LANDSAT_BAD_QA_MASK = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4)

# Same OKLCH-validated single-hue ramp used throughout for the burn-severity
# legend (dark-tone steps) - a distinct hue from the fire/recency ramps in
# likelihood.py, so "confirmed burn scar" never reads as "recent heat".
SEVERITY_RAMP_RGB = [
    (0x4E, 0x3B, 0xAA),
    (0x60, 0x50, 0xC1),
    (0x73, 0x66, 0xD9),
    (0x87, 0x7C, 0xF1),
    (0x9C, 0x95, 0xFC),
]

SEVERITY_LABELS = ["unburned", "low", "moderate", "high"]
# Standard Key & Benson / USGS FIREMON dNBR class thresholds.
SEVERITY_THRESHOLDS = (0.10, 0.27, 0.66)


# ----------------------------------------------------------------- search


def _catalog() -> pystac_client.Client:
    """Open a STAC client with Planetary Computer's SAS-signing modifier
    pre-attached, so every item this client returns already has its assets
    signed and ready to open directly - callers never need to remember to
    call ``pc.sign`` themselves except for the one-off case in
    ``_asset_href`` below."""
    return pystac_client.Client.open(STAC_URL, modifier=pc.sign_inplace, timeout=STAC_TIMEOUT_S)


def find_best_scene(
    catalog: pystac_client.Client,
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    collection: str,
    max_cloud: float = MAX_CLOUD_PCT,
):
    """Least-cloudy scene covering ``bbox`` within the given date range and
    cloud-cover ceiling, or ``None`` if nothing qualifies. Sorting
    server-side by cloud cover means only the single best candidate needs
    inspecting, not the whole result set."""
    search = catalog.search(
        collections=[collection],
        bbox=list(bbox),
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
        sortby=[{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        limit=5,
    )
    items = list(search.items())
    return items[0] if items else None


def find_pre_post_scenes(
    bbox: tuple[float, float, float, float], first_seen_ts: float, last_seen_ts: float
) -> dict[str, dict[str, Any] | None]:
    """Best pre-fire (baseline) and post-fire scene, Sentinel-2 first,
    falling back to Landsat if nothing clear enough is found."""
    catalog = _catalog()
    first_dt = datetime.fromtimestamp(first_seen_ts, tz=timezone.utc)
    last_dt = datetime.fromtimestamp(last_seen_ts, tz=timezone.utc)
    now = datetime.now(timezone.utc)

    windows = {
        "pre": (first_dt - timedelta(days=SEARCH_WINDOW_DAYS), first_dt - timedelta(days=1)),
        # A few days' buffer after the last detection lets active fire/smoke clear.
        "post": (last_dt + timedelta(days=3), min(now, last_dt + timedelta(days=SEARCH_WINDOW_DAYS))),
    }

    result: dict[str, dict[str, Any] | None] = {}
    for label, (start, end) in windows.items():
        if start >= end:
            result[label] = None
            continue
        item = find_best_scene(catalog, bbox, start, end, "sentinel-2-l2a")
        collection = "sentinel-2-l2a"
        if item is None:
            item = find_best_scene(catalog, bbox, start, end, "landsat-c2-l2")
            collection = "landsat-c2-l2"
        result[label] = {"item": item, "collection": collection} if item else None
    return result


# ------------------------------------------------------------- band reads


def read_band_on_grid(
    href: str,
    geom: dict[str, Any],
    resampling: Resampling = Resampling.bilinear,
    src_nodata: float | None = 0,
) -> np.ndarray:
    """Read one band, reprojected directly onto NexFiremap's shared AOI grid
    (the same one likelihood.py uses) so pre/post scenes - possibly
    different UTM tiles or even sensors - land pixel-for-pixel aligned.

    GDAL's warper only reads the source window the destination actually
    needs, so this stays a small, fast read regardless of the source
    scene's full size (confirmed live: sub-second to ~2s per band).
    """
    west, south, east, north = geom["bbox"]
    nx, ny = geom["nx"], geom["ny"]
    dst_transform = Affine((east - west) / nx, 0, west, 0, -(north - south) / ny, north)
    dst = np.full((ny, nx), np.nan, dtype=np.float32)
    # GDAL's HTTP VFS (what an http(s) href like this one streams through)
    # has no timeout by default - see STAC_TIMEOUT_S's comment above.
    with rasterio.Env(
        GDAL_HTTP_TIMEOUT=GDAL_HTTP_TIMEOUT_S, GDAL_HTTP_CONNECTTIMEOUT=GDAL_HTTP_CONNECTTIMEOUT_S
    ), rasterio.open(href) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs="EPSG:4326",
            resampling=resampling,
            src_nodata=src_nodata,
            dst_nodata=np.nan,
        )
    return dst


def nbr_from_bands(nir: np.ndarray, swir: np.ndarray, bad_mask: np.ndarray) -> np.ndarray:
    """NBR = (NIR - SWIR) / (NIR + SWIR), with cloud/shadow/water/snow
    cells (``bad_mask``) and any non-finite result set to NaN.

    Pure array math, kept separate from the network fetch in
    ``compute_nbr`` so it's directly unit-testable. Sentinel-2's ratio runs
    on raw reflectance DNs - the shared multiplicative scale factor cancels
    in a ratio, so it's skipped - the small per-processing-baseline additive
    offset Sentinel-2 applies is not corrected for, a documented
    simplification with negligible practical effect on the burn signal this
    is used for.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        nbr = (nir - swir) / (nir + swir)
    nbr = nbr.copy()
    nbr[bad_mask] = np.nan
    nbr[~np.isfinite(nbr)] = np.nan
    return nbr


def _asset_href(item: Any, band: str) -> str:
    """``item.assets[band].href``, but with an error a human (or a job's
    "error" field, which is what actually surfaces this) can act on. A bare
    ``KeyError`` here stringifies to just ``"'B8A'"`` with no indication of
    which scene, which collection, or that this means "Planetary Computer's
    asset naming changed" rather than a bug in the request itself."""
    asset = item.assets.get(band)
    if asset is None:
        raise RuntimeError(
            f"Scene {item.id} has no {band!r} asset (available: {sorted(item.assets)}) - "
            "Planetary Computer may have renamed or dropped this band."
        )
    return pc.sign(asset.href)


def compute_nbr(item: Any, collection: str, geom: dict[str, Any]) -> np.ndarray | None:
    """Fetch the bands this scene needs and hand off to nbr_from_bands() -
    returns None if too much of the AOI is cloud/shadow/no-data to be usable."""
    if collection == "sentinel-2-l2a":
        nir = read_band_on_grid(_asset_href(item, S2_NIR_BAND), geom, src_nodata=0)
        swir = read_band_on_grid(_asset_href(item, S2_SWIR_BAND), geom, src_nodata=0)
        scl = read_band_on_grid(
            _asset_href(item, S2_SCL_BAND), geom, resampling=Resampling.nearest, src_nodata=0
        )
        bad = np.isin(np.nan_to_num(scl, nan=0).astype(np.int16), list(S2_BAD_SCL))
    elif collection == "landsat-c2-l2":
        nir_dn = read_band_on_grid(_asset_href(item, LANDSAT_NIR_BAND), geom, src_nodata=0)
        swir_dn = read_band_on_grid(_asset_href(item, LANDSAT_SWIR_BAND), geom, src_nodata=0)
        nir = nir_dn * LANDSAT_SCALE + LANDSAT_OFFSET
        swir = swir_dn * LANDSAT_SCALE + LANDSAT_OFFSET
        qa = read_band_on_grid(
            _asset_href(item, LANDSAT_QA_BAND), geom, resampling=Resampling.nearest, src_nodata=0
        )
        qa_int = np.nan_to_num(qa, nan=0).astype(np.int32)
        bad = (qa_int & LANDSAT_BAD_QA_MASK) != 0
    else:
        raise ValueError(f"unsupported collection: {collection}")

    nbr = nbr_from_bands(nir, swir, bad)
    valid_fraction = float(np.mean(~np.isnan(nbr)))
    if valid_fraction < MIN_VALID_FRACTION:
        log.info("Scene %s too cloud/shadow-obscured (%.0f%% valid)", item.id, valid_fraction * 100)
        return None
    return nbr


# ---------------------------------------------------------------- severity


def classify_severity(dnbr: np.ndarray) -> np.ndarray:
    """dNBR -> integer class 0..3 (unburned/low/moderate/high), -1 for
    no-data, via the fixed USGS threshold table - a lookup, not a model."""
    out = np.full(dnbr.shape, -1, dtype=np.int8)
    valid = ~np.isnan(dnbr)
    low_t, mod_t, high_t = SEVERITY_THRESHOLDS
    out[valid & (dnbr < low_t)] = 0
    out[valid & (dnbr >= low_t) & (dnbr < mod_t)] = 1
    out[valid & (dnbr >= mod_t) & (dnbr < high_t)] = 2
    out[valid & (dnbr >= high_t)] = 3
    return out


def render_severity_png(severity: np.ndarray, max_alpha: int = 200) -> bytes:
    """Class -> RGBA: unburned is fully transparent (nothing to show),
    low/moderate/high step through the violet ramp with rising opacity."""
    ny, nx = severity.shape
    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    # classes 1,2,3 map onto ramp steps 2,3,4 (skip the palest step, reserved
    # for a near-zero/unburned reading so the visible bands read as a clear step series)
    ramp_for_class = {1: SEVERITY_RAMP_RGB[2], 2: SEVERITY_RAMP_RGB[3], 3: SEVERITY_RAMP_RGB[4]}
    alpha_for_class = {1: int(max_alpha * 0.45), 2: int(max_alpha * 0.7), 3: max_alpha}
    for cls, color in ramp_for_class.items():
        mask = severity == cls
        rgba[mask, 0] = color[0]
        rgba[mask, 1] = color[1]
        rgba[mask, 2] = color[2]
        rgba[mask, 3] = alpha_for_class[cls]
    return encode_png(rgba)


# -------------------------------------------------------------- job kind


def analyze_burn_scar(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Job body: find pre/post scenes for an event, compute dNBR, classify
    severity, render and persist. Cached per event like everything else -
    once computed for an event, this never needs to re-run."""
    event_id = int(params["event_id"])
    resolution_m = float(params.get("resolution_m", 20.0))
    pad_deg = float(params.get("pad_deg", 0.05))

    conn = sqlite3.connect(ctx.db_path, timeout=30.0)
    try:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise ValueError(f"no such event: {event_id}")
        cols = [d[0] for d in conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).description]
        event = dict(zip(cols, row))
        conn.close()  # not needed again - imagery fetch is all network + numpy

        bbox = (
            event["bbox_west"] - pad_deg,
            event["bbox_south"] - pad_deg,
            event["bbox_east"] + pad_deg,
            event["bbox_north"] + pad_deg,
        )
        ctx.report_progress(5, note="searching for pre/post imagery")
        scenes = find_pre_post_scenes(bbox, event["first_seen"], event["last_seen"])

        if not scenes["pre"] or not scenes["post"]:
            missing = [k for k in ("pre", "post") if not scenes[k]]
            return {
                "event_id": event_id,
                "ok": False,
                "reason": f"no usable {'/'.join(missing)} scene found (cloud cover or coverage gap)",
            }
        ctx.report_progress(20, note=f"pre={scenes['pre']['item'].id}")

        geom = grid_geometry(bbox, desired_res_m=resolution_m, max_dim=400, min_res_m=10.0)

        nbr_pre = compute_nbr(scenes["pre"]["item"], scenes["pre"]["collection"], geom)
        ctx.report_progress(55, note="pre-fire NBR computed")
        nbr_post = compute_nbr(scenes["post"]["item"], scenes["post"]["collection"], geom)
        ctx.report_progress(80, note="post-fire NBR computed")

        if nbr_pre is None or nbr_post is None:
            return {
                "event_id": event_id,
                "ok": False,
                "reason": "best available scene(s) too cloud/shadow-obscured over this AOI",
            }

        # find_pre_post_scenes picks pre/post independently (each falls
        # back Sentinel-2 -> Landsat on its own), so it's entirely possible
        # for one to be Sentinel-2 and the other Landsat. Differencing
        # across sensors with no bandpass harmonisation between their SWIR
        # channels (the kind HLS applies between the two) can carry a
        # systematic severity bias - undisclosed until now, unlike this
        # module's other documented simplifications (see nbr_from_bands'
        # own DN-scale note).
        cross_sensor = scenes["pre"]["collection"] != scenes["post"]["collection"]
        dnbr = nbr_pre - nbr_post
        severity = classify_severity(dnbr)

        counts = {
            SEVERITY_LABELS[c]: int(np.sum(severity == c)) for c in range(len(SEVERITY_LABELS))
        }
        total_valid = sum(counts.values())
        cell_area_ha = (geom["res_m"] ** 2) / 10_000.0
        burned_ha = sum(v for k, v in counts.items() if k != "unburned") * cell_area_ha

        png = render_severity_png(severity)
        (ctx.result_path / "burn_severity.png").write_bytes(png)
        (ctx.result_path / "meta.json").write_text(
            json.dumps(
                {
                    "pre_scene": scenes["pre"]["item"].id,
                    "pre_date": scenes["pre"]["item"].datetime.isoformat(),
                    "pre_collection": scenes["pre"]["collection"],
                    "post_scene": scenes["post"]["item"].id,
                    "post_date": scenes["post"]["item"].datetime.isoformat(),
                    "post_collection": scenes["post"]["collection"],
                    "cross_sensor": cross_sensor,
                }
            )
        )

        ctx.report_progress(100, note="done")
        return {
            "event_id": event_id,
            "ok": True,
            "bounds": [[bbox[1], bbox[0]], [bbox[3], bbox[2]]],
            "grid": {"nx": geom["nx"], "ny": geom["ny"], "res_m": geom["res_m"]},
            "pre_scene": {
                "id": scenes["pre"]["item"].id,
                "date": scenes["pre"]["item"].datetime.isoformat(),
                "collection": scenes["pre"]["collection"],
            },
            "post_scene": {
                "id": scenes["post"]["item"].id,
                "date": scenes["post"]["item"].datetime.isoformat(),
                "collection": scenes["post"]["collection"],
            },
            # True when pre/post came from different sensors (Sentinel-2 vs
            # Landsat) - no cross-sensor bandpass harmonisation is applied,
            # so severity can carry a systematic bias in that case. See the
            # comment above dnbr's computation.
            "cross_sensor": cross_sensor,
            "severity_counts": counts,
            "valid_cells": total_valid,
            "estimated_burned_hectares": round(burned_ha, 1),
            "files": {"severity": "burn_severity.png", "meta": "meta.json"},
        }
    finally:
        try:
            conn.close()
        except sqlite3.ProgrammingError:
            pass  # already closed above, on the success path


register_kind("analyze_burn_scar", analyze_burn_scar)
