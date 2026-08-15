"""CAP parsing, alert storage/expiry, and the alert map layer.

The fixtures below are shaped like real MoWaS/DWD and NWS output, including
the parts that trip implementations up: lat,lon coordinate order, one alert
repeated in several languages, exercise messages that must never render as
real, geocode-only alerts with no polygon, and circles.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.alerts import AlertManager
from nexfiremap.config import load_settings
from nexfiremap.db import Database
from nexfiremap.ingest import IngestError
from nexfiremap.ingest import cap


def _iso(offset_hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).isoformat(timespec="seconds")


# A bilingual alert, as MoWaS actually publishes: the same warning twice.
# Coordinates are lat,lon per the CAP spec - the polygon sits around Munich.
BILINGUAL = f"""<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>mow.DE-BY-M-W094</identifier>
  <sender>MoWaS</sender>
  <sent>2026-08-14T09:00:00+02:00</sent>
  <status>Actual</status>
  <msgType>Alert</msgType>
  <scope>Public</scope>
  <info>
    <language>de-DE</language>
    <category>Fire</category>
    <event>Waldbrand</event>
    <urgency>Immediate</urgency>
    <severity>Severe</severity>
    <certainty>Observed</certainty>
    <effective>{_iso(-1)}</effective>
    <expires>{_iso(6)}</expires>
    <headline>Waldbrand noerdlich Muenchen</headline>
    <description>Grossflaechiger Waldbrand.</description>
    <instruction>Fenster geschlossen halten.</instruction>
    <area>
      <areaDesc>Landkreis Muenchen</areaDesc>
      <polygon>48.10,11.50 48.20,11.50 48.20,11.70 48.10,11.70 48.10,11.50</polygon>
      <geocode><valueName>WARNCELLID</valueName><value>809184000</value></geocode>
    </area>
  </info>
  <info>
    <language>en-GB</language>
    <event>Wildfire</event>
    <urgency>Immediate</urgency>
    <severity>Severe</severity>
    <certainty>Observed</certainty>
    <headline>Wildfire north of Munich</headline>
    <area>
      <areaDesc>Munich district</areaDesc>
      <polygon>48.10,11.50 48.20,11.50 48.20,11.70 48.10,11.70 48.10,11.50</polygon>
    </area>
  </info>
</alert>"""

# An exercise. Must never be drawn as a real warning.
EXERCISE = """<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>drill-1</identifier><sender>MoWaS</sender>
  <sent>2026-08-14T09:00:00+02:00</sent>
  <status>Exercise</status><msgType>Alert</msgType><scope>Public</scope>
  <info><event>Uebung</event><urgency>Immediate</urgency><severity>Extreme</severity>
    <certainty>Observed</certainty>
    <area><areaDesc>x</areaDesc><polygon>48.1,11.5 48.2,11.5 48.2,11.7 48.1,11.5</polygon></area>
  </info>
</alert>"""

# Two disjoint districts in one alert, plus a circle.
MULTI_AREA = f"""<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>multi-1</identifier><sender>DWD</sender>
  <sent>2026-08-14T09:00:00Z</sent><status>Actual</status><msgType>Alert</msgType><scope>Public</scope>
  <info><event>Sturm</event><urgency>Expected</urgency><severity>Moderate</severity>
    <certainty>Likely</certainty><expires>{_iso(4)}</expires>
    <area><areaDesc>A</areaDesc><polygon>48.0,11.0 48.1,11.0 48.1,11.1 48.0,11.0</polygon></area>
    <area><areaDesc>B</areaDesc><polygon>49.0,12.0 49.1,12.0 49.1,12.1 49.0,12.0</polygon></area>
    <area><areaDesc>C</areaDesc><circle>47.5,10.5 10</circle></area>
  </info>
</alert>"""

# Geocode only, no polygon - common for cell-based German warnings.
GEOCODE_ONLY = f"""<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>cell-1</identifier><sender>DWD</sender>
  <sent>2026-08-14T09:00:00Z</sent><status>Actual</status><msgType>Alert</msgType><scope>Public</scope>
  <info><event>Hitze</event><urgency>Future</urgency><severity>Minor</severity>
    <certainty>Possible</certainty><expires>{_iso(8)}</expires>
    <area><areaDesc>Zelle</areaDesc>
      <geocode><valueName>WARNCELLID</valueName><value>109162000</value></geocode>
    </area>
  </info>
</alert>"""

EXPIRED = f"""<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>old-1</identifier><sender>DWD</sender>
  <sent>2026-08-13T09:00:00Z</sent><status>Actual</status><msgType>Alert</msgType><scope>Public</scope>
  <info><event>Gewitter</event><urgency>Past</urgency><severity>Minor</severity>
    <certainty>Observed</certainty><expires>{_iso(-2)}</expires>
    <area><areaDesc>x</areaDesc><polygon>48.0,11.0 48.1,11.0 48.1,11.1 48.0,11.0</polygon></area>
  </info>
