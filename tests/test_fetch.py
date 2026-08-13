"""End-to-end test of the fetch pipeline against a stubbed FIRMS server.

Covers the part that is hard to eyeball: a 30 day backfill being cut into
chunked area requests, deduplicated, recorded as coverage, and served back out
of SQLite - plus the automatic step-down when the API rejects a day range.

Run with:  python tests/test_fetch.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.cache import CacheManager, utc_today
from nexfiremap.config import Settings
from nexfiremap.db import Database
from nexfiremap.firms import FirmsClient

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


HEADER = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight"
)


class StubFirms:
    """Minimal stand-in for the FIRMS area API."""

    def __init__(self, *, max_day_range: int = 10, rows_per_day: int = 3) -> None:
        self.max_day_range = max_day_range
        self.rows_per_day = rows_per_day
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        # /api/area/csv/{key}/{source}/{area}/{range}/{start}
        parts = request.url.path.strip("/").split("/")
        key, source, area, day_range, start = parts[3], parts[4], parts[5], int(parts[6]), parts[7]
        self.requests.append(
            {"source": source, "area": area, "day_range": day_range, "start": start}
        )

        if key != "TESTKEY":
            return httpx.Response(200, text="Invalid MAP_KEY provided.")
        if day_range > self.max_day_range:
            return httpx.Response(
                200, text=f"Invalid day range, must be 1-{self.max_day_range}"
            )

        start_date = date.fromisoformat(start)
        lines = [HEADER]
        for offset in range(day_range):
            day = start_date + timedelta(days=offset)
            if day > utc_today():
                continue
            for n in range(self.rows_per_day):
                lines.append(
                    f"{40.0 + n * 0.01},{-3.0 - n * 0.01},330.5,0.4,0.36,"
                    f"{day.isoformat()},{1000 + n:04d},N,VIIRS,n,2.0NRT,295.0,{5.0 + n},D"
                )
        return httpx.Response(200, text="\n".join(lines) + "\n")


def make_manager(tmp: Path, stub: StubFirms, *, max_day_range: int = 10) -> CacheManager:
    settings = Settings(
        map_key="TESTKEY",
        host="127.0.0.1",
        port=8000,
        db_path=tmp / "cache.sqlite3",
        cache_days=30,
        sources=["VIIRS_NOAA20_NRT"],
        max_day_range=max_day_range,
        hot_days=1,
        hot_ttl_minutes=60,
        refresh_interval_minutes=15,
        cell_size_deg=10.0,
        max_cells_per_request=64,
        max_concurrent_fetches=2,
        request_timeout_s=10.0,
    )
    db = Database(settings.db_path)
    manager = CacheManager(settings, db)
    manager.client = FirmsClient(
        "TESTKEY", timeout=10.0, transport=httpx.MockTransport(stub.handler)
    )
    return manager


async def drain(manager: CacheManager, timeout: float = 20.0) -> None:
    """Wait until the fetch queue is empty."""
    deadline = asyncio.get_event_loop().time() + timeout
    while manager.pending > 0 and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)


async def test_backfill(tmp: Path) -> None:
    print("\n30 day backfill")
    stub = StubFirms()
    (tmp / "a").mkdir(parents=True, exist_ok=True)
    manager = make_manager(tmp / "a", stub)

    await manager.start()
    # This bbox straddles the 40N grid line, so it covers two cells:
    # 2 cells x ceil(30 days / 10 per request) = 6 chunks.
    result = await manager.ensure_cached((-5.0, 38.0, -1.0, 42.0), 30)
    check("queued 2 cells x 3 chunks", result["queued"] == 6, str(result))
    await drain(manager)

    check("all chunks issued", len(stub.requests) == 6, str(len(stub.requests)))
    check(
        "chunk sizes are 10 days",
        all(r["day_range"] == 10 for r in stub.requests),
        str([r["day_range"] for r in stub.requests]),
    )
    check(
        "requests target the containing cell",
        all(r["area"] == "-10,30,0,40" or r["area"] == "-10,40,0,50" for r in stub.requests),
        str({r["area"] for r in stub.requests}),
    )

    stats = manager.db.stats()
    check("rows stored", stats["detections"] > 0, str(stats["detections"]))

    # Second pass must be a pure cache hit.
    before = len(stub.requests)
    again = await manager.ensure_cached((-5.0, 38.0, -1.0, 42.0), 30)
    await drain(manager)
    check("second pass queues nothing", again["queued"] == 0, str(again))
    check("no extra HTTP calls", len(stub.requests) == before, str(len(stub.requests)))

    # Narrower window inside the cached one is also a hit.
    narrow = await manager.ensure_cached((-4.0, 39.0, -2.0, 41.0), 7)
    check("narrower window is cached", narrow["queued"] == 0, str(narrow))

    # A single-cell bbox needs exactly one request per 10 day chunk.
    single = await manager.ensure_cached((11.0, 42.0, 19.0, 48.0), 30)
    check("single cell queues 3 chunks", single["queued"] == 3, str(single))
    await drain(manager)

    rows = manager.db.query_detections(bbox=(-10.0, 30.0, 0.0, 50.0), limit=10000)
    check("rows queryable by bbox", len(rows) > 0, str(len(rows)))
    check("duplicates collapsed", len(rows) == stats["detections"], f"{len(rows)} vs {stats['detections']}")

    await manager.stop()
    manager.db.close()


async def test_day_range_stepdown(tmp: Path) -> None:
    print("\nDay range step-down")
    stub = StubFirms(max_day_range=5)
    (tmp / "b").mkdir(parents=True, exist_ok=True)
    manager = make_manager(tmp / "b", stub, max_day_range=10)

    await manager.start()
    await manager.ensure_cached((-5.0, 38.0, -1.0, 42.0), 10)
    await drain(manager)

    check(
        "day range lowered after rejection",
        manager.effective_day_range <= 5,
        str(manager.effective_day_range),
    )
    accepted = [r for r in stub.requests if r["day_range"] <= 5]
    check("retried within the accepted range", len(accepted) > 0, str(stub.requests))
    check("rows landed despite rejection", manager.db.stats()["detections"] > 0)

    await manager.stop()
    manager.db.close()


async def test_bad_key(tmp: Path) -> None:
    print("\nBad map key")
    stub = StubFirms()
    (tmp / "c").mkdir(parents=True, exist_ok=True)
    manager = make_manager(tmp / "c", stub)
    manager.client = FirmsClient(
        "WRONGKEY", timeout=10.0, transport=httpx.MockTransport(stub.handler)
    )

    await manager.start()
    await manager.ensure_cached((-5.0, 38.0, -1.0, 42.0), 3)
    await drain(manager)

    check("error surfaced", manager.last_error is not None and "map key" in manager.last_error.lower(), str(manager.last_error))
    check("nothing stored", manager.db.stats()["detections"] == 0)

    await manager.stop()
    manager.db.close()


async def test_retention(tmp: Path) -> None:
    print("\nRetention window")
    stub = StubFirms()
    (tmp / "d").mkdir(parents=True, exist_ok=True)
    manager = make_manager(tmp / "d", stub)

    old_day = (utc_today() - timedelta(days=45)).isoformat()
    stamp = int(
        datetime.fromisoformat(old_day + "T12:00:00").replace(tzinfo=timezone.utc).timestamp()
    )
    manager.db.upsert_detections(
        [
            {
                "source": "VIIRS_NOAA20_NRT",
                "satellite": "N",
                "instrument": "VIIRS",
                "latitude": 1.0,
                "longitude": 1.0,
                "acq_date": old_day,
                "acq_time": "1200",
                "acq_ts": stamp,
                "brightness": 300.0,
                "brightness2": 280.0,
                "scan": 0.4,
                "track": 0.4,
                "confidence_raw": "n",
                "confidence_pct": None,
                "confidence_level": "nominal",
                "frp": 3.0,
                "daynight": "D",
                "version": "2.0NRT",
            }
        ]
    )
    check("old row inserted", manager.db.stats()["detections"] == 1)
    removed, _ = manager.purge_now()
    check("purge removes beyond 30 days", removed == 1, str(removed))
    check("cache empty after purge", manager.db.stats()["detections"] == 0)
    manager.db.close()


async def test_antimeridian(tmp: Path) -> None:
    print("\nAntimeridian-crossing viewport")
    stub = StubFirms()
    (tmp / "e").mkdir(parents=True, exist_ok=True)
    manager = make_manager(tmp / "e", stub)

    # west > east: a viewport straddling 180deg (e.g. the Aleutians/Fiji).
    # Before the fix this planned zero cells and silently queued nothing.
    crossing = (170.0, -5.0, -170.0, 5.0)
    tasks, cached_days = manager.plan(crossing, ["2026-08-01"], ["VIIRS_NOAA20_NRT"])
    check("crossing bbox plans at least one task", len(tasks) > 0, str(tasks))
    cells = {t.cell for t in tasks}
    check(
        "cells found on both sides of the antimeridian",
        any(c[0] < 18 for c in cells) and any(c[0] >= 18 for c in cells),
        str(cells),
    )

    await manager.start()
    result = await manager.ensure_cached(crossing, 3)
    check("crossing bbox actually queues fetches", result["queued"] > 0, str(result))
    await drain(manager)
    check("rows landed for the crossing viewport", manager.db.stats()["detections"] > 0)

    await manager.stop()
    manager.db.close()


async def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        await test_backfill(tmp)
        await test_day_range_stepdown(tmp)
        await test_bad_key(tmp)
        await test_retention(tmp)
        await test_antimeridian(tmp)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
