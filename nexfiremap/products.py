"""Deterministic classified incident products and portable vector exports.

Turns a snapshot/export bundle (see operations.py) into a downloadable file in
whatever format the recipient's workflow needs - GeoJSON/GPX/KML/KMZ/CSV for
GIS and handheld tools, GeoTIFF/GeoPackage/GeoPDF for offline raster viewers,
and a plain PDF map for printing or attaching to an incident action plan.

"Deterministic" matters here: two calls with the same bundle and format
always produce byte-identical output (fixed timestamps in PDF/KMZ metadata,
sorted JSON keys, sorted dict iteration) so a product's stored SHA-256 is a
real integrity check, not something that drifts on every regeneration.

Classification (draft/operational/public) controls redaction, not just a
label: a ``public`` product is rebuilt from a filtered feature set
(``classified_bundle``) that strips everything except a small public-safe
subset of feature types and properties - tactics, resources, hazards, safety
records and audit history never make it into a public-facing product even by
accident, because they are never in the input bundle handed to the renderer.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
import re
import sqlite3
import struct
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.shutil import copy as raster_copy

from .db import Database
from . import symbology
from .operations import (
    OBSERVATION_TYPES, SAFETY_CHECKS, OperationsError, OperationsStore,
    _clean_text, _id, utcnow,
)


# NOTE on the two GeoPackage entries: "gpkg" produces a *raster* GeoPackage
# (features burned into a classified raster, see _raster_product) and keeps
# that meaning so products already stored under it stay readable.
# "gpkg_features" is the vector one - real geometry and attributes, which is
# what a GIS-literate agency wants at handover. Renaming either would break
# existing stored products, so both exist and the distinction is explicit.
FORMATS = {"geojson", "csv", "gpx", "kml", "json", "kmz", "pdf", "geopdf",
           "geotiff", "gpkg", "gpkg_features"}
CLASSIFICATIONS = {"draft", "operational", "public"}
PRODUCT_TYPES = {"strategic", "field", "iap", "briefing", "transport", "air_operations",
                 "evacuation", "progression", "public_information", "handover",
                 # Command paperwork, laid out to match the real forms - see
                 # FORM_LAYOUTS. Rendered only for fmt="pdf"; asking for one in
                 # GeoJSON just gives the ordinary data export, since a form is
                 # a page layout rather than a different set of facts.
                 "ics201", "ics202", "ics204", "lagekarte"}
PUBLIC_TYPES = {"confirmed_perimeter", "burn_area", "active_edge", "inactive_edge", "smoke_report"}
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _public_feature(feature: dict[str, Any]) -> dict[str, Any] | None:
    """Redact one feature for a public-classification product, or drop it
    entirely (``None``) if its type isn't in the public-safe allowlist or it
    has been soft-deleted. Only a fixed, small property set survives -
    anything not explicitly named in ``allowed`` below (tactics, hazards,
    resource assignments, ...) is left out rather than filtered out, so a
    new property added elsewhere in the app can never leak into a public
    product by omission."""
    props = feature.get("properties", {})
    if props.get("feature_type") not in PUBLIC_TYPES or props.get("deleted_at"):
        return None
    allowed = {key: props.get(key) for key in ("id", "feature_type", "title", "status", "observed_at", "confidence")}
    return {"type": "Feature", "id": props.get("id"), "geometry": feature.get("geometry"), "properties": allowed}


def classified_bundle(bundle: dict[str, Any], classification: str) -> dict[str, Any]:
    """For ``draft``/``operational`` products, pass the bundle through
    unchanged. For ``public``, rebuild it from scratch with only the
    redacted feature set and a minimal incident summary - see
    ``_public_feature`` for exactly what is dropped."""
    if classification != "public": return bundle
    incident = bundle["incident"]
    public_features = [item for feature in bundle["features"]["features"] if (item := _public_feature(feature))]
    return {
        "schema": "nexfiremap-public-product/1", "exported_at": bundle.get("exported_at"),
        "incident": {"id": incident["id"], "name": incident["name"],
                     "incident_number": incident.get("incident_number"), "status": incident.get("status")},
        "features": {"type": "FeatureCollection", "features": public_features},
        "limitations": "Public-information product. Operational tactics, hazards, resources, safety records, audit history and source files are excluded.",
    }


def _feature_rows(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten every feature to one CSV-friendly row. Geometry itself is
    serialized to a JSON string column rather than split into lat/lon (or
    similar) columns, since features can be points, lines or polygons -
    one column that round-trips exactly beats several that only work for
    one geometry type."""
    return [{"id": p.get("id"), "feature_type": p.get("feature_type"), "title": p.get("title"),
             "status": p.get("status"), "observed_at": p.get("observed_at"),
             "geometry_type": feature.get("geometry", {}).get("type"),
             "coordinates_json": json.dumps(feature.get("geometry", {}).get("coordinates"), separators=(",", ":"))}
            for feature in bundle.get("features", {}).get("features", []) for p in [feature.get("properties", {})]]


