"""ESRI Shapefile import: geometry, attributes, and CRS handling.

Fixtures are built byte-by-byte here rather than checked in as binaries, so
what every field means is visible in the test rather than opaque. The CRS
cases matter most: a shapefile is usually *not* in WGS84, and importing UTM
metres as degrees is a several-thousand-kilometre error that passes every
range check on the way through.
"""

from __future__ import annotations

import io
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.field_import import FieldImportError, FieldImportManager
from nexfiremap.ingest import IngestError
from nexfiremap.ingest import shapefile as shp
from nexfiremap.operations import OperationsStore, default_period

WGS84_PRJ = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
             'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')

UTM32N_PRJ = ('PROJCS["WGS_1984_UTM_Zone_32N",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
              'SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
              'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
              'PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",0.0],'
              'PARAMETER["Central_Meridian",9.0],PARAMETER["Scale_Factor",0.9996],'
              'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]')

# Gauss-Kruger on the DHDN datum - extremely common in older German exports,
# and NOT transformable without a datum-shift grid.
GAUSS_KRUEGER_PRJ = ('PROJCS["DHDN_3_Degree_Gauss_Zone_4",GEOGCS["GCS_Deutsches_Hauptdreiecksnetz",'
                     'DATUM["D_Deutsches_Hauptdreiecksnetz",SPHEROID["Bessel_1841",6377397.155,299.1528128]],'
                     'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
                     'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",4500000.0],'
                     'PARAMETER["Central_Meridian",12.0],PARAMETER["Scale_Factor",1.0],UNIT["Meter",1.0]]')

WEB_MERCATOR_PRJ = ('PROJCS["WGS_1984_Web_Mercator_Auxiliary_Sphere",GEOGCS["GCS_WGS_1984",'
                    'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]]],'
                    'PROJECTION["Mercator_Auxiliary_Sphere"],UNIT["Meter",1.0]]')


