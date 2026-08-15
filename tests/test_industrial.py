"""Smoke tests for the persistent thermal-source classifier
(further_plan.md section 13).

Fully offline: the Overpass HTTP call is only unit-tested directly against
a mocked transport (parsing correctness) - ``ensure_osm_candidates`` itself
is only exercised on its cache-hit path here, same convention as
test_orbits.py's ``ensure_tle`` test (a cache-miss/live-fetch path is
exercised by a live end-to-end run against the real server, not here).

Run with:  python tests/test_industrial.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nexfiremap.industrial as industrial_module
from nexfiremap.db import Database
from nexfiremap.jobs import JobContext
from nexfiremap.industrial import (
    THRESHOLD_AMBIGUOUS,
    _evidence_class,
    apply_wildfire_override,
    build_overpass_query,
    ensure_osm_candidates,
    fetch_osm_candidates_sync,
    match_detections,
    match_radius_km,
    persistence_features,
    scan_industrial_sources,
    score_source,
)

# persistence_features now also looks up observation opportunity via
# orbits.aoi_clear_passes (real orbit propagation/TLE fetch) - stubbed out
# module-wide so this file stays fully offline like every test here already
# is. None reproduces the exact pre-existing behaviour (falls back to the
# window_days denominator) for every test below that isn't specifically
# about this new feature - see test_score_source_observed_days_denominator.
industrial_module._observed_days = lambda *a, **kw: None

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


def test_build_overpass_query() -> None:
    print("\nOverpass query construction")
    q = build_overpass_query((10.0, 40.0, 10.1, 40.1))
    check("contains bbox", "(40.0,10.0,40.1,10.1)" in q, q)
    check("queries flare tag", 'node["man_made"="flare"]' in q, q)
    check("queries refinery tag", 'node["industrial"="refinery"]' in q, q)
    check("uses out center for way centroids", "out center;" in q, q)


def test_evidence_class() -> None:
    print("\nEvidence-class classification")
    check("flare is strong", _evidence_class({"man_made": "flare"}) == "strong")
    check("refinery is strong", _evidence_class({"industrial": "refinery"}) == "strong")
    check("works is moderate", _evidence_class({"man_made": "works"}) == "moderate")
    check("unrelated tags are None", _evidence_class({"landuse": "industrial"}) is None)
    check("empty tags are None", _evidence_class({}) is None)

    # A real gap found by live-testing against Rotterdam's dense OSM data:
    # rooftop solar panels are commonly tagged power=generator too, but
    # produce no heat signature and must not be treated as industrial
    # evidence (further_plan.md section 13.3 explicitly separates solar
    # into its own reflection-risk concern).
    solar = {"power": "generator", "generator:source": "solar", "generator:method": "photovoltaic"}
    check("solar rooftop generator is not industrial evidence", _evidence_class(solar) is None, str(solar))
    wind = {"power": "generator", "generator:source": "wind"}
    check("wind generator is not industrial evidence", _evidence_class(wind) is None, str(wind))
    gas_generator = {"power": "generator", "generator:source": "gas"}
    check("combustion (gas) generator is still moderate evidence", _evidence_class(gas_generator) == "moderate", str(gas_generator))
    untagged_generator = {"power": "generator"}
    check("generator with no source tag defaults to moderate (ambiguous, not excluded)", _evidence_class(untagged_generator) == "moderate")


def test_fetch_osm_candidates_sync() -> None:
    print("\nfetch_osm_candidates_sync: parsing a mocked Overpass response")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "elements": [
                    {"type": "node", "id": 1, "lat": 40.01, "lon": 10.01, "tags": {"man_made": "flare"}},
                    {
                        "type": "way", "id": 2, "center": {"lat": 40.02, "lon": 10.02},
                        "tags": {"industrial": "refinery", "name": "Big Refinery"},
                    },
                    # No recognised tag - should be dropped.
                    {"type": "node", "id": 3, "lat": 40.03, "lon": 10.03, "tags": {"shop": "bakery"}},
                    # Recognised tag but no usable centre - should be dropped.
                    {"type": "way", "id": 4, "tags": {"man_made": "works"}},
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        candidates = fetch_osm_candidates_sync(client, (10.0, 40.0, 10.1, 40.1))

    check("only tagged, positioned elements kept", len(candidates) == 2, str(candidates))
    by_id = {c["osm_id"]: c for c in candidates}
    check("node flare kept as strong", by_id[1]["evidence_class"] == "strong")
    check("way refinery uses center lat/lon", by_id[2]["lat"] == 40.02 and by_id[2]["lon"] == 10.02)
    check("tags preserved", by_id[2]["tags"]["name"] == "Big Refinery")


def test_ensure_osm_candidates_cache_hit(tmp: Path) -> None:
    print("\nensure_osm_candidates: cache-hit path never touches the network")
    db_path = tmp / "cache_osm.sqlite3"
    db = Database(db_path)
    now = int(time.time())
    conn = db.conn
    query_bbox = (10.0, 40.0, 10.1, 40.1)
    from nexfiremap.industrial import _snap_bbox

    snapped = _snap_bbox(query_bbox)
    # Pre-seed a fresh query-cache row for the snapped bbox this call will
    # use, and one candidate inside both the snapped *and* the original
    # query bbox - if the cache-hit branch weren't taken, this call would
    # try a real Overpass request and either hang or fail in this offline
    # test environment.
    conn.execute(
        "INSERT INTO industrial_query_cache (west, south, east, north, fetched_at) VALUES (?,?,?,?,?)",
        (*snapped, now),
    )
    conn.execute(
        "INSERT INTO industrial_sources (osm_type, osm_id, latitude, longitude, evidence_class, tags_json, fetched_at) "
        "VALUES ('node', 99, 40.05, 10.05, 'strong', '{}', ?)",
        (now,),
    )
    conn.commit()

    candidates = ensure_osm_candidates(conn, query_bbox)
    check("cached candidate returned", len(candidates) == 1, str(candidates))
    check("candidate fields intact", candidates[0]["evidence_class"] == "strong")
    db.close()


def test_persistence_features(tmp: Path) -> None:
    print("\npersistence_features: recurrence/stationarity/radiometric stats")
    db_path = tmp / "cache_persist.sqlite3"
    db = Database(db_path)
    conn = db.conn
    now = time.time()

    # A tight cluster of detections near (40.0, 10.0) over several distinct
    # days, plus one far-away detection that must not be counted.
    rows = []
    for day_offset in range(5):
        ts = now - day_offset * 86400
        rows.append(
            {
                "source": "VIIRS_NOAA20_NRT", "satellite": "N", "instrument": "VIIRS",
                "latitude": 40.0 + 0.001 * day_offset, "longitude": 10.0,
                "acq_date": time.strftime("%Y-%m-%d", time.gmtime(ts)),
                "acq_time": "0000", "acq_ts": int(ts),
                "brightness": 330.0, "brightness2": 295.0, "scan": 0.4, "track": 0.4,
                "confidence_raw": "n", "confidence_pct": None, "confidence_level": "nominal",
                "frp": 10.0 + day_offset, "daynight": "D" if day_offset % 2 == 0 else "N",
                "version": "2.0NRT",
            }
        )
    # Far away - outside the 2km radius used below.
    rows.append(
        {
            "source": "VIIRS_NOAA20_NRT", "satellite": "N", "instrument": "VIIRS",
            "latitude": 45.0, "longitude": 15.0,
            "acq_date": "2026-08-01", "acq_time": "0000", "acq_ts": int(now),
            "brightness": 330.0, "brightness2": 295.0, "scan": 0.4, "track": 0.4,
            "confidence_raw": "n", "confidence_pct": None, "confidence_level": "nominal",
            "frp": 500.0, "daynight": "D", "version": "2.0NRT",
        }
    )
    db.upsert_detections(rows)

    features = persistence_features(conn, 40.0, 10.0, radius_km=2.0, end_ts=now, window_days=30)
    check("only nearby detections counted", features["detection_count"] == 5, str(features))
    check("5 distinct days", features["distinct_days"] == 5, str(features))
    check("far-away detection excluded (FRP would spike otherwise)", features["frp_median"] < 100, str(features))
    check("centroid near the cluster", abs(features["centroid_lat"] - 40.002) < 0.01, str(features))
    check("spread is small (tight cluster)", features["spread_km"] < 1.0, str(features))
    check("day/night ratio computed", features["day_night_ratio"] is not None, str(features))

    empty = persistence_features(conn, 0.0, 0.0, radius_km=2.0, end_ts=now, window_days=30)
    check("no nearby detections -> zero count, no crash", empty["detection_count"] == 0, str(empty))
    check("no nearby detections -> None spread", empty["spread_km"] is None)
    check(
        "observed_days key present (None here since orbits is stubbed offline)",
        "observed_days" in features and features["observed_days"] is None,
        str(features.get("observed_days")),
    )
    db.close()


def test_persistence_features_separates_instruments(tmp: Path) -> None:
    print("\npersistence_features: MODIS/VIIRS spread computed separately, not pooled")
    db_path = tmp / "cache_instruments.sqlite3"
    db = Database(db_path)
    conn = db.conn
    now = time.time()

    def row(source, instrument, lat, lon, day_offset):
        ts = now - day_offset * 86400
        return {
            "source": source, "satellite": "N", "instrument": instrument,
            "latitude": lat, "longitude": lon,
            "acq_date": time.strftime("%Y-%m-%d", time.gmtime(ts)),
            "acq_time": "0000", "acq_ts": int(ts),
            "brightness": 330.0, "brightness2": 295.0, "scan": 0.4, "track": 0.4,
            "confidence_raw": "n", "confidence_pct": None, "confidence_level": "nominal",
            "frp": 10.0, "daynight": "D", "version": "2.0NRT",
        }

    rows = []
    # VIIRS: a genuinely tight cluster (~50m across).
    for i in range(4):
        rows.append(row("VIIRS_NOAA20_NRT", "VIIRS", 40.0 + 0.0003 * i, 10.0, i))
    # MODIS: coarser pixels, scattered further out (~1-2km) around a
    # *different* sub-centroid than the VIIRS points - pooling everything
    # into one centroid/spread would inflate the reported spread well
    # beyond what the VIIRS cluster alone shows.
    for i in range(4):
        rows.append(row("MODIS_NRT", "MODIS", 40.02 + 0.01 * i, 10.02, i))
    db.upsert_detections(rows)

    features = persistence_features(conn, 40.01, 10.01, radius_km=5.0, end_ts=now, window_days=30)
    check("all 8 detections counted", features["detection_count"] == 8, str(features))

    # The naive pooled-centroid spread this used to compute, for comparison.
    all_points = [(r["latitude"], r["longitude"]) for r in rows]
    pooled_lat = sum(p[0] for p in all_points) / len(all_points)
    pooled_lon = sum(p[1] for p in all_points) / len(all_points)
    from nexfiremap.geo import haversine_km

    pooled_spread = sum(haversine_km(pooled_lat, pooled_lon, la, lo) for la, lo in all_points) / len(all_points)

    check(
        "reported spread is much tighter than the naive pooled-centroid spread",
        features["spread_km"] < pooled_spread * 0.5,
        f"reported={features['spread_km']} pooled={pooled_spread}",
    )
    check("reported spread matches the tighter (VIIRS-only) cluster", features["spread_km"] < 0.1, str(features))
    db.close()


def test_score_source() -> None:
    print("\nscore_source: transparent rule-based score")
    strong_persistent = {
        "detection_count": 20, "distinct_days": 25, "span_days": 29.0,
        "spread_km": 0.1, "frp_median": 5.0, "frp_iqr": 0.2,
    }
    result = score_source(strong_persistent, "strong", window_days=30)
    expected = round(0.30 * 1.0 + 0.30 * (25 / 30) + 0.25 * max(0.0, 1 - 0.1 / 2.0) + 0.15 * (1 - 0.2 / 5.0), 4)
    check("score matches the documented linear formula exactly", result["score"] == expected, str(result))
    check("high score classified persistent_industrial", result["classification"] == "persistent_industrial", str(result))
    check("reason is non-empty and explains the call", len(result["reason"]) > 10)

    no_detections = {
        "detection_count": 0, "distinct_days": 0, "span_days": 0.0,
        "spread_km": None, "frp_median": None, "frp_iqr": None,
    }
    result2 = score_source(no_detections, "strong", window_days=30)
    check("no detections -> insufficient_evidence regardless of OSM tag", result2["classification"] == "insufficient_evidence", str(result2))

    weak = {
        "detection_count": 1, "distinct_days": 1, "span_days": 0.0,
        "spread_km": 3.0, "frp_median": 5.0, "frp_iqr": 4.0,
    }
    result3 = score_source(weak, "moderate", window_days=30)
    check("one-off weak-evidence candidate scores low", result3["score"] < THRESHOLD_AMBIGUOUS, str(result3))


def test_score_source_observed_days_denominator() -> None:
    print("\nscore_source: persistence uses observation opportunity when available")
    base = {
        "detection_count": 10, "distinct_days": 10, "span_days": 20.0,
        "spread_km": 0.1, "frp_median": 5.0, "frp_iqr": 0.2,
    }

    # No observed_days key at all (persistence_features couldn't compute
    # it, e.g. orbits unavailable) - falls back to window_days, the exact
    # pre-existing behaviour.
    no_orbit_data = dict(base)
    result_fallback = score_source(no_orbit_data, "strong", window_days=30)
    check("falls back to window_days when observed_days is absent", abs(result_fallback["p"] - 10 / 30) < 1e-9, str(result_fallback))

    # A location a satellite only genuinely passed over 12 days out of the
    # 30-day window - persistence should be judged against those 12 days of
    # real opportunity, not the full 30, so a location that was detected on
    # every day it was actually observed scores as *more* persistent, not
    # less, than the raw-calendar-day denominator would give it.
    with_orbit_data = {**base, "observed_days": 12}
    result_opportunity = score_source(with_orbit_data, "strong", window_days=30)
    check(
        "detected every observed day -> p reflects that, not calendar days",
        abs(result_opportunity["p"] - 10 / 12) < 1e-9,
        str(result_opportunity),
    )
    check(
        "opportunity-aware p is higher than the naive calendar-day p for the same evidence",
        result_opportunity["p"] > result_fallback["p"],
        f"{result_opportunity['p']} vs {result_fallback['p']}",
    )

    # Orbit approximation under-counting opportunity below what real
    # detections already prove happened must not make p exceed 1.0.
    undercounted = {**base, "observed_days": 3}
    result_guarded = score_source(undercounted, "strong", window_days=30)
    check("p never exceeds 1.0 even if observed_days undercounts", result_guarded["p"] <= 1.0, str(result_guarded))


def test_match_radius_and_matching() -> None:
    print("\nmatch_radius_km and match_detections")
    tight = {"spread_km": 0.1}
    loose = {"spread_km": 2.0}
    check("tight cluster -> small match radius", match_radius_km(tight) < match_radius_km(loose))

    sources = [
        {"id": 1, "lat": 40.0, "lon": 10.0, "classification": "persistent_industrial", "match_radius_km": 1.0, "score": 0.9},
        {"id": 2, "lat": 41.0, "lon": 11.0, "classification": "insufficient_evidence", "match_radius_km": 1.0, "score": 0.1},
    ]
    detections = [
        {"id": 100, "lat": 40.005, "lon": 10.0},   # near source 1, within radius
        {"id": 101, "lat": 41.005, "lon": 11.0},   # near source 2, but not flaggable
        {"id": 102, "lat": 30.0, "lon": 30.0},     # nowhere near anything
    ]
    matches = match_detections(detections, sources)
    check("detection near a flaggable source is matched", 100 in matches, str(matches))
    check("detection near a non-flaggable source is not matched", 101 not in matches, str(matches))
    check("distant detection is not matched", 102 not in matches, str(matches))
    check("matched classification carried through", matches[100]["classification"] == "persistent_industrial")


def test_apply_wildfire_override() -> None:
    print("\napply_wildfire_override")
    src = {
        "id": 1, "lat": 40.0, "lon": 10.0, "classification": "persistent_industrial", "match_radius_km": 0.5,
    }
    now = time.time()

    # Growing case: early detections tight near the source, later ones far
    # beyond its footprint.
    growing = (
        [{"id": i, "lat": 40.0 + 0.001 * i, "lon": 10.0, "ts": now + i * 60} for i in range(2)]
        + [{"id": 10 + i, "lat": 40.0 + 0.05 * i, "lon": 10.0, "ts": now + 3600 + i * 60} for i in range(2)]
    )
    matches = {d["id"]: {"source_id": 1, "classification": "persistent_industrial"} for d in growing}
    downgraded = apply_wildfire_override(growing, matches, [src])
    check("growth beyond footprint triggers override", downgraded > 0, str(matches))
    check(
        "downgraded entries relabelled possible_industrial_incident",
        all(m["classification"] == "possible_industrial_incident" for m in matches.values() if "override_reason" in m),
    )

    # Static case: everything stays tight near the source - no override.
    static = [{"id": i, "lat": 40.0 + 0.0001 * i, "lon": 10.0, "ts": now + i * 60} for i in range(6)]
    matches2 = {d["id"]: {"source_id": 1, "classification": "persistent_industrial"} for d in static}
    downgraded2 = apply_wildfire_override(static, matches2, [src])
    check("no growth -> no override", downgraded2 == 0, str(matches2))
    check(
        "classifications untouched when not overridden",
        all(m["classification"] == "persistent_industrial" for m in matches2.values()),
    )

    test_wildfire_override_on_real_matcher_output()


def test_wildfire_override_on_real_matcher_output() -> None:
    """The override must fire on state the matcher can actually produce.

    The case above builds its `matches` dict by hand, including detections
    kilometres outside the source's 0.5 km radius. `match_detections` cannot
    return those - it only ever matches within `match_radius_km` - so that test
    passed while the real pipeline could never reach the branch: measured over
    the matched subset alone, the late-phase distance is bounded by the radius,
    and the trigger asks for more than twice it. The safeguard was unreachable
    dead code, and README advertises it.

    So this drives `match_detections` for real and asserts the outcome.
    """
    print("\napply_wildfire_override on real match_detections output")
    now = time.time()
    source = {
        "id": 7, "lat": 40.0, "lon": 10.0, "score": 0.9,
        "classification": "persistent_industrial", "match_radius_km": 0.5,
    }

    # A fire igniting at the flare site: early detections sit on the source
    # (so they match), later ones march away from it (so they do not).
    early = [{"id": i, "lat": 40.0 + 0.0005 * i, "lon": 10.0, "ts": now + i * 60}
             for i in range(3)]
    late = [{"id": 20 + i, "lat": 40.0 + 0.02 * (i + 1), "lon": 10.0, "ts": now + 7200 + i * 60}
            for i in range(3)]
    detections = early + late

    matches = match_detections(detections, [source])
    # The premise: only the early, on-source detections match at all. If this
    # ever stops holding, the test below stops testing what it claims to.
    check("only on-source detections match", set(matches) == {d["id"] for d in early}, str(sorted(matches)))
    for det_id, match in matches.items():
        check(f"detection {det_id} matched inside the radius",
              match["distance_km"] <= source["match_radius_km"], str(match))

    downgraded = apply_wildfire_override(detections, matches, [source])
    check("a fire outgrowing the source is unmasked", downgraded > 0, str(matches))
    check("every matched detection is reclassified",
          all(m["classification"] == "possible_industrial_incident" for m in matches.values()),
          str(matches))
    check("the reason names the growth",
          all("grew from" in m.get("override_reason", "") for m in matches.values()))

    # ...and a genuinely static source still must not be unmasked, or the
    # classifier becomes useless noise during routine flaring.
    static = [{"id": 50 + i, "lat": 40.0 + 0.0002 * i, "lon": 10.0, "ts": now + i * 600}
              for i in range(6)]
    static_matches = match_detections(static, [source])
    check("a static source's detections all match", len(static_matches) == len(static))
    check("steady flaring is not unmasked",
          apply_wildfire_override(static, static_matches, [source]) == 0, str(static_matches))
    check("static classifications untouched",
          all(m["classification"] == "persistent_industrial" for m in static_matches.values()))


def test_scan_industrial_sources_job(tmp: Path) -> None:
    print("\nscan_industrial_sources job body: end-to-end (offline, pre-seeded OSM cache)")
    db_path = tmp / "cache_scan.sqlite3"
    db = Database(db_path)
    now = int(time.time())
    conn = db.conn

    bbox = (9.0, 39.0, 11.0, 41.0)
    from nexfiremap.industrial import _snap_bbox

    snapped = _snap_bbox(bbox)
    conn.execute(
        "INSERT INTO industrial_query_cache (west, south, east, north, fetched_at) VALUES (?,?,?,?,?)",
        (*snapped, now),
    )
    conn.execute(
        "INSERT INTO industrial_sources (osm_type, osm_id, latitude, longitude, evidence_class, tags_json, fetched_at) "
        "VALUES ('node', 555, 40.0, 10.0, 'strong', ?, ?)",
        (json.dumps({"man_made": "flare"}), now),
    )
    conn.commit()

    # 20 tightly-clustered detections across many distinct days near the
    # candidate - enough recurrence/stationarity to score as persistent.
    rows = []
    for day_offset in range(20):
        ts = now - day_offset * 86400
        rows.append(
            {
                "source": "VIIRS_NOAA20_NRT", "satellite": "N", "instrument": "VIIRS",
                "latitude": 40.0001 * (1 + day_offset % 3), "longitude": 10.0,
                "acq_date": time.strftime("%Y-%m-%d", time.gmtime(ts)),
                "acq_time": "0000", "acq_ts": int(ts),
                "brightness": 330.0, "brightness2": 295.0, "scan": 0.4, "track": 0.4,
                "confidence_raw": "n", "confidence_pct": None, "confidence_level": "nominal",
                "frp": 5.0, "daynight": "N", "version": "2.0NRT",
            }
        )
    db.upsert_detections(rows)

    job_id = db.create_job("scan_industrial_sources", {})
    result_dir = tmp / "job-scan"
    result_dir.mkdir()
    db.close()

    ctx = JobContext(job_id=job_id, db_path=str(db_path), result_dir=str(result_dir))
    result = scan_industrial_sources({"bbox": list(bbox), "window_days": 30}, ctx)

    check("candidate found", result["candidate_count"] == 1, str(result))
    check("source scored persistent_industrial", result["sources"][0]["classification"] == "persistent_industrial", str(result))
    check("detections matched to the source", len(result["matches"]) > 0, str(result["classification_counts"]))
    check(
        "classification_counts reflects the matches",
        result["classification_counts"].get("persistent_industrial", 0) == len(result["matches"]),
        str(result),
    )

    db2 = Database(db_path)
    score_row = db2.conn.execute("SELECT * FROM industrial_source_scores").fetchone()
    check("score persisted to industrial_source_scores", score_row is not None)
    check("original detections table untouched (still 20 rows)", db2.conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 20)
    db2.close()


def main() -> int:
    test_build_overpass_query()
    test_evidence_class()
    test_fetch_osm_candidates_sync()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        test_ensure_osm_candidates_cache_hit(tmp)
        test_persistence_features(tmp)
        test_persistence_features_separates_instruments(tmp)
    test_score_source()
    test_score_source_observed_days_denominator()
    test_match_radius_and_matching()
    test_apply_wildfire_override()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        test_scan_industrial_sources_job(tmp)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
