"""Smoke tests for space-time event clustering (Phase 2).

Run with:  python tests/test_events.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.events import cluster_detections, detect_events
from nexfiremap.jobs import JobContext

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


HOUR = 3600
DAY = 86400
BASE_TS = 1_800_000_000  # arbitrary fixed epoch so tests are deterministic


def row(lat, lon, ts_offset, scan=0.4, track=0.4):
    return {"lat": lat, "lon": lon, "ts": BASE_TS + ts_offset, "scan": scan, "track": track}


def test_two_tight_groups_far_apart() -> None:
    print("\nTwo spatially distant groups stay separate")
    rows = [
        row(40.0, -120.0, 0),
        row(40.001, -120.001, HOUR),
        row(40.002, -120.002, 2 * HOUR),
        # A group ~500km away - clearly a different fire.
        row(45.0, -100.0, 0),
        row(45.001, -100.001, HOUR),
    ]
    clusters = cluster_detections(rows, v_max_kmh=8.0, max_dt_hours=168.0)
    check("two clusters found", len(clusters) == 2, str(clusters))
    sizes = sorted(len(c) for c in clusters)
    check("cluster sizes are 2 and 3", sizes == [2, 3], str(sizes))


def test_chain_over_time_stays_one_event() -> None:
    print("\nA slowly-drifting fire over several overpasses is one event")
    rows = [
        row(30.0, 10.0, 0),
        row(30.02, 10.02, 6 * HOUR),
        row(30.04, 10.04, 12 * HOUR),
        row(30.06, 10.06, 24 * HOUR),
    ]
    clusters = cluster_detections(rows, v_max_kmh=8.0, max_dt_hours=168.0)
    check("single event", len(clusters) == 1, str(clusters))
    check("all four detections included", len(clusters[0]) == 4, str(clusters))


def test_max_dt_hours_cuts_the_link() -> None:
    print("\nA time gap beyond max_dt_hours splits the event even if close")
    rows = [
        row(30.0, 10.0, 0),
        row(30.0001, 10.0001, 20 * DAY),  # ~physically adjacent, but weeks later
    ]
    clusters = cluster_detections(rows, v_max_kmh=8.0, max_dt_hours=168.0)  # 7 day cap
    check("split into two singleton events", len(clusters) == 2, str(clusters))


def test_singletons_and_empty() -> None:
    print("\nEdge cases: empty input and a single point")
    check("empty input -> no clusters", cluster_detections([]) == [])
    single = cluster_detections([row(1.0, 1.0, 0)])
    check("single point -> one cluster of one", single == [[0]], str(single))


def test_missing_footprint_uses_default_radius() -> None:
    print("\nMissing scan/track falls back to a default footprint radius")
    rows = [
        {"lat": 10.0, "lon": 10.0, "ts": BASE_TS, "scan": None, "track": None},
        {"lat": 10.0005, "lon": 10.0005, "ts": BASE_TS + HOUR, "scan": None, "track": None},
    ]
    clusters = cluster_detections(rows, v_max_kmh=8.0, max_dt_hours=168.0)
    check("still links two very close points", len(clusters) == 1, str(clusters))


def test_detect_events_job() -> None:
    print("\ndetect_events job body: end-to-end against a real cache DB")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        db_path = tmp / "cache.sqlite3"
        db = Database(db_path)

        def det(lat, lon, ts_offset, source="VIIRS_NOAA20_NRT"):
            from datetime import datetime, timezone

            ts = BASE_TS + ts_offset
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return {
                "source": source,
                "satellite": "N",
                "instrument": "VIIRS",
                "latitude": lat,
                "longitude": lon,
                "acq_date": dt.strftime("%Y-%m-%d"),
                "acq_time": dt.strftime("%H%M"),
                "acq_ts": ts,
                "brightness": 330.0,
                "brightness2": 295.0,
                "scan": 0.4,
                "track": 0.4,
                "confidence_raw": "n",
                "confidence_pct": None,
                "confidence_level": "nominal",
                "frp": 10.0,
                "daynight": "D",
                "version": "2.0NRT",
            }

        rows = [
            det(38.0, -5.0, 0),
            det(38.001, -5.001, HOUR),
            det(38.002, -5.002, 2 * HOUR),
            det(41.0, 2.0, 0),  # a separate, distant fire
        ]
        db.upsert_detections(rows)
        job_id = db.create_job("detect_events", {})
        result_dir = tmp / "job1"
        result_dir.mkdir()
        db.close()

        ctx = JobContext(job_id=job_id, db_path=str(db_path), result_dir=str(result_dir))
        result = detect_events(
            {
                "bbox": [-10.0, 30.0, 10.0, 45.0],
                "start_ts": BASE_TS - DAY,
                "end_ts": BASE_TS + DAY,
                "min_detections": 2,
            },
            ctx,
        )

        # The lone detection at (41,2) is far from the 3-point cluster and
        # forms its own singleton cluster; min_detections=2 means it's
        # dropped entirely rather than stored as a one-detection event.
        check("only the 3-point cluster survives min_detections", result["event_count"] == 1, str(result))
        check("all four detections were examined", result["detection_count"] == 4, str(result))
        check("one cluster was dropped as too small", result["singleton_dropped"] == 1, str(result))

        db2 = Database(db_path)
        events = db2.list_events()
        check("exactly one event stored", len(events) == 1, str(len(events)))

        event = events[0]
        check("event has 3 detections", event["detection_count"] == 3, str(dict(event)))
        members = db2.event_detections(event["id"])
        check("event_members join works", len(members) == 3, str(len(members)))
        check(
            "centroid is within the cluster's bbox",
            event["bbox_west"] <= event["centroid_lon"] <= event["bbox_east"]
            and event["bbox_south"] <= event["centroid_lat"] <= event["bbox_north"],
        )
        check(
            "first/last seen match the 3 detections' span",
            event["first_seen"] == BASE_TS and event["last_seen"] == BASE_TS + 2 * HOUR,
            f"{event['first_seen']} {event['last_seen']}",
        )

        db2.close()


def main() -> int:
    test_two_tight_groups_far_apart()
    test_chain_over_time_stays_one_event()
    test_max_dt_hours_cuts_the_link()
    test_singletons_and_empty()
    test_missing_footprint_uses_default_radius()
    test_detect_events_job()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
