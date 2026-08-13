"""Background compute job queue.

Everything from Phase 2 onward (event clustering, likelihood rasters,
burn-scar anchoring, propagation modelling) is "expensive - run it off the
request path, at low OS priority, and let the UI poll for a result." This
module is that machinery, built once and reused by every later phase:

- Jobs are rows in the ``jobs`` SQLite table (see db.py) so status survives a
  restart and is easy to inspect.
- Work runs in a ``ProcessPoolExecutor`` - real OS processes, not threads, so
  numpy/scipy-heavy code isn't limited by the GIL and can actually use extra
  cores.
- Each worker process is dropped to idle OS scheduling priority right after
  it starts, so this queue only spends CPU cycles nothing else on the
  machine wants - the literal "deferred until CPU time is available."
- A job is registered by name -> a plain module-level function via
  ``register_kind()``, called by each phase module at import time. The
  function receives its params dict plus a small picklable ``JobContext`` it
  can use to report progress. It returns a small JSON-able result dict
  (larger outputs - rasters, GeoJSON - are written to ``ctx.result_dir`` and
  referenced from the result dict).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .db import Database

log = logging.getLogger("nexfiremap.jobs")

# How long stop() waits for in-flight jobs to unwind cleanly before giving up
# and terminating their worker processes directly - see stop()'s docstring.
SHUTDOWN_GRACE_S = 15.0

# If the worker pool has to be replaced this many times within this window,
# something is systematically crashing workers (a native dependency, an
# out-of-memory condition, ...) rather than one-off bad luck - see
# _replace_pool_locked.
POOL_CHURN_WINDOW_S = 300.0
POOL_CHURN_LIMIT = 5


def _json_safe(value: Any) -> Any:
    """Recursively replace NaN/Infinity with None.

    A job result built from real data can end up with a stray NaN (an empty
    array's .max(), a 0/0 ratio, ...) even when every individual field is
    handled carefully. api.py's ``_json()`` helper serializes job rows with
    ``allow_nan=False`` (matching the JSON spec, which has no NaN literal),
    so a NaN written here would otherwise make every future read of this
    job's row raise and 500, permanently - there'd be no way to retrieve it
    through the API again short of purging it from the database.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(v) for key, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value

class JobQueueFull(RuntimeError):
    """Too many jobs already queued or running - see Settings.job_queue_max."""


JobFunc = Callable[[dict[str, Any], "JobContext"], dict[str, Any]]

# Populated by each phase module at import time via register_kind(). Kept
# process-local - a spawned worker re-imports whatever module owns the
# function it was handed, so this only needs to be populated in the main
# process for submission-time validation.
KIND_REGISTRY: dict[str, JobFunc] = {}


def register_kind(name: str, func: JobFunc) -> None:
    """Make a job kind submittable. Call at module import time.

    ``func`` must be a plain module-level function (not a lambda or bound
    method) - it has to survive being pickled to a worker process.
    """
    if name in KIND_REGISTRY and KIND_REGISTRY[name] is not func:
        log.warning("Job kind %r re-registered with a different function", name)
    KIND_REGISTRY[name] = func


@dataclass(frozen=True)
class JobContext:
    """Handed to a job function. Plain data + a self-contained progress
    reporter - safe to pickle across the process boundary."""

    job_id: int
    db_path: str
    result_dir: str

    @property
    def result_path(self) -> Path:
        path = Path(self.result_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def report_progress(self, pct: int, note: str | None = None) -> None:
        """Called from inside the worker process to update job status."""
        _update_job_row(
            Path(self.db_path), self.job_id, progress=max(0, min(100, int(pct))), note=note
        )


def _update_job_row(db_path: Path, job_id: int, **fields: Any) -> None:
    """Standalone row update using its own short-lived connection.

    Worker processes can't share the API server's ``Database`` object (its
    threading.Lock and thread-local connections don't survive a process
    spawn), so this opens a plain connection, writes, and closes - the WAL
    journal mode already set on the file makes this safe alongside the
    server's own connections.
    """
    if not fields:
        return
    set_clause = ", ".join(f"{key} = ?" for key in fields)
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", [*fields.values(), job_id])
        conn.commit()
    finally:
        conn.close()


def _worker_init() -> None:
    """Runs once per worker process at startup: drop to idle priority."""
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        idle = getattr(psutil, "IDLE_PRIORITY_CLASS", None)
        proc.nice(idle if idle is not None else 19)  # 19 = lowest POSIX niceness
    except Exception:  # pragma: no cover - best effort, never fatal
        log.debug("Could not lower worker process priority", exc_info=True)


def _run_in_worker(func: JobFunc, params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Top-level trampoline so the executor only ever pickles plain data."""
    return func(params, ctx)


def _demo_job(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Built-in kind used to exercise the pipeline end-to-end in tests."""
    steps = int(params.get("steps", 3))
    total = 0
    for i in range(steps):
        total += i * i
        ctx.report_progress(int((i + 1) / steps * 100), note=f"step {i + 1}/{steps}")
    return {"total": total}


register_kind("demo", _demo_job)


class JobManager:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.job_dir = settings.job_dir
        self.job_dir.mkdir(parents=True, exist_ok=True)

        workers = settings.job_workers or max(1, (os.cpu_count() or 2) - 1)
        self.worker_count = workers
        self._pool = ProcessPoolExecutor(max_workers=workers, initializer=_worker_init)
        # Guards recreating self._pool: if several jobs are in flight when a
        # worker dies, every one of their futures breaks at once and each
        # would otherwise race to replace the pool independently.
        self._pool_lock = asyncio.Lock()
        # Timestamps of recent pool replacements, for the churn warning in
        # _replace_pool_locked.
        self._pool_replacements: list[float] = []
        # Caps how many jobs are actually submitted into the pool at once,
        # separately from how many are merely queued as asyncio tasks - see
        # _run's use of this. Matching it 1:1 to worker_count means "holding
        # a slot" is a much closer proxy for "actually running" than "an
        # asyncio task for it exists", which status() used to report as
        # "running" for every submitted job regardless of real backlog.
        self._run_slots = asyncio.Semaphore(workers)

        self._tasks: dict[int, asyncio.Task] = {}
        self._prune_task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        self._prune_task = asyncio.create_task(self._prune_loop(), name="job-prune")
        # Anything left "running" from a previous, uncleanly-stopped process
        # can never finish - mark it failed rather than have it hang forever.
        await asyncio.to_thread(self._requeue_orphans)

    async def stop(self) -> None:
        """Cancel every in-flight job and wait up to SHUTDOWN_GRACE_S for
        them to unwind, then terminate their worker processes directly.

        A cancelled asyncio Task awaiting ``run_in_executor`` for a work
        item that's *already executing* in a worker doesn't actually get
        cancelled promptly: ``concurrent.futures.Future.cancel()`` only
        succeeds for items that haven't started yet, so cancellation just
        sits there until the executor future completes on its own. Without
        the timeout below, a single long-running ensemble/propagation job
        could make server shutdown hang for its entire remaining duration.
        """
        self._stopping = True
        if self._prune_task:
            self._prune_task.cancel()
        for task in list(self._tasks.values()):
            task.cancel()

        pending = [*([self._prune_task] if self._prune_task else []), *self._tasks.values()]
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=SHUTDOWN_GRACE_S
                )
            except asyncio.TimeoutError:
                still_running = [jid for jid, t in self._tasks.items() if not t.done()]
                log.warning(
                    "Shutdown: %d job(s) still running after a %.0fs grace period (%s) - "
                    "terminating their worker processes directly",
                    len(still_running),
                    SHUTDOWN_GRACE_S,
                    still_running,
                )

        # Discarding the pool object alone (shutdown(wait=False)) doesn't
        # kill an already-running worker - it would keep executing as an
        # orphan after this process exits. _processes is private API (no
        # public equivalent exists), used the same way _recreate_pool
        # already relies on _broken.
        for proc in list(getattr(self._pool, "_processes", {}).values()):  # noqa: SLF001
            if proc.is_alive():
                proc.terminate()
        self._pool.shutdown(wait=False, cancel_futures=True)

    async def _recreate_pool(self) -> None:
        """A ``ProcessPoolExecutor`` stays permanently unusable once a worker
        dies unexpectedly (an OS-level kill, not a normal Python exception in
        the job itself - those are caught fine without ever reaching here) -
        that's how ``concurrent.futures`` is documented to behave, not a bug
        in this project. Without this, one crashed worker would quietly take
        down every future job for the rest of the process's life, with no
        symptom beyond every subsequent submission failing the same way.
        Guarded by a lock so concurrent jobs hitting the same broken pool
        only trigger one replacement, not one per failed job."""
        async with self._pool_lock:
            if not self._pool._broken:  # noqa: SLF001 - concurrent.futures exposes no public check
                return  # another caller already replaced it
            await self._replace_pool_locked("worker pool broke (a worker was killed)")

    async def _abandon_pool(self, reason: str) -> None:
        """Unconditionally swap in a fresh pool - used when a single job
        blows past its execution timeout. The stuck worker process isn't
        forcibly killed here (there's no reliable, version-stable way to
        map a still-running future back to its exact worker pid the way
        stop() can for *all* of them at once via _processes), but it stops
        being handed any more work: everything submitted after this point
        runs on a clean pool instead of queuing up behind it. The old
        worker exits on its own once its current item finishes."""
        async with self._pool_lock:
            await self._replace_pool_locked(reason)

    async def _replace_pool_locked(self, reason: str) -> None:
        """Caller must hold self._pool_lock."""
        now = time.time()
        self._pool_replacements = [
            t for t in self._pool_replacements if now - t < POOL_CHURN_WINDOW_S
        ]
        self._pool_replacements.append(now)
        if len(self._pool_replacements) >= POOL_CHURN_LIMIT:
            log.critical(
                "Job worker pool replaced %d times in the last %.0f minutes (latest: %s) - "
                "this looks systemic (a native dependency, an out-of-memory condition, "
                "a crash-on-every-input job kind, ...), not one-off bad luck. Restarting "
                "the server and investigating is recommended rather than expecting this "
                "to keep self-healing.",
                len(self._pool_replacements),
                POOL_CHURN_WINDOW_S / 60,
                reason,
            )
        else:
            log.error("Replacing job worker pool: %s", reason)
        old_pool = self._pool
        self._pool = ProcessPoolExecutor(max_workers=self.worker_count, initializer=_worker_init)
        await asyncio.to_thread(old_pool.shutdown, wait=False, cancel_futures=True)

    def _requeue_orphans(self) -> None:
        for row in self.db.list_jobs(status="running", limit=1000):
            self.db.update_job(
                row["id"],
                status="error",
                error="Interrupted by server restart",
                finished_at=int(time.time()),
            )

    # ------------------------------------------------------------- submit

    async def submit(self, kind: str, params: dict[str, Any]) -> int:
        if kind not in KIND_REGISTRY:
            raise ValueError(f"Unknown job kind: {kind!r}")

        def _create() -> int:
            # The run-slot semaphore only caps concurrent execution, not how
            # many jobs can pile up waiting for one - this is the actual
            # backstop against a flood of submissions (buggy retry loop,
            # stuck double-submit, deliberate abuse) exhausting disk (every
            # job gets its own directory) or memory before any of them ever
            # run. This count-then-insert isn't atomic against another
            # concurrent submission also passing the check before either
            # commits, so a burst can overshoot the limit by a little - fine
            # for a soft backstop against unbounded growth, not meant to be
            # an exact ceiling.
            pending = len(self.db.list_jobs(status="queued", limit=self.settings.job_queue_max + 1)) + \
                len(self.db.list_jobs(status="running", limit=self.settings.job_queue_max + 1))
            if pending >= self.settings.job_queue_max:
                raise JobQueueFull(
                    f"{pending} jobs are already queued or running (limit {self.settings.job_queue_max}) - "
                    "wait for some to finish before submitting more"
                )
            job_id = self.db.create_job(kind, params)
            (self.job_dir / str(job_id)).mkdir(parents=True, exist_ok=True)
            return job_id

        job_id = await asyncio.to_thread(_create)
        task = asyncio.create_task(self._run(job_id, kind, params), name=f"job-{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t, jid=job_id: self._tasks.pop(jid, None))
        return job_id

    async def cancel(self, job_id: int) -> bool:
        """Cancel a job if it's still tracked as queued or running.

        Returns whether a cancellation was actually requested. ``_tasks``
        is in-memory only, so this can't reach a job from before the last
        restart (that case is already handled separately - see
        _requeue_orphans). If the job is queued waiting on ``_run_slots``,
        this cancels promptly. If it's already executing in a worker
        process, the same limitation ``stop()`` documents applies (the
        underlying executor future can't be interrupted mid-flight), so the
        job is marked cancelled once its current step yields control back
        to _run's except-CancelledError handler, not necessarily instantly.
        """
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def _run(self, job_id: int, kind: str, params: dict[str, Any]) -> None:
        func = KIND_REGISTRY[kind]
        ctx = JobContext(
            job_id=job_id,
            db_path=str(self.db.path),
            result_dir=str(self.job_dir / str(job_id)),
        )
        try:
            await self._run_inner(job_id, kind, params, func, ctx)
        except asyncio.CancelledError:
            # Catches cancellation from *either* still being queued (blocked
            # on _run_slots.acquire(), inside the "async with" below but
            # before the executor call even starts) or from an executor
            # future that did honour the cancel - see cancel()'s docstring
            # for the case where it can't. A single handler here (rather
            # than one nested inside _run_inner's own try/except, as this
            # used to be) means a job cancelled before ever reaching a
            # worker still gets its DB row marked instead of just vanishing
            # with the row stuck at "queued" forever.
            await asyncio.to_thread(
                self.db.update_job,
                job_id,
                status="error",
                error="Cancelled",
                finished_at=int(time.time()),
            )
            raise

    async def _run_inner(
        self, job_id: int, kind: str, params: dict[str, Any], func: JobFunc, ctx: JobContext
    ) -> None:
        # Wait for one of worker_count slots rather than submitting straight
        # into the pool: the job's DB row stays "queued" (its status since
        # create_job) for as long as this genuinely has to wait its turn,
        # instead of flipping to "running" the instant this coroutine
        # happened to start - which status() then reported as "running" for
        # every submitted job at once, regardless of how many the pool
        # could actually run concurrently.
        async with self._run_slots:
            await asyncio.to_thread(
                self.db.update_job, job_id, status="running", started_at=int(time.time())
            )
            loop = asyncio.get_running_loop()
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(self._pool, _run_in_worker, func, params, ctx),
                    timeout=self.settings.job_timeout_s,
                )
            except asyncio.TimeoutError:
                log.warning(
                    "Job %d (%s) exceeded the %ds execution limit - abandoning it",
                    job_id,
                    kind,
                    self.settings.job_timeout_s,
                )
                await asyncio.to_thread(
                    self.db.update_job,
                    job_id,
                    status="error",
                    error=(
                        f"Job exceeded the {self.settings.job_timeout_s}s execution limit "
                        "and was abandoned. The worker pool has been replaced so other "
                        "jobs aren't blocked by it; the abandoned worker process will "
                        "exit on its own once its current step finishes."
                    ),
                    finished_at=int(time.time()),
                    progress=0,
                )
                await self._abandon_pool(f"job {job_id} ({kind}) exceeded its execution timeout")
                return
            except BrokenProcessPool as exc:
                # A worker process died (OS-level kill/crash - antivirus,
                # out-of-memory, a Windows power/efficiency policy suspending
                # an idle-priority process, ...), not a normal exception
                # inside the job. The executor is now permanently unusable -
                # replace it so every job after this one isn't silently
                # doomed too.
                log.warning("Job %d (%s): worker process died (%s)", job_id, kind, exc)
                await asyncio.to_thread(
                    self.db.update_job,
                    job_id,
                    status="error",
                    error=(
                        "A background worker process was killed unexpectedly (not a bug "
                        f"in this job's own logic): {exc}. The job queue has recovered "
                        "automatically - please retry."
                    ),
                    finished_at=int(time.time()),
                    progress=0,
                )
                await self._recreate_pool()
                return
            except Exception as exc:  # noqa: BLE001 - surface any worker failure
                log.warning("Job %d (%s) failed: %s", job_id, kind, exc)
                await asyncio.to_thread(
                    self.db.update_job,
                    job_id,
                    status="error",
                    error=str(exc)[:2000],
                    finished_at=int(time.time()),
                    progress=0,
                )
                return

        await asyncio.to_thread(
            self.db.update_job,
            job_id,
            status="done",
            progress=100,
            result_json=json.dumps(_json_safe(result)),
            finished_at=int(time.time()),
        )

    # -------------------------------------------------------------- upkeep

    async def _prune_loop(self) -> None:
        interval = 3600
        while not self._stopping:
            try:
                await asyncio.sleep(interval)
                await asyncio.to_thread(self._prune_now)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                log.exception("Job prune error")

    def _prune_now(self) -> int:
        removed = self.db.purge_old_jobs(self.settings.job_retention_days * 86400)
        for child in self.job_dir.iterdir():
            if not child.is_dir():
                continue
            try:
                job_id = int(child.name)
            except ValueError:
                continue
            if self.db.get_job(job_id) is None:
                shutil.rmtree(child, ignore_errors=True)
        return removed

    # --------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        now = time.time()
        recent_replacements = sum(
            1 for t in self._pool_replacements if now - t < POOL_CHURN_WINDOW_S
        )
        return {
            "workers": self.worker_count,
            "active": len(self._tasks),
            "queued": len(self.db.list_jobs(status="queued", limit=1000)),
            "running": len(self.db.list_jobs(status="running", limit=1000)),
            # Repeated replacements point at a systemic problem (a crashing
            # native dependency, an OOM condition, ...) rather than one-off
            # bad luck - see _replace_pool_locked's log.critical threshold.
            "pool_replacements_recent": recent_replacements,
        }