</alert>"""

ATOM_FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><link href="https://example.org/cap/1.xml"/></entry>
  <entry><link href="https://example.org/cap/2.xml"/></entry>
</feed>"""


def check_parse() -> None:
    (record,) = cap.parse(BILINGUAL.encode())

    # One feature per alert, not one per language - two <info> blocks would
    # otherwise stack identical polygons on the map.
    assert record["identifier"] == "mow.DE-BY-M-W094"
    assert record["event"] == "Waldbrand", "the German <info> should win over the English one"

    # CAP writes lat,lon; GeoJSON is lon,lat. Getting this backwards puts
    # every European alert in the ocean off Somalia and still parses cleanly,
    # which is why it is asserted explicitly.
    ring = record["geometry"]["coordinates"][0]
    assert record["geometry"]["type"] == "Polygon"
    assert ring[0] == [11.50, 48.10], ring[0]
    assert all(11.0 < point[0] < 12.0 and 48.0 < point[1] < 49.0 for point in ring)

    assert record["severity"] == "Severe" and record["urgency"] == "Immediate"
    assert record["severity_rank"] == cap.SEVERITIES.index("Severe")
    assert record["headline"] == "Waldbrand noerdlich Muenchen"
    assert record["instruction"] == "Fenster geschlossen halten."
    assert record["geocodes"] == [{"name": "WARNCELLID", "value": "809184000"}]
    assert record["expires"].endswith("Z"), "timestamps are normalised to UTC"


def check_exercise_never_renders() -> None:
    # An exercise or system test must never reach the map. It is skipped
    # rather than raised on, since it is normal traffic in a real feed.
    assert cap.parse(EXERCISE.encode()) == []


def check_multi_area_and_circle() -> None:
    (record,) = cap.parse(MULTI_AREA.encode())
    # Several disjoint districts in one alert must all be kept - reporting
    # only the first would understate an evacuation's extent.
    assert record["geometry"]["type"] == "MultiPolygon"
    assert len(record["geometry"]["coordinates"]) == 3, "two polygons plus the circle"

    # The circle is synthesised as a polygon, and must be wider than it is
    # tall in degrees because a degree of longitude is shorter at 47.5N.
    circle = record["geometry"]["coordinates"][2][0]
    lons = [p[0] for p in circle]
    lats = [p[1] for p in circle]
    width, height = max(lons) - min(lons), max(lats) - min(lats)
    assert width > height, f"circle must widen in longitude at latitude: {width} vs {height}"
    assert abs(height - 2 * 10 / 110.574) < 1e-3, height


def check_geocode_only() -> None:
    (record,) = cap.parse(GEOCODE_ONLY.encode())
    assert record["geometry"] is None
    # It still has to be recorded - a warning we cannot draw is not a warning
    # we may discard.
    assert record["geocodes"] == [{"name": "WARNCELLID", "value": "109162000"}]


