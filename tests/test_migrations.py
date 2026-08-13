"""Versioned-schema, pre-migration backup and rollback-drill checks."""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database, SCHEMA_VERSION


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        legacy = root / "incident.sqlite3"
        conn = sqlite3.connect(legacy)
        conn.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        conn.execute("INSERT INTO legacy_marker VALUES ('before-migration')")
        conn.commit(); conn.close()

        db = Database(legacy)
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        backup = root / "incident.pre-migration-v0.sqlite3"
        assert backup.is_file()
        db.close()

        # Rollback drill: restore the automatic backup to a separate file and
        # prove its pre-migration data is independently readable.
        restored = root / "rollback.sqlite3"
        shutil.copy2(backup, restored)
        check = sqlite3.connect(restored)
        assert check.execute("SELECT value FROM legacy_marker").fetchone()[0] == "before-migration"
        check.close()

        future = root / "future.sqlite3"
        conn = sqlite3.connect(future); conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}"); conn.commit(); conn.close()
        try:
            Database(future)
            raise AssertionError("newer database schema was accepted")
        except RuntimeError:
            pass
    print("Database migration and rollback checks passed.")


if __name__ == "__main__":
    main()
