"""Versioned-schema, pre-migration backup and rollback-drill checks.

Covers both halves of nexfiremap/db.py's migration system:

* the *policy* around it - a pre-existing older database is backed up before
  anything touches it, a newer-than-supported database is refused, a fresh
  one needs neither;
* the *ladder* itself - ordered `MigrationStep`s applied one version at a
  time, resuming correctly from any starting version, never re-running a
  step the file has already seen, and never advancing `user_version` past a
  step whose body raised.
"""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap import db as db_module
from nexfiremap.db import (
    Database,
    MigrationStep,
    MIGRATIONS,
    SCHEMA_VERSION,
    apply_migrations,
)


# --------------------------------------------------------------- helpers


@contextlib.contextmanager
def patched_ladder(steps, version: int):
    """Swap in a fake migration ladder for the duration of a block.

    `Database._init_schema` looks both names up as module globals at call
    time, so patching them here exercises the real startup path - backup
    included - against a ladder we control."""
    saved_steps, saved_version = db_module.MIGRATIONS, db_module.SCHEMA_VERSION
    db_module.MIGRATIONS, db_module.SCHEMA_VERSION = tuple(steps), version
    try:
        yield
    finally:
        db_module.MIGRATIONS, db_module.SCHEMA_VERSION = saved_steps, saved_version


# A deliberately order-dependent three-step ladder: step 6 needs the table
# step 5 creates, and step 7 needs the column step 6 adds. If the runner ever
# applied these out of sequence the SQL itself would fail, which is a far
# stronger ordering check than counting calls.
_STEP_FIVE_SQL = """
CREATE TABLE ladder_note (
    id    INTEGER PRIMARY KEY,
    label TEXT NOT NULL
);
INSERT INTO ladder_note (id, label) VALUES (1, 'five');
"""

_STEP_SEVEN_SQL = """
-- leading comment, then two statements: exercises the statement splitter
CREATE INDEX idx_ladder_note_label ON ladder_note (label);
INSERT INTO ladder_note (id, label, label_upper) VALUES (3, 'seven', 'SEVEN');
"""


def _step_six(conn: sqlite3.Connection) -> None:
    """The 'real migration' shape: alter a table, then backfill it."""
    conn.execute("ALTER TABLE ladder_note ADD COLUMN label_upper TEXT")
    conn.execute("UPDATE ladder_note SET label_upper = UPPER(label)")
    conn.execute("INSERT INTO ladder_note (id, label, label_upper) VALUES (2, 'six', 'SIX')")


def _fake_ladder() -> tuple[MigrationStep, ...]:
    # Declared out of order on purpose: the runner sorts by version, so a
    # step appended in the wrong place must not apply in the wrong place.
    return (
        MigrationStep(7, "index + row depending on step 6's column", _STEP_SEVEN_SQL),
        MigrationStep(5, "create ladder_note", _STEP_FIVE_SQL),
        MigrationStep(6, "add label_upper and backfill", _step_six),
    )


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _ladder_rows(conn: sqlite3.Connection) -> list[tuple]:
    # tuple() so the result compares equal whatever row factory the caller's
    # connection uses (Database sets sqlite3.Row).
    return [
        tuple(row)
        for row in conn.execute("SELECT id, label, label_upper FROM ladder_note ORDER BY id")
    ]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


# ------------------------------------------------------- existing coverage


def check_backup_and_downgrade_refusal(root: Path) -> None:
    """Original coverage: a legacy file migrates forward, leaves a restorable
    pre-migration backup, and a future-schema file is refused."""
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


def check_fresh_database_needs_no_backup(root: Path) -> None:
    """A database created from nothing is already current - no prior state
    exists to protect, so no backup file should appear beside it."""
    fresh = root / "fresh.sqlite3"
    db = Database(fresh)
    assert _user_version(db.conn) == SCHEMA_VERSION
    db.close()
    assert not list(root.glob("fresh.pre-migration-*"))


# ------------------------------------------- the real, shipped ladder


