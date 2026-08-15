"""ESRI Shapefile - still what a GIS department actually hands you.

Hydrants, access routes, water supply points, cadastre, preplans: a Kreis or
county GIS office exports these as Shapefile, and will keep doing so long
after everything else has moved on. `field_import` already reads GeoJSON, GPX,
KML/KMZ, GeoPackage and CSV; this closes the one remaining gap that stops a
department's existing data going straight onto the map.

A "shapefile" is really three files that must travel together, which is why
this accepts a ZIP bundle:

  ``.shp``  geometry records
  ``.dbf``  the attribute table (dBase III), one row per shape, in order
  ``.prj``  the coordinate reference system, as WKT

All three are read here with `struct.unpack` in the same style as
`field_import._wkb_geometry`, for the same reason: the formats are narrow,
fully specified, and reading them directly avoids a GDAL/pyproj dependency
that would dwarf the code it replaces and add real install friction on
Windows.

**The CRS is the hard part, and it is handled by refusing rather than
guessing.** Unlike GeoJSON, a shapefile can be in any projection, and it is
usually *not* in WGS84 - German exports are typically UTM zone 32/33 or the
older Gauss-Kruger, US county data is typically a State Plane zone. Importing
those coordinates as though they were degrees would place features thousands
of kilometres away, so `_crs_from_prj` identifies only what it can transform
exactly (WGS84 geographic, Web Mercator, WGS84 UTM) and rejects everything
else with an error naming the CRS it found. A datum shift - Gauss-Kruger's
DHDN, NAD27 - is a genuinely different problem that cannot be approximated
without a grid file, so it is refused rather than silently mis-plotted by a
hundred metres.
"""

from __future__ import annotations

import io
import re
import struct
import zipfile
from pathlib import Path
from typing import Any

from . import CONTRACT_OVERLAY, IngestError, clean_text, overlay_feature
from ..geo import mercator_3857_to_lonlat, utm_to_lonlat

NAME = "shapefile"
CONTRACT = CONTRACT_OVERLAY
MEDIA_TYPES = ("application/x-esri-shape", "application/zip", "application/octet-stream")

#: Shapefile magic: 9994 as a big-endian int at byte 0.
FILE_CODE = 9994

#: Shape types this reads. The Z and M variants (11/13/15, 21/23/25) place
#: their extra arrays *after* the XY data, so the same XY parsing works for
#: all of them and the trailing arrays are simply not read.
NULL_SHAPE = 0
POINT_SHAPES = {1, 11, 21}
POLYLINE_SHAPES = {3, 13, 23}
POLYGON_SHAPES = {5, 15, 25}
MULTIPOINT_SHAPES = {8, 18, 28}

#: Caps matching `field_import`'s own 10,000-feature limit, applied here too
#: so a hostile or corrupt header cannot drive a huge allocation before the
#: per-record bounds checks would catch it.
MAX_RECORDS = 10_000
MAX_POINTS_PER_RECORD = 1_000_000
MAX_PARTS_PER_RECORD = 10_000

#: Uncompressed ceiling for a zipped bundle, matching `field_import`'s
#: MAX_SOURCE_BYTES so the two import paths bound memory identically.
MAX_BUNDLE_BYTES = 20 * 1024 * 1024


# --------------------------------------------------------------------- CRS

