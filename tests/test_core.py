"""Smoke tests for the parsing, chunking and cache logic.

Run with:  python tests/test_core.py
No network and no FIRMS key needed.
"""

from __future__ import annotations

import io
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.cache import cell_area, cells_for_bbox, chunk_days, clamp_bbox
from nexfiremap.db import Database
from nexfiremap.firms import (
    FirmsAuthError,
    FirmsError,
    _normalise_confidence,
    _normalise_row,
    _normalise_time,
    _raise_for_body,
)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


VIIRS_CSV_ROW = {
    "latitude": "38.4213",
    "longitude": "-120.1234",
    "bright_ti4": "331.2",
    "scan": "0.42",
    "track": "0.38",
    "acq_date": "2026-08-01",
    "acq_time": "942",
    "satellite": "N",
    "instrument": "VIIRS",
    "confidence": "n",
    "version": "2.0NRT",
    "bright_ti5": "295.1",
    "frp": "12.7",
    "daynight": "D",
}

MODIS_CSV_ROW = {
    "latitude": "-9.5",
    "longitude": "23.75",
    "brightness": "345.0",
    "scan": "1.1",
    "track": "1.0",
    "acq_date": "2026-07-30",
    "acq_time": "0130",
    "satellite": "Terra",
    "instrument": "MODIS",
    "confidence": "84",
    "version": "6.1NRT",
    "bright_t31": "290.4",
    "frp": "40.2",
    "daynight": "N",
}


def test_normalisation() -> None:
    print("\nRow normalisation")
    viirs = _normalise_row(VIIRS_CSV_ROW, "VIIRS_NOAA20_NRT")
    check("viirs row parses", viirs is not None)
    check("viirs time zero-padded", viirs["acq_time"] == "0942", viirs["acq_time"])
    check("viirs brightness from bright_ti4", viirs["brightness"] == 331.2)
    check("viirs second band from bright_ti5", viirs["brightness2"] == 295.1)
    check("viirs confidence letter -> level", viirs["confidence_level"] == "nominal")
    check("viirs confidence pct is null", viirs["confidence_pct"] is None)
    # 2026-08-01 09:42 UTC
    check("viirs timestamp is utc", viirs["acq_ts"] == 1785577320, str(viirs["acq_ts"]))

    modis = _normalise_row(MODIS_CSV_ROW, "MODIS_NRT")
    check("modis brightness from brightness", modis["brightness"] == 345.0)
    check("modis second band from bright_t31", modis["brightness2"] == 290.4)
    check("modis pct -> high", modis["confidence_level"] == "high")
    check("modis pct kept", modis["confidence_pct"] == 84)

    check("bad row rejected", _normalise_row({"latitude": "x"}, "MODIS_NRT") is None)
    check("time '5' -> 0005", _normalise_time("5") == "0005")
    check("conf 12 -> low", _normalise_confidence("12") == (12, "low"))
    check("conf h -> high", _normalise_confidence("h") == (None, "high"))
    check("conf empty", _normalise_confidence("") == (None, None))


def test_error_sniffing() -> None:
    print("\nFIRMS error sniffing")

    def raises(body: str, exc_type) -> bool:
        try:
            _raise_for_body(body, "VIIRS_SNPP_NRT")
        except exc_type:
            return True
        except Exception:
            return False
        return False

    check("invalid key detected", raises("Invalid MAP_KEY provided", FirmsAuthError))
    check("html error detected", raises("<html><body>oops</body></html>", FirmsError))
    check(
        "valid header accepted",
        _raise_for_body("latitude,longitude,frp\n1,2,3\n", "X") is None,
    )
    check(
        "empty result accepted",
        _raise_for_body("latitude,longitude,acq_date\n", "X") is None,
    )


def test_grid() -> None:
    print("\nCoverage grid")
    check("clamp keeps order", clamp_bbox((-200, 95, 200, -95)) == (-180, -90, 180, 90))

    cells = cells_for_bbox((-10, 40, 10, 50), 10.0)
    check("bbox spanning 2x1 cells", len(cells) == 2, str(cells))

    world = cells_for_bbox((-180, -90, 180, 90), 10.0)
    check("world is 36x18 cells", len(world) == 648, str(len(world)))

    check("cell area string", cell_area((17, 13), 10.0) == "-10,40,0,50", cell_area((17, 13), 10.0))
    check("world sentinel", cell_area((-1, -1), 10.0) == "world")


