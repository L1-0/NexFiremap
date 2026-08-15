"""Streaming CoT/MAVLink gateway, the OsmAnd endpoint, and CoT output.

The security assertions here matter more than usual: this gateway accepts
packets that carry no authentication at all, so the allowlist, the rate limit
and the default-off behaviour are the only things standing in front of it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import socket
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from nexfiremap.api import create_app
from nexfiremap.config import load_settings
from nexfiremap.cot_gateway import CotGateway
from nexfiremap.db import Database
from nexfiremap.ingest import mavlink
from nexfiremap.operations import OperationsStore, default_period
from nexfiremap.telemetry import TelemetryManager

ATAK_UNIT = ('<event version="2.0" uid="ANDROID-1" type="a-f-G-U-C" how="m-g" '
             'time="2026-08-14T10:15:30.000Z" start="2026-08-14T10:15:30.000Z" '
             'stale="2026-08-14T10:20:30.000Z">'
             '<point lat="48.1372" lon="11.5755" hae="519.4" ce="9.5" le="9999999.0"/>'
             '<detail><contact callsign="FLORIAN 11/1"/><track speed="12.5" course="271.3"/></detail>'
             '</event>')

ATAK_MARKER = ('<event version="2.0" uid="MARK-1" type="b-m-p-w" how="h-e" '
               'time="2026-08-14T10:16:00.000Z" start="2026-08-14T10:16:00.000Z" '
               'stale="2026-08-14T11:16:00.000Z">'
               '<point lat="48.1400" lon="11.5800" hae="9999999.0" ce="9999999.0" le="9999999.0"/>'
               '<detail><contact callsign="Hazard A"/></detail></event>')


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _settings(temp: Path, **overrides) -> object:
    defaults = {"db_path": temp / "gw.sqlite3", "tile_cache_dir": temp / "tiles", "lan_mode": False}
    return dataclasses.replace(load_settings(), **{**defaults, **overrides})


def check_disabled_by_default() -> None:
    """The gateway must not listen unless explicitly enabled AND pointed at a
    feed. It accepts unauthenticated packets, so silently-on would be a
    genuine security defect, not an inconvenience."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        db = Database(root / "gw.sqlite3")
        try:
            store = OperationsStore(db)
            settings = _settings(root)
            gateway = CotGateway(settings, TelemetryManager(store, settings), store)
            assert gateway.enabled is False
            asyncio.run(gateway.start())
            assert gateway._server is None and gateway._transports == []

            # Enabled but with no feed source: still refuses, because a
            # received position would have nowhere to go.
            settings = _settings(root, cot_enabled=True, cot_source_id="")
            gateway = CotGateway(settings, TelemetryManager(store, settings), store)
            assert gateway.enabled is False
            asyncio.run(gateway.start())
            assert gateway._server is None
        finally:
            db.close()


