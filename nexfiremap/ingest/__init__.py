"""Stateless format adapters that normalise external standards into the
ingestion contracts NexFiremap already has.

The problem this package exists to solve: a fire department's existing
equipment does not speak NexFiremap's dialects. It speaks Cursor on Target
(ATAK/WinTAK), CAP (MoWaS/NINA, DWD, IPAWS), NMEA or the OsmAnd/Traccar
tracker protocols, ESRI Shapefile, and whatever JSON its dispatch/CAD vendor
happened to pick. Supporting each of those by growing a *parallel pipeline*
- its own validation, its own writes, its own provenance handling - is how a
codebase ends up with five subtly different ways to create the same row.

So the rule here is: an adapter only ever *translates*. It converts bytes in
some external format into one of the small set of contracts defined below,
each of which is exactly the shape an existing manager already accepts:

    CONTRACT_POSITION  ->  TelemetryManager.ingest()      (telemetry.py)
    CONTRACT_OVERLAY   ->  FieldImportManager.prepare()   (field_import.py)
    CONTRACT_ALERT     ->  AlertManager                   (alerts.py)

Nothing in this package touches the database, opens a socket, or reads a
setting. That keeps every adapter trivially testable (bytes in, dicts out -
see ``tests/test_cot.py`` and friends) and, more importantly, means adding a
standard can never accidentally bypass the token authentication, rate
limiting, replay-safety, preview-before-apply or provenance guarantees the
receiving managers enforce. A new standard is a new parser, never a new
write path.

Stateful pieces - a UDP listener, a polling loop, an MQTT connection - are
*not* adapters. They live in their own manager modules (``cot_gateway.py``,
``alerts.py``, ``mqtt.py``), get constructed in `api.py`'s ``lifespan()``
alongside ``cache``/``tiles``/``jobs``, and call into this package to do the
actual parsing.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable, Protocol


# --------------------------------------------------------------- contracts

# What an adapter's parse() promises to have produced. Callers dispatch on
# this rather than on the adapter's name, so a transport (the CoT gateway, an
# MQTT topic, a webhook) can accept several formats and route each result to
# the right manager without knowing which parser ran.
CONTRACT_POSITION = "position"
CONTRACT_OVERLAY = "overlay"
CONTRACT_ALERT = "alert"
CONTRACTS = {CONTRACT_POSITION, CONTRACT_OVERLAY, CONTRACT_ALERT}


class IngestError(ValueError):
    """A source payload could not be parsed into its declared contract.

    Deliberately a ``ValueError`` subclass, matching `FieldImportError`/
    `TelemetryError`/`OperationsError` elsewhere in the codebase: every one
    of these means "the caller sent something invalid", which routes to a
    4xx, never a 5xx.
    """


class Adapter(Protocol):
    """The shape every module in this package exposes.

    Structural (a `Protocol`, not a base class) because adapters are plain
    modules, not instances - `registry()` returns module objects and this is
    what documents the surface they have to provide.
    """

    #: Registry key, matching the module name (``"cot"``, ``"cap"``, ...).
    NAME: str
    #: One of CONTRACTS - what ``parse`` returns.
    CONTRACT: str
    #: Content types / filename extensions this adapter claims, used by the
    #: HTTP entry points to pick a parser when the caller doesn't name one.
    MEDIA_TYPES: tuple[str, ...]

    def parse(self, raw: bytes, **context: Any) -> list[dict[str, Any]]:
        """Bytes in, list of contract dicts out. Raises `IngestError`."""


# ---------------------------------------------------------------- registry

# Populated lazily by `registry()` rather than at import time. Importing every
# adapter eagerly would make this package's import cost proportional to the
# number of standards supported, on a code path (`api.py`'s import chain) that
# runs at startup on a field laptop.
_REGISTRY: dict[str, Any] = {}

_ADAPTER_MODULES = ("cot", "cap", "nmea", "mavlink", "shapefile", "webhook")


def registry() -> dict[str, Any]:
    """Every available adapter, keyed by ``NAME``.

    Import failures are swallowed per-adapter for the same reason
    `routes/common.py` swallows them for the optional analysis modules: one
    adapter that fails to import (a half-finished module during development,
    a syntax error introduced by a concurrent edit) should disable itself,
    not take the server's startup down with it.
    """
    if not _REGISTRY:
        import importlib

        for name in _ADAPTER_MODULES:
            try:
                _REGISTRY[name] = importlib.import_module(f".{name}", __package__)
            except Exception:  # noqa: BLE001 - see docstring
                continue
    return dict(_REGISTRY)


def adapter(name: str) -> Any:
    """One adapter by name, or `IngestError` naming what is available.

    The error lists the alternatives because the usual way to reach this is a
    typo in an operator-supplied mapping (an MQTT topic map, a webhook
    definition), where "unknown adapter 'nmea0183'" alone is a dead end.
    """
    found = registry().get(str(name or "").strip().lower())
    if found is None:
        raise IngestError(f"unknown ingest adapter {name!r}; available: {', '.join(sorted(registry()))}")
    return found


def parse(name: str, raw: bytes, **context: Any) -> list[dict[str, Any]]:
    """Dispatch to a named adapter. The one entry point transports should use."""
    return adapter(name).parse(raw, **context)


def adapter_for_media_type(media_type: str, *, contract: str | None = None) -> Any | None:
    """Pick an adapter from a Content-Type header, or ``None`` for no match.

    Only the type/subtype is compared - parameters like ``; charset=utf-8``
    are stripped, since senders vary on whether they include them and a
    header that differs only by charset is still the same format.
    """
    wanted = str(media_type or "").split(";")[0].strip().lower()
    if not wanted:
        return None
    for module in registry().values():
        if contract is not None and getattr(module, "CONTRACT", None) != contract:
            continue
        if wanted in getattr(module, "MEDIA_TYPES", ()):
            return module
    return None


# --------------------------------------------------- shared value coercion

# These exist so every adapter rejects the same malformed input the same way,
# rather than each hand-rolling its own float()/range checks and disagreeing at
# the edges. They are intentionally *stricter than necessary* in one respect:
# TelemetryManager.ingest re-validates all of this server-side anyway, so a
# bug here can only ever produce a clear rejection, never a bad row.


def clean_text(value: Any, limit: int) -> str:
    """Trim to ``limit`` characters, treating None/blank alike.

    Mirrors `field_import`/`telemetry`'s ``_text`` so a callsign truncated by
    an adapter matches one truncated by the manager receiving it.
    """
    return str(value if value is not None else "").strip()[:limit]


def coerce_float(value: Any, field: str, *, low: float | None = None,
                 high: float | None = None, allow_none: bool = True) -> float | None:
    """Parse a number, enforcing finiteness and an optional inclusive range.

    NaN and infinity are rejected explicitly rather than left to the range
    check: a bare ``low <= value <= high`` is False for NaN, which would
    produce a misleading "outside the valid range" message for what is really
    a non-numeric value.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_none:
            return None
        raise IngestError(f"{field} is required")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise IngestError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise IngestError(f"{field} must be finite")
    if low is not None and number < low:
        raise IngestError(f"{field} must be at least {low}")
    if high is not None and number > high:
        raise IngestError(f"{field} must be at most {high}")
    return number


