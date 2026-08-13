"""EUMETSAT MTG/FCI "Active Fire Monitoring" - an independent, geostationary
corroboration source for the European/African/Atlantic region, answering
further_plan.md's suggestion of an EUMETSAT LSA SAF-style confirmation
layer (its own "Recommended European data stack" section 1).

**Why this product, not literally LSA SAF**: LSA SAF's classic MSG/SEVIRI
FRP-PIXEL product needs its own separate registration on top of a EUMETSAT
account (see README's "Not built, and why" note - that's exactly the
blocker that kept this out of the original build). Investigated live once
real EUMETSAT Data Store credentials became available: the **EUMETSAT Data
Store** itself (`api.eumetsat.int`) serves an operational, actively-produced
successor product - "Active Fire Monitoring (netCDF) - MTG - 0 degree"
(collection ``EO:EUM:DAT:0682``) from the MTG-I1/FCI instrument, which
answers the same "is there independent satellite confirmation of fire here"
question LSA SAF's product would have, reachable with the credentials this
project already needs to register for anyway. Live-confirmed (2026-08-07):
~10-minute repeat cycle, full-disk coverage from -67.5 to +67.5 degrees
longitude/latitude (Europe, Africa, the Atlantic, the Middle East), ~2km
resolution at nadir, with a `fire_result` classification (no fire / low /
medium / high confidence / missing) and a `fire_probability` (0-1) per pixel
in a 5568x5568 geostationary-projection raster.

**Auth**: OAuth2 client_credentials, `Settings.eumetsat_consumer_key/secret`
(from `EUMETSAT_CONSUMER_KEY`/`EUMETSAT_CONSUMER_SECRET`) exchanged for a
short-lived Bearer token (~37 minutes, live-confirmed - `_get_access_token`
caches it and refreshes a little early, never storing the token itself
since it isn't a durable credential).

**Explicitly scoped limitations**:

* This product reports a fire *classification* and *probability*, not a
  radiative-power (MW) value the way FIRMS does - it's a yes/no-with-
  confidence corroboration signal, not an independent energy cross-check.
* Full-disk coverage stops at +-67.5 degrees longitude/latitude - fires
  outside that window (most of the Americas, Asia-Pacific, Antarctica)
  simply have no EUMETSAT corroboration available, which the API surfaces
  as "no data" rather than a false negative.
* Satellite active-fire products have known false-positive modes over
  water/sun-glint at low confidence - `confidence` is exposed unfiltered
  precisely so callers can choose their own trust threshold rather than
  this module silently deciding for them.
* No canopy/cloud masking beyond what the product's own algorithm already
  applies - a cloud-obscured fire is invisible to this source exactly as it
  would be to any other optical/IR instrument.
"""

from __future__ import annotations

import base64
import io
import logging
import sqlite3
import time as time_module
import zipfile
from typing import Any

import h5py
import httpx
import numpy as np
from rasterio.crs import CRS
from rasterio.warp import transform as warp_transform

from .config import load_settings
from .jobs import JobContext, register_kind

log = logging.getLogger("nexfiremap.eumetsat")

TOKEN_URL = "https://api.eumetsat.int/token"
SEARCH_URL = "https://api.eumetsat.int/data/search-products/1.0.0/os"
DOWNLOAD_URL_TMPL = (
    "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0682/products/{product_id}"
)
COLLECTION_ID = "EO:EUM:DAT:0682"

# fire_result enum, confirmed live against a real product's HDF5 enum type
# definition (0="No fire", 4="Missing-undefined" - both excluded below).
_CONFIDENCE_BY_CLASS = {1: "low", 2: "medium", 3: "high"}

