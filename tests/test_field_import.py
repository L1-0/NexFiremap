"""Preview/apply, provenance and temporal field-observation import checks."""

from __future__ import annotations

import json
import base64
import io
import sys
import tempfile
import zipfile
import sqlite3
import struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap import field_import as field_import_module
from nexfiremap.field_import import FieldImportError, FieldImportManager
from nexfiremap.operations import OperationsStore, default_period


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "field.sqlite3")
        try:
            store = OperationsStore(db)
            incident = store.create_incident({"name": "Field import"}, "IC")
            period = store.create_period(incident["id"], default_period(), "IC")
            manager = FieldImportManager(db, store)
            source = json.dumps({
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [11.5, 48.1]},
                     "properties": {"name": "Spot A", "time": "2026-08-12T10:00:00+00:00"}},
                    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [12.5, 49.1]},
                     "properties": {"name": "Spot B", "time": "2026-08-12T12:00:00+00:00"}},
                ],
            })
            request = {
                "filename": "spots.geojson", "content": source, "source": "GPS team",
                "observer": "DIV-A", "period_id": period["id"], "aoi_bbox": [11, 47, 12, 49],
                "mapping": {"title": "name", "observed_at": "time"},
            }
            report, prepared = manager.prepare(incident["id"], request)
            assert report["feature_count"] == 2 and report["outside_aoi"] == 1
            assert len(prepared) == 2 and report["sha256"]
            try:
                manager.apply(incident["id"], request, "DIV-A")
                raise AssertionError("outside-AOI import applied without acknowledgement")
            except FieldImportError:
                pass
            assert manager.list_imports(incident["id"]) == []

            request["acknowledge_outside_aoi"] = True
            applied = manager.apply(incident["id"], request, "DIV-A")
            assert len(applied["feature_ids"]) == 2
            imports = manager.list_imports(incident["id"])
            assert len(imports) == 1 and imports[0]["sha256"] == report["sha256"]
            filename, original = manager.original(incident["id"], applied["import_id"])
            assert filename == "spots.geojson" and original == source.encode()
            bundle = store.export_bundle(incident["id"])
            assert bundle["source_imports"][0]["sha256"] == report["sha256"]
            second_db = Database(Path(temp) / "roundtrip.sqlite3")
            try:
                second = OperationsStore(second_db)
                imported = second.import_bundle(bundle, "receiving IC")
                assert imported["imported"] is True
                transferred = FieldImportManager(second_db, second).list_imports(incident["id"])
                assert transferred[0]["sha256"] == report["sha256"]
            finally:
                second_db.close()

            progression = store.progression(
                incident["id"], "2026-08-12T11:00:00+00:00", "2026-08-12T13:00:00+00:00"
            )
            assert progression["counts"] == {"from": 1, "to": 2, "new_since": 1}

            confirmed = {
                "filename": "perimeter.geojson", "format": "geojson", "period_id": period["id"],
                "content": json.dumps({"type": "Feature", "geometry": {"type": "Polygon", "coordinates":
                    [[[11.4, 48.0], [11.6, 48.0], [11.6, 48.2], [11.4, 48.0]]]}, "properties": {}}),
                "default_feature_type": "confirmed_perimeter", "aoi_bbox": [11, 47, 12, 49],
            }
            preview, _ = manager.prepare(incident["id"], confirmed)
            assert preview["requires_confirmation"] is True
            try:
                manager.apply(incident["id"], confirmed, "IC")
                raise AssertionError("confirmed perimeter imported without a reason")
            except FieldImportError:
                pass
            confirmed["confirmation_reason"] = "Ground GPS perimeter accepted by IC"
            assert manager.apply(incident["id"], confirmed, "IC")["imported"] is True

            kmz_buffer = io.BytesIO()
            with zipfile.ZipFile(kmz_buffer, "w") as archive:
                archive.writestr("doc.kml", """<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>Radio report</name><Point><coordinates>11.5,48.1</coordinates></Point></Placemark></Document></kml>""")
            kmz_request = {"filename": "radio.kmz", "content_base64": base64.b64encode(kmz_buffer.getvalue()).decode(),
                           "aoi_bbox": [11, 47, 12, 49]}
            kmz_report, _ = manager.prepare(incident["id"], kmz_request)
            assert kmz_report["format"] == "kmz" and kmz_report["feature_count"] == 1

            # A KMZ member's declared (central-directory) uncompressed size
            # is the archive's own claim, not something zipfile verifies -
            # the real guard has to be a bounded streaming read of what
            # actually comes out, not a metadata size check. Prove it
            # against real decompressed output, not against a spoofed
            # declared size: shrink the limit and confirm a real KML that
            # decompresses past it is rejected before ever being held whole
            # in memory.
            original_limit = field_import_module.MAX_SOURCE_BYTES
            field_import_module.MAX_SOURCE_BYTES = 4096
            try:
                oversized_buffer = io.BytesIO()
                with zipfile.ZipFile(oversized_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    padding = "<!-- " + ("x" * 200_000) + " -->"
                    archive.writestr(
                        "doc.kml",
                        f"""<kml xmlns="http://www.opengis.net/kml/2.2">{padding}<Document>"""
                        """<Placemark><name>Oversized</name><Point><coordinates>11.5,48.1</coordinates></Point></Placemark>"""
                        """</Document></kml>""",
                    )
                oversized_bytes = oversized_buffer.getvalue()
                assert len(oversized_bytes) < field_import_module.MAX_SOURCE_BYTES, \
                    "test setup: compressed KMZ must stay under the shrunk raw-upload limit so only the decompressed-size guard can catch it"
                oversized_request = {
                    "filename": "oversized.kmz", "content_base64": base64.b64encode(oversized_bytes).decode(),
                    "aoi_bbox": [11, 47, 12, 49],
                }
                try:
                    manager.prepare(incident["id"], oversized_request)
                    raise AssertionError("oversized KMZ decompressed content was not rejected")
                except FieldImportError as exc:
                    assert "exceeds safe import limits" in str(exc), str(exc)
            finally:
                field_import_module.MAX_SOURCE_BYTES = original_limit

            gpkg_path = Path(temp) / "field.gpkg"; conn = sqlite3.connect(gpkg_path)
            conn.executescript(
                "CREATE TABLE gpkg_contents (table_name TEXT PRIMARY KEY,data_type TEXT);"
                "CREATE TABLE gpkg_geometry_columns (table_name TEXT,column_name TEXT,srs_id INTEGER);"
                "CREATE TABLE reports (id INTEGER PRIMARY KEY,name TEXT,geom BLOB);"
            )
            conn.execute("INSERT INTO gpkg_contents VALUES ('reports','features')")
            conn.execute("INSERT INTO gpkg_geometry_columns VALUES ('reports','geom',4326)")
            geometry = b"GP" + bytes([0, 1]) + struct.pack("<i", 4326) + bytes([1]) + struct.pack("<I2d", 1, 11.5, 48.1)
            conn.execute("INSERT INTO reports VALUES (1,'GPKG report',?)", (geometry,)); conn.commit(); conn.close()
            gpkg_request = {"filename": "field.gpkg", "content_base64": base64.b64encode(gpkg_path.read_bytes()).decode(),
                            "aoi_bbox": [11, 47, 12, 49], "mapping": {"title": "name"}}
            gpkg_report, gpkg_prepared = manager.prepare(incident["id"], gpkg_request)
            assert gpkg_report["format"] == "gpkg" and gpkg_prepared[0]["title"] == "GPKG report"

            before = len(manager.list_imports(incident["id"]))
            invalid = {"filename": "bad.csv", "content": "lat,lon\nnope,11", "format": "csv",
                       "mapping": {"latitude": "lat", "longitude": "lon"}, "aoi_bbox": [11, 47, 12, 49]}
            try:
                manager.apply(incident["id"], invalid, "test")
                raise AssertionError("invalid CSV import applied")
            except FieldImportError:
                pass
            assert len(manager.list_imports(incident["id"])) == before
        finally:
            db.close()
    print("Field observation import checks passed.")


if __name__ == "__main__":
    main()
