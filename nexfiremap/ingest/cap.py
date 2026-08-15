"""Common Alerting Protocol (CAP 1.2) - official public warnings as map data.

Virtually every national warning system publishes CAP: Germany's MoWaS/NINA
and the DWD's weather warnings, the US IPAWS and NWS, and their equivalents
elsewhere. Parsing it puts the official warning polygons - severe weather,
evacuation orders, civil-protection alerts - onto the same map as the fire
detections and the tactical picture, which is where an incident commander
needs them.

The format is an OASIS XML standard: one ``<alert>`` carrying one or more
``<info>`` blocks (typically one per language), each with zero or more
``<area>`` blocks describing where it applies. Feeds are usually served as
Atom or RSS with the alerts either inline or linked, so `parse` accepts a bare
alert, a feed containing several, and the ``<link>``-only case is surfaced via
`feed_links` for the poller to follow.

Three details are worth knowing before reading the code:

*Coordinate order is reversed.* CAP writes ``lat,lon`` pairs; GeoJSON is
``lon,lat``. Every polygon in every feed would be silently transposed - and
land somewhere in the ocean off Somalia for European alerts - if this were
missed. It is the single most likely bug in a CAP implementation.

*Language duplication.* One alert usually carries the same warning in several
languages. Emitting one map feature per ``<info>`` would draw identical
polygons stacked three deep. `parse` picks one preferred ``<info>`` per alert.

*Circles need synthesising.* CAP's ``<circle>`` is ``lat,lon radius_km``, which
has no GeoJSON equivalent, so it is approximated as a polygon here rather than
carried as an unrenderable primitive.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import Any

from . import CONTRACT_ALERT, IngestError, alert_record, clean_text

NAME = "cap"
CONTRACT = CONTRACT_ALERT
MEDIA_TYPES = ("application/cap+xml", "application/common-alerting-protocol+xml")

#: CAP's own vocabularies. Kept as ordered tuples because severity is ranked -
#: the frontend colours by it and `AlertManager` can filter on it - and an
#: ordering has to come from somewhere; these are the orders CAP itself
#: defines, most severe first.
SEVERITIES = ("Extreme", "Severe", "Moderate", "Minor", "Unknown")
URGENCIES = ("Immediate", "Expected", "Future", "Past", "Unknown")
CERTAINTIES = ("Observed", "Likely", "Possible", "Unlikely", "Unknown")

#: Statuses that describe a real, live warning. Everything else - a system
#: test, an exercise, a draft - must never be drawn as if it were real, which
#: is the entire reason CAP carries the field.
LIVE_STATUSES = {"actual"}

#: How many vertices approximate a `<circle>`. 64 keeps a 10 km radius within
#: about 12 m of true - far inside the positional accuracy of the warning
#: itself - without bloating a payload that may carry hundreds of areas.
CIRCLE_SEGMENTS = 64

#: Ceiling on one payload, mirroring `field_import`'s 10,000-feature cap. A
#: national feed during severe weather is large but bounded.
MAX_ALERTS = 5_000

#: Preferred `<info>` languages, best first. Both target audiences are
#: represented; anything else falls back to the first block present, so a feed
#: in a third language still renders rather than being dropped.
PREFERRED_LANGUAGES = ("de", "en")


def _reject_doctype(text: str) -> None:
    """Block XXE. CAP feeds are fetched from third-party government hosts over
    the internet, so this parses genuinely untrusted input - the same guard as
    `field_import._parse_kml` and `ingest/cot.py`."""
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise IngestError("XML document type/entity declarations are not allowed")


def _root(raw: bytes | str) -> ET.Element:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    if not text.strip():
        raise IngestError("CAP payload is empty")
    _reject_doctype(text)
    try:
        return ET.fromstring(text.strip())
    except ET.ParseError as exc:
        raise IngestError(f"invalid CAP XML: {exc}") from exc


def _pairs(text: str | None, *, label: str) -> list[list[float]]:
    """Parse CAP's whitespace-separated ``lat,lon`` list into GeoJSON order.

    The swap on the way out (``[longitude, latitude]``) is the important line
    in this module: CAP is lat-first, GeoJSON is lon-first, and getting it
    backwards produces polygons that are perfectly well-formed and in
    completely the wrong place.
    """
    coordinates: list[list[float]] = []
    for token in str(text or "").split():
        parts = token.split(",")
        if len(parts) < 2:
            raise IngestError(f"CAP {label} needs latitude,longitude pairs")
        try:
            latitude, longitude = float(parts[0]), float(parts[1])
        except ValueError as exc:
            raise IngestError(f"CAP {label} contains non-numeric coordinates") from exc
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise IngestError(f"CAP {label} coordinate is outside the valid range")
        coordinates.append([longitude, latitude])
    return coordinates


def _polygon(text: str | None) -> dict[str, Any]:
    """One ``<polygon>`` as a GeoJSON Polygon.

    CAP requires the ring to be explicitly closed, but real feeds are not
    always compliant, so the ring is closed here rather than rejected - a
    warning polygon that renders is worth more than a strictly-validated
    rejection during severe weather.
    """
    ring = _pairs(text, label="polygon")
    if len(ring) < 3:
        raise IngestError("CAP polygon needs at least three points")
    if ring[0] != ring[-1]:
        ring.append(list(ring[0]))
    return {"type": "Polygon", "coordinates": [ring]}


def _circle(text: str | None) -> dict[str, Any]:
    """One ``<circle>`` (``lat,lon radius_km``) approximated as a polygon.

    The longitude step is divided by ``cos(latitude)`` because a degree of
    longitude narrows toward the poles; without it the circle would render as
    an ellipse squashed in the wrong axis, increasingly so at higher latitudes
    - which is to say, over exactly the countries this targets.
    """
    parts = str(text or "").split()
    if len(parts) != 2:
        raise IngestError("CAP circle must be 'latitude,longitude radius'")
    centre = _pairs(parts[0], label="circle")
    try:
        radius_km = float(parts[1])
    except ValueError as exc:
        raise IngestError("CAP circle radius must be numeric") from exc
    if len(centre) != 1 or radius_km < 0:
        raise IngestError("CAP circle needs one centre point and a non-negative radius")

    longitude, latitude = centre[0]
    if radius_km == 0:
        return {"type": "Point", "coordinates": [longitude, latitude]}
    lat_step = radius_km / 110.574
    lon_step = radius_km / (111.320 * max(0.01, math.cos(math.radians(latitude))))
    ring = [[longitude + lon_step * math.sin(2 * math.pi * i / CIRCLE_SEGMENTS),
             latitude + lat_step * math.cos(2 * math.pi * i / CIRCLE_SEGMENTS)]
            for i in range(CIRCLE_SEGMENTS)]
    ring.append(list(ring[0]))
    return {"type": "Polygon", "coordinates": [ring]}


def _geometry(info: ET.Element) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Combined geometry for every ``<area>`` in one ``<info>``, plus geocodes.

    A single alert routinely covers several disjoint districts, so the result
    is a MultiPolygon when more than one area is present rather than only the
    first - dropping the rest would under-report the warning's extent, which
    for an evacuation order is a safety-relevant error.

    Geocodes (Germany's ``WARNCELLID``, the US ``SAME``/``FIPS`` codes) are
    returned separately rather than resolved: turning a cell id into a boundary
    needs a lookup table this project does not ship. They are preserved so a
    geometry-less alert is still recorded and searchable, and so an operator
    can see *why* it has no polygon.
    """
    polygons: list[list[list[list[float]]]] = []
    geocodes: list[dict[str, str]] = []
    for area in info.findall("{*}area"):
        for polygon in area.findall("{*}polygon"):
            polygons.append(_polygon(polygon.text)["coordinates"])
        for circle in area.findall("{*}circle"):
            shape = _circle(circle.text)
            if shape["type"] == "Polygon":
                polygons.append(shape["coordinates"])
        for geocode in area.findall("{*}geocode"):
            name = clean_text(geocode.findtext("{*}valueName"), 80)
            value = clean_text(geocode.findtext("{*}value"), 120)
            if name and value:
                geocodes.append({"name": name, "value": value})
    if not polygons:
        return None, geocodes
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}, geocodes
    return {"type": "MultiPolygon", "coordinates": polygons}, geocodes