def _all_points(value: Any) -> list[tuple[float, float]]:
    """Recursively flatten a GeoJSON ``coordinates`` array of any nesting
    depth (Point/LineString/Polygon, and their Multi- variants if they ever
    show up) down to a flat list of (lon, lat) pairs - a coordinate leaf is
    recognised by being a 2+-element list of numbers, everything else just
    gets walked one level deeper."""
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(v, (int, float)) for v in value[:2]):
        return [(float(value[0]), float(value[1]))]
    if not isinstance(value, list):
        return []
    return [point for child in value for point in _all_points(child)]


def _bounds(bundle: dict[str, Any]) -> tuple[float, float, float, float]:
    """Padded (west, south, east, north) map extent for the raster/PDF
    renderers below. Falls back to a small box around the incident's centre
    point when the bundle has no geometry at all (e.g. an empty export),
    rather than raising - a still-useful, if nearly featureless, map beats
    a failed product."""
    points = [point for feature in bundle.get("features", {}).get("features", [])
              for point in _all_points((feature.get("geometry") or {}).get("coordinates", []))]
    if not points:
        incident = bundle.get("incident", {})
        lon, lat = incident.get("center_lon"), incident.get("center_lat")
        if lon is None or lat is None: return (-0.01, -0.01, 0.01, 0.01)
        return (float(lon) - .01, float(lat) - .01, float(lon) + .01, float(lat) + .01)
    west, east = min(p[0] for p in points), max(p[0] for p in points)
    south, north = min(p[1] for p in points), max(p[1] for p in points)
    # A minimum pad (.002 deg) keeps a single-point or degenerate-extent
    # bundle from producing a zero-size bbox that rasterize()/matplotlib
    # would choke on.
    pad = max(east - west, north - south, .002) * .05
    return west - pad, south - pad, east + pad, north + pad


def _raster_product(bundle: dict[str, Any], driver: str) -> bytes:
    """Burn every feature into a single-band classified raster (GeoTIFF or
    GeoPackage raster) - each distinct ``feature_type`` gets its own integer
    class value (1..n, sorted alphabetically for determinism), 0 is nodata/
    background. The class-to-type mapping and the full product metadata are
    embedded as GDAL tags (``CLASS_VALUES``/``PRODUCT_METADATA``) so the
    raster is self-describing even opened outside NexFiremap."""
    features = bundle.get("features", {}).get("features", [])
    kinds = sorted({str((f.get("properties") or {}).get("feature_type", "feature")) for f in features})
    values = {kind: index + 1 for index, kind in enumerate(kinds)}
    shapes = [(feature["geometry"], values[str((feature.get("properties") or {}).get("feature_type", "feature"))])
              for feature in features if feature.get("geometry")]
    width = height = 1024
    bounds = _bounds(bundle); transform = from_bounds(*bounds, width, height)
    image = rasterize(shapes, out_shape=(height, width), transform=transform, fill=0,
                      all_touched=True, dtype="uint16") if shapes else np.zeros((height, width), dtype=np.uint16)
    suffix = ".tif" if driver == "GTiff" else ".gpkg"
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / f"product{suffix}"
        with rasterio.open(path, "w", driver=driver, width=width, height=height, count=1,
                           dtype="uint16", crs="EPSG:4326", transform=transform,
                           compress="deflate" if driver == "GTiff" else None,
                           nodata=0) as dataset:
            dataset.write(image, 1)
            dataset.update_tags(
                NEXFIREMAP_SCHEMA="nexfiremap-operational-raster/1",
                CLASS_VALUES=json.dumps(values, sort_keys=True, separators=(",", ":")),
                PRODUCT_METADATA=json.dumps(bundle.get("product_metadata", {}), sort_keys=True, separators=(",", ":")),
            )
        return path.read_bytes()


def _wkb(geometry: dict[str, Any]) -> bytes:
    """One GeoJSON geometry as little-endian Well-Known Binary.

    The mirror image of `field_import._wkb_geometry`, which *reads* this same
    encoding out of an imported GeoPackage. Hand-rolled for the same reason it
    is there: Point/LineString/Polygon is a narrow enough surface that a
    library dependency would be larger than the code it replaced.
    """
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if kind == "Point":
        return struct.pack("<BI2d", 1, 1, float(coordinates[0]), float(coordinates[1]))
    if kind == "LineString":
        body = struct.pack("<BII", 1, 2, len(coordinates))
        return body + b"".join(struct.pack("<2d", float(x), float(y)) for x, y, *_ in coordinates)
    if kind == "Polygon":
        body = struct.pack("<BII", 1, 3, len(coordinates))
        for ring in coordinates:
            body += struct.pack("<I", len(ring))
            body += b"".join(struct.pack("<2d", float(x), float(y)) for x, y, *_ in ring)
        return body
    raise OperationsError(f"GeoPackage export supports Point, LineString and Polygon, not {kind}")


def _gpkg_blob(geometry: dict[str, Any]) -> bytes:
    """WKB wrapped in the GeoPackage binary header.

    Header is magic ``GP``, version 0, a flags byte, then the SRS id. The flags
    byte is 1 here: bit 0 set means the header's own integers are
    little-endian, and the envelope bits are left clear because no bounding-box
    envelope is written - the spec allows omitting it, and every reader
    computes extents from the geometry anyway.
    """
    return b"GP" + bytes([0, 1]) + struct.pack("<i", 4326) + _wkb(geometry)


