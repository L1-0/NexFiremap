"""MAVLink v2 position frames - live drone telemetry, without pymavlink.

`drone.py` already handles the *evidence* side of drone operations (imagery,
mosaics, missions). What it has no way to take is a live position stream from
an aircraft in the air, which is what a ground-control station is already
broadcasting over UDP throughout a flight.

Scope is deliberately one message: ``GLOBAL_POSITION_INT`` (id 33), the fused
position/velocity estimate every autopilot emits. That single message is what
puts the aircraft on the incident map alongside the vehicles, and decoding it
needs a fixed-offset struct unpack rather than the full MAVLink machinery -
which is why this is ~100 lines of stdlib instead of a pymavlink dependency
that would pull in a code generator and a dialect tree to read one packet.

What this deliberately does **not** do, and why it is safe not to:

*No CRC validation.* MAVLink's checksum needs a per-message ``CRC_EXTRA`` byte
from the dialect definition - exactly the generated table this module exists to
avoid. Instead, every decoded field is range-checked (see `_frame`), so a
corrupted packet is rejected on its contents rather than its checksum. A
corruption that survives *both* the length check and the coordinate/altitude
ranges would have to be a valid position, which is harmless.

*No signing.* MAVLink v2's optional signature is not verified, so a frame's
origin is not authenticated. That is why the gateway that feeds this module
binds LAN-only behind an address allowlist and is disabled by default - see
`cot_gateway`. This module never decides whether to trust a packet; it only
decodes one.
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from typing import Any

from . import CONTRACT_POSITION, IngestError, clean_text, position_report

NAME = "mavlink"
CONTRACT = CONTRACT_POSITION
MEDIA_TYPES = ("application/x-mavlink",)

#: Frame start bytes. v1 (0xFE) is still emitted by older autopilots and has a
#: different header length, so both are recognised rather than assuming v2.
MAGIC_V2 = 0xFD
MAGIC_V1 = 0xFE

#: Header sizes *including* the magic byte, excluding the 2-byte CRC trailer.
HEADER_V2 = 10
HEADER_V1 = 6

#: The one message this module decodes.
GLOBAL_POSITION_INT = 33

#: Its payload is 28 bytes of little-endian fields:
#:   time_boot_ms(u32) lat(i32) lon(i32) alt(i32) relative_alt(i32)
#:   vx(i16) vy(i16) vz(i16) hdg(u16)
#: Coordinates are degrees x 1e7; altitudes are millimetres; velocities are
#: cm/s; heading is centidegrees (65535 = unknown).
_GLOBAL_POSITION_STRUCT = struct.Struct("<IiiiihhhH")
GLOBAL_POSITION_LENGTH = _GLOBAL_POSITION_STRUCT.size

#: MAVLink's "heading unknown" sentinel. Storing 65535 centidegrees as a real
#: bearing would be 655 degrees, which fails validation - but storing it as
#: 0 would silently claim the aircraft is pointing due north.
HEADING_UNKNOWN = 65535

#: Bound on frames per payload, so a malformed stream cannot drive an
#: unbounded loop before any range check runs.
MAX_FRAMES = 10_000


def _frames(raw: bytes) -> list[dict[str, Any]]:
    """Walk a byte stream and yield every well-formed frame's header + payload.

    UDP delivers whole datagrams but a TCP read can split or coalesce frames,
    and a ground station interleaves dozens of message types. So this scans for
    a magic byte, validates that the declared length fits, and skips forward
    one byte on anything that does not - resynchronising rather than giving up,
    which is what lets a mid-frame read boundary recover on the next packet.
    """
    frames: list[dict[str, Any]] = []
    offset, length = 0, len(raw)
    while offset < length and len(frames) < MAX_FRAMES:
        magic = raw[offset]
        if magic == MAGIC_V2:
            header, payload_at = HEADER_V2, offset + HEADER_V2
            if offset + HEADER_V2 > length:
                break
            payload_length = raw[offset + 1]
            incompatible = raw[offset + 2]
            # A frame with the signing flag set carries 13 extra trailing
            # bytes. We do not verify the signature (see the module docstring)
            # but must account for its length or the next frame starts in the
            # wrong place and the whole stream desynchronises.
            trailer = 2 + (13 if incompatible & 0x01 else 0)
            message_id = int.from_bytes(raw[offset + 7:offset + 10], "little")
            sequence, system_id, component_id = raw[offset + 4], raw[offset + 5], raw[offset + 6]
        elif magic == MAGIC_V1:
            header, payload_at = HEADER_V1, offset + HEADER_V1
            if offset + HEADER_V1 > length:
                break
            payload_length = raw[offset + 1]
            trailer = 2
            message_id = raw[offset + 5]
            sequence, system_id, component_id = raw[offset + 2], raw[offset + 3], raw[offset + 4]
        else:
            offset += 1
            continue

        end = payload_at + payload_length
        if end + trailer > length:
            # Truncated frame - a partial read, not necessarily corruption.
            break
        frames.append({
            "message_id": message_id, "sequence": sequence,
            "system_id": system_id, "component_id": component_id,
            "payload": raw[payload_at:end],
            "version": 2 if magic == MAGIC_V2 else 1,
        })
        offset = end + trailer
    return frames


def _decode_global_position(frame: dict[str, Any], *, boot_reference: datetime) -> dict[str, Any] | None:
    """One GLOBAL_POSITION_INT payload as canonical units, or ``None`` if unusable.

    MAVLink v2 truncates trailing zero bytes on the wire ("payload
    truncation"), so a short payload is normal rather than corrupt and is
    zero-padded back to full length here. Treating it as an error would drop
    perfectly valid frames from any modern autopilot.
    """
    payload = frame["payload"]
    if len(payload) < GLOBAL_POSITION_LENGTH:
        payload = payload + b"\x00" * (GLOBAL_POSITION_LENGTH - len(payload))
    elif len(payload) > GLOBAL_POSITION_LENGTH:
        payload = payload[:GLOBAL_POSITION_LENGTH]

    boot_ms, lat, lon, alt_mm, relative_mm, vx, vy, vz, heading = _GLOBAL_POSITION_STRUCT.unpack(payload)

    latitude, longitude = lat / 1e7, lon / 1e7
    # Standing in for the CRC we deliberately do not compute: a corrupted
    # frame overwhelmingly decodes to coordinates outside these ranges.
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    # An autopilot with no GPS lock publishes 0,0 - the same trap as CoT's
    # null island, and the same reason to skip it rather than plot it.
    if latitude == 0.0 and longitude == 0.0:
        return None

    # Velocity is a 3D vector in cm/s; ground speed is the horizontal
    # component only, since vertical rate is not "how fast it is travelling".
    ground_speed_ms = ((vx / 100.0) ** 2 + (vy / 100.0) ** 2) ** 0.5
    return {
        "latitude": latitude,
        "longitude": longitude,
        "altitude_m": alt_mm / 1000.0,
        "relative_altitude_m": relative_mm / 1000.0,
        "speed_kmh": round(ground_speed_ms * 3.6, 3),
        "heading_deg": None if heading == HEADING_UNKNOWN else (heading / 100.0) % 360,
        "climb_rate_ms": -vz / 100.0,  # MAVLink vz is positive *downward*
        "boot_ms": boot_ms,
        # GLOBAL_POSITION_INT carries no wall-clock time, only milliseconds
        # since the autopilot booted. See `parse` for how that becomes an
        # absolute timestamp and why that is an approximation.
        "observed_at": (boot_reference + timedelta(milliseconds=boot_ms)).isoformat(
            timespec="milliseconds").replace("+00:00", "Z"),
        "system_id": frame["system_id"],
        "component_id": frame["component_id"],
    }


def parse(raw: bytes, *, callsign: str = "", received_at: datetime | None = None,
          **_context: Any) -> list[dict[str, Any]]:
    """Every GLOBAL_POSITION_INT position in a MAVLink byte stream.

    **On timestamps.** GLOBAL_POSITION_INT reports ``time_boot_ms`` - time
    since the autopilot powered on - and nothing else. There is no way to turn
    that into a wall-clock instant from the message alone. The approximation
    used here anchors the *newest* frame in the payload to the moment of
    receipt and back-dates the others by their relative boot times. That is
    accurate to the transport delay (milliseconds on a LAN) for a live stream,
    and it keeps the ordering and spacing within one payload exactly right,
    which is what track reconstruction actually depends on. It would be wrong
    for a replayed log, so `received_at` is injectable for exactly that case.

    ``callsign`` comes from the caller because MAVLink identifies an aircraft
    by a numeric ``system_id``, not a name; the system id is preserved in the
    report's extras so several aircraft on one link stay distinguishable.
    """
    name = clean_text(callsign, 200)
    if not name:
        raise IngestError("MAVLink carries no callsign; one must be supplied by the caller")
    if not raw:
        raise IngestError("MAVLink payload is empty")

    frames = [frame for frame in _frames(raw) if frame["message_id"] == GLOBAL_POSITION_INT]
    if not frames:
        raise IngestError("MAVLink payload contains no GLOBAL_POSITION_INT frames")

    anchor = received_at or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        raise IngestError("received_at must be timezone-aware")

    # Decode once against an arbitrary epoch to learn the newest boot time,
    # then re-anchor so that frame lands exactly at `anchor`.
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    provisional = [decoded for frame in frames
                   if (decoded := _decode_global_position(frame, boot_reference=epoch)) is not None]
    if not provisional:
        raise IngestError("MAVLink payload contains no GLOBAL_POSITION_INT frame with a valid fix")
    newest_boot_ms = max(item["boot_ms"] for item in provisional)
    boot_reference = anchor - timedelta(milliseconds=newest_boot_ms)

    reports = []
    for frame in frames:
        decoded = _decode_global_position(frame, boot_reference=boot_reference)
        if decoded is None:
            continue
        reports.append(position_report(
            # system_id in the key as well as the timestamp: one link can
            # carry several aircraft, and two of them sharing a boot time
            # must not collide on the replay key.
            external_id=f"{name}#{decoded['system_id']}@{decoded['observed_at']}",
            callsign=name,
            observed_at=decoded["observed_at"],
            latitude=decoded["latitude"],
            longitude=decoded["longitude"],
            altitude_m=decoded["altitude_m"],
            speed_kmh=decoded["speed_kmh"],
            heading_deg=decoded["heading_deg"],
            accuracy_m=None,  # GLOBAL_POSITION_INT carries no accuracy estimate
            extra={
                "mavlink_system_id": decoded["system_id"],
                "mavlink_component_id": decoded["component_id"],
                "mavlink_time_boot_ms": decoded["boot_ms"],
                "relative_altitude_m": decoded["relative_altitude_m"],
                "climb_rate_ms": decoded["climb_rate_ms"],
                # Recorded because it changes what observed_at means - see the
                # docstring: this is a derived wall-clock, not a reported one.
                "timestamp_source": "derived_from_time_boot_ms",
            },
        ))
    return reports


def encode_global_position(*, latitude: float, longitude: float, altitude_m: float,
                           relative_altitude_m: float = 0.0, vx_ms: float = 0.0,
                           vy_ms: float = 0.0, vz_ms: float = 0.0,
                           heading_deg: float | None = None, boot_ms: int = 0,
                           system_id: int = 1, component_id: int = 1,
                           sequence: int = 0) -> bytes:
    """Build one MAVLink v2 GLOBAL_POSITION_INT frame.

    Present so the parser can be tested against frames with known contents
    without checking a binary fixture into the repository, and so the gateway
    has something to smoke-test against. The CRC bytes are zero, which is
    consistent with this module not validating them.
    """
    payload = _GLOBAL_POSITION_STRUCT.pack(
        int(boot_ms), int(round(latitude * 1e7)), int(round(longitude * 1e7)),
        int(round(altitude_m * 1000)), int(round(relative_altitude_m * 1000)),
        int(round(vx_ms * 100)), int(round(vy_ms * 100)), int(round(vz_ms * 100)),
        HEADING_UNKNOWN if heading_deg is None else int(round(heading_deg * 100)) % 36000,
    )
    header = bytes([MAGIC_V2, len(payload), 0, 0, sequence & 0xFF, system_id, component_id])
    return header + GLOBAL_POSITION_INT.to_bytes(3, "little") + payload + b"\x00\x00"


__all__ = ["CONTRACT", "GLOBAL_POSITION_INT", "GLOBAL_POSITION_LENGTH", "HEADING_UNKNOWN",
           "MAGIC_V1", "MAGIC_V2", "MAX_FRAMES", "MEDIA_TYPES", "NAME",
           "encode_global_position", "parse"]
