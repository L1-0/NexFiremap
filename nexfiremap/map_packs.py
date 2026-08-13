"""Deterministic manifests for proving cached AOI map coverage offline.

This module never downloads tiles. It inventories the existing local tile
cache so an operator can see gaps before disconnecting from the WAN and can
detect missing or modified files later.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .offline_sources import OfflineSourceError, OfflineSourceManager
from .tiles import TileCache


MANIFEST_SCHEMA = "nexfiremap-map-pack/1"
MANIFEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_EXPECTED_TILES = 100_000
WEB_MERCATOR_LIMIT = 85.05112878


class MapPackError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tile_x(lon: float, zoom: int) -> int:
    count = 1 << zoom
    return min(count - 1, max(0, int(math.floor((lon + 180.0) / 360.0 * count))))


def _tile_y(lat: float, zoom: int) -> int:
    lat = max(-WEB_MERCATOR_LIMIT, min(WEB_MERCATOR_LIMIT, lat))
    radians = math.radians(lat)
    count = 1 << zoom
    value = (1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0 * count
    return min(count - 1, max(0, int(math.floor(value))))


def expected_tiles(bbox: list[float] | tuple[float, ...], min_zoom: int,
                   max_zoom: int) -> Iterator[tuple[int, int, int]]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise MapPackError("bbox must contain west, south, east, north")
    try:
        west, south, east, north = (float(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise MapPackError("bbox coordinates must be numeric") from exc
    if not (-180 <= west < east <= 180 and -WEB_MERCATOR_LIMIT <= south < north <= WEB_MERCATOR_LIMIT):
        raise MapPackError("bbox must be ordered and inside Web Mercator limits")
    if not (0 <= min_zoom <= max_zoom <= 22):
        raise MapPackError("zoom range must satisfy 0 <= minimum <= maximum <= 22")
    for zoom in range(min_zoom, max_zoom + 1):
        # East and south bounds are exclusive for tile enumeration. Sampling
        # one representable float back inside the AOI avoids counting a tile
        # that only touches the bbox at a zero-area boundary.
        x0, x1 = _tile_x(west, zoom), _tile_x(math.nextafter(east, west), zoom)
        y0, y1 = _tile_y(north, zoom), _tile_y(math.nextafter(south, north), zoom)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                yield zoom, x, y


class MapPackManager:
    def __init__(self, tiles: TileCache, offline_sources: OfflineSourceManager | None = None) -> None:
        self.tiles = tiles
        self.offline_sources = offline_sources
        self.directory = (tiles.dir / "manifests").resolve()

    def _layer(self, layer_id: str) -> dict[str, Any] | None:
        online = self.tiles.layer(layer_id)
        if online is not None:
            return {**online, "local_offline": False}
        if self.offline_sources is None or not layer_id.startswith("mbtiles-"):
            return None
        source_id = layer_id.removeprefix("mbtiles-")
        try:
            record = self.offline_sources.load(source_id)
            path = self.offline_sources._stored_path(record)
        except (FileNotFoundError, OfflineSourceError):
            return None
        return {"id": layer_id, "source_id": source_id, "name": record["name"],
                "attribution": record.get("attribution", ""), "min_zoom": int(record.get("min_zoom", 0)),
                "max_zoom": int(record.get("max_zoom", 22)), "tile_ext": "png",
                "local_offline": True, "source_kind": record.get("kind", "mbtiles"),
                "bounds": record.get("bounds"), "source_path": path,
                "source_size_bytes": path.stat().st_size, "source_sha256": _sha256(path)}

    def _manifest_path(self, manifest_id: str) -> Path:
        if not MANIFEST_ID_RE.fullmatch(manifest_id):
            raise MapPackError("invalid map-pack manifest id")
        path = (self.directory / f"{manifest_id}.json").resolve()
        if not path.is_relative_to(self.directory):
            raise MapPackError("invalid map-pack manifest path")
        return path

    def create(self, name: str, bbox: list[float], layer_ids: list[str],
               min_zoom: int, max_zoom: int) -> dict[str, Any]:
        clean_name = str(name or "").strip()[:200] or "Offline AOI check"
        if not isinstance(layer_ids, list) or not layer_ids:
            raise MapPackError("at least one cached layer is required")
        if len(set(layer_ids)) != len(layer_ids):
            raise MapPackError("layer ids must be unique")
        layers = []
        for layer_id in layer_ids:
            meta = self._layer(str(layer_id))
            if meta is None:
                raise MapPackError(f"unknown tile layer: {layer_id}")
            if int(min_zoom) < int(meta.get("min_zoom", 0)):
                raise MapPackError(f"{layer_id} supports zooms only from {meta.get('min_zoom', 0)}")
            if max_zoom > int(meta.get("max_zoom", 22)):
                raise MapPackError(f"{layer_id} supports zooms only through {meta.get('max_zoom')}")
            layer = {
                "id": meta["id"], "name": meta.get("name", meta["id"]),
                "attribution": meta.get("attribution", ""),
                "min_zoom": meta.get("min_zoom", 0),
                "max_zoom": meta.get("max_zoom"),
                "tile_ext": meta.get("tile_ext", "png"),
                "local_offline": bool(meta.get("local_offline")),
            }
            if meta.get("local_offline"):
                layer.update({key: meta[key] for key in
                              ("source_id", "source_kind", "bounds", "source_size_bytes", "source_sha256")})
                if meta.get("source_kind") == "raster":
                    bounds = meta.get("bounds")
                    if not bounds or not (bounds[0] <= bbox[0] and bounds[1] <= bbox[1]
                                          and bounds[2] >= bbox[2] and bounds[3] >= bbox[3]):
                        raise MapPackError(f"{layer_id} local raster does not fully cover the requested AOI")
            layers.append(layer)

        coordinates = list(expected_tiles(bbox, int(min_zoom), int(max_zoom)))
        expected_count = len(coordinates) * len(layers)
        if expected_count > MAX_EXPECTED_TILES:
            raise MapPackError(
                f"AOI requests {expected_count} tiles; maximum is {MAX_EXPECTED_TILES}"
            )

        entries: list[dict[str, Any]] = []
        present = total_bytes = 0
        for layer in layers:
            if layer.get("local_offline") and layer.get("source_kind") == "raster":
                total_bytes += int(layer.get("source_size_bytes") or 0)
            for zoom, x, y in coordinates:
                entry: dict[str, Any] = {"layer": layer["id"], "z": zoom, "x": x, "y": y}
                if layer.get("local_offline") and layer.get("source_kind") == "raster":
                    entry.update({"present": True, "storage": "local_raster",
                                  "size_bytes": 0, "sha256": layer["source_sha256"]})
                    present += 1
                elif layer.get("local_offline"):
                    tile = self.offline_sources.tile(layer["source_id"], zoom, x, y) if self.offline_sources else None
                    if tile is not None:
                        data = tile[0]
                        entry.update({"present": True, "storage": "local_mbtiles", "size_bytes": len(data),
                                      "sha256": hashlib.sha256(data).hexdigest()})
                        present += 1; total_bytes += len(data)
                    else:
                        entry["present"] = False
                else:
                    path = self.tiles.path_for(layer["id"], zoom, x, y)
                    if path.is_file():
                        stat = path.stat()
                        entry.update({
                            "present": True, "storage": "tile_cache", "size_bytes": stat.st_size,
                            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                            "sha256": _sha256(path),
                        })
                        present += 1
                        total_bytes += stat.st_size
                    else:
                        entry["present"] = False
                entries.append(entry)

        manifest_id = uuid4().hex
        manifest = {
            "schema": MANIFEST_SCHEMA, "id": manifest_id, "name": clean_name,
            "created_at": _now(), "bbox": [float(value) for value in bbox],
            "min_zoom": int(min_zoom), "max_zoom": int(max_zoom), "layers": layers,
            "summary": self._summary(expected_count, present, total_bytes, 0),
            "zoom_levels": self._breakdown(entries),
            "tiles": entries,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        final = self._manifest_path(manifest_id)
        partial = final.with_suffix(".json.tmp")
        try:
            partial.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(partial, final)
        finally:
            if partial.exists():
                partial.unlink()
        return manifest

    @staticmethod
    def _summary(expected: int, present: int, total_bytes: int, modified: int) -> dict[str, Any]:
        return {
            "expected": expected, "present": present, "missing": expected - present,
            "modified": modified, "bytes": total_bytes,
            "complete": present == expected and modified == 0,
            "completeness_percent": round((present / expected * 100.0) if expected else 100.0, 2),
        }

    @classmethod
    def _breakdown(cls, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for entry in entries:
            groups.setdefault((int(entry["z"]), str(entry["layer"])), []).append(entry)
        rows = []
        for (zoom, layer), values in sorted(groups.items()):
            present = sum(bool(value.get("present")) for value in values)
            modified = sum(bool(value.get("modified")) for value in values)
            summary = cls._summary(len(values), present, sum(int(value.get("size_bytes") or 0) for value in values), modified)
            rows.append({"zoom": zoom, "layer": layer, **summary})
        return rows

    def load(self, manifest_id: str) -> dict[str, Any]:
        path = self._manifest_path(manifest_id)
        if not path.is_file():
            raise FileNotFoundError(manifest_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MapPackError("map-pack manifest is unreadable") from exc
        if not isinstance(value, dict) or value.get("schema") != MANIFEST_SCHEMA:
            raise MapPackError("unsupported map-pack manifest schema")
        return value

    def verify(self, manifest_id: str) -> dict[str, Any]:
        manifest = self.load(manifest_id)
        expected = len(manifest.get("tiles", []))
        present = modified = total_bytes = 0
        gaps: list[dict[str, Any]] = []
        checked_entries: list[dict[str, Any]] = []
        layers = {str(item["id"]): item for item in manifest.get("layers", [])}
        raster_hashes: dict[str, str | None] = {}
        for entry in manifest.get("tiles", []):
            try:
                layer, zoom, x, y = str(entry["layer"]), int(entry["z"]), int(entry["x"]), int(entry["y"])
            except (KeyError, TypeError, ValueError) as exc:
                raise MapPackError("map-pack manifest contains an invalid tile entry") from exc
            meta = self._layer(layer)
            if meta is None:
                raise MapPackError(f"manifest references unknown tile layer: {layer}")
            checked = {**entry, "present": False, "modified": False}
            layer_manifest = layers.get(layer, {})
            if meta.get("local_offline") and meta.get("source_kind") == "raster":
                if layer not in raster_hashes:
                    try: raster_hashes[layer] = _sha256(meta["source_path"])
                    except OSError: raster_hashes[layer] = None
                    if raster_hashes[layer] is not None:
                        total_bytes += int(meta.get("source_size_bytes") or 0)
                current_hash = raster_hashes[layer]
                if current_hash is None:
                    gaps.append({"layer": layer, "z": zoom, "x": x, "y": y, "reason": "missing"})
                    checked_entries.append(checked); continue
                present += 1; checked["present"] = True
                if current_hash != layer_manifest.get("source_sha256") or current_hash != entry.get("sha256"):
                    modified += 1; checked["modified"] = True
                    gaps.append({"layer": layer, "z": zoom, "x": x, "y": y, "reason": "modified"})
                checked_entries.append(checked); continue
            if meta.get("local_offline"):
                tile = self.offline_sources.tile(meta["source_id"], zoom, x, y) if self.offline_sources else None
                if tile is None:
                    gaps.append({"layer": layer, "z": zoom, "x": x, "y": y, "reason": "missing"})
                    checked_entries.append(checked); continue
                data = tile[0]; present += 1; total_bytes += len(data); checked["present"] = True
                if not entry.get("present") or len(data) != entry.get("size_bytes") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                    modified += 1; checked["modified"] = True
                    gaps.append({"layer": layer, "z": zoom, "x": x, "y": y, "reason": "modified"})
                checked_entries.append(checked); continue
            path = self.tiles.path_for(layer, zoom, x, y)
            if not path.is_file():
                gaps.append({"layer": layer, "z": zoom, "x": x, "y": y, "reason": "missing"})
                checked_entries.append(checked)
                continue
            stat = path.stat(); present += 1; total_bytes += stat.st_size; checked["present"] = True
            original_hash = entry.get("sha256")
            if not entry.get("present") or not original_hash or stat.st_size != entry.get("size_bytes") or _sha256(path) != original_hash:
                modified += 1; checked["modified"] = True
                gaps.append({"layer": layer, "z": zoom, "x": x, "y": y, "reason": "modified"})
            checked_entries.append(checked)
        summary = self._summary(expected, present, total_bytes, modified)
        return {
            "schema": MANIFEST_SCHEMA, "id": manifest["id"], "name": manifest.get("name", ""),
            "verified_at": _now(), "summary": summary, "zoom_levels": self._breakdown(checked_entries),
            "all_zoom_levels_complete": summary["complete"] and all(row["complete"] for row in self._breakdown(checked_entries)),
            "gaps": gaps[:1000], "gaps_truncated": len(gaps) > 1000,
        }

    def list(self) -> list[dict[str, Any]]:
        if not self.directory.is_dir():
            return []
        rows = []
        for path in sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                manifest = self.load(path.stem)
            except (MapPackError, FileNotFoundError):
                continue
            rows.append({key: manifest.get(key) for key in ("id", "name", "created_at", "bbox", "min_zoom", "max_zoom", "summary")})
        return rows

    def delete(self, manifest_id: str) -> dict[str, Any]:
        """Unpin a retired pack without deleting its tile files immediately."""
        manifest = self.load(manifest_id)
        path = self._manifest_path(manifest_id)
        path.unlink()
        return {"id": manifest_id, "deleted": True, "name": manifest.get("name", ""),
                "tiles_unpinned": sum(bool(item.get("present")) for item in manifest.get("tiles", []))}