def check_allowlist() -> None:
    """The CIDR allowlist is the gateway's actual access control. A config
    typo must narrow it, never widen it to everything."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        db = Database(root / "gw.sqlite3")
        try:
            store = OperationsStore(db)

            settings = _settings(root, cot_allow="")
            gateway = CotGateway(settings, TelemetryManager(store, settings), store)
            assert gateway._allowed("127.0.0.1") is True
            assert gateway._allowed("10.0.0.5") is False, "default must be loopback only"
            assert gateway._allowed("not-an-ip") is False

            # A malformed entry is dropped; the remaining valid one still applies.
            settings = _settings(root, cot_allow="10.0.0.0/8, not-a-cidr")
            gateway = CotGateway(settings, TelemetryManager(store, settings), store)
            assert gateway._allowed("10.4.5.6") is True
            assert gateway._allowed("192.168.1.1") is False

            # ...and if EVERY entry is malformed, it falls back to loopback,
            # not to allow-all.
            settings = _settings(root, cot_allow="nonsense, also-nonsense")
            gateway = CotGateway(settings, TelemetryManager(store, settings), store)
            assert gateway._allowed("127.0.0.1") is True
            assert gateway._allowed("10.0.0.1") is False, "a bad allowlist must not become allow-all"

            # An IPv4 peer on a dual-stack socket arrives mapped into IPv6 and
            # must still match an IPv4 CIDR.
            settings = _settings(root, cot_allow="10.0.0.0/8")
            gateway = CotGateway(settings, TelemetryManager(store, settings), store)
            assert gateway._allowed("::ffff:10.1.2.3") is True
        finally:
            db.close()


def check_rate_limit() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        db = Database(root / "gw.sqlite3")
        try:
            store = OperationsStore(db)
            settings = _settings(root, cot_packets_per_minute=3)
            gateway = CotGateway(settings, TelemetryManager(store, settings), store)
            assert [gateway._within_rate("10.0.0.1") for _ in range(4)] == [True, True, True, False]
            # The budget is per peer - one flooding device must not lock out
            # every other unit on the net.
            assert gateway._within_rate("10.0.0.2") is True
        finally:
            db.close()


def check_gateway_end_to_end() -> None:
    """A real TCP connection carrying a real ATAK event must land as a
    position through TelemetryManager - not via some parallel write path."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        db = Database(root / "gw.sqlite3")
        try:
            store = OperationsStore(db)
            incident = store.create_incident({"name": "Gateway"}, "IC")
            store.create_period(incident["id"], default_period(), "IC")
            bootstrap = TelemetryManager(store, _settings(root))
            feed = bootstrap.create_source(incident["id"], {"name": "TAK"}, "IC")

            port = _free_port()
            settings = _settings(
                root, cot_enabled=True, cot_host="127.0.0.1", cot_tcp_port=port,
                cot_udp_port=0, cot_source_id=feed["id"], cot_source_token=feed["ingest_token"],
                cot_incident_id=incident["id"], cot_accept_features=True)
            telemetry = TelemetryManager(store, settings)
            gateway = CotGateway(settings, telemetry, store)

            async def exercise() -> None:
                await gateway.start()
                assert gateway._server is not None, "gateway failed to bind"
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                # Both kinds down one connection, which is how TAK actually
                # behaves: a fleet's positions and a drawn marker together.
                writer.write((ATAK_UNIT + ATAK_MARKER).encode())
                await writer.drain()
                for _ in range(50):
                    await asyncio.sleep(0.05)
                    if gateway.positions and gateway.features:
                        break
                writer.close()
                await gateway.stop()

            asyncio.run(exercise())

            assert gateway.positions == 1, gateway.status()
            assert gateway.features == 1, gateway.status()

            # The position went through TelemetryManager, so it is visible in
            # exactly the same view the JSON feed populates.
            latest = telemetry.latest(incident["id"])
            assert latest["features"][0]["properties"]["callsign"] == "FLORIAN 11/1"
            assert latest["features"][0]["properties"]["speed_kmh"] == 45.0

            # The marker became a tactical feature carrying its CoT uid, which
            # is what makes a repeat idempotent.
            features = store.list_features(incident["id"])
            assert len(features) == 1
            assert features[0]["properties"]["cot_uid"] == "MARK-1"
            assert gateway.db_lookup(incident["id"], "MARK-1") is not None
        finally:
            db.close()


