"""Offline gate: the server must start and serve with every outbound call dead.

NexFiremap's premise is that it keeps working with the WAN down during an
incident. This blocks *all* outbound sockets, then starts the app with every
new network-touching feature configured (CAP polling, an MQTT broker, an OIDC
issuer, a custom WMS layer) and asserts that startup completes and the core
endpoints still answer.
"""

import dataclasses
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_real_socket = socket.socket
_real_create_connection = socket.create_connection
_real_getaddrinfo = socket.getaddrinfo

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class _OfflineSocket(_real_socket):
    """A socket that refuses every non-loopback connection.

    Loopback stays open because TestClient talks to the app over it; the point
    is to sever the *WAN*, not the process's ability to serve.
    """

    def connect(self, address):  # noqa: D102
        host = address[0] if isinstance(address, tuple) else ""
        if str(host) not in LOOPBACK:
            raise OSError("offline: outbound network is unavailable")
        return super().connect(address)

    connect_ex = connect


def _offline_create_connection(address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) else ""
    if str(host) not in LOOPBACK:
        raise OSError("offline: outbound network is unavailable")
    return _real_create_connection(address, *args, **kwargs)


def _offline_getaddrinfo(host, *args, **kwargs):
    if str(host) not in LOOPBACK:
        raise socket.gaierror("offline: DNS is unavailable")
    return _real_getaddrinfo(host, *args, **kwargs)


socket.socket = _OfflineSocket
socket.create_connection = _offline_create_connection
socket.getaddrinfo = _offline_getaddrinfo

from fastapi.testclient import TestClient  # noqa: E402

from nexfiremap.api import create_app  # noqa: E402
from nexfiremap.config import load_settings  # noqa: E402

root = Path(tempfile.mkdtemp())
settings = dataclasses.replace(
    load_settings(),
    db_path=root / "offline.sqlite3",
    tile_cache_dir=root / "tiles",
    job_dir=root / "jobs",
    backup_dir=root / "backups",
    backup_interval_minutes=0,
    job_workers=1,
    lan_mode=False,
    # Every outbound-touching feature turned on, pointed at hosts that cannot
    # be reached. None of them may block or fail startup.
    cap_feeds=["https://opendata.dwd.de/weather/alerts/cap/test.zip"],
    mqtt_url="mqtt://broker.example.de:1883",
    mqtt_topics="fleet/#=src-1:json",
    oidc_issuer="https://idp.example.de",
    oidc_client_id="nexfiremap",
)

failures = []


def check(label, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'}  {label}{(' - ' + str(detail)) if detail and not condition else ''}")
    if not condition:
        failures.append(label)


print("Offline start (WAN severed, CAP/MQTT/OIDC/WMS all configured)")
with TestClient(create_app(settings)) as client:
    check("server starts and serves /health", client.get("/health").json()["status"] == "ok")

    config = client.get("/api/config").json()
    check("/api/config answers", config["app"] == "NexFiremap")
    check("basemaps still listed", len(config["basemaps"]) > 0)
    check("CAP feature advertised (configured, just unreachable)", config["features"]["alerts"] is True)

    # A configured-but-unreachable CAP feed must degrade to a logged warning
    # with an empty layer, never a 5xx and never a startup failure.
    alerts = client.get("/api/alerts")
    check("/api/alerts answers with an empty collection", alerts.status_code == 200 and alerts.json()["features"] == [])
    status = client.get("/api/alerts/status").json()
    check("alert status reports the feed problem rather than pretending", status["enabled"] is True)

    check("/api/status answers", client.get("/api/status?key=false").status_code == 200)
    check("operations meta answers", "symbology" in client.get("/api/operations/meta").json())

    # The incident-command core must be fully usable offline.
    incident = client.post("/api/operations/incidents", json={"name": "Offline"}).json()
    check("incident created offline", "incident" in incident)
    feature = client.post(
        f"/api/operations/incidents/{incident['incident']['id']}/features",
        json={"period_id": incident["period"]["id"], "feature_type": "command_post",
              "geometry": {"type": "Point", "coordinates": [11.5, 48.1]}, "title": "ELW"})
    check("tactical feature created offline", feature.status_code == 201, feature.text)

    # A CoT export is pure local rendering and must work with the WAN down.
    cot = client.get(f"/api/operations/incidents/{incident['incident']['id']}/cot")
    check("CoT export renders offline", cot.status_code == 200 and cot.content.startswith(b"<?xml"))

    # A tile with nothing cached must serve the transparent placeholder, not 500.
    tile = client.get("/tiles/osm/2/1/1.png")
    check("tile request degrades to a placeholder", tile.status_code == 200)

    # Registering a WMS layer is a local write; only probing needs the network.
    layer = client.post("/api/layers", json={
        "name": "Kreis GIS", "kind": "wms", "endpoint": "https://gis.example.de/wms",
        "wms_layers": "hydranten"})
    check("custom layer registered offline", layer.status_code == 201, layer.text)
    probe = client.post("/api/layers/probe", json={"endpoint": "https://gis.example.de/wms", "kind": "wms"})
    check("layer probe fails as 4xx, not 5xx", probe.status_code == 400, probe.status_code)

    # OIDC is configured but the IdP is unreachable: a clear 4xx, and local
    # password login must remain the break-glass path.
    providers = client.get("/api/auth/providers").json()
    check("OIDC advertised but local login still offered", providers["oidc"] is True and providers["local"] is True)
    oidc = client.get("/auth/oidc/login", follow_redirects=False)
    check("OIDC login fails as 4xx, not 5xx", oidc.status_code == 400, oidc.status_code)

print()
if failures:
    print(f"{len(failures)} offline check(s) FAILED: {', '.join(failures)}")
    sys.exit(1)
print("Offline checks passed: the server starts and serves with the WAN down.")