def _crs_from_prj(wkt: str) -> dict[str, Any]:
    """Identify the projection from a .prj well-known-text string.

    Deliberately a small matcher over the handful of CRSs that can be
    transformed exactly, not a WKT parser. Anything unrecognised raises with
    the projection's own name in the message, because "reproject this to
    WGS84 before importing" is only actionable if the operator can see what it
    is currently in.
    """
    text = " ".join(str(wkt or "").split())
    if not text:
        # No .prj at all. ESRI treats this as "unknown", and the overwhelmingly
        # common case for a file with no .prj is that it is already in degrees
        # - but assuming that for a UTM file would be a 5000 km error, so the
        # caller must say so explicitly (see `parse`'s `assume_wgs84`).
        raise IngestError("shapefile has no .prj; its coordinate system is unknown")

    upper = text.upper()
    name_match = re.search(r'^\s*(?:PROJCS|GEOGCS)\s*\[\s*"([^"]+)"', text, re.IGNORECASE)
    name = name_match.group(1) if name_match else "unknown"

    if upper.startswith("GEOGCS"):
        # Geographic (already lon/lat). Only WGS84 and the practically
        # identical ETRS89 are accepted; another datum needs a real shift.
        if "WGS_1984" in upper or "WGS 84" in upper or "D_WGS_1984" in upper:
            return {"kind": "wgs84", "name": name}
        if "ETRS_1989" in upper or "ETRS89" in upper or "EUROPEAN_TERRESTRIAL" in upper:
            # ETRS89 and WGS84 differ by well under a metre in Europe - far
            # inside the accuracy of anything being imported here - so this is
            # accepted as equivalent rather than refused. Stated, not silent.
            return {"kind": "wgs84", "name": name, "note": "ETRS89 treated as WGS84 (sub-metre difference)"}
        raise IngestError(f"shapefile uses geographic CRS {name!r} on an unsupported datum; "
                          "reproject to WGS84 before import")

    if "MERCATOR_AUXILIARY_SPHERE" in upper or "PSEUDO-MERCATOR" in upper or "WEB_MERCATOR" in upper:
        return {"kind": "webmercator", "name": name}

    if "TRANSVERSE_MERCATOR" in upper:
        if "WGS_1984" not in upper and "WGS 84" not in upper and "ETRS" not in upper:
            # Gauss-Kruger (DHDN), NAD27 State Plane and friends land here.
            # Transforming them needs a datum shift grid, which this cannot do.
            raise IngestError(f"shapefile uses projected CRS {name!r} on a non-WGS84 datum; "
                              "a datum shift is required - reproject to WGS84 before import")
        central = re.search(r'"CENTRAL_MERIDIAN"\s*,\s*(-?[\d.]+)', text, re.IGNORECASE)
        scale = re.search(r'"SCALE_FACTOR"\s*,\s*([\d.]+)', text, re.IGNORECASE)
        false_northing = re.search(r'"FALSE_NORTHING"\s*,\s*(-?[\d.]+)', text, re.IGNORECASE)
        if central is None or scale is None or abs(float(scale.group(1)) - 0.9996) > 1e-9:
            raise IngestError(f"shapefile uses transverse-Mercator CRS {name!r} that is not a UTM zone; "
                              "reproject to WGS84 before import")
        # UTM zone from the central meridian: zone 1 is centred on -177 and
        # each zone spans 6 degrees.
        zone = int(round((float(central.group(1)) + 183) / 6))
        if not 1 <= zone <= 60:
            raise IngestError(f"shapefile CRS {name!r} has a central meridian outside the UTM zones")
        # A non-zero false northing is the southern-hemisphere marker; getting
        # it wrong puts the data in the opposite hemisphere entirely.
        northern = not (false_northing and float(false_northing.group(1)) > 0)
        if "SOUTH" in upper.replace("_", " "):
            northern = False
        return {"kind": "utm", "zone": zone, "northern": northern, "name": name}

    raise IngestError(f"shapefile uses unsupported CRS {name!r}; reproject to WGS84, "
                      "Web Mercator or a WGS84 UTM zone before import")


def _projector(crs: dict[str, Any]):
    """A callable turning this CRS's (x, y) into (lon, lat)."""
    if crs["kind"] == "wgs84":
        return lambda x, y: (x, y)
    if crs["kind"] == "webmercator":
        return mercator_3857_to_lonlat
    return lambda x, y: utm_to_lonlat(x, y, crs["zone"], northern=crs["northern"])


# ---------------------------------------------------------------- geometry