def test_chunking() -> None:
    print("\nDay chunking")
    days = [(date(2026, 8, 1) + timedelta(days=n)).isoformat() for n in range(30)]
    chunks = chunk_days(days, 10)
    check("30 days -> 3 chunks of 10", [len(c) for c in chunks] == [10, 10, 10], str([len(c) for c in chunks]))
    check("chunks stay contiguous", chunks[0][0] == "2026-08-01" and chunks[0][-1] == "2026-08-10")

    gapped = ["2026-08-01", "2026-08-02", "2026-08-09"]
    runs = chunk_days(gapped, 10)
    check("gap splits the run", [len(c) for c in runs] == [2, 1], str(runs))
    check("empty input", chunk_days([], 10) == [])


def test_database() -> None:
    print("\nDatabase")
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.sqlite3")
        rows = [
            _normalise_row(VIIRS_CSV_ROW, "VIIRS_NOAA20_NRT"),
            _normalise_row(MODIS_CSV_ROW, "MODIS_NRT"),
        ]
        check("insert returns count", db.upsert_detections(rows) == 2)
        # Resubmitting the same natural keys is an upsert, not an ignore -
        # it reports both rows as touched (see upsert_detections'
        # docstring) rather than silently ignoring them, and a revised
        # value on one of them (as FIRMS does during the "hot" re-check
        # window) has to actually land, not be discarded because the row
        # already existed.
        check("resubmit reports both rows touched", db.upsert_detections(rows) == 2)
        total_before = db.stats()["detections"]
        revised = dict(rows[0])
        revised["confidence_pct"] = 5
        revised["frp"] = 15.0  # still under the later "frp filter" check's threshold
        check("revised resubmit still touches 1 row", db.upsert_detections([revised]) == 1)
        check("revision did not add a new row", db.stats()["detections"] == total_before)
        updated = db.query_detections(sources=["VIIRS_NOAA20_NRT"])[0]
        check("revised confidence applied", updated["confidence_pct"] == 5, updated["confidence_pct"])
        check("revised frp applied", updated["frp"] == 15.0, updated["frp"])

        found = db.query_detections(bbox=(-130, 30, -110, 45))
        check("bbox filter narrows", len(found) == 1 and found[0]["source"] == "VIIRS_NOAA20_NRT")

        antimeridian = db.query_detections(bbox=(170, -90, -110, 90))
        check("antimeridian bbox wraps", len(antimeridian) == 1, str(len(antimeridian)))

        check("frp filter", len(db.query_detections(min_frp=20)) == 1)
        check("confidence filter", len(db.query_detections(confidence_levels=["high"])) == 1)
        check("source filter", len(db.query_detections(sources=["MODIS_NRT"])) == 1)

        db.mark_coverage("MODIS_NRT", 1, 2, ["2026-08-01", "2026-08-02"], row_count=5)
        cov = db.coverage_state("MODIS_NRT", 1, 2, ["2026-08-01", "2026-08-03"])
        check("coverage recorded", set(cov) == {"2026-08-01"}, str(set(cov)))
        check("cached cells listed", len(db.cached_cells("2026-01-01")) == 1)

        counts = db.daily_counts()
        check("daily counts grouped", len(counts) == 2, str([dict(c) for c in counts]))

        removed, cov_removed = db.purge_older_than(date(2026, 8, 1))
        check("purge drops older rows", removed == 1 and cov_removed == 0, f"{removed},{cov_removed}")
        check("stats reflect purge", db.stats()["detections"] == 1)
        db.close()


