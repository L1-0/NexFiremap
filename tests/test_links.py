"""Cross-module links, incident AOI, and the safety loop they enable.

The central property under test is the one the whole design rests on:
**a link is a frozen snapshot, not a live reference.** Events get re-clustered
and models re-run, so a plan justified by "the 09:40 run" has to keep meaning
that afterwards. Several checks below deliberately mutate or delete the
referent and assert the link is unchanged.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from fastapi.testclient import TestClient

from nexfiremap import safety
from nexfiremap.api import create_app
from nexfiremap.config import load_settings
from nexfiremap.db import Database
from nexfiremap.operations import (
    LINK_KINDS, OperationsError, OperationsStore, default_period,
    normalise_aoi, point_in_polygon,
)
from nexfiremap.telemetry import TelemetryManager


def _iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)) \
        .isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _store(root: Path) -> tuple[Database, OperationsStore]:
    db = Database(root / "links.sqlite3")
    return db, OperationsStore(db)


def check_snapshot_is_frozen() -> None:
    """The property everything else depends on: re-clustering an event must not
    rewrite what an incident recorded about it."""
    with tempfile.TemporaryDirectory() as temp:
        db, store = _store(Path(temp))
        try:
            incident = store.create_incident({"name": "Frozen"}, "IC")
            store.create_period(incident["id"], default_period(), "IC")
            db.conn.execute(
                "INSERT INTO events (bbox_west,bbox_south,bbox_east,bbox_north,centroid_lat,"
                "centroid_lon,first_seen,last_seen,detection_count,sources_json,params_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (11.5, 48.1, 11.7, 48.2, 48.15, 11.6, 1, 2, 14, "[]", "{}", 3))
            db.conn.commit()
            event_id = db.conn.execute("SELECT id FROM events").fetchone()[0]

            store.add_link(incident["id"], "event", str(event_id),
                           {"detection_count": 14, "bbox": [11.5, 48.1, 11.7, 48.2]}, "", "IC")

            # The clustering job runs again and rewrites the event entirely...
            db.conn.execute("UPDATE events SET detection_count=99, bbox_east=99.0 WHERE id=?", (event_id,))
            db.conn.commit()
            (link,) = store.list_links(incident["id"], "event")
            assert link["snapshot"]["detection_count"] == 14, "the snapshot followed the live row"
            assert link["snapshot"]["bbox"][2] == 11.7

            # ...and even deleting the event leaves the record of what was seen.
            db.conn.execute("DELETE FROM events WHERE id=?", (event_id,))
            db.conn.commit()
            (link,) = store.list_links(incident["id"], "event")
            assert link["snapshot"]["detection_count"] == 14
            assert link["snapshot"]["linked_at"], "a snapshot must say when it was taken"
        finally:
            db.close()


def check_link_rules() -> None:
    with tempfile.TemporaryDirectory() as temp:
        db, store = _store(Path(temp))
        try:
            incident = store.create_incident({"name": "Rules"}, "IC")
            store.create_period(incident["id"], default_period(), "IC")

            # A link with no snapshot is a live reference, which is the one
            # thing this table exists to prevent.
            for label, snapshot in (("empty", {}), ("not a dict", "nope")):
                try:
                    store.add_link(incident["id"], "event", "1", snapshot, "", "IC")
                    raise AssertionError(f"{label} snapshot was accepted")
                except OperationsError:
                    pass

            try:
                store.add_link(incident["id"], "wormhole", "1", {"a": 1}, "", "IC")
                raise AssertionError("an unknown link kind was accepted")
            except OperationsError:
                pass
            assert "event" in LINK_KINDS and "model_run" in LINK_KINDS

            # Idempotent: "attach to incident" is a button, and a second press
            # must refresh rather than duplicate.
            store.add_link(incident["id"], "alert", "cap-1", {"severity": "Severe"}, "", "IC")
            store.add_link(incident["id"], "alert", "cap-1", {"severity": "Extreme"}, "", "IC")
            links = store.list_links(incident["id"], "alert")
            assert len(links) == 1 and links[0]["snapshot"]["severity"] == "Extreme"

            # Removing a link keeps the evidence in the audit trail.
            assert store.remove_link(incident["id"], links[0]["id"], "IC") is True
            assert store.list_links(incident["id"], "alert") == []
            assert db.conn.execute(
                "SELECT COUNT(*) FROM incident_audit_log WHERE entity_type='link'"
            ).fetchone()[0] >= 2
        finally:
            db.close()


def check_aoi() -> None:
    with tempfile.TemporaryDirectory() as temp:
        db, store = _store(Path(temp))
        try:
            incident = store.create_incident({"name": "AOI"}, "IC")
            store.create_period(incident["id"], default_period(), "IC")

            # A bbox and a polygon must both normalise to one closed polygon,
            # so nothing downstream branches on shape.
            store.set_aoi(incident["id"], [11.4, 48.0, 11.8, 48.3], "IC")
            aoi = store.get_aoi(incident["id"])
            assert aoi["type"] == "Polygon"
            ring = aoi["coordinates"][0]
            assert ring[0] == ring[-1], "the ring must be closed"

            assert [i["name"] for i in store.incidents_covering(48.15, 11.6)] == ["AOI"]
            assert store.incidents_covering(40.0, 2.0) == []
            # A point outside the bbox but inside its lat range must not match.
            assert store.incidents_covering(48.15, 20.0) == []

            # An incident with no AOI is never matched - "we did not draw an
            # area" is not the same claim as "this point is inside our area".
            other = store.create_incident({"name": "No AOI"}, "IC")
            assert other["name"] not in [i["name"] for i in store.incidents_covering(48.15, 11.6)]

            store.set_aoi(incident["id"], None, "IC")
            assert store.get_aoi(incident["id"]) is None
            assert store.incidents_covering(48.15, 11.6) == []

            for bad in ([1, 2], {"type": "Point", "coordinates": [1, 2]},
                        {"type": "Polygon", "coordinates": [[[999, 1], [2, 2], [3, 3]]]}):
                try:
                    normalise_aoi(bad)
                    raise AssertionError(f"{bad} was accepted as an AOI")
                except OperationsError:
                    pass
        finally:
            db.close()


def check_point_in_polygon() -> None:
    """Ray casting, including the vertex-level case that naive implementations
    get wrong by counting a shared vertex twice."""
    square = normalise_aoi([0.0, 0.0, 10.0, 10.0])
    assert point_in_polygon(5, 5, square) is True
    assert point_in_polygon(15, 5, square) is False
    assert point_in_polygon(5, 15, square) is False
    assert point_in_polygon(-1, 5, square) is False
    assert point_in_polygon(5, 5, None) is False

    # A concave shape: the notch must read as outside.
    lshape = {"type": "Polygon", "coordinates": [[
        [0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10], [0, 0]]]}
    assert point_in_polygon(2, 2, lshape) is True
    assert point_in_polygon(8, 8, lshape) is False, "the notch is outside the L"


def check_export_import_round_trip() -> None:
    """Links must survive a handover: the receiving installation has none of
    the source events, so the snapshot is the only thing that carries meaning."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        db, store = _store(root)
        try:
            incident = store.create_incident({"name": "Handover"}, "IC")
            store.create_period(incident["id"], default_period(), "IC")
            store.set_aoi(incident["id"], [11.4, 48.0, 11.8, 48.3], "IC")
            store.add_link(incident["id"], "event", "77",
                           {"detection_count": 5, "bbox": [11.5, 48.1, 11.6, 48.2]}, "seen", "IC")
            bundle = store.export_bundle(incident["id"])
            assert len(bundle["links"]) == 1
        finally:
            db.close()

        target_db = Database(root / "target.sqlite3")
        try:
            target = OperationsStore(target_db)
            preview = target.preview_import(bundle)
            assert preview["valid"], preview.get("errors")
            target.import_bundle(bundle, "receiving")
            links = target.list_links(incident["id"])
            assert len(links) == 1
            assert links[0]["snapshot"]["detection_count"] == 5, \
                "the snapshot is what crosses the handover"
        finally:
            target_db.close()