def _preferred_info(alert: ET.Element) -> ET.Element | None:
    """Pick one ``<info>`` block from an alert.

    One CAP alert typically repeats the same warning in several languages, and
    emitting one feature each would stack identical polygons on the map. The
    first block in `PREFERRED_LANGUAGES` wins; failing that, the first block
    present, so a feed in an unanticipated language still renders.
    """
    blocks = alert.findall("{*}info")
    if not blocks:
        return None
    for wanted in PREFERRED_LANGUAGES:
        for block in blocks:
            if clean_text(block.findtext("{*}language"), 20).lower().startswith(wanted):
                return block
    return blocks[0]


def _one(alert: ET.Element) -> dict[str, Any] | None:
    """One ``<alert>`` as a CONTRACT_ALERT record, or ``None`` to skip it.

    Skipped rather than raised for the two cases that are normal traffic in a
    real feed: a non-``Actual`` status (an exercise, a system test, a draft),
    and a ``Cancel``/``Ack`` message that references a prior alert rather than
    carrying one. Raising on either would make one routine message poison an
    otherwise good poll of a national feed.
    """
    status = clean_text(alert.findtext("{*}status"), 40)
    if status and status.lower() not in LIVE_STATUSES:
        return None
    message_type = clean_text(alert.findtext("{*}msgType"), 40)
    identifier = clean_text(alert.findtext("{*}identifier"), 300)
    if not identifier:
        raise IngestError("CAP alert has no <identifier>")

    info = _preferred_info(alert)
    if info is None:
        # Cancel/Ack messages legitimately carry no <info>; anything else
        # without one has nothing to draw or describe.
        if message_type.lower() in {"cancel", "ack", "error"}:
            return None
        raise IngestError("CAP alert has no <info> block")

    geometry, geocodes = _geometry(info)
    severity = clean_text(info.findtext("{*}severity"), 40) or "Unknown"
    return alert_record(
        identifier=identifier,
        sender=clean_text(alert.findtext("{*}sender"), 300),
        sent=clean_text(alert.findtext("{*}sent"), 80) or None,
        event=clean_text(info.findtext("{*}event"), 300),
        severity=severity,
        urgency=clean_text(info.findtext("{*}urgency"), 40) or "Unknown",
        certainty=clean_text(info.findtext("{*}certainty"), 40) or "Unknown",
        geometry=geometry,
        headline=clean_text(info.findtext("{*}headline"), 500),
        description=clean_text(info.findtext("{*}description"), 5000),
        effective=clean_text(info.findtext("{*}effective"), 80) or None,
        # CAP calls it <expires>; some producers only set <onset>. No expiry
        # means "until cancelled", which AlertManager's prune treats as
        # never-expiring rather than as already-expired.
        expires=clean_text(info.findtext("{*}expires"), 80) or None,
        extra={
            "status": status or "Actual",
            "msg_type": message_type or "Alert",
            "scope": clean_text(alert.findtext("{*}scope"), 40),
            "category": clean_text(info.findtext("{*}category"), 80),
            "response_type": clean_text(info.findtext("{*}responseType"), 80),
            "instruction": clean_text(info.findtext("{*}instruction"), 5000),
            "web": clean_text(info.findtext("{*}web"), 500),
            "contact": clean_text(info.findtext("{*}contact"), 300),
            "language": clean_text(info.findtext("{*}language"), 20),
            "geocodes": geocodes,
            "severity_rank": SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES),
        },
    )


