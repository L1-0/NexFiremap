"""Streaming CoT/MAVLink listener - the TAK-side network contract.

`ingest/cot.py` parses CoT; this is the socket that receives it. TAK devices
do not POST to an HTTP endpoint: ATAK meshes over UDP multicast on a local
network, and a TAK Server federates over a persistent TCP stream. Supporting
those transports is what makes NexFiremap visible to a team already carrying
TAK devices without anyone reconfiguring their kit.

**This listener is disabled by default and must stay that way.** The reason is
worth stating plainly rather than burying: CoT over UDP has *no authentication
of any kind*. There is no token, no session, and MAVLink v2 signing is not
verified either (see `ingest/mavlink.py`). Anything that can send a packet to
the port can put a marker on the incident map. The compensating controls are:

  * off unless `NEXFIREMAP_COT_ENABLED` is set;
  * bound to an explicit address, defaulting to loopback, never 0.0.0.0 by
    accident;
  * every packet's source address checked against an operator-configured
    allowlist (`NEXFIREMAP_COT_ALLOW`), which defaults to loopback only;
  * a per-peer rate limit, so one misbehaving device cannot flood the store.

That is the incident-LAN trust model - the same one `lan_mode` already assumes
for a closed, physically-controlled network - and it is documented as such in
`docs/INCIDENT_LAN_RUNBOOK.md`. It is emphatically *not* suitable for an
internet-facing host. Proper TAK Server federation (TLS with client
certificates) is a follow-up, not something this quietly approximates.

Everything received here still lands through the ordinary managers:
position atoms go to `TelemetryManager.ingest` with the configured feed's own
token, and markers/drawings become tactical features through `OperationsStore`.
The gateway is a transport, not a second write path.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .ingest import IngestError
from .ingest import cot, mavlink
from .operations import OperationsError, OperationsStore
from .telemetry import TelemetryError, TelemetryManager

log = logging.getLogger("nexfiremap.cot")

#: Biggest single CoT payload accepted from a stream. A CoT event is a few
#: hundred bytes; this is generous for a batched TAK Server frame while
#: bounding what an unauthenticated peer can make us buffer and XML-parse.
MAX_PAYLOAD_BYTES = 512 * 1024

#: Per-peer packet budget per minute, mirroring `Settings.
#: telemetry_requests_per_minute`'s intent on the HTTP path. Kept in memory
#: only - a restart resets it, which only ever makes the limit temporarily
#: more permissive, never less.
DEFAULT_PACKETS_PER_MINUTE = 600


class CotGateway:
    """Listens for CoT (TCP + UDP) and MAVLink (UDP), routing into the
    existing managers. One instance per app, started from `lifespan()`."""

    def __init__(self, settings: Settings, telemetry: TelemetryManager,
                 store: OperationsStore) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.store = store
        self._server: asyncio.Server | None = None
        self._transports: list[asyncio.DatagramTransport] = []
        self._stopping = False
        self._rates: dict[str, deque[float]] = {}
        self._networks = self._parse_allowlist(settings.cot_allow)
        # Surfaced through /api/status so an operator can see whether the
        # gateway is actually hearing anything - "no markers" and "nothing has
        # reached the port" look identical on the map otherwise.
        self.received = 0
        self.rejected = 0
        self.positions = 0
        self.features = 0
        # Features received while feature acceptance is on but no incident is
        # configured. Counted rather than only logged so the misconfiguration
        # is visible in `status()` long after the one-shot warning scrolled by.
        self.features_discarded = 0
        self._warned_missing_incident = False
        self.last_error: str | None = None
        self.last_received_at: str | None = None

    @property
    def enabled(self) -> bool:
        """Whether to listen at all.

        Requires both the explicit enable flag *and* a configured feed source
        id: without a feed there is no token, no incident to attach to, and
        nowhere for a position to go, so listening would only collect packets
        it must then discard.
        """
        return bool(self.settings.cot_enabled and self.settings.cot_source_id)

    @staticmethod
    def _parse_allowlist(raw: str) -> list[ipaddress._BaseNetwork]:
        """Parse the CIDR allowlist, defaulting to loopback only.

        A malformed entry is dropped with a warning rather than failing
        startup - consistent with every other config helper - but the default
        when *nothing* parses is loopback, never "allow all". A config typo
        must narrow access, not widen it.
        """
        networks = []
        for item in (part.strip() for part in str(raw or "").split(",") if part.strip()):
            try:
                networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                log.warning("Ignoring invalid CoT allowlist entry %r", item)
        if not networks:
            networks = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")]
        return networks

    def _allowed(self, host: str) -> bool:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        # An IPv4 peer arriving on a dual-stack socket appears as ::ffff:a.b.c.d
        # and would otherwise never match an IPv4 CIDR in the allowlist.
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in self._networks)

    def _within_rate(self, host: str) -> bool:
        """Sliding one-minute per-peer budget. Same approach as
        `TelemetryManager.ingest`'s limiter, applied a layer earlier because
        here there is no token to attribute traffic to."""
        now = time.monotonic()
        history = self._rates.setdefault(host, deque())
        while history and history[0] <= now - 60:
            history.popleft()
        if len(history) >= self.settings.cot_packets_per_minute:
            return False
        history.append(now)
        return True

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if not self.enabled:
            if self.settings.cot_enabled:
                log.warning("CoT gateway enabled but NEXFIREMAP_COT_SOURCE_ID is unset; not listening")
            return
        host = self.settings.cot_host
        loop = asyncio.get_running_loop()
        try:
            self._server = await asyncio.start_server(self._handle_stream, host, self.settings.cot_tcp_port)
            for port, handler in ((self.settings.cot_udp_port, self._handle_cot_datagram),
                                  (self.settings.cot_mavlink_port, self._handle_mavlink_datagram)):
                if port:
                    transport, _ = await loop.create_datagram_endpoint(
                        lambda handler=handler: _Datagram(self, handler), local_addr=(host, port))
                    self._transports.append(transport)
        except OSError as exc:
            # A port already in use must not prevent the fire map from
            # starting - the gateway is an optional extra, not a dependency.
            log.error("CoT gateway could not bind on %s: %s", host, exc)
            self.last_error = str(exc)
            await self.stop()
            return
        log.info("CoT gateway listening on %s (TCP %d, UDP %d, MAVLink UDP %s); allowlist: %s",
                 host, self.settings.cot_tcp_port, self.settings.cot_udp_port,
                 self.settings.cot_mavlink_port or "off",
                 ", ".join(str(network) for network in self._networks))

    async def stop(self) -> None:
        self._stopping = True
        for transport in self._transports:
            transport.close()
        self._transports.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ------------------------------------------------------------ transports

    async def _handle_stream(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One TCP connection, which for TAK is long-lived and carries many events.

        Events are framed by the closing ``</event>`` tag rather than by any
        length prefix, because CoT over TCP has no framing of its own - a TAK
        Server simply writes documents back to back. Buffering until that tag
        appears is what makes a read that lands mid-event recover instead of
        handing a truncated document to the parser.
        """
        peer = writer.get_extra_info("peername")
        host = str(peer[0]) if peer else ""
        if not self._allowed(host):
            self.rejected += 1
            log.warning("Rejected CoT connection from %s (not in allowlist)", host)
            writer.close()
            return
        log.info("CoT stream connected from %s", host)
        buffer = bytearray()
        try:
            while not self._stopping:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > MAX_PAYLOAD_BYTES:
                    log.warning("CoT stream from %s exceeded %d bytes; dropping", host, MAX_PAYLOAD_BYTES)
                    buffer.clear()
                    continue
                marker = buffer.rfind(b"</event>")
                if marker == -1:
                    continue
                complete = bytes(buffer[: marker + len(b"</event>")])
                del buffer[: marker + len(b"</event>")]
                await self._dispatch_cot(complete, host)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass  # a device dropping off the net is normal, not an error
        finally:
            writer.close()

    async def _handle_cot_datagram(self, data: bytes, host: str) -> None:
        await self._dispatch_cot(data, host)

    async def _handle_mavlink_datagram(self, data: bytes, host: str) -> None:
        try:
            reports = mavlink.parse(data, callsign=self.settings.cot_mavlink_callsign,
                                    received_at=datetime.now(timezone.utc))
        except IngestError as exc:
            # A ground station emits dozens of message types; most datagrams
            # legitimately contain no position at all, so this is debug-level
            # noise rather than a fault.
            log.debug("MAVLink datagram from %s carried no position: %s", host, exc)
            return
        await self._ingest_positions(reports, host)

    # -------------------------------------------------------------- routing

    async def _dispatch_cot(self, payload: bytes, host: str) -> None:
        """Split one payload into positions and features and route each.

        The atom/bit distinction is CoT's own (see `cot.is_atom`), and it maps
        exactly onto NexFiremap's split between telemetry and tactical
        features - which is what lets one connection carry a fleet's positions
        and a commander's drawn perimeter without any per-message
        configuration.
        """
        self.received += 1
        self.last_received_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            events = cot.parse_events(payload)
        except IngestError as exc:
            self.rejected += 1
            self.last_error = str(exc)
            log.warning("Malformed CoT from %s: %s", host, exc)
            return

        positions = [item["payload"] for item in events if item["contract"] == "position"]
        overlays = [item["payload"] for item in events if item["contract"] == "overlay"]
        if positions:
            await self._ingest_positions(positions, host)
        if overlays and self.settings.cot_accept_features:
            await asyncio.to_thread(self._upsert_features, overlays, host)

    async def _ingest_positions(self, reports: list[dict[str, Any]], host: str) -> None:
        """Hand positions to `TelemetryManager` using the configured feed's token.

        Routing through `ingest` rather than writing rows directly is the whole
        point: the gateway inherits the batch limits, the replay-safety hash,
        the derived speed/quality flags and the resource-position update, none
        of which it reimplements.
        """
        try:
            result = await asyncio.to_thread(
                self.telemetry.ingest, self.settings.cot_source_id,
                self.settings.cot_source_token, reports)
        except PermissionError:
            # Almost always a stale token after a rotate. Loud, because the
            # gateway is otherwise silently discarding every position.
            self.last_error = "configured CoT feed token was rejected"
            log.error("CoT gateway token rejected for feed %s - check NEXFIREMAP_COT_SOURCE_TOKEN",
                      self.settings.cot_source_id)
            return
        except TelemetryError as exc:
            self.last_error = str(exc)
            log.warning("CoT positions from %s rejected: %s", host, exc)
            return
        self.positions += int(result.get("accepted", 0))

    def _upsert_features(self, overlays: list[tuple[dict[str, Any], dict[str, Any]]], host: str) -> None:
        """Create or update tactical features, keyed by the sender's CoT uid.

        The uid lookup is what makes a repeating sender idempotent: ATAK
        refreshes a marker every few seconds, and creating a row per refresh
        would bury the incident under thousands of duplicates within an hour.
        Matching on the stored ``cot_uid`` turns each refresh into an update of
        the feature it already produced.
        """
        incident_id = self.settings.cot_incident_id
        if not incident_id:
            # Feature acceptance is switched on but has nowhere to put anything.
            # This used to return in silence, so an operator who had enabled
            # cot_accept_features and forgotten cot_incident_id saw a gateway
            # reporting healthy receipt while every tactical marker a TAK device
            # sent was dropped - "features disabled" and "features misconfigured"
            # looked identical from outside.
            #
            # Warned once rather than per packet: an ATAK device refreshes its
            # markers every few seconds, and a per-packet log would bury the
            # message it is trying to deliver. `status()` carries the standing
            # version of the same fact.
            if not self._warned_missing_incident:
                self._warned_missing_incident = True
                log.warning(
                    "NEXFIREMAP_COT_ACCEPT_FEATURES is on but NEXFIREMAP_COT_INCIDENT_ID is "
                    "unset - inbound CoT tactical features are being discarded. Set the "
                    "incident id, or turn feature acceptance off.")
            self.features_discarded += len(overlays)
            return
        for geometry, properties in overlays:
            uid = str(properties.get("cot_uid") or "")
            if not uid:
                continue
            payload = {
                "feature_type": properties.get("feature_type"),
                "title": properties.get("title") or uid,
                "geometry": geometry,
                "status": "observed",
                "source": f"cot:{host}",
                "observed_at": properties.get("observed_at"),
                "properties": {"cot_uid": uid, "cot_type": properties.get("cot_type"),
                               "cot_stale": properties.get("cot_stale"),
                               "symbology_profile": self.settings.symbology_profile},
            }
            try:
                existing = self.db_lookup(incident_id, uid)
                if existing is None:
                    self.store.create_feature(incident_id, payload, f"cot:{host}")
                    self.features += 1
                else:
                    # expected_revision is a positional argument, not a key in
                    # `data` - passing it inside the dict silently sends the
                    # actor into that slot and every update fails the
                    # optimistic-concurrency check.
                    self.store.update_feature(
                        incident_id, existing["id"], payload,
                        existing["revision"], f"cot:{host}")
            except (OperationsError, KeyError) as exc:
                # One malformed marker must not abandon the rest of the batch.
                self.last_error = str(exc)
                log.warning("CoT feature %s from %s rejected: %s", uid, host, exc)

    def db_lookup(self, incident_id: str, uid: str) -> dict[str, Any] | None:
        """Find a feature previously created from this CoT uid.

        Uses SQLite's ``json_extract`` against ``properties_json`` rather than
        a dedicated column, so no schema change is needed; the expression index
        declared in `db.py`'s SCHEMA keeps it from being a table scan on every
        inbound marker.
        """
        row = self.store.db.conn.execute(
            "SELECT id,revision FROM tactical_features "
            "WHERE incident_id=? AND json_extract(properties_json,'$.cot_uid')=? "
            "AND deleted_at IS NULL LIMIT 1",
            (incident_id, uid),
        ).fetchone()
        return None if row is None else {"id": row["id"], "revision": row["revision"]}

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "listening": self._server is not None,
            "host": self.settings.cot_host,
            "tcp_port": self.settings.cot_tcp_port,
            "udp_port": self.settings.cot_udp_port,
            "mavlink_port": self.settings.cot_mavlink_port,
            "allowlist": [str(network) for network in self._networks],
            "received": self.received,
            "rejected": self.rejected,
            "positions": self.positions,
            "features": self.features,
            "features_discarded": self.features_discarded,
            # Surfaced so "features are off" and "features are on but going
            # nowhere" are distinguishable without reading the server log.
            "accept_features": bool(self.settings.cot_accept_features),
            "incident_id": self.settings.cot_incident_id or None,
            "last_received_at": self.last_received_at,
            "last_error": self.last_error,
        }


class _Datagram(asyncio.DatagramProtocol):
    """UDP receive side: allowlist and rate-limit, then hand off.

    The checks live here rather than in the handlers so that a packet from a
    disallowed or flooding peer is dropped before anything parses it - with no
    authentication available on this transport, the cheapest possible rejection
    is the only real protection the port has.
    """

    def __init__(self, gateway: CotGateway, handler: Any) -> None:
        self.gateway = gateway
        self.handler = handler

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        host = str(addr[0])
        if not self.gateway._allowed(host):
            self.gateway.rejected += 1
            return
        if not self.gateway._within_rate(host):
            self.gateway.rejected += 1
            return
        if len(data) > MAX_PAYLOAD_BYTES:
            self.gateway.rejected += 1
            return
        # Fire-and-forget: a datagram protocol callback cannot await, and
        # blocking it would stall every other packet on the socket.
        asyncio.get_running_loop().create_task(self.handler(data, host))

    def error_received(self, exc: Exception) -> None:  # pragma: no cover - transport-level
        log.debug("CoT datagram error: %s", exc)


__all__ = ["DEFAULT_PACKETS_PER_MINUTE", "MAX_PAYLOAD_BYTES", "CotGateway"]