def _points(data: bytes, offset: int, count: int, project) -> tuple[list[list[float]], int]:
    """Read ``count`` XY pairs and project each to lon/lat."""
    size = count * 16
    if offset + size > len(data):
        raise IngestError("shapefile record is truncated")
    coordinates = []
    for index in range(count):
        x, y = struct.unpack_from("<dd", data, offset + index * 16)
        coordinates.append(list(project(x, y)))
    return coordinates, offset + size


def _ring_is_clockwise(ring: list[list[float]]) -> bool:
    """Shoelace orientation test.

    The shapefile spec distinguishes an outer ring from a hole *by winding
    order alone*: outer rings are clockwise, holes counter-clockwise. There is
    no other marker, so this is the only way to reconstruct a polygon with
    holes - a lake inside a burn area, a courtyard inside a building.
    """
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        total += (x2 - x1) * (y2 + y1)
    return total > 0


def _parts(data: bytes, offset: int, project) -> tuple[list[list[list[float]]], int]:
    """Read a PolyLine/Polygon record's part-indexed point array."""
    if offset + 40 > len(data):
        raise IngestError("shapefile record is truncated")
    offset += 32  # skip the record's own bounding box
    part_count, point_count = struct.unpack_from("<ii", data, offset)
    offset += 8
    if not 0 < part_count <= MAX_PARTS_PER_RECORD or not 0 < point_count <= MAX_POINTS_PER_RECORD:
        raise IngestError("shapefile record declares an unsafe part/point count")
    if offset + part_count * 4 > len(data):
        raise IngestError("shapefile part index is truncated")
    starts = list(struct.unpack_from(f"<{part_count}i", data, offset))
    offset += part_count * 4
    coordinates, offset = _points(data, offset, point_count, project)
    starts.append(point_count)
    rings = [coordinates[starts[index]:starts[index + 1]] for index in range(part_count)]
    return [ring for ring in rings if ring], offset


def _shape(data: bytes, project) -> list[dict[str, Any]]:
    """One shape record as zero or more GeoJSON geometries.

    Returns a *list* because a multi-part polygon or a multipoint is several
    GeoJSON geometries, and `field_import` works one geometry per feature.
    """
    if len(data) < 4:
        raise IngestError("shapefile record is empty")
    shape_type = struct.unpack_from("<i", data, 0)[0]
    if shape_type == NULL_SHAPE:
        return []  # a legitimate placeholder row, not an error

    if shape_type in POINT_SHAPES:
        if len(data) < 20:
            raise IngestError("shapefile point record is truncated")
        x, y = struct.unpack_from("<dd", data, 4)
        return [{"type": "Point", "coordinates": list(project(x, y))}]

    if shape_type in MULTIPOINT_SHAPES:
        count = struct.unpack_from("<i", data, 36)[0]
        if not 0 < count <= MAX_POINTS_PER_RECORD:
            raise IngestError("shapefile multipoint declares an unsafe point count")
        coordinates, _ = _points(data, 40, count, project)
        return [{"type": "Point", "coordinates": point} for point in coordinates]

    if shape_type in POLYLINE_SHAPES:
        rings, _ = _parts(data, 4, project)
        return [{"type": "LineString", "coordinates": ring} for ring in rings if len(ring) >= 2]

    if shape_type in POLYGON_SHAPES:
        rings, _ = _parts(data, 4, project)
        polygons: list[dict[str, Any]] = []
        for ring in rings:
            if len(ring) < 4:
                continue
            if ring[0] != ring[-1]:
                ring.append(list(ring[0]))
            if _ring_is_clockwise(ring) or not polygons:
                # An outer ring starts a new polygon. The `or not polygons`
                # guard matters for a file whose very first ring is wound the
                # wrong way (common in hand-edited exports) - without it the
                # hole would have no polygon to belong to and be dropped.
                polygons.append({"type": "Polygon", "coordinates": [ring]})
            else:
                polygons[-1]["coordinates"].append(ring)
        return polygons

    raise IngestError(f"shapefile shape type {shape_type} is not supported "
                      "(Point, MultiPoint, PolyLine and Polygon are)")