def coerce_latlon(lat: Any, lon: Any) -> tuple[float, float]:
    """Latitude/longitude as a validated pair.

    Checked together because a swapped lat/lon is the single most common
    error in this class of data, and catching it needs both values at once -
    a longitude of 48.1 is perfectly valid on its own.
    """
    latitude = coerce_float(lat, "latitude", low=-90, high=90, allow_none=False)
    longitude = coerce_float(lon, "longitude", low=-180, high=180, allow_none=False)
    return float(latitude), float(longitude)


def iso_timestamp(value: Any) -> str:
    """Normalise a timestamp to the UTC ISO 8601 form the managers store.

    Accepts an ISO 8601 string (with ``Z`` or a numeric offset) or an epoch
    number. A naive string - no timezone at all - is **rejected**, matching
    `telemetry._time`'s reasoning: a local time from a device whose timezone
    we don't know is silently wrong rather than usefully approximate, and
    silently-wrong timestamps on an incident timeline are worse than a
    rejected batch.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise IngestError("observed_at is required")
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise IngestError("observed_at must be finite")
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise IngestError("observed_at must be an ISO 8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise IngestError("observed_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def position_report(*, external_id: str, callsign: str, observed_at: Any,
                    latitude: Any, longitude: Any, altitude_m: Any = None,
                    speed_kmh: Any = None, heading_deg: Any = None,
                    accuracy_m: Any = None, resource_id: str | None = None,
                    extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one CONTRACT_POSITION dict.

    The returned keys are exactly the ones `TelemetryManager.ingest` reads,
    which is the whole point: an adapter's output is handed to that method
    unchanged, so CoT, NMEA and OsmAnd positions all take the identical write
    path (token check, rate limit, per-``external_id`` replay hash, quality
    flags, resource position update) as the native JSON feed.

    ``extra`` is merged in rather than nested because `ingest` stores the
    whole dict verbatim as ``raw_json`` evidence - so protocol-specific
    fields a caller might later want (a CoT ``uid``, an NMEA fix quality)
    survive alongside the canonical columns instead of being discarded.

    ``external_id`` carries the replay-safety guarantee: it must be stable
    for a given report and distinct between reports, or a resend gets stored
    twice (distinct when it should repeat) or rejected outright (repeated
    when it should differ). Adapters derive it from the source's own
    identity plus its timestamp for exactly this reason.
    """
    latitude_value, longitude_value = coerce_latlon(latitude, longitude)
    identifier = clean_text(external_id, 200)
    call = clean_text(callsign, 200)
    if not identifier or not call:
        raise IngestError("external_id and callsign are required")
    report: dict[str, Any] = {
        "external_id": identifier,
        "callsign": call,
        "observed_at": iso_timestamp(observed_at),
        "latitude": latitude_value,
        "longitude": longitude_value,
        "altitude_m": coerce_float(altitude_m, "altitude_m"),
        "speed_kmh": coerce_float(speed_kmh, "speed_kmh", low=0),
        "heading_deg": coerce_float(heading_deg, "heading_deg", low=0, high=360),
        "accuracy_m": coerce_float(accuracy_m, "accuracy_m", low=0),
    }
    if resource_id:
        report["resource_id"] = clean_text(resource_id, 64)
    for key, value in (extra or {}).items():
        report.setdefault(key, value)
    return report


