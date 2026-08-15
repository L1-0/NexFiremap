"""Operator-editable settings: precedence, coercion, and secret containment.

The property this file exists to defend is the last one: **a stored API key
must never come back out of the server**. Everything else here is ordinary
store behaviour; that one is a security guarantee, so it is asserted against
the real HTTP surface as well as against the store, and against the full
serialised response body rather than a field the test picks out.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap import settings_store
from nexfiremap.config import load_settings
from nexfiremap.db import Database

SECRET = "a-real-looking-firms-key-9f3c"


def main() -> None:
    check_store()
    check_precedence()
    check_masking()
    check_routes()
    print("Settings store checks passed.")


def check_store() -> None:
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "settings.sqlite3")
        try:
            assert settings_store.read_overrides(db) == {}

            restart = settings_store.write(db, {"map_key": SECRET, "cache_days": "14"}, "tester")
            # cache_days is coerced to a real int, not stored as the string it
            # arrived as - a JSON round trip that returns "14" would make
            # `cache_days - 1` a TypeError somewhere far from here.
            stored = settings_store.read_overrides(db)
            assert stored["cache_days"] == 14 and isinstance(stored["cache_days"], int)
            assert stored["map_key"] == SECRET
            assert "map_key" in restart and "cache_days" in restart

            # A comma-separated string is the shape an HTML input produces; a
            # list is the shape an API client sends. Both must land as a list.
            settings_store.write(db, {"cap_feeds": "https://a.example/x, https://b.example/y"})
            assert settings_store.read_overrides(db)["cap_feeds"] == \
                ["https://a.example/x", "https://b.example/y"]
            settings_store.write(db, {"cap_feeds": ["https://c.example/z"]})
            assert settings_store.read_overrides(db)["cap_feeds"] == ["https://c.example/z"]

            # Unknown names are refused rather than stored, so a typo surfaces
            # immediately instead of persisting into a table nobody re-reads.
            for bad in ({"db_path": "/etc/passwd"}, {"admin_password": "x"}, {"nonsense": 1}):
                try:
                    settings_store.write(db, bad)
                    raise AssertionError(f"stored an uneditable setting: {bad}")
                except settings_store.SettingsError:
                    pass
            # ...including fields that exist on Settings but are deliberately
            # not operator-editable. Assert that explicitly - it is the gate
            # that keeps a web form away from the filesystem and the LAN gate.
            assert "db_path" not in settings_store.EDITABLE
            assert "admin_password" not in settings_store.EDITABLE
            assert "lan_mode" not in settings_store.EDITABLE

            try:
                settings_store.write(db, {"cache_days": "soon"})
                raise AssertionError("accepted a non-numeric integer setting")
            except settings_store.SettingsError:
                pass
            try:
                settings_store.write(db, {"symbology_profile": "martian"})
                raise AssertionError("accepted a symbology profile outside the vocabulary")
            except settings_store.SettingsError:
                pass

            settings_store.clear(db, "map_key")
            assert "map_key" not in settings_store.read_overrides(db)

            # A corrupt row must be skipped, not fatal: a settings value that
            # stops the server booting cannot be fixed from the settings pane.
            with db._write_lock:
                db.conn.execute("INSERT INTO app_settings (key,value_json,updated_at) VALUES (?,?,?)",
                                ("tile_contact", "{not json", "2026-08-15T00:00:00Z"))
                db.conn.commit()
            survivors = settings_store.read_overrides(db)
            assert "tile_contact" not in survivors and survivors["cache_days"] == 14
        finally:
            db.close()


def check_precedence() -> None:
    """Database over environment, and a blank override falls through."""
    base = replace(load_settings(), map_key="from-dot-env", cache_days=3)

    folded = settings_store.apply_overrides(base, {"map_key": "from-database"})
    assert folded.map_key == "from-database"
    assert folded.cache_days == 3, "an unrelated setting must not be disturbed"
    assert base.map_key == "from-dot-env", "apply_overrides must not mutate its input"

    # An empty stored value means "not set here" and must not blank out a key
    # an administrator deliberately put in .env.
    assert settings_store.apply_overrides(base, {"map_key": ""}).map_key == "from-dot-env"
    assert settings_store.apply_overrides(base, {"cap_feeds": []}).cap_feeds == base.cap_feeds

    # Anything that reached the table but is not editable is ignored on the way
    # out too, so the gate holds even against a hand-edited database.
    assert settings_store.apply_overrides(base, {"db_path": "/tmp/evil"}).db_path == base.db_path
    assert settings_store.apply_overrides(base, {}) is base


def check_masking() -> None:
    base = replace(load_settings(), map_key=SECRET, eumetsat_consumer_key="",
                   tile_contact="ops@example.org")
    view = settings_store.public_view(base, {"map_key": SECRET})

    blob = json.dumps(view)
    assert SECRET not in blob, "public_view leaked the key"
    assert view["fields"]["map_key"]["configured"] is True
    assert view["fields"]["map_key"]["hint"] == "…9f3c"
    assert view["fields"]["map_key"]["source"] == "database"
    assert view["fields"]["map_key"]["restart_required"] is True
    # A secret must not carry a `value` at all - a UI that fell back to
    # rendering `entry.value` would then show an empty box, not a key.
    assert "value" not in view["fields"]["map_key"]

    assert view["fields"]["eumetsat_consumer_key"]["configured"] is False
    assert view["fields"]["eumetsat_consumer_key"]["source"] == "unset"
    # Non-secrets do carry their value; that is the point of the pane.
    assert view["fields"]["tile_contact"]["value"] == "ops@example.org"
    assert view["fields"]["tile_contact"]["source"] == "environment"

    # Every secret listed is actually a field, or the mask would silently
    # protect nothing.
    assert settings_store.SECRETS <= set(settings_store.EDITABLE)


def check_routes() -> None:
    """The same guarantee over real HTTP, including the admin gate."""
    from fastapi.testclient import TestClient

    from nexfiremap.api import create_app

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = replace(load_settings(), db_path=root / "app.sqlite3",
                           tile_cache_dir=root / "tiles", lan_mode=False, map_key="")
        with TestClient(create_app(settings)) as client:
            first = client.get("/api/settings")
            assert first.status_code == 200, first.text
            assert first.json()["fields"]["map_key"]["configured"] is False

            saved = client.put("/api/settings", json={"values": {
                "map_key": SECRET, "coordinate_system": "mgrs", "symbology_profile": "dv102"}})
            assert saved.status_code == 200, saved.text
            # The write response is the most likely place for a key to escape,
            # since it is built right after the value was handled.
            assert SECRET not in saved.text
            assert "map_key" in saved.json()["restart_needed_for"], \
                "the operator must be told the key needs a restart"

            after = client.get("/api/settings")
            assert SECRET not in after.text
            assert after.json()["fields"]["map_key"]["configured"] is True
            assert after.json()["fields"]["map_key"]["hint"] == "…9f3c"


            # The shared display preference reaches every client via /api/config
            # (which needs no administrator), while the key does not.
            config = client.get("/api/config")
            assert config.status_code == 200, config.text
            assert config.json()["client_settings"]["coordinate_system"] == "mgrs"
            assert config.json()["symbology_profile"] == "dv102"
            assert SECRET not in config.text
            assert "map_key" not in config.json()

            # An unknown name is a 400, not a silent no-op.
            assert client.put("/api/settings", json={"values": {"nope": 1}}).status_code == 400
            assert client.put("/api/settings", json={"values": {"db_path": "/etc"}}).status_code == 400

            # Clearing falls back to the environment value beneath.
            cleared = client.delete("/api/settings/map_key")
            assert cleared.status_code == 200
            assert client.get("/api/settings").json()["fields"]["map_key"]["configured"] is False
            assert client.delete("/api/settings/db_path").status_code == 404

            # Every handler must answer in the same shape, because the settings
            # pane re-renders itself from whatever a write returns. A write
            # response missing `symbology_profiles` left the profile <select>
            # with no options, and the *next* save then posted an empty profile.
            for name, response in (("GET", after), ("PUT", saved), ("DELETE", cleared)):
                body = response.json()
                assert {"fields", "client", "symbology_profiles"} <= set(body), \
                    f"{name} response is missing keys the pane renders from"
                assert {p["id"] for p in body["symbology_profiles"]}, f"{name} sent an empty profile list"
                assert set(body["fields"]) == set(settings_store.EDITABLE), name

    # ...and with authentication actually enforced, a non-administrator gets
    # nothing at all. This is the gate the masking sits behind.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = replace(load_settings(), db_path=root / "app.sqlite3",
                           tile_cache_dir=root / "tiles", lan_mode=True,
                           admin_password="correct-horse-battery")
        with TestClient(create_app(settings)) as client:
            anonymous = client.get("/api/settings")
            assert anonymous.status_code in (401, 403), anonymous.status_code
            assert client.put("/api/settings", json={"values": {"map_key": SECRET}}).status_code \
                in (401, 403)


if __name__ == "__main__":
    main()