def check_safety_warnings_are_readable() -> None:
    """The safety loop's output must be readable back, not just recorded.

    `record_warnings` and `record_watch` have written into the incident audit
    trail since the integration work, and `AuditLog` had no read method at all -
    so the most safety-relevant output in the product ("this crew is inside the
    forecast perimeter") reached the device that posted the position, and the
    log, and nobody else. The only way to see it was to download the whole
    handover export. This pins the read surface that closes that loop.
    """
    from fastapi.testclient import TestClient

    from nexfiremap import safety
    from nexfiremap.api import create_app

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = dataclasses.replace(
            load_settings(), db_path=root / "w.sqlite3", tile_cache_dir=root / "tiles",
            lan_mode=False)
        with TestClient(create_app(settings)) as client:
            incident = client.post("/api/operations/incidents",
                                   json={"name": "Warning surface"}).json()["incident"]
            base = f"/api/operations/incidents/{incident['id']}/safety-warnings"

            empty = client.get(base)
            assert empty.status_code == 200, empty.text
            assert empty.json()["entries"] == []
            assert empty.json()["warning_count"] == 0

            store = client.app.state.operations
            safety.record_warnings(store, incident["id"], [
                {"code": "inside_hazard", "feature_id": "f1",
                 "callsign": "FL 11/1", "message": "FL 11/1 is inside a burn area"},
                {"code": "modelled_arrival", "feature_id": "f2",
                 "callsign": "FL 11/2", "message": "fire modelled to reach FL 11/2 in 0.6 h"},
            ])

            body = client.get(base).json()
            assert body["warning_count"] == 2, body
            assert {e["entity_type"] for e in body["entries"]} == {"safety_warning"}
            messages = [e["payload"]["message"] for e in body["entries"]]
            assert any("inside a burn area" in m for m in messages), messages
            # Newest first, so a panel showing the top N shows the latest N.
            assert body["entries"] == sorted(
                body["entries"], key=lambda e: e["changed_at"], reverse=True)

            # `since` is what keeps the poll incremental rather than re-rendering
            # the same warnings on every 15-second cycle. Tested with the
            # timestamp both properly encoded and passed raw: `utcnow()` emits
            # "+00:00", an unencoded "+" arrives as a space, and a space sorts
            # below "+" - so the naive form used to return every entry at that
            # instant again, repeating a safety warning forever.
            from urllib.parse import quote

            newest = body["entries"][0]["changed_at"]
            assert client.get(f"{base}?since={quote(newest)}").json()["entries"] == []
            assert client.get(f"{base}?since={newest}").json()["entries"] == [], \
                "an unencoded '+' in the timestamp re-delivered warnings already seen"

            # An unknown incident is a 404, not an empty list that reads as
            # "nothing is wrong".
            assert client.get("/api/operations/incidents/nope/safety-warnings").status_code == 404

            # A malformed payload must not hide that the event happened.
            with store.db._write_lock:
                store.db.conn.execute(
                    "UPDATE incident_audit_log SET payload_json='{bad' "
                    "WHERE entity_type='safety_warning' AND entity_id='f1'")
                store.db.conn.commit()
            survived = client.get(base).json()
            assert survived["warning_count"] == 2, "a bad payload dropped the whole row"
            assert any(e["payload"] == {} for e in survived["entries"])