def _vector_gpkg_product(bundle: dict[str, Any]) -> bytes:
    """The incident's features as a real vector GeoPackage.

    Distinct from ``fmt="gpkg"``, which produces a *raster* GeoPackage via
    rasterio (see `_raster_product`) and keeps that meaning so products already
    stored under it stay readable. This is the format a GIS-literate agency
    actually wants at handover: attributes intact, geometry as geometry, and
    openable in QGIS/ArcGIS without conversion.

    A GeoPackage is just SQLite with three catalog tables, which makes writing
    one from a SQLite-native project a matter of getting the metadata right
    rather than pulling in a GIS stack. Everything below is required by the
    spec; a reader will reject the file if any of it is missing.
    """
    features = bundle.get("features", {}).get("features", [])
    metadata = bundle.get("product_metadata", {})
    west, south, east, north = _bounds(bundle)

    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "product.gpkg"
        conn = sqlite3.connect(path)
        try:
            # The magic that makes this a GeoPackage rather than a plain
            # database: 'GPKG' in the application_id header field, and the
            # spec version in user_version (1.2.1).
            conn.execute("PRAGMA application_id = 1196444487")
            conn.execute("PRAGMA user_version = 10201")
            conn.executescript("""
                CREATE TABLE gpkg_spatial_ref_sys (
                    srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY,
                    organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
                    definition TEXT NOT NULL, description TEXT);
                CREATE TABLE gpkg_contents (
                    table_name TEXT PRIMARY KEY, data_type TEXT NOT NULL,
                    identifier TEXT UNIQUE, description TEXT DEFAULT '',
                    last_change DATETIME NOT NULL, min_x DOUBLE, min_y DOUBLE,
                    max_x DOUBLE, max_y DOUBLE, srs_id INTEGER);
                CREATE TABLE gpkg_geometry_columns (
                    table_name TEXT NOT NULL, column_name TEXT NOT NULL,
                    geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL,
                    z TINYINT NOT NULL, m TINYINT NOT NULL,
                    PRIMARY KEY (table_name, column_name));
                CREATE TABLE incident_features (
                    fid INTEGER PRIMARY KEY AUTOINCREMENT,
                    geom BLOB,
                    feature_id TEXT, feature_type TEXT, title TEXT, status TEXT,
                    observed_at TEXT, source TEXT, observer TEXT, confidence TEXT,
                    properties_json TEXT);
            """)
            # The two "undefined" rows are mandated by the spec even when
            # unused; 4326 is the one the data actually uses.
            conn.executemany(
                "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
                [("Undefined cartesian SRS", -1, "NONE", -1, "undefined", None),
                 ("Undefined geographic SRS", 0, "NONE", 0, "undefined", None),
                 ("WGS 84 geodetic", 4326, "EPSG", 4326,
                  'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
                  'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]', "WGS 84")])

            last_change = str(metadata.get("produced_at") or "2000-01-01T00:00:00Z")
            conn.execute(
                "INSERT INTO gpkg_contents VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("incident_features", "features", "incident_features",
                 str(metadata.get("title") or "NexFiremap incident features"),
                 last_change, west, south, east, north, 4326))
            # "GEOMETRY" rather than a specific type: one incident carries
            # points, lines and areas in the same table, and declaring any one
            # of them would make the file lie about the other two.
            conn.execute("INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
                         ("incident_features", "geom", "GEOMETRY", 4326, 0, 0))

            for feature in features:
                geometry = feature.get("geometry")
                if not geometry:
                    continue
                properties = feature.get("properties") or {}
                conn.execute(
                    "INSERT INTO incident_features "
                    "(geom,feature_id,feature_type,title,status,observed_at,source,observer,"
                    "confidence,properties_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (_gpkg_blob(geometry), properties.get("id"), properties.get("feature_type"),
                     properties.get("title"), properties.get("status"), properties.get("observed_at"),
                     properties.get("source"), properties.get("observer"), properties.get("confidence"),
                     json.dumps(properties, sort_keys=True, separators=(",", ":"))))
            conn.commit()
        finally:
            conn.close()
        return path.read_bytes()


