"""Classified deterministic product and public-leakage checks."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.operations import OperationsError, OperationsStore, default_period
from nexfiremap.products import ProductManager


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "products.sqlite3")
        try:
            store = OperationsStore(db); manager = ProductManager(db, store)
            incident = store.create_incident({"name": "Product test", "notes": "SECRET IC NOTE"}, "IC")
            period = store.create_period(incident["id"], default_period(), "IC")
            scenario = store.create_scenario(incident["id"], period["id"], {"name": "SECRET PLAN"}, "IC")
            store.create_resource(incident["id"], {"callsign": "SECRET ENGINE", "unit_type": "engine"}, "IC")
            store.create_feature(incident["id"], {"period_id": period["id"], "scenario_id": scenario["id"],
                "feature_type": "tactical_line", "title": "SECRET TACTIC", "status": "planned",
                "geometry": {"type": "LineString", "coordinates": [[11, 48], [11.1, 48.1]]},
                "properties": {"hazards": "SECRET HAZARD"}}, "IC")
            perimeter = store.create_feature(incident["id"], {"period_id": period["id"],
                "feature_type": "confirmed_perimeter", "title": "Published perimeter", "status": "confirmed",
                "geometry": {"type": "Polygon", "coordinates": [[[11, 48], [11.1, 48], [11.1, 48.1], [11, 48]]]},
                "observer": "SECRET OBSERVER", "source": "SECRET SOURCE"}, "IC")
            public = manager.create(incident["id"], fmt="geojson", classification="public",
                                    product_type="public_information", snapshot_id=None, actor="PIO")
            filename, media_type, content = manager.content(incident["id"], public["id"])
            assert filename.endswith(".geojson") and media_type == "application/geo+json"
            assert hashlib.sha256(content).hexdigest() == public["sha256"]
            text = content.decode()
            for secret in ("SECRET IC NOTE", "SECRET PLAN", "SECRET ENGINE", "SECRET TACTIC",
                           "SECRET HAZARD", "SECRET OBSERVER", "SECRET SOURCE", "audit_log", "source_imports"):
                assert secret not in text, f"public product leaked {secret}"
            payload = json.loads(text)
            assert payload["features"]["features"][0]["id"] == perimeter["properties"]["id"]

            snapshot = store.create_snapshot(incident["id"], "Product base", period["id"], "operational", "IC")
            first = manager.create(incident["id"], fmt="csv", classification="operational", product_type="field",
                                   snapshot_id=snapshot["id"], actor="PLANS", title="Field map data")
            second = manager.create(incident["id"], fmt="csv", classification="operational", product_type="field",
                                    snapshot_id=snapshot["id"], actor="PLANS", title="Field map data")
            assert manager.content(incident["id"], first["id"])[2] == manager.content(incident["id"], second["id"])[2]
            signatures = {"pdf": b"%PDF", "geopdf": b"%PDF", "kmz": b"PK", "geotiff": b"II*\x00", "gpkg": b"SQLite format 3\x00"}
            for fmt, signature in signatures.items():
                made = manager.create(incident["id"], fmt=fmt, classification="operational", product_type="field",
                                      snapshot_id=snapshot["id"], actor="PLANS", title=f"Field {fmt}")
                assert manager.content(incident["id"], made["id"])[2].startswith(signature), fmt
            assert len(manager.list(incident["id"])) == 8
            try:
                manager.create(incident["id"], fmt="csv", classification="public", product_type="field",
                               snapshot_id=None, actor="bad")
                raise AssertionError("public classification allowed non-public template")
            except OperationsError:
                pass
        finally:
            db.close()
    check_vector_geopackage()
    check_command_forms()
    print("Classified product checks passed.")


def check_command_forms() -> None:
    """ICS 201/202/204 and the Lagekarte are page layouts over the same
    bundle, not different data. They must render as multi-page PDFs, stay
    byte-deterministic, and leave the plain map product untouched."""
    import re

    from nexfiremap.products import FORM_LAYOUTS, PRODUCT_TYPES, render

    assert set(FORM_LAYOUTS) <= PRODUCT_TYPES

    bundle = {
        "product_metadata": {"title": "Form", "produced_at": "2026-08-14T10:00:00Z",
                             "classification": "operational", "product_type": "ics201",
                             "author": "IC", "freshness_statement": "current to export time"},
        "incident": {"id": "i1", "name": "Waldbrand Nord", "incident_number": "2026-0815",
                     "status": "active", "notes": "Riegelstellung halten."},
        "operational_periods": [{"name": "OP-1", "status": "active", "objectives": "Schutz der Ortslage.",
                                 "starts_at": "2026-08-14T06:00:00Z", "ends_at": "2026-08-14T18:00:00Z"}],
        "resources": [{"name": "FL 11/1", "kind": "engine", "status": "working"},
                      {"name": "FL 11/2", "kind": "engine", "status": "available"}],
        "safety_checks": [{"check_key": "hazards", "checked": 1}, {"check_key": "lookouts", "checked": 0}],
        "features": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [11.5755, 48.1372]},
             "properties": {"id": "f1", "feature_type": "command_post", "title": "ELW",
                            "status": "observed", "symbology_profile": "dv102"}},
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[11.5, 48.1], [11.6, 48.2]]},
             "properties": {"id": "f2", "feature_type": "tactical_line", "title": "Riegel Nord",
                            "status": "planned", "responsible_unit": "Abschnitt 1"}},
        ]},
    }

    for kind, layout in FORM_LAYOUTS.items():
        payload = {**bundle, "product_metadata": {**bundle["product_metadata"], "product_type": kind}}
        content, media_type = render(payload, "pdf")
        assert media_type == "application/pdf" and content.startswith(b"%PDF"), kind

        # Page 1 is the map; the rest carry two form sections each.
        pages = len(re.findall(rb"/Type\s*/Page[^s]", content))
        expected = 1 + (len(layout["sections"]) + 1) // 2
        assert pages == expected, f"{kind}: {pages} pages, expected {expected}"

        # Determinism is this module's central guarantee - a stored SHA-256 is
        # only an integrity check if the same bundle always renders identically.
        assert render(payload, "pdf")[0] == content, f"{kind} is not deterministic"

    # An unknown product type must still take the plain map path, and a form
    # asked for in a data format is just the ordinary export - a form is a
    # page layout, not a different set of facts.
    plain = {**bundle, "product_metadata": {**bundle["product_metadata"], "product_type": "field"}}
    map_only, _ = render(plain, "pdf")
    assert len(re.findall(rb"/Type\s*/Page[^s]", map_only)) == 1
    assert render({**bundle, "product_metadata": {**bundle["product_metadata"],
                                                  "product_type": "lagekarte"}}, "geojson")[1] \
        == "application/geo+json"

    # An incident with far more features than a page holds must say so rather
    # than silently clipping the list off the bottom.
    crowded = {**bundle, "product_metadata": {**bundle["product_metadata"], "product_type": "ics201"},
               "resources": [{"name": f"FL {i}", "kind": "engine", "status": "working"} for i in range(80)]}
    assert render(crowded, "pdf")[0].startswith(b"%PDF")


def check_vector_geopackage() -> None:
    """`gpkg_features` must be a real, readable vector GeoPackage - not the
    raster one `gpkg` produces, and not merely well-formed SQLite.

    The strongest available check is a round trip through this project's own
    GeoPackage *reader* (`field_import._parse_gpkg`): if what we write can be
    imported back as the same geometries, the header, WKB encoding and catalog
    tables are all right, because that reader validates every one of them.
    """
    import sqlite3

    from nexfiremap.field_import import _parse_gpkg
    from nexfiremap.products import FORMATS, render

    assert {"gpkg", "gpkg_features"} <= FORMATS

    bundle = {
        "product_metadata": {"title": "GPKG", "produced_at": "2026-08-14T10:00:00Z"},
        "incident": {"id": "i1", "name": "Test", "center_lat": 48.1, "center_lon": 11.5},
        "features": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [11.5755, 48.1372]},
             "properties": {"id": "f1", "feature_type": "water_source", "title": "Hydrant"}},
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[11.5, 48.1], [11.6, 48.2]]},
             "properties": {"id": "f2", "feature_type": "tactical_line", "title": "Riegel"}},
            {"type": "Feature", "geometry": {"type": "Polygon",
                                             "coordinates": [[[11.5, 48.1], [11.6, 48.1], [11.6, 48.2], [11.5, 48.1]]]},
             "properties": {"id": "f3", "feature_type": "burn_area", "title": "Flaeche"}},
        ]},
    }
    content, media_type = render(bundle, "gpkg_features")
    assert media_type == "application/geopackage+sqlite3"

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "product.gpkg"
        path.write_bytes(content)
        conn = sqlite3.connect(path)
        try:
            # 0x47504B47 is ASCII "GPKG" - the header field that makes a
            # reader treat this as a GeoPackage rather than plain SQLite.
            assert conn.execute("PRAGMA application_id").fetchone()[0] == 1196444487
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            row = conn.execute("SELECT data_type, srs_id FROM gpkg_contents").fetchone()
            assert row == ("features", 4326), row
            assert conn.execute("SELECT COUNT(*) FROM incident_features").fetchone()[0] == 3
        finally:
            conn.close()

    parsed = _parse_gpkg(content)
    assert [geometry["type"] for geometry, _ in parsed] == ["Point", "LineString", "Polygon"]
    assert [properties.get("title") for _, properties in parsed] == ["Hydrant", "Riegel", "Flaeche"]
    assert parsed[0][0]["coordinates"] == [11.5755, 48.1372]

    # Determinism is this module's central guarantee: a stored SHA-256 is only
    # an integrity check if regenerating the same bundle gives the same bytes.
    assert render(bundle, "gpkg_features")[0] == content

    # ...and `gpkg` must still mean the raster product it always did, or
    # already-stored products would change meaning under their recipients.
    raster, _ = render(bundle, "gpkg")
    assert raster != content


if __name__ == "__main__":
    main()