def check_real_ladder_shape() -> None:
    """The shipped ladder has to satisfy the invariants the runner assumes:
    unique, ascending, positive versions, none beyond SCHEMA_VERSION, and a
    body that is either raw SQL or a callable."""
    versions = [step.version for step in MIGRATIONS]
    assert versions == sorted(versions), "MIGRATIONS should be declared oldest-first"
    assert len(set(versions)) == len(versions), "duplicate migration version"
    assert all(version >= 1 for version in versions)
    assert all(version <= SCHEMA_VERSION for version in versions)
    for step in MIGRATIONS:
        assert isinstance(step.apply, str) or callable(step.apply)
        assert step.description


def check_legacy_detections_column_migration(root: Path) -> None:
    """The one real, already-shipped migration, now expressed as a step: a
    pre-v4 `detections` table (no `raw_json`) must gain the column, keep its
    rows, and be writable through the normal upsert path afterwards.

    `CREATE TABLE IF NOT EXISTS` cannot do this - the table already exists,
    so the SCHEMA script skips it entirely and only the ladder can retrofit
    the column."""
    legacy = root / "pre_raw_json.sqlite3"
    conn = sqlite3.connect(legacy)
    conn.executescript(
        """
        CREATE TABLE detections (
            id               INTEGER PRIMARY KEY,
            source           TEXT    NOT NULL,
            satellite        TEXT,
            instrument       TEXT,
            latitude         REAL    NOT NULL,
            longitude        REAL    NOT NULL,
            acq_date         TEXT    NOT NULL,
            acq_time         TEXT    NOT NULL,
            acq_ts           INTEGER NOT NULL,
            brightness       REAL,
            brightness2      REAL,
            scan             REAL,
            track            REAL,
            confidence_raw   TEXT,
            confidence_pct   INTEGER,
            confidence_level TEXT,
            frp              REAL,
            daynight         TEXT,
            version          TEXT,
            UNIQUE (source, satellite, latitude, longitude, acq_date, acq_time)
        );
        INSERT INTO detections
            (source, satellite, latitude, longitude, acq_date, acq_time, acq_ts)
        VALUES ('VIIRS_SNPP_NRT', 'N', 1.0, 2.0, '2024-01-01', '0100', 1704070800);
        """
    )
    conn.execute("PRAGMA user_version=3")
    conn.commit(); conn.close()

    db = Database(legacy)
    try:
        columns = {row[1] for row in db.conn.execute("PRAGMA table_info(detections)")}
        assert "raw_json" in columns, "step 4 did not retrofit detections.raw_json"
        assert _user_version(db.conn) == SCHEMA_VERSION
        assert (root / "pre_raw_json.pre-migration-v3.sqlite3").is_file()
        # The pre-existing row survived, and the migrated table still works.
        assert db.conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 1
        db.upsert_detections(
            [
                {
                    "source": "VIIRS_SNPP_NRT",
                    "satellite": "N",
                    "latitude": 3.0,
                    "longitude": 4.0,
                    "acq_date": "2024-01-02",
                    "acq_time": "0200",
                    "acq_ts": 1704160800,
                    "raw_json": '{"kept": true}',
                }
            ]
        )
        stored = db.conn.execute(
            "SELECT raw_json FROM detections WHERE acq_date = '2024-01-02'"
        ).fetchone()[0]
        assert stored == '{"kept": true}'
    finally:
        db.close()

    # And re-opening an already-current database is a no-op: no second
    # backup, version unchanged.
    db = Database(legacy)
    try:
        assert _user_version(db.conn) == SCHEMA_VERSION
    finally:
        db.close()
    assert len(list(root.glob("pre_raw_json.pre-migration-*"))) == 1


# --------------------------------------------- the sequential mechanism


def check_sequential_steps_from_scratch(root: Path) -> None:
    """A three-step ladder applied end to end lands every step, in version
    order, and stamps the final version."""
    path = root / "ladder_full.sqlite3"
    conn = sqlite3.connect(path)
    try:
        reached = apply_migrations(conn, _fake_ladder(), 7)
        assert reached == 7
        assert _user_version(conn) == 7
        assert _ladder_rows(conn) == [
            (1, "five", "FIVE"),
            (2, "six", "SIX"),
            (3, "seven", "SEVEN"),
        ]
        # The runner restores the connection's transaction handling.
        assert conn.isolation_level == ""
    finally:
        conn.close()