def read_shp(data: bytes, project) -> list[list[dict[str, Any]]]:
    """Every record's geometries, in file order (so the .dbf rows line up)."""
    if len(data) < 100:
        raise IngestError("shapefile .shp is too short to contain a header")
    if struct.unpack_from(">i", data, 0)[0] != FILE_CODE:
        raise IngestError("file is not an ESRI shapefile (.shp magic number missing)")

    # The header's own declared length is authoritative but untrusted; clamp it
    # to the bytes actually present rather than reading past the buffer.
    declared_end = struct.unpack_from(">i", data, 24)[0] * 2
    end = min(declared_end, len(data)) if declared_end > 0 else len(data)

    records: list[list[dict[str, Any]]] = []
    offset = 100
    while offset + 8 <= end:
        _number, content_words = struct.unpack_from(">ii", data, offset)
        length = content_words * 2
        offset += 8
        if length <= 0 or offset + length > end:
            break
        records.append(_shape(data[offset:offset + length], project))
        offset += length
        if len(records) > MAX_RECORDS:
            raise IngestError(f"shapefile contains more than {MAX_RECORDS} records")
    if not records:
        raise IngestError("shapefile contains no shape records")
    return records


# --------------------------------------------------------------------- DBF

def read_dbf(data: bytes) -> list[dict[str, Any]]:
    """The attribute table as one dict per record, in file order.

    Encoding is the practical wrinkle: DBF predates Unicode and its language
    driver byte is unreliable in the wild, so this tries UTF-8 and falls back
    to cp1252. Getting that wrong turns German street names into mojibake in
    the imported feature titles, which is exactly the kind of defect that
    survives into an incident.
    """
    if len(data) < 32:
        raise IngestError("shapefile .dbf is too short to contain a header")
    record_count, header_length, record_length = struct.unpack_from("<IHH", data, 4)
    if record_count > MAX_RECORDS:
        raise IngestError(f"shapefile .dbf declares more than {MAX_RECORDS} records")
    if header_length < 33 or record_length < 1:
        raise IngestError("shapefile .dbf header is invalid")

    fields: list[tuple[str, str, int]] = []
    offset = 32
    while offset + 32 <= min(header_length, len(data)):
        if data[offset] == 0x0D:  # field-descriptor terminator
            break
        name = data[offset:offset + 11].split(b"\x00")[0].decode("ascii", "replace").strip()
        kind = chr(data[offset + 11])
        length = data[offset + 32 - 32 + 16]
        if name:
            fields.append((name, kind, length))
        offset += 32
    if not fields:
        raise IngestError("shapefile .dbf declares no fields")

    def decode(raw: bytes) -> str:
        for encoding in ("utf-8", "cp1252"):
            try:
                return raw.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1", "replace").strip()

    rows: list[dict[str, Any]] = []
    for index in range(record_count):
        start = header_length + index * record_length
        if start + record_length > len(data):
            break  # truncated file: keep the rows that are whole
        record = data[start:start + record_length]
        if record[:1] == b"*":
            # dBase marks a deleted row rather than removing it. The .shp
            # still holds its geometry, so this must be skipped by *both*
            # readers or the attribute rows stop lining up with the shapes -
            # hence the placeholder rather than a `continue`.
            rows.append({"_deleted": True})
            continue
        row: dict[str, Any] = {}
        cursor = 1
        for name, kind, length in fields:
            raw = record[cursor:cursor + length]
            cursor += length
            text = decode(raw)
            if kind in {"N", "F"} and text:
                try:
                    row[name] = float(text) if ("." in text or kind == "F") else int(text)
                except ValueError:
                    row[name] = text
            elif kind == "L":
                row[name] = text.upper() in {"T", "Y"} if text else None
            else:
                row[name] = text
        rows.append(row)
    return rows


