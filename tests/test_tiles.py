"""Smoke test for the on-disk tile cache against a stubbed tile server.

Run with:  python tests/test_tiles.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.config import Settings
from nexfiremap.tiles import TileCache, public_layer

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


TILE_BYTES = b"\x89PNG\r\n\x1a\nFAKETILEDATA"


def make_settings(tmp: Path) -> Settings:
    return Settings(
        map_key="",
        host="127.0.0.1",
        port=8000,
        db_path=tmp / "unused.sqlite3",
        cache_days=30,
        tile_cache_dir=tmp / "tiles",
        tile_cache_days=30,
        tile_cache_max_mb=1,  # tiny cap to exercise eviction
        tile_max_concurrent=4,
    )


async def test_fetch_and_cache(tmp: Path) -> None:
    print("\nFetch and reuse from disk")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=TILE_BYTES, headers={"content-type": "image/png"})

    cache = TileCache(make_settings(tmp / "a"))
    cache._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await cache.start()

    data = await cache.get("osm", 3, 4, 5)
    check("first fetch returns bytes", data == TILE_BYTES)
    check("first fetch hit upstream once", calls["n"] == 1, str(calls["n"]))

    data2 = await cache.get("osm", 3, 4, 5)
    check("second call served from disk", data2 == TILE_BYTES)
    check("no extra upstream call", calls["n"] == 1, str(calls["n"]))

    path = cache.path_for("osm", 3, 4, 5)
    check("tile file written to disk", path.is_file(), str(path))

    check("unknown layer returns None", await cache.get("nope", 1, 1, 1) is None)

    stats = cache.stats()
    check("stats count tiles", stats["tiles"] == 1, str(stats))
    check("hits/misses tracked", stats["hits"] == 1 and stats["misses"] == 1, str(stats))

    await cache.stop()


async def test_expiry(tmp: Path) -> None:
    print("\nTTL expiry triggers a re-fetch")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=TILE_BYTES)

    settings = make_settings(tmp / "b")
    settings = Settings(**{**settings.__dict__, "tile_cache_days": 30})
    cache = TileCache(settings)
    cache._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await cache.start()

    await cache.get("osm", 1, 0, 0)
    path = cache.path_for("osm", 1, 0, 0)
    # Backdate the file well past the TTL.
    old = time.time() - 40 * 86400
    import os

    os.utime(path, (old, old))

    await cache.get("osm", 1, 0, 0)
    check("stale tile triggers re-fetch", calls["n"] == 2, str(calls["n"]))

    await cache.stop()


async def test_upstream_failure_serves_stale(tmp: Path) -> None:
    print("\nUpstream failure falls back to stale tile")

    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=TILE_BYTES)

    settings = make_settings(tmp / "c")
    cache = TileCache(settings)
    cache._client = httpx.AsyncClient(transport=httpx.MockTransport(ok))
    await cache.start()
    await cache.get("osm", 2, 1, 1)

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    cache._client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    path = cache.path_for("osm", 2, 1, 1)
    old = time.time() - 40 * 86400
    import os

    os.utime(path, (old, old))

    data = await cache.get("osm", 2, 1, 1)
    check("stale tile served when upstream fails", data == TILE_BYTES)

    await cache.stop()


async def test_eviction(tmp: Path) -> None:
    print("\nSize-cap eviction")

    def handler(request: httpx.Request) -> httpx.Response:
        # ~200KB per tile so a 1MB cap forces eviction quickly.
        return httpx.Response(200, content=b"x" * 200_000)

    cache = TileCache(make_settings(tmp / "d"))
    cache._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await cache.start()

    for i in range(10):
        await cache.get("osm", 5, i, 0)

    before = cache.stats()
    result = cache.prune_now()
    after = cache.stats()
    check("eviction removed some tiles", result["removed_over_budget"] > 0, str(result))
    check("cache shrank under budget", after["bytes"] <= 1024 * 1024, str(after))
    check("fewer tiles remain", after["tiles"] < before["tiles"], f"{before} -> {after}")

    await cache.stop()


async def test_non_png_extension_covered(tmp: Path) -> None:
    print("\nNon-.png tile extensions are covered by stats/TTL/eviction too")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=TILE_BYTES)

    cache = TileCache(make_settings(tmp / "e"))
    cache._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await cache.start()

    # esri-terrain is the one bundled layer with tile_ext="jpg" - its tiles
    # used to be invisible to stats()/prune_now(), which only ever globbed
    # "*.png".
    data = await cache.get("esri-terrain", 3, 4, 5)
    check("jpg tile fetched", data == TILE_BYTES)
    path = cache.path_for("esri-terrain", 3, 4, 5)
    check("jpg tile written with a .jpg extension", path.suffix == ".jpg", str(path))

    stats = cache.stats()
    check("stats count the jpg tile", stats["tiles"] == 1, str(stats))

    old = time.time() - 40 * 86400
    import os

    os.utime(path, (old, old))
    result = cache.prune_now()
    check("jpg tile expired by TTL prune too", result["removed_expired"] == 1, str(result))
    check("jpg tile actually gone from disk", not path.is_file())

    await cache.stop()


def test_public_layer() -> None:
    print("\nPublic layer URL rewriting")
    layer = {
        "id": "osm",
        "name": "OpenStreetMap",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "subdomains": "abc",
        "attribution": "x",
    }
    pub = public_layer(layer)
    check("url points at local proxy", pub["url"] == "/tiles/osm/{z}/{x}/{y}.png", pub["url"])
    check("subdomains stripped", "subdomains" not in pub)
    check("other fields kept", pub["attribution"] == "x")


async def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        await test_fetch_and_cache(tmp)
        await test_expiry(tmp)
        await test_upstream_failure_serves_stale(tmp)
        await test_eviction(tmp)
        await test_non_png_extension_covered(tmp)
    test_public_layer()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
