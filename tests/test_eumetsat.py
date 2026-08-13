"""Offline tests for nexfiremap.eumetsat - the netCDF parsing/reprojection
math (the most bug-prone part, tested against a synthetic minimal product
built to match the real product's structure, confirmed live against an
actual downloaded file - see the module docstring), product-search response
parsing, and ingestion idempotency. All network calls are stubbed via
httpx.MockTransport, matching this project's existing convention
(test_fetch.py, test_industrial.py, test_tiles.py) - no live EUMETSAT calls
here; those were done manually against the real API during development.

Run with:  python tests/test_eumetsat.py
"""

from __future__ import annotations

import io
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import h5py
import httpx
import numpy as np

import nexfiremap.eumetsat as eumetsat_module
from nexfiremap.db import Database
from nexfiremap.eumetsat import (
    _fire_pixels_from_netcdf,
    _get_access_token,
    ingest_product,
    scan_eumetsat_fires,
    search_recent_products,
)
from nexfiremap.jobs import JobContext

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


# ------------------------------------------------------ synthetic product

# Real product parameters (satellite height/ellipsoid), live-confirmed
# against an actual downloaded EO:EUM:DAT:0682 product - not guessed.
_HEIGHT = 35_786_400.0
_SEMI_MAJOR = 6_378_137.0
_SEMI_MINOR = 6_356_752.0


