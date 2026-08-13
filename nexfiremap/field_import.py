"""Previewed, provenance-preserving field observation imports."""

from __future__ import annotations

import csv
import base64
import hashlib
import io
import json
import sqlite3
import struct
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .db import Database
from .operations import (
    FEATURE_STATUSES, FEATURE_TYPES, OperationsError, OperationsStore, POINT_TYPES,
    LINE_TYPES, AREA_TYPES, _clean_text, _id, _validate_geometry, utcnow,
)


MAX_SOURCE_BYTES = 20 * 1024 * 1024
SUPPORTED_FORMATS = {"geojson", "gpx", "kml", "kmz", "gpkg", "csv"}


class FieldImportError(OperationsError):
    pass


def _positions(geometry: dict[str, Any]) -> Iterable[list[float]]:
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Point":
        yield coordinates
    elif geometry.get("type") == "LineString":
        yield from coordinates
    elif geometry.get("type") == "Polygon":
        for ring in coordinates:
            yield from ring


def _default_type(geometry_type: str) -> str:
    return {"Point": "spot_fire", "LineString": "fire_perimeter", "Polygon": "burn_area"}[geometry_type]


def _parse_geojson(content: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise FieldImportError(f"invalid GeoJSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise FieldImportError("GeoJSON root must be an object")
    if value.get("crs") not in (None, {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}):
        raise FieldImportError("GeoJSON CRS is ambiguous; reproject to WGS84/CRS84 before import")
    if value.get("type") == "Feature":
        features = [value]
    elif value.get("type") == "FeatureCollection" and isinstance(value.get("features"), list):
        features = value["features"]
    else:
        raise FieldImportError("GeoJSON must be a Feature or FeatureCollection")
    result = []
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            raise FieldImportError("every GeoJSON feature requires a geometry")
        props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        result.append((feature["geometry"], props))
    return result


def _parse_gpx(content: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper():
        raise FieldImportError("XML document type/entity declarations are not allowed")
    try: root = ET.fromstring(content)
    except ET.ParseError as exc: raise FieldImportError(f"invalid GPX: {exc}") from exc
    result = []
    for waypoint in root.findall(".//{*}wpt"):
        try: coordinates = [float(waypoint.attrib["lon"]), float(waypoint.attrib["lat"])]
        except (KeyError, ValueError) as exc: raise FieldImportError("GPX waypoint has invalid coordinates") from exc
        props = {child.tag.split("}")[-1]: child.text or "" for child in waypoint}
        result.append(({"type": "Point", "coordinates": coordinates}, props))
    for segment in root.findall(".//{*}trkseg"):
        coords = []
        for point in segment.findall("{*}trkpt"):
            try: coords.append([float(point.attrib["lon"]), float(point.attrib["lat"])])
            except (KeyError, ValueError) as exc: raise FieldImportError("GPX track point has invalid coordinates") from exc
        if coords:
            track = segment.find("../{*}name")
            result.append(({"type": "LineString", "coordinates": coords}, {"name": track.text if track is not None else "GPS track"}))
    if not result: raise FieldImportError("GPX contains no waypoints or track segments")
    return result


def _kml_coordinates(text: str | None) -> list[list[float]]:
    result = []
    for token in str(text or "").split():
        try:
            values = [float(value) for value in token.split(",")]
        except ValueError as exc:
            raise FieldImportError("KML contains invalid coordinates") from exc
        if len(values) < 2: raise FieldImportError("KML coordinates require longitude and latitude")
        result.append(values[:3])
    return result


def _parse_kml(content: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper():
        raise FieldImportError("XML document type/entity declarations are not allowed")
    try: root = ET.fromstring(content)
    except ET.ParseError as exc: raise FieldImportError(f"invalid KML: {exc}") from exc
    result = []
    for placemark in root.findall(".//{*}Placemark"):
        name = placemark.find("{*}name")
        props = {"name": name.text if name is not None else ""}
        point = placemark.find(".//{*}Point/{*}coordinates")
        line = placemark.find(".//{*}LineString/{*}coordinates")
        polygon = placemark.find(".//{*}Polygon/{*}outerBoundaryIs/{*}LinearRing/{*}coordinates")
        if point is not None:
            coords = _kml_coordinates(point.text)
            if len(coords) != 1: raise FieldImportError("KML Point requires one coordinate")
            result.append(({"type": "Point", "coordinates": coords[0]}, props))
        elif line is not None:
            result.append(({"type": "LineString", "coordinates": _kml_coordinates(line.text)}, props))
        elif polygon is not None:
            result.append(({"type": "Polygon", "coordinates": [_kml_coordinates(polygon.text)]}, props))
    if not result: raise FieldImportError("KML contains no supported placemark geometry")
    return result


def _parse_csv(content: str, mapping: dict[str, str]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    try: rows = list(csv.DictReader(io.StringIO(content)))
    except csv.Error as exc: raise FieldImportError(f"invalid CSV: {exc}") from exc
    if not rows: raise FieldImportError("CSV contains no data rows")
    lat_key, lon_key = mapping.get("latitude", "latitude"), mapping.get("longitude", "longitude")
    result = []
    for index, row in enumerate(rows, start=2):
        try: coordinates = [float(row[lon_key]), float(row[lat_key])]
        except (KeyError, TypeError, ValueError) as exc:
            raise FieldImportError(f"CSV row {index} requires numeric {lat_key}/{lon_key}") from exc
        result.append(({"type": "Point", "coordinates": coordinates}, dict(row)))
    return result


def _wkb_geometry(data: bytes) -> dict[str, Any]:
    def read(offset: int = 0) -> tuple[dict[str, Any], int]:
        if offset + 5 > len(data): raise FieldImportError("GeoPackage geometry is truncated")
        endian = "<" if data[offset] == 1 else ">" if data[offset] == 0 else None
        if endian is None: raise FieldImportError("GeoPackage WKB byte order is invalid")
        raw_type = struct.unpack_from(endian + "I", data, offset + 1)[0]; offset += 5
        has_z = bool(raw_type & 0x80000000) or 1000 <= raw_type % 10000 < 2000
        base = raw_type & 0xFF if raw_type & 0xE0000000 else raw_type % 1000
        dimensions = 3 if has_z else 2
        def point() -> list[float]:
            nonlocal offset
            size = dimensions * 8
            if offset + size > len(data): raise FieldImportError("GeoPackage coordinate is truncated")
            values = struct.unpack_from(endian + "d" * dimensions, data, offset); offset += size
            return list(values)
        if base == 1:
            return {"type": "Point", "coordinates": point()}, offset
        if offset + 4 > len(data): raise FieldImportError("GeoPackage geometry count is truncated")
        count = struct.unpack_from(endian + "I", data, offset)[0]; offset += 4
        if count > 1_000_000: raise FieldImportError("GeoPackage geometry has an unsafe coordinate count")
        if base == 2:
            return {"type": "LineString", "coordinates": [point() for _ in range(count)]}, offset
        if base == 3:
            rings = []
            for _ in range(count):
                if offset + 4 > len(data): raise FieldImportError("GeoPackage polygon ring is truncated")
                points = struct.unpack_from(endian + "I", data, offset)[0]; offset += 4
                rings.append([point() for _ in range(points)])
            return {"type": "Polygon", "coordinates": rings}, offset
        raise FieldImportError("GeoPackage supports Point, LineString and Polygon features only")
    return read()[0]


def _gpkg_geometry(blob: bytes) -> dict[str, Any]:
    if len(blob) < 8 or blob[:2] != b"GP": raise FieldImportError("GeoPackage geometry header is invalid")
    flags = blob[3]; envelope_code = (flags >> 1) & 0x07
    envelope_values = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}.get(envelope_code)
    if envelope_values is None: raise FieldImportError("GeoPackage geometry envelope is invalid")
    return _wkb_geometry(blob[8 + envelope_values * 8:])


def _parse_gpkg(raw: bytes) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "source.gpkg"; path.write_bytes(raw)
        try: conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        except sqlite3.DatabaseError as exc: raise FieldImportError("GeoPackage is not a readable SQLite database") from exc
        conn.row_factory = sqlite3.Row
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise FieldImportError("GeoPackage integrity check failed")
            tables = conn.execute(
                "SELECT c.table_name,g.column_name,g.srs_id FROM gpkg_contents c JOIN gpkg_geometry_columns g ON g.table_name=c.table_name WHERE c.data_type='features'"
            ).fetchall()
            if not tables: raise FieldImportError("GeoPackage contains no vector feature table")
            result = []
            for definition in tables:
                if int(definition["srs_id"]) != 4326:
                    raise FieldImportError("GeoPackage vector layers must use EPSG:4326 before field import")
                table, geometry_column = str(definition["table_name"]), str(definition["column_name"])
                quoted = '"' + table.replace('"', '""') + '"'
                for row in conn.execute(f"SELECT * FROM {quoted}"):
                    blob = row[geometry_column]
                    if blob is None: continue
                    props = {key: value for key, value in dict(row).items() if key != geometry_column and not isinstance(value, bytes)}
                    result.append((_gpkg_geometry(bytes(blob)), props))
                    if len(result) > 10_000: raise FieldImportError("source contains more than 10,000 features")
            if not result: raise FieldImportError("GeoPackage vector layers contain no geometries")
            return result
        except sqlite3.DatabaseError as exc:
            raise FieldImportError(f"invalid GeoPackage schema: {exc}") from exc
        finally: conn.close()


class FieldImportManager:
    def __init__(self, db: Database, store: OperationsStore) -> None:
        self.db, self.store = db, store

    @staticmethod
    def _format(filename: str, requested: str) -> str:
        value = requested.lower().strip() if requested else Path(filename).suffix.lower().lstrip(".")
        if value == "json": value = "geojson"
        if value not in SUPPORTED_FORMATS:
            raise FieldImportError("supported field import formats are GeoJSON, GPX, KML, KMZ, GeoPackage and CSV")
        return value

    @staticmethod
    def _raw(request: dict[str, Any]) -> bytes:
        encoded = request.get("content_base64")
        if encoded:
            try: return base64.b64decode(str(encoded), validate=True)
            except ValueError as exc: raise FieldImportError("source base64 content is invalid") from exc
        content = request.get("content")
        if not isinstance(content, str):
            raise FieldImportError("text content or content_base64 is required")
        return content.encode("utf-8")

    @staticmethod
    def _kmz_text(raw: bytes) -> str:
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                files = [item for item in archive.infolist() if not item.is_dir()]
                # ZipInfo.file_size is the central directory's own declared
                # uncompressed size - a claim the archive makes about itself,
                # not something zipfile verifies against what actually comes
                # out on read(). A crafted entry can under-report it while a
                # highly repetitive payload still decompresses to gigabytes
                # (deflate's ratio ceiling is roughly 1000:1), so this sum is
                # only a fast first filter, not the real guard - the actual
                # guard is the bounded streaming read below, which never
                # trusts a declared size and stops the moment real output
                # crosses the limit.
                if len(files) > 100 or sum(item.file_size for item in files) > MAX_SOURCE_BYTES:
                    raise FieldImportError("KMZ expanded content exceeds safe import limits")
                if any(Path(item.filename).is_absolute() or ".." in Path(item.filename).parts for item in files):
                    raise FieldImportError("KMZ contains an unsafe path")
                candidates = [item for item in files if item.filename.lower().endswith(".kml")]
                if len(candidates) != 1:
                    raise FieldImportError("KMZ must contain exactly one KML document")
                chunks: list[bytes] = []
                total = 0
                with archive.open(candidates[0]) as member:
                    while True:
                        chunk = member.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_SOURCE_BYTES:
                            raise FieldImportError("KMZ expanded content exceeds safe import limits")
                        chunks.append(chunk)
                return b"".join(chunks).decode("utf-8")
        except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
            raise FieldImportError("KMZ is not a valid UTF-8 KML archive") from exc

    def prepare(self, incident_id: str, request: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        incident = self.store.get_incident(incident_id)
        filename = _clean_text(request.get("filename"), 300)
        if not filename:
            raise FieldImportError("filename is required")
        raw = self._raw(request)
        if not raw or len(raw) > MAX_SOURCE_BYTES:
            raise FieldImportError(f"source file must be between 1 byte and {MAX_SOURCE_BYTES} bytes")
        mapping = request.get("mapping") if isinstance(request.get("mapping"), dict) else {}
        fmt = self._format(filename, str(request.get("format") or ""))
        try: content = self._kmz_text(raw) if fmt == "kmz" else "" if fmt == "gpkg" else raw.decode("utf-8")
        except UnicodeDecodeError as exc: raise FieldImportError("source text must be UTF-8") from exc
        parsed = {"geojson": _parse_geojson, "gpx": _parse_gpx, "kml": _parse_kml}.get(fmt)
        items = _parse_csv(content, mapping) if fmt == "csv" else _parse_kml(content) if fmt == "kmz" else _parse_gpkg(raw) if fmt == "gpkg" else parsed(content)
        if len(items) > 10_000: raise FieldImportError("source contains more than 10,000 features")
        bbox = request.get("aoi_bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise FieldImportError("aoi_bbox [west,south,east,north] is required for review")
        try: west, south, east, north = map(float, bbox)
        except (TypeError, ValueError) as exc: raise FieldImportError("aoi_bbox must be numeric") from exc
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise FieldImportError("aoi_bbox is invalid")

        prepared = []
        outside = confirmed = 0
        default_type = str(request.get("default_feature_type") or "").strip()
        for index, (geometry, props) in enumerate(items, start=1):
            geometry_type = geometry.get("type")
            if geometry_type not in {"Point", "LineString", "Polygon"}:
                raise FieldImportError(f"feature {index} has unsupported geometry {geometry_type}")
            mapped_type = props.get(mapping.get("feature_type", "feature_type")) if mapping.get("feature_type") else None
            feature_type = str(mapped_type or default_type or _default_type(geometry_type)).strip()
            if feature_type not in FEATURE_TYPES:
                raise FieldImportError(f"feature {index} has invalid feature_type {feature_type}")
            geometry = _validate_geometry(geometry, feature_type)
            if (geometry_type == "Point" and feature_type not in POINT_TYPES) or (geometry_type == "LineString" and feature_type not in LINE_TYPES) or (geometry_type == "Polygon" and feature_type not in AREA_TYPES):
                raise FieldImportError(f"feature {index} type does not match its geometry")
            if any(not (west <= float(p[0]) <= east and south <= float(p[1]) <= north) for p in _positions(geometry)):
                outside += 1
            def field(key: str, fallback: str = "") -> Any:
                column = mapping.get(key, key)
                return props.get(column, fallback)
            status = str(field("status", "unconfirmed" if feature_type == "confirmed_perimeter" else "observed") or "observed")
            if status not in FEATURE_STATUSES: raise FieldImportError(f"feature {index} has invalid status {status}")
            if feature_type == "confirmed_perimeter" or status == "confirmed": confirmed += 1
            prepared.append({
                "feature_type": feature_type, "title": _clean_text(field("title", field("name", "")), 300),
                "status": status, "geometry": geometry,
                "observed_at": _clean_text(field("observed_at", ""), 80) or utcnow(),
                "source": _clean_text(field("source", request.get("source") or filename), 200),
                "observer": _clean_text(field("observer", request.get("observer") or ""), 200),
                "confidence": _clean_text(field("confidence", ""), 50) or None,
                "properties": {"import_properties": props,
                    "horizontal_accuracy_m": field("horizontal_accuracy_m", ""),
                    "fire_activity": field("fire_activity", ""), "intensity": field("intensity", ""),
                    "wind_speed": field("wind_speed", ""), "wind_direction": field("wind_direction", "")},
            })
        report = {
            "valid": True, "incident_id": incident["id"], "incident_name": incident["name"],
            "filename": filename, "format": fmt, "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw), "feature_count": len(prepared), "outside_aoi": outside,
            "requires_aoi_acknowledgement": outside > 0, "requires_confirmation": confirmed > 0,
            "geometry_counts": {kind: sum(1 for item in prepared if item["geometry"]["type"] == kind)
                                for kind in ("Point", "LineString", "Polygon")},
            "preview": [{"feature_type": item["feature_type"], "title": item["title"],
                         "status": item["status"], "geometry": item["geometry"]}
                        for item in prepared[:100]],
        }
        return report, prepared

    def apply(self, incident_id: str, request: dict[str, Any], actor: str) -> dict[str, Any]:
        report, prepared = self.prepare(incident_id, request)
        if report["requires_aoi_acknowledgement"] and not request.get("acknowledge_outside_aoi"):
            raise FieldImportError("features outside the reviewed AOI require explicit acknowledgement")
        if report["requires_confirmation"] and not _clean_text(request.get("confirmation_reason"), 1000):
            raise FieldImportError("confirmed observations require a named confirmation reason")
        import_id, now = _id(), utcnow()
        raw = self._raw(request)
        period_id = request.get("period_id") or None
        if period_id and not self.db.conn.execute(
            "SELECT 1 FROM operational_periods WHERE id=? AND incident_id=?", (period_id, incident_id)
        ).fetchone(): raise FieldImportError("operational period does not belong to incident")
        with self.db._write_lock:
            try:
                self.db.conn.execute("BEGIN IMMEDIATE")
                self.db.conn.execute(
                    "INSERT INTO incident_source_imports (id,incident_id,filename,format,sha256,size_bytes,source,imported_by,imported_at,feature_count,report_json,original_blob) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (import_id, incident_id, report["filename"], report["format"], report["sha256"],
                     report["size_bytes"], _clean_text(request.get("source"), 500), _clean_text(actor, 200),
                     now, len(prepared), json.dumps(report, separators=(",", ":")), raw),
                )
                ids = []
                for item in prepared:
                    feature_id = _id(); ids.append(feature_id)
                    props = {**item["properties"], "source_import_id": import_id}
                    self.db.conn.execute(
                        "INSERT INTO tactical_features (id,incident_id,period_id,scenario_id,feature_type,title,status,geometry_json,properties_json,observed_at,source,observer,confidence,valid_from,valid_to,created_by,created_at,updated_at,revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (feature_id, incident_id, period_id, None, item["feature_type"], item["title"], item["status"],
                         json.dumps(item["geometry"], separators=(",", ":")), json.dumps(props, separators=(",", ":")),
                         item["observed_at"], item["source"], item["observer"], item["confidence"], None, None,
                         _clean_text(actor, 200) or "local operator", now, now, 1),
                    )
                    payload = {**item, "id": feature_id, "incident_id": incident_id, "period_id": period_id,
                               "revision": 1, "source_import_id": import_id}
                    self.store._audit(incident_id, "feature", feature_id, "import", 1, payload, actor)
                self.store._audit(incident_id, "source_import", import_id, "apply", 1,
                                  {**report, "feature_ids": ids,
                                   "confirmation_reason": _clean_text(request.get("confirmation_reason"), 1000)}, actor)
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback(); raise
        return {"imported": True, "import_id": import_id, "feature_ids": ids, "report": report}

    def list_imports(self, incident_id: str) -> list[dict[str, Any]]:
        self.store.get_incident(incident_id)
        return [dict(row) for row in self.db.conn.execute(
            "SELECT id,incident_id,filename,format,sha256,size_bytes,source,imported_by,imported_at,feature_count,report_json FROM incident_source_imports WHERE incident_id=? ORDER BY imported_at DESC",
            (incident_id,),
        ).fetchall()]

    def original(self, incident_id: str, import_id: str) -> tuple[str, bytes]:
        row = self.db.conn.execute(
            "SELECT filename,original_blob FROM incident_source_imports WHERE id=? AND incident_id=?",
            (import_id, incident_id),
        ).fetchone()
        if row is None: raise FieldImportError("source import not found")
        return row["filename"], bytes(row["original_blob"])
