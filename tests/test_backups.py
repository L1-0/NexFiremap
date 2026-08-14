"""Verified operational backup, recovery and integrity drills; no network required.

`backups.py` deliberately has *no* in-place restore. `create_recovery` always
materialises a separate, independently verified database beside the backups and
never writes over the running application's own file. Recovery is therefore
tested here as a round-trip into that separate file - including the case that
matters most mid-incident, where the live database has moved on since the backup
was taken and must not be rolled back just because someone inspected an older
snapshot.

Each drill gets its own workspace under the shared temporary directory so a
failure in one leaves the others' files intact for inspection.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.backups import BackupManager
from nexfiremap.config import Settings
from nexfiremap.db import Database
from nexfiremap.operations import OperationsStore, default_period


# --------------------------------------------------------------- helpers


def _workspace(root: Path, name: str, keep: int = 3) -> tuple[Settings, Database, BackupManager]:
    """An isolated live database plus its own backup directory."""
    home = root / name
    settings = Settings(map_key="", host="127.0.0.1", port=8000,
                        db_path=home / "live.sqlite3", cache_days=30,
                        backup_dir=home / "backups", backup_interval_minutes=0,
                        backup_keep=keep)
    db = Database(settings.db_path)
    return settings, db, BackupManager(settings, db)


def _dump(path: Path) -> dict[str, list[tuple]]:
    """Every user table's full contents, keyed by table name.

    Rows are sorted by repr rather than natural tuple order because a column
    can legitimately mix None and text, which is unorderable in Python."""
    conn = sqlite3.connect(path)
    try:
        tables = [str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()]
        return {table: sorted((tuple(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()), key=repr)
                for table in tables}
    finally:
        conn.close()


def _damage_one_byte(path: Path) -> str:
    """Corrupt a SQLite file so it still *opens* but fails integrity_check.

    The interesting failure mode is a database that looks fine until it is
    checked - a wholly unreadable file is the easy case. Flipping a single byte
    deep inside a b-tree page typically produces "row N missing from index ...",
    but exactly which offset does that is data dependent, so this tries a few
    and returns the first genuine partial-corruption result. Raises if it cannot
    manufacture one, so the drill can never pass vacuously."""
    original = bytes(path.read_bytes())
    page = 4096
    for page_index in range(3, max(4, len(original) // page)):
        for tail in (6, 46, 120):
            offset = page * page_index - tail
            if offset <= page or offset >= len(original):
                continue
            data = bytearray(original)
            data[offset] ^= 0xFF
            path.write_bytes(bytes(data))
            conn = None
            try:
                conn = sqlite3.connect(path)
                rows = [str(r[0]) for r in conn.execute("PRAGMA integrity_check").fetchall()]
            except sqlite3.DatabaseError:
                continue
            finally:
                if conn is not None:
                    conn.close()
            if rows != ["ok"]:
                return "; ".join(rows[:10])
    path.write_bytes(original)
    raise AssertionError("could not manufacture a partially corrupt backup file")


def _busy_incident(store: OperationsStore, name: str) -> dict[str, object]:
    """A realistic incident with something in every operational table."""
    incident = store.create_incident({"name": name, "center_lat": 47.6, "center_lon": 11.2}, "IC-1")
    period = store.create_period(incident["id"], default_period(), "IC-1")
    scenario = store.create_scenario(incident["id"], period["id"], {"name": "Plan A", "kind": "primary"}, "IC-1")
    store.create_feature(incident["id"], {
        "period_id": period["id"], "scenario_id": scenario["id"], "feature_type": "tactical_line",
        "title": "South handline", "status": "planned",
        "geometry": {"type": "LineString", "coordinates": [[11.2, 47.6], [11.3, 47.7]]},
        "properties": {"objective": "Anchor off the road", "responsible_unit": "Division South"},
    }, "DIV-S")
    store.create_resource(incident["id"], {"callsign": "Engine 4", "unit_type": "engine", "crew_size": 4}, "LOG")
    store.create_snapshot(incident["id"], "Shift start", period["id"], "operational", "IC-1")
    return {"incident": incident, "period": period, "scenario": scenario}


# ----------------------------------------------------------------- drills


def check_backup_and_recovery_lifecycle(root: Path) -> None:
    """Original end-to-end lifecycle: create, prune, verify, recover, and
    refuse to publish a recovery from an unreadable backup."""
    settings, db, manager = _workspace(root, "lifecycle", keep=2)
    try:
        incident = OperationsStore(db).create_incident({"name": "Backup Drill"}, "test")
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
        assert list(manager.directory.glob(".partial-*")) == []
    finally:
        db.close()


def check_recovery_never_rolls_back_newer_live_data(root: Path) -> None:
    """The safety property the whole module exists for.

    An operator inspecting last shift's backup must not lose this shift's work.
    Because `create_recovery` writes to its own file and never to `db.path`, the
    live database keeps every record written after the backup was taken - so the
    drill takes a backup, moves the live incident on, recovers the *older*
    backup, and then proves the newer live records are all still there while the
    recovery holds only the older state."""
    settings, db, manager = _workspace(root, "newer-live")
    try:
        store = OperationsStore(db)
        incident = store.create_incident({"name": "Ridge Fire"}, "IC-1")
        backup = manager.create_backup("end of day shift")

        # The live database moves on after the backup was taken.
        store.update_incident(incident["id"], {"notes": "night shift briefing"}, 1, "IC-1")
        second = store.create_incident({"name": "Second Start"}, "IC-2")
        store.create_resource(incident["id"], {"callsign": "Engine 9", "unit_type": "engine"}, "LOG")

        # The live database file itself must not be written by a recovery at
        # all - the most direct statement of the invariant, and the one check
        # that still holds if a "restore" were ever added that replaced the
        # file out from under the running process.
        before_stat = settings.db_path.stat()
        recovery = manager.create_recovery(backup["name"])
        after_stat = settings.db_path.stat()
        assert (before_stat.st_size, before_stat.st_mtime_ns) == (after_stat.st_size, after_stat.st_mtime_ns), \
            "create_recovery wrote to the live database file"
        recovery_path = manager.recovery_path_for_download(recovery["name"])

        # 1. A recovery is always a separate file, never the running database.
        assert recovery_path.resolve() != settings.db_path.resolve()
        assert recovery_path.parent.resolve() == manager.recovery_directory
        assert settings.db_path.is_file()

        # 2. Everything written after the backup is still live and untouched.
        live = store.get_incident(incident["id"])
        assert live["revision"] == 2 and live["notes"] == "night shift briefing"
        assert {row["name"] for row in store.list_incidents(include_closed=True)} == {"Ridge Fire", "Second Start"}
        assert [row["callsign"] for row in store.list_resources(incident["id"])] == ["Engine 9"]

        # ...and it is still on disk, not just in the open connection's view.
        # Checking only through `store` would let a recovery that replaced the
        # live *file* underneath the running process slip through.
        live_conn = sqlite3.connect(settings.db_path)
        try:
            on_disk = {row[0] for row in live_conn.execute("SELECT name FROM incidents").fetchall()}
            assert on_disk == {"Ridge Fire", "Second Start"}, on_disk
            assert live_conn.execute("SELECT COUNT(*) FROM incident_resources").fetchone()[0] == 1
            assert live_conn.execute("SELECT revision FROM incidents WHERE id=?",
                                     (incident["id"],)).fetchone()[0] == 2
        finally:
            live_conn.close()

        # 3. The recovery carries the older state, and only the older state -
        #    proving the two really are independent copies rather than aliases.
        conn = sqlite3.connect(recovery_path)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            names = {row[0] for row in conn.execute("SELECT name FROM incidents").fetchall()}
            assert names == {"Ridge Fire"}, names
            assert conn.execute("SELECT revision, notes FROM incidents WHERE id=?",
                                (incident["id"],)).fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM incident_resources").fetchone()[0] == 0
            assert conn.execute("SELECT 1 FROM incidents WHERE id=?", (second["id"],)).fetchone() is None
        finally:
            conn.close()

        # 4. Writing into the recovery copy must not reach the live database.
        conn = sqlite3.connect(recovery_path)
        try:
            conn.execute("UPDATE incidents SET name='Edited In Recovery' WHERE id=?", (incident["id"],))
            conn.commit()
        finally:
            conn.close()
        assert store.get_incident(incident["id"])["name"] == "Ridge Fire"
    finally:
        db.close()


def check_recovery_round_trip_preserves_every_record(root: Path) -> None:
    """Record-for-record round-trip: a fully populated incident survives
    live -> backup -> recovery with every user table identical, and the
    recovered database re-exports a byte-identical incident package."""
    settings, db, manager = _workspace(root, "round-trip")
    recovery_path = None
    try:
        store = OperationsStore(db)
        built = _busy_incident(store, "Round Trip Fire")
        incident_id = built["incident"]["id"]
        bundle_before = store.export_bundle(incident_id)

        backup = manager.create_backup("round trip")
        recovery = manager.create_recovery(backup["name"])
        recovery_path = manager.recovery_path_for_download(recovery["name"])

        live_tables = _dump(settings.db_path)
        backup_tables = _dump(manager.path_for_download(backup["name"]))
        recovery_tables = _dump(recovery_path)

        # Guard against a vacuous pass: the fixture really did write rows.
        assert len(live_tables["incidents"]) == 1
        assert len(live_tables["tactical_features"]) == 1
        assert len(live_tables["incident_resources"]) == 1
        assert len(live_tables["incident_snapshots"]) == 1
        assert len(live_tables["incident_audit_log"]) >= 5

        assert backup_tables == live_tables, "backup diverged from the live database"
        assert recovery_tables == live_tables, "recovery diverged from the live database"
    finally:
        db.close()

    # The recovered file is a working workspace, not just matching bytes:
    # re-open it as a database and re-export the same incident package.
    assert recovery_path is not None
    recovered_db = Database(recovery_path)
    try:
        bundle_after = OperationsStore(recovered_db).export_bundle(incident_id)
    finally:
        recovered_db.close()
    volatile = {"package_id", "exported_at"}
    assert {k: v for k, v in bundle_after.items() if k not in volatile} == \
           {k: v for k, v in bundle_before.items() if k not in volatile}
    assert bundle_after["origin_installation_id"] == bundle_before["origin_installation_id"]


def check_corrupt_backup_is_detected_and_refused(root: Path) -> None:
    """A backup that still opens but fails integrity_check must be reported as
    unhealthy by verify() and must never be turned into a recovery."""
    settings, db, manager = _workspace(root, "corrupt")
    try:
        store = OperationsStore(db)
        _busy_incident(store, "Corruption Drill")
        # Bulk the file out so it has enough b-tree pages to damage.
        for index in range(400):
            store.create_resource(store.list_incidents()[0]["id"],
                                  {"callsign": f"Engine {index}", "unit_type": "engine"}, "LOG")
        backup = manager.create_backup("pre-corruption")
        healthy = manager.verify(backup["name"])
        assert healthy["ok"] is True and healthy["integrity"] == "ok"

        damage = _damage_one_byte(manager.path_for_download(backup["name"]))
        assert damage != "ok"

        # verify() reports the damage rather than raising, so an operator can
        # see *what* is wrong with an archived backup.
        checked = manager.verify(backup["name"])
        assert checked["ok"] is False
        assert checked["integrity"] != "ok" and checked["integrity"]

        # ...and a recovery is refused outright rather than silently produced
        # from a database SQLite already knows is damaged.
        recoveries_before = len(manager.list_recoveries())
        try:
            manager.create_recovery(backup["name"])
            raise AssertionError("recovery produced from a backup that fails integrity_check")
        except RuntimeError as exc:
            assert "source backup integrity check failed" in str(exc), exc
        assert len(manager.list_recoveries()) == recoveries_before
        assert list(manager.recovery_directory.glob("*.partial")) == []
        assert list(manager.directory.glob(".partial-*")) == []

        # The live database is untouched by any of this.
        assert manager._integrity(settings.db_path) == "ok"
    finally:
        db.close()


def check_backup_and_recovery_names_are_validated(root: Path) -> None:
    """Both name->path resolvers reject anything that is not a name this module
    generated, and report a well-formed but absent name as missing rather than
    as invalid."""
    settings, db, manager = _workspace(root, "names")
    try:
        manager.create_backup("seed")
        absent_backup = "nexfiremap-20200101T000000000000Z.sqlite3"
        absent_recovery = f"recovery-{'0' * 32}.sqlite3"

        for bad in ("../live.sqlite3", "..\\live.sqlite3", "nexfiremap-nope.sqlite3",
                    "live.sqlite3", "", "nexfiremap-20200101T000000000000Z.sqlite3.bak"):
            for call in (manager.verify, manager.path_for_download, manager.create_recovery):
                try:
                    call(bad)
                    raise AssertionError(f"{call.__name__} accepted unsafe backup name {bad!r}")
                except ValueError:
                    pass
        for bad in ("../../live.sqlite3", f"recovery-{'z' * 32}.sqlite3", "recovery-abc.sqlite3", ""):
            try:
                manager.recovery_path_for_download(bad)
                raise AssertionError(f"unsafe recovery name accepted: {bad!r}")
            except ValueError:
                pass

        for call in (manager.verify, manager.path_for_download, manager.create_recovery):
            try:
                call(absent_backup)
                raise AssertionError(f"{call.__name__} accepted a missing backup")
            except FileNotFoundError:
                pass
        try:
            manager.recovery_path_for_download(absent_recovery)
            raise AssertionError("missing recovery accepted")
        except FileNotFoundError:
            pass
    finally:
        db.close()


def check_pruning_listing_and_status(root: Path) -> None:
    """Retention keeps the newest backups *by name*, foreign and half-written
    files never show up as backups, and status() reports the real state."""
    settings, db, manager = _workspace(root, "prune", keep=2)
    try:
        store = OperationsStore(db)
        store.create_incident({"name": "Retention Drill"}, "IC-1")

        idle = manager.status()
        assert idle["enabled"] is False and idle["interval_minutes"] == 0
        assert idle["keep"] == 2 and idle["count"] == 0
        assert idle["latest"] is None and idle["last_success"] is None and idle["last_error"] is None

        made = [manager.create_backup("scheduled") for _ in range(4)]
        names = [item["name"] for item in made]
        assert names == sorted(names), "backup names are not monotonically increasing"
        kept = [item["name"] for item in manager.list_backups()]
        assert kept == sorted(names, reverse=True)[:2], kept

        # Retention deliberately orders by filename, not mtime, so that copying
        # the backup directory around cannot change which backup is "newest".
        oldest_kept = manager.path_for_download(kept[-1])
        future = time.time() + 86_400
        os.utime(oldest_kept, (future, future))
        assert [item["name"] for item in manager.list_backups()] == kept

        # Leftover partial files and unrelated files are invisible to callers.
        (manager.directory / ".partial-deadbeef.sqlite3").write_bytes(b"half written")
        (manager.directory / "notes.txt").write_text("operator note", encoding="utf-8")
        (manager.directory / "nexfiremap-not-a-stamp.sqlite3").write_bytes(b"wrong shape")
        assert [item["name"] for item in manager.list_backups()] == kept

        # A further backup still prunes down to `keep`, ignoring the noise.
        newest = manager.create_backup("manual")
        listed = manager.list_backups()
        assert [item["name"] for item in listed] == sorted([*kept, newest["name"]], reverse=True)[:2]
        assert (manager.directory / ".partial-deadbeef.sqlite3").is_file()

        healthy = manager.status()
        assert healthy["count"] == 2 and healthy["keep"] == 2
        assert healthy["latest"]["name"] == newest["name"]
        assert healthy["last_success"]["name"] == newest["name"]
        assert healthy["last_success"]["reason"] == "manual"
        assert healthy["last_error"] is None
    finally:
        db.close()


def check_recovery_provenance_survives_a_lost_sidecar(root: Path) -> None:
    """A recovery's source backup is recorded in a JSON sidecar; if that sidecar
    is lost or damaged the recovery must still be listed (the database itself is
    fine) - just without provenance, never dropped from the listing."""
    settings, db, manager = _workspace(root, "provenance")
    try:
        OperationsStore(db).create_incident({"name": "Provenance Drill"}, "IC-1")
        backup = manager.create_backup("manual")
        recovery = manager.create_recovery(backup["name"])
        sidecar = manager.recovery_path_for_download(recovery["name"]).with_suffix(".json")
        assert sidecar.is_file()
        assert json.loads(sidecar.read_text(encoding="utf-8"))["source_backup"] == backup["name"]

        listed = manager.list_recoveries()
        assert len(listed) == 1 and listed[0]["source_backup"] == backup["name"]
        assert listed[0]["size_bytes"] > 0

        sidecar.write_text("{ this is not json", encoding="utf-8")
        damaged = manager.list_recoveries()
        assert len(damaged) == 1 and damaged[0]["name"] == recovery["name"]
        assert damaged[0]["source_backup"] is None and damaged[0]["size_bytes"] > 0

        sidecar.unlink()
        missing = manager.list_recoveries()
        assert len(missing) == 1 and missing[0]["name"] == recovery["name"]
        assert missing[0]["source_backup"] is None

        # An unrelated file in the recovery directory is not a recovery.
        (manager.recovery_directory / "recovery-not-a-uuid.sqlite3").write_bytes(b"nope")
        assert len(manager.list_recoveries()) == 1
    finally:
        db.close()


def check_backup_under_concurrent_writes(root: Path) -> None:
    """Backups use SQLite's online backup API, so they are taken while the
    incident is still being written to. Every snapshot taken mid-write must be
    internally consistent - never a torn copy holding a row the writer had not
    committed."""
    settings, db, manager = _workspace(root, "concurrent", keep=5)
    store = OperationsStore(db)
    incident = store.create_incident({"name": "Busy Fire"}, "IC-1")
    written: list[str] = []
    errors: list[Exception] = []
    stop = threading.Event()

    def writer() -> None:
        index = 0
        while not stop.is_set():
            try:
                index += 1
                row = store.create_resource(incident["id"],
                                            {"callsign": f"Engine {index}", "unit_type": "engine"}, "LOG")
                written.append(row["id"])
            except Exception as exc:  # noqa: BLE001 - reported to the main thread
                errors.append(exc)
                return

    thread = threading.Thread(target=writer, name="incident-writer")
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while len(written) < 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        snapshots = [manager.create_backup("mid-write") for _ in range(3)]
    finally:
        stop.set()
        thread.join(timeout=10)
    try:
        assert not thread.is_alive()
        assert not errors, errors[0]
        assert len(written) >= 5, written
        committed = set(written)
        assert all(item["integrity"] == "ok" for item in snapshots)
        for item in snapshots:
            conn = sqlite3.connect(manager.path_for_download(item["name"]))
            try:
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert conn.execute("SELECT name FROM incidents WHERE id=?",
                                    (incident["id"],)).fetchone() == ("Busy Fire",)
                ids = {str(row[0]) for row in conn.execute("SELECT id FROM incident_resources").fetchall()}
                # No phantom rows: everything in the snapshot was committed.
                assert ids <= committed, ids - committed
                # Every snapshotted resource is a whole row, not a half-written one.
                assert conn.execute(
                    "SELECT COUNT(*) FROM incident_resources WHERE callsign IS NULL OR incident_id IS NULL"
                ).fetchone()[0] == 0
            finally:
                conn.close()
        assert list(manager.directory.glob(".partial-*")) == []
    finally:
        db.close()


def check_scheduled_backups_run_until_stopped(root: Path) -> None:
    """The scheduled loop actually produces backups, records a failure instead
    of dying on it, and stops promptly when asked.

    The interval is set directly rather than through Settings because the
    configured unit is whole minutes - far too slow for a drill, and the loop's
    behaviour does not depend on the length of the wait."""
    settings, db, manager = _workspace(root, "scheduled", keep=3)

    async def drill() -> None:
        manager.interval_s = 0.05
        await manager.start()
        try:
            deadline = time.monotonic() + 15
            while not manager.list_backups() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert manager.list_backups(), "the scheduled loop never produced a backup"
            assert manager.last_success is not None
            assert manager.last_success["reason"] == "scheduled"
            assert manager.last_error is None
            assert manager.status()["enabled"] is True

            # A failing backup is recorded for the operator, not raised into
            # the event loop and not fatal to the schedule.
            def explode(reason: str = "manual") -> dict[str, object]:
                raise RuntimeError("backup volume is full")

            manager.create_backup = explode  # type: ignore[method-assign]
            deadline = time.monotonic() + 15
            while manager.last_error is None and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert manager.last_error == "backup volume is full", manager.last_error
        finally:
            # Guarded: if an assertion above fired before the override was
            # installed, deleting it would mask the real failure.
            manager.__dict__.pop("create_backup", None)
            await manager.stop()

        # Stopped means stopped: no further backups appear.
        settled = len(manager.list_backups())
        await asyncio.sleep(0.3)
        assert len(manager.list_backups()) == settled
        assert manager._task is None

    try:
        asyncio.run(drill())
    finally:
        db.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        check_backup_and_recovery_lifecycle(root)
        check_recovery_never_rolls_back_newer_live_data(root)
        check_recovery_round_trip_preserves_every_record(root)
        check_corrupt_backup_is_detected_and_refused(root)
        check_backup_and_recovery_names_are_validated(root)
        check_pruning_listing_and_status(root)
        check_recovery_provenance_survives_a_lost_sidecar(root)
        check_backup_under_concurrent_writes(root)
        check_scheduled_backups_run_until_stopped(root)
    print("Verified backup checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