# A 10-minute product is small (~1-1.5MB) regardless of how big the caller's
# bbox is (it's always a full-disk raster) - the cap here is about not
# re-downloading/re-parsing dozens of products per scan, not about area.
MAX_PRODUCTS_PER_SCAN = 24  # ~4 hours of history at the product's own cadence
DEFAULT_LOOKBACK_HOURS = 3.0
# Generous headroom over a real full-disk SEVIRI fire product (typically tens
# of MB) - just a backstop against decompressing an unbounded amount into
# memory from a corrupted or hostile response, not a tuned expectation.
EUMETSAT_MAX_PRODUCT_BYTES = 500 * 1024 * 1024
# How stale the most recently *ingested* product may be before a fresh scan
# is worth queuing - matches the product's own ~10 minute cadence with
# margin for its few-minutes-of-processing latency.
FRESHNESS_WINDOW_S = 20 * 60.0

_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _get_access_token(client: httpx.Client, force_refresh: bool = False) -> str:
    """Exchanges the stored consumer key/secret for a short-lived Bearer
    token (client_credentials grant, live-confirmed ~37 minute lifetime),
    caching it in-process and refreshing a couple of minutes early rather
    than storing the token itself - see module docstring.

    ``force_refresh`` skips the cache even if it looks fresh - used when a
    scan spans long enough to cross the token's real expiry anyway (a slow
    network, a large batch) and a download comes back 401 mid-batch - see
    scan_eumetsat_fires' retry-once-on-401 handling.
    """
    now = time_module.time()
    if not force_refresh and _token_cache["token"] and now < _token_cache["expires_at"] - 120:
        return _token_cache["token"]

    settings = load_settings()
    if not settings.has_eumetsat_key:
        raise RuntimeError(
            "EUMETSAT_CONSUMER_KEY/EUMETSAT_CONSUMER_SECRET not set - register a free account at "
            "https://api.eumetsat.int/api-key/ and add them to .env"
        )
    basic = base64.b64encode(
        f"{settings.eumetsat_consumer_key}:{settings.eumetsat_consumer_secret}".encode()
    ).decode()
    response = client.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError(
            f"EUMETSAT token endpoint returned no access_token (response keys: {sorted(payload)})"
        )
    _token_cache["token"] = access_token
    _token_cache["expires_at"] = now + float(payload.get("expires_in", 1800))
    return _token_cache["token"]


