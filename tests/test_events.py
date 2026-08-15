"""Smoke tests for space-time event clustering (Phase 2).

Run with:  python tests/test_events.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

from nexfiremap.db import Database
from nexfiremap.events import MAX_PLAUSIBLE_EVENT_SPAN_KM, cluster_detections, detect_events, spread_topology
from nexfiremap.geo import haversine_km
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


def test_chaining_is_span_bounded() -> None:
    print("\ncluster_detections() itself refuses to chain past max_span_km")
    # Consecutive points ~17.9km apart, 3 hours apart (allowed budget
    # ~2 + 8*3 = 26km, comfortably covering each individual step - and, at
    # this spacing/timing, even covering many non-adjacent pairs directly,
    # not merely by chaining through intermediates). Sixteen of them span
    # roughly 268km end to end - the exact live pattern found in this
    # project's own dev database (a real 14,000-point, 1,005km event).
    rows = [row(50.0, 4.0 + step * 0.25, step * 3 * HOUR) for step in range(16)]
    clusters = cluster_detections(
        rows, v_max_kmh=8.0, max_dt_hours=168.0, max_span_km=MAX_PLAUSIBLE_EVENT_SPAN_KM
    )
    check("split into more than one cluster", len(clusters) > 1, str(clusters))
    check(
        "every detection still accounted for exactly once",
        sorted(idx for cluster in clusters for idx in cluster) == list(range(16)),
        str(clusters),
    )
    check(
        # A weaker "len(clusters) > 1" check alone would also pass on a
        # degenerate all-16-singletons result - not what a sane split looks
        # like. The geometric grid splitter groups neighbouring points
        # together (each cell some contiguous run of the chain), so a
        # sensible split leaves no cluster stranded alone.
        "the split is sensible, not degenerate - no cluster is left a singleton",
        all(len(cluster) > 1 for cluster in clusters),
        str(clusters),
    )

    def span_km(cluster: list[int]) -> float:
        lats = [rows[i]["lat"] for i in cluster]
        lons = [rows[i]["lon"] for i in cluster]
        return haversine_km(min(lats), min(lons), max(lats), max(lons))

    spans = [span_km(cluster) for cluster in clusters]
    check(
        "no resulting cluster exceeds the span bound",
        all(span <= MAX_PLAUSIBLE_EVENT_SPAN_KM for span in spans),
        str(list(zip((len(c) for c in clusters), spans))),
    )


def test_chained_scatter_splits_instead_of_one_wide_event() -> None:
    print("\nA chained scatter spanning hundreds of km splits into smaller events end to end, none flagged wide_span")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        db_path = tmp / "cache.sqlite3"
        db = Database(db_path)

        def det(lat, lon, ts_offset):
            from datetime import datetime, timezone

            ts = BASE_TS + ts_offset
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return {
                "source": "VIIRS_NOAA20_NRT", "satellite": "N", "instrument": "VIIRS",
                "latitude": lat, "longitude": lon,
                "acq_date": dt.strftime("%Y-%m-%d"), "acq_time": dt.strftime("%H%M"), "acq_ts": ts,
                "brightness": 330.0, "brightness2": 295.0, "scan": 0.4, "track": 0.4,
                "confidence_raw": "n", "confidence_pct": None, "confidence_level": "nominal",
                "frp": 10.0, "daynight": "D", "version": "2.0NRT",
            }

        # Each step is individually well within the default link rule
        # (~18km apart, 3 hours apart - allowed budget is r_i+r_j + 8*3 ~=
        # 26km, and at this spacing/timing even several non-adjacent steps
        # link directly) so the pairwise rule alone would happily chain all
        # 16 into one connected component spanning ~268km end to end -
        # exactly like the real 14,000-point, 1,005km event found live in
        # this project's own dev database. cluster_detections' span-bounded
        # merge step is what's actually under test here: it should refuse
        # to let that chain grow past MAX_PLAUSIBLE_EVENT_SPAN_KM, splitting
        # it into several smaller, individually-plausible candidates instead.
        rows = [det(50.0, 4.0 + step * 0.25, step * 3 * HOUR) for step in range(16)]
        db.upsert_detections(rows)
        job_id = db.create_job("detect_events", {})
        result_dir = tmp / "job1"
        result_dir.mkdir()
        db.close()

        ctx = JobContext(job_id=job_id, db_path=str(db_path), result_dir=str(result_dir))
        result = detect_events(
            {"bbox": [-10.0, 40.0, 20.0, 60.0], "start_ts": BASE_TS - DAY,
             "end_ts": BASE_TS + 3 * DAY, "min_detections": 2},
            ctx,
        )
        check("split into more than one candidate instead of one giant one", result["event_count"] > 1, str(result))
        check("detection_count sums back to all 16 - none lost in the split", result["detection_count"] == 16, str(result))
        check("no event needed the wide_span defence-in-depth flag", result["wide_span_events"] == 0, str(result))

        db2 = Database(db_path)
        events = db2.list_events()
        check(
            "every stored event's own span_km is within the plausible bound",
            all(json.loads(e["params_json"])["span_km"] <= MAX_PLAUSIBLE_EVENT_SPAN_KM for e in events),
            str([json.loads(e["params_json"]) for e in events]),
        )
        check(
            "every stored event's own wide_span flag is false",
            all(json.loads(e["params_json"])["wide_span"] is False for e in events),
            str([json.loads(e["params_json"]) for e in events]),
        )
        check(
            "detections split across events sum back to 16",
            sum(e["detection_count"] for e in events) == 16,
            str([dict(e) for e in events]),
        )
        db2.close()


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


def test_spread_topology_job() -> None:
    print("\nspread_topology job body: distinct-pass cutoffs, cumulative nesting, full-ramp spread")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        db_path = tmp / "cache.sqlite3"
        db = Database(db_path)

        def det(lat, lon, ts_offset):
            from datetime import datetime, timezone

            ts = BASE_TS + ts_offset
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return {
                "source": "VIIRS_NOAA20_NRT", "satellite": "N", "instrument": "VIIRS",
                "latitude": lat, "longitude": lon,
                "acq_date": dt.strftime("%Y-%m-%d"), "acq_time": dt.strftime("%H%M"), "acq_ts": ts,
                "brightness": 330.0, "brightness2": 295.0, "scan": 0.4, "track": 0.4,
                "confidence_raw": "n", "confidence_pct": None, "confidence_level": "nominal",
                "frp": 10.0, "daynight": "D", "version": "2.0NRT",
            }

        # Two distinct satellite passes roughly a day apart, each a tight
        # clump of minute-apart detections - the exact real-world pattern
        # (confirmed live against a 97-detection dev-DB case) that a naive
        # linear-quantile cutoff mishandles: it would land two of its five
        # cutoffs within the same clump (minutes apart, near-duplicate
        # bands) while the genuine day-long gap between passes goes
        # unrepresented. Pass-grouping should instead recognise exactly two
        # distinct passes here and use both fully, not pad out to five.
        bbox = [8.9, 44.9, 9.3, 45.3]
        rows = [det(45.0 + i * 0.001, 9.0 + i * 0.001, i * 60) for i in range(10)]
        rows += [det(45.05 + i * 0.001, 9.05 + i * 0.001, DAY + i * 60) for i in range(10)]
        db.upsert_detections(rows)
        job_id = db.create_job("spread_topology", {})
        result_dir = tmp / "job1"
        result_dir.mkdir()
        db.close()

        ctx = JobContext(job_id=job_id, db_path=str(db_path), result_dir=str(result_dir))
        result = spread_topology(
            {"bbox": bbox, "start_ts": BASE_TS - HOUR, "end_ts": BASE_TS + DAY + 2 * HOUR}, ctx
        )

        check("exactly two distinct passes recognised", result["pass_count"] == 2, str(result))
        check("band count matches pass count, not padded to 5", result["band_count"] == 2, str(result))
        check("all 20 detections examined", result["detection_count"] == 20, str(result))
        check("cutoffs roughly a day apart", abs((result["cutoffs"][1] - result["cutoffs"][0]) - DAY) < 60, str(result))

        geo = json.loads((result_dir / "spread_topology.geojson").read_text())
        check("at least one contour ring produced", len(geo["features"]) > 0, str(geo))

        band_indices = sorted({f["properties"]["band_index"] for f in geo["features"]})
        check("band_index is the plain 0-based slot order, not a rescaled LUT index", band_indices == [0, 1], str(band_indices))

        band_fractions = sorted({f["properties"]["band_fraction"] for f in geo["features"]})
        check(
            "two-band case's band_fraction spans the full earliest(0)-to-latest(1) range, not bunched at one end",
            band_fractions == [0.0, 1.0],
            str(band_fractions),
        )

        west, south, east, north = bbox
        out_of_bbox = [
            [lon, lat]
            for f in geo["features"]
            for lon, lat in f["geometry"]["coordinates"][0]
            if not (west <= lon <= east and south <= lat <= north)
        ]
        check("every contour coordinate stays within the requested bbox", not out_of_bbox, str(out_of_bbox[:5]))

        # Cumulative construction: the later band's detection_count must be
        # >= the earlier one's (every detection in band 0 is also in band 1
        # by construction - see spread_topology's docstring).
        counts_by_band = {f["properties"]["band_index"]: f["properties"]["detection_count"] for f in geo["features"]}
        check(
            "later band's cumulative detection_count is >= earlier band's",
            counts_by_band[1] >= counts_by_band[0],
            str(counts_by_band),
        )


def main() -> int:
    test_two_tight_groups_far_apart()
    test_chain_over_time_stays_one_event()
    test_max_dt_hours_cuts_the_link()
    test_singletons_and_empty()
    test_missing_footprint_uses_default_radius()
    test_chaining_is_span_bounded()
    test_chained_scatter_splits_instead_of_one_wide_event()
    test_detect_events_job()
    test_spread_topology_job()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
