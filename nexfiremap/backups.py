"""Verified, recoverable SQLite backups for the local command database.

Two related but separate mechanisms live here:

* **Backups** (`create_backup`/`list_backups`/`verify`): periodic snapshots
  of the live database, written via SQLite's own online backup API (safe to
  run against a database that's being written to concurrently), verified
  with ``PRAGMA integrity_check`` before being kept, and pruned to the most
  recent ``keep`` count.
* **Recoveries** (`create_recovery`/`list_recoveries`): a *separate*,
  independently verified copy materialized from one specific backup, meant
  for a human to inspect/download/activate elsewhere - never written back
  over the running application's own database file. Each recovery carries a
  JSON sidecar recording which backup it came from, since the recovery
  filename itself (a random UUID) doesn't.

Both write paths follow the same pattern: write to a private temporary file,
verify it, then ``os.replace`` it into its final name. That last step is
atomic on both POSIX and Windows, so a reader (or a crash) never observes a
half-written backup/recovery file under its real name - only a fully
verified one, or none at all.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import Settings
from .db import Database

# Filenames are generated exclusively by this module (timestamp/uuid, never
# user input), but every path derived from a name is still re-validated
# against these patterns before touching disk - see _resolve/_recovery_path,
# which also guard against the resulting path escaping its own directory.
BACKUP_RE = re.compile(r"^nexfiremap-\d{8}T\d{12}Z\.sqlite3$")
RECOVERY_RE = re.compile(r"^recovery-[0-9a-f]{32}\.sqlite3$")


class BackupManager:
    """Owns the backup/recovery directories, the scheduled-backup loop, and
    the lock serializing every write into either directory (so a scheduled
    backup and a manually triggered one, or two recoveries, can't race and
    clobber each other's partial files)."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.db = db
        self.directory = settings.backup_dir.resolve()
        self.recovery_directory = (self.directory / "recoveries").resolve()
        self.keep = settings.backup_keep
        self.interval_s = settings.backup_interval_minutes * 60
        self._lock = threading.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.last_error: str | None = None
        self.last_success: dict[str, Any] | None = None

    async def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.interval_s > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="verified-db-backups")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        """Background scheduled-backup loop. Waits on ``self._stop`` with a
        timeout rather than plain ``sleep`` so `stop()` can end the loop
        promptly instead of waiting out a possibly long interval; a timeout
        (the expected case) means it's time to back up, while the event
        actually being set means shutdown - `break` out immediately either
        way once stop fires. Runs the (blocking, sqlite-file-copying)
        backup in a thread so it never stalls the event loop.
        """
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
                break
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.to_thread(self.create_backup, "scheduled")
            except Exception as exc:  # noqa: BLE001 - recorded for operator status
                self.last_error = str(exc)

    def _resolve(self, name: str) -> Path:
        """Turns a backup filename into a validated on-disk path. Rejects
        anything not matching the exact generated-name shape (blocks
        traversal via e.g. ``../``) and, belt-and-suspenders, confirms the
        resolved path still lands inside ``self.directory``."""
        if not BACKUP_RE.fullmatch(name):
            raise ValueError("invalid backup filename")
        path = (self.directory / name).resolve()
        if not path.is_relative_to(self.directory):
            raise ValueError("invalid backup path")
        return path

    @staticmethod
    def _integrity(path: Path) -> str:
        """Runs SQLite's own ``PRAGMA integrity_check`` against a database
        file and reduces it to "ok" or a short joined summary of what's
        wrong - the single source of truth `create_backup`/`create_recovery`/
        `verify` all gate on before trusting a copy."""
        conn = sqlite3.connect(path, timeout=30.0)
        try:
            rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()]
            return "ok" if rows == ["ok"] else "; ".join(rows[:10])
        finally:
            conn.close()

    def create_backup(self, reason: str = "manual") -> dict[str, Any]:
        """Snapshot the live database, verify it, then atomically publish it
        under its final name; prunes older backups afterward. ``reason`` is
        purely informational ("manual" vs. "scheduled") and only shows up in
        the returned/recorded status - it doesn't change the backup itself.
        """
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            name = f"nexfiremap-{stamp}.sqlite3"
            final = self._resolve(name)
            partial = (self.directory / f".partial-{uuid4().hex}.sqlite3").resolve()
            if not partial.is_relative_to(self.directory):  # defensive; directory is resolved
                raise RuntimeError("backup temporary path escaped backup directory")
            source = destination = None
            try:
                # sqlite3's Connection.backup() is the online backup API -
                # copies page-by-page under SQLite's own locking, safe to run
                # against a database that other connections keep writing to,
                # unlike a raw file copy which could grab a torn snapshot.
                source = sqlite3.connect(self.db.path, timeout=30.0)
                destination = sqlite3.connect(partial, timeout=30.0)
                source.backup(destination, pages=512)
                destination.commit()
                destination.close(); destination = None
                integrity = self._integrity(partial)
                if integrity != "ok":
                    raise RuntimeError(f"backup integrity check failed: {integrity}")
                # Atomic rename: any reader either sees no file at this final
                # name yet, or a fully-written, already-verified one - never
                # a partially-written backup.
                os.replace(partial, final)
                result = {
                    "name": name, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "size_bytes": final.stat().st_size, "integrity": "ok", "reason": reason,
                }
                self.last_success = result
                self.last_error = None
                self._prune()
                return result
            finally:
                if destination is not None:
                    destination.close()
                if source is not None:
                    source.close()
                if partial.exists():
                    partial.unlink()

    def _prune(self) -> None:
        """Deletes everything past the ``keep`` most recent backups. Sorting
        by filename (rather than mtime) works because the timestamp is
        embedded in the name and zero-padded, so lexical order equals
        chronological order - and it's robust to a filesystem that doesn't
        preserve mtimes precisely (e.g. across a copy/restore of the backup
        directory itself)."""
        backups = sorted(
            (p for p in self.directory.glob("nexfiremap-*.sqlite3") if BACKUP_RE.fullmatch(p.name)),
            key=lambda p: p.name,
            reverse=True,
        )
        for path in backups[self.keep:]:
            path.unlink()

    def list_backups(self) -> list[dict[str, Any]]:
        """Lists completed backups, newest first. The BACKUP_RE re-check
        here (on top of the glob) is what filters out any leftover
        ``.partial-*`` temp files from an interrupted `create_backup` -
        those never match the finished-backup name pattern, so they're
        invisible to callers rather than showing up as a corrupt entry."""
        self.directory.mkdir(parents=True, exist_ok=True)
        rows = []
        for path in sorted(self.directory.glob("nexfiremap-*.sqlite3"), key=lambda p: p.name, reverse=True):
            if not BACKUP_RE.fullmatch(path.name) or not path.is_file():
                continue
            stat = path.stat()
            rows.append({
                "name": path.name, "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            })
        return rows

    def verify(self, name: str) -> dict[str, Any]:
        """On-demand integrity re-check of an already-created backup - lets
        an operator confirm an older backup is still trustworthy without
        having to actually restore it first."""
        path = self._resolve(name)
        if not path.is_file():
            raise FileNotFoundError(name)
        integrity = self._integrity(path)
        return {"name": name, "integrity": integrity, "ok": integrity == "ok", "size_bytes": path.stat().st_size}

    def path_for_download(self, name: str) -> Path:
        path = self._resolve(name)
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def _recovery_path(self, name: str) -> Path:
        """Same filename-then-directory-containment validation as
        `_resolve`, against the separate recovery directory and pattern."""
        if not RECOVERY_RE.fullmatch(name):
            raise ValueError("invalid recovery filename")
        path = (self.recovery_directory / name).resolve()
        if not path.is_relative_to(self.recovery_directory):
            raise ValueError("invalid recovery path")
        return path

    def create_recovery(self, backup_name: str) -> dict[str, Any]:
        """Materialize a backup as a separate verified database.

        This deliberately never targets ``self.db.path``. Recovery files are
        intended for inspection/download and activation on a clean process or
        second machine, not hot replacement beneath the running application.
        """
        source_path = self._resolve(backup_name)
        if not source_path.is_file():
            raise FileNotFoundError(backup_name)
        # Checked twice: here against the source backup (no point copying a
        # backup that's already corrupt) and again below against the fresh
        # copy (the copy itself could still fail/truncate independently).
        source_integrity = self._integrity(source_path)
        if source_integrity != "ok":
            raise RuntimeError(f"source backup integrity check failed: {source_integrity}")
        with self._lock:
            self.recovery_directory.mkdir(parents=True, exist_ok=True)
            recovery_name = f"recovery-{uuid4().hex}.sqlite3"
            final = self._recovery_path(recovery_name)
            partial = self.recovery_directory / f".{recovery_name}.partial"
            source = destination = None
            try:
                source = sqlite3.connect(source_path, timeout=30.0)
                destination = sqlite3.connect(partial, timeout=30.0)
                source.backup(destination, pages=512)
                destination.commit(); destination.close(); destination = None
                integrity = self._integrity(partial)
                if integrity != "ok":
                    raise RuntimeError(f"recovery integrity check failed: {integrity}")
                os.replace(partial, final)
                result = {
                    "name": recovery_name, "source_backup": backup_name,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "size_bytes": final.stat().st_size, "integrity": "ok",
                }
                # The recovery filename alone (a random UUID) can't say which
                # backup it came from, so that provenance is recorded in a
                # JSON sidecar next to it - written the same partial-then-
                # atomic-rename way so it's never seen half-written either.
                sidecar = final.with_suffix(".json")
                sidecar_partial = sidecar.with_suffix(".json.partial")
                sidecar_partial.write_text(json.dumps(result, indent=2), encoding="utf-8")
                os.replace(sidecar_partial, sidecar)
                return result
            finally:
                if destination is not None: destination.close()
                if source is not None: source.close()
                if partial.exists(): partial.unlink()

    def list_recoveries(self) -> list[dict[str, Any]]:
        """Lists recoveries, newest first, reading each one's provenance
        back from its JSON sidecar. Falls back to a minimal record (no
        ``source_backup``) if the sidecar is missing or unreadable, rather
        than dropping the recovery file from the listing entirely - the
        underlying database is still there and usable even if its metadata
        isn't."""
        if not self.recovery_directory.is_dir():
            return []
        rows = []
        for path in sorted(self.recovery_directory.glob("recovery-*.sqlite3"),
                           key=lambda item: item.stat().st_mtime, reverse=True):
            if not RECOVERY_RE.fullmatch(path.name):
                continue
            sidecar = path.with_suffix(".json")
            try:
                item = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                item = {"name": path.name, "source_backup": None,
                        "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
            item["size_bytes"] = path.stat().st_size
            rows.append(item)
        return rows

    def recovery_path_for_download(self, name: str) -> Path:
        path = self._recovery_path(name)
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def status(self) -> dict[str, Any]:
        """Operator-facing summary for a status panel: whether scheduled
        backups are enabled at all, the retention settings, and the
        most recent success/failure - enough to answer "is backup healthy"
        at a glance without listing every backup."""
        backups = self.list_backups()
        return {
            "enabled": self.interval_s > 0, "interval_minutes": self.interval_s // 60,
            "keep": self.keep, "count": len(backups), "latest": backups[0] if backups else None,
            "last_success": self.last_success, "last_error": self.last_error,
        }
