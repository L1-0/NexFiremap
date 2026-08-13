"""Verified operational backup tests; no network required."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.backups import BackupManager
from nexfiremap.config import Settings
from nexfiremap.db import Database
from nexfiremap.operations import OperationsStore


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = Settings(map_key="", host="127.0.0.1", port=8000,
                            db_path=root / "live.sqlite3", cache_days=30,
                            backup_dir=root / "backups", backup_interval_minutes=0,
                            backup_keep=2)
        db = Database(settings.db_path)
        incident = OperationsStore(db).create_incident({"name": "Backup Drill"}, "test")
        manager = BackupManager(settings, db)
        made = [manager.create_backup("test") for _ in range(3)]
        backups = manager.list_backups()
        assert len(backups) == 2
        assert made[-1]["integrity"] == "ok"
        verified = manager.verify(backups[0]["name"])
        assert verified["ok"] is True
        restored = sqlite3.connect(manager.path_for_download(backups[0]["name"]))
        try:
            row = restored.execute("SELECT name FROM incidents WHERE id=?", (incident["id"],)).fetchone()
            assert row == ("Backup Drill",)
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            restored.close()
        recovery = manager.create_recovery(backups[0]["name"])
        assert recovery["integrity"] == "ok" and recovery["source_backup"] == backups[0]["name"]
        assert manager.list_recoveries()[0]["name"] == recovery["name"]
        recovery_path = manager.recovery_path_for_download(recovery["name"])
        assert recovery_path != settings.db_path and recovery_path != manager.path_for_download(backups[0]["name"])
        recovered = sqlite3.connect(recovery_path)
        try:
            assert recovered.execute("SELECT name FROM incidents WHERE id=?", (incident["id"],)).fetchone() == ("Backup Drill",)
            assert recovered.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            recovered.close()
        published_before = len(manager.list_recoveries())
        manager.path_for_download(backups[-1]["name"]).write_bytes(b"not sqlite")
        try:
            manager.create_recovery(backups[-1]["name"])
            raise AssertionError("corrupt backup produced a recovery database")
        except (RuntimeError, sqlite3.DatabaseError):
            pass
        assert len(manager.list_recoveries()) == published_before
        try:
            manager.verify("../live.sqlite3")
            raise AssertionError("unsafe backup path accepted")
        except ValueError:
            pass
        assert list((root / "backups").glob(".partial-*")) == []
        db.close()
    print("Verified backup checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
