"""Cursor on Target (CoT) - the TAK ecosystem's wire format, both directions.

CoT is the closest thing emergency services have to a lingua franca for
"this thing is at this place at this time". ATAK/WinTAK/iTAK speak it
natively, which means supporting it connects NexFiremap to every team already
carrying a TAK device without those teams changing anything.

The format itself is small - one XML element carrying an identity, a type, a
validity window and a point::

    <event version="2.0" uid="ANDROID-9" type="a-f-G-U-C"
           time="..." start="..." stale="..." how="m-g">
      <point lat="48.1" lon="11.5" hae="520.0" ce="9.9" le="9999999.0"/>
      <detail>
        <contact callsign="FLORIAN 11/1"/>
        <track speed="12.5" course="270.0"/>
        <remarks>...</remarks>
      </detail>
    </event>

which maps almost 1:1 onto NexFiremap's existing models. ``a-`` types are
*atoms* - a unit, vehicle or person that exists somewhere - and become
position reports. ``b-`` types are *bits* - markers, waypoints and drawn
shapes - and become tactical features. `parse` returns the first kind (it is
the adapter registry's CONTRACT_POSITION entry point, used by the feed
endpoint); `overlays` returns the second; `parse_events` returns everything
unclassified, which is what the streaming gateway needs since a single TAK
connection carries both.

Two details in here are load-bearing rather than incidental:

*Units.* CoT speaks SI - ``speed`` is metres per second and ``hae`` is metres
above the WGS84 ellipsoid. NexFiremap stores km/h. Getting that conversion
wrong is silent: 12.5 stored as km/h instead of 45 is a plausible-looking
number, so it would never surface as an error, only as a wrong track.

*Sentinel values.* CoT has no null. Senders write ``9999999.0`` into ``ce``/
``le``/``hae`` to mean "unknown", and a device with no fix writes ``0,0`` into
lat/lon. Storing those literally would put every unlocated unit in the Gulf of
Guinea and claim 9999 km of GPS accuracy, so both are mapped back to "absent"
here - see `_optional_measure` and the null-island check in `_position`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from . import (
    CONTRACT_OVERLAY, CONTRACT_POSITION, IngestError, clean_text, coerce_float,
    iso_timestamp, overlay_feature, position_report,
)
from ..operations import AREA_TYPES, LINE_TYPES, POINT_TYPES
from ..symbology import cot_type, feature_type_for_cot

NAME = "cot"
CONTRACT = CONTRACT_POSITION
MEDIA_TYPES = ("application/xml", "text/xml", "application/cot+xml", "application/xml-cot")

#: CoT writes this into any float field it does not know. Not a magic number
#: of ours - it is what ATAK itself emits for an unknown error estimate.
UNKNOWN_MEASURE = 9999999.0

#: A generous ceiling on how many events one payload may carry, matching the
#: spirit of `field_import`'s 10,000-feature cap: a TAK server federating a
#: large exercise can legitimately send thousands, but not millions, and the
#: parse runs before any authentication decision on the UDP path.
MAX_EVENTS = 10_000

#: Matches an XML declaration anywhere in the payload - see `_root`, where a
#: back-to-back event stream carries one per event and only the first is legal.
_XML_DECLARATION = re.compile(r"<\?xml[^>]*\?>")


def _reject_doctype(text: str) -> None:
    """Block XXE before parsing.

    Identical guard to `field_import._parse_gpx`/`_parse_kml`: Python's stdlib
    XML parser will resolve external entities, so a crafted CoT event could
    otherwise read local files or make outbound requests. This matters more
    here than for an uploaded file, because on the gateway path the sender is
    a UDP peer rather than a logged-in operator.
    """
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise IngestError("XML document type/entity declarations are not allowed")


def _root(raw: bytes | str) -> ET.Element:
    """Parse a payload into an element, accepting a bare ``<event>`` or a
    wrapper containing several.

    TAK servers stream events back to back with no enclosing document
    element, which is not well-formed XML on its own. Wrapping such a payload
    in a synthetic root is what lets one read of a socket buffer holding three
    events parse as three events rather than failing outright.
    """
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    text = text.strip()
    if not text:
        raise IngestError("CoT payload is empty")
    _reject_doctype(text)
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        # Concatenated events each carry their own `<?xml ...?>` declaration,
        # which is only legal at the very start of a document - so wrapping
        # the payload as-is fails a second time with a different, more
        # confusing error. Strip every declaration before wrapping; they carry
        # no information we use (CoT is always UTF-8 XML 1.0).
        stripped = _XML_DECLARATION.sub("", text).strip()
        try:
            return ET.fromstring(f"<cot-multi>{stripped}</cot-multi>")
        except ET.ParseError as exc:
            raise IngestError(f"invalid CoT XML: {exc}") from exc


def _events(root: ET.Element) -> list[ET.Element]:
    """Every ``<event>`` in a parsed payload, however it was wrapped."""
    found = [root] if root.tag == "event" else root.findall(".//event")
    if not found:
        raise IngestError("CoT payload contains no <event> element")
    if len(found) > MAX_EVENTS:
        raise IngestError(f"CoT payload contains more than {MAX_EVENTS} events")
    return found


def _optional_measure(value: Any, field: str, *, low: float | None = None,
                      high: float | None = None) -> float | None:
    """A CoT float, with the "unknown" sentinel mapped back to ``None``.

    Anything at or above `UNKNOWN_MEASURE` is treated as absent rather than as
    a real reading - see the module docstring on why storing 9999999.0 as a
    genuine accuracy figure would be worse than storing nothing.
    """
    number = coerce_float(value, field, low=low, high=high)
    if number is None or abs(number) >= UNKNOWN_MEASURE:
        return None
    return number


def _time(value: Any, *, required: bool = True) -> str | None:
    """A CoT timestamp. CoT mandates ISO 8601 with an explicit zone, which is
    exactly what `iso_timestamp` requires, so this is mostly a nullability
    wrapper around it."""
    raw = clean_text(value, 80)
    if not raw:
        if required:
            raise IngestError("CoT event requires a time attribute")
        return None
    return iso_timestamp(raw)


def _detail_text(detail: ET.Element | None, path: str, attribute: str) -> str:
    """One attribute from a ``<detail>`` child, or "" when absent.

    ``detail`` is an open-ended extension point - senders put arbitrary
    sub-elements in it - so every read of it has to tolerate the element
    simply not being there.
    """
    if detail is None:
        return ""
    node = detail.find(path)
    return "" if node is None else clean_text(node.get(attribute), 300)


def _callsign(event: ET.Element, detail: ET.Element | None) -> str:
    """Best available human name for an event.

    Preference order matters operationally: ``contact/@callsign`` is what the
    operator typed into their device and what they will be called on the
    radio; the UID is a device identifier no one says out loud. Falling back to
    the UID keeps the report ingestable (callsign is required) rather than
    dropping a position because a sender omitted the contact block.
    """
    return (_detail_text(detail, "contact", "callsign")
            or _detail_text(detail, "__group", "name")
            or clean_text(event.get("uid"), 200))


def _point(event: ET.Element) -> ET.Element:
    child = event.find("point")
    if child is None:
        raise IngestError("CoT event has no <point> element")
    return child


def _position(event: ET.Element) -> dict[str, Any] | None:
    """One CoT atom as a position report, or ``None`` if it has no real fix.

    The null-island guard is not pedantry: a TAK device that has not acquired
    GPS reports exactly ``lat="0.0" lon="0.0"``, and those events are common in
    a real feed. Ingesting them would draw the whole fleet off the coast of
    Africa and, worse, would feed `TelemetryManager`'s derived-speed
    calculation garbage, flagging genuine positions either side of it as
    implausible jumps.
    """
    point = _point(event)
    latitude = coerce_float(point.get("lat"), "latitude", low=-90, high=90, allow_none=False)
    longitude = coerce_float(point.get("lon"), "longitude", low=-180, high=180, allow_none=False)
    if latitude == 0.0 and longitude == 0.0:
        return None

    detail = event.find("detail")
    uid = clean_text(event.get("uid"), 200)
    if not uid:
        raise IngestError("CoT event requires a uid attribute")
    observed_at = _time(event.get("time") or event.get("start"))

    speed_ms = _optional_measure(_detail_text(detail, "track", "speed") or None, "speed", low=0)
    course = _optional_measure(_detail_text(detail, "track", "course") or None, "course", low=0, high=360)

    return position_report(
        # uid alone is not unique over time - a device sends a fresh event for
        # the same uid every few seconds - so the replay key has to include the
        # timestamp. This makes a genuine resend of one report hash identical
        # (correctly counted as replayed) while two distinct fixes stay distinct.
        external_id=f"{uid}@{observed_at}",
        callsign=_callsign(event, detail),
        observed_at=observed_at,
        latitude=latitude,
        longitude=longitude,
        altitude_m=_optional_measure(point.get("hae"), "altitude_m"),
        speed_kmh=None if speed_ms is None else round(speed_ms * 3.6, 3),
        heading_deg=course,
        accuracy_m=_optional_measure(point.get("ce"), "accuracy_m", low=0),
        extra={
            "cot_uid": uid,
            "cot_type": clean_text(event.get("type"), 80),
            "cot_how": clean_text(event.get("how"), 40),
            "cot_stale": _time(event.get("stale"), required=False),
            "cot_remarks": _detail_text(detail, "remarks", "text") or clean_text(
                (detail.findtext("remarks") if detail is not None else "") or "", 1000),
        },
    )


def _shape_geometry(detail: ET.Element | None, point: ET.Element) -> dict[str, Any]:
    """Geometry for a drawing event.

    TAK draws user shapes as ``<shape><polyline><vertex lat lon/></polyline>``,
    with ``closed="true"`` marking a polygon rather than a line. A marker with
    no shape block is just its own point. The ring-closing step matters because
    TAK omits the repeated final vertex that GeoJSON requires - handing an
    unclosed ring to `_validate_geometry` downstream would be rejected.
    """
    polyline = None if detail is None else detail.find(".//polyline")
    if polyline is None:
        latitude = coerce_float(point.get("lat"), "latitude", low=-90, high=90, allow_none=False)
        longitude = coerce_float(point.get("lon"), "longitude", low=-180, high=180, allow_none=False)
        return {"type": "Point", "coordinates": [longitude, latitude]}

    vertices: list[list[float]] = []
    for vertex in polyline.findall("vertex"):
        latitude = coerce_float(vertex.get("lat"), "latitude", low=-90, high=90, allow_none=False)
        longitude = coerce_float(vertex.get("lon"), "longitude", low=-180, high=180, allow_none=False)
        vertices.append([float(longitude), float(latitude)])
    if len(vertices) < 2:
        raise IngestError("CoT polyline requires at least two vertices")

    if str(polyline.get("closed", "")).lower() in {"true", "1"}:
        if len(vertices) < 3:
            raise IngestError("CoT closed polyline requires at least three vertices")
        if vertices[0] != vertices[-1]:
            vertices.append(list(vertices[0]))
        return {"type": "Polygon", "coordinates": [vertices]}
    return {"type": "LineString", "coordinates": vertices}


def _overlay(event: ET.Element) -> tuple[dict[str, Any], dict[str, Any]]:
    """One CoT bit as a tactical-feature ``(geometry, properties)`` pair.

    ``cot_uid`` is carried through into properties deliberately: it is what
    lets the gateway *update* the feature a repeated event refers to instead of
    creating a duplicate every time the sender refreshes it (see
    `cot_gateway`'s upsert).
    """
    detail = event.find("detail")
    uid = clean_text(event.get("uid"), 200)
    if not uid:
        raise IngestError("CoT event requires a uid attribute")
    raw_type = clean_text(event.get("type"), 80)
    geometry = _shape_geometry(detail, _point(event))
    # The geometry the sender actually drew outranks the type table's answer.
    # CoT has far fewer type codes than we have feature types, so one code
    # legitimately covers several - "b-m-p-w" is *the* generic waypoint and is
    # claimed by a dozen point types. When someone draws a polygon and tags it
    # with that code, resolving by code alone yields a point feature type whose
    # geometry `_validate_geometry` would then reject downstream. Checking the
    # resolved type against the shape actually drawn, and falling back when
    # they disagree, is what keeps a drawn area importable as an area.
    default = {"Point": "spot_fire", "LineString": "fire_perimeter", "Polygon": "burn_area"}[geometry["type"]]
    expected = {"Point": POINT_TYPES, "LineString": LINE_TYPES, "Polygon": AREA_TYPES}[geometry["type"]]
    # An event we produced states its own feature type outright, so a round
    # trip through a TAK device returns the exact type it left as instead of
    # whatever the lossy code table would infer back.
    declared = _detail_text(detail, EXTENSION_TAG, "feature_type")
    feature_type = declared or feature_type_for_cot(raw_type, default=default)
    if feature_type not in expected:
        feature_type = default

    properties = {
        "title": (_detail_text(detail, "contact", "callsign")
                  or clean_text(detail.findtext("remarks") if detail is not None else "", 300)
                  or uid),
        "feature_type": feature_type,
        "observed_at": _time(event.get("time") or event.get("start"), required=False),
        "source": "cot",
        "cot_uid": uid,
        "cot_type": raw_type,
        "cot_stale": _time(event.get("stale"), required=False),
    }
    return overlay_feature(geometry, properties)


#: Our own extension element inside `<detail>`. CoT deliberately leaves
#: `detail` open for vendor extensions, and other implementations ignore
#: elements they do not recognise, so adding this is interoperable rather than
#: a private dialect.
EXTENSION_TAG = "__nexfiremap"


def is_atom(event: ET.Element) -> bool:
    """Whether an event should be read as a position report rather than a
    tactical feature.

    Normally this is just CoT's own distinction: ``a-`` types are *atoms*
    (a unit that exists somewhere), ``b-`` types are *bits* (a marker or a
    drawing). But several NexFiremap feature types are genuinely atoms in CoT
    terms - a command post really is ``a-f-G-U-C`` - so exporting one and
    reading it back would reclassify a tactical feature as a vehicle position,
    losing what it was.

    The `EXTENSION_TAG` check fixes that asymmetry for our own output without
    misreading anyone else's: an event we produced says so explicitly and is
    read back as the feature it started as, while a genuine ATAK unit marker
    carrying the same type code is still correctly treated as a position.
    """
    detail = event.find("detail")
    if detail is not None and detail.find(EXTENSION_TAG) is not None:
        return False
    return clean_text(event.get("type"), 80).startswith("a-")


def parse_events(raw: bytes | str) -> list[dict[str, Any]]:
    """Every event in a payload, each tagged with which contract it satisfies.

    Used by the streaming gateway, where one connection carries both kinds and
    the routing decision has to be made per event. Returns dicts of
    ``{"contract", "event", "payload"}``; ``payload`` is a position report for
    atoms and a ``(geometry, properties)`` pair for bits.

    An atom with no usable fix (see `_position`) is skipped rather than
    reported as an error - those are normal traffic in any real feed, and
    failing the whole batch because one device has not locked GPS yet would
    discard the other devices' good positions with it.
    """
    results: list[dict[str, Any]] = []
    for event in _events(_root(raw)):
        if is_atom(event):
            report = _position(event)
            if report is not None:
                results.append({"contract": CONTRACT_POSITION, "event": event, "payload": report})
        else:
            results.append({"contract": CONTRACT_OVERLAY, "event": event, "payload": _overlay(event)})
    return results


def parse(raw: bytes, **_context: Any) -> list[dict[str, Any]]:
    """CONTRACT_POSITION entry point: position reports only.

    This is what the registry exposes and what the HTTP feed endpoint calls, so
    a CoT payload posted to `/api/feeds/positions/{source_id}` takes the exact
    same `TelemetryManager.ingest` path - token check, rate limit, replay hash,
    quality flags - as a native JSON batch.
    """
    reports = [item["payload"] for item in parse_events(raw) if item["contract"] == CONTRACT_POSITION]
    if not reports:
        raise IngestError("CoT payload contains no position atoms with a valid fix")
    return reports


def overlays(raw: bytes, **_context: Any) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """CONTRACT_OVERLAY entry point: markers and drawn shapes only."""
    return [item["payload"] for item in parse_events(raw) if item["contract"] == CONTRACT_OVERLAY]


# ------------------------------------------------------------- serialisation

#: How long a rendered event claims to remain valid. CoT consumers grey out or
#: drop a marker past its ``stale`` time, so this is effectively "how long a
#: TAK device should keep showing this if it stops hearing from us". Five
#: minutes matches `Settings.position_stale_seconds`' default reasoning: long
#: enough to survive a missed poll, short enough that a dead link visibly ages.
DEFAULT_STALE_SECONDS = 300


def _iso(value: Any, fallback: datetime) -> str:
    """A timestamp in CoT's required form, tolerating junk.

    Rendering must not fail on one bad stored timestamp - a single feature with
    a malformed ``observed_at`` should not take down an entire incident's CoT
    export - so an unparseable value falls back rather than raising.
    """
    raw = clean_text(value, 80)
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        except ValueError:
            pass
    return fallback.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _representative_point(geometry: dict[str, Any]) -> tuple[float, float]:
    """A single lat/lon for a geometry of any shape.

    Every CoT event needs a ``<point>``, including one describing a polygon, so
    a line or area has to nominate one. This uses the centroid of the vertices
    (not a true area centroid - the difference is irrelevant at incident scale
    and a real centroid would need a polygon algorithm here for no benefit).
    """
    def walk(value: Any) -> list[tuple[float, float]]:
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(v, (int, float)) for v in value[:2]):
            return [(float(value[0]), float(value[1]))]
        if not isinstance(value, list):
            return []
        return [pair for child in value for pair in walk(child)]

    points = walk(geometry.get("coordinates", []))
    if not points:
        raise IngestError("cannot render a CoT point for an empty geometry")
    return (sum(p[1] for p in points) / len(points), sum(p[0] for p in points) / len(points))


def render_feature(feature: dict[str, Any], *, stale_seconds: int = DEFAULT_STALE_SECONDS,
                   now: datetime | None = None) -> ET.Element:
    """One tactical feature as a CoT ``<event>`` element.

    A drawn line or area is rendered with both a representative ``<point>``
    (which CoT requires) *and* a ``<shape><polyline>`` carrying the real
    vertices, so a TAK client draws the actual perimeter rather than a single
    marker sitting near the middle of it.
    """
    moment = now or datetime.now(timezone.utc)
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    feature_type = str(properties.get("feature_type") or "spot_fire")
    latitude, longitude = _representative_point(geometry)
    # Reuse the incoming uid when this feature originally came from CoT, so a
    # round trip updates the sender's own marker instead of spawning a second
    # copy of it on their screen.
    uid = clean_text(properties.get("cot_uid"), 200) or f"nexfiremap-{properties.get('id', '')}"
    observed = _iso(properties.get("observed_at") or properties.get("updated_at"), moment)

    event = ET.Element("event", {
        "version": "2.0", "uid": uid, "type": cot_type(feature_type), "how": "m-g",
        "time": observed, "start": observed,
        "stale": _iso(None, moment.fromtimestamp(moment.timestamp() + stale_seconds, tz=timezone.utc)),
    })
    ET.SubElement(event, "point", {
        "lat": f"{latitude:.7f}", "lon": f"{longitude:.7f}", "hae": "9999999.0",
        "ce": "9999999.0", "le": "9999999.0",
    })
    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact", {"callsign": clean_text(properties.get("title"), 200) or feature_type})
    # Carry the exact feature type and status, so reading our own export back
    # is lossless (see `is_atom`). Other CoT consumers ignore unknown detail
    # children, so this costs nothing on the wire for them.
    ET.SubElement(detail, EXTENSION_TAG, {
        "feature_type": feature_type,
        "status": clean_text(properties.get("status"), 40) or "observed",
    })
    remarks = ET.SubElement(detail, "remarks")
    remarks.text = f"{feature_type} ({properties.get('status', 'observed')}) via NexFiremap"

    if geometry.get("type") in {"LineString", "Polygon"}:
        rings = geometry["coordinates"] if geometry["type"] == "LineString" else geometry["coordinates"][0]
        shape = ET.SubElement(detail, "shape")
        polyline = ET.SubElement(shape, "polyline", {
            "closed": "true" if geometry["type"] == "Polygon" else "false"})
        for vertex in rings:
            ET.SubElement(polyline, "vertex", {"lat": f"{float(vertex[1]):.7f}", "lon": f"{float(vertex[0]):.7f}"})
    return event


def render_position(feature: dict[str, Any], *, stale_seconds: int = DEFAULT_STALE_SECONDS,
                    now: datetime | None = None) -> ET.Element:
    """One vehicle position (a feature from `TelemetryManager.latest`) as a
    friendly-ground-vehicle atom, with speed converted back to m/s."""
    moment = now or datetime.now(timezone.utc)
    properties = feature.get("properties") or {}
    coordinates = (feature.get("geometry") or {}).get("coordinates") or [0.0, 0.0]
    callsign = clean_text(properties.get("callsign"), 200) or "unit"
    observed = _iso(properties.get("observed_at"), moment)

    event = ET.Element("event", {
        "version": "2.0",
        "uid": clean_text(properties.get("cot_uid"), 200) or f"nexfiremap-unit-{callsign}",
        "type": cot_type("resource_position"), "how": "m-g",
        "time": observed, "start": observed,
        "stale": _iso(None, moment.fromtimestamp(moment.timestamp() + stale_seconds, tz=timezone.utc)),
    })
    accuracy = properties.get("accuracy_m")
    altitude = properties.get("altitude_m")
    ET.SubElement(event, "point", {
        "lat": f"{float(coordinates[1]):.7f}", "lon": f"{float(coordinates[0]):.7f}",
        "hae": "9999999.0" if altitude is None else f"{float(altitude):.1f}",
        "ce": "9999999.0" if accuracy is None else f"{float(accuracy):.1f}",
        "le": "9999999.0",
    })
    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact", {"callsign": callsign})
    speed, heading = properties.get("speed_kmh"), properties.get("heading_deg")
    if speed is not None or heading is not None:
        ET.SubElement(detail, "track", {
            "speed": "0.0" if speed is None else f"{float(speed) / 3.6:.3f}",
            "course": "0.0" if heading is None else f"{float(heading):.1f}",
        })
    if properties.get("stale"):
        # Surface the server's own freshness verdict rather than leaving the
        # receiving client to infer it - the operator reading the TAK screen
        # should see the same "this is old" judgement the NexFiremap map shows.
        ET.SubElement(detail, "remarks").text = "position is stale"
    return event


def serialize(events: list[ET.Element]) -> bytes:
    """Wrap rendered events into one document.

    A single ``<events>`` root rather than concatenated bare elements, because
    this is served over HTTP where the response has to be well-formed XML on
    its own; the streaming gateway writes elements individually instead.
    """
    root = ET.Element("events")
    for event in events:
        root.append(event)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


__all__ = [
    "CONTRACT", "DEFAULT_STALE_SECONDS", "MEDIA_TYPES", "NAME", "UNKNOWN_MEASURE",
    "is_atom", "overlays", "parse", "parse_events", "render_feature",
    "render_position", "serialize",
]
