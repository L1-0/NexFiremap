"""Offline AOI manifest completeness and integrity checks."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.config import Settings
from nexfiremap.map_packs import MAX_EXPECTED_TILES, MapPackError, MapPackManager, expected_tiles
from nexfiremap.offline_sources import OfflineSourceManager
from nexfiremap.tiles import TileCache


async def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = Settings(
            map_key="", host="127.0.0.1", port=8000, db_path=root / "db.sqlite3",
            cache_days=30, tile_cache_dir=root / "tiles", job_dir=root / "jobs",
        )
        tiles = TileCache(settings)
        try:
            manager = MapPackManager(tiles)
            bbox = [11.45, 48.05, 11.55, 48.15]
            coordinates = list(expected_tiles(bbox, 12, 13))
            assert coordinates == list(expected_tiles(bbox, 12, 13)), "tile enumeration is not deterministic"
            assert len(coordinates) > 1

            for index, (zoom, x, y) in enumerate(coordinates):
                path = tiles.path_for("osm", zoom, x, y)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"tile-" + str(index).encode())

            manifest = manager.create("Field AOI", bbox, ["osm"], 12, 13)
            assert manifest["summary"]["expected"] == len(coordinates)
            assert manifest["summary"]["complete"] is True
            assert manifest["summary"]["completeness_percent"] == 100.0
            assert {item["zoom"] for item in manifest["zoom_levels"]} == {12, 13}
            assert all(item["complete"] for item in manifest["zoom_levels"])
            assert manager.list()[0]["id"] == manifest["id"]

            # Once manifested, operational pack tiles survive normal expiry.
            old = time.time() - 60 * 86400
            os.utime(tiles.path_for("osm", *coordinates[0]), (old, old))
            prune = tiles.prune_now()
            assert tiles.path_for("osm", *coordinates[0]).is_file()
            assert prune["pinned_tiles"] == len(coordinates)
            assert tiles.stats()["pinned_tiles"] == len(coordinates)

            first = coordinates[0]
            tiles.path_for("osm", *first).write_bytes(b"modified")
            verification = manager.verify(manifest["id"])
            assert verification["summary"]["complete"] is False
            assert verification["summary"]["modified"] == 1
            assert any(item["reason"] == "modified" for item in verification["gaps"])
            assert any(item["zoom"] == first[0] and item["modified"] == 1
                       for item in verification["zoom_levels"])

            second = coordinates[1]
            tiles.path_for("osm", *second).unlink()
            verification = manager.verify(manifest["id"])
            assert verification["summary"]["missing"] == 1
            assert any(item["reason"] == "missing" for item in verification["gaps"])
            assert verification["all_zoom_levels_complete"] is False

            # A local raster is itself the offline source of truth and is
            # renderable at every selected zoom without prefetching a provider.
            offline = OfflineSourceManager(settings.tile_cache_dir)
            source_id, partial = offline.begin_upload(None)
            values = np.full((16, 16), 100, dtype=np.uint8)
            with rasterio.open(partial, "w", driver="GTiff", width=16, height=16, count=1,
                               dtype="uint8", crs="EPSG:4326",
                               transform=from_bounds(11.4, 48.0, 11.6, 48.2, 16, 16)) as output:
                output.write(values, 1)
            record = offline.finalize_raster_upload(
                source_id, partial, name="Local raster", source="authorised GIS",
                attribution="test", acquired_at="2026-08-13", licence="incident use",
            )
            local_manager = MapPackManager(tiles, offline)
            local = local_manager.create("Local every zoom", bbox, [f"mbtiles-{source_id}"], 10, 14)
            assert local["summary"]["complete"] is True
            assert [item["zoom"] for item in local["zoom_levels"]] == [10, 11, 12, 13, 14]
            assert all(item["complete"] for item in local["zoom_levels"])
            assert local_manager.verify(local["id"])["all_zoom_levels_complete"] is True
            with offline._stored_path(record).open("ab") as handle:
                handle.write(b"changed")
            altered = local_manager.verify(local["id"])
            assert altered["summary"]["modified"] == altered["summary"]["expected"]

            # Retiring a complete manifest unpins its cached tiles but does
            # not destructively remove the files at the moment of retirement.
            retired_path = tiles.path_for("osm", *coordinates[-1])
            assert retired_path.is_file()
            deleted = manager.delete(manifest["id"])
            assert deleted["deleted"] is True and retired_path.is_file()
            assert retired_path.resolve() not in tiles._pinned_paths()

            before = len(manager.list())
            for args in (
                ("Bad layer", bbox, ["not-a-layer"], 12, 12),
                ("Bad bounds", [12, 49, 11, 48], ["osm"], 12, 12),
                ("Too large", [-180, -85, 180, 85], ["osm"], 10, 10),
            ):
                try:
                    manager.create(*args)
                    raise AssertionError(f"invalid map pack accepted: {args[0]}")
                except MapPackError:
                    pass
            assert len(manager.list()) == before, "rejected request mutated manifest storage"
            assert MAX_EXPECTED_TILES == 100_000
        finally:
            await tiles.stop()
    print("Map-pack manifest checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
