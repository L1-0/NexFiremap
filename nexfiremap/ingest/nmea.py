"""NMEA 0183 position sentences - what GPS hardware actually emits.

A vehicle AVL box, a hand-held receiver or a serial GPS puck speaks NMEA, not
JSON. Accepting it directly on the existing feed endpoint means a department
can point its hardware (or a two-line serial-to-HTTP relay) at NexFiremap
without anyone writing a translation layer.

Only the two sentences that carry a position are parsed - ``RMC`` (the
"recommended minimum": position, speed, course, *and date*) and ``GGA``
(position, altitude, fix quality, satellites). Everything else in a typical
sentence burst (GSA, GSV, VTG, GLL...) is skipped rather than rejected,
because a receiver emits them all together and failing the batch over a
satellite-status sentence would discard the position that arrived with it.

Three things about NMEA make this less trivial than it looks:

*Coordinates are not degrees.* NMEA writes ``ddmm.mmmm`` - degrees and
*minutes* concatenated. Reading ``4807.038`` as 4807.038 degrees fails
validation, but reading it as 48.07038 degrees is the subtle version of the
same bug: it looks like a plausible latitude and is about 8 km wrong. The
correct value is 48 + 07.038/60.

*GGA has no date.* It carries only a time of day, so a GGA on its own cannot
produce a full timestamp. `parse` takes the date from any RMC in the same
payload, which is how receivers actually emit them; see `_resolve_date` for
what happens when there is none.

*Identity comes from outside.* NMEA carries no callsign, unit id or vehicle
name whatsoever - the sentence says where *something* is, not what. The
identity therefore has to come from the caller (the feed's own configuration
or a ``?callsign=`` parameter), which is why `parse` requires it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import CONTRACT_POSITION, IngestError, clean_text, position_report

NAME = "nmea"
CONTRACT = CONTRACT_POSITION
MEDIA_TYPES = ("text/plain", "application/vnd.nmea", "text/nmea")

#: Sentence types carrying a usable position. Matched on the last three
#: characters so every talker id works - GP (GPS), GN (multi-constellation
#: GNSS, what most modern receivers emit), GL (GLONASS), GA (Galileo),
#: GB/BD (BeiDou) - without enumerating the combinations.
POSITION_SENTENCES = ("RMC", "GGA")

#: GGA fix-quality values that mean "this is a real position". 0 is "no fix"
#: and 6 is dead-reckoning (an inertial estimate, not an observation), both of
#: which would otherwise be stored as though a satellite had confirmed them.
VALID_GGA_FIX = {"1", "2", "3", "4", "5", "7", "8", "9"}

#: One knot in km/h. NMEA speed-over-ground is in knots; NexFiremap stores km/h.
KMH_PER_KNOT = 1.852

#: Ceiling on sentences per payload - a receiver emitting at 10 Hz for a minute
#: is a few hundred, so this is generous while still bounded.
MAX_SENTENCES = 5_000


def _checksum_ok(sentence: str) -> bool:
    """Validate the ``*hh`` trailer: an XOR of every byte between ``$`` and ``*``.

    A sentence with no checksum at all is accepted - it is optional in NMEA
    0183 and some cheap receivers omit it - but a checksum that is *present
    and wrong* means the line was corrupted in transit, which over a flaky
    serial or radio link is exactly the case worth rejecting. Silently
    ingesting a garbled position would put a vehicle somewhere it never was.
    """
    if "*" not in sentence:
        return True
    body, _, declared = sentence.partition("*")
    declared = declared.strip()[:2]
    if len(declared) != 2:
        return False
    checksum = 0
    for character in body.lstrip("$!"):
        checksum ^= ord(character)
    try:
        return checksum == int(declared, 16)
    except ValueError:
        return False


def _degrees(value: str, hemisphere: str, *, is_longitude: bool) -> float:
    """``ddmm.mmmm`` + hemisphere to signed decimal degrees.

    The degree field is fixed width (2 digits for latitude, 3 for longitude)
    and the rest is minutes - see the module docstring on why treating the
    whole thing as degrees is a bug that survives validation.
    """
    raw = clean_text(value, 20)
    if not raw:
        raise IngestError("NMEA coordinate is empty")
    width = 3 if is_longitude else 2
    if len(raw.split(".")[0]) < width:
        raise IngestError(f"NMEA coordinate {raw!r} is too short to carry degrees and minutes")
    try:
        degrees = float(raw[:width])
        minutes = float(raw[width:])
    except ValueError as exc:
        raise IngestError(f"NMEA coordinate {raw!r} is not numeric") from exc
    if minutes >= 60:
        raise IngestError(f"NMEA coordinate {raw!r} has invalid minutes")
    result = degrees + minutes / 60.0
    if clean_text(hemisphere, 1).upper() in {"S", "W"}:
        result = -result
    return result


def _timestamp(time_field: str, date_field: str | None) -> str:
    """Combine NMEA's ``hhmmss.ss`` and ``ddmmyy`` fields into an ISO instant.

    NMEA time is always UTC, which is the one thing that makes this safe -
    `iso_timestamp` would reject a naive local time, and rightly so.

    The two-digit year is resolved as 2000+yy. NMEA 0183 has no century field,
    so any interpretation is a convention; this one is correct until 2100 and
    wrong in a way that is immediately obvious rather than subtly off.
    """
    raw_time = clean_text(time_field, 20)
    if len(raw_time) < 6:
        raise IngestError("NMEA sentence has no usable time field")
    try:
        hour, minute = int(raw_time[0:2]), int(raw_time[2:4])
        second = float(raw_time[4:])
    except ValueError as exc:
        raise IngestError(f"NMEA time {raw_time!r} is not numeric") from exc

    raw_date = clean_text(date_field, 10)
    if len(raw_date) == 6:
        try:
            day, month, two_digit_year = int(raw_date[0:2]), int(raw_date[2:4]), int(raw_date[4:6])
        except ValueError as exc:
            raise IngestError(f"NMEA date {raw_date!r} is not numeric") from exc
        # NMEA 0183 has no century field, so the two-digit year needs a
        # convention. This is the POSIX/`strptime("%y")` sliding window
        # (69-99 -> 19xx, 00-68 -> 20xx). A flat "2000 + yy" is the tempting
        # simplification and is wrong for any pre-2000 sentence - it reads the
        # standard's own 1994 reference example as 2094, and would put a
        # replayed historical track a century in the future. GPS itself only
        # dates from 1980, so nothing legitimate falls in the 69-79 gap.
        year = (1900 if two_digit_year >= 69 else 2000) + two_digit_year
    else:
        # No date anywhere in the payload. See `_resolve_date`: this is the
        # documented fallback, and it is why a GGA-only feed is timestamped
        # on the server's calendar day rather than the receiver's.
        today = datetime.now(timezone.utc)
        day, month, year = today.day, today.month, today.year

    try:
        moment = datetime(year, month, day, hour, minute, tzinfo=timezone.utc) + timedelta(seconds=second)
    except ValueError as exc:
        raise IngestError(f"NMEA sentence has an invalid date/time: {exc}") from exc
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _resolve_date(sentences: list[list[str]]) -> str | None:
    """The date field from any RMC in the payload, for the GGAs around it.

    Receivers emit a sentence *burst* per fix - typically GGA, GSA, several
    GSVs, then RMC - so the date is usually present somewhere even when the
    individual GGA being parsed has none. Scanning the whole payload first is
    what lets a mixed burst produce correctly-dated positions.

    Returns ``None`` when the payload is GGA-only, in which case `_timestamp`
    falls back to the server's current UTC date. That fallback is wrong by a
    day for a fix taken just before midnight and delivered just after, which
    is why RMC is strongly preferred and why this is documented rather than
    hidden.
    """
    for fields in sentences:
        if fields[0][-3:] == "RMC" and len(fields) > 9 and clean_text(fields[9], 10):
            return fields[9]
    return None


def _rmc(fields: list[str]) -> dict[str, Any] | None:
    """One RMC sentence, or ``None`` when the receiver reports no valid fix.

    Field 2 is the status: ``A`` (active) or ``V`` (void). A void sentence
    still carries stale or zeroed coordinates, so ingesting it would plant a
    vehicle at its last known - or at 0,0 - and present it as current.
    """
    if len(fields) < 10 or clean_text(fields[2], 1).upper() != "A":
        return None
    latitude = _degrees(fields[3], fields[4], is_longitude=False)
    longitude = _degrees(fields[5], fields[6], is_longitude=True)
    speed_knots = clean_text(fields[7], 20)
    course = clean_text(fields[8], 20)
    return {
        "latitude": latitude,
        "longitude": longitude,
        "time": fields[1],
        "date": fields[9],
        # Knots to km/h. Storing the knot value directly would understate
        # every speed by a factor of 1.852 - plausible-looking and wrong.
        "speed_kmh": round(float(speed_knots) * KMH_PER_KNOT, 3) if speed_knots else None,
        "heading_deg": float(course) % 360 if course else None,
        "altitude_m": None,
        "accuracy_m": None,
        "sentence": "RMC",
    }


def _gga(fields: list[str]) -> dict[str, Any] | None:
    """One GGA sentence, or ``None`` when the fix quality says there is none.

    HDOP is converted to a rough metre figure by multiplying by a nominal 5 m
    UERE. That is an approximation, not a measurement - but `TelemetryManager`
    flags anything over 100 m as poor accuracy, and leaving the field empty
    would mean a receiver with a terrible fix looks exactly like one with a
    perfect one.
    """
    if len(fields) < 10 or clean_text(fields[6], 2) not in VALID_GGA_FIX:
        return None
    latitude = _degrees(fields[2], fields[3], is_longitude=False)
    longitude = _degrees(fields[4], fields[5], is_longitude=True)
    hdop = clean_text(fields[8], 20)
    altitude = clean_text(fields[9], 20)
    return {
        "latitude": latitude,
        "longitude": longitude,
        "time": fields[1],
        "date": None,
        "speed_kmh": None,
        "heading_deg": None,
        "altitude_m": float(altitude) if altitude else None,
        "accuracy_m": round(float(hdop) * 5.0, 1) if hdop else None,
        "sentence": "GGA",
        "fix_quality": clean_text(fields[6], 2),
        "satellites": clean_text(fields[7], 4),
    }


def parse(raw: bytes, *, callsign: str = "", **_context: Any) -> list[dict[str, Any]]:
    """Every valid fix in an NMEA sentence stream.

    ``callsign`` is required because NMEA carries no identity of its own - see
    the module docstring. The caller (the feed endpoint) supplies it from the
    feed's configuration or a query parameter.

    A receiver emitting GGA *and* RMC for the same instant would otherwise
    produce two position reports a fraction of a second apart, doubling every
    track. They are merged per timestamp instead, taking RMC's speed/course and
    GGA's altitude/accuracy - which is strictly more information than either
    sentence carries alone.
    """
    name = clean_text(callsign, 200)
    if not name:
        raise IngestError("NMEA carries no identity; a callsign must be supplied by the caller")

    text = raw.decode("ascii", errors="replace") if isinstance(raw, bytes) else str(raw)
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
    if not lines:
        raise IngestError("NMEA payload is empty")
    if len(lines) > MAX_SENTENCES:
        raise IngestError(f"NMEA payload contains more than {MAX_SENTENCES} sentences")

    parsed: list[list[str]] = []
    for line in lines:
        if not line.startswith(("$", "!")):
            continue
        if not _checksum_ok(line):
            raise IngestError(f"NMEA checksum failed for {line[:16]!r}")
        fields = line.split("*")[0].lstrip("$!").split(",")
        if fields and fields[0][-3:] in POSITION_SENTENCES:
            parsed.append(fields)
    if not parsed:
        raise IngestError("NMEA payload contains no RMC or GGA sentences")

    fallback_date = _resolve_date(parsed)

    # Keyed by timestamp so a GGA and an RMC describing the same fix merge into
    # one report rather than two near-identical ones.
    merged: dict[str, dict[str, Any]] = {}
    for fields in parsed:
        fix = _rmc(fields) if fields[0][-3:] == "RMC" else _gga(fields)
        if fix is None:
            continue
        observed_at = _timestamp(fix["time"], fix["date"] or fallback_date)
        current = merged.setdefault(observed_at, {"observed_at": observed_at, "sentences": []})
        current["sentences"].append(fix["sentence"])
        for key in ("latitude", "longitude", "speed_kmh", "heading_deg", "altitude_m", "accuracy_m"):
            if fix.get(key) is not None:
                current[key] = fix[key]
        for key in ("fix_quality", "satellites"):
            if fix.get(key):
                current[key] = fix[key]

    reports = []
    for observed_at, fix in sorted(merged.items()):
        if "latitude" not in fix:
            continue
        reports.append(position_report(
            # Callsign plus timestamp: stable for a resend of the same fix
            # (correctly counted as a replay) and distinct between fixes.
            external_id=f"{name}@{observed_at}",
            callsign=name,
            observed_at=observed_at,
            latitude=fix["latitude"],
            longitude=fix["longitude"],
            altitude_m=fix.get("altitude_m"),
            speed_kmh=fix.get("speed_kmh"),
            heading_deg=fix.get("heading_deg"),
            accuracy_m=fix.get("accuracy_m"),
            extra={"nmea_sentences": sorted(set(fix["sentences"])),
                   "nmea_fix_quality": fix.get("fix_quality"),
                   "nmea_satellites": fix.get("satellites"),
                   # Recorded because it changes what the timestamp means: with
                   # no RMC the calendar day came from the server's clock, not
                   # the receiver's, which is wrong across a midnight boundary.
                   "nmea_date_source": "sentence" if fallback_date else "server_clock"},
        ))
    if not reports:
        raise IngestError("NMEA payload contains no sentence with a valid fix")
    return reports


__all__ = ["CONTRACT", "KMH_PER_KNOT", "MAX_SENTENCES", "MEDIA_TYPES", "NAME",
           "POSITION_SENTENCES", "VALID_GGA_FIX", "parse"]
