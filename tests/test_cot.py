"""Cursor on Target parse/serialise round-trips against realistic TAK payloads.

The fixtures below are shaped like what ATAK actually emits - including the
awkward parts (the 9999999.0 "unknown" sentinel, a no-fix device reporting
0,0, back-to-back events with no enclosing document element) rather than
idealised XML, since those are exactly the cases that would otherwise reach
production untested.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.ingest import CONTRACT_OVERLAY, CONTRACT_POSITION, IngestError
from nexfiremap.ingest import cot

ATAK_UNIT = """<?xml version="1.0" encoding="UTF-8"?>
<event version="2.0" uid="ANDROID-352214" type="a-f-G-U-C" how="m-g"
       time="2026-08-14T10:15:30.000Z" start="2026-08-14T10:15:30.000Z"
       stale="2026-08-14T10:20:30.000Z">
  <point lat="48.1372000" lon="11.5755000" hae="519.4" ce="9.5" le="9999999.0"/>
  <detail>
    <contact callsign="FLORIAN 11/1"/>
    <__group name="Cyan" role="Team Member"/>
    <track speed="12.5" course="271.3"/>
    <remarks>en route</remarks>
  </detail>
</event>"""

# A device that has not acquired GPS: real TAK traffic, must not be ingested.
ATAK_NO_FIX = """<event version="2.0" uid="ANDROID-000" type="a-f-G-U-C" how="m-g"
       time="2026-08-14T10:15:31.000Z" start="2026-08-14T10:15:31.000Z"
       stale="2026-08-14T10:20:31.000Z">
  <point lat="0.0" lon="0.0" hae="9999999.0" ce="9999999.0" le="9999999.0"/>
  <detail><contact callsign="NOFIX"/></detail>
</event>"""

ATAK_DRAWN_AREA = """<event version="2.0" uid="POLY-1" type="b-m-p-w" how="h-e"
       time="2026-08-14T10:16:00.000Z" start="2026-08-14T10:16:00.000Z"
       stale="2026-08-14T11:16:00.000Z">
  <point lat="48.1400000" lon="11.5800000" hae="9999999.0" ce="9999999.0" le="9999999.0"/>
  <detail>
    <contact callsign="Burn area A"/>
    <shape>
      <polyline closed="true">
        <vertex lat="48.140" lon="11.570"/>
        <vertex lat="48.145" lon="11.580"/>
        <vertex lat="48.138" lon="11.590"/>
      </polyline>
    </shape>
  </detail>
</event>"""

ATAK_DRAWN_LINE = """<event version="2.0" uid="LINE-1" type="b-l-l" how="h-e"
       time="2026-08-14T10:17:00.000Z" start="2026-08-14T10:17:00.000Z"
       stale="2026-08-14T11:17:00.000Z">
  <point lat="48.1400000" lon="11.5800000" hae="9999999.0" ce="9999999.0" le="9999999.0"/>
  <detail>
    <contact callsign="Control line"/>
    <shape><polyline closed="false">
      <vertex lat="48.140" lon="11.570"/><vertex lat="48.145" lon="11.580"/>
    </polyline></shape>
  </detail>