def check_malformed() -> None:
    for label, payload in (
        ("XXE", b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><alert/>'),
        ("empty", b"  "),
        ("not CAP", b"<html><body>404</body></html>"),
        ("no identifier", b'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><info/></alert>'),
        ("bad coordinates", ("""<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>i</identifier>
             <status>Actual</status><info><event>e</event><severity>Minor</severity>
             <area><polygon>200,300 1,2 3,4 200,300</polygon></area></info></alert>""").encode()),
        ("short polygon", ("""<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>i</identifier>
             <status>Actual</status><info><event>e</event><severity>Minor</severity>
             <area><polygon>48.1,11.5 48.2,11.5</polygon></area></info></alert>""").encode()),
    ):
        try:
            cap.parse(payload)
            raise AssertionError(f"{label} was accepted")
        except IngestError:
            pass


def check_feed_links() -> None:
    links = cap.feed_links(ATOM_FEED.encode())
    assert links == ["https://example.org/cap/1.xml", "https://example.org/cap/2.xml"], links
    # A CAP document itself has no links; feed_links must not raise on it.
    assert cap.feed_links(BILINGUAL.encode()) == []


def check_storage_and_expiry() -> None:
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "alerts.sqlite3")
        try:
            settings = dataclasses.replace(load_settings(), db_path=Path(temp) / "alerts.sqlite3",
                                           cap_feeds=["https://example.org/cap.xml"])
            manager = AlertManager(settings, db)
            assert manager.enabled

            for payload in (BILINGUAL, MULTI_AREA, GEOCODE_ONLY, EXPIRED):
                manager._store(cap.parse(payload.encode()), "test-feed", payload)

            result = manager.query()
            # The expired alert must not render even before the prune pass runs.
            identifiers = {f["id"] for f in result["features"]}
            assert "old-1" not in identifiers, "an expired warning must never be drawn"
            assert "mow.DE-BY-M-W094" in identifiers and "multi-1" in identifiers

            # Geocode-only alerts are reported separately, never dropped.
            assert [item["identifier"] for item in result["without_geometry"]] == ["cell-1"]

            # Ordered most severe first.
            severities = [f["properties"]["severity"] for f in result["features"]]
            assert severities == sorted(severities, key=cap.SEVERITIES.index), severities

            # Viewport filtering is overlap, not containment: a viewport inside
            # a large alert polygon must still match it.
            inside = manager.query((11.55, 48.12, 11.60, 48.15))
            assert "mow.DE-BY-M-W094" in {f["id"] for f in inside["features"]}
            far = manager.query((2.0, 40.0, 2.1, 40.1))
            assert far["features"] == []

            # Re-polling must update in place, not duplicate - and must not
            # rewrite when the alert was first seen.
            first_seen = db.conn.execute(
                "SELECT received_at FROM alerts WHERE identifier=?", ("multi-1",)).fetchone()[0]
            manager._store(cap.parse(MULTI_AREA.encode()), "test-feed", MULTI_AREA)
            assert db.conn.execute("SELECT COUNT(*) FROM alerts WHERE identifier=?",
                                   ("multi-1",)).fetchone()[0] == 1
            assert db.conn.execute("SELECT received_at FROM alerts WHERE identifier=?",
                                   ("multi-1",)).fetchone()[0] == first_seen

            # Provenance: the published XML is kept verbatim.
            assert "<identifier>multi-1</identifier>" in (manager.original("multi-1") or "")
            assert manager.original("nope") is None
            # ...but is not carried in the map payload.
            assert all("raw_xml" not in f["properties"] for f in result["features"])

            assert manager.prune_now() == 1, "the expired alert should be pruned"
            assert manager.query(include_expired=True)["features"], "prune must not empty the table"
            assert manager.status()["stored"] == 3
        finally:
            db.close()


def check_disabled_without_feeds() -> None:
    """With no feeds configured nothing is polled, nothing is started, and the
    frontend feature flag reports it off - an install that never wanted CAP
    must pay nothing for it, and must not make outbound connections."""
    import asyncio

    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "alerts.sqlite3")
        try:
            settings = dataclasses.replace(load_settings(), cap_feeds=[])
            manager = AlertManager(settings, db)
            assert manager.enabled is False
            asyncio.run(manager.start())
            assert manager._client is None and manager._task is None
            assert asyncio.run(manager.poll_now())["polled"] == 0
            assert manager.status()["enabled"] is False
            asyncio.run(manager.stop())
        finally:
            db.close()


def check_http_surface() -> None:
    from fastapi.testclient import TestClient

    from nexfiremap.api import create_app

    with tempfile.TemporaryDirectory() as temp:
        settings = dataclasses.replace(
            load_settings(), db_path=Path(temp) / "api.sqlite3",
            tile_cache_dir=Path(temp) / "tiles", lan_mode=False, cap_feeds=[])
        with TestClient(create_app(settings)) as client:
            assert client.get("/api/config").json()["features"]["alerts"] is False
            payload = client.get("/api/alerts").json()
            assert payload["type"] == "FeatureCollection" and payload["features"] == []
            assert client.get("/api/alerts/status").json()["enabled"] is False
            # Refresh with nothing configured is a clear 400, not a 500.
            assert client.post("/api/alerts/refresh").status_code == 400

            client.app.state.alerts._store(cap.parse(BILINGUAL.encode()), "feed", BILINGUAL)
            features = client.get("/api/alerts?bbox=11.4,48.0,11.8,48.3").json()["features"]
            assert len(features) == 1 and features[0]["id"] == "mow.DE-BY-M-W094"
            # CAP identifiers routinely contain dots, colons and slashes.
            original = client.get("/api/alerts/mow.DE-BY-M-W094/original")
            assert original.status_code == 200 and "<identifier>" in original.text
            assert client.get("/api/alerts/nope/original").status_code == 404


def main() -> None:
    check_parse()
    check_exercise_never_renders()
    check_multi_area_and_circle()
    check_geocode_only()
    check_malformed()
    check_feed_links()
    check_storage_and_expiry()
    check_disabled_without_feeds()
    check_http_surface()
    print("CAP alert checks passed.")


if __name__ == "__main__":
    main()