def _shp(shape_type: int, records: list[bytes]) -> bytes:
    """Assemble a .shp: 100-byte header, then length-prefixed records."""
    body = b""
    for index, content in enumerate(records, start=1):
        body += struct.pack(">ii", index, len(content) // 2) + content
    header = struct.pack(">i", shp.FILE_CODE) + b"\x00" * 20
    header += struct.pack(">i", (100 + len(body)) // 2)
    header += struct.pack("<ii", 1000, shape_type)
    header += struct.pack("<8d", 0, 0, 0, 0, 0, 0, 0, 0)
    return header + body


def _point_record(x: float, y: float) -> bytes:
    return struct.pack("<i", 1) + struct.pack("<dd", x, y)


def _poly_record(shape_type: int, rings: list[list[tuple[float, float]]]) -> bytes:
    points = [point for ring in rings for point in ring]
    starts, cursor = [], 0
    for ring in rings:
        starts.append(cursor)
        cursor += len(ring)
    return (struct.pack("<i", shape_type)
            + struct.pack("<4d", 0, 0, 0, 0)
            + struct.pack("<ii", len(rings), len(points))
            + struct.pack(f"<{len(starts)}i", *starts)
            + b"".join(struct.pack("<dd", x, y) for x, y in points))


def _dbf(fields: list[tuple[str, str, int]], rows: list[list[str]], *, deleted: set[int] = frozenset()) -> bytes:
    """Assemble a dBase III table matching the .shp record order."""
    record_length = 1 + sum(length for _, _, length in fields)
    header_length = 32 + 32 * len(fields) + 1
    out = bytearray(struct.pack("<BBBBIHH", 3, 26, 8, 15, len(rows), header_length, record_length))
    out += b"\x00" * 20
    for name, kind, length in fields:
        descriptor = bytearray(32)
        encoded = name.encode("ascii")[:10]
        descriptor[0:len(encoded)] = encoded
        descriptor[11] = ord(kind)
        descriptor[16] = length
        out += descriptor
    out += b"\x0d"
    for index, row in enumerate(rows):
        out += b"*" if index in deleted else b" "
        for (_, _, length), value in zip(fields, row):
            out += value.encode("cp1252")[:length].ljust(length, b" ")
    return bytes(out)


def _bundle(shp_bytes: bytes, dbf_bytes: bytes | None = None, prj: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("layer.shp", shp_bytes)
        if dbf_bytes is not None:
            archive.writestr("layer.dbf", dbf_bytes)
        if prj is not None:
            archive.writestr("layer.prj", prj)
    return buffer.getvalue()


def check_points_wgs84() -> None:
    bundle = _bundle(
        _shp(1, [_point_record(11.5755, 48.1372), _point_record(11.58, 48.14)]),
        _dbf([("NAME", "C", 20), ("KAPAZITAET", "N", 8)], [["Hydrant Nord", "1600"], ["Hydrant Sued", "800"]]),
        WGS84_PRJ)
    features = shp.parse(bundle)
    assert len(features) == 2
    geometry, properties = features[0]
    assert geometry == {"type": "Point", "coordinates": [11.5755, 48.1372]}
    assert properties["NAME"] == "Hydrant Nord"
    # Numeric DBF fields must come back as numbers, not strings, or every
    # downstream comparison is a string comparison.
    assert properties["KAPAZITAET"] == 1600 and isinstance(properties["KAPAZITAET"], int)
    assert properties["name"] == "Hydrant Nord", "a label column should be offered as `name`"
    assert properties["source_crs"] == "GCS_WGS_1984"


def check_utm_reprojection() -> None:
    """The case that matters: a UTM export must be reprojected, not read as
    degrees. 692220E/5334541N in zone 32N is central Munich."""
    bundle = _bundle(_shp(1, [_point_record(692220.0, 5334541.0)]), None, UTM32N_PRJ)
    (geometry, properties), = shp.parse(bundle)
    longitude, latitude = geometry["coordinates"]
    assert 11.5 < longitude < 11.6, longitude
    assert 48.1 < latitude < 48.2, latitude
    assert properties["source_crs"] == "WGS_1984_UTM_Zone_32N"


def check_web_mercator_reprojection() -> None:
    bundle = _bundle(_shp(1, [_point_record(1288578.77, 6129710.45)]), None, WEB_MERCATOR_PRJ)
    (geometry, _), = shp.parse(bundle)
    longitude, latitude = geometry["coordinates"]
    assert abs(longitude - 11.5755) < 1e-4 and abs(latitude - 48.1372) < 1e-4, geometry


def check_unsupported_crs_refused() -> None:
    """A CRS needing a datum shift must be refused by name, not approximated.
    Importing Gauss-Kruger as if it were WGS84 UTM is a ~100 m error that
    nobody would notice until it mattered."""
    bundle = _bundle(_shp(1, [_point_record(4500000.0, 5334541.0)]), None, GAUSS_KRUEGER_PRJ)
    try:
        shp.parse(bundle)
        raise AssertionError("a DHDN/Gauss-Kruger shapefile was imported")
    except IngestError as exc:
        assert "DHDN" in str(exc) and "datum shift" in str(exc), str(exc)

    # No .prj at all: unknown, and the operator has to say so explicitly.
    plain = _bundle(_shp(1, [_point_record(11.5755, 48.1372)]))
    try:
        shp.parse(plain)
        raise AssertionError("a shapefile with no .prj was imported without acknowledgement")
    except IngestError as exc:
        assert ".prj" in str(exc)
    (geometry, _), = shp.parse(plain, assume_wgs84=True)
    assert geometry["coordinates"] == [11.5755, 48.1372]


def check_polygon_rings_and_holes() -> None:
    """Shapefile distinguishes an outer ring from a hole by winding order
    alone - clockwise is outer, counter-clockwise a hole. Without that test a
    lake inside a burn area becomes a second, overlapping polygon."""
    outer = [(11.50, 48.10), (11.50, 48.20), (11.70, 48.20), (11.70, 48.10), (11.50, 48.10)]
    hole = [(11.55, 48.13), (11.60, 48.13), (11.60, 48.16), (11.55, 48.16), (11.55, 48.13)]
    assert shp._ring_is_clockwise(list(map(list, outer))), "fixture outer ring must be clockwise"
    assert not shp._ring_is_clockwise(list(map(list, hole))), "fixture hole must be counter-clockwise"

    bundle = _bundle(_shp(5, [_poly_record(5, [outer, hole])]), None, WGS84_PRJ)
    (geometry, _), = shp.parse(bundle)
    assert geometry["type"] == "Polygon"
    assert len(geometry["coordinates"]) == 2, "the hole must attach to its outer ring"

    # Two separate outer rings are two polygons, not one with a hole.
    second = [(12.00, 48.10), (12.00, 48.20), (12.20, 48.20), (12.20, 48.10), (12.00, 48.10)]
    bundle = _bundle(_shp(5, [_poly_record(5, [outer, second])]), None, WGS84_PRJ)
    features = shp.parse(bundle)
    assert len(features) == 2 and all(len(g["coordinates"]) == 1 for g, _ in features)


def check_polyline() -> None:
    line = [(11.50, 48.10), (11.55, 48.12), (11.60, 48.15)]
    bundle = _bundle(_shp(3, [_poly_record(3, [line])]), None, WGS84_PRJ)
    (geometry, _), = shp.parse(bundle)
    assert geometry["type"] == "LineString" and len(geometry["coordinates"]) == 3


def check_dbf_alignment() -> None:
    """A deleted dBase row still has its geometry in the .shp. If only one of
    the two readers skips it, every subsequent shape gets the wrong
    attributes - a silent, total corruption of the import."""
    bundle = _bundle(
        _shp(1, [_point_record(11.50, 48.10), _point_record(11.55, 48.11), _point_record(11.60, 48.12)]),
        _dbf([("NAME", "C", 10)], [["first"], ["deleted"], ["third"]], deleted={1}),
        WGS84_PRJ)
    features = shp.parse(bundle)
    assert len(features) == 2, "the deleted row's shape must be skipped too"
    assert [properties["NAME"] for _, properties in features] == ["first", "third"]
    assert features[1][0]["coordinates"] == [11.60, 48.12], "attributes must stay aligned to shapes"


def check_encoding() -> None:
    """DBF predates Unicode; a cp1252 export must not turn German street names
    into mojibake in the imported feature titles."""
    bundle = _bundle(
        _shp(1, [_point_record(11.50, 48.10)]),
        _dbf([("NAME", "C", 20)], [["Grünwalder Straße"]]),
        WGS84_PRJ)
    (_, properties), = shp.parse(bundle)
    assert properties["NAME"] == "Grünwalder Straße", properties["NAME"]


def check_rejections() -> None:
    for label, payload in (
        ("not a zip", b"not a zip at all"),
        ("no .shp", _bundle(b"", None, WGS84_PRJ).replace(b"layer.shp", b"layer.txt")),
        ("bad magic", _bundle(b"\x00" * 200, None, WGS84_PRJ)),
        ("too short", _bundle(b"\x00" * 12, None, WGS84_PRJ)),
    ):
        try:
            shp.parse(payload)
            raise AssertionError(f"{label} was accepted")
        except IngestError:
            pass

    # A hostile part/point count must be refused before it drives an allocation.
    hostile = struct.pack("<i", 5) + struct.pack("<4d", 0, 0, 0, 0) + struct.pack("<ii", 1, 2**30)
    try:
        shp.parse(_bundle(_shp(5, [hostile]), None, WGS84_PRJ))
        raise AssertionError("an unsafe point count was accepted")
    except IngestError as exc:
        assert "unsafe" in str(exc)


def check_field_import_integration() -> None:
    """The whole point: a shapefile must flow through the same
    prepare -> acknowledge -> apply pipeline, with the same AOI review and the
    same original-bytes provenance, as every other source format."""
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "shp.sqlite3")
        try:
            store = OperationsStore(db)
            incident = store.create_incident({"name": "Shapefile"}, "IC")
            store.create_period(incident["id"], default_period(), "IC")
            manager = FieldImportManager(db, store)

            import base64
            bundle = _bundle(
                _shp(1, [_point_record(11.5755, 48.1372), _point_record(11.58, 48.14)]),
                _dbf([("NAME", "C", 20)], [["Hydrant Nord"], ["Hydrant Sued"]]),
                WGS84_PRJ)
            request = {
                "filename": "hydranten.zip",
                "content_base64": base64.b64encode(bundle).decode("ascii"),
                "aoi_bbox": [11.4, 48.0, 11.8, 48.3],
                "default_feature_type": "water_source",
                "mapping": {"title": "name"},
            }
            report, prepared = manager.prepare(incident["id"], request)
            assert report["format"] == "shp", report["format"]
            assert report["feature_count"] == 2
            assert report["outside_aoi"] == 0
            assert prepared[0]["title"] == "Hydrant Nord"
            assert prepared[0]["feature_type"] == "water_source"

            applied = manager.apply(incident["id"], request, "IC")
            assert applied["imported"] is True and len(applied["feature_ids"]) == 2

            # Provenance: the original archive is kept byte-for-byte.
            filename, original = manager.original(incident["id"], applied["import_id"])
            assert filename == "hydranten.zip" and original == bundle

            # AOI review still applies - a feature outside the reviewed box
            # must require explicit acknowledgement.
            far = dict(request, aoi_bbox=[2.0, 40.0, 2.1, 40.1])
            far_report, _ = manager.prepare(incident["id"], far)
            assert far_report["requires_aoi_acknowledgement"] is True
            try:
                manager.apply(incident["id"], far, "IC")
                raise AssertionError("features outside the AOI were imported unacknowledged")
            except FieldImportError:
                pass

            # A refused CRS must surface as a FieldImportError, not an IngestError.
            bad = dict(request, content_base64=base64.b64encode(
                _bundle(_shp(1, [_point_record(4500000.0, 5334541.0)]), None, GAUSS_KRUEGER_PRJ)).decode("ascii"))
            try:
                manager.prepare(incident["id"], bad)
                raise AssertionError("an untransformable CRS was accepted")
            except FieldImportError as exc:
                assert "datum shift" in str(exc)
        finally:
            db.close()


def main() -> None:
    check_points_wgs84()
    check_utm_reprojection()
    check_web_mercator_reprojection()
    check_unsupported_crs_refused()
    check_polygon_rings_and_holes()
    check_polyline()
    check_dbf_alignment()
    check_encoding()
    check_rejections()
    check_field_import_integration()
    print("Shapefile import checks passed.")


if __name__ == "__main__":
    main()