def _build_synthetic_netcdf(n: int = 10) -> bytes:
    """A minimal but structurally faithful stand-in for a real Active Fire
    Monitoring product: a small n x n grid with a handful of fire pixels at
    known classes/probabilities, using the same variable names, scale/
    offset encoding, and CF geostationary grid-mapping attributes the real
    product uses (confirmed live)."""
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        fire_result = np.zeros((n, n), dtype=np.int8)
        fire_probability = np.full((n, n), -127, dtype=np.int8)  # _FillValue everywhere by default

        # One pixel of each real class, near the sub-satellite point (x=y=0
        # radians -> nadir, i.e. (0N, 0E)) so the reprojection sanity checks
        # below have an easy expected answer.
        fire_result[5, 5] = 1  # low confidence
        fire_probability[5, 5] = 35
        fire_result[5, 6] = 2  # medium confidence
        fire_probability[5, 6] = 60
        fire_result[6, 5] = 3  # high confidence
        fire_probability[6, 5] = 95
        fire_result[6, 6] = 4  # missing/undefined - must be excluded
        fire_probability[6, 6] = -127

        f.create_dataset("fire_result", data=fire_result)
        f["fire_result"].attrs["grid_mapping"] = "mtg_geos_projection"
        f.create_dataset("fire_probability", data=fire_probability)
        f["fire_probability"].attrs["_FillValue"] = np.array([-127], dtype=np.int8)

        # x/y indices centred so index n//2 (=5 for n=10) sits at radians=0
        # (nadir) - scale_factor/add_offset chosen so raw index 5 -> 0.0 rad.
        idx = np.arange(1, n + 1, dtype=np.int16)
        scale = 5.5887776e-05
        offset = -scale * (n // 2 + 1)
        f.create_dataset("x", data=idx)
        f["x"].attrs["scale_factor"] = np.array([scale], dtype=np.float32)
        f["x"].attrs["add_offset"] = np.array([offset], dtype=np.float32)
        f.create_dataset("y", data=idx)
        f["y"].attrs["scale_factor"] = np.array([-scale], dtype=np.float32)
        f["y"].attrs["add_offset"] = np.array([-offset], dtype=np.float32)

        proj = f.create_dataset("mtg_geos_projection", data=np.array(0, dtype=np.int32))
        proj.attrs["perspective_point_height"] = np.array([_HEIGHT], dtype=np.float32)
        proj.attrs["semi_major_axis"] = np.array([_SEMI_MAJOR], dtype=np.float32)
        proj.attrs["semi_minor_axis"] = np.array([_SEMI_MINOR], dtype=np.float32)
    return buf.getvalue()


def _zip_product(nc_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.xml", "<manifest/>")
        zf.writestr("product.nc", nc_bytes)
    return buf.getvalue()


def test_fire_pixels_from_netcdf() -> None:
    print("\n_fire_pixels_from_netcdf: classification, probability, reprojection")
    nc_bytes = _build_synthetic_netcdf()
    pixels = _fire_pixels_from_netcdf(nc_bytes)

    check("exactly 3 fire pixels extracted (class 0 and 4 excluded)", len(pixels) == 3, str(pixels))
    confidences = sorted(p["confidence"] for p in pixels)
    check("all three confidence levels present", confidences == ["high", "low", "medium"], str(confidences))

    probs = {p["confidence"]: p["probability"] for p in pixels}
    check("low-confidence probability matches raw*0.01", abs(probs["low"] - 0.35) < 1e-9, str(probs))
    check("medium-confidence probability matches raw*0.01", abs(probs["medium"] - 0.60) < 1e-9, str(probs))
    check("high-confidence probability matches raw*0.01", abs(probs["high"] - 0.95) < 1e-9, str(probs))

    # The nadir-ish pixels should reproject close to (0N, 0E) - not exact
    # since they're one grid cell off nadir, but nowhere near another
    # continent, which is the class of bug (wrong axis order, wrong sign,
    # forgetting the *height scaling) this check would actually catch.
    for p in pixels:
        check(
            f"{p['confidence']} pixel reprojects near nadir (0N,0E), not some far continent",
            abs(p["lat"]) < 5 and abs(p["lon"]) < 5,
            str(p),
        )

    empty = _fire_pixels_from_netcdf(_empty_netcdf())
    check("a product with zero fire pixels returns an empty list, not an error", empty == [])


def _empty_netcdf() -> bytes:
    """Same structure as `_build_synthetic_netcdf` but with fire_result
    entirely 0 (no fire) - a legitimate, common real-world product."""
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        n = 4
        f.create_dataset("fire_result", data=np.zeros((n, n), dtype=np.int8))
        f.create_dataset("fire_probability", data=np.full((n, n), -127, dtype=np.int8))
        idx = np.arange(1, n + 1, dtype=np.int16)
        f.create_dataset("x", data=idx)
        f["x"].attrs["scale_factor"] = np.array([1e-5], dtype=np.float32)
        f["x"].attrs["add_offset"] = np.array([0.0], dtype=np.float32)
        f.create_dataset("y", data=idx)
        f["y"].attrs["scale_factor"] = np.array([1e-5], dtype=np.float32)
        f["y"].attrs["add_offset"] = np.array([0.0], dtype=np.float32)
        proj = f.create_dataset("mtg_geos_projection", data=np.array(0, dtype=np.int32))
        proj.attrs["perspective_point_height"] = np.array([_HEIGHT], dtype=np.float32)
        proj.attrs["semi_major_axis"] = np.array([_SEMI_MAJOR], dtype=np.float32)
        proj.attrs["semi_minor_axis"] = np.array([_SEMI_MINOR], dtype=np.float32)
    return buf.getvalue()


def test_search_recent_products() -> None:
    print("\nsearch_recent_products: response parsing")

    def handler(request: httpx.Request) -> httpx.Response:
        check("token forwarded as a Bearer header", request.headers.get("authorization") == "Bearer test-token")
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "id": "product-A",
                        "properties": {
                            "date": "2026-08-07T13:00:00Z/2026-08-07T13:10:00Z",
                            "links": {"data": [{"href": "https://example.invalid/download/product-A"}]},
                        },
                    },
                    {
                        "id": "product-B-no-download-link",
                        "properties": {
                            "date": "2026-08-07T12:50:00Z/2026-08-07T13:00:00Z",
                            "links": {"data": []},
                        },
                    },
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        products = search_recent_products(client, "test-token", 1786100000.0, 1786110000.0)

    check("only the product with a download link is kept", len(products) == 1, str(products))
    check("product id preserved", products[0]["product_id"] == "product-A", str(products))
    check("download url extracted", products[0]["download_url"] == "https://example.invalid/download/product-A")
    check("end_ts parsed from the date range's end", abs(products[0]["end_ts"] - 1786108200.0) < 1.0, str(products[0]))


def test_ingest_product_idempotent() -> None:
    print("\ningest_product: downloads once, re-ingestion is a DB-only no-op")
    nc_bytes = _build_synthetic_netcdf()
    zip_bytes = _zip_product(nc_bytes)
    download_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        download_calls["count"] += 1
        return httpx.Response(200, content=zip_bytes)

    product = {"product_id": "test-product-1", "end_ts": 1786108200.0, "download_url": "https://example.invalid/x"}

    with tempfile.TemporaryDirectory() as raw:
        db_path = Path(raw) / "cache.sqlite3"
        db = Database(db_path)
        db.close()
        conn = sqlite3.connect(db_path)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            count1 = ingest_product(conn, client, "tok", product)
            check("first ingest downloaded once", download_calls["count"] == 1, str(download_calls))
            check("first ingest returns the real fire-pixel count", count1 == 3, str(count1))

            count2 = ingest_product(conn, client, "tok", product)
            check("re-ingesting the same product doesn't download again", download_calls["count"] == 1, str(download_calls))
            check("re-ingesting returns the same cached count", count2 == 3, str(count2))

        stored = conn.execute("SELECT COUNT(*) FROM eumetsat_fires WHERE product_id = ?", (product["product_id"],)).fetchone()[0]
        check("exactly 3 rows stored (no duplicate insert from the second call)", stored == 3, str(stored))
        conn.close()


class _FakeSettings:
    has_eumetsat_key = True
    eumetsat_consumer_key = "test-key"
    eumetsat_consumer_secret = "test-secret"


def _reset_token_cache() -> None:
    eumetsat_module._token_cache["token"] = None
    eumetsat_module._token_cache["expires_at"] = 0.0


def test_get_access_token_malformed_response() -> None:
    print("\n_get_access_token: a 200 with no access_token raises a clear error, not KeyError")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token_type": "bearer"})  # no access_token field

    original_load_settings = eumetsat_module.load_settings
    eumetsat_module.load_settings = lambda: _FakeSettings()
    _reset_token_cache()
    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            raised = None
            try:
                _get_access_token(client)
            except Exception as exc:  # noqa: BLE001 - inspecting exactly what type/message this raises
                raised = exc
    finally:
        eumetsat_module.load_settings = original_load_settings
        _reset_token_cache()

    check("raises RuntimeError, not a bare KeyError", isinstance(raised, RuntimeError), str(type(raised)))
    check("error message names the missing field", "access_token" in str(raised), str(raised))


def _search_response(products: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"features": products})