def check_sequential_steps_resume(root: Path) -> None:
    """Applied from various starting versions: only the steps above the
    file's own version run, and a database already at the target is left
    completely alone."""
    path = root / "ladder_resume.sqlite3"
    conn = sqlite3.connect(path)
    try:
        # As an older build would have left it: that release only knew about
        # step 5, so the file stops there and `label_upper` (added by step 6)
        # does not exist yet.
        older_release = tuple(step for step in _fake_ladder() if step.version <= 5)
        assert apply_migrations(conn, older_release, 5) == 5
        assert _user_version(conn) == 5
        assert conn.execute("SELECT id, label FROM ladder_note").fetchall() == [(1, "five")]

        # Now the code catches up: steps 6 and 7 run, step 5 does not
        # (re-running its CREATE TABLE would raise "table already exists").
        assert apply_migrations(conn, _fake_ladder(), 7) == 7
        assert _ladder_rows(conn) == [
            (1, "five", "FIVE"),
            (2, "six", "SIX"),
            (3, "seven", "SEVEN"),
        ]

        # Already current: nothing runs, nothing changes.
        assert apply_migrations(conn, _fake_ladder(), 7) == 7
        assert len(_ladder_rows(conn)) == 3

        # Starting one rung down from the top applies only the last step.
        conn.execute("PRAGMA user_version=6")
        conn.execute("DELETE FROM ladder_note WHERE id = 3")
        conn.execute("DROP INDEX idx_ladder_note_label")
        conn.commit()
        assert apply_migrations(conn, _fake_ladder(), 7) == 7
        assert [row[0] for row in _ladder_rows(conn)] == [1, 2, 3]
    finally:
        conn.close()


def check_version_gap_is_stamped(root: Path) -> None:
    """Versions with no step of their own (purely additive SCHEMA changes)
    still advance user_version to the target."""
    path = root / "ladder_gap.sqlite3"
    conn = sqlite3.connect(path)
    try:
        reached = apply_migrations(conn, (MigrationStep(5, "create", _STEP_FIVE_SQL),), 8)
        assert reached == 8
        assert _user_version(conn) == 8
        assert _table_exists(conn, "ladder_note")
    finally:
        conn.close()


def check_runner_rejects_bad_ladders(root: Path) -> None:
    """Authoring mistakes fail loudly instead of half-migrating a file."""
    path = root / "ladder_bad.sqlite3"
    conn = sqlite3.connect(path)
    try:
        duplicate = (
            MigrationStep(5, "one", "SELECT 1;"),
            MigrationStep(5, "two", "SELECT 1;"),
        )
        try:
            apply_migrations(conn, duplicate, 5)
            raise AssertionError("duplicate migration version was accepted")
        except RuntimeError as exc:
            assert "duplicate" in str(exc)

        try:
            apply_migrations(conn, (MigrationStep(0, "zero", "SELECT 1;"),), 5)
            raise AssertionError("version 0 migration was accepted")
        except RuntimeError:
            pass

        # A step beyond the declared schema version would stamp the file
        # into a state this code then refuses to open.
        try:
            apply_migrations(conn, (MigrationStep(9, "too new", "SELECT 1;"),), 5)
            raise AssertionError("migration beyond the target version was accepted")
        except RuntimeError as exc:
            assert "exceeds schema version" in str(exc)
        assert _user_version(conn) == 0

        # Downgrade refusal applies to the runner itself, not just Database.
        conn.execute("PRAGMA user_version=7")
        conn.commit()
        try:
            apply_migrations(conn, _fake_ladder(), 6)
            raise AssertionError("downgrade was accepted")
        except RuntimeError as exc:
            assert "newer than supported schema" in str(exc)
    finally:
        conn.close()


