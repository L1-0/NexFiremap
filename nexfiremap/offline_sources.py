"""Validated operator-supplied MBTiles basemaps for disconnected use.

An operator can upload their own pre-built offline basemap - either an
MBTiles tile archive (the usual offline-map format: a SQLite database of
pre-rendered raster tiles) or a plain georeferenced raster (GeoTIFF/raster
GeoPackage, e.g. an orthophoto or a DEM) - and this module validates it,
stores it under a stable id, and serves it through the same tile-URL shape
the rest of the app already uses for online layers (see ``public_layers``).

Every upload is structurally validated before it's accepted (SQLite
integrity check, required tables/columns, at least one tile, sane zoom
range, real coordinate bounds) so a corrupt or malformed file fails loudly
at import time with a specific reason, rather than surfacing as silent
missing tiles later during an actual incident. Metadata (name, source,
attribution, licence, limitations) is mandatory on every import for the
same reason further_plan.md requires it elsewhere: an offline layer with no
provenance is not trustworthy enough to base tactical decisions on.

A raster source can also be locally re-derived into slope/aspect rasters
and elevation contours (``derive_terrain_package``) entirely offline - the
same terrain products ``terrain.py`` would otherwise need a network DEM
source for.
"""

from __future__ import annotations

import json
import html
import os
import re
import sqlite3
import io
import math
import hashlib
import time
import zipfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds
from rasterio.warp import transform as transform_coordinates
from skimage.measure import find_contours


SOURCE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
FORMATS = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