def search_recent_products(
    client: httpx.Client, token: str, start_ts: float, end_ts: float, limit: int = MAX_PRODUCTS_PER_SCAN
) -> list[dict[str, Any]]:
    """Products published in [start_ts, end_ts) for the Active Fire
    Monitoring collection, newest first. A bbox parameter isn't used here -
    every product covers the full disk, so filtering by search-time bbox
    would filter nothing (confirmed live) while pixel-level bbox filtering
    happens after parsing (see `ingest_product`)."""
    from datetime import datetime, timezone

    def iso(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    response = client.get(
        SEARCH_URL,
        params={
            "pi": COLLECTION_ID,
            "dtstart": iso(start_ts),
            "dtend": iso(end_ts),
            "c": min(limit, 500),
            "format": "json",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()

    products = []
    for feat in payload.get("features", []):
        props = feat.get("properties", {})
        date_range = props.get("date", "")
        end_iso = date_range.split("/")[-1] if "/" in date_range else date_range
        try:
            end_dt = datetime.strptime(end_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        download_links = props.get("links", {}).get("data", [])
        if not download_links:
            continue
        products.append(
            {
                "product_id": feat["id"],
                "end_ts": end_dt.timestamp(),
                "download_url": download_links[0]["href"],
            }
        )
    products.sort(key=lambda p: p["end_ts"], reverse=True)  # newest first, done client-side (see below)
    return products[:limit]


def _fire_pixels_from_netcdf(nc_bytes: bytes) -> list[dict[str, float]]:
    """Parses one Active Fire Monitoring netCDF product into a list of
    ``{lat, lon, confidence, probability}`` dicts, one per detected fire
    pixel (fire_result classes 1-3, where class 0 = no fire, 4 = missing/space,
    both excluded). Reprojects from the product's native full-disk
    geostationary grid to lat/lon via the product's own CF grid-mapping
    metadata (satellite height/ellipsoid), live-confirmed against a real
    product rather than assumed from the format spec."""
    try:
        f = h5py.File(io.BytesIO(nc_bytes), "r")
    except OSError as exc:
        raise RuntimeError(
            f"{len(nc_bytes)}-byte .nc member isn't a valid HDF5/netCDF4 file "
            "(product may be truncated or in an unexpected format)"
        ) from exc
    with f:
        fire_result = f["fire_result"][:]
        fire_probability = f["fire_probability"][:]
        x_raw, y_raw = f["x"][:].astype(np.float64), f["y"][:].astype(np.float64)
        x_scale, x_off = f["x"].attrs["scale_factor"][0], f["x"].attrs["add_offset"][0]
        y_scale, y_off = f["y"].attrs["scale_factor"][0], f["y"].attrs["add_offset"][0]
        proj = f["mtg_geos_projection"].attrs
        height = float(proj["perspective_point_height"][0])
        semi_major = float(proj["semi_major_axis"][0])
        semi_minor = float(proj["semi_minor_axis"][0])

    rows, cols = np.where((fire_result >= 1) & (fire_result <= 3))
    if rows.size == 0:
        return []

    x_rad = x_raw[cols] * x_scale + x_off
    y_rad = y_raw[rows] * y_scale + y_off
    # CF geostationary convention: the netCDF's angular x/y (radians) become
    # the projection's metric coordinates once scaled by satellite height.
    xm, ym = x_rad * height, y_rad * height

    src_crs = CRS.from_proj4(
        f"+proj=geos +h={height} +a={semi_major} +b={semi_minor} +lon_0=0 +sweep=y +units=m +no_defs"
    )
    lon, lat = warp_transform(src_crs, CRS.from_epsg(4326), xm.tolist(), ym.tolist())

    out = []
    for i in range(len(rows)):
        lon_i, lat_i = lon[i], lat[i]
        if not (np.isfinite(lon_i) and np.isfinite(lat_i)):
            continue
        prob_raw = int(fire_probability[rows[i], cols[i]])
        probability = prob_raw * 0.01 if 0 <= prob_raw <= 100 else None  # -127 = _FillValue
        out.append(
            {
                "lat": round(float(lat_i), 5),
                "lon": round(float(lon_i), 5),
                "confidence": _CONFIDENCE_BY_CLASS[int(fire_result[rows[i], cols[i]])],
                "probability": probability,
            }
        )
    return out


def ingest_product(
    conn: sqlite3.Connection, client: httpx.Client, token: str, product: dict[str, Any]
) -> int:
    """Downloads one product (a small zip - manifest, quicklook, the netCDF
    itself), extracts every fire pixel *anywhere in the full disk* (not
    clipped to any particular caller's bbox - see module docstring on why),
    and upserts them. Returns the fire-pixel count. Idempotent: re-ingesting
    an already-seen product is a fast no-op via `eumetsat_products`."""
    existing = conn.execute(
        "SELECT fire_pixels FROM eumetsat_products WHERE product_id = ?", (product["product_id"],)
    ).fetchone()
    if existing is not None:
        return existing[0]

    response = client.get(
        product["download_url"], headers={"Authorization": f"Bearer {token}"}, timeout=60.0
    )
    response.raise_for_status()
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            nc_names = [n for n in zf.namelist() if n.lower().endswith(".nc")]
            if not nc_names:
                raise RuntimeError(
                    f"product {product['product_id']}: downloaded zip has no .nc member "
                    f"(contents: {zf.namelist()})"
                )
            # zf.read() decompresses without regard to the member's declared
            # size - fine for a well-behaved upstream, but this is a
            # network response, and a corrupted or hostile one (DNS
            # spoofing on a field network isn't implausible) could still
            # balloon in memory on a resource-constrained laptop. Bounded
            # streaming read, same guard as field_import.py's KMZ handling.
            chunks: list[bytes] = []
            total = 0
            with zf.open(nc_names[0]) as member:
                while True:
                    chunk = member.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > EUMETSAT_MAX_PRODUCT_BYTES:
                        raise RuntimeError(
                            f"product {product['product_id']}: decompressed .nc member exceeds "
                            f"the {EUMETSAT_MAX_PRODUCT_BYTES} byte safety limit"
                        )
                    chunks.append(chunk)
            nc_bytes = b"".join(chunks)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            f"product {product['product_id']}: downloaded content isn't a valid zip "
            f"(got {len(response.content)} bytes, content-type {response.headers.get('content-type')!r})"
        ) from exc

    pixels = _fire_pixels_from_netcdf(nc_bytes)
    now = int(time_module.time())
    conn.execute("BEGIN")
    if pixels:
        conn.executemany(
            "INSERT INTO eumetsat_fires (latitude, longitude, acq_ts, confidence, probability, product_id) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT (product_id, latitude, longitude) DO NOTHING",
            [
                (p["lat"], p["lon"], int(product["end_ts"]), p["confidence"], p["probability"], product["product_id"])
                for p in pixels
            ],
        )
    conn.execute(
        "INSERT INTO eumetsat_products (product_id, end_ts, fire_pixels, fetched_at) VALUES (?,?,?,?) "
        "ON CONFLICT (product_id) DO NOTHING",
        (product["product_id"], int(product["end_ts"]), len(pixels), now),
    )
    conn.commit()
    return len(pixels)


def scan_eumetsat_fires(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Job body: fetch and ingest any Active Fire Monitoring products
    published in the last ``lookback_hours`` that this server hasn't already
    ingested. ``bbox`` is accepted for logging/consistency with the other
    autofetch job kinds but doesn't scope the fetch itself - see module
    docstring on why (every product is full-disk)."""
    end_ts = float(params.get("end_ts") or time_module.time())
    lookback_hours = float(params.get("lookback_hours", DEFAULT_LOOKBACK_HOURS))
    start_ts = end_ts - lookback_hours * 3600.0

    conn = sqlite3.connect(ctx.db_path, timeout=30.0)
    try:
        with httpx.Client() as client:
            token = _get_access_token(client)
            ctx.report_progress(10, note="authenticated")

            products = search_recent_products(client, token, start_ts, end_ts)
            ctx.report_progress(20, note=f"{len(products)} product(s) found")

            total_pixels = 0
            ingested = 0
            skipped: list[str] = []
            for i, product in enumerate(products):
                try:
                    count = ingest_product(conn, client, token, product)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 401:
                        # The token cache said it was still fresh, but a
                        # long-running scan (many products, a slow network)
                        # can cross the real ~37-minute server-side expiry
                        # anyway - refresh once and retry this one product
                        # rather than failing the rest of the batch behind it.
                        log.info(
                            "EUMETSAT token rejected mid-scan for product %s - refreshing and retrying once",
                            product["product_id"],
                        )
                        token = _get_access_token(client, force_refresh=True)
                        try:
                            count = ingest_product(conn, client, token, product)
                        except Exception:  # noqa: BLE001 - one bad product, keep scanning the rest
                            log.warning(
                                "Skipping product %s after token refresh retry also failed",
                                product["product_id"], exc_info=True,
                            )
                            skipped.append(product["product_id"])
                            continue
                    else:
                        log.warning("Skipping product %s: HTTP %d", product["product_id"], exc.response.status_code)
                        skipped.append(product["product_id"])
                        continue
                except Exception:  # noqa: BLE001 - a malformed/corrupt product shouldn't drop every
                    # remaining, potentially valid, product behind it in this batch.
                    log.warning("Skipping malformed product %s", product["product_id"], exc_info=True)
                    skipped.append(product["product_id"])
                    continue

                total_pixels += count
                ingested += 1
                ctx.report_progress(
                    20 + int(70 * (i + 1) / max(1, len(products))),
                    note=f"product {i + 1}/{len(products)}: {count} fire pixel(s)",
                )

        ctx.report_progress(100, note="done")
        return {
            "products_scanned": ingested,
            "products_skipped": skipped,
            "total_fire_pixels": total_pixels,
            "start_ts": start_ts,
            "end_ts": end_ts,
        }
    finally:
        conn.close()


register_kind("scan_eumetsat_fires", scan_eumetsat_fires)
