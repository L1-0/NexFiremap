"""Local drone-image evidence, georeferencing and deterministic mosaics.

Handles three things for operator-flown drone imagery: (1) storing the
original image plus a thumbnail as tactical evidence tied to a mission, (2)
optionally georeferencing it from four operator-supplied WGS84 corner
points into a GeoTIFF (a simple GCP warp - not photogrammetric bundle
adjustment, see ``_write_geotiff``), and (3) combining several georeferenced
frames into one mosaic via a deterministic "first wins" merge so overlap
resolution never depends on filesystem iteration order.

Georeferencing here is intentionally low-tech: no feature matching, no
camera-model correction, just the four corners the operator affirms are
correct for a nadir or already-orthorectified image. That's a real accuracy
ceiling (documented on every mosaic/asset's metadata, not hidden), but it
needs no internet connectivity and no photogrammetry pipeline - the
appropriate trade-off for imagery captured and processed in the field.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import rasterio
from PIL import Image, ImageOps, UnidentifiedImageError
from rasterio.control import GroundControlPoint
from rasterio.enums import ColorInterp, Resampling
from rasterio.merge import merge
from rasterio.transform import from_bounds
from rasterio.warp import reproject

from .config import Settings
from .offline_sources import OfflineSourceError, OfflineSourceManager
from .operations import NotFoundError, OperationsStore, utcnow


class DroneError(ValueError):
    """Invalid drone mission/image/mosaic input, or an unsafe/corrupt file."""


MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "TIFF": "image/tiff"}


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _json_field(row: Any) -> dict[str, Any]:
    """SQLite row -> API dict, expanding the ``*_json`` columns back into
    nested objects (mirrors telemetry.py's ``_row`` for the same reason)."""
    item = dict(row)
    for field in ("corners_json", "footprint_json", "metadata_json", "asset_ids_json"):
        if field in item:
            item[field.removesuffix("_json")] = json.loads(item.pop(field)) if item[field] else None
    return item


def _validate_time(value: Any) -> str | None:
    raw = _text(value, 80)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DroneError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise DroneError("timestamp must include a timezone")
    return parsed.isoformat(timespec="seconds")


def _corners(value: Any) -> list[list[float]] | None:
    """Validate operator-supplied georeferencing corners: exactly four
    WGS84 [lon, lat] positions, ordered TL/TR/BR/BL, forming a real
    (non-degenerate, non-self-intersecting) quadrilateral within a sane
    size. ``None`` in, ``None`` out - georeferencing is optional; an asset
    can be stored as plain unreferenced evidence."""
    if value in (None, ""):
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise DroneError("corners require TL, TR, BR and BL WGS84 positions")
    result: list[list[float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise DroneError("each corner must contain longitude and latitude")
        try:
            lon, lat = float(point[0]), float(point[1])
        except (TypeError, ValueError) as exc:
            raise DroneError("corner coordinates must be numeric") from exc
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            raise DroneError("corner is outside WGS84 bounds")
        result.append([lon, lat])
    # Shoelace formula: a near-zero signed area means the four points are
    # (near-)collinear rather than a real quadrilateral - the corners the
    # operator entered can't be a sane georeference in that case.
    area = sum(result[i][0] * result[(i + 1) % 4][1] - result[(i + 1) % 4][0] * result[i][1] for i in range(4)) / 2
    if abs(area) < 1e-12:
        raise DroneError("corners form a zero-area footprint")
    def crossed(a: list[float], b: list[float], c: list[float], d: list[float]) -> bool:
        def side(p: list[float], q: list[float], r: list[float]) -> float:
            return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0
    # Checking the two diagonal-adjacent edge pairs for a crossing catches a
    # "bowtie" quadrilateral - e.g. corners entered as TL/BR/TR/BL instead
    # of the required TL/TR/BR/BL winding order, which the area check alone
    # wouldn't reliably flag (a self-intersecting shape can still have a
    # sizeable signed area).
    if crossed(result[0], result[1], result[2], result[3]) or crossed(result[1], result[2], result[3], result[0]):
        raise DroneError("corner order creates a self-intersecting footprint")
    if max(p[0] for p in result) - min(p[0] for p in result) > 5 or max(p[1] for p in result) - min(p[1] for p in result) > 5:
        raise DroneError("drone footprint exceeds the five-degree safety limit")
    return result


class DroneManager:
    """Missions group imagery by flight; assets are individual images (each
    optionally georeferenced on ingest); mosaics combine several
    georeferenced assets into one merged raster. Georeferenced assets and
    mosaics are stored through ``OfflineSourceManager`` so they're
    automatically servable as map layers and included in map-pack coverage -
    plain (unreferenced) assets are evidence files only, kept solely on
    local disk under ``self.root``."""

    def __init__(self, store: OperationsStore, offline: OfflineSourceManager, settings: Settings) -> None:
        self.store, self.db, self.offline, self.settings = store, store.db, offline, settings
        self.root = settings.drone_dir.resolve()

    def _path(self, relative: str) -> Path:
        """Resolve a stored-relative path under the drone evidence root,
        rejecting anything that would escape it (defence against a crafted
        stored path, however that path was set)."""
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise DroneError("invalid drone evidence path")
        return path

    def create_mission(self, incident_id: str, data: dict[str, Any], actor: str) -> dict[str, Any]:
        self.store.get_incident(incident_id)
        name = _text(data.get("name"), 200)
        if not name:
            raise DroneError("mission name is required")
        mission_id, now = uuid4().hex, utcnow()
        values = (mission_id, incident_id, name, _text(data.get("aircraft"), 200),
                  _text(data.get("operator"), 200), _validate_time(data.get("started_at")),
                  _validate_time(data.get("ended_at")), _text(data.get("notes"), 10000), actor[:200], now, now)
        with self.db._write_lock:
            self.db.conn.execute(
                "INSERT INTO drone_missions (id,incident_id,name,aircraft,operator,started_at,ended_at,notes,created_by,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)", values,
            )
            self.store._audit(incident_id, "drone_mission", mission_id, "create", 1,
                              {"name": name, "aircraft": values[3], "started_at": values[5]}, actor)
            self.db.conn.commit()
        return self.get_mission(incident_id, mission_id)

    def get_mission(self, incident_id: str, mission_id: str) -> dict[str, Any]:
        row = self.db.conn.execute("SELECT * FROM drone_missions WHERE id=? AND incident_id=?",
                                   (mission_id, incident_id)).fetchone()
        if row is None:
            raise NotFoundError("drone mission not found")
        return dict(row)

    def list_missions(self, incident_id: str) -> list[dict[str, Any]]:
        self.store.get_incident(incident_id)
        return [dict(row) for row in self.db.conn.execute(
            "SELECT * FROM drone_missions WHERE incident_id=? ORDER BY COALESCE(started_at,created_at) DESC", (incident_id,)
        ).fetchall()]

    def _write_geotiff(self, image: Image.Image, corners: list[list[float]], partial: Path) -> dict[str, Any]:
        """Warp the raw image into a georeferenced GeoTIFF using the four
        operator-supplied corners as Ground Control Points - the ``gcp-
        warp-v1`` algorithm, deliberately the simplest thing that can
        produce a usable overlay: no lens-distortion correction, no
        orthorectification against a DEM, just a bilinear resample from
        image space into the corners' WGS84 quadrilateral. A per-pixel
        alpha (band 4) marks which output pixels actually came from source
        image data versus fill, so the result composites cleanly over a
        basemap without a rectangular black/white border where the source
        image doesn't reach the raster's bounding box."""
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        height, width = rgb.shape[:2]
        gcps = [GroundControlPoint(row=0, col=0, x=corners[0][0], y=corners[0][1]),
                GroundControlPoint(row=0, col=width, x=corners[1][0], y=corners[1][1]),
                GroundControlPoint(row=height, col=width, x=corners[2][0], y=corners[2][1]),
                GroundControlPoint(row=height, col=0, x=corners[3][0], y=corners[3][1])]
        west, east = min(p[0] for p in corners), max(p[0] for p in corners)
        south, north = min(p[1] for p in corners), max(p[1] for p in corners)
        if west == east or south == north:
            raise DroneError("corners do not define a raster extent")
        transform = from_bounds(west, south, east, north, width, height)
        output = np.zeros((4, height, width), dtype=np.uint8)
        for band in range(3):
            reproject(source=rgb[:, :, band], destination=output[band], gcps=gcps, src_crs="EPSG:4326",
                      dst_transform=transform, dst_crs="EPSG:4326", resampling=Resampling.bilinear)
        source_mask = np.full((height, width), 255, dtype=np.uint8)
        reproject(source=source_mask, destination=output[3], gcps=gcps, src_crs="EPSG:4326",
                  dst_transform=transform, dst_crs="EPSG:4326", resampling=Resampling.nearest)
        with rasterio.open(partial, "w", driver="GTiff", width=width, height=height, count=4,
                           dtype="uint8", crs="EPSG:4326", transform=transform, compress="deflate") as dataset:
            dataset.write(output); dataset.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
            dataset.update_tags(NEXFIREMAP_DERIVATION="gcp-warp-v1", AREA_OR_POINT="Area")
        return {"algorithm": "gcp-warp-v1", "crs": "EPSG:4326", "bounds": [west, south, east, north]}

    def ingest_image(self, incident_id: str, mission_id: str, filename: str, content: bytes,
                     data: dict[str, Any], actor: str) -> dict[str, Any]:
        """Store one uploaded image as evidence, optionally georeferencing
        it. Decoded twice on purpose: ``verify()`` alone (Pillow's own
        integrity check) doesn't fully decode pixel data, so a second
        ``Image.open`` + real decode is needed to catch a payload that
        passes the header-level check but is malformed/hostile at the pixel
        level (a decompression-bomb-style image, for instance - caught here
        via ``DecompressionBombError`` and the explicit pixel-count check
        below, run both before *and* after EXIF auto-rotation since
        rotation can change which dimension is width vs. height)."""
        self.get_mission(incident_id, mission_id)
        if not content or len(content) > self.settings.drone_max_upload_mb * 1024 * 1024:
            raise DroneError(f"image must be between 1 byte and {self.settings.drone_max_upload_mb} MiB")
        try:
            with Image.open(io.BytesIO(content)) as opened:
                opened.verify()
            with Image.open(io.BytesIO(content)) as opened:
                image_format = str(opened.format or "").upper()
                if image_format not in MEDIA_TYPES:
                    raise DroneError("drone image must be JPEG, PNG or TIFF")
                oriented = ImageOps.exif_transpose(opened)
                if oriented.width * oriented.height > self.settings.drone_max_pixels:
                    raise DroneError("drone image exceeds the decoded-pixel safety limit")
                image = oriented.convert("RGB")
                image.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise DroneError("drone image is invalid or unsafe") from exc
        width, height = image.size
        if width * height > self.settings.drone_max_pixels:
            raise DroneError("drone image exceeds the decoded-pixel safety limit")
        corners = _corners(data.get("corners"))
        georef_kind = _text(data.get("georef_kind"), 40)
        if corners and georef_kind not in {"nadir", "orthorectified"}:
            raise DroneError("georeferenced imagery must be affirmed as nadir or orthorectified")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise DroneError("metadata must be an object")
        classification = _text(data.get("classification"), 40) or "operational"
        if classification not in {"draft", "operational", "restricted"}:
            raise DroneError("classification must be draft, operational or restricted")
        asset_id, created_at = uuid4().hex, utcnow()
        directory = self._path(f"{incident_id}/{mission_id}/{asset_id}"); directory.mkdir(parents=True, exist_ok=False)
        suffix = {"JPEG": ".jpg", "PNG": ".png", "TIFF": ".tif"}[image_format]
        original = directory / f"original{suffix}"; thumbnail = directory / "thumbnail.jpg"
        original.write_bytes(content)
        preview = image.copy(); preview.thumbnail((1024, 1024)); preview.save(thumbnail, "JPEG", quality=85, optimize=True)
        offline_source_id = None; derivation = None
        try:
            if corners:
                source_id, partial = self.offline.begin_upload(None)
                derivation = self._write_geotiff(image, corners, partial)
                acquired = _validate_time(data.get("captured_at")) or created_at
                record = self.offline.finalize_raster_upload(
                    source_id, partial, name=f"Drone {Path(filename).name[:120]}", source=f"mission:{mission_id}",
                    attribution=_text(data.get("attribution"), 1000) or f"Drone operator: {_text(data.get('operator'), 200) or actor}",
                    acquired_at=acquired, licence="Incident operational imagery; distribution controlled by classification",
                    limitations="Operator-supplied four-corner nadir georeference; not survey-grade orthophotography",
                )
                offline_source_id = record["id"]
            footprint = ({"type": "Polygon", "coordinates": [[*corners, corners[0]]]} if corners else None)
            stored_metadata = {**metadata, "derivation": derivation, "georef_kind": georef_kind or None,
                               "original_sha256": hashlib.sha256(content).hexdigest(),
                               "orientation_applied": True}
            row = {"id": asset_id, "incident_id": incident_id, "mission_id": mission_id,
                   "filename": Path(filename).name[:300] or f"image{suffix}", "media_type": MEDIA_TYPES[image_format],
                   "classification": classification, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
                   "captured_at": _validate_time(data.get("captured_at")), "width": width, "height": height,
                   "original_path": original.relative_to(self.root).as_posix(),
                   "thumbnail_path": thumbnail.relative_to(self.root).as_posix(),
                   "corners_json": json.dumps(corners, separators=(",", ":")) if corners else None,
                   "footprint_json": json.dumps(footprint, separators=(",", ":")) if footprint else None,
                   "metadata_json": json.dumps(stored_metadata, sort_keys=True, separators=(",", ":")),
                   "georef_status": "operator_corners" if corners else "unreferenced",
                   "offline_source_id": offline_source_id, "created_by": actor[:200], "created_at": created_at}
            with self.db._write_lock:
                columns = ",".join(row); binds = ",".join(f":{key}" for key in row)
                self.db.conn.execute(f"INSERT INTO drone_assets ({columns}) VALUES ({binds})", row)
                self.store._audit(incident_id, "drone_asset", asset_id, "create", 1,
                                  {"mission_id": mission_id, "sha256": row["sha256"], "size_bytes": len(content),
                                   "georef_status": row["georef_status"], "offline_source_id": offline_source_id}, actor)
                self.db.conn.commit()
            return self.get_asset(incident_id, mission_id, asset_id)
        except Exception:
            # Everything above (writing the original/thumbnail files,
            # creating an offline_sources record for a georeferenced asset)
            # happens outside the database transaction - a SQLite rollback
            # alone wouldn't undo any of it. On any failure past this point,
            # manually unwind in reverse: drop the offline source record (if
            # one was created), delete the evidence files, and remove the
            # now-empty asset directory - so a failed ingest leaves no
            # orphaned files behind.
            if offline_source_id:
                self.offline.remove(offline_source_id)
            for path in (thumbnail, original):
                if path.exists(): path.unlink()
            try: directory.rmdir()
            except OSError: pass
            raise

    def get_asset(self, incident_id: str, mission_id: str, asset_id: str) -> dict[str, Any]:
        row = self.db.conn.execute(
            "SELECT * FROM drone_assets WHERE id=? AND incident_id=? AND mission_id=?", (asset_id, incident_id, mission_id)
        ).fetchone()
        if row is None:
            raise NotFoundError("drone asset not found")
        return _json_field(row)

    def list_assets(self, incident_id: str, mission_id: str) -> list[dict[str, Any]]:
        self.get_mission(incident_id, mission_id)
        return [_json_field(row) for row in self.db.conn.execute(
            "SELECT * FROM drone_assets WHERE incident_id=? AND mission_id=? ORDER BY COALESCE(captured_at,created_at),id",
            (incident_id, mission_id),
        ).fetchall()]

    def asset_file(self, incident_id: str, mission_id: str, asset_id: str, kind: str) -> tuple[Path, str, str]:
        asset = self.get_asset(incident_id, mission_id, asset_id)
        if kind == "original":
            path, media, filename = self._path(asset["original_path"]), asset["media_type"], asset["filename"]
        elif kind == "thumbnail":
            path, media, filename = self._path(asset["thumbnail_path"]), "image/jpeg", f"{asset_id}-thumbnail.jpg"
        else:
            raise DroneError("unknown asset rendition")
        if not path.is_file():
            raise NotFoundError("drone asset file is missing")
        return path, media, filename

    def create_mosaic(self, incident_id: str, mission_id: str, name: str, asset_ids: list[str], actor: str) -> dict[str, Any]:
        """Merge several already-georeferenced assets into one raster.
        ``method="first"`` (rasterio's merge) means overlap is resolved by
        whichever source dataset covers a pixel first in the input list -
        deterministic because ``assets`` is built from *sorted* asset ids,
        not upload/request order, so the same asset set always produces a
        byte-identical mosaic regardless of how the ids were passed in.
        This is a purely visual composite, not a photogrammetric blend
        (no seam-line feathering, no exposure/colour matching) - documented
        on the resulting record's ``limitations`` field."""
        self.get_mission(incident_id, mission_id)
        if not name.strip() or not 1 <= len(asset_ids) <= 500 or len(set(asset_ids)) != len(asset_ids):
            raise DroneError("mosaic requires a name and 1 to 500 unique assets")
        assets = [self.get_asset(incident_id, mission_id, asset_id) for asset_id in sorted(asset_ids)]
        if any(not asset["offline_source_id"] for asset in assets):
            raise DroneError("every mosaic input must be georeferenced")
        paths = [self.offline._stored_path(self.offline.load(asset["offline_source_id"])) for asset in assets]
        datasets = [rasterio.open(path) for path in paths]
        try:
            if any(dataset.crs != datasets[0].crs for dataset in datasets):
                raise DroneError("mosaic inputs must share a CRS")
            west, south = min(d.bounds.left for d in datasets), min(d.bounds.bottom for d in datasets)
            east, north = max(d.bounds.right for d in datasets), max(d.bounds.top for d in datasets)
            xres, yres = min(abs(d.transform.a) for d in datasets), min(abs(d.transform.e) for d in datasets)
            output_pixels = math.ceil((east - west) / xres) * math.ceil((north - south) / yres)
            if output_pixels > self.settings.drone_mosaic_max_pixels:
                raise DroneError("mosaic exceeds the configured output-pixel limit")
            mosaic, transform = merge(datasets, bounds=(west, south, east, north), res=(xres, yres), method="first")
            source_id, partial = self.offline.begin_upload(None)
            profile = datasets[0].profile.copy()
            profile.update(driver="GTiff", width=mosaic.shape[2], height=mosaic.shape[1], count=mosaic.shape[0],
                           transform=transform, compress="deflate")
            with rasterio.open(partial, "w", **profile) as output:
                output.write(mosaic)
                if mosaic.shape[0] == 4:
                    output.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
                output.update_tags(NEXFIREMAP_DERIVATION="stable-first-mosaic-v1",
                                   NEXFIREMAP_ASSET_IDS=",".join(sorted(asset_ids)))
        finally:
            for dataset in datasets: dataset.close()
        record = self.offline.finalize_raster_upload(
            source_id, partial, name=name[:200], source=f"drone-mission:{mission_id}", attribution="Incident drone imagery",
            acquired_at=utcnow(), licence="Incident operational imagery; distribution controlled by source assets",
            limitations="Deterministic visual mosaic from operator-georeferenced frames; not survey-grade bundle adjustment",
        )
        final_path = self.offline._stored_path(record); digest = hashlib.sha256(final_path.read_bytes()).hexdigest()
        mosaic_id, created_at = uuid4().hex, utcnow()
        metadata = {"algorithm": "stable-first-mosaic-v1", "ordered_asset_ids": sorted(asset_ids),
                    "source_hashes": [asset["sha256"] for asset in assets], "bounds": record["bounds"]}
        with self.db._write_lock:
            self.db.conn.execute(
                "INSERT INTO drone_mosaics (id,incident_id,mission_id,name,asset_ids_json,offline_source_id,sha256,size_bytes,metadata_json,created_by,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (mosaic_id, incident_id, mission_id, name[:200], json.dumps(sorted(asset_ids)), record["id"], digest,
                 final_path.stat().st_size, json.dumps(metadata, sort_keys=True, separators=(",", ":")), actor[:200], created_at),
            )
            self.store._audit(incident_id, "drone_mosaic", mosaic_id, "create", 1,
                              {"offline_source_id": record["id"], "asset_ids": sorted(asset_ids), "sha256": digest}, actor)
            self.db.conn.commit()
        return self.get_mosaic(incident_id, mission_id, mosaic_id)

    def get_mosaic(self, incident_id: str, mission_id: str, mosaic_id: str) -> dict[str, Any]:
        row = self.db.conn.execute(
            "SELECT * FROM drone_mosaics WHERE id=? AND incident_id=? AND mission_id=?", (mosaic_id, incident_id, mission_id)
        ).fetchone()
        if row is None:
            raise NotFoundError("drone mosaic not found")
        return _json_field(row)

    def list_mosaics(self, incident_id: str, mission_id: str) -> list[dict[str, Any]]:
        self.get_mission(incident_id, mission_id)
        return [_json_field(row) for row in self.db.conn.execute(
            "SELECT * FROM drone_mosaics WHERE incident_id=? AND mission_id=? ORDER BY created_at DESC", (incident_id, mission_id)
        ).fetchall()]

    def sensor_manifest(self, incident_id: str) -> dict[str, Any]:
        """Hash every external sensor file needed beside a database handover.

        The database only stores paths/hashes for drone assets and
        georeferenced mosaics/rasters, not the file bytes themselves (unlike
        e.g. incident_products) - handing off an incident package therefore
        means copying the drone root and tile-cache offline_sources
        directories *alongside* the database file. This manifest is the
        checklist for doing that correctly: every external file's expected
        path, size and SHA-256, so the receiving side can verify nothing was
        missed or corrupted in transit, plus a reminder to rotate feed
        tokens (a handover is also a good moment to rotate credentials)."""
        self.store.get_incident(incident_id)
        files: list[dict[str, Any]] = []
        seen_offline: set[str] = set()
        assets = self.db.conn.execute(
            "SELECT * FROM drone_assets WHERE incident_id=? ORDER BY mission_id,id", (incident_id,)
        ).fetchall()
        for raw in assets:
            asset = dict(raw)
            for kind, field in (("original", "original_path"), ("thumbnail", "thumbnail_path")):
                path = self._path(asset[field])
                files.append({"kind": kind, "asset_id": asset["id"], "root": "drone",
                              "path": asset[field], "exists": path.is_file(),
                              "size_bytes": path.stat().st_size if path.is_file() else None,
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None})
            if asset["offline_source_id"]:
                seen_offline.add(str(asset["offline_source_id"]))
        for row in self.db.conn.execute(
            "SELECT offline_source_id FROM drone_mosaics WHERE incident_id=?", (incident_id,)
        ).fetchall():
            seen_offline.add(str(row[0]))
        for source_id in sorted(seen_offline):
            try:
                record = self.offline.load(source_id); raster = self.offline._stored_path(record); sidecar = self.offline._sidecar(source_id)
                for kind, path in (("offline_raster", raster), ("offline_metadata", sidecar)):
                    files.append({"kind": kind, "offline_source_id": source_id, "root": "tile_cache",
                                  "path": path.relative_to(self.settings.tile_cache_dir.resolve()).as_posix(),
                                  "exists": path.is_file(), "size_bytes": path.stat().st_size if path.is_file() else None,
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None})
            except (FileNotFoundError, OfflineSourceError, ValueError):
                files.append({"kind": "offline_source", "offline_source_id": source_id, "root": "tile_cache",
                              "path": None, "exists": False, "size_bytes": None, "sha256": None})
        report_count = int(self.db.conn.execute(
            "SELECT COUNT(*) FROM vehicle_position_reports WHERE incident_id=?", (incident_id,)
        ).fetchone()[0])
        mosaic_count = int(self.db.conn.execute(
            "SELECT COUNT(*) FROM drone_mosaics WHERE incident_id=?", (incident_id,)
        ).fetchone()[0])
        feeds = [dict(row) for row in self.db.conn.execute(
            "SELECT id,name,provider,device_kind,active,last_received_at,revision FROM position_feed_sources "
            "WHERE incident_id=? ORDER BY id", (incident_id,)
        ).fetchall()]
        return {"schema": "nexfiremap-sensor-files/1", "incident_id": incident_id, "generated_at": utcnow(),
                "database_contains": {"position_reports": report_count, "position_feeds": feeds,
                                      "drone_assets": len(assets), "drone_mosaics": mosaic_count},
                "external_files": files, "complete": all(item["exists"] for item in files),
                "instructions": "Copy the database, drone root and tile-cache offline_sources together; verify every path, size and SHA-256 on the receiver. Rotate feed tokens."}