def _pdf_product(bundle: dict[str, Any]) -> bytes:
    """Render a single-page reference map (features, legend, scale bar, north
    arrow, footer) with matplotlib and export straight to PDF - no basemap
    tiles, since a printed/offline product can't depend on live tile
    fetches. ``CreationDate``/``ModDate`` are pinned to the product's own
    ``produced_at`` (not wall-clock "now") so re-rendering the same bundle
    later still yields a byte-identical PDF, matching this module's
    determinism guarantee."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    metadata = bundle.get("product_metadata", {})
    fig = plt.figure(figsize=(11.69, 8.27), dpi=120)
    _draw_map_axes(fig, bundle)
    output = io.BytesIO()
    timestamp = str(metadata.get("produced_at") or "2000-01-01T00:00:00+00:00").replace("Z", "+00:00")
    try: pdf_date = dt.datetime.fromisoformat(timestamp)
    except ValueError: pdf_date = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)  # malformed timestamp - a fixed fallback beats failing the whole product
    fig.savefig(output, format="pdf", metadata={"Title": str(metadata.get("title") or "NexFiremap product"),
                "Author": "NexFiremap", "Subject": str(metadata.get("product_type") or "incident map"),
                "CreationDate": pdf_date, "ModDate": pdf_date})
    plt.close(fig)
    return output.getvalue()


def _draw_map_axes(fig: Any, bundle: dict[str, Any]) -> Any:
    """Draw the reference map (features, legend, scale bar, north arrow,
    footer) onto ``fig``.

    Split out of `_pdf_product` so the multi-page ICS/Lagekarte forms can put
    the identical map on their first page. That sharing is the point: an ICS
    201's map box and a standalone map product must never drift apart, which
    they inevitably would as two copies of this layout code.
    """
    from matplotlib import pyplot as plt
    from matplotlib.lines import Line2D

    metadata = bundle.get("product_metadata", {})
    axis = fig.add_subplot(111)
    colors: dict[str, Any] = {}
    for feature in bundle.get("features", {}).get("features", []):
        geometry, props = feature.get("geometry") or {}, feature.get("properties") or {}
        kind = str(props.get("feature_type", "feature")); color = colors.setdefault(kind, plt.cm.tab20(len(colors) % 20))
        coords = geometry.get("coordinates", [])
        if geometry.get("type") == "Point" and len(coords) >= 2:
            axis.scatter([coords[0]], [coords[1]], c=[color], s=28, zorder=3)
        elif geometry.get("type") == "LineString":
            axis.plot([p[0] for p in coords], [p[1] for p in coords], color=color, linewidth=1.8)
        elif geometry.get("type") == "Polygon" and coords:
            for ring in coords:
                axis.fill([p[0] for p in ring], [p[1] for p in ring], facecolor=color, edgecolor=color, alpha=.28)
    west, south, east, north = _bounds(bundle); axis.set(xlim=(west, east), ylim=(south, north), xlabel="Longitude (°)", ylabel="Latitude (°)")
    axis.grid(True, linewidth=.35, alpha=.5); axis.set_aspect("equal", adjustable="box")
    # Target roughly a fifth of the map's on-screen width, converted from
    # degrees to km at this latitude (the cos() term shrinks a degree of
    # longitude toward the poles); the nearest round number at or below
    # that becomes the bar's actual length, so the bar always reads a tidy
    # value ("5 km") instead of an arbitrary one ("4.37 km").
    center_lat = (south + north) / 2; target_km = (east - west) * 111.32 * max(.05, math.cos(math.radians(center_lat))) / 5
    candidates = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200]
    scale_km = max((value for value in candidates if value <= target_km), default=0.1)
    scale_lon = scale_km / (111.32 * max(.05, math.cos(math.radians(center_lat))))
    scale_x, scale_y = west + (east - west) * .06, south + (north - south) * .06
    axis.plot([scale_x, scale_x + scale_lon], [scale_y, scale_y], color="black", linewidth=4, solid_capstyle="butt")
    axis.text(scale_x + scale_lon / 2, scale_y + (north - south) * .015, f"{scale_km:g} km", ha="center", va="bottom", fontsize=8)
    axis.annotate("N", xy=(east - (east - west) * .05, north - (north - south) * .04),
                  xytext=(east - (east - west) * .05, north - (north - south) * .13),
                  ha="center", weight="bold", arrowprops={"arrowstyle": "-|>", "color": "black"})
    incident = bundle.get("incident", {})
    axis.set_title(metadata.get("title") or f"{incident.get('name', 'Incident')}: {metadata.get('product_type', 'map')}", loc="left", fontsize=15, weight="bold")
    if colors:
        axis.legend(handles=[Line2D([0], [0], color=color, lw=3, label=kind.replace("_", " ")) for kind, color in colors.items()],
                    loc="upper right", fontsize=7, title="Legend")
    profile = symbology.normalise_profile(next(
        (str((f.get("properties") or {}).get("symbology_profile", ""))
         for f in bundle.get("features", {}).get("features", []) if f.get("properties")), ""))
    footer = (f"Symbology: {symbology.profile_authority(profile)}\n"
              f"Classification: {metadata.get('classification', '')}   CRS: EPSG:4326 / OGC:CRS84   "
              f"Produced: {metadata.get('produced_at', '')}\n{metadata.get('freshness_statement', '')}\n"
              "Coordinate grid shown. Scale varies with latitude; verify distances with the operational measurement tools. Page 1/1")
    fig.text(.06, .02, footer, fontsize=7, va="bottom")
    # Bottom band widened from .10 to .14 because the footer gained a
    # "Symbology: ..." line; without it the footer would overlap the x-axis
    # label. (matplotlib also emits a "tight layout not applied" warning here
    # about the *left/right* margins - that predates this change and is
    # unrelated: rect's .04 left inset is narrower than the y-axis label needs.
    # Harmless, since the layout falls back to the default which fits.)
    fig.tight_layout(rect=(.04, .14, .98, .96))
    return axis


# The command paperwork each audience is actually audited against. A
# department adopts a tool far faster when it emits the forms they already
# have to file, so these are laid out to match the real documents' box
# structure rather than being a generic "report".
FORM_LAYOUTS: dict[str, dict[str, Any]] = {
    "ics201": {
        "title": "ICS 201 - Incident Briefing",
        "authority": "ICS/NIMS Incident Briefing (US)",
        "sections": ("situation", "objectives", "organisation", "resources", "safety"),
    },
    "ics202": {
        "title": "ICS 202 - Incident Objectives",
        "authority": "ICS/NIMS Incident Objectives (US)",
        "sections": ("objectives", "safety", "weather"),
    },
    "ics204": {
        "title": "ICS 204 - Assignment List",
        "authority": "ICS/NIMS Assignment List (US)",
        "sections": ("organisation", "assignments", "resources", "safety"),
    },
    "lagekarte": {
        "title": "Lagekarte / Einsatzuebersicht",
        "authority": "DV 100 Fuehrungssystem, DV 102 taktische Zeichen (DE)",
        "sections": ("lage", "kraefte", "massnahmen", "sicherheit"),
    },
}

#: German headings for the Lagekarte, so the DE product is not an English
#: form with translated content - the audience reads these labels.
_DE_HEADINGS = {
    "lage": "Lage", "kraefte": "Eingesetzte Kraefte", "massnahmen": "Massnahmen",
    "sicherheit": "Sicherheit / Eigenschutz",
}


def _form_sections(bundle: dict[str, Any], kind: str) -> list[tuple[str, list[str]]]:
    """Assemble each form section's lines from the export bundle.

    Everything here already exists in the operational record - periods,
    features, resources, `SAFETY_CHECKS` state - so a form is a *view* of the
    incident, never a separate thing to keep up to date. That is what stops
    the paperwork drifting from the map, which is the usual failure of
    bolted-on reporting.
    """
    incident = bundle.get("incident", {})
    features = bundle.get("features", {}).get("features", [])
    resources = bundle.get("resources", [])
    periods = bundle.get("operational_periods", [])
    safety = bundle.get("safety_checks", [])
    german = kind == "lagekarte"

    live = [f for f in features if not (f.get("properties") or {}).get("deleted_at")]
    by_type: dict[str, int] = {}
    for feature in live:
        by_type[str((feature.get("properties") or {}).get("feature_type", "?"))] = \
            by_type.get(str((feature.get("properties") or {}).get("feature_type", "?")), 0) + 1
    by_status: dict[str, int] = {}
    for resource in resources:
        by_status[str(resource.get("status", "?"))] = by_status.get(str(resource.get("status", "?")), 0) + 1

    current = next((p for p in periods if p.get("status") == "active"), periods[-1] if periods else {})
    profile = _clean_text((live[0].get("properties") or {}).get("symbology_profile") if live else "", 60) \
        or "simplified_multinational"

    situation = [
        f"Incident: {incident.get('name', '')}  ({incident.get('incident_number') or 'no number'})",
        f"Status: {incident.get('status', 'active')}",
        f"Operational period: {current.get('name', '-')}  {current.get('starts_at', '')} - {current.get('ends_at', '')}",
        f"Mapped features: {len(live)}",
    ] + [f"  {kind_name.replace('_', ' ')}: {count}" for kind_name, count in sorted(by_type.items())]

    objectives = [line for line in (
        _clean_text(current.get("objectives"), 2000) or "",
        _clean_text(incident.get("notes"), 2000) or "",
    ) if line] or ["No objectives recorded for this operational period."]

    organisation = [
        f"Prepared by: {bundle.get('product_metadata', {}).get('author', 'local operator')}",
        f"Operational periods on record: {len(periods)}",
        f"Symbology profile: {profile}",
    ]

    resource_lines = [f"Resources: {len(resources)}"] + \
        [f"  {status}: {count}" for status, count in sorted(by_status.items())] + \
        [f"  - {_clean_text(r.get('name'), 60)} ({r.get('kind', '?')}, {r.get('status', '?')})"
         for r in resources[:25]]

    # SAFETY_CHECKS is the fixed nine-item checklist; showing which are
    # unticked is the whole point of putting safety on a form.
    ticked = {row.get("check_key") for row in safety if row.get("checked")}
    safety_lines = [
        ("ERFUELLT" if key in ticked else "OFFEN") + f"  {label}" if german
        else ("DONE" if key in ticked else "OPEN") + f"  {label}"
        for key, label in SAFETY_CHECKS
    ] or ["No safety record for this period."]

    assignments = [
        f"- {_clean_text((f.get('properties') or {}).get('title'), 60)}"
        f"  [{(f.get('properties') or {}).get('feature_type')}]"
        f"  {(f.get('properties') or {}).get('responsible_unit') or 'unassigned'}"
        for f in live if (f.get("properties") or {}).get("feature_type") in
        {"tactical_line", "division_boundary", "branch_boundary", "structure_protection_area"}
    ] or ["No division/branch assignments recorded."]

    weather = [
        f"- {_clean_text((f.get('properties') or {}).get('title'), 60)}: "
        f"{(f.get('properties') or {}).get('wind_speed_ms', '?')} m/s from "
        f"{(f.get('properties') or {}).get('wind_from_deg', '?')} deg"
        for f in live if (f.get("properties") or {}).get("feature_type") == "wind_observation"
    ] or ["No wind observations recorded."]

    available = {
        "situation": ("Situation Summary", situation),
        "objectives": ("Objectives", objectives),
        "organisation": ("Organisation", organisation),
        "resources": ("Resource Summary", resource_lines),
        "assignments": ("Assignments", assignments),
        "safety": ("Safety", safety_lines),
        "weather": ("Weather", weather),
        "lage": (_DE_HEADINGS["lage"], situation),
        "kraefte": (_DE_HEADINGS["kraefte"], resource_lines),
        "massnahmen": (_DE_HEADINGS["massnahmen"], objectives + assignments),
        "sicherheit": (_DE_HEADINGS["sicherheit"], safety_lines),
    }
    return [available[name] for name in FORM_LAYOUTS[kind]["sections"] if name in available]


def _form_product(bundle: dict[str, Any], kind: str) -> bytes:
    """An ICS form or a Lagekarte as a multi-page PDF.

    Page 1 is the existing reference map (`_pdf_product`), unchanged - the
    map *is* the first box of an ICS 201 and the whole point of a Lagekarte -
    and the following pages lay out the form's text boxes.

    Determinism is preserved exactly as in `_pdf_product`: page metadata is
    pinned to the product's own ``produced_at`` rather than wall-clock now, so
    re-rendering the same bundle still yields byte-identical output and the
    stored SHA-256 stays a real integrity check.
    """
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    layout = FORM_LAYOUTS[kind]
    metadata = bundle.get("product_metadata", {})
    incident = bundle.get("incident", {})
    sections = _form_sections(bundle, kind)

    timestamp = str(metadata.get("produced_at") or "2000-01-01T00:00:00+00:00").replace("Z", "+00:00")
    try:
        pdf_date = dt.datetime.fromisoformat(timestamp)
    except ValueError:
        pdf_date = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)

    output = io.BytesIO()
    with PdfPages(output) as pdf:
        # --- page 1: the map, reusing the existing renderer verbatim -------
        map_bytes = _pdf_product(bundle)
        # PdfPages cannot append an existing PDF, so page 1 is re-rendered
        # into this document rather than concatenated. Keeping `_pdf_product`
        # as the single map renderer matters more than avoiding the second
        # render: a form's map and a standalone map product must never drift.
        del map_bytes
        figure = plt.figure(figsize=(11.69, 8.27), dpi=120)
        _draw_map_axes(figure, bundle)
        pdf.savefig(figure)
        plt.close(figure)

        # --- following pages: the form's boxes -----------------------------
        per_page = 2
        for index in range(0, len(sections), per_page):
            figure = plt.figure(figsize=(8.27, 11.69), dpi=120)
            figure.text(.07, .955, layout["title"], fontsize=15, weight="bold")
            figure.text(.07, .933,
                        f"{incident.get('name', '')}  -  {metadata.get('classification', '')}  -  "
                        f"{metadata.get('produced_at', '')}", fontsize=8)
            top = .90
            for heading, lines in sections[index:index + per_page]:
                figure.text(.07, top, heading, fontsize=11, weight="bold")
                top -= .022
                # A form box has to be bounded: an incident with 400 features
                # would otherwise run text off the page silently, and a
                # truncated list that says so is honest where a clipped one is
                # not.
                shown = lines[:22]
                for line in shown:
                    figure.text(.09, top, str(line)[:110], fontsize=8, family="monospace")
                    top -= .0155
                if len(lines) > len(shown):
                    figure.text(.09, top, f"... {len(lines) - len(shown)} more (see the full export)",
                                fontsize=8, style="italic")
                    top -= .0155
                top -= .025
            figure.text(.07, .04,
                        f"{layout['authority']}   |   CRS: EPSG:4326 / OGC:CRS84   |   "
                        f"Page {index // per_page + 2}/{(len(sections) + per_page - 1) // per_page + 1}\\n"
                        f"{metadata.get('freshness_statement', '')}", fontsize=7, va="bottom")
            pdf.savefig(figure)
            plt.close(figure)

        pdf.infodict().update({
            "Title": str(metadata.get("title") or layout["title"]),
            "Author": "NexFiremap", "Subject": layout["authority"],
            "CreationDate": pdf_date, "ModDate": pdf_date,
        })
    return output.getvalue()


def _geopdf_product(bundle: dict[str, Any]) -> bytes:
    """A geospatial PDF (readable in Avenza/GIS tools with coordinates, not
    just a picture): rasterize features into a false-colour RGB GeoTIFF
    (each feature gets a distinct-ish colour from a cheap deterministic hash
    of its class index, not a real palette) and let GDAL's PDF driver carry
    the georeferencing across into the PDF itself via ``raster_copy``.
    Requires a GDAL build with PDF write support - raised as a clear
    ``OperationsError`` rather than a raw rasterio exception when that
    support is missing, since this is an installation/environment issue the
    caller can't fix by retrying."""
    features = bundle.get("features", {}).get("features", [])
    width = height = 1024; bounds = _bounds(bundle); transform = from_bounds(*bounds, width, height)
    shapes = [(feature["geometry"], index + 1) for index, feature in enumerate(features) if feature.get("geometry")]
    classes = rasterize(shapes, out_shape=(height, width), transform=transform, fill=0,
                        all_touched=True, dtype="uint16") if shapes else np.zeros((height, width), dtype=np.uint16)
    rgb = np.zeros((3, height, width), dtype=np.uint8)
    rgb[0] = (classes * 73 % 255).astype(np.uint8); rgb[1] = (classes * 151 % 255).astype(np.uint8)
    rgb[2] = (classes * 211 % 255).astype(np.uint8)
    with tempfile.TemporaryDirectory() as temp:
        source_path = Path(temp) / "product.tif"; path = Path(temp) / "product.pdf"
        try:
            with rasterio.open(source_path, "w", driver="GTiff", width=width, height=height, count=3,
                               dtype="uint8", crs="EPSG:4326", transform=transform) as dataset:
                dataset.write(rgb); dataset.update_tags(NEXFIREMAP_METADATA=json.dumps(bundle.get("product_metadata", {}), sort_keys=True))
            raster_copy(source_path, path, driver="PDF")
        except rasterio.errors.RasterioError as exc:
            raise OperationsError(f"this GDAL installation cannot create a geospatial PDF: {exc}") from exc
        return path.read_bytes()


