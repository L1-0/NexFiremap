"""On-disk cache for basemap tile images.

Every OSM/basemap tile the map draws goes through this proxy on its way to
the browser and lands on disk. Panning back over the same area - or the next
time the server starts - costs zero upstream requests: the tile is already
there. Storage is bounded two ways: a calendar TTL (tiles are mostly static
but road/label data does change) and a total size cap enforced LRU-style, so
the cache stays "cached to an extent" rather than growing without bound.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Iterator

import httpx

from .basemaps import BASEMAPS, OVERLAYS, TERRAIN_DEM
from .config import Settings

log = logging.getLogger("nexfiremap.tiles")

# TERRAIN_DEM is proxied/cached like any other layer but deliberately isn't
# part of BASEMAPS/OVERLAYS - see its definition in basemaps.py.
_ALL_LAYERS = {layer["id"]: layer for layer in [*BASEMAPS, *OVERLAYS, TERRAIN_DEM]}


def _tile_extensions() -> set[str]:
    """Every file extension a cached tile can actually be written with,
    driven by each layer's own ``tile_ext`` (basemaps.py) instead of
    hardcoded - so TTL expiry, LRU eviction, and /api/status's size stats
    all cover a future basemap added with a new format automatically,
    rather than needing a matching change here (esri-terrain's .jpg tiles
    used to fall outside all three because this was hardcoded to "*.png")."""
    return {meta.get("tile_ext", "png") for meta in _ALL_LAYERS.values()}


def _iter_tile_files(root: Path) -> Iterator[Path]:
    for ext in _tile_extensions():
        yield from root.rglob(f"*.{ext}")

# Served when a tile can't be fetched and nothing stale is on disk, so a
# broken upstream shows an empty tile instead of a broken-image icon.
TRANSPARENT_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a4944415478da63600000020001551ec7d10000000049454e44ae426082"
)


def public_layer(layer: dict) -> dict:
    """Layer metadata as sent to the browser: URL points at our proxy."""
    public = {k: v for k, v in layer.items() if k != "subdomains"}
    ext = layer.get("tile_ext", "png")
    public["url"] = f"/tiles/{layer['id']}/{{z}}/{{x}}/{{y}}.{ext}"
    return public


class TileCache:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dir = settings.tile_cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            headers={"User-Agent": settings.tile_user_agent},
            follow_redirects=True,
        )
        # Caps how many upstream tile fetches run at once - keeps a fast pan
        # from bursting a tile provider, in line with normal usage-policy
        # expectations even though this traffic is proxied, not scraped.
        self._sem = asyncio.Semaphore(settings.tile_max_concurrent)
        self._locks: dict[Path, asyncio.Lock] = {}
        self._stopping = False
        self._prune_task: asyncio.Task | None = None
        self.hits = 0
        self.misses = 0
        self._pin_signature: tuple[tuple[str, int, int], ...] = ()
        self._pin_cache: set[Path] = set()

    def layer(self, layer_id: str) -> dict | None:
        return _ALL_LAYERS.get(layer_id)

    def path_for(self, layer_id: str, z: int, x: int, y: int) -> Path:
        meta = self.layer(layer_id)
        ext = meta.get("tile_ext", "png") if meta else "png"
        return self.dir / layer_id / str(z) / str(x) / f"{y}.{ext}"

    async def start(self) -> None:
        self._prune_task = asyncio.create_task(self._prune_loop(), name="tile-prune")

    async def stop(self) -> None:
        self._stopping = True
        if self._prune_task:
            self._prune_task.cancel()
            await asyncio.gather(self._prune_task, return_exceptions=True)
        await self._client.aclose()

    async def get(self, layer_id: str, z: int, x: int, y: int) -> bytes | None:
        meta = self.layer(layer_id)
        if meta is None:
            return None

        path = self.path_for(layer_id, z, x, y)
        ttl = self.settings.tile_cache_days * 86400

        cached = await self._read_if_fresh(path, ttl)
        if cached is not None:
            self.hits += 1
            return cached

        # Coalesce concurrent requests for the same tile into one fetch.
        lock = self._locks.setdefault(path, asyncio.Lock())
        async with lock:
            try:
                cached = await self._read_if_fresh(path, ttl)
                if cached is not None:
                    self.hits += 1
                    return cached

                self.misses += 1
                data = await self._fetch(meta, z, x, y)
                if data is not None:
                    await asyncio.to_thread(self._write, path, data)
                    return data
                # Upstream failed - a stale tile still beats a blank map.
                if path.is_file():
                    return await asyncio.to_thread(path.read_bytes)
                return None
            finally:
                self._locks.pop(path, None)

    @staticmethod
    async def _read_if_fresh(path: Path, ttl: float) -> bytes | None:
        # mtime is "time last fetched from upstream", used both for TTL
        # expiry and (below) eviction order. NTFS access-time updates are
        # commonly disabled, so we rank eviction by fetch time rather than
        # last-read time - simpler and doesn't depend on a filesystem setting.
        try:
            stat = path.stat()
        except OSError:
            return None
        if time.time() - stat.st_mtime > ttl:
            return None
        return await asyncio.to_thread(path.read_bytes)

    async def _fetch(self, meta: dict, z: int, x: int, y: int) -> bytes | None:
        subdomains = meta.get("subdomains") or "a"
        url = (
            meta["url"]
            .replace("{s}", subdomains[0])
            .replace("{z}", str(z))
            .replace("{x}", str(x))
            .replace("{y}", str(y))
            .replace("{r}", "")
        )
        async with self._sem:
            try:
                response = await self._client.get(url)
            except httpx.HTTPError as exc:
                log.warning("Tile fetch failed %s: %s", url, exc)
                return None
        if response.status_code != 200:
            log.warning("Tile fetch HTTP %d: %s", response.status_code, url)
            return None
        return response.content

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    # ------------------------------------------------------------- upkeep

    async def _prune_loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(3600)
                await asyncio.to_thread(self.prune_now)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                log.exception("Tile prune error")

    def prune_now(self) -> dict:
        ttl = self.settings.tile_cache_days * 86400
        cap = self.settings.tile_cache_max_mb * 1024 * 1024
        now = time.time()

        # A .tmp is always a leftover from an interrupted _write() (the
        # replace() that would rename it away happens right after the write
        # completes) - anything older than a few minutes is orphaned, not a
        # write genuinely in progress.
        removed_orphaned = 0
        for tmp_path in self.dir.rglob("*.tmp"):
            try:
                if now - tmp_path.stat().st_mtime > 300:
                    tmp_path.unlink()
                    removed_orphaned += 1
            except OSError:
                pass

        pinned = self._pinned_paths()
        files: list[tuple[Path, float, int, bool]] = []
        total = 0
        removed_expired = 0
        pinned_bytes = 0
        for path in _iter_tile_files(self.dir):
            try:
                stat = path.stat()
            except OSError:
                continue
            is_pinned = path.resolve() in pinned
            if now - stat.st_mtime > ttl and not is_pinned:
                try:
                    path.unlink()
                except OSError:
                    pass
                removed_expired += 1
                continue
            files.append((path, stat.st_mtime, stat.st_size, is_pinned))
            total += stat.st_size
            if is_pinned: pinned_bytes += stat.st_size

        removed_lru = 0
        if total > cap:
            files.sort(key=lambda f: f[1])  # oldest fetched first
            for path, _, size, is_pinned in files:
                if total <= cap:
                    break
                if is_pinned:
                    continue
                try:
                    path.unlink()
                    total -= size
                    removed_lru += 1
                except OSError:
                    pass

        if removed_expired or removed_lru or removed_orphaned:
            log.info(
                "Tile cache prune: %d expired, %d over budget, %d orphaned .tmp, %.1f MB remaining",
                removed_expired,
                removed_lru,
                removed_orphaned,
                total / 1024 / 1024,
            )
        return {
            "removed_expired": removed_expired,
            "removed_over_budget": removed_lru,
            "removed_orphaned": removed_orphaned,
            "bytes_remaining": total,
            "pinned_tiles": len(pinned),
            "pinned_bytes": pinned_bytes,
            "over_budget_due_to_pins": max(0, total - cap),
        }

    def _pinned_paths(self) -> set[Path]:
        """Files named by map-pack manifests are operational data, not LRU cache."""
        manifest_dir = (self.dir / "manifests").resolve()
        if not manifest_dir.is_dir():
            return set()
        manifests = list(manifest_dir.glob("*.json"))
        signature_rows = []
        for path in manifests:
            try:
                stat = path.stat(); signature_rows.append((path.name, stat.st_mtime_ns, stat.st_size))
            except OSError:
                continue
        signature = tuple(sorted(signature_rows))
        if signature == self._pin_signature:
            return {path for path in self._pin_cache if path.is_file()}
        pinned: set[Path] = set()
        for path in manifests:
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if manifest.get("schema") != "nexfiremap-map-pack/1" or not manifest.get("summary", {}).get("complete"):
                    continue
                for entry in manifest.get("tiles", []):
                    layer = str(entry["layer"])
                    if not entry.get("present") or self.layer(layer) is None:
                        continue
                    tile_path = self.path_for(layer, int(entry["z"]), int(entry["x"]), int(entry["y"])).resolve()
                    if tile_path.is_relative_to(self.dir.resolve()) and tile_path.is_file():
                        pinned.add(tile_path)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
        self._pin_signature, self._pin_cache = signature, pinned
        return pinned

    def stats(self) -> dict:
        total_bytes = 0
        total_files = 0
        for path in _iter_tile_files(self.dir):
            try:
                total_bytes += path.stat().st_size
                total_files += 1
            except OSError:
                continue
        pinned = self._pinned_paths()
        pinned_bytes = sum(path.stat().st_size for path in pinned if path.is_file())
        return {
            "tiles": total_files,
            "pinned_tiles": len(pinned),
            "pinned_bytes": pinned_bytes,
            "bytes": total_bytes,
            "hits": self.hits,
            "misses": self.misses,
        }