def test_purge_cascades_events() -> None:
    print("\nPurge cascades into events")
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.sqlite3")

        def insert_detection(day: str, lat: float, lon: float) -> int:
            stamp = int(
                datetime.combine(date.fromisoformat(day), datetime.min.time())
                .replace(tzinfo=None)
                .timestamp()
            )
            db.upsert_detections(
                [
                    {
                        "source": "VIIRS_NOAA20_NRT",
                        "satellite": "N",
                        "instrument": "VIIRS",
                        "latitude": lat,
                        "longitude": lon,
                        "acq_date": day,
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
            row = db.conn.execute(
                "SELECT id FROM detections WHERE acq_date = ? AND latitude = ? AND longitude = ?",
                (day, lat, lon),
            ).fetchone()
            return int(row["id"])

        # Event A: one old detection (will be purged) and one recent one -
        # should survive with its summary shrunk to just the recent point.
        old_a = insert_detection("2026-06-01", 10.0, 20.0)
        recent_a = insert_detection("2026-08-05", 10.5, 20.5)
        # Event B: both detections are old - should be dropped entirely
        # once it has no members left.
        old_b1 = insert_detection("2026-06-01", -5.0, -5.0)
        old_b2 = insert_detection("2026-06-02", -5.5, -5.5)

        now = int(time.time())
        cur = db.conn.execute(
            "INSERT INTO events (bbox_west, bbox_south, bbox_east, bbox_north, "
            "centroid_lat, centroid_lon, first_seen, last_seen, detection_count, "
            "sources_json, params_json, created_at) VALUES (20,10,20.5,10.5,10.25,20.25,0,0,2,'[]','{}',?)",
            (now,),
        )
        event_a = int(cur.lastrowid)
        cur = db.conn.execute(
            "INSERT INTO events (bbox_west, bbox_south, bbox_east, bbox_north, "
            "centroid_lat, centroid_lon, first_seen, last_seen, detection_count, "
            "sources_json, params_json, created_at) VALUES (-5.5,-5.5,-5,-5,-5.25,-5.25,0,0,2,'[]','{}',?)",
            (now,),
        )
        event_b = int(cur.lastrowid)
        db.conn.executemany(
            "INSERT INTO event_members (event_id, detection_id) VALUES (?, ?)",
            [(event_a, old_a), (event_a, recent_a), (event_b, old_b1), (event_b, old_b2)],
        )
        db.conn.commit()

        db.purge_older_than(date(2026, 7, 1))

        remaining_members_a = {r["id"] for r in db.event_detections(event_a)}
        check(
            "event A keeps only its surviving member",
            remaining_members_a == {recent_a},
            str(remaining_members_a),
        )
        event_a_row = db.get_event(event_a)
        check("event A detection_count reconciled", event_a_row["detection_count"] == 1)
        check(
            "event A bbox shrunk to the surviving point",
            event_a_row["bbox_west"] == 20.5 and event_a_row["bbox_east"] == 20.5,
            f"{event_a_row['bbox_west']},{event_a_row['bbox_east']}",
        )

        check("event B was dropped (no members left)", db.get_event(event_b) is None)
        orphans = db.conn.execute(
            "SELECT COUNT(*) FROM event_members WHERE event_id = ?", (event_b,)
        ).fetchone()[0]
        check("event B's memberships are gone too", orphans == 0)

        db.close()

    test_purge_beyond_sql_variable_limit()


def test_purge_beyond_sql_variable_limit() -> None:
    """A purge larger than SQLite's host-parameter cap must still work.

    SQLite allows 32766 bound parameters per statement (999 before 3.32), and
    the purge used to bind one per expiring detection - so a single expiring day
    of a whole-world fetch raised "too many SQL variables". Retention then
    stopped silently, and because a purge also runs during startup the server
    subsequently refused to boot. 40000 rows is comfortably past the modern cap.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "purge.sqlite3")
        count = 40_000
        stamp = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
        db.upsert_detections([
            {
                "source": "VIIRS_NOAA20_NRT", "satellite": "N", "instrument": "VIIRS",
                "latitude": 40.0 + (i % 200) * 0.001, "longitude": -120.0 + (i // 200) * 0.001,
                "acq_date": "2026-06-01", "acq_time": "1200", "acq_ts": stamp,
                "brightness": 300.0, "brightness2": 280.0, "scan": 0.4, "track": 0.4,
                "confidence_raw": "n", "confidence_pct": None, "confidence_level": "nominal",
                "frp": 3.0, "daynight": "D", "version": "2.0NRT",
            }
            for i in range(count)
        ])
        detection_ids = [r[0] for r in db.conn.execute("SELECT id FROM detections").fetchall()]
        check(f"{count} detections staged", len(detection_ids) == count, str(len(detection_ids)))

        cur = db.conn.execute(
            "INSERT INTO events (bbox_west,bbox_south,bbox_east,bbox_north,centroid_lat,"
            "centroid_lon,first_seen,last_seen,detection_count,sources_json,params_json,"
            "created_at) VALUES (-120,40,-119.9,40.2,40.1,-119.95,0,0,?,'[]','{}',?)",
            (count, int(time.time())),
        )
        event_id = int(cur.lastrowid)
        # Every purged detection is also an event member, so the membership
        # cleanup faces the same volume as the detection delete.
        db.conn.executemany(
            "INSERT INTO event_members (event_id, detection_id) VALUES (?, ?)",
            [(event_id, det_id) for det_id in detection_ids],
        )
        db.conn.commit()

        removed, _ = db.purge_older_than(date(2026, 7, 1))
        check(f"purged all {count} rows past the parameter cap", removed == count, str(removed))
        check("the emptied event was dropped", db.get_event(event_id) is None)
        orphans = db.conn.execute("SELECT COUNT(*) FROM event_members").fetchone()[0]
        check("no dangling memberships remain", orphans == 0, str(orphans))
        # The temp table the purge uses must not survive it, or a second purge
        # on the same connection would collide with the leftover.
        again = db.purge_older_than(date(2026, 7, 1))
        check("a second purge on the same connection is clean", again == (0, 0), str(again))

        db.close()


def main() -> int:
    test_normalisation()
    test_error_sniffing()
    test_grid()
    test_chunking()
    test_database()
    test_purge_cascades_events()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
