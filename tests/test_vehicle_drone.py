"""End-to-end checks for incident-LAN vehicle feeds and drone map products."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import rasterio
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.api import create_app
from nexfiremap.config import Settings


def settings(root: Path) -> Settings:
    return Settings(map_key="", host="127.0.0.1", port=8000, db_path=root / "sensors.sqlite3",
                    cache_days=30, tile_cache_dir=root / "tiles", job_dir=root / "jobs", job_workers=1,
                    backup_dir=root / "backups", backup_interval_minutes=0, backup_keep=3,
                    drone_dir=root / "drone", position_stale_seconds=300, position_max_speed_kmh=180,
                    drone_max_pixels=1_000_000, drone_mosaic_max_pixels=2_000_000)


def image_bytes(colour: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (48, 32), colour).save(output, "PNG")
    return output.getvalue()


def feed_settings(root: Path) -> Settings:
    """Same workspace, but with a small rate limit and batch ceiling so the
    ingest limits can be reached without pushing thousands of reports."""
    return Settings(map_key="", host="127.0.0.1", port=8000, db_path=root / "sensors.sqlite3",
                    cache_days=30, tile_cache_dir=root / "tiles", job_dir=root / "jobs", job_workers=1,
                    backup_dir=root / "backups", backup_interval_minutes=0, backup_keep=3,
                    drone_dir=root / "drone", position_stale_seconds=300, position_max_speed_kmh=180,
                    drone_max_pixels=1_000_000, drone_mosaic_max_pixels=2_000_000,
                    telemetry_max_batch=3, telemetry_requests_per_minute=12)


def check_feed_token_auth_is_scoped_to_one_feed(root: Path) -> None:
    """`POST /api/feeds/positions/{id}` is the only route with no browser
    session, so its per-feed `X-Feed-Token` is the entire authentication story
    for field devices.

    The property that matters beyond "a wrong token is rejected" is *scoping*: a
    token is a credential for one feed, not for the installation. A leaked
    tablet token must not let its holder write positions into another feed - or
    another incident - so every rejection below is also checked to have
    ingested nothing."""
    with TestClient(create_app(feed_settings(root))) as client:
        def make_incident(name: str) -> str:
            response = client.post("/api/operations/incidents", json={"name": name})
            assert response.status_code == 201, response.text
            return response.json()["incident"]["id"]

        def make_feed(incident_id: str, name: str) -> tuple[str, str]:
            response = client.post(f"/api/operations/incidents/{incident_id}/position-feeds",
                                   json={"name": name, "provider": "test"})
            assert response.status_code == 201, response.text
            return response.json()["id"], response.json()["ingest_token"]

        first_incident = make_incident("Token scope A")
        second_incident = make_incident("Token scope B")
        feed_a, token_a = make_feed(first_incident, "Tablet A")
        feed_b, token_b = make_feed(first_incident, "Tablet B")
        feed_c, token_c = make_feed(second_incident, "Other incident tablet")
        assert len({token_a, token_b, token_c}) == 3, "feeds shared an ingest token"

        now = datetime.now(timezone.utc).replace(microsecond=0)

        def report(external_id: str) -> dict:
            return {"external_id": external_id, "callsign": "Engine 7",
                    "observed_at": now.isoformat(), "latitude": 48.0, "longitude": 11.0}

        def push(source_id: str, external_id: str, headers: dict | None) -> int:
            return client.post(f"/api/feeds/positions/{source_id}",
                               json={"positions": [report(external_id)]},
                               headers=headers or {}).status_code

        def stored(incident_id: str) -> int:
            listed = client.get(f"/api/operations/incidents/{incident_id}/vehicle-positions/latest")
            assert listed.status_code == 200, listed.text
            return len(listed.json()["features"])

        # The token issued for a feed authenticates that feed.
        assert push(feed_a, "a-1", {"X-Feed-Token": token_a}) == 202
        assert stored(first_incident) == 1

        # ...and nothing else. A token is scoped to one feed, in one incident.
        assert push(feed_b, "cross-1", {"X-Feed-Token": token_a}) == 401
        assert push(feed_a, "cross-2", {"X-Feed-Token": token_b}) == 401
        assert push(feed_c, "cross-3", {"X-Feed-Token": token_a}) == 401
        assert push(feed_a, "cross-4", {"X-Feed-Token": token_c}) == 401

        # A missing header is rejected exactly like a wrong one - no anonymous
        # ingest, and no "absent means skip the check" hole.
        assert push(feed_a, "no-header", None) == 401
        assert push(feed_a, "empty", {"X-Feed-Token": ""}) == 401
        assert push(feed_a, "blank", {"X-Feed-Token": "   "}) == 401
        assert push(feed_a, "guessed", {"X-Feed-Token": "not-the-token"}) == 401
        # A token's SHA-256 is what is stored; sending the hash must not work.
        assert push(feed_a, "hash", {"X-Feed-Token": hashlib.sha256(token_a.encode()).hexdigest()}) == 401

        # None of the refusals wrote a position anywhere.
        assert stored(first_incident) == 1
        assert stored(second_incident) == 0

        # An unknown feed id is unavailable rather than authenticated.
        assert push("00000000000000000000000000000000", "ghost", {"X-Feed-Token": token_a}) == 400

        # Rotation revokes the old credential and issues one that is still
        # scoped to the same single feed.
        rotated = client.post(
            f"/api/operations/incidents/{first_incident}/position-feeds/{feed_a}/rotate-token")
        assert rotated.status_code == 200, rotated.text
        new_token = rotated.json()["ingest_token"]
        assert new_token != token_a
        assert push(feed_a, "old-token", {"X-Feed-Token": token_a}) == 401
        assert push(feed_a, "new-token", {"X-Feed-Token": new_token}) == 202
        assert push(feed_b, "new-token-cross", {"X-Feed-Token": new_token}) == 401
        assert stored(first_incident) == 1  # same callsign, newer fix

        # The token and its hash never appear in any read response.
        listed = client.get(f"/api/operations/incidents/{first_incident}/position-feeds")
        assert listed.status_code == 200
        body = listed.text
        for secret in (token_a, token_b, new_token):
            assert secret not in body, "an ingest token leaked from a read route"
        assert "token_hash" not in body and "ingest_token" not in body

        # Batch limits are enforced for an authenticated device too.
        authorised = {"X-Feed-Token": new_token}
        empty = client.post(f"/api/feeds/positions/{feed_a}", json={"positions": []}, headers=authorised)
        assert empty.status_code == 400, empty.text
        oversized = client.post(
            f"/api/feeds/positions/{feed_a}",
            json={"positions": [report(f"big-{index}") for index in range(4)]}, headers=authorised)
        assert oversized.status_code == 400, oversized.text
        not_json = client.post(f"/api/feeds/positions/{feed_a}", content=b"{not json",
                               headers={**authorised, "Content-Type": "application/json"})
        assert not_json.status_code == 400, not_json.text

        # The per-feed rate limit eventually answers 429 rather than 401 - the
        # device is authenticated, just too talkative.
        codes = {push(feed_a, f"burst-{index}", authorised) for index in range(20)}
        assert 429 in codes, codes
        assert 401 not in codes, codes


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        with TestClient(create_app(settings(root))) as client:
            response = client.post("/api/operations/incidents", json={"name": "Sensor test"})
            assert response.status_code == 201, response.text
            incident = response.json()["incident"]; incident_id = incident["id"]
            resource_response = client.post(
                f"/api/operations/incidents/{incident_id}/resources",
                json={"callsign": "Engine 7", "unit_type": "engine"},
            )
            assert resource_response.status_code == 201, resource_response.text
            resource = resource_response.json()

            created = client.post(
                f"/api/operations/incidents/{incident_id}/position-feeds",
                json={"name": "Vehicle gateway", "provider": "test", "metadata": {"protocol": "json-v1"}},
            )
            assert created.status_code == 201, created.text
            source, token = created.json(), created.json()["ingest_token"]
            listed = client.get(f"/api/operations/incidents/{incident_id}/position-feeds").json()
            assert len(listed) == 1 and "ingest_token" not in listed[0] and "token_hash" not in listed[0]

            now = datetime.now(timezone.utc).replace(microsecond=0)
            positions = [
                {"external_id": "p1", "callsign": "Engine 7", "resource_id": resource["id"],
                 "observed_at": (now - timedelta(seconds=120)).isoformat(), "latitude": 48.0, "longitude": 11.0,
                 "accuracy_m": 8},
                {"external_id": "p2", "callsign": "Engine 7", "resource_id": resource["id"],
                 "observed_at": (now - timedelta(seconds=60)).isoformat(), "latitude": 48.0001, "longitude": 11.0001,
                 "heading_deg": 45},
                {"external_id": "p3", "callsign": "Engine 7", "resource_id": resource["id"],
                 "observed_at": now.isoformat(), "latitude": 49.0, "longitude": 13.0},
            ]
            ingested = client.post(f"/api/feeds/positions/{source['id']}", json={"positions": positions},
                                   headers={"X-Feed-Token": token})
            assert ingested.status_code == 202, ingested.text
            assert ingested.json()["accepted"] == 3
            replay = client.post(f"/api/feeds/positions/{source['id']}", json={"positions": positions},
                                 headers={"X-Feed-Token": token})
            assert replay.status_code == 202 and replay.json()["replayed"] == 3
            assert client.post(f"/api/feeds/positions/{source['id']}", json={"positions": positions},
                               headers={"X-Feed-Token": "wrong"}).status_code == 401
            conflict = [{**positions[0], "latitude": 47.0}]
            assert client.post(f"/api/feeds/positions/{source['id']}", json={"positions": conflict},
                               headers={"X-Feed-Token": token}).status_code == 400

            latest = client.get(f"/api/operations/incidents/{incident_id}/vehicle-positions/latest").json()
            assert len(latest["features"]) == 1
            assert latest["features"][0]["properties"]["quality"]["implausible_speed"] is True
            tracks = client.get(f"/api/operations/incidents/{incident_id}/vehicle-tracks").json()
            assert len(tracks["features"]) == 2, tracks
            interpolated = client.get(
                f"/api/operations/incidents/{incident_id}/vehicle-positions/interpolate",
                params={"at": (now - timedelta(seconds=90)).isoformat(), "source_id": source["id"],
                        "callsign": "Engine 7"},
            )
            assert interpolated.status_code == 200, interpolated.text
            assert interpolated.json()["properties"]["estimated"] is True

            rotated = client.post(
                f"/api/operations/incidents/{incident_id}/position-feeds/{source['id']}/rotate-token"
            ).json()
            new_token = rotated["ingest_token"]
            extra = [{"external_id": "p4", "callsign": "Engine 8", "observed_at": now.isoformat(),
                      "latitude": 48.1, "longitude": 11.1}]
            assert client.post(f"/api/feeds/positions/{source['id']}", json={"positions": extra},
                               headers={"X-Feed-Token": token}).status_code == 401
            assert client.post(f"/api/feeds/positions/{source['id']}", json={"positions": extra},
                               headers={"X-Feed-Token": new_token}).status_code == 202

            mission_response = client.post(
                f"/api/operations/incidents/{incident_id}/drone-missions",
                json={"name": "Nadir sweep", "aircraft": "UAS-1", "operator": "Air desk",
                      "started_at": now.isoformat()},
            )
            assert mission_response.status_code == 201, mission_response.text
            mission = mission_response.json()
            first_corners = [[11.0, 48.001], [11.001, 48.001], [11.001, 48.0], [11.0, 48.0]]
            second_corners = [[11.001, 48.001], [11.002, 48.001], [11.002, 48.0], [11.001, 48.0]]
            assets = []
            for index, (colour, corners) in enumerate((((220, 40, 20), first_corners), ((20, 80, 220), second_corners))):
                upload = client.post(
                    f"/api/operations/incidents/{incident_id}/drone-missions/{mission['id']}/assets",
                    params={"filename": f"frame-{index}.png", "captured_at": (now + timedelta(seconds=index)).isoformat(),
                            "corners": json.dumps(corners), "georef_kind": "nadir",
                            "metadata": json.dumps({"sequence": index})},
                    content=image_bytes(colour), headers={"Content-Type": "image/png"},
                )
                assert upload.status_code == 201, upload.text
                asset = upload.json(); assets.append(asset)
                assert asset["georef_status"] == "operator_corners" and asset["offline_source_id"]
                record = client.app.state.offline_sources.load(asset["offline_source_id"])
                with rasterio.open(client.app.state.offline_sources._stored_path(record)) as dataset:
                    assert dataset.crs.to_string() == "EPSG:4326" and dataset.count == 4
                thumbnail = client.get(
                    f"/api/operations/incidents/{incident_id}/drone-missions/{mission['id']}/assets/{asset['id']}/thumbnail"
                )
                assert thumbnail.status_code == 200 and thumbnail.headers["content-type"].startswith("image/jpeg")

            raw_only = client.post(
                f"/api/operations/incidents/{incident_id}/drone-missions/{mission['id']}/assets",
                params={"filename": "oblique.png"}, content=image_bytes((10, 10, 10)),
            )
            assert raw_only.status_code == 201 and raw_only.json()["georef_status"] == "unreferenced"
            assert raw_only.json()["offline_source_id"] is None
            assert client.post(
                f"/api/operations/incidents/{incident_id}/drone-missions/{mission['id']}/assets",
                params={"filename": "bad.jpg"}, content=b"not an image",
            ).status_code == 400

            mosaic = client.post(
                f"/api/operations/incidents/{incident_id}/drone-missions/{mission['id']}/mosaics",
                json={"name": "Air desk visual mosaic", "asset_ids": [assets[1]["id"], assets[0]["id"]]},
            )
            assert mosaic.status_code == 201, mosaic.text
            product = mosaic.json()
            assert product["metadata"]["ordered_asset_ids"] == sorted([asset["id"] for asset in assets])
            mosaic_record = client.app.state.offline_sources.load(product["offline_source_id"])
            tile = client.get(f"/offline-tiles/{mosaic_record['id']}/0/0/0")
            assert tile.status_code == 200 and tile.headers["content-type"] == "image/png"

            audit = client.app.state.db.conn.execute(
                "SELECT entity_type FROM incident_audit_log WHERE incident_id=?", (incident_id,)
            ).fetchall()
            entity_types = {item["entity_type"] for item in audit}
            assert {"position_feed", "drone_mission", "drone_asset", "drone_mosaic"}.issubset(entity_types)
            manifest = client.get(f"/api/operations/incidents/{incident_id}/sensor-files-manifest").json()
            assert manifest["complete"] is True and manifest["database_contains"]["position_reports"] == 4
            assert all(item["sha256"] and item["size_bytes"] > 0 for item in manifest["external_files"])

        # Clean-root relocation proves the local database plus the two media
        # directories survive a disconnected command-laptop handover.
        receiver = root / "receiver"; receiver.mkdir()
        shutil.copy2(root / "sensors.sqlite3", receiver / "sensors.sqlite3")
        shutil.copytree(root / "tiles", receiver / "tiles")
        shutil.copytree(root / "drone", receiver / "drone")
        with TestClient(create_app(settings(receiver))) as receiving_client:
            assert len(receiving_client.get(
                f"/api/operations/incidents/{incident_id}/vehicle-positions/latest"
            ).json()["features"]) == 2
            received_assets = receiving_client.get(
                f"/api/operations/incidents/{incident_id}/drone-missions/{mission['id']}/assets"
            ).json()
            assert len(received_assets) == 3
            original = receiving_client.get(
                f"/api/operations/incidents/{incident_id}/drone-missions/{mission['id']}/assets/{assets[0]['id']}/original"
            )
            assert original.status_code == 200 and original.content == image_bytes((220, 40, 20))
            assert receiving_client.get(
                f"/offline-tiles/{product['offline_source_id']}/0/0/0"
            ).status_code == 200
            received_manifest = receiving_client.get(
                f"/api/operations/incidents/{incident_id}/sensor-files-manifest"
            ).json()
            assert received_manifest["complete"] is True
            assert [(item["path"], item["sha256"]) for item in received_manifest["external_files"]] == [
                (item["path"], item["sha256"]) for item in manifest["external_files"]
            ]

    with tempfile.TemporaryDirectory() as temp:
        check_feed_token_auth_is_scoped_to_one_feed(Path(temp))

    print("vehicle/drone tests passed")


if __name__ == "__main__":
    main()
