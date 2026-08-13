"""Cache orchestration: decides what to pull from FIRMS and keeps it fresh.

The cache is organised as a coverage grid. The world is cut into
``cell_size_deg`` squares. For every (source, cell, day) we remember whether it
has been fetched and when. A map viewport therefore only triggers requests for
the gaps it actually needs.

Two things run in the background:

* a small pool of workers draining a fetch queue,
* a refresh loop that re-pulls the most recent days (which keep receiving new
  detections) and drops anything older than the retention window.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from .config import Settings
from .db import Database
from .firms import (
    FirmsAuthError,
    FirmsClient,
    FirmsDayRangeError,
    FirmsError,
    FirmsRateLimitError,
    FirmsSourceError,
)
from .geo import split_antimeridian

log = logging.getLogger("nexfiremap.cache")

# Sentinel cell standing for a whole-world fetch.
WORLD_CELL = (-1, -1)

# How long to wait before retrying a (source, cell, day) that errored.
ERROR_RETRY_SECONDS = 15 * 60


@dataclass(order=True)
class FetchTask:
    priority: int
    source: str = field(compare=False)
    cell: tuple[int, int] = field(compare=False)
    days: list[str] = field(compare=False, default_factory=list)
    attempt: int = field(compare=False, default=0)

    @property
    def key(self) -> tuple[str, int, int, str, str]:
        return (self.source, self.cell[0], self.cell[1], self.days[0], self.days[-1])


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def clamp_bbox(
    bbox: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    west, south, east, north = bbox
    south = max(-90.0, min(90.0, south))
    north = max(-90.0, min(90.0, north))
    if south > north:
        south, north = north, south
    west = max(-180.0, min(180.0, west))
    east = max(-180.0, min(180.0, east))
    return west, south, east, north


def cells_for_bbox(
    bbox: tuple[float, float, float, float], size: float
) -> list[tuple[int, int]]:
    """Grid cells overlapping a bbox (already clamped, ``west <= east``).

    A bbox crossing the antimeridian isn't valid input here: split it with
    ``geo.split_antimeridian`` first and call this once per piece, as
    ``plan()`` below does. Passing a crossing bbox directly makes ``x0 >
    x1``, so the cell range comes out empty and this silently returns no
    cells at all instead of raising.
    """
    west, south, east, north = bbox
    x0 = int(math.floor((west + 180.0) / size))
    x1 = int(math.floor((east + 180.0 - 1e-9) / size))
    y0 = int(math.floor((south + 90.0) / size))
    y1 = int(math.floor((north + 90.0 - 1e-9) / size))

    nx = int(math.ceil(360.0 / size))
    ny = int(math.ceil(180.0 / size))

    cells: list[tuple[int, int]] = []
    for x in range(max(0, x0), min(nx - 1, x1) + 1):
        for y in range(max(0, y0), min(ny - 1, y1) + 1):
            cells.append((x, y))
    return cells


def cell_area(cell: tuple[int, int], size: float) -> str:
    """FIRMS area string for a grid cell."""
    if cell == WORLD_CELL:
        return "world"
    x, y = cell
    west = -180.0 + x * size
    south = -90.0 + y * size
    east = min(180.0, west + size)
    north = min(90.0, south + size)
    return f"{west:g},{south:g},{east:g},{north:g}"


def chunk_days(days: Sequence[str], max_span: int) -> list[list[str]]:
    """Group sorted ISO days into runs of consecutive days, then split by span."""
    if not days:
        return []
    ordered = sorted(set(days))
    runs: list[list[str]] = [[ordered[0]]]
    for day in ordered[1:]:
        previous = date.fromisoformat(runs[-1][-1])
        if date.fromisoformat(day) - previous == timedelta(days=1):
            runs[-1].append(day)
        else:
            runs.append([day])

    chunks: list[list[str]] = []
    for run in runs:
        for start in range(0, len(run), max_span):
            chunks.append(run[start : start + max_span])
    return chunks


class CacheManager:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.client = FirmsClient(settings.map_key, timeout=settings.request_timeout_s)

        self._queue: asyncio.PriorityQueue[FetchTask] = asyncio.PriorityQueue()
        self._workers: list[asyncio.Task[None]] = []
        self._background: list[asyncio.Task[None]] = []
        self._inflight: set[tuple[str, int, int, str, str]] = set()
        self._lock = asyncio.Lock()
        self._stopping = False

        # Runtime state surfaced through /api/status.
        self.effective_day_range = settings.max_day_range
        self.disabled_sources: dict[str, str] = {}
        self.completed = 0
        self.failed = 0
        self.rows_added = 0
        self.active = 0
        self.last_error: str | None = None
        self.last_error_at: float | None = None
        self.last_success_at: float | None = None
        self.rate_limited_until: float = 0.0

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        for index in range(self.settings.max_concurrent_fetches):
            self._workers.append(asyncio.create_task(self._worker(index), name=f"fetch-{index}"))
        self._background.append(asyncio.create_task(self._refresh_loop(), name="refresh"))
        self._background.append(asyncio.create_task(self._purge_loop(), name="purge"))

        await asyncio.to_thread(self.purge_now)

        if self.settings.startup_bbox and self.settings.startup_days:
            log.info(
                "Warming cache for %s over %d days",
                self.settings.startup_bbox,
                self.settings.startup_days,
            )
            await self.ensure_cached(
                self.settings.startup_bbox, self.settings.startup_days
            )

    async def stop(self) -> None:
        self._stopping = True
        for task in [*self._workers, *self._background]:
            task.cancel()
        await asyncio.gather(*self._workers, *self._background, return_exceptions=True)
        await self.client.aclose()

    # ------------------------------------------------------------- planning

    def active_sources(self) -> list[str]:
        return [s for s in self.settings.sources if s not in self.disabled_sources]

    def _days_needing_fetch(
        self, source: str, cell: tuple[int, int], days: Sequence[str]
    ) -> list[str]:
        own = self.db.coverage_state(source, cell[0], cell[1], days)
        world = (
            self.db.coverage_state(source, WORLD_CELL[0], WORLD_CELL[1], days)
            if cell != WORLD_CELL
            else {}
        )

        now = time.time()
        today = utc_today()
        hot_cutoff = today - timedelta(days=self.settings.hot_days - 1)
        hot_ttl = self.settings.hot_ttl_minutes * 60

        missing: list[str] = []
        for day in days:
            record = own.get(day) or world.get(day)
            if record is None:
                missing.append(day)
                continue
            if record["status"] != "ok":
                if now - record["fetched_at"] > ERROR_RETRY_SECONDS:
                    missing.append(day)
                continue
            # Recent days keep receiving detections, so they expire.
            if date.fromisoformat(day) >= hot_cutoff and now - record["fetched_at"] > hot_ttl:
                missing.append(day)
        return missing

    def plan(
        self,
        bbox: tuple[float, float, float, float],
        days: Sequence[str],
        sources: Sequence[str],
    ) -> tuple[list[FetchTask], int]:
        """Work out which fetches a viewport still needs. Runs off the loop."""
        bbox = clamp_bbox(bbox)
        # A viewport crossing the antimeridian (west > east, e.g. the
        # Aleutians or Fiji) has to be split into two non-crossing pieces
        # before cells_for_bbox, which otherwise silently returns no cells
        # at all for it (its cell-index range comes out empty rather than
        # wrapping around).
        cells: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for piece in split_antimeridian(bbox):
            for cell in cells_for_bbox(piece, self.settings.cell_size_deg):
                if cell not in seen:
                    seen.add(cell)
                    cells.append(cell)
        if not cells:
            return [], 0
        if len(cells) > self.settings.max_cells_per_request:
            # Zoomed far out: one world request beats hundreds of tiles.
            cells = [WORLD_CELL]

        tasks: list[FetchTask] = []
        cached_days = 0
        today = utc_today()

        for source in sources:
            for cell in cells:
                missing = self._days_needing_fetch(source, cell, days)
                cached_days += len(days) - len(missing)
                if not missing:
                    continue
                for chunk in chunk_days(missing, self.effective_day_range):
                    # Fresher days first so the map fills in from "now" backwards.
                    age = (today - date.fromisoformat(chunk[-1])).days
                    tasks.append(FetchTask(priority=age, source=source, cell=cell, days=chunk))
        return tasks, cached_days

    async def ensure_cached(
        self,
        bbox: tuple[float, float, float, float],
        days_back: int,
        sources: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Queue whatever is missing for this viewport/time window."""
        if not self.settings.has_map_key:
            return {"queued": 0, "cached_days": 0, "error": "no map key configured"}

        days_back = max(1, min(self.settings.cache_days, days_back))
        today = utc_today()
        days = [(today - timedelta(days=n)).isoformat() for n in range(days_back)]
        selected = list(sources or self.active_sources())
        selected = [s for s in selected if s not in self.disabled_sources]

        tasks, cached_days = await asyncio.to_thread(self.plan, bbox, days, selected)

        queued = 0
        async with self._lock:
            for task in tasks:
                if task.key in self._inflight:
                    continue
                self._inflight.add(task.key)
                self._queue.put_nowait(task)
                queued += 1

        return {"queued": queued, "cached_days": cached_days, "pending": self.pending}

    # -------------------------------------------------------------- workers

    @property
    def pending(self) -> int:
        return self._queue.qsize() + self.active

    @property
    def busy(self) -> bool:
        return self.pending > 0

    async def _worker(self, index: int) -> None:
        while not self._stopping:
            task = await self._queue.get()
            self.active += 1
            try:
                await self._run_task(task)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                log.exception("Unhandled error in fetch worker %d", index)
            finally:
                self.active -= 1
                async with self._lock:
                    self._inflight.discard(task.key)
                self._queue.task_done()

    async def _requeue(self, task: FetchTask) -> None:
        async with self._lock:
            if task.key not in self._inflight:
                self._inflight.add(task.key)
            self._queue.put_nowait(task)

    async def _run_task(self, task: FetchTask) -> None:
        # Respect an active rate-limit backoff.
        wait = self.rate_limited_until - time.time()
        if wait > 0:
            await asyncio.sleep(min(wait, 60))

        area = cell_area(task.cell, self.settings.cell_size_deg)
        start = date.fromisoformat(task.days[0])
        span = len(task.days)

        try:
            rows = await self.client.fetch_area(task.source, area, start, span)
        except FirmsDayRangeError as exc:
            # Step the chunk size down and retry in smaller pieces.
            new_span = max(1, span // 2)
            if new_span < self.effective_day_range:
                self.effective_day_range = new_span
                log.warning("FIRMS rejected a %d-day range; using %d days", span, new_span)
            self._note_error(exc)
            if span > 1:
                for chunk in chunk_days(task.days, new_span):
                    await self._requeue(
                        FetchTask(
                            priority=task.priority,
                            source=task.source,
                            cell=task.cell,
                            days=chunk,
                            attempt=task.attempt + 1,
                        )
                    )
            else:
                await self._mark(task, 0, "error", str(exc))
            return
        except FirmsRateLimitError as exc:
            self.rate_limited_until = time.time() + 120
            self._note_error(exc)
            if task.attempt < 5:
                task.attempt += 1
                await asyncio.sleep(5)
                await self._requeue(task)
            else:
                await self._mark(task, 0, "error", str(exc))
            return
        except FirmsSourceError as exc:
            self.disabled_sources[task.source] = str(exc)
            self._note_error(exc)
            await self._mark(task, 0, "error", str(exc))
            return
        except FirmsAuthError as exc:
            self._note_error(exc)
            await self._mark(task, 0, "error", str(exc))
            return
        except FirmsError as exc:
            self._note_error(exc)
            if task.attempt < 2:
                task.attempt += 1
                await asyncio.sleep(3)
                await self._requeue(task)
            else:
                await self._mark(task, 0, "error", str(exc))
            self.failed += 1
            return

        inserted = await asyncio.to_thread(self.db.upsert_detections, rows)
        self.rows_added += inserted
        self.completed += 1
        self.last_success_at = time.time()
        await self._mark(task, len(rows), "ok", None)
        log.info(
            "%s %s %s..%s -> %d rows (%d new)",
            task.source,
            area if area == "world" else f"cell {task.cell}",
            task.days[0],
            task.days[-1],
            len(rows),
            inserted,
        )

    async def _mark(
        self, task: FetchTask, row_count: int, status: str, note: str | None
    ) -> None:
        await asyncio.to_thread(
            self.db.mark_coverage,
            task.source,
            task.cell[0],
            task.cell[1],
            task.days,
            row_count=row_count,
            status=status,
            note=note,
        )

    def _note_error(self, exc: Exception) -> None:
        self.last_error = str(exc)
        self.last_error_at = time.time()
        log.warning("FIRMS fetch problem: %s", exc)

    # ------------------------------------------------------- background jobs

    async def _refresh_loop(self) -> None:
        """Periodically re-pull the hot days for regions already cached."""
        interval = self.settings.refresh_interval_minutes * 60
        while not self._stopping:
            try:
                await asyncio.sleep(interval)
                if not self.settings.has_map_key:
                    continue
                await self.refresh_cached_regions()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                log.exception("Refresh loop error")

    async def refresh_cached_regions(self) -> int:
        """Re-fetch the most recent days for every region seen before."""
        today = utc_today()
        hot_days = [
            (today - timedelta(days=n)).isoformat() for n in range(self.settings.hot_days)
        ]
        min_day = (today - timedelta(days=self.settings.cache_days)).isoformat()
        rows = await asyncio.to_thread(self.db.cached_cells, min_day)

        queued = 0
        for row in rows:
            source = row["source"]
            if source in self.disabled_sources:
                continue
            cell = (row["cell_x"], row["cell_y"])
            missing = await asyncio.to_thread(
                self._days_needing_fetch, source, cell, hot_days
            )
            if not missing:
                continue
            for chunk in chunk_days(missing, self.effective_day_range):
                task = FetchTask(priority=0, source=source, cell=cell, days=chunk)
                async with self._lock:
                    if task.key in self._inflight:
                        continue
                    self._inflight.add(task.key)
                    self._queue.put_nowait(task)
                    queued += 1
        if queued:
            log.info("Refresh queued %d fetches", queued)
        return queued

    async def _purge_loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(3600)
                await asyncio.to_thread(self.purge_now)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                log.exception("Purge loop error")

    def purge_now(self) -> tuple[int, int]:
        cutoff = utc_today() - timedelta(days=self.settings.cache_days)
        detections, coverage = self.db.purge_older_than(cutoff)
        if detections or coverage:
            log.info(
                "Purged %d detections and %d coverage rows older than %s",
                detections,
                coverage,
                cutoff.isoformat(),
            )
        return detections, coverage

    # --------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        return {
            "pending": self.pending,
            "active": self.active,
            "queued": self._queue.qsize(),
            "completed": self.completed,
            "failed": self.failed,
            "rows_added": self.rows_added,
            "effective_day_range": self.effective_day_range,
            "disabled_sources": self.disabled_sources,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "last_success_at": self.last_success_at,
            "rate_limited": self.rate_limited_until > time.time(),
        }