class OfflineSourceError(ValueError):
    """Invalid offline-source upload, metadata, or a corrupt/unreadable file."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OfflineSourceManager:
    """Import, validate, store and serve operator-supplied offline map
    sources. Each source has two files on disk: the data itself
    (``<id>.mbtiles``/``.tif``/``.gpkg``) and a JSON sidecar
    (``<id>.json``) carrying its metadata - the sidecar's existence is what
    ``list()``/``load()`` treat as "this source is fully imported", see
    ``_prune_orphaned_data`` for why that split matters."""

    def __init__(self, root: Path) -> None:
        self.directory = (root / "offline_sources").resolve()

    def _path(self, source_id: str) -> Path:
        """MBTiles path for a source id, rejecting a malformed id (blocks
        path traversal) and double-checking the resolved path still lands
        inside the offline-sources directory."""
        if not SOURCE_ID_RE.fullmatch(source_id):
            raise OfflineSourceError("invalid offline source id")
        path = (self.directory / f"{source_id}.mbtiles").resolve()
        if not path.is_relative_to(self.directory):
            raise OfflineSourceError("invalid offline source path")
        return path

    def _sidecar(self, source_id: str) -> Path:
        return self._path(source_id).with_suffix(".json")

    def _stored_path(self, record: dict[str, Any]) -> Path:
        """Actual data file for a loaded record - not simply ``_path()``
        with a different suffix, because a raster source's real extension
        (``.tif``/``.gpkg``) is recorded in ``storage_filename`` rather than
        assumed, and ``Path(filename).name`` strips any directory component
        so a crafted ``storage_filename`` can't escape the sources directory."""
        filename = str(record.get("storage_filename") or f"{record['id']}.mbtiles")
        path = (self.directory / Path(filename).name).resolve()
        if not path.is_relative_to(self.directory):
            raise OfflineSourceError("invalid offline source storage path")
        return path

    def begin_upload(self, content_length: int | None) -> tuple[str, Path]:
        """Allocate a new source id and a hidden partial-upload path to
        stream the incoming file into - nothing is visible to ``list()``/
        ``load()`` until ``finalize_upload``/``finalize_raster_upload``
        atomically publishes it, so a request that dies mid-upload never
        exposes a half-written source."""
        if content_length is not None and (content_length <= 0 or content_length > MAX_UPLOAD_BYTES):
            raise OfflineSourceError(f"MBTiles upload must be between 1 byte and {MAX_UPLOAD_BYTES} bytes")
        self.directory.mkdir(parents=True, exist_ok=True)
        source_id = uuid4().hex
        return source_id, self.directory / f".{source_id}.upload"

    def abort_upload(self, partial: Path) -> None:
        """Clean up a partial-upload file after a failed/cancelled
        request - best-effort, so any filesystem error is swallowed rather
        than masking the original failure that triggered the abort."""
        try:
            if partial.is_relative_to(self.directory) and partial.exists():
                partial.unlink()
        except OSError:
            pass

    @staticmethod
    def _detect_format(value: str | None, sample: bytes) -> str:
        """Trust the MBTiles ``metadata`` table's declared ``format`` when
        present and recognised; otherwise sniff one tile's magic bytes.
        Falling back to sniffing matters because not every MBTiles producer
        populates the metadata table correctly, and a wrong declared format
        would otherwise get served with the wrong Content-Type."""
        normalized = str(value or "").lower().strip().replace("jpeg", "jpg")
        if normalized in {"png", "jpg", "webp"}:
            return normalized
        if sample.startswith(b"\x89PNG\r\n\x1a\n"): return "png"
        if sample.startswith(b"\xff\xd8\xff"): return "jpg"
        if sample.startswith(b"RIFF") and sample[8:12] == b"WEBP": return "webp"
        raise OfflineSourceError("MBTiles format must be PNG, JPEG or WebP")

    def finalize_upload(self, source_id: str, partial: Path, *, name: str, source: str,
                        attribution: str, acquired_at: str, licence: str,
                        limitations: str = "") -> dict[str, Any]:
        """Validate an uploaded MBTiles file end to end and publish it.

        Validation opens the file as SQLite directly (not through a tile
        library) so a structurally-invalid file is rejected with a specific
        reason before anything is trusted about it: integrity check first
        (catches truncated/corrupt uploads), then required tables/columns,
        then real data (tile count, zoom range, an actual tile sniffed for
        format, and - if present - bounds that parse as valid WGS84).

        Publishing is two atomic renames (data file, then sidecar) rather
        than one - see ``_prune_orphaned_data`` for why the gap between them
        is safe to leave rather than trying to make fully atomic.
        """
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise OfflineSourceError("uploaded MBTiles file is empty")
        if partial.stat().st_size > MAX_UPLOAD_BYTES:
            raise OfflineSourceError(f"MBTiles upload exceeds {MAX_UPLOAD_BYTES} bytes")
        required = {
            "name": str(name or "").strip()[:200], "source": str(source or "").strip()[:500],
            "attribution": str(attribution or "").strip()[:2000],
            "acquired_at": str(acquired_at or "").strip()[:100],
            "licence": str(licence or "").strip()[:1000],
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise OfflineSourceError(f"offline source metadata required: {', '.join(missing)}")

        conn = sqlite3.connect(partial, timeout=30.0)
        try:
            integrity = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
            if integrity != ["ok"]:
                raise OfflineSourceError(f"MBTiles integrity check failed: {'; '.join(map(str, integrity[:5]))}")
            objects = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()}
            if not {"metadata", "tiles"}.issubset(objects):
                raise OfflineSourceError("MBTiles requires metadata and tiles tables/views")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tiles)").fetchall()}
            if not {"zoom_level", "tile_column", "tile_row", "tile_data"}.issubset(columns):
                raise OfflineSourceError("MBTiles tiles relation has invalid columns")
            metadata = {str(row[0]): str(row[1]) for row in conn.execute("SELECT name,value FROM metadata").fetchall()}
            tile_count, min_zoom, max_zoom = conn.execute(
                "SELECT COUNT(*), MIN(zoom_level), MAX(zoom_level) FROM tiles"
            ).fetchone()
            if not tile_count:
                raise OfflineSourceError("MBTiles contains no tiles")
            if min_zoom is None or max_zoom is None or not (0 <= int(min_zoom) <= int(max_zoom) <= 22):
                raise OfflineSourceError("MBTiles zoom levels must be between 0 and 22")
            sample = bytes(conn.execute("SELECT tile_data FROM tiles LIMIT 1").fetchone()[0])
            tile_format = self._detect_format(metadata.get("format"), sample)
            bounds = None
            if metadata.get("bounds"):
                try:
                    values = [float(value) for value in metadata["bounds"].split(",")]
                    if len(values) != 4 or not (-180 <= values[0] < values[2] <= 180 and -90 <= values[1] < values[3] <= 90):
                        raise ValueError
                    bounds = values
                except ValueError as exc:
                    raise OfflineSourceError("MBTiles bounds metadata is invalid") from exc
        except sqlite3.DatabaseError as exc:
            raise OfflineSourceError(f"uploaded file is not valid MBTiles: {exc}") from exc
        finally:
            conn.close()

        final = self._path(source_id)
        sidecar = self._sidecar(source_id)
        record = {
            "schema": "nexfiremap-offline-source/1", "id": source_id, **required,
            "limitations": str(limitations or "").strip()[:5000], "imported_at": _now(),
            "size_bytes": partial.stat().st_size, "tile_count": int(tile_count),
            "min_zoom": int(min_zoom), "max_zoom": int(max_zoom),
            "format": tile_format, "bounds": bounds, "mbtiles_metadata": metadata,
        }
        sidecar_partial = sidecar.with_suffix(".json.partial")
        try:
            sidecar_partial.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(partial, final)
            os.replace(sidecar_partial, sidecar)
        except Exception:
            if final.exists() and not sidecar.exists():
                final.unlink()
            raise
        finally:
            if sidecar_partial.exists(): sidecar_partial.unlink()
            if partial.exists(): partial.unlink()
        return record

    def finalize_raster_upload(self, source_id: str, partial: Path, *, name: str, source: str,
                               attribution: str, acquired_at: str, licence: str,
                               limitations: str = "") -> dict[str, Any]:
        """Validate an uploaded georeferenced raster (GeoTIFF or raster
        GeoPackage) and publish it - the raster counterpart to
        ``finalize_upload``. Bounds are always reprojected to WGS84
        (``transform_bounds``) regardless of the source CRS, since every
        consumer of a source record (map-pack coverage checks, the tile
        server's XYZ math) works in WGS84/Web Mercator, not the raster's
        native projection."""
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise OfflineSourceError("uploaded raster file is empty")
        required = {"name": str(name or "").strip()[:200], "source": str(source or "").strip()[:500],
                    "attribution": str(attribution or "").strip()[:2000],
                    "acquired_at": str(acquired_at or "").strip()[:100],
                    "licence": str(licence or "").strip()[:1000]}
        missing = [key for key, value in required.items() if not value]
        if missing: raise OfflineSourceError(f"offline source metadata required: {', '.join(missing)}")
        try:
            with rasterio.open(partial) as dataset:
                if dataset.driver not in {"GTiff", "GPKG"}:
                    raise OfflineSourceError("raster source must be GeoTIFF or raster GeoPackage")
                if dataset.crs is None:
                    raise OfflineSourceError("raster source requires an explicit CRS")
                if dataset.count < 1 or dataset.width < 1 or dataset.height < 1:
                    raise OfflineSourceError("raster source has no readable bands")
                if dataset.width * dataset.height > 2_000_000_000:
                    raise OfflineSourceError("raster dimensions exceed the safe import limit")
                bounds = list(transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds, densify_pts=21))
                if not all(math.isfinite(value) for value in bounds):
                    raise OfflineSourceError("raster bounds cannot be transformed to WGS84")
                suffix = ".tif" if dataset.driver == "GTiff" else ".gpkg"
                raster_meta = {"driver": dataset.driver, "crs": dataset.crs.to_string(),
                               "width": dataset.width, "height": dataset.height,
                               "band_count": dataset.count, "dtype": dataset.dtypes[0]}
        except rasterio.errors.RasterioError as exc:
            raise OfflineSourceError(f"uploaded geospatial raster is invalid: {exc}") from exc
        final = (self.directory / f"{source_id}{suffix}").resolve(); sidecar = self._sidecar(source_id)
        record = {"schema": "nexfiremap-offline-source/1", "id": source_id, **required,
                  "limitations": str(limitations or "").strip()[:5000], "imported_at": _now(),
                  "size_bytes": partial.stat().st_size, "kind": "raster", "storage_filename": final.name,
                  "min_zoom": 0, "max_zoom": 22, "format": "png", "bounds": bounds,
                  "raster_metadata": raster_meta}
        sidecar_partial = sidecar.with_suffix(".json.partial")
        try:
            sidecar_partial.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(partial, final); os.replace(sidecar_partial, sidecar)
        finally:
            if sidecar_partial.exists(): sidecar_partial.unlink()
            if partial.exists(): partial.unlink()
        return record

    def load(self, source_id: str) -> dict[str, Any]:
        """Read one source's metadata sidecar, verifying its schema tag and
        that the data file it points at still actually exists (a sidecar
        with no data file behind it - e.g. the data file was manually
        deleted - is treated the same as "not found", not a crash)."""
        sidecar = self._sidecar(source_id)
        if not sidecar.is_file():
            raise FileNotFoundError(source_id)
        try:
            record = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OfflineSourceError("offline source metadata is unreadable") from exc
        if record.get("schema") != "nexfiremap-offline-source/1" or record.get("id") != source_id:
            raise OfflineSourceError("offline source metadata is invalid")
        if not self._stored_path(record).is_file(): raise FileNotFoundError(source_id)
        return record

    def remove(self, source_id: str) -> None:
        """Remove one internally-created source during a failed transaction."""
        record = None
        try:
            record = self.load(source_id)
        except (FileNotFoundError, OfflineSourceError):
            pass
        paths = [self._sidecar(source_id)]
        if record is not None:
            paths.append(self._stored_path(record))
        else:
            paths.extend(self.directory.glob(f"{source_id}.*"))
        for path in paths:
            resolved = path.resolve()
            if resolved.is_relative_to(self.directory) and resolved.is_file():
                resolved.unlink()

    def _prune_orphaned_data(self) -> None:
        # finalize_upload()/finalize_raster_upload() write the data file
        # before the sidecar (two separate os.replace() calls), so a crash or
        # power loss between them - a real risk on field hardware - can
        # leave a data file with no sidecar. Nothing else ever looks for
        # such a file (list() only walks sidecars), so it would otherwise sit
        # on disk forever. The age check matters: that same gap between the
        # two os.replace() calls also exists for a moment during every
        # ordinary successful upload, and list() can run concurrently on
        # another request thread - pruning on sight would delete a file a
        # finalize is still actively writing the sidecar for. 300s is far
        # past how long a rename-only finalize ever takes, so only a genuine
        # crash orphan is old enough to qualify.
        cutoff = time.time() - 300
        for path in self.directory.glob("*.mbtiles"):
            if not SOURCE_ID_RE.fullmatch(path.stem) or path.with_suffix(".json").exists():
                continue
            try:
                if path.stat().st_mtime > cutoff:
                    continue
                path.unlink()
            except OSError:
                pass

    def list(self) -> list[dict[str, Any]]:
        if not self.directory.is_dir(): return []
        self._prune_orphaned_data()
        rows = []
        for sidecar in sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try: rows.append(self.load(sidecar.stem))
            except (FileNotFoundError, OfflineSourceError): continue
        return rows

    def public_layers(self) -> list[dict[str, Any]]:
        return [{
            "id": f"mbtiles-{item['id']}", "source_id": item["id"], "name": item["name"],
            "url": f"/offline-tiles/{item['id']}/{{z}}/{{x}}/{{y}}",
            "attribution": html.escape(item["attribution"]), "min_zoom": item["min_zoom"],
            "max_zoom": item["max_zoom"], "dark": False, "offline": True,
            "acquired_at": item["acquired_at"], "source": item["source"],
            "licence": item["licence"], "limitations": item["limitations"],
            "source_kind": item.get("kind", "mbtiles"),
        } for item in self.list()]

    def tile(self, source_id: str, zoom: int, x: int, y: int) -> tuple[bytes, str] | None:
        """One XYZ tile from a source, whichever kind it is. A raster
        source is reprojected on the fly per request (``_raster_tile``); an
        MBTiles source is a direct row lookup."""
        record = self.load(source_id)
        if not (record["min_zoom"] <= zoom <= record["max_zoom"]): return None
        count = 1 << zoom
        if not (0 <= x < count and 0 <= y < count): return None
        if record.get("kind") == "raster":
            return self._raster_tile(record, zoom, x, y)
        # MBTiles stores tiles in TMS row order (origin at the bottom-left),
        # the XYZ scheme this server/the browser otherwise uses everywhere
        # has origin at the top-left - flip the row index to bridge the two.
        tms_y = count - 1 - y
        uri = self._path(source_id).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        try:
            row = conn.execute(
                "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                (zoom, x, tms_y),
            ).fetchone()
            if row is None: return None
            return bytes(row[0]), FORMATS[record["format"].replace("jpeg", "jpg")]
        finally:
            conn.close()

    def _raster_tile(self, record: dict[str, Any], zoom: int, x: int, y: int) -> tuple[bytes, str] | None:
        """Reproject one 256x256 Web Mercator tile window out of a stored
        raster on demand - there's no pre-rendered tile pyramid for an
        imported raster, so every request does a small windowed reproject
        (GDAL only reads the source pixels the destination window actually
        needs, so this stays cheap regardless of the source raster's full
        size, matching imagery.py's read_band_on_grid). Returns ``None`` if
        the tile falls entirely outside the raster's bounds."""
        origin = 20037508.342789244; tiles = 1 << zoom; span = 2 * origin / tiles
        left, right = -origin + x * span, -origin + (x + 1) * span
        top, bottom = origin - y * span, origin - (y + 1) * span
        try:
            with rasterio.open(self._stored_path(record)) as source:
                bounds = transform_bounds(source.crs, "EPSG:3857", *source.bounds, densify_pts=21)
                if right <= bounds[0] or left >= bounds[2] or top <= bounds[1] or bottom >= bounds[3]:
                    return None
                indexes = list(range(1, min(3, source.count) + 1)); destination_transform = from_bounds(left, bottom, right, top, 256, 256)
                values = np.zeros((len(indexes), 256, 256), dtype=np.float64)
                for output_index, source_index in enumerate(indexes):
                    reproject(source=rasterio.band(source, source_index), destination=values[output_index],
                              src_transform=source.transform, src_crs=source.crs, src_nodata=source.nodata,
                              dst_transform=destination_transform, dst_crs="EPSG:3857", dst_nodata=0,
                              resampling=Resampling.bilinear)
                coverage = np.zeros((256, 256), dtype=np.uint8)
                reproject(source=source.dataset_mask(), destination=coverage, src_transform=source.transform,
                          src_crs=source.crs, dst_transform=destination_transform, dst_crs="EPSG:3857",
                          dst_nodata=0, resampling=Resampling.nearest)
                mask = coverage == 0
                if source.dtypes[0] != "uint8":
                    # A DEM or other non-8-bit raster (float elevation,
                    # 16-bit imagery, ...) needs some value range mapped
                    # into a displayable 0-255 - a 2nd/98th-percentile
                    # stretch (per band, per tile) gives a reasonable
                    # visual contrast without a fixed min/max that would
                    # need per-dataset tuning, at the cost of the same
                    # raw value not always mapping to the same displayed
                    # brightness across different tiles/zooms.
                    for band in range(values.shape[0]):
                        valid = values[band][~mask]
                        low, high = (np.percentile(valid, [2, 98]) if valid.size else (0, 1))
                        values[band] = np.clip((values[band] - low) * 255 / max(1e-9, high - low), 0, 255)
                if values.shape[0] == 1: values = np.repeat(values, 3, axis=0)
                elif values.shape[0] == 2: values = np.concatenate([values, values[1:2]], axis=0)
                rgba = np.dstack([values[:3].astype(np.uint8).transpose(1, 2, 0), coverage])
                output = io.BytesIO(); Image.fromarray(rgba, mode="RGBA").save(output, format="PNG")
                return output.getvalue(), "image/png"
        except rasterio.errors.RasterioError as exc:
            raise OfflineSourceError(f"offline raster tile could not be read: {exc}") from exc

    def derive_terrain_package(self, source_id: str, interval_m: float = 20.0) -> tuple[Path, dict[str, Any]]:
        """Derive slope, aspect and elevation contours from a locally
        imported DEM, entirely offline, and bundle them into one zip. Slope/
        aspect come from a simple finite-difference gradient (not a
        smoothed/windowed algorithm - adequate for tactical use, not survey
        precision); geographic (lat/lon) DEMs have their pixel spacing
        converted to metres first (``x_size``/``y_size``) so the gradient's
        units are consistent regardless of the source CRS. Contour levels
        are capped at 200 to keep the output bounded for a very fine
        interval over a large elevation range."""
        record = self.load(source_id)
        if record.get("kind") != "raster": raise OfflineSourceError("terrain derivation requires a geospatial raster source")
        if not math.isfinite(interval_m) or not 1 <= interval_m <= 1000:
            raise OfflineSourceError("contour interval must be between 1 and 1000 metres")
        with rasterio.open(self._stored_path(record)) as source:
            if source.width * source.height > 100_000_000:
                raise OfflineSourceError("DEM is too large for local derivation; clip it to the incident AOI first")
            elevation = source.read(1, masked=True).astype(np.float64)
            filled = np.ma.filled(elevation, np.nan)
            if not np.isfinite(filled).any(): raise OfflineSourceError("DEM contains no finite elevation values")
            x_size, y_size = abs(source.transform.a), abs(source.transform.e)
            if source.crs and source.crs.is_geographic:
                latitude = (source.bounds.bottom + source.bounds.top) / 2
                x_size *= 111_320 * max(.05, math.cos(math.radians(latitude))); y_size *= 111_320
            dy, dx = np.gradient(filled, y_size, x_size)
            slope = np.degrees(np.arctan(np.hypot(dx, dy))).astype(np.float32)
            aspect = ((np.degrees(np.arctan2(-dx, dy)) + 360) % 360).astype(np.float32)
            mask = np.ma.getmaskarray(elevation) | ~np.isfinite(filled); slope[mask] = np.nan; aspect[mask] = np.nan
            profile = source.profile.copy(); profile.update(driver="GTiff", dtype="float32", count=1, nodata=np.nan, compress="deflate")
            minimum, maximum = float(np.nanmin(filled)), float(np.nanmax(filled))
            first = math.ceil(minimum / interval_m) * interval_m
            levels = np.arange(first, maximum + interval_m * .5, interval_m)
            if len(levels) > 200: raise OfflineSourceError("contour interval would create more than 200 levels; use a larger interval")
            source_crs, source_transform = source.crs, source.transform
            with tempfile.TemporaryDirectory() as temp:
                temp_path = Path(temp); members: dict[str, bytes] = {}
                for name, values in (("slope-degrees.tif", slope), ("aspect-degrees.tif", aspect)):
                    path = temp_path / name
                    with rasterio.open(path, "w", **profile) as output: output.write(values, 1)
                    members[name] = path.read_bytes()
                features = []
                for level in levels:
                    for contour in find_contours(filled, float(level), mask=~mask):
                        if len(contour) < 2: continue
                        coordinates = [rasterio.transform.xy(source_transform, float(row), float(col)) for row, col in contour]
                        xs, ys = zip(*coordinates)
                        if source_crs and source_crs.to_string() != "EPSG:4326":
                            xs, ys = transform_coordinates(source_crs, "EPSG:4326", xs, ys)
                        features.append({"type": "Feature", "geometry": {"type": "LineString",
                                         "coordinates": [[float(x), float(y)] for x, y in zip(xs, ys)]},
                                         "properties": {"elevation_m": float(level)}})
                members["contours.geojson"] = json.dumps({"type": "FeatureCollection", "features": features},
                                                           sort_keys=True, separators=(",", ":")).encode()
                manifest = {"schema": "nexfiremap-terrain-package/1", "source_id": source_id,
                            "source_sha256": hashlib.sha256(self._stored_path(record).read_bytes()).hexdigest(),
                            "crs": record["raster_metadata"]["crs"], "contour_interval_m": interval_m,
                            "elevation_range_m": [minimum, maximum], "contour_features": len(features),
                            "files": {name: hashlib.sha256(data).hexdigest() for name, data in members.items()}}
                members["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode()
                filename = f"{source_id}-terrain-{interval_m:g}m.zip"; output_path = self.directory / filename
                partial = output_path.with_suffix(".zip.partial")
                with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
                    for name in sorted(members):
                        info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED
                        archive.writestr(info, members[name])
                os.replace(partial, output_path)
        return output_path, {**manifest, "filename": output_path.name,
                             "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                             "size_bytes": output_path.stat().st_size}
