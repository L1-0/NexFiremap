"""Fail-closed incident-LAN authentication, CSRF and role policy checks."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.api import create_app
from nexfiremap.config import Settings


def settings(root: Path, password: str = "correct horse battery staple") -> Settings:
    return Settings(map_key="", host="0.0.0.0", port=8000, db_path=root / "secure.sqlite3",
                    cache_days=30, tile_cache_dir=root / "tiles", job_dir=root / "jobs",
                    backup_dir=root / "backups", backup_interval_minutes=0,
                    lan_mode=True, admin_password=password, session_minutes=5)


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        try:
            app = create_app(settings(root, "short"))
            with TestClient(app):
                raise AssertionError("LAN app started with a weak/missing administrator password")
        except RuntimeError:
            pass

        with TestClient(create_app(settings(root))) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/api/operations/incidents").status_code == 401
            bad = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
            assert bad.status_code == 401
            login = client.post("/api/auth/login", json={"username": "admin", "password": "correct horse battery staple"})
            assert login.status_code == 200, login.text
            csrf = login.json()["csrf"]
            assert client.get("/api/operations/incidents").status_code == 200
            blocked = client.post("/api/operations/incidents", json={"name": "Secure"})
            assert blocked.status_code == 403
            created = client.post("/api/operations/incidents", json={"name": "Secure"}, headers={"X-CSRF-Token": csrf})
            assert created.status_code == 201, created.text
            incident_id = created.json()["incident"]["id"]
            feed = client.post(
                f"/api/operations/incidents/{incident_id}/position-feeds",
                json={"name": "LAN gateway"}, headers={"X-CSRF-Token": csrf},
            )
            assert feed.status_code == 201, feed.text
            feed_id, feed_token = feed.json()["id"], feed.json()["ingest_token"]
            audit = client.get(f"/api/operations/incidents/{incident_id}").json()["audit_log"]
            assert {row["actor"] for row in audit} == {"admin"}
            assert created.headers["x-content-type-options"] == "nosniff"
            assert created.headers["x-frame-options"] == "DENY"

            viewer = client.post(
                "/api/auth/accounts",
                json={"username": "viewer1", "role": "viewer", "password": "viewer password 123"},
                headers={"X-CSRF-Token": csrf},
            )
            assert viewer.status_code == 201, viewer.text
            public = client.post(
                "/api/auth/accounts",
                json={"username": "public1", "role": "public", "password": "public password 123"},
                headers={"X-CSRF-Token": csrf},
            )
            assert public.status_code == 201, public.text
            logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
            assert logout.status_code == 200
            assert client.get("/api/operations/incidents").status_code == 401
            # Device ingestion has no browser session or CSRF cookie. Its only
            # public route is isolated by a source-specific revocable token.
            device_push = client.post(
                f"/api/feeds/positions/{feed_id}",
                json={"positions": [{"external_id": "secure-1", "callsign": "Engine 1",
                                      "observed_at": "2026-08-13T00:00:00Z",
                                      "latitude": 48.0, "longitude": 11.0}]},
                headers={"X-Feed-Token": feed_token},
            )
            assert device_push.status_code == 202, device_push.text
            assert client.get(f"/api/operations/incidents/{incident_id}/vehicle-positions/latest").status_code == 401

            viewer_login = client.post("/api/auth/login", json={"username": "viewer1", "password": "viewer password 123"})
            assert viewer_login.status_code == 200
            viewer_csrf = viewer_login.json()["csrf"]
            assert client.get("/api/operations/incidents").status_code == 200
            assert client.get("/api/auth/accounts").status_code == 403
            assert client.post("/api/operations/incidents", json={"name": "Denied"},
                               headers={"X-CSRF-Token": viewer_csrf}).status_code == 403
            # A backup/recovery is a whole-database export spanning every
            # incident, not the incident-scoped data may_read's "any
            # non-public role" rule is meant for - a role below administrator
            # must not be able to list or fetch one.
            assert client.get("/api/operations/backups").status_code == 403
            assert client.get("/api/operations/backups/anything.sqlite3/download").status_code == 403
            assert client.get("/api/operations/recoveries").status_code == 403
            assert client.get("/api/operations/recoveries/recovery-anything.sqlite3/download").status_code == 403
            client.post("/api/auth/logout", headers={"X-CSRF-Token": viewer_csrf})

            public_login = client.post("/api/auth/login", json={"username": "public1", "password": "public password 123"})
            assert public_login.status_code == 200
            assert client.get("/api/operations/incidents").status_code == 403
            assert client.get("/api/public/products").status_code == 200
    print("Incident LAN security checks passed.")


if __name__ == "__main__":
    main()
