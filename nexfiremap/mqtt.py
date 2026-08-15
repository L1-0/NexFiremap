"""MQTT bridge - telemetry over the transport IoT hardware already speaks.

A lot of tracker and sensor infrastructure publishes to an MQTT broker rather
than POSTing anywhere: it is the default for LoRaWAN gateways, for
Traccar-adjacent fleet boxes, and for the weather/air-quality sensors a
department may already run. Subscribing means that hardware needs no change at
all - the broker it already talks to becomes the integration point.

MQTT also behaves well on the kind of network an incident actually has: a
persistent session with keepalive and automatic reconnect survives the brief
outages a field link produces, where a stream of HTTP POSTs would simply fail.

**`aiomqtt` is an optional dependency**, imported the same guarded way as
`rasterio`/`skyfield`/`h5py` in `routes/common.py`. If it is missing the server
starts exactly as before, this bridge reports itself unavailable through
`/api/config`'s feature map, and nothing else notices. That pattern is the
reason a heavier dependency is acceptable here at all: an install that does not
want MQTT never pays for it, and one that does gets a protocol implementation
that has been exercised far more widely than a hand-rolled codec would be.

Routing is table-driven and reuses `nexfiremap.ingest`: each subscribed topic
names an adapter (``json``, ``cot``, ``nmea``, ``mavlink``) and a position feed,
so a payload arriving over MQTT takes the identical `TelemetryManager` write
path as one arriving over HTTP - same replay-safety, same quality flags, same
audit entry. The transport is new; the pipeline is not.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .db import Database
from .ingest import IngestError, adapter

log = logging.getLogger("nexfiremap.mqtt")

# Guarded exactly like the optional analysis modules: a missing or broken
# dependency disables this feature, it never prevents the server from starting.
try:  # pragma: no cover - import-time branch
    import aiomqtt

    AIOMQTT_AVAILABLE = True
    AIOMQTT_ERROR = ""
except Exception as exc:  # noqa: BLE001 - see routes/common.py for the rationale
    aiomqtt = None  # type: ignore[assignment]
    AIOMQTT_AVAILABLE = False
    AIOMQTT_ERROR = str(exc)

#: Reconnect backoff bounds, in seconds. Starts fast because the common case is
#: a momentary link blip during an incident; caps low enough that a broker
#: coming back is picked up promptly rather than after a long sleep.
RECONNECT_MIN_SECONDS = 2
RECONNECT_MAX_SECONDS = 60

#: Largest payload accepted from a topic. A broker will happily deliver
#: megabytes; nothing NexFiremap ingests over MQTT is anywhere near that, and
#: this parses untrusted input.
MAX_PAYLOAD_BYTES = 256 * 1024

#: Adapter names a topic mapping may name. "json" is not an adapter module -
#: it is the native position-report format the HTTP feed already accepts, and
#: is handled inline below.
SUPPORTED_FORMATS = ("json", "cot", "nmea", "mavlink")


class MqttBridge:
    """Subscribes to configured topics and routes payloads into telemetry."""

    def __init__(self, settings: Settings, db: Database, telemetry: Any) -> None:
        self.settings = settings
        self.db = db
        self.telemetry = telemetry
        self._task: asyncio.Task | None = None
        self._stopping = False
        # Surfaced via /api/status: "no positions" and "the broker has been
        # unreachable for an hour" are indistinguishable on the map otherwise.
        self.connected = False
        self.received = 0
        self.accepted = 0
        self.rejected = 0
        self.last_error: str | None = None
        self.topics = _parse_topic_map(settings.mqtt_topics)

    @property
    def enabled(self) -> bool:
        """Configured *and* usable. Both halves matter: a configured broker
        with the dependency missing must report unavailable rather than
        silently doing nothing."""
        return bool(self.settings.mqtt_url and self.topics and AIOMQTT_AVAILABLE)

    async def start(self) -> None:
        if not self.settings.mqtt_url:
            return
        if not AIOMQTT_AVAILABLE:
            log.warning("MQTT is configured but aiomqtt is not installed (%s); "
                        "install it or unset NEXFIREMAP_MQTT_URL", AIOMQTT_ERROR)
            self.last_error = f"aiomqtt unavailable: {AIOMQTT_ERROR}"
            return
        if not self.topics:
            log.warning("MQTT is configured but NEXFIREMAP_MQTT_TOPICS names no topics")
            return
        self._task = asyncio.create_task(self._loop(), name="mqtt-bridge")
        log.info("MQTT bridge started for %d topic(s)", len(self.topics))

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        """Connect, subscribe, consume - reconnecting with backoff forever.

        The backoff resets on every successful connection, so a link that flaps
        repeatedly does not end up waiting a minute between attempts, while a
        broker that is genuinely gone is not hammered.
        """
        delay = RECONNECT_MIN_SECONDS
        parsed = urlparse(self.settings.mqtt_url)
        while not self._stopping:
            try:
                async with aiomqtt.Client(
                    hostname=parsed.hostname or "localhost",
                    port=parsed.port or (8883 if parsed.scheme == "mqtts" else 1883),
                    username=parsed.username or self.settings.mqtt_username or None,
                    password=parsed.password or self.settings.mqtt_password or None,
                    tls_context=_tls_context() if parsed.scheme == "mqtts" else None,
                    identifier=self.settings.mqtt_client_id or None,
                    keepalive=60,
                ) as client:
                    self.connected = True
                    self.last_error = None
                    delay = RECONNECT_MIN_SECONDS
                    for topic in self.topics:
                        await client.subscribe(topic)
                    log.info("MQTT connected to %s; subscribed to %s",
                             parsed.hostname, ", ".join(self.topics))
                    async for message in client.messages:
                        await self._handle(str(message.topic), bytes(message.payload))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any broker/network fault
                # A broker being unreachable is an expected state on an
                # incident LAN, not a fault: log it, back off, keep trying.
                self.connected = False
                self.last_error = str(exc)
                log.warning("MQTT connection lost (%s); retrying in %ds", exc, delay)
            if self._stopping:
                break
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            delay = min(delay * 2, RECONNECT_MAX_SECONDS)
        self.connected = False

    def _match(self, topic: str) -> dict[str, Any] | None:
        """Find the configuration for a delivered topic.

        Subscriptions may use MQTT wildcards (``fleet/+/position``), so an
        arriving concrete topic has to be matched back against the patterns
        rather than looked up directly.
        """
        for pattern, config in self.topics.items():
            if _topic_matches(pattern, topic):
                return config
        return None

    async def _handle(self, topic: str, payload: bytes) -> None:
        self.received += 1
        if len(payload) > MAX_PAYLOAD_BYTES:
            self.rejected += 1
            log.warning("MQTT payload on %s exceeds %d bytes; dropped", topic, MAX_PAYLOAD_BYTES)
            return
        config = self._match(topic)
        if config is None:
            return  # subscribed by a wildcard we have no mapping for

        try:
            reports = self._decode(config, topic, payload)
        except IngestError as exc:
            self.rejected += 1
            self.last_error = str(exc)
            log.warning("MQTT payload on %s could not be parsed: %s", topic, exc)
            return

        try:
            # The already-authenticated path: the broker connection is the
            # trust boundary here (credentials are in the broker URL), so
            # there is no per-message token to check - exactly the case
            # `ingest_authenticated` exists for.
            result = await asyncio.to_thread(
                self.telemetry.ingest_authenticated, config["source_id"], reports)
        except Exception as exc:  # noqa: BLE001 - a bad batch must not kill the loop
            self.rejected += 1
            self.last_error = str(exc)
            log.warning("MQTT positions from %s rejected: %s", topic, exc)
            return
        self.accepted += int(result.get("accepted", 0))

    def _decode(self, config: dict[str, Any], topic: str, payload: bytes) -> list[dict[str, Any]]:
        """Turn a payload into position reports using the topic's adapter.

        ``json`` is the native format the HTTP feed already accepts, so it is
        passed through unchanged rather than round-tripped through an adapter
        that would only re-validate what `TelemetryManager` validates anyway.
        """
        if config["format"] == "json":
            try:
                document = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise IngestError(f"MQTT JSON payload is invalid: {exc}") from exc
            reports = document.get("positions") if isinstance(document, dict) else document
            if not isinstance(reports, list):
                raise IngestError("MQTT JSON payload must be a list, or an object with 'positions'")
            return reports
        # The callsign falls back to the topic itself, which is usually the
        # most identifying thing available (e.g. "fleet/florian-11-1/position").
        return adapter(config["format"]).parse(
            payload, callsign=config.get("callsign") or topic.replace("/", "-")[:200])

    def status(self) -> dict[str, Any]:
        return {
            "available": AIOMQTT_AVAILABLE,
            "unavailable_reason": AIOMQTT_ERROR or None,
            "enabled": self.enabled,
            "connected": self.connected,
            "broker": _redacted(self.settings.mqtt_url),
            "topics": sorted(self.topics),
            "received": self.received,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "last_error": self.last_error,
        }


def _tls_context():
    """A default-verifying TLS context. Deliberately no relaxation switch - a
    broker carrying incident telemetry should not be reachable over an
    unverified channel."""
    import ssl

    return ssl.create_default_context()


def _redacted(url: str) -> str:
    """A broker URL safe to show in a status response.

    MQTT URLs routinely embed credentials, and `/api/status` is readable by
    every authenticated role - so the password never leaves this function.
    """
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.hostname:
        return "(malformed)"
    port = f":{parsed.port}" if parsed.port else ""
    user = f"{parsed.username}@" if parsed.username else ""
    return f"{parsed.scheme}://{user}{parsed.hostname}{port}"


def _topic_matches(pattern: str, topic: str) -> bool:
    """MQTT topic-filter matching: ``+`` is one level, ``#`` is the rest.

    Needed because a subscription may be a wildcard while the delivered topic
    is concrete, so the routing table cannot be a plain dict lookup.
    """
    pattern_parts, topic_parts = pattern.split("/"), topic.split("/")
    for index, part in enumerate(pattern_parts):
        if part == "#":
            # '#' must be the final level and matches everything below it.
            return index == len(pattern_parts) - 1
        if index >= len(topic_parts):
            return False
        if part != "+" and part != topic_parts[index]:
            return False
    return len(pattern_parts) == len(topic_parts)


def _parse_topic_map(raw: str) -> dict[str, dict[str, Any]]:
    """Parse ``"topic=source_id:format[:callsign],..."``.

    A malformed or unknown-format entry is dropped with a warning rather than
    failing startup - the same contract as every other config helper here, and
    the failure mode is "that one topic is not ingested" rather than "the
    server will not start during an incident".
    """
    topics: dict[str, dict[str, Any]] = {}
    for item in (part.strip() for part in str(raw or "").split(",") if part.strip()):
        topic, _, target = item.partition("=")
        pieces = target.split(":")
        if not topic.strip() or len(pieces) < 2:
            log.warning("Ignoring MQTT topic mapping %r (expected topic=source_id:format)", item)
            continue
        source_id, fmt = pieces[0].strip(), pieces[1].strip().lower()
        if not source_id or fmt not in SUPPORTED_FORMATS:
            log.warning("Ignoring MQTT topic mapping %r (format must be one of %s)",
                        item, ", ".join(SUPPORTED_FORMATS))
            continue
        topics[topic.strip()] = {"source_id": source_id, "format": fmt,
                                 "callsign": pieces[2].strip() if len(pieces) > 2 else ""}
    return topics


__all__ = ["AIOMQTT_AVAILABLE", "AIOMQTT_ERROR", "MAX_PAYLOAD_BYTES", "RECONNECT_MAX_SECONDS",
           "RECONNECT_MIN_SECONDS", "SUPPORTED_FORMATS", "MqttBridge"]