</event>"""


def check_position_parse() -> None:
    reports = cot.parse(ATAK_UNIT.encode())
    assert len(reports) == 1
    report = reports[0]

    assert report["callsign"] == "FLORIAN 11/1", "contact/@callsign must win over the uid"
    assert report["latitude"] == 48.1372 and report["longitude"] == 11.5755
    assert report["observed_at"] == "2026-08-14T10:15:30.000Z"
    # CoT speed is m/s; NexFiremap stores km/h. 12.5 m/s == 45 km/h.
    assert report["speed_kmh"] == 45.0, report["speed_kmh"]
    assert report["heading_deg"] == 271.3
    assert report["altitude_m"] == 519.4
    assert report["accuracy_m"] == 9.5
    # le="9999999.0" is CoT's "unknown" sentinel, never a real measurement.
    assert report["cot_uid"] == "ANDROID-352214"
    assert report["cot_type"] == "a-f-G-U-C"
    assert report["cot_remarks"] == "en route"

    # The replay key must combine uid and time: the same device sends a new
    # event every few seconds under one uid, so uid alone would collide.
    assert report["external_id"] == "ANDROID-352214@2026-08-14T10:15:30.000Z"

    # Every key TelemetryManager.ingest reads must be present, or a CoT batch
    # would take a different validation path from a native JSON batch.
    for key in ("external_id", "callsign", "observed_at", "latitude", "longitude",
                "altitude_m", "speed_kmh", "heading_deg", "accuracy_m"):
        assert key in report, key


def check_unknown_sentinels() -> None:
    payload = ATAK_UNIT.replace('hae="519.4"', 'hae="9999999.0"').replace('ce="9.5"', 'ce="9999999.0"')
    report = cot.parse(payload.encode())[0]
    assert report["altitude_m"] is None, "9999999.0 hae must read as absent, not as 9999 km altitude"
    assert report["accuracy_m"] is None, "9999999.0 ce must read as absent, not as 9999 km accuracy"


def check_no_fix_skipped() -> None:
    # A no-fix atom alone leaves nothing to ingest, which is an error for the
    # single-payload HTTP path...
    try:
        cot.parse(ATAK_NO_FIX.encode())
        raise AssertionError("a 0,0 no-fix atom was ingested")
    except IngestError:
        pass
    # ...but mixed with a good report it must be silently skipped, not fail
    # the batch and discard the position that did have a fix.
    reports = cot.parse((ATAK_UNIT + ATAK_NO_FIX).encode())
    assert len(reports) == 1 and reports[0]["callsign"] == "FLORIAN 11/1"


def check_multi_event_stream() -> None:
    # TAK servers stream events back to back with no document element, which
    # is not well-formed XML until it is wrapped.
    stream = ATAK_UNIT + "\n" + ATAK_DRAWN_AREA
    events = cot.parse_events(stream.encode())
    assert len(events) == 2
    assert [item["contract"] for item in events] == [CONTRACT_POSITION, CONTRACT_OVERLAY]


def check_overlay_parse() -> None:
    (geometry, properties), = cot.overlays(ATAK_DRAWN_AREA.encode())
    assert geometry["type"] == "Polygon"
    ring = geometry["coordinates"][0]
    # TAK omits the repeated closing vertex that GeoJSON requires.
    assert ring[0] == ring[-1], "polygon ring must be closed for _validate_geometry"
    assert len(ring) == 4
    assert ring[0] == [11.570, 48.140], "coordinates must be [lon, lat]"
    assert properties["title"] == "Burn area A"
    assert properties["cot_uid"] == "POLY-1"
    # Geometry outranks the type table: b-m-p-w maps to a point type, but the
    # sender drew a polygon, so the feature type must be area-shaped.
    assert properties["feature_type"] == "burn_area", properties["feature_type"]

    (line_geometry, line_properties), = cot.overlays(ATAK_DRAWN_LINE.encode())
    assert line_geometry["type"] == "LineString" and len(line_geometry["coordinates"]) == 2
    assert line_properties["feature_type"] in {"fire_perimeter", "tactical_line", "active_edge",
                                               "inactive_edge", "escape_route", "spread_arrow",
                                               "division_boundary", "branch_boundary",
                                               "arrival_time_line", "road_restriction"}


def check_malformed_rejected() -> None:
    for label, payload in (
        ("XXE doctype", b'<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><event uid="a" type="a-f"/>'),
        ("empty", b"   "),
        ("no event", b"<something/>"),
        ("no point", b'<event uid="a" type="a-f-G" time="2026-08-14T10:00:00Z"/>'),
        ("no uid", b'<event type="a-f-G" time="2026-08-14T10:00:00Z"><point lat="1" lon="2"/></event>'),
        ("bad latitude", b'<event uid="a" type="a-f-G" time="2026-08-14T10:00:00Z"><point lat="99" lon="2"/></event>'),
        ("naive time", b'<event uid="a" type="a-f-G" time="2026-08-14T10:00:00"><point lat="1" lon="2"/></event>'),
        ("short polyline", b'<event uid="p" type="b-m-p-w" time="2026-08-14T10:00:00Z">'
                           b'<point lat="1" lon="2"/><detail><shape><polyline closed="true">'
                           b'<vertex lat="1" lon="2"/><vertex lat="3" lon="4"/>'
                           b'</polyline></shape></detail></event>'),
    ):
        try:
            cot.parse_events(payload)
            raise AssertionError(f"{label} payload was accepted")
        except IngestError:
            pass


def check_render_feature() -> None:
    feature = {
        "geometry": {"type": "Polygon", "coordinates": [[[11.57, 48.14], [11.58, 48.145],
                                                         [11.59, 48.138], [11.57, 48.14]]]},
        "properties": {"id": "abc123", "feature_type": "burn_area", "title": "Burn area A",
                       "status": "confirmed", "observed_at": "2026-08-14T10:16:00.000Z"},
    }
    event = cot.render_feature(feature)
    assert event.tag == "event"
    assert event.get("type") == "b-l-l"
    assert event.get("uid") == "nexfiremap-abc123"
    assert event.get("time") == "2026-08-14T10:16:00.000Z"
    point = event.find("point")
    # Representative point must fall inside the drawn extent.
    assert 48.13 < float(point.get("lat")) < 48.15
    assert 11.56 < float(point.get("lon")) < 11.60
    polyline = event.find(".//polyline")
    assert polyline is not None and polyline.get("closed") == "true"
    assert len(polyline.findall("vertex")) == 4
    assert event.find("detail/contact").get("callsign") == "Burn area A"

    # A feature that arrived from CoT keeps its original uid, so a round trip
    # updates the sender's marker rather than duplicating it on their screen.
    feature["properties"]["cot_uid"] = "POLY-1"
    assert cot.render_feature(feature).get("uid") == "POLY-1"


def check_render_position() -> None:
    position = {
        "geometry": {"type": "Point", "coordinates": [11.5755, 48.1372]},
        "properties": {"callsign": "FLORIAN 11/1", "observed_at": "2026-08-14T10:15:30.000Z",
                       "speed_kmh": 45.0, "heading_deg": 271.3, "accuracy_m": 9.5,
                       "altitude_m": 519.4, "stale": False},
    }
    event = cot.render_position(position)
    assert event.get("type") == "a-f-G-E-V"
    assert event.find("detail/contact").get("callsign") == "FLORIAN 11/1"
    # km/h back to m/s on the way out - the inverse of the parse conversion.
    assert abs(float(event.find("detail/track").get("speed")) - 12.5) < 1e-6
    point = event.find("point")
    assert point.get("ce") == "9.5" and point.get("hae") == "519.4"

    absent = {"geometry": {"type": "Point", "coordinates": [11.5, 48.1]},
              "properties": {"callsign": "X", "observed_at": "2026-08-14T10:15:30.000Z",
                             "speed_kmh": None, "heading_deg": None, "accuracy_m": None,
                             "altitude_m": None, "stale": True}}
    absent_event = cot.render_position(absent)
    assert absent_event.find("point").get("ce") == "9999999.0", "absent accuracy must use the sentinel"
    assert absent_event.find("detail/remarks").text == "position is stale"


def check_round_trip() -> None:
    """Parse an ATAK event, render it back, and re-parse - the values that
    survive a full cycle are the ones interop actually depends on."""
    original = cot.parse(ATAK_UNIT.encode())[0]
    rendered = cot.render_position({
        "geometry": {"type": "Point", "coordinates": [original["longitude"], original["latitude"]]},
        "properties": {"callsign": original["callsign"], "observed_at": original["observed_at"],
                       "speed_kmh": original["speed_kmh"], "heading_deg": original["heading_deg"],
                       "accuracy_m": original["accuracy_m"], "altitude_m": original["altitude_m"],
                       "cot_uid": original["cot_uid"]},
    })
    again = cot.parse(ET.tostring(rendered))[0]
    for key in ("callsign", "latitude", "longitude", "observed_at", "speed_kmh",
                "heading_deg", "accuracy_m", "altitude_m", "cot_uid"):
        assert again[key] == original[key], f"{key}: {again[key]!r} != {original[key]!r}"


def check_serialize() -> None:
    feature = {"geometry": {"type": "Point", "coordinates": [11.5, 48.1]},
               "properties": {"id": "x", "feature_type": "command_post", "title": "CP"}}
    payload = cot.serialize([cot.render_feature(feature)])
    assert payload.startswith(b"<?xml")
    root = ET.fromstring(payload)
    assert root.tag == "events" and len(root.findall("event")) == 1

    # A command post is genuinely an *atom* in CoT terms (a-f-G-U-C), so
    # classifying purely by type prefix would read our own exported feature
    # back as a vehicle position. The __nexfiremap extension element is what
    # keeps the round trip lossless.
    assert root.find("event").get("type") == "a-f-G-U-C"
    assert cot.overlays(payload)[0][1]["feature_type"] == "command_post"
    assert cot.parse_events(payload)[0]["contract"] == CONTRACT_OVERLAY

    # ...and it must not over-trigger: a genuine ATAK unit marker carrying the
    # same type code but no extension element is still a position report.
    assert cot.parse_events(ATAK_UNIT.encode())[0]["contract"] == CONTRACT_POSITION


def check_http_ingest() -> None:
    """A CoT payload posted to the real feed endpoint must take the identical
    write path as a native JSON batch - same token check, same replay-safety,
    same derived quality flags. That equivalence is the whole design claim, so
    it is asserted against the assembled app rather than the parser alone."""
    import dataclasses
    import tempfile

    from fastapi.testclient import TestClient

    from nexfiremap.api import create_app
    from nexfiremap.config import load_settings

    with tempfile.TemporaryDirectory() as temp:
        settings = dataclasses.replace(
            load_settings(), db_path=Path(temp) / "cot.sqlite3", lan_mode=False)
        with TestClient(create_app(settings)) as client:
            incident = client.post("/api/operations/incidents", json={"name": "CoT"}).json()["incident"]
            feed = client.post(f"/api/operations/incidents/{incident['id']}/position-feeds",
                               json={"name": "ATAK"}).json()
            headers = {"X-Feed-Token": feed["ingest_token"], "Content-Type": "application/xml"}
            url = f"/api/feeds/positions/{feed['id']}"

            accepted = client.post(url, content=ATAK_UNIT, headers=headers)
            assert accepted.status_code == 202, accepted.text
            assert accepted.json()["accepted"] == 1

            stored = client.get(
                f"/api/operations/incidents/{incident['id']}/vehicle-positions/latest").json()["features"][0]
            assert stored["properties"]["callsign"] == "FLORIAN 11/1"
            assert stored["geometry"]["coordinates"] == [11.5755, 48.1372]
            assert stored["properties"]["speed_kmh"] == 45.0, "m/s -> km/h must survive the HTTP path"

            # Replay safety comes from TelemetryManager, not the adapter - but
            # only if the adapter's external_id is stable across identical posts.
            replay = client.post(url, content=ATAK_UNIT, headers=headers).json()
            assert (replay["accepted"], replay["replayed"]) == (0, 1), replay

            assert client.post(url, content="<event/>", headers=headers).status_code == 400
            assert client.post(url, content=ATAK_UNIT,
                               headers={**headers, "X-Feed-Token": "wrong"}).status_code == 401

            # The JSON contract must be untouched by the new content-type branch.
            native = client.post(url, headers={"X-Feed-Token": feed["ingest_token"]}, json={"positions": [
                {"external_id": "j1", "callsign": "JSON", "observed_at": "2026-08-14T10:16:00Z",
                 "latitude": 48.1, "longitude": 11.5}]})
            assert native.status_code == 202 and native.json()["accepted"] == 1, native.text


def main() -> None:
    check_position_parse()
    check_unknown_sentinels()
    check_no_fix_skipped()
    check_multi_event_stream()
    check_overlay_parse()
    check_malformed_rejected()
    check_render_feature()
    check_render_position()
    check_round_trip()
    check_serialize()
    check_http_ingest()
    print("Cursor on Target checks passed.")


if __name__ == "__main__":
    main()
