"""MQTT bridge: topic routing, payload decoding, and graceful degradation.

Written to pass whether or not the optional `aiomqtt` dependency is installed,
because that is the contract: an install without it must start unchanged and
report the bridge unavailable, never fail. The message-handling path is
exercised directly rather than through a real broker, so the routing and
decoding logic is covered either way.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap import mqtt as mqtt_module
from nexfiremap.config import load_settings
from nexfiremap.db import Database
from nexfiremap.mqtt import MqttBridge, _parse_topic_map, _redacted, _topic_matches
from nexfiremap.operations import OperationsStore, default_period
from nexfiremap.telemetry import TelemetryManager

NMEA = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"


def check_topic_matching() -> None:
    """A subscription may be a wildcard while the delivered topic is concrete,
    so routing cannot be a plain dict lookup."""
    assert _topic_matches("fleet/florian/pos", "fleet/florian/pos")
    assert not _topic_matches("fleet/florian/pos", "fleet/other/pos")

    # '+' matches exactly one level.
    assert _topic_matches("fleet/+/pos", "fleet/florian/pos")
    assert not _topic_matches("fleet/+/pos", "fleet/a/b/pos")
    assert not _topic_matches("fleet/+/pos", "fleet/pos")

    # '#' matches the remainder, and only as the final level.
    assert _topic_matches("fleet/#", "fleet/a/b/c")
    assert _topic_matches("fleet/#", "fleet/a")
    assert not _topic_matches("a/#/b", "a/x/b"), "'#' is only valid as the last level"

    # A shorter pattern must not match a longer topic.
    assert not _topic_matches("fleet", "fleet/a")


def check_topic_map_parsing() -> None:
    parsed = _parse_topic_map("fleet/+/pos=src-1:nmea:FL 11/1, drones/#=src-2:mavlink, t=src-3:json")
    assert parsed["fleet/+/pos"] == {"source_id": "src-1", "format": "nmea", "callsign": "FL 11/1"}
    assert parsed["drones/#"]["format"] == "mavlink"
    assert parsed["t"]["format"] == "json"

    # A malformed or unknown-format entry is dropped, not fatal: the failure
    # mode must be "that topic is not ingested", never "the server will not
    # start during an incident".
    assert _parse_topic_map("nonsense") == {}
    assert _parse_topic_map("t=src:wrongformat") == {}
    assert _parse_topic_map("=src:json") == {}
    assert _parse_topic_map("") == {}


def check_broker_url_is_redacted() -> None:
    """MQTT URLs routinely embed credentials and /api/status is readable by
    every authenticated role, so the password must never appear there."""
    redacted = _redacted("mqtts://fleet:sup3rs3cret@broker.example.de:8883")
    assert "sup3rs3cret" not in redacted
    assert redacted == "mqtts://fleet@broker.example.de:8883"
    assert _redacted("") == ""
    assert _redacted("garbage") == "(malformed)"


def check_disabled_without_dependency_or_config() -> None:
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "m.sqlite3")
        try:
            store = OperationsStore(db)
            base = dataclasses.replace(load_settings(), db_path=Path(temp) / "m.sqlite3")
            telemetry = TelemetryManager(store, base)

            # Nothing configured: inert, and start() must not create a task.
            bridge = MqttBridge(base, db, telemetry)
            assert bridge.enabled is False
            asyncio.run(bridge.start())
            assert bridge._task is None
            asyncio.run(bridge.stop())

            # Configured but the optional dependency missing: still inert, and
            # the status says *why* rather than looking like an empty feed.
            configured = dataclasses.replace(
                base, mqtt_url="mqtt://broker.example.de:1883", mqtt_topics="t=src:json")
            bridge = MqttBridge(configured, db, telemetry)
            status = bridge.status()
            assert status["available"] is mqtt_module.AIOMQTT_AVAILABLE
            if not mqtt_module.AIOMQTT_AVAILABLE:
                assert bridge.enabled is False
                asyncio.run(bridge.start())
                assert bridge._task is None
                assert "aiomqtt" in (bridge.last_error or "")
                assert status["unavailable_reason"]
            else:
                assert bridge.enabled is True
        finally:
            db.close()


def check_message_routing() -> None:
    """A payload arriving over MQTT must take the same TelemetryManager write
    path as one arriving over HTTP - that equivalence is the whole design."""
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "m.sqlite3")
        try:
            settings_base = dataclasses.replace(load_settings(), db_path=Path(temp) / "m.sqlite3")
            store = OperationsStore(db)
            telemetry = TelemetryManager(store, settings_base)
            incident = store.create_incident({"name": "MQTT"}, "IC")
            store.create_period(incident["id"], default_period(), "IC")
            feed = telemetry.create_source(incident["id"], {"name": "Broker"}, "IC")

            settings = dataclasses.replace(
                settings_base, mqtt_url="mqtt://localhost:1883",
                mqtt_topics=f"fleet/+/nmea={feed['id']}:nmea:FL 11/1,"
                            f"fleet/json={feed['id']}:json")
            bridge = MqttBridge(settings, db, telemetry)

            # NMEA over a wildcard topic.
            asyncio.run(bridge._handle("fleet/florian/nmea", NMEA.encode()))
            assert bridge.accepted == 1, bridge.status()
            latest = telemetry.latest(incident["id"])
            properties = latest["features"][0]["properties"]
            assert properties["callsign"] == "FL 11/1"
            assert abs(properties["speed_kmh"] - 22.4 * 1.852) < 1e-2

            # Native JSON, the same shape the HTTP feed accepts.
            payload = json.dumps({"positions": [{
                "external_id": "j-1", "callsign": "JSON", "observed_at": "2026-08-14T10:00:00Z",
                "latitude": 48.1, "longitude": 11.5}]})
            asyncio.run(bridge._handle("fleet/json", payload.encode()))
            assert bridge.accepted == 2, bridge.status()

            # Replay safety comes free from the shared write path.
            asyncio.run(bridge._handle("fleet/json", payload.encode()))
            assert bridge.accepted == 2, "a resent payload must not create a second position"

            # A topic with no mapping is ignored rather than counted as an error.
            before = bridge.rejected
            asyncio.run(bridge._handle("something/else", b"{}"))
            assert bridge.rejected == before

            # A malformed payload is rejected and logged, never fatal - one bad
            # message must not take down the subscription for everything else.
            asyncio.run(bridge._handle("fleet/json", b"not json"))
            assert bridge.rejected == before + 1
            assert bridge.last_error

            # ...and the bridge keeps working afterwards.
            asyncio.run(bridge._handle("fleet/florian/nmea", NMEA.encode()))
            assert bridge.accepted == 2, "a replayed NMEA fix is a replay, not a new position"

            # Oversized payloads are dropped before parsing.
            before = bridge.rejected
            asyncio.run(bridge._handle("fleet/json", b"x" * (mqtt_module.MAX_PAYLOAD_BYTES + 1)))
            assert bridge.rejected == before + 1

            status = bridge.status()
            assert status["connected"] is False and "localhost" in status["broker"]
            assert sorted(status["topics"]) == ["fleet/+/nmea", "fleet/json"]
        finally:
            db.close()


def check_server_starts_without_mqtt() -> None:
    """The graceful-degradation contract, asserted against the assembled app."""
    from fastapi.testclient import TestClient

    from nexfiremap.api import create_app

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = dataclasses.replace(
            load_settings(), db_path=root / "api.sqlite3", tile_cache_dir=root / "tiles",
            lan_mode=False, mqtt_url="mqtt://broker.example.de:1883", mqtt_topics="t=src:json")
        with TestClient(create_app(settings)) as client:
            config = client.get("/api/config").json()
            assert config["features"]["mqtt"] is mqtt_module.AIOMQTT_AVAILABLE
            status = client.get("/api/status?key=false").json()
            assert status["mqtt"]["available"] is mqtt_module.AIOMQTT_AVAILABLE
            # Credentials never reach a status response.
            assert "password" not in json.dumps(status["mqtt"]).lower()


def main() -> None:
    check_topic_matching()
    check_topic_map_parsing()
    check_broker_url_is_redacted()
    check_disabled_without_dependency_or_config()
    check_message_routing()
    check_server_starts_without_mqtt()
    suffix = "" if mqtt_module.AIOMQTT_AVAILABLE else " (aiomqtt not installed - degradation path covered)"
    print(f"MQTT bridge checks passed{suffix}.")


if __name__ == "__main__":
    main()