# ------------------------------------------------------------------ bundle

def _bundle(raw: bytes) -> dict[str, bytes]:
    """Extract .shp/.dbf/.prj from a zipped shapefile.

    Reuses `field_import._kmz_text`'s guards - entry count, declared-size
    filter, bounded streaming read, and the Zip Slip path check - because this
    accepts exactly the same kind of untrusted archive upload.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir()]
            if len(entries) > 100 or sum(item.file_size for item in entries) > MAX_BUNDLE_BYTES:
                raise IngestError("shapefile archive exceeds safe import limits")
            if any(Path(item.filename).is_absolute() or ".." in Path(item.filename).parts
                   for item in entries):
                raise IngestError("shapefile archive contains an unsafe path")

            found: dict[str, bytes] = {}
            total = 0
            for item in entries:
                suffix = Path(item.filename).suffix.lower()
                if suffix not in {".shp", ".dbf", ".prj"} or suffix in found:
                    continue
                with archive.open(item) as member:
                    chunks, size = [], 0
                    while True:
                        chunk = member.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        total += len(chunk)
                        if total > MAX_BUNDLE_BYTES:
                            raise IngestError("shapefile archive expands beyond safe import limits")
                        chunks.append(chunk)
                found[suffix] = b"".join(chunks)
            return found
    except zipfile.BadZipFile as exc:
        raise IngestError("shapefile archive is not a valid ZIP") from exc


def parse(raw: bytes, *, assume_wgs84: bool = False, **_context: Any
          ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """A zipped shapefile as ``(geometry, properties)`` pairs.

    Output matches `field_import`'s other parsers exactly, so an imported
    shapefile goes through the same preview→acknowledge→apply pipeline, the
    same AOI review, and the same original-bytes provenance as every other
    source format.

    ``assume_wgs84`` is the deliberate escape hatch for a bundle with no
    .prj - it makes the operator state the assumption rather than having the
    importer guess, because guessing wrong on a UTM file is a 5000 km error.
    """
    files = _bundle(raw)
    if ".shp" not in files:
        raise IngestError("shapefile archive contains no .shp")

    prj = files.get(".prj", b"").decode("utf-8", "replace")
    if not prj.strip() and assume_wgs84:
        crs = {"kind": "wgs84", "name": "assumed WGS84 (no .prj supplied)"}
    else:
        crs = _crs_from_prj(prj)
    project = _projector(crs)

    geometries = read_shp(files[".shp"], project)
    attributes = read_dbf(files[".dbf"]) if ".dbf" in files else []

    results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, shapes in enumerate(geometries):
        row = attributes[index] if index < len(attributes) else {}
        if row.get("_deleted"):
            continue
        for geometry in shapes:
            properties = {key: value for key, value in row.items() if not key.startswith("_")}
            properties["source_crs"] = crs["name"]
            if crs.get("note"):
                properties["crs_note"] = crs["note"]
            # A GIS export names its label column anything at all, so offer the
            # usual candidates as `name`; field_import's own `mapping` remains
            # the way to point at a column this does not guess.
            for candidate in ("NAME", "Name", "name", "BEZEICHNUNG", "LABEL", "TITLE", "TEXT"):
                if clean_text(row.get(candidate), 300):
                    properties.setdefault("name", clean_text(row[candidate], 300))
                    break
            results.append(overlay_feature(geometry, properties))
    if not results:
        raise IngestError("shapefile contains no usable geometry")
    if len(results) > MAX_RECORDS:
        raise IngestError(f"shapefile expands to more than {MAX_RECORDS} features")
    return results


__all__ = ["CONTRACT", "FILE_CODE", "MAX_BUNDLE_BYTES", "MAX_RECORDS", "MEDIA_TYPES",
           "NAME", "parse", "read_dbf", "read_shp"]
