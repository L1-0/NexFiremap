"""Verified, recoverable SQLite backups for the local command database."""

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

BACKUP_RE = re.compile(r"^nexfiremap-\d{8}T\d{12}Z\.sqlite3$")
RECOVERY_RE = re.compile(r"^recovery-[0-9a-f]{32}\.sqlite3$")


class BackupManager:
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
        if not BACKUP_RE.fullmatch(name):
            raise ValueError("invalid backup filename")
        path = (self.directory / name).resolve()
        if not path.is_relative_to(self.directory):
            raise ValueError("invalid backup path")
        return path

    @staticmethod
    def _integrity(path: Path) -> str:
        conn = sqlite3.connect(path, timeout=30.0)
        try:
            rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()]
            return "ok" if rows == ["ok"] else "; ".join(rows[:10])
        finally:
            conn.close()

    def create_backup(self, reason: str = "manual") -> dict[str, Any]:
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
                source = sqlite3.connect(self.db.path, timeout=30.0)
                destination = sqlite3.connect(partial, timeout=30.0)
                source.backup(destination, pages=512)
                destination.commit()
                destination.close(); destination = None
                integrity = self._integrity(partial)
                if integrity != "ok":
                    raise RuntimeError(f"backup integrity check failed: {integrity}")
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
        backups = sorted(
            (p for p in self.directory.glob("nexfiremap-*.sqlite3") if BACKUP_RE.fullmatch(p.name)),
            key=lambda p: p.name,
            reverse=True,
        )
        for path in backups[self.keep:]:
            path.unlink()

    def list_backups(self) -> list[dict[str, Any]]:
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
        backups = self.list_backups()
        return {
            "enabled": self.interval_s > 0, "interval_minutes": self.interval_s // 60,
            "keep": self.keep, "count": len(backups), "latest": backups[0] if backups else None,
            "last_success": self.last_success, "last_error": self.last_error,
        }
