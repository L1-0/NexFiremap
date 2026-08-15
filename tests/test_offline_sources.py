"""Validated local MBTiles ingestion and XYZ serving checks."""

from __future__ import annotations

import sqlite3
import math
import json
import zipfile
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.offline_sources import OfflineSourceError, OfflineSourceManager
from nexfiremap.tiles import TRANSPARENT_PNG


def build_mbtiles(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            "CREATE TABLE metadata (name TEXT, value TEXT);"
            "CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);"
            "CREATE UNIQUE INDEX tile_index ON tiles (zoom_level,tile_column,tile_row);"
        )
        conn.executemany("INSERT INTO metadata VALUES (?,?)", [
            ("name", "Training map"), ("format", "png"), ("bounds", "10,47,12,49"),
            ("minzoom", "1"), ("maxzoom", "1"),
        ])
        # MBTiles uses TMS row 0; at z1 that is XYZ y=1.
        conn.execute("INSERT INTO tiles VALUES (1,1,0,?)", (TRANSPARENT_PNG,))
        conn.commit()
    finally:
        conn.close()


def check_mission_imagery_is_an_overlay(manager, mbtiles_id: str, plain_raster_id: str) -> None:
    """Drone imagery must be published as a stackable overlay, not a basemap.

    Basemaps are mutually exclusive in the frontend, so classifying a drone
    frame as one means an operator can see exactly one at a time - never a
    newer pass over an older one, which is the whole point of flying again.
    `acquired_at` has to survive the trip too, because that is the field the
    stacking order is derived from.
    """
    import rasterio
    from rasterio.transform import from_bounds

    from nexfiremap.offline_sources import is_mission_imagery

    def add_raster(source: str, acquired: str):
        source_id, partial = manager.begin_upload(None)
        with rasterio.open(partial, "w", driver="GTiff", width=8, height=8, count=3,
                           dtype="uint8", crs="EPSG:4326",
                           transform=from_bounds(10, 47, 11, 48, 8, 8)) as dataset:
            dataset.write(np.full((3, 8, 8), 120, dtype=np.uint8))
        return manager.finalize_raster_upload(
            source_id, partial, name=f"Pass {acquired}", source=source, attribution="UAS",
            acquired_at=acquired, licence="incident use", limitations="visual only")

    old = add_raster("mission:m1", "2026-08-14T09:00:00Z")
    new = add_raster("mission:m1", "2026-08-14T15:00:00Z")
    mosaic = add_raster("drone-mission:m1", "2026-08-14T16:00:00Z")

    assert is_mission_imagery(old) and is_mission_imagery(new)
    # Mosaics use a different source prefix than single frames and are equally
    # mission imagery - a predicate that matched only one would leave mosaics
    # stranded as basemaps.
    assert is_mission_imagery(mosaic), "a drone mosaic must stack like its frames"

    layers = {item["source_id"]: item for item in manager.public_layers()}
    for record in (old, new, mosaic):
        assert layers[record["id"]]["overlay"] is True, record["source"]
        assert layers[record["id"]]["acquired_at"], "stacking order needs a capture time"
    # ...while everything that genuinely *is* a whole basemap stays one.
    assert layers[mbtiles_id]["overlay"] is False
    assert layers[plain_raster_id]["overlay"] is False, \
        "a raster with no mission source is an imported scene, not drone imagery"


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        manager = OfflineSourceManager(root)
        source_id, partial = manager.begin_upload(None)
        build_mbtiles(partial)
        record = manager.finalize_upload(
            source_id, partial, name="District offline map", source="District GIS export",
            attribution="<b>District GIS</b>", acquired_at="2026-08-12", licence="Internal operational use",
            limitations="Not for cadastral decisions",
        )
        assert record["tile_count"] == 1 and record["format"] == "png"
        assert manager.list()[0]["id"] == source_id
        assert manager.public_layers()[0]["url"].startswith("/offline-tiles/")
        assert manager.public_layers()[0]["attribution"] == "&lt;b&gt;District GIS&lt;/b&gt;"
        tile = manager.tile(source_id, 1, 1, 1)
        assert tile is not None and tile[0] == TRANSPARENT_PNG and tile[1] == "image/png"
        assert manager.tile(source_id, 1, 1, 0) is None, "XYZ/TMS Y conversion is reversed"

        raster_id, raster_partial = manager.begin_upload(None)
        with rasterio.open(raster_partial, "w", driver="GTiff", width=64, height=64, count=3,
                           dtype="uint8", crs="EPSG:4326", transform=from_bounds(10, 47, 12, 49, 64, 64)) as dataset:
            pixels = np.zeros((3, 64, 64), dtype=np.uint8)
            pixels[0] = np.linspace(20, 220, 64, dtype=np.uint8)[None, :]
            pixels[1] = 80; pixels[2] = 20; dataset.write(pixels)
        raster_record = manager.finalize_raster_upload(
            raster_id, raster_partial, name="Drone orthomosaic", source="UAS team", attribution="Incident UAS",
            acquired_at="2026-08-12T12:00:00Z", licence="incident use", limitations="visual interpretation only",
        )
        assert raster_record["kind"] == "raster" and raster_record["raster_metadata"]["crs"] == "EPSG:4326"

        check_mission_imagery_is_an_overlay(manager, source_id, raster_id)
        zoom = 8; x = int((11 + 180) / 360 * (1 << zoom))
        y = int((1 - math.asinh(math.tan(math.radians(48))) / math.pi) / 2 * (1 << zoom))
        raster_tile = manager.tile(raster_id, zoom, x, y)
        assert raster_tile is not None and raster_tile[0].startswith(b"\x89PNG")
        package, manifest = manager.derive_terrain_package(raster_id, 20)
        assert package.is_file() and manifest["contour_features"] > 0
        with zipfile.ZipFile(package) as archive:
            assert set(archive.namelist()) == {"aspect-degrees.tif", "contours.geojson", "manifest.json", "slope-degrees.tif"}
            embedded = json.loads(archive.read("manifest.json"))
            assert embedded["schema"] == "nexfiremap-terrain-package/1"

        before = len(manager.list())
        bad_id, bad = manager.begin_upload(None)
        bad.write_bytes(b"not sqlite")
        try:
            manager.finalize_upload(
                bad_id, bad, name="Bad", source="test", attribution="test",
                acquired_at="2026-08-12", licence="test",
            )
            raise AssertionError("malformed MBTiles was published")
        except OfflineSourceError:
            manager.abort_upload(bad)
        assert len(manager.list()) == before
    print("Offline MBTiles, raster and terrain-package checks passed.")


if __name__ == "__main__":
    main()