def parse(raw: bytes, **_context: Any) -> list[dict[str, Any]]:
    """Every live alert in a CAP document or feed.

    Accepts a bare ``<alert>`` as well as an Atom/RSS feed with alerts inline,
    because publishers differ and a poller should not have to know which shape
    a given endpoint returns.
    """
    root = _root(raw)
    alerts = [root] if root.tag.endswith("alert") else root.findall(".//{*}alert")
    if not alerts:
        raise IngestError("CAP payload contains no <alert> element")
    if len(alerts) > MAX_ALERTS:
        raise IngestError(f"CAP payload contains more than {MAX_ALERTS} alerts")
    return [record for alert in alerts if (record := _one(alert)) is not None]


def feed_links(raw: bytes) -> list[str]:
    """CAP document URLs referenced by an Atom/RSS index feed.

    Many publishers - the DWD among them - serve an index of links rather than
    inline alerts, so the poller has to follow them. Returned separately from
    `parse` so the network fetch stays out of this stateless module.
    """
    try:
        root = _root(raw)
    except IngestError:
        return []
    links: list[str] = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        href = node.get("href") if tag == "link" else (node.text if tag in {"link", "guid"} else None)
        value = clean_text(href, 2000)
        if value.startswith(("http://", "https://")) and value not in links:
            links.append(value)
    return links


__all__ = ["CERTAINTIES", "CIRCLE_SEGMENTS", "CONTRACT", "LIVE_STATUSES", "MEDIA_TYPES",
           "NAME", "SEVERITIES", "URGENCIES", "feed_links", "parse"]