def render(bundle: dict[str, Any], fmt: str) -> tuple[bytes, str]:
    """Dispatch to the right encoder for ``fmt`` and return ``(content,
    media_type)``. GPX/KML share one XML-building code path below (they're
    structurally very similar - waypoints/tracks vs. placemarks) rather than
    two near-duplicate functions."""
    if fmt in {"json", "geojson"}:
        return json.dumps(bundle, sort_keys=True, indent=2, ensure_ascii=False).encode(), "application/geo+json" if fmt == "geojson" else "application/json"
    if fmt == "pdf":
        # The *product type* selects the page layout, not the format: an ICS
        # 201 and a plain reference map are both PDFs of the same bundle.
        form = str(bundle.get("product_metadata", {}).get("product_type", ""))
        if form in FORM_LAYOUTS:
            return _form_product(bundle, form), "application/pdf"
        return _pdf_product(bundle), "application/pdf"
    if fmt == "geopdf": return _geopdf_product(bundle), "application/pdf"
    if fmt == "gpkg_features":
        return _vector_gpkg_product(bundle), "application/geopackage+sqlite3"
    if fmt in {"geotiff", "gpkg"}:
        return (_raster_product(bundle, "GTiff"), "image/tiff") if fmt == "geotiff" else (_raster_product(bundle, "GPKG"), "application/geopackage+sqlite3")
    if fmt == "kmz":
        kml, _ = render(bundle, "kml"); output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            # Fixed date_time, not "now" - zip entries otherwise embed a
            # timestamp that would make the same bundle produce a different
            # KMZ (and thus a different SHA-256) on every call.
            info = zipfile.ZipInfo("doc.kml", date_time=(2000, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, kml)
        return output.getvalue(), "application/vnd.google-earth.kmz"
    rows = _feature_rows(bundle)
    if fmt == "csv":
        output = io.StringIO(newline="")
        fields = ["id", "feature_type", "title", "status", "observed_at", "geometry_type", "coordinates_json"]
        writer = csv.DictWriter(output, fields); writer.writeheader(); writer.writerows(rows)
        return output.getvalue().encode("utf-8-sig"), "text/csv"
    root = ET.Element("gpx", {"version": "1.1", "creator": "NexFiremap", "xmlns": "http://www.topografix.com/GPX/1/1"}) if fmt == "gpx" else ET.Element("kml", {"xmlns": "http://www.opengis.net/kml/2.2"})
    container = ET.SubElement(root, "Document") if fmt == "kml" else root
    for feature in bundle.get("features", {}).get("features", []):
        geometry, props = feature.get("geometry", {}), feature.get("properties", {})
        kind, coords = geometry.get("type"), geometry.get("coordinates")
        if fmt == "gpx":
            if kind == "Point":
                node = ET.SubElement(container, "wpt", {"lat": str(coords[1]), "lon": str(coords[0])})
                ET.SubElement(node, "name").text = str(props.get("title") or props.get("feature_type"))
            elif kind == "LineString":
                trk = ET.SubElement(container, "trk"); ET.SubElement(trk, "name").text = str(props.get("title") or props.get("feature_type")); seg = ET.SubElement(trk, "trkseg")
                for point in coords: ET.SubElement(seg, "trkpt", {"lat": str(point[1]), "lon": str(point[0])})
        else:
            placemark = ET.SubElement(container, "Placemark"); ET.SubElement(placemark, "name").text = str(props.get("title") or props.get("feature_type"))
            node = ET.SubElement(placemark, kind)
            if kind == "Polygon":
                boundary = ET.SubElement(node, "outerBoundaryIs"); node = ET.SubElement(boundary, "LinearRing"); coords = coords[0]
            ET.SubElement(node, "coordinates").text = " ".join(",".join(map(str, point)) for point in ([coords] if kind == "Point" else coords))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), "application/gpx+xml" if fmt == "gpx" else "application/vnd.google-earth.kml+xml"


