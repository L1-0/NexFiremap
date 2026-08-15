"""Generic CAD/dispatch webhook ingest: mapping, safety, and dead-lettering.

The central assertion here is a negative one: the mapping DSL must never
evaluate anything. This endpoint is authenticated by a token alone and takes
arbitrary JSON from an outside system, so an expression evaluator in the
mapping would be a remote-code-execution surface reachable by anyone who ever
learns a hook URL.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from nexfiremap.api import create_app
from nexfiremap.config import load_settings
from nexfiremap.db import Database
from nexfiremap.ingest import IngestError
from nexfiremap.ingest import webhook
from nexfiremap.operations import OperationsStore, default_period
from nexfiremap.telemetry import TelemetryManager
from nexfiremap.webhooks import DEADLETTER_PER_HOOK, WebhookError, WebhookManager

# Shaped like a real dispatch payload: nested, with names nobody would guess.
CAD_PAYLOAD = {
    "einsatz": {"nummer": "2026-0815", "stichwort": "B3 Waldbrand"},
    "fahrzeug": {"funkrufname": "Florian Muenchen 11/1", "status": 3},
    "position": {"breite": 48.1372, "laenge": 11.5755, "genauigkeit": 8},
    "zeitstempel": "2026-08-14T10:15:30+02:00",
}

MAPPING = {
    "callsign": "$.fahrzeug.funkrufname",
    "latitude": "$.position.breite",
    "longitude": "$.position.laenge",
    "accuracy_m": "$.position.genauigkeit",
    "observed_at": "$.zeitstempel",
    "external_id": "$.einsatz.nummer",
}


def check_mapping_resolution() -> None:
    (report,) = webhook.parse(json.dumps(CAD_PAYLOAD).encode(), mapping=MAPPING)
    assert report["callsign"] == "Florian Muenchen 11/1"
    # A bare path must preserve the payload's own type - a latitude arriving
    # as a JSON number must not be stringified and re-parsed.
    assert report["latitude"] == 48.1372 and isinstance(report["latitude"], float)
    assert report["accuracy_m"] == 8.0
    assert report["observed_at"] == "2026-08-14T08:15:30.000Z", "the +02:00 offset must normalise to UTC"
    assert report["external_id"] == "2026-0815"

    # Array indexing.
    nested = {"units": [{"id": "A", "lat": 48.1, "lon": 11.5}, {"id": "B", "lat": 49.0, "lon": 12.0}]}
    (second,) = webhook.parse(json.dumps(nested).encode(), mapping={
        "callsign": "$.units[1].id", "latitude": "$.units[1].lat", "longitude": "$.units[1].lon"})
    assert second["callsign"] == "B" and second["latitude"] == 49.0

    # A literal (no "$.") passes through - a single-unit hook can hardcode its
    # own callsign rather than expecting the vendor to send one.
    (literal,) = webhook.parse(json.dumps({"la": 48.1, "lo": 11.5}).encode(), mapping={
        "callsign": "Drehleiter 1", "latitude": "$.la", "longitude": "$.lo"})
    assert literal["callsign"] == "Drehleiter 1"

    # A template interpolates and stringifies.
    assert webhook.resolve("unit {} at $.position.breite".replace("{}", "$.fahrzeug.funkrufname"),
                           CAD_PAYLOAD) == "unit Florian Muenchen 11/1 at 48.1372"

    # A missing optional field is None, not an error - a CAD routinely omits
    # what it has nothing for, and one absent field must not reject a message.
    assert webhook.resolve("$.nope.missing", CAD_PAYLOAD) is None
    assert webhook.resolve("$.units[9].id", nested) is None


def check_no_expression_evaluation() -> None:
    """The security property: mappings are lookup and substitution only.

    Each expression below would be dangerous if anything evaluated it. All must
    come back as inert text (or fail to resolve), never as a computed value and
    never with a side effect.
    """
    payload = {"lat": 48.1, "lon": 11.5, "x": 2}
    marker = Path(tempfile.gettempdir()) / "nexfiremap_webhook_rce_marker"
    if marker.exists():
        marker.unlink()

    dangerous = [
        "__import__('os').system('echo pwned')",
        "{{7*7}}",
        "${7*7}",
        "$.x + $.x",
        "eval('1+1')",
        f"__import__('pathlib').Path({str(marker)!r}).write_text('x')",
        "{}.__class__.__mro__[1].__subclasses__()",
    ]
    for expression in dangerous:
        result = webhook.resolve(expression, payload)
        # Whatever comes back must be the literal text, never a computed value.
        assert result == expression or result is None or "$." in expression, \
            f"{expression!r} resolved to {result!r}"
        assert result != 49, f"{expression!r} was evaluated"
        assert result != 2, f"{expression!r} was evaluated"
    assert not marker.exists(), "a mapping expression touched the filesystem"

    # "$.x + $.x" contains paths, so it is treated as a *template*: the paths
    # substitute and the "+" stays literal text. Substitution, not arithmetic.
    assert webhook.resolve("$.x + $.x", payload) == "2 + 2"


def check_mapping_validation() -> None:
    for label, mapping in (
        ("not an object", ["a"]),
        ("empty", {}),
        ("unknown target", {**MAPPING, "system_command": "$.a"}),
        ("missing callsign", {"latitude": "$.a", "longitude": "$.b"}),
        ("missing longitude", {"callsign": "$.a", "latitude": "$.b"}),
    ):
        try:
            webhook.validate_mapping(mapping)
            raise AssertionError(f"{label} mapping was accepted")
        except IngestError:
            pass
    assert webhook.validate_mapping(MAPPING) == MAPPING

    # Targets are restricted as well as expressions, so a mapping cannot inject
    # arbitrary keys into a stored report.
    assert "callsign" in webhook.MAPPABLE_FIELDS and "system_command" not in webhook.MAPPABLE_FIELDS


def check_adapter_never_claims_json() -> None:
    """Regression guard: the webhook adapter must not be reachable by
    content-type sniffing.

    It cannot parse anything without a mapping, so claiming
    ``application/json`` makes `adapter_for_media_type` hand it every ordinary
    JSON batch posted to `/api/feeds/positions/{id}` - which then fails with
    "a mapping is required" and breaks the native feed for every existing
    client. That is exactly what happened when this adapter was first added.
    """
    from nexfiremap import ingest

    assert webhook.MEDIA_TYPES == (), "the webhook adapter must claim no media types"
    matched = ingest.adapter_for_media_type("application/json", contract=ingest.CONTRACT_POSITION)
    assert matched is None or matched.NAME != "webhook", \
        f"application/json resolved to {getattr(matched, 'NAME', matched)!r}"
    # ...and the adapters that *should* answer for their formats still do.
    assert ingest.adapter_for_media_type("application/xml", contract=ingest.CONTRACT_POSITION).NAME == "cot"
    assert ingest.adapter_for_media_type("text/plain", contract=ingest.CONTRACT_POSITION).NAME == "nmea"


def check_describe() -> None:
    """An undocumented vendor payload has to be inspectable, or a broken
    integration is undiagnosable."""
    described = webhook.describe(json.dumps(CAD_PAYLOAD).encode())
    paths = {item["path"] for item in described["paths"]}
    assert "$.fahrzeug.funkrufname" in paths
    assert "$.position.breite" in paths
    # Every suggested path must actually resolve against the payload it came
    # from, or the suggestion is worse than useless.
    for item in described["paths"]:
        assert webhook.resolve(item["path"], CAD_PAYLOAD) is not None, item["path"]


def check_batch_and_rejections() -> None:
    batch = [CAD_PAYLOAD, {**CAD_PAYLOAD, "fahrzeug": {"funkrufname": "FL 11/2", "status": 1}}]
    reports = webhook.parse(json.dumps(batch).encode(), mapping=MAPPING)
    assert len(reports) == 2 and reports[1]["callsign"] == "FL 11/2"

    for label, payload, mapping in (
        ("not JSON", b"<xml/>", MAPPING),
        ("no mapping", json.dumps(CAD_PAYLOAD).encode(), None),
        ("missing coordinates", json.dumps({"a": 1}).encode(), MAPPING),
        ("latitude out of range", json.dumps(
            {**CAD_PAYLOAD, "position": {"breite": 991, "laenge": 11.5}}).encode(), MAPPING),
    ):
        try:
            webhook.parse(payload, mapping=mapping)
            raise AssertionError(f"{label} was accepted")
        except IngestError:
            pass


def check_manager_and_deadletter() -> None:
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "hooks.sqlite3")
        try:
            settings = dataclasses.replace(load_settings(), db_path=Path(temp) / "hooks.sqlite3")
            store = OperationsStore(db)
            telemetry = TelemetryManager(store, settings)
            incident = store.create_incident({"name": "CAD"}, "IC")
            store.create_period(incident["id"], default_period(), "IC")
            feed = telemetry.create_source(incident["id"], {"name": "Leitstelle"}, "IC")

            manager = WebhookManager(db, telemetry)
            hook = manager.create({"name": "FE2", "incident_id": incident["id"],
                                   "source_id": feed["id"], "mapping": MAPPING}, "IC")
            token = hook["token"]
            # The token is shown once and only its hash is stored.
            assert token and hook["token_shown_once"]
            assert "token_hash" not in manager.get(hook["id"])
            assert "token" not in manager.get(hook["id"])

            result = manager.receive(hook["id"], token, json.dumps(CAD_PAYLOAD).encode())
            assert result["accepted"] == 1

            # The position went through TelemetryManager, so it appears in the
            # same view every other transport feeds.
            latest = telemetry.latest(incident["id"])
            assert latest["features"][0]["properties"]["callsign"] == "Florian Muenchen 11/1"

            # Replay safety comes free from that shared path.
            replay = manager.receive(hook["id"], token, json.dumps(CAD_PAYLOAD).encode())
            assert (replay["accepted"], replay["replayed"]) == (0, 1)

            try:
                manager.receive(hook["id"], "wrong-token", b"{}")
                raise AssertionError("a bad token was accepted")
            except PermissionError:
                pass

            # An unmappable payload is KEPT, not dropped - it is the only way
            # to repair a mapping against an undocumented vendor format.
            try:
                manager.receive(hook["id"], token, json.dumps({"totally": "different"}).encode())
                raise AssertionError("an unmappable payload was accepted")
            except WebhookError:
                pass
            failures = manager.failures(hook["id"])
            assert len(failures) == 1
            assert "totally" in failures[0]["body"]
            assert any(p["path"] == "$.totally" for p in failures[0]["suggested_paths"]), \
                "a stored failure must suggest the paths it actually contains"

            # The dead-letter store is bounded, or a misconfigured hook fills
            # a field laptop's disk.
            for index in range(DEADLETTER_PER_HOOK + 10):
                manager.record_failure(hook["id"], "boom", json.dumps({"n": index}).encode())
            assert len(manager.failures(hook["id"])) == DEADLETTER_PER_HOOK

            # Deactivating stops deliveries immediately.
            manager.update(hook["id"], {"active": False})
            try:
                manager.receive(hook["id"], token, json.dumps(CAD_PAYLOAD).encode())
                raise AssertionError("an inactive hook accepted a delivery")
            except WebhookError:
                pass

            # Rotation invalidates the old credential.
            manager.update(hook["id"], {"active": True})
            rotated = manager.rotate_token(hook["id"])
            assert rotated["token"] != token
            try:
                manager.receive(hook["id"], token, json.dumps(CAD_PAYLOAD).encode())
                raise AssertionError("a rotated-away token still worked")
            except PermissionError:
                pass

            # A hook must name a feed that exists on its incident, or every
            # delivery would authenticate and then fail confusingly.
            try:
                manager.create({"name": "bad", "incident_id": incident["id"],
                                "source_id": "nope", "mapping": MAPPING}, "IC")
                raise AssertionError("a hook was created against an unknown feed")
            except WebhookError:
                pass

            assert manager.delete(hook["id"]) is True
            assert manager.delete(hook["id"]) is False
        finally:
            db.close()


def check_http_surface() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = dataclasses.replace(
            load_settings(), db_path=root / "api.sqlite3", tile_cache_dir=root / "tiles", lan_mode=False)
        with TestClient(create_app(settings)) as client:
            incident = client.post("/api/operations/incidents", json={"name": "CAD"}).json()["incident"]
            feed = client.post(f"/api/operations/incidents/{incident['id']}/position-feeds",
                               json={"name": "Leitstelle"}).json()
            created = client.post("/api/webhooks", json={
                "name": "FE2", "incident_id": incident["id"],
                "source_id": feed["id"], "mapping": MAPPING})
            assert created.status_code == 201, created.text
            hook = created.json()

            delivered = client.post(f"/api/ingest/webhook/{hook['id']}", json=CAD_PAYLOAD,
                                    headers={"X-Webhook-Token": hook["token"]})
            assert delivered.status_code == 202, delivered.text
            assert delivered.json()["accepted"] == 1

            # Token in the URL, for CAD products that can only be given a URL.
            # A different vehicle, so this is genuinely a new report: this
            # hook's mapping points external_id at the Einsatz number, which
            # identifies the *incident* - see below.
            assert client.post(
                f"/api/ingest/webhook/{hook['id']}?token={hook['token']}",
                json={**CAD_PAYLOAD, "einsatz": {"nummer": "2026-0816"},
                      "fahrzeug": {"funkrufname": "Florian Muenchen 11/2"}},
            ).status_code == 202

            # The configuration trap this mapping contains, asserted so the
            # behaviour is pinned: mapping external_id to a dispatch number
            # makes it stable per incident rather than per position, so the
            # vehicle's *second* fix collides and is refused. That rejection is
            # correct - TelemetryManager will not silently overwrite history -
            # but it means a hook mapped this way stops updating a moving
            # vehicle after its first report. Leaving external_id unmapped is
            # the safe default.
            collided = client.post(f"/api/ingest/webhook/{hook['id']}", headers={
                "X-Webhook-Token": hook["token"]},
                json={**CAD_PAYLOAD, "zeitstempel": "2026-08-14T10:16:30+02:00"})
            assert collided.status_code == 400
            assert "external_id" in collided.json()["detail"]
            assert client.post(f"/api/ingest/webhook/{hook['id']}", json=CAD_PAYLOAD,
                               headers={"X-Webhook-Token": "no"}).status_code == 401

            # An invalid mapping is refused at configuration time, not at 3am.
            assert client.post("/api/webhooks", json={
                "name": "bad", "incident_id": incident["id"], "source_id": feed["id"],
                "mapping": {"latitude": "$.a"}}).status_code == 400

            assert client.get(f"/api/webhooks/{hook['id']}/failures").json() == []
            assert client.delete(f"/api/webhooks/{hook['id']}").status_code == 200


def check_security_carveout() -> None:
    """The receive path is exempt from the session gate; the administration
    routes are emphatically not."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = dataclasses.replace(
            load_settings(), db_path=root / "sec.sqlite3", tile_cache_dir=root / "tiles",
            lan_mode=True, admin_password="a-long-enough-password")
        with TestClient(create_app(settings)) as client:
            # Reaches the handler's own token check rather than the session gate.
            assert client.post("/api/ingest/webhook/whatever", json={}).status_code in (400, 401)
            # Administration stays session-gated.
            assert client.get("/api/webhooks").status_code == 401
            assert client.post("/api/webhooks", json={"name": "x", "incident_id": "y",
                                                      "source_id": "z", "mapping": MAPPING}).status_code == 401
            # ...and a GET on the receive path is not carved out either.
            assert client.get("/api/ingest/webhook/whatever").status_code in (401, 405)


def main() -> None:
    check_mapping_resolution()
    check_no_expression_evaluation()
    check_mapping_validation()
    check_adapter_never_claims_json()
    check_describe()
    check_batch_and_rejections()
    check_manager_and_deadletter()
    check_http_surface()
    check_security_carveout()
    print("Webhook/CAD ingest checks passed.")


if __name__ == "__main__":
    main()