def check_failed_step_is_recoverable(root: Path) -> None:
    """A step that raises part way through must leave the database at the
    last version that fully succeeded, with its own partial work rolled
    back - and the pre-migration backup must still hold the original file so
    the whole ladder can be abandoned."""
    path = root / "ladder_fail.sqlite3"
    db = Database(path)          # fresh database at SCHEMA_VERSION
    db.set_meta("marker", "before-migration")
    db.close()

    def exploding_step(conn: sqlite3.Connection) -> None:
        # Do real work first, so the rollback has something to undo.
        conn.execute("CREATE TABLE half_done (x INTEGER)")
        conn.execute("INSERT INTO half_done VALUES (1)")
        raise ValueError("simulated mid-migration failure")

    broken = (
        MigrationStep(5, "create ladder_note", _STEP_FIVE_SQL),
        MigrationStep(6, "explodes", exploding_step),
        MigrationStep(7, "never reached", _STEP_SEVEN_SQL),
    )

    with patched_ladder(broken, 7):
        try:
            Database(path)
            raise AssertionError("failing migration was not reported")
        except RuntimeError as exc:
            assert "simulated mid-migration failure" in str(exc)
            assert "version 6" in str(exc)

    conn = sqlite3.connect(path)
    try:
        # Step 5 committed and is stamped; step 6 rolled back entirely and
        # its version was never stamped; step 7 never ran.
        assert _user_version(conn) == 5, "user_version advanced past a failed step"
        assert _table_exists(conn, "ladder_note")
        assert not _table_exists(conn, "half_done"), "failed step was not rolled back"
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'idx_ladder_note_label'"
        ).fetchone()[0] == 0
    finally:
        conn.close()

    # The escape hatch: the backup predates the whole ladder.
    backup = root / f"ladder_fail.pre-migration-v{SCHEMA_VERSION}.sqlite3"
    assert backup.is_file()
    conn = sqlite3.connect(backup)
    try:
        assert _user_version(conn) == SCHEMA_VERSION
        assert not _table_exists(conn, "ladder_note")
        assert conn.execute(
            "SELECT value FROM meta WHERE key = 'marker'"
        ).fetchone()[0] == "before-migration"
    finally:
        conn.close()

    # Restore it and re-run with the bug fixed: the ladder replays from the
    # backup's version and lands on the target.
    restored = root / "ladder_restored.sqlite3"
    shutil.copy2(backup, restored)
    with patched_ladder(_fake_ladder(), 7):
        db = Database(restored)
        try:
            assert _user_version(db.conn) == 7
            assert db.get_meta("marker") == "before-migration"
            assert [row[0] for row in _ladder_rows(db.conn)] == [1, 2, 3]
        finally:
            db.close()
    assert (root / f"ladder_restored.pre-migration-v{SCHEMA_VERSION}.sqlite3").is_file()


def check_database_startup_runs_ladder(root: Path) -> None:
    """The whole startup path end to end: an existing database several
    versions behind is backed up once, migrated through every pending step
    in order, and stamped at the new target."""
    path = root / "startup.sqlite3"
    db = Database(path)
    db.set_meta("marker", "kept")
    db.close()

    with patched_ladder(_fake_ladder(), 7):
        db = Database(path)
        try:
            assert _user_version(db.conn) == 7
            assert db.get_meta("marker") == "kept"
            assert _ladder_rows(db.conn) == [
                (1, "five", "FIVE"),
                (2, "six", "SIX"),
                (3, "seven", "SEVEN"),
            ]
        finally:
            db.close()
        backups = list(root.glob("startup.pre-migration-*"))
        assert [p.name for p in backups] == [
            f"startup.pre-migration-v{SCHEMA_VERSION}.sqlite3"
        ]

        # Re-opening at the target does no further work and takes no
        # further backup.
        db = Database(path)
        try:
            assert _user_version(db.conn) == 7
            assert len(_ladder_rows(db.conn)) == 3
        finally:
            db.close()
        assert len(list(root.glob("startup.pre-migration-*"))) == 1


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        check_backup_and_downgrade_refusal(root)
        check_fresh_database_needs_no_backup(root)
        check_real_ladder_shape()
        check_legacy_detections_column_migration(root)
        check_sequential_steps_from_scratch(root)
        check_sequential_steps_resume(root)
        check_version_gap_is_stamped(root)
        check_runner_rejects_bad_ladders(root)
        check_failed_step_is_recoverable(root)
        check_database_startup_runs_ladder(root)
    print("Database migration and rollback checks passed.")


if __name__ == "__main__":
    main()