class ProductManager:
    """Creates, lists and serves incident products, storing the rendered
    bytes (not just a pointer to them) in the database so a product remains
    retrievable byte-for-byte even if the incident's live data later
    changes - a product is a point-in-time artifact, not a live view."""

    def __init__(self, db: Database, store: OperationsStore) -> None:
        self.db, self.store = db, store

    def create(self, incident_id: str, *, fmt: str, classification: str, product_type: str,
               snapshot_id: str | None, actor: str, title: str = "") -> dict[str, Any]:
        """Render and persist one product. Pulls from a specific historical
        snapshot when ``snapshot_id`` is given (so the product reflects
        that moment even if the incident has since changed), otherwise from
        a fresh live export. ``public_information``/``public`` are enforced
        as a matched pair - a public-classification product always uses the
        redacting template and nothing else, so a public export can never
        accidentally carry an operational template's full detail."""
        if fmt not in FORMATS: raise OperationsError("unsupported product format")
        if classification not in CLASSIFICATIONS: raise OperationsError("invalid product classification")
        if product_type not in PRODUCT_TYPES: raise OperationsError("invalid product type")
        if product_type == "public_information" and classification != "public":
            raise OperationsError("public-information products require public classification")
        if classification == "public" and product_type != "public_information":
            raise OperationsError("public classification requires the public-information template")
        bundle = self.store._snapshot_bundle(incident_id, snapshot_id) if snapshot_id else self.store.export_bundle(incident_id)
        payload = classified_bundle(bundle, classification)
        produced_at = str(bundle.get("exported_at")) if snapshot_id else utcnow()
        metadata = {
            "schema": "nexfiremap-product/1", "incident_id": incident_id, "snapshot_id": snapshot_id,
            "product_type": product_type, "classification": classification, "format": fmt,
            "title": _clean_text(title, 300), "produced_at": produced_at, "author": _clean_text(actor, 200),
            "crs": "OGC:CRS84 / WGS84 longitude-latitude", "freshness_statement":
                "Operational records are current to the embedded snapshot/export time; external layers retain their stated limitations.",
        }
        payload = {"product_metadata": metadata, **payload}
        content, media_type = render(payload, fmt)
        digest = hashlib.sha256(content).hexdigest(); product_id = _id()
        stem = SAFE_NAME.sub("-", f"{product_type}-{classification}-{product_id[:8]}").strip("-")
        extension = "tif" if fmt == "geotiff" else "pdf" if fmt == "geopdf" else "gpkg" if fmt == "gpkg_features" else fmt
        filename = f"{stem}.{extension}"
        with self.db._write_lock:
            self.db.conn.execute(
                "INSERT INTO incident_products (id,incident_id,snapshot_id,format,classification,product_type,filename,sha256,size_bytes,created_by,created_at,metadata_json,content_blob) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (product_id, incident_id, snapshot_id, fmt, classification, product_type, filename, digest,
                 len(content), _clean_text(actor, 200), metadata["produced_at"], json.dumps({**metadata, "media_type": media_type}, separators=(",", ":")), content),
            )
            self.store._audit(incident_id, "product", product_id, "create", 1,
                              {**metadata, "filename": filename, "sha256": digest, "size_bytes": len(content)}, actor)
            self.db.conn.commit()
        return {"id": product_id, "filename": filename, "sha256": digest, "size_bytes": len(content), **metadata}

    def list(self, incident_id: str) -> list[dict[str, Any]]:
        """Product metadata only, deliberately excluding ``content_blob`` -
        callers listing products (e.g. a picker UI) shouldn't pay to
        transfer every stored file's bytes just to show a filename."""
        self.store.get_incident(incident_id)
        return [dict(row) for row in self.db.conn.execute(
            "SELECT id,incident_id,snapshot_id,format,classification,product_type,filename,sha256,size_bytes,created_by,created_at,metadata_json FROM incident_products WHERE incident_id=? ORDER BY created_at DESC", (incident_id,)
        ).fetchall()]

    def content(self, incident_id: str, product_id: str) -> tuple[str, str, bytes]:
        """Fetch one stored product's ``(filename, media_type, bytes)`` for download."""
        row = self.db.conn.execute("SELECT filename,metadata_json,content_blob FROM incident_products WHERE id=? AND incident_id=?", (product_id, incident_id)).fetchone()
        if row is None: raise OperationsError("product not found")
        metadata = json.loads(row["metadata_json"])
        return row["filename"], metadata["media_type"], bytes(row["content_blob"])