def check_marker_refresh_is_idempotent() -> None:
    """ATAK re-sends every marker every few seconds. Creating a row per
    refresh would bury an incident under thousands of duplicates within an
    hour, so a repeat must update the feature it already produced."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        db = Database(root / "gw.sqlite3")
        try:
            store = OperationsStore(db)
            incident = store.create_incident({"name": "Refresh"}, "IC")
            store.create_period(incident["id"], default_period(), "IC")
            settings = _settings(root, cot_incident_id=incident["id"], cot_accept_features=True)
            gateway = CotGateway(settings, TelemetryManager(store, settings), store)

            from nexfiremap.ingest import cot
            for _ in range(3):
                gateway._upsert_features(cot.overlays(ATAK_MARKER.encode()), "10.0.0.1")

            features = store.list_features(incident["id"])
            assert len(features) == 1, f"{len(features)} rows from three identical refreshes"
            # list_features returns GeoJSON, so the row fields live under
            # `properties` rather than at the top level.
            assert features[0]["properties"]["revision"] >= 2, "a repeat must update, not no-op"
        finally:
            db.close()


def check_mavlink_over_gateway() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        db = Database(root / "gw.sqlite3")
        try:
            store = OperationsStore(db)
            incident = store.create_incident({"name": "Drone"}, "IC")
            store.create_period(incident["id"], default_period(), "IC")
            bootstrap = TelemetryManager(store, _settings(root))
            feed = bootstrap.create_source(incident["id"], {"name": "MAVLink"}, "IC")

            settings = _settings(
                root, cot_source_id=feed["id"], cot_source_token=feed["ingest_token"],
                cot_mavlink_callsign="DRONE-1")
            telemetry = TelemetryManager(store, settings)
            gateway = CotGateway(settings, telemetry, store)

            frame = mavlink.encode_global_position(
                latitude=48.1372, longitude=11.5755, altitude_m=520.5,
                relative_altitude_m=120.0, vx_ms=10.0, heading_deg=90.0, boot_ms=5000)
            asyncio.run(gateway._handle_mavlink_datagram(frame, "127.0.0.1"))
            assert gateway.positions == 1, gateway.status()

            latest = telemetry.latest(incident["id"])
            properties = latest["features"][0]["properties"]
            assert properties["callsign"] == "DRONE-1"
            assert properties["speed_kmh"] == 36.0, "10 m/s must store as 36 km/h"
            assert properties["altitude_m"] == 520.5

            # A datagram carrying no position (a heartbeat, a status message)
            # is normal traffic and must be ignored, not counted as an error.
            before = gateway.positions
            asyncio.run(gateway._handle_mavlink_datagram(b"\xfd\x00\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00", "127.0.0.1"))
            assert gateway.positions == before
        finally:
            db.close()


def check_http_surface() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        with TestClient(create_app(_settings(root))) as client:
            incident = client.post("/api/operations/incidents", json={"name": "CoT"}).json()["incident"]
            feed = client.post(f"/api/operations/incidents/{incident['id']}/position-feeds",
                               json={"name": "Tracker"}).json()
            token = feed["ingest_token"]
            osmand = f"/api/feeds/positions/{feed['id']}/osmand"

            # The OsmAnd protocol: a bare GET with query parameters, credential
            # in the URL because this hardware cannot set a header.
            accepted = client.get(osmand, params={
                "id": "FL 11/1", "lat": "48.1372", "lon": "11.5755",
                "timestamp": "1786000000", "speed": "22.4", "bearing": "84.4",
                "altitude": "520", "hdop": "0.9", "token": token})
            assert accepted.status_code == 202, accepted.text
            assert accepted.json()["accepted"] == 1

            stored = client.get(
                f"/api/operations/incidents/{incident['id']}/vehicle-positions/latest"
            ).json()["features"][0]["properties"]
            assert stored["callsign"] == "FL 11/1"
            # OsmAnd speed is in KNOTS, inherited from NMEA. Storing the raw
            # number would understate every speed by a factor of 1.852.
            assert abs(stored["speed_kmh"] - 22.4 * 1.852) < 1e-2, stored["speed_kmh"]

            # Re-sending the same fix is a replay, not a second position.
            replay = client.get(osmand, params={
                "id": "FL 11/1", "lat": "48.1372", "lon": "11.5755",
                "timestamp": "1786000000", "speed": "22.4", "bearing": "84.4",
                "altitude": "520", "hdop": "0.9", "token": token}).json()
            assert (replay["accepted"], replay["replayed"]) == (0, 1), replay

            assert client.get(osmand, params={"lat": "48.1", "lon": "11.5",
                                              "token": "wrong"}).status_code == 401
            assert client.get(osmand, params={"lat": "48.1", "token": token}).status_code == 400

            # NMEA over the ordinary feed endpoint.
            nmea_body = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
            posted = client.post(f"/api/feeds/positions/{feed['id']}", content=nmea_body,
                                 params={"callsign": "TANK 1"},
                                 headers={"X-Feed-Token": token, "Content-Type": "text/plain"})
            assert posted.status_code == 202, posted.text
            assert posted.json()["accepted"] == 1

            # CoT output: the incident rendered for a TAK client to poll.
            payload = client.get(f"/api/operations/incidents/{incident['id']}/cot")
            assert payload.status_code == 200
            assert payload.headers["content-type"].startswith("application/xml")
            root_element = ET.fromstring(payload.content)
            callsigns = {node.get("callsign") for node in root_element.iter("contact")}
            assert {"FL 11/1", "TANK 1"} <= callsigns, callsigns
            for event in root_element.findall("event"):
                assert event.get("type") and event.find("point") is not None

            status = client.get("/api/feeds/cot/status").json()
            assert status["enabled"] is False and status["listening"] is False
            assert status["allowlist"] == ["127.0.0.0/8", "::1/128"]


def check_security_carveout_stays_narrow() -> None:
    """The middleware exempts GET only on the exact /osmand suffix. Widening
    it to the whole /api/feeds/ prefix would expose every future read endpoint
    added there without a session."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = _settings(root, lan_mode=True, admin_password="a-long-enough-password")
        with TestClient(create_app(settings)) as client:
            # No session: the OsmAnd GET is allowed through to its own token check...
            allowed = client.get("/api/feeds/positions/whatever/osmand",
                                 params={"lat": "48.1", "lon": "11.5", "token": "x"})
            assert allowed.status_code != 401 or "feed" in allowed.text.lower(), allowed.text

            # ...but a sibling GET under the same prefix is NOT.
            assert client.get("/api/feeds/cot/status").status_code == 401
            # ...and neither is anything else.
            assert client.get("/api/operations/incidents").status_code == 401


def main() -> None:
    check_disabled_by_default()
    check_allowlist()
    check_rate_limit()
    check_gateway_end_to_end()
    check_marker_refresh_is_idempotent()
    check_mavlink_over_gateway()
    check_http_surface()
    check_security_carveout_stays_narrow()
    print("CoT gateway, OsmAnd and MAVLink checks passed.")


if __name__ == "__main__":
    main()
