"""Smoke test for the background job queue (Phase 0).

Verifies a job actually runs in a separate process (ProcessPoolExecutor),
reports progress while running, survives being looked up mid-flight, and
lands in the SQLite jobs table with its result - the full path every later
phase (event clustering, likelihood rasters, ...) will build on.

Run with:  python tests/test_jobs.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.config import Settings
from nexfiremap.db import Database
from nexfiremap.jobs import JobContext, JobManager, JobQueueFull, register_kind

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name} {detail}")


def _slow_squares(params: dict, ctx: JobContext) -> dict:
    """A tiny stand-in for a real analysis job: reports progress, writes a
    result file, returns a small JSON-able summary - the exact shape later
    phases (likelihood rasters, dNBR, propagation) will follow."""
    n = int(params.get("n", 5))
    values = []
    for i in range(n):
        time.sleep(0.05)
        values.append(i * i)
        ctx.report_progress(int((i + 1) / n * 100), note=f"squared {i}")
    out_file = ctx.result_path / "values.txt"
    out_file.write_text(",".join(map(str, values)))
    return {"sum": sum(values), "file": out_file.name}


def _boom(params: dict, ctx: JobContext) -> dict:
    raise RuntimeError("deliberate failure for testing")


register_kind("test_slow_squares", _slow_squares)
register_kind("test_boom", _boom)


def make_manager(tmp: Path) -> JobManager:
    settings = Settings(
        map_key="",
        host="127.0.0.1",
        port=8000,
        db_path=tmp / "cache.sqlite3",
        cache_days=30,
        job_dir=tmp / "jobs",
        job_workers=2,
        job_retention_days=14,
    )
    db = Database(settings.db_path)
    return JobManager(settings, db)


async def wait_for(manager: JobManager, job_id: int, timeout: float = 15.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        row = manager.db.get_job(job_id)
        if row["status"] in ("done", "error"):
            return dict(row)
        await asyncio.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish in time")


async def test_success(tmp: Path) -> None:
    print("\nJob runs to completion with progress reporting")
    manager = make_manager(tmp / "a")
    await manager.start()

    job_id = await manager.submit("test_slow_squares", {"n": 5})
    check("job id assigned", isinstance(job_id, int) and job_id > 0)

    row = manager.db.get_job(job_id)
    check("starts queued or running", row["status"] in ("queued", "running"), row["status"])

    # Progress should move while the job runs (worker self-reports via its
    # own sqlite connection - this is the cross-process path being tested).
    saw_progress = False
    for _ in range(40):
        await asyncio.sleep(0.05)
        row = manager.db.get_job(job_id)
        if row["progress"] and row["progress"] > 0:
            saw_progress = True
            break
    check("worker process reported progress", saw_progress, str(dict(row)))

    final = await wait_for(manager, job_id)
    check("job finished successfully", final["status"] == "done", final["status"])
    check("progress reached 100", final["progress"] == 100, str(final["progress"]))

    import json

    result = json.loads(final["result_json"])
    check("result sum correct", result["sum"] == sum(i * i for i in range(5)), str(result))

    result_file = manager.job_dir / str(job_id) / result["file"]
    check("result file written to job dir", result_file.is_file(), str(result_file))

    await manager.stop()
    manager.db.close()


async def test_failure(tmp: Path) -> None:
    print("\nFailing job is recorded, not left hanging")
    manager = make_manager(tmp / "b")
    await manager.start()

    job_id = await manager.submit("test_boom", {})
    final = await wait_for(manager, job_id)
    check("status is error", final["status"] == "error", final["status"])
    check("error message captured", "deliberate failure" in (final["error"] or ""), final["error"])

    await manager.stop()
    manager.db.close()


async def test_unknown_kind(tmp: Path) -> None:
    print("\nUnknown kind rejected at submission")
    manager = make_manager(tmp / "c")
    await manager.start()

    raised = False
    try:
        await manager.submit("does_not_exist", {})
    except ValueError:
        raised = True
    check("ValueError raised for unknown kind", raised)

    await manager.stop()
    manager.db.close()


async def test_concurrency(tmp: Path) -> None:
    print("\nMultiple jobs run concurrently, not serialized")
    manager = make_manager(tmp / "d")
    await manager.start()

    start = time.time()
    ids = [await manager.submit("test_slow_squares", {"n": 8}) for _ in range(3)]
    for job_id in ids:
        await wait_for(manager, job_id, timeout=20.0)
    elapsed = time.time() - start

    # 3 jobs * 8 steps * 50ms = 1.2s of pure work if fully serialized. With 2
    # workers it should clearly beat that even after Windows' slow (100-300ms)
    # process-spawn overhead is added in - generous margin to avoid flakiness.
    check("ran faster than fully serial", elapsed < 1.9, f"{elapsed:.2f}s")

    await manager.stop()
    manager.db.close()


async def test_orphan_recovery(tmp: Path) -> None:
    print("\nJobs stuck 'running' from a prior crash are marked failed on start")
    dir_ = tmp / "e"
    settings = Settings(
        map_key="",
        host="127.0.0.1",
        port=8000,
        db_path=dir_ / "cache.sqlite3",
        cache_days=30,
        job_dir=dir_ / "jobs",
        job_workers=1,
    )
    db = Database(settings.db_path)
    job_id = db.create_job("test_slow_squares", {"n": 1})
    db.update_job(job_id, status="running", started_at=int(time.time()))

    manager = JobManager(settings, db)
    await manager.start()
    row = manager.db.get_job(job_id)
    check("orphaned running job marked error", row["status"] == "error", row["status"])
    await manager.stop()
    db.close()


async def test_pool_recovery(tmp: Path) -> None:
    print("\nA killed worker breaks the pool once, then the queue recovers on its own")
    # Reproduces a real bug found live: a worker process died mid-job (OS
    # kill, not a Python exception in the job itself) and every job
    # submitted afterwards failed the same way forever, because a
    # ProcessPoolExecutor stays permanently unusable once broken unless
    # something replaces it - see JobManager._recreate_pool.
    manager = make_manager(tmp / "f")
    await manager.start()

    # Get at least one worker process alive and tracked by the pool.
    warm_id = await manager.submit("test_slow_squares", {"n": 1})
    await wait_for(manager, warm_id)

    # A job that runs long enough to kill mid-flight - then actually kill
    # its worker process, the same failure mode a crash/OOM/AV-kill
    # produces, not a simulated exception.
    victim_id = await manager.submit("test_slow_squares", {"n": 40})
    await asyncio.sleep(0.2)
    pids = list(manager._pool._processes.keys())
    check("at least one worker process exists to kill", len(pids) > 0, str(pids))
    if pids:
        manager._pool._processes[pids[0]].kill()

    final = await wait_for(manager, victim_id, timeout=15.0)
    check("killed job recorded as error, not left hanging forever", final["status"] == "error", str(final))
    check(
        "error explains a worker crashed, not the job's own logic",
        "killed unexpectedly" in (final["error"] or ""),
        final["error"],
    )

    # The whole point: the queue must have healed itself, not stayed broken.
    recovered_id = await manager.submit("test_slow_squares", {"n": 3})
    recovered = await wait_for(manager, recovered_id, timeout=15.0)
    check(
        "queue self-healed: a job submitted after the crash still succeeds",
        recovered["status"] == "done",
        str(recovered),
    )

    await manager.stop()
    manager.db.close()


async def test_cancel(tmp: Path) -> None:
    print("\nCancel: a queued job never starts, a running one still stops")
    dir_ = tmp / "g"
    settings = Settings(
        map_key="",
        host="127.0.0.1",
        port=8000,
        db_path=dir_ / "cache.sqlite3",
        cache_days=30,
        job_dir=dir_ / "jobs",
        job_workers=1,
    )
    db = Database(settings.db_path)
    manager = JobManager(settings, db)
    await manager.start()

    # A single worker: the second job is genuinely queued behind the first,
    # blocked on _run_slots rather than merely an asyncio task that exists.
    busy_id = await manager.submit("test_slow_squares", {"n": 20})
    queued_id = await manager.submit("test_slow_squares", {"n": 1})
    await asyncio.sleep(0.1)
    check(
        "second job is queued, not running",
        db.get_job(queued_id)["status"] == "queued",
        db.get_job(queued_id)["status"],
    )

    ok = await manager.cancel(queued_id)
    check("cancel() reports success for a queued job", ok)
    final = await wait_for(manager, queued_id, timeout=5.0)
    check(
        "queued job marked cancelled without ever running",
        final["status"] == "error" and final["error"] == "Cancelled" and final["started_at"] is None,
        str(final),
    )

    check("first job is running", db.get_job(busy_id)["status"] == "running", db.get_job(busy_id)["status"])
    ok = await manager.cancel(busy_id)
    check("cancel() reports success for a running job", ok)
    final = await wait_for(manager, busy_id, timeout=15.0)
    check(
        "running job eventually marked cancelled",
        final["status"] == "error" and final["error"] == "Cancelled",
        str(final),
    )

    check("cancel() on an already-finished job reports failure", await manager.cancel(busy_id) is False)

    await manager.stop()
    db.close()


async def test_queue_full(tmp: Path) -> None:
    print("\nA flood of submissions is rejected once the queue backstop is hit")
    dir_ = tmp / "h"
    settings = Settings(
        map_key="",
        host="127.0.0.1",
        port=8000,
        db_path=dir_ / "cache.sqlite3",
        cache_days=30,
        job_dir=dir_ / "jobs",
        job_workers=1,
        job_queue_max=3,
    )
    db = Database(settings.db_path)
    manager = JobManager(settings, db)
    await manager.start()

    # One worker, so everything after the first submission genuinely queues
    # rather than finishing before the count is even checked.
    busy_id = await manager.submit("test_slow_squares", {"n": 20})
    queued_ids = [await manager.submit("test_slow_squares", {"n": 1}) for _ in range(2)]
    check("reached the configured limit without rejection", len(queued_ids) == 2)

    raised = False
    try:
        await manager.submit("test_slow_squares", {"n": 1})
    except JobQueueFull:
        raised = True
    check("next submission past the limit is rejected", raised)

    check("rejection didn't consume a job id / create a job row",
          len(db.list_jobs(limit=100)) == 3, len(db.list_jobs(limit=100)))

    ok = await manager.cancel(busy_id)
    check("cleanup: cancel the still-running job", ok)
    for job_id in queued_ids:
        await manager.cancel(job_id)

    await manager.stop()
    db.close()


async def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        await test_success(tmp)
        await test_failure(tmp)
        await test_unknown_kind(tmp)
        await test_concurrency(tmp)
        await test_orphan_recovery(tmp)
        await test_pool_recovery(tmp)
        await test_cancel(tmp)
        await test_queue_full(tmp)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