def overlay_feature(geometry: dict[str, Any], properties: dict[str, Any] | None = None
                    ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build one CONTRACT_OVERLAY pair.

    A ``(geometry, properties)`` tuple, identical to what `field_import`'s
    `_parse_geojson`/`_parse_gpx`/`_parse_kml` return - so an adapter's
    output can be handed straight to the existing prepare→acknowledge→apply
    pipeline and inherits its AOI review, per-feature validation and
    original-bytes provenance without any of that being re-implemented.

    Geometry is shape-checked but *not* coordinate-validated here;
    `_validate_geometry` in `operations/common.py` is the single authority on
    that, and duplicating it would mean two places to keep in step.
    """
    if not isinstance(geometry, dict) or geometry.get("type") not in {"Point", "LineString", "Polygon"}:
        raise IngestError("overlay geometry must be a Point, LineString or Polygon")
    if geometry.get("coordinates") in (None, []):
        raise IngestError("overlay geometry requires coordinates")
    return geometry, dict(properties or {})


def alert_record(*, identifier: str, sender: str, sent: Any, event: str,
                 severity: str, urgency: str, certainty: str,
                 geometry: dict[str, Any] | None, headline: str = "",
                 description: str = "", expires: Any = None,
                 effective: Any = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one CONTRACT_ALERT dict.

    ``identifier`` is the alert's own upstream id (CAP's ``<identifier>``),
    used as the storage key so re-polling a feed updates the existing row
    instead of accumulating a duplicate on every cycle - the same
    idempotency idea as ``external_id`` above, applied to a pull feed rather
    than a push one.

    ``expires`` is kept as a first-class field, not buried in ``extra``,
    because `AlertManager`'s prune pass reads it directly: an expired
    warning still drawn on a situation map is actively misleading.
    """
    key = clean_text(identifier, 300)
    if not key:
        raise IngestError("alert identifier is required")
    record: dict[str, Any] = {
        "identifier": key,
        "sender": clean_text(sender, 300),
        "sent": iso_timestamp(sent) if sent else None,
        "event": clean_text(event, 300),
        "headline": clean_text(headline, 500),
        "description": clean_text(description, 5000),
        "severity": clean_text(severity, 40) or "Unknown",
        "urgency": clean_text(urgency, 40) or "Unknown",
        "certainty": clean_text(certainty, 40) or "Unknown",
        "effective": iso_timestamp(effective) if effective else None,
        "expires": iso_timestamp(expires) if expires else None,
        "geometry": geometry,
    }
    for field_name, value in (extra or {}).items():
        record.setdefault(field_name, value)
    return record


#: One knot in km/h. The OsmAnd protocol reports speed in knots, matching its
#: NMEA heritage - see `osmand_report`.
KMH_PER_KNOT = 1.852


def osmand_report(parameters: dict[str, Any], *, default_callsign: str = "tracker") -> list[dict[str, Any]]:
    """One OsmAnd/Traccar query-parameter position as a position report.

    Lives here rather than in an adapter module because it is not a *format* -
    there are no bytes to parse, only an already-decoded query string - but it
    still has to produce the identical contract so it reaches
    `TelemetryManager.ingest` on the same path as everything else.

    Two field quirks are handled, both of which are silent if missed:

    * ``speed`` is in **knots**, not km/h or m/s. The protocol inherits it from
      NMEA. Storing the raw number understates every speed by a factor of
      1.852, which looks entirely plausible on a map.
    * ``timestamp`` may be Unix seconds *or* an ISO 8601 string depending on the
      client; `iso_timestamp` accepts both. When it is absent altogether -
      some minimal trackers omit it - the report is stamped at receipt, which
      is honest for a live push and recorded as such.
    """
    lowered = {str(key).lower(): value for key, value in parameters.items()}
    latitude = lowered.get("lat", lowered.get("latitude"))
    longitude = lowered.get("lon", lowered.get("longitude", lowered.get("lng")))
    if latitude is None or longitude is None:
        raise IngestError("lat and lon are required")

    raw_timestamp = lowered.get("timestamp") or lowered.get("time") or ""
    if str(raw_timestamp).strip().isdigit():
        raw_timestamp = float(raw_timestamp)
    timestamp_source = "reported"
    if not raw_timestamp:
        timestamp_source = "server_clock"
        raw_timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    speed_knots = coerce_float(lowered.get("speed"), "speed", low=0)
    callsign = clean_text(lowered.get("id") or lowered.get("deviceid") or lowered.get("callsign"), 200) \
        or clean_text(default_callsign, 200)

    observed_at = iso_timestamp(raw_timestamp)
    return [position_report(
        external_id=f"{callsign}@{observed_at}",
        callsign=callsign,
        observed_at=observed_at,
        latitude=latitude,
        longitude=longitude,
        altitude_m=lowered.get("altitude", lowered.get("alt")),
        speed_kmh=None if speed_knots is None else round(speed_knots * KMH_PER_KNOT, 3),
        heading_deg=lowered.get("bearing", lowered.get("heading", lowered.get("course"))),
        # OsmAnd reports HDOP, a unitless dilution-of-precision figure, not
        # metres. The same nominal 5 m UERE the NMEA adapter uses converts it,
        # so an accuracy figure means the same thing whichever protocol
        # delivered it.
        accuracy_m=(None if (hdop := coerce_float(lowered.get("hdop"), "hdop", low=0)) is None
                    else round(hdop * 5.0, 1)) if "hdop" in lowered
                   else lowered.get("accuracy"),
        extra={
            "protocol": "osmand",
            "battery_pct": lowered.get("batt"),
            "timestamp_source": timestamp_source,
        },
    )]


__all__ = [
    "CONTRACTS", "CONTRACT_ALERT", "CONTRACT_OVERLAY", "CONTRACT_POSITION", "KMH_PER_KNOT",
    "Adapter", "IngestError",
    "adapter", "adapter_for_media_type", "alert_record", "clean_text",
    "coerce_float", "coerce_latlon", "iso_timestamp", "osmand_report", "overlay_feature",
    "parse", "position_report", "registry",
]