def check_safety_loop() -> None:
    """A crew inside a hazard area, and a crew in the modelled path, both get
    warned - and neither warning is allowed to cost the position."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = dataclasses.replace(
            load_settings(), db_path=root / "links.sqlite3", job_dir=root / "jobs")
        db, store = _store(root)
        try:
            telemetry = TelemetryManager(store, settings)
            incident = store.create_incident({"name": "Safety"}, "IC")
            period = store.create_period(incident["id"], default_period(), "IC")
            feed = telemetry.create_source(incident["id"], {"name": "AVL"}, "IC")
            store.create_feature(incident["id"], {
                "period_id": period["id"], "feature_type": "evacuation_area",
                "title": "Raeumung Nord", "geometry": {"type": "Polygon", "coordinates": [[
                    [11.55, 48.10], [11.65, 48.10], [11.65, 48.20], [11.55, 48.20], [11.55, 48.10]]]}}, "IC")

            def push(callsign: str, lat: float, lon: float, offset: int = -60) -> dict:
                stamp = _iso(offset)
                return telemetry.ingest(feed["id"], feed["ingest_token"], [{
                    "external_id": f"{callsign}-{stamp}", "callsign": callsign,
                    "observed_at": stamp, "latitude": lat, "longitude": lon}])

            inside = push("FL 11/1", 48.15, 11.60)
            assert inside["accepted"] == 1
            assert [w["code"] for w in inside["warnings"]] == ["inside_hazard_area"]

            assert push("FL 11/2", 40.0, 2.0)["warnings"] == [], "a unit far away must not be warned"

            # An arrival raster the crew is standing in.
            job_dir = root / "jobs" / "7"
            job_dir.mkdir(parents=True)
            hours = np.full((64, 64), 99.0, dtype=np.float32)
            hours[20:44, 20:44] = 0.5
            np.savez_compressed(job_dir / "impact_surface.npz", earliest_hours=hours,
                                median_hours=hours + 0.4, latest_hours=hours + 1.0)
            db.conn.execute(
                "INSERT INTO jobs (id,kind,status,params_json,result_json,created_at,finished_at) "
                "VALUES (7,'propagation','done','{}',?,1,1)",
                (json.dumps({"bounds": [[48.10, 11.55], [48.20, 11.65]]}),))
            db.conn.commit()
            store.add_link(incident["id"], "model_run", "7", {"job_id": 7}, "", "IC")
            safety.clear_cache()

            modelled = push("FL 11/3", 48.15, 11.60, -30)
            codes = sorted(w["code"] for w in modelled["warnings"])
            assert codes == ["inside_hazard_area", "modelled_arrival_imminent"], codes
            arrival = next(w for w in modelled["warnings"] if w["code"] == "modelled_arrival_imminent")
            # The band, not a single figure - and stated as modelled.
            assert set(arrival["hours"]) == {"earliest", "median", "latest"}
            assert "modelled, not observed" in arrival["message"]

            # Warnings land in the audit trail, so they travel in a handover.
            assert db.conn.execute(
                "SELECT COUNT(*) FROM incident_audit_log WHERE entity_type='safety_warning'"
            ).fetchone()[0] >= 3

            # A broken raster must not cost the position - the failure policy
            # that matters most in this module.
            (job_dir / "impact_surface.npz").write_bytes(b"not an npz")
            safety.clear_cache()
            still = push("FL 11/4", 48.15, 11.60, -20)
            assert still["accepted"] == 1, "a failed evaluation rejected the position"
        finally:
            db.close()


def check_control_lines() -> None:
    """Built control lines become barriers; planned ones must not.

    Feeding a plan into the model as though it were built would produce a
    forecast that flatters the plan - the most dangerous way this could be
    wrong."""
    from nexfiremap.safety import control_mask

    with tempfile.TemporaryDirectory() as temp:
        db, store = _store(Path(temp))
        try:
            incident = store.create_incident({"name": "Lines"}, "IC")
            period = store.create_period(incident["id"], default_period(), "IC")
            bounds = [[48.10, 11.55], [48.20, 11.65]]

            assert not control_mask(db, incident["id"], bounds, (64, 64)).any()

            store.create_feature(incident["id"], {
                "period_id": period["id"], "feature_type": "tactical_line", "status": "proposed",
                "title": "Geplant",
                "geometry": {"type": "LineString", "coordinates": [[11.56, 48.19], [11.64, 48.19]]}}, "IC")
            assert not control_mask(db, incident["id"], bounds, (64, 64)).any(), \
                "a planned line must not act as a barrier"

            store.create_feature(incident["id"], {
                "period_id": period["id"], "feature_type": "tactical_line", "status": "completed",
                "title": "Riegel",
                "geometry": {"type": "LineString", "coordinates": [[11.56, 48.12], [11.64, 48.18]]}}, "IC")
            mask = control_mask(db, incident["id"], bounds, (64, 64))
            assert mask.any(), "a completed line must act as a barrier"

            # No diagonal gaps: a barrier with a hole is not a barrier. Every
            # row the line spans must carry at least one marked cell.
            rows = np.argwhere(mask)[:, 0]
            spanned = set(range(rows.min(), rows.max() + 1))
            assert spanned <= set(rows.tolist()), "the rasterised line has gaps"
        finally:
            db.close()


def check_http_surface() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = dataclasses.replace(
            load_settings(), db_path=root / "api.sqlite3", tile_cache_dir=root / "tiles",
            job_dir=root / "jobs", lan_mode=False)
        with TestClient(create_app(settings)) as client:
            now = int(time.time())
            db = client.app.state.db
            db.conn.execute(
                "INSERT INTO events (bbox_west,bbox_south,bbox_east,bbox_north,centroid_lat,"
                "centroid_lon,first_seen,last_seen,detection_count,sources_json,params_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (11.5, 48.1, 11.7, 48.25, 48.17, 11.6, now - 7200, now - 600, 14, "[]", "{}", now))
            db.conn.execute(
                "INSERT INTO detections (source,satellite,instrument,latitude,longitude,acq_date,"
                "acq_time,acq_ts,brightness,confidence_level,frp,daynight,version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("VIIRS_SNPP_NRT", "N", "VIIRS", 48.171, 11.601, "2026-08-15", "1000",
                 now - 900, 350.0, "nominal", 12.5, "D", "2"))
            db.conn.commit()
            event_id = db.conn.execute("SELECT id FROM events").fetchone()[0]

            # The route that replaces five minutes of typing with one click.
            created = client.post("/api/operations/incidents/from_context",
                                  json={"event_id": event_id, "name": "Waldbrand Nord"})
            assert created.status_code == 201, created.text
            payload = created.json()
            incident_id = payload["incident"]["id"]
            assert payload["period"] and payload["scenario"], "an incident must not start empty"
            assert payload["aoi"]["type"] == "Polygon"
            assert payload["links"][0]["kind"] == "event"
            assert payload["links"][0]["snapshot"]["detection_count"] == 14

            # `covering` is a literal path where incidents declares a
            # parameter; the router include order is what keeps it reachable.
            covering = client.get("/api/operations/incidents/covering?lat=48.17&lon=11.6")
            assert covering.status_code == 200, covering.text
            assert [i["id"] for i in covering.json()] == [incident_id]

            situation = client.get("/api/situation?lat=48.17&lon=11.6&radius_km=5").json()
            labels = {section["provider"]: section["summary"] for section in situation["sections"]}
            assert "detections" in labels and "1 within" in labels["detections"]
            assert "event" in labels and "14 detections" in labels["event"]
            assert "Waldbrand Nord" in labels["incidents"]
            # A provider that could not run is NAMED, never silently dropped -
            # "we did not ask" and "there is nothing there" are different
            # answers when someone is deciding whether to send a crew.
            assert isinstance(situation["unavailable"], list)

            watch = client.get(f"/api/operations/watch?since_ts={now - 3600}").json()
            assert watch["hits"] and watch["hits"][0]["incident_id"] == incident_id
            assert watch["hits"][0]["count"] >= 1

            assert client.get(f"/api/operations/incidents/{incident_id}/links").json()
            assert client.put(f"/api/operations/incidents/{incident_id}/aoi",
                              json={"bbox": [11.0, 48.0, 12.0, 49.0]}).status_code == 200
            assert client.post("/api/operations/incidents/from_context",
                               json={}).status_code == 400
            assert client.post("/api/operations/incidents/from_context",
                               json={"event_id": 99999}).status_code == 404

            # The two commit paths the map tool palette's area tool writes
            # through (static/js/tools.js). Its geometry helpers are JavaScript
            # and this repo has no JS test runner, so what is pinned here is the
            # server contract they depend on: a polygon posted as an AOI, and
            # the same polygon posted as an area feature.
            drawn = {"type": "Polygon", "coordinates": [[
                [11.50, 48.10], [11.60, 48.10], [11.60, 48.20], [11.50, 48.20], [11.50, 48.10]]]}
            aoi_set = client.put(f"/api/operations/incidents/{incident_id}/aoi",
                                 json={"aoi": drawn})
            assert aoi_set.status_code == 200, aoi_set.text
            stored = client.get(f"/api/operations/incidents/{incident_id}/aoi").json()["aoi"]
            assert stored["type"] == "Polygon"
            assert stored["coordinates"][0][0] == stored["coordinates"][0][-1], \
                "the server must close the ring the tool sends"

            # Every AREA_TYPES value must be offerable, because the tool builds
            # its dropdown from the server's own vocabulary rather than a
            # hardcoded list - so any of them can arrive here.
            meta = client.get("/api/operations/meta").json()
            area_types = meta["geometry"]["Polygon"]
            assert "evacuation_area" in area_types
            workspace = client.get(f"/api/operations/incidents/{incident_id}").json()
            period_id = workspace["operational_periods"][0]["id"]
            for feature_type in area_types:
                created_feature = client.post(
                    f"/api/operations/incidents/{incident_id}/features",
                    json={"period_id": period_id, "feature_type": feature_type,
                          "geometry": drawn, "title": f"Drawn {feature_type}"})
                assert created_feature.status_code == 201, \
                    f"{feature_type} rejected: {created_feature.text}"


def main() -> None:
    check_snapshot_is_frozen()
    check_link_rules()
    check_aoi()
    check_point_in_polygon()
    check_export_import_round_trip()
    check_safety_loop()
    check_safety_warnings_are_readable()
    check_control_lines()
    check_http_surface()
    print("Cross-module link, AOI and safety-loop checks passed.")


if __name__ == "__main__":
    main()