def _feature(product_id: str, url: str, start: str, end: str) -> dict:
    return {
        "id": product_id,
        "properties": {"date": f"{start}/{end}", "links": {"data": [{"href": url}]}},
    }


def test_scan_skips_bad_product_and_continues() -> None:
    print("\nscan_eumetsat_fires: one malformed product doesn't abort the rest of the batch")
    nc_bytes = _build_synthetic_netcdf()
    good_zip = _zip_product(nc_bytes)

    products = [
        _feature("bad-1", "https://example.invalid/bad", "2026-08-01T00:00:00Z", "2026-08-01T00:10:00Z"),
        _feature("good-1", "https://example.invalid/good", "2026-08-01T00:10:00Z", "2026-08-01T00:20:00Z"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 2000})
        if "search-products" in url:
            return _search_response(products)
        if url.endswith("/bad"):
            return httpx.Response(200, content=b"not a zip at all")
        if url.endswith("/good"):
            return httpx.Response(200, content=good_zip)
        return httpx.Response(404)

    original_client = eumetsat_module.httpx.Client
    original_load_settings = eumetsat_module.load_settings
    _real_httpx_client = httpx.Client
    eumetsat_module.httpx.Client = lambda *a, **kw: _real_httpx_client(transport=httpx.MockTransport(handler))
    eumetsat_module.load_settings = lambda: _FakeSettings()
    _reset_token_cache()

    with tempfile.TemporaryDirectory() as raw:
        db_path = Path(raw) / "cache.sqlite3"
        db = Database(db_path)
        db.close()
        ctx = JobContext(job_id=1, db_path=str(db_path), result_dir=str(Path(raw) / "job1"))
        try:
            result = scan_eumetsat_fires({"lookback_hours": 1.0}, ctx)
        finally:
            eumetsat_module.httpx.Client = original_client
            eumetsat_module.load_settings = original_load_settings
            _reset_token_cache()

    check("the good product was still ingested despite the bad one", result["products_scanned"] == 1, str(result))
    check("the bad product is reported as skipped", result["products_skipped"] == ["bad-1"], str(result))
    check("fire pixels from the good product were counted", result["total_fire_pixels"] == 3, str(result))


def test_scan_retries_once_on_expired_token() -> None:
    print("\nscan_eumetsat_fires: a 401 mid-batch refreshes the token and retries once")
    nc_bytes = _build_synthetic_netcdf()
    good_zip = _zip_product(nc_bytes)
    products = [_feature("p-1", "https://example.invalid/p", "2026-08-01T00:00:00Z", "2026-08-01T00:10:00Z")]

    tokens_issued = {"count": 0}
    download_attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/token"):
            tokens_issued["count"] += 1
            return httpx.Response(200, json={"access_token": f"tok-{tokens_issued['count']}", "expires_in": 2000})
        if "search-products" in url:
            return _search_response(products)
        if url.endswith("/p"):
            download_attempts["count"] += 1
            # First attempt: simulate the cached token having actually
            # expired server-side already. Second attempt (after refresh):
            # succeed.
            if download_attempts["count"] == 1:
                return httpx.Response(401)
            return httpx.Response(200, content=good_zip)
        return httpx.Response(404)

    original_client = eumetsat_module.httpx.Client
    original_load_settings = eumetsat_module.load_settings
    _real_httpx_client = httpx.Client
    eumetsat_module.httpx.Client = lambda *a, **kw: _real_httpx_client(transport=httpx.MockTransport(handler))
    eumetsat_module.load_settings = lambda: _FakeSettings()
    _reset_token_cache()

    with tempfile.TemporaryDirectory() as raw:
        db_path = Path(raw) / "cache.sqlite3"
        db = Database(db_path)
        db.close()
        ctx = JobContext(job_id=1, db_path=str(db_path), result_dir=str(Path(raw) / "job1"))
        try:
            result = scan_eumetsat_fires({"lookback_hours": 1.0}, ctx)
        finally:
            eumetsat_module.httpx.Client = original_client
            eumetsat_module.load_settings = original_load_settings
            _reset_token_cache()

    check("token was refreshed once after the 401", tokens_issued["count"] == 2, str(tokens_issued))
    check("download was retried and eventually succeeded", download_attempts["count"] == 2, str(download_attempts))
    check("the product was still counted as scanned, not skipped", result["products_scanned"] == 1, str(result))
    check("no products reported skipped", result["products_skipped"] == [], str(result))


def main() -> int:
    test_fire_pixels_from_netcdf()
    test_search_recent_products()
    test_ingest_product_idempotent()
    test_get_access_token_malformed_response()
    test_scan_skips_bad_product_and_continues()
    test_scan_retries_once_on_expired_token()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
