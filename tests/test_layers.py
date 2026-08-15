"""Custom WMS/WMTS/XYZ layer registry, GetMap bbox math and cache integration.

The bbox assertions use published EPSG:3857 constants rather than values
recomputed from this codebase's own helpers - a tile bbox that is
self-consistently wrong would still serve a map, just of the wrong place, so
the check has to come from outside.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.db import Database
from nexfiremap.geo import tile_bounds_3857, tile_bounds_4326
from nexfiremap.layers import LayerError, LayerRegistry

# Half the projected world in metres, from the EPSG:3857 definition.
HALF_WORLD = 20037508.342789244


def check_tile_bounds() -> None:
    # Zoom 0 is one tile covering the whole projected world.
    west, south, east, north = tile_bounds_3857(0, 0, 0)
    for value, expected in ((west, -HALF_WORLD), (south, -HALF_WORLD),
                            (east, HALF_WORLD), (north, HALF_WORLD)):
        assert abs(value - expected) < 1e-6, f"{value} != {expected}"

    # XYZ counts rows downward from the north-west corner while projected Y
    # increases northward. Tile (1,0,0) is therefore the NORTH-west quadrant:
    # negative X, positive Y. Getting this backwards yields a map that is
    # mirrored top-to-bottom but otherwise looks entirely plausible.
    nw = tile_bounds_3857(1, 0, 0)
    assert abs(nw[0] + HALF_WORLD) < 1e-6 and abs(nw[3] - HALF_WORLD) < 1e-6
    assert abs(nw[1]) < 1e-6 and abs(nw[2]) < 1e-6, "z1 tile (0,0) must be the northern hemisphere"

    se = tile_bounds_3857(1, 1, 1)
    assert abs(se[2] - HALF_WORLD) < 1e-6 and abs(se[1] + HALF_WORLD) < 1e-6

    # Adjacent tiles must share an edge exactly, or seams appear between them.
    left, right = tile_bounds_3857(3, 2, 4), tile_bounds_3857(3, 3, 4)
    assert abs(left[2] - right[0]) < 1e-9
    upper, lower = tile_bounds_3857(3, 2, 3), tile_bounds_3857(3, 2, 4)
    assert abs(upper[1] - lower[3]) < 1e-9

    # The 4326 form of the same tile, with Mercator's pole truncation.
    geo = tile_bounds_4326(0, 0, 0)
    assert abs(geo[0] + 180) < 1e-9 and abs(geo[2] - 180) < 1e-9
    assert abs(geo[3] - 85.0511287798066) < 1e-6, geo[3]


def check_validation(registry: LayerRegistry) -> None:
    for label, payload in (
        ("unknown kind", {"name": "x", "kind": "wfs", "url_template": "https://e/{z}/{x}/{y}.png"}),
        ("no name", {"name": "", "kind": "xyz", "url_template": "https://e/{z}/{x}/{y}.png"}),
        ("template missing {y}", {"name": "x", "kind": "xyz", "url_template": "https://e/{z}/{x}.png"}),
        ("wms without layers", {"name": "x", "kind": "wms", "endpoint": "https://e/wms"}),
        ("bad wms version", {"name": "x", "kind": "wms", "endpoint": "https://e/wms",
                             "wms_layers": "a", "wms_version": "1.0.0"}),
        ("bad crs", {"name": "x", "kind": "wms", "endpoint": "https://e/wms",
                     "wms_layers": "a", "wms_crs": "EPSG:31467"}),
        ("bad image format", {"name": "x", "kind": "wms", "endpoint": "https://e/wms",
                              "wms_layers": "a", "image_format": "application/pdf"}),
        ("zoom inverted", {"name": "x", "kind": "xyz", "url_template": "https://e/{z}/{x}/{y}.png",
                           "min_zoom": 12, "max_zoom": 4}),
        # SSRF guard: these URLs are fetched by the server, so a non-http
        # scheme would turn "add a map layer" into "read a host file".
        ("file scheme", {"name": "x", "kind": "wms", "endpoint": "file:///etc/passwd", "wms_layers": "a"}),
        ("scheme-less host", {"name": "x", "kind": "wms", "endpoint": "internal-gis/wms", "wms_layers": "a"}),
    ):
        try:
            registry.create(payload)
            raise AssertionError(f"{label} was accepted")
        except LayerError:
            pass


def check_getmap(registry: LayerRegistry) -> None:
    record = registry.create({
        "name": "Kreis GIS Hydranten", "kind": "wms",
        # A query string already on the endpoint (an access token, a map
        # definition) must survive - some authorities require it.
        "endpoint": "https://gis.example.de/wms?token=abc123",
        "wms_layers": "hydranten,leitungen", "image_format": "image/png",
        "attribution": "Kreis GIS", "licence": "dl-de/by-2-0", "overlay": True,
    })
    assert record["id"].startswith("custom-") and record["tile_ext"] == "png"

    url = registry.getmap_url(record, 12, 2185, 1421)
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.netloc == "gis.example.de" and parts.path == "/wms"
    assert query["token"] == ["abc123"], "endpoint query parameters must be preserved"
    assert query["SERVICE"] == ["WMS"] and query["REQUEST"] == ["GetMap"]
    assert query["LAYERS"] == ["hydranten,leitungen"]
    assert query["WIDTH"] == ["256"] and query["HEIGHT"] == ["256"]
    # WMS 1.3.0 spells it CRS; 1.1.1 spells it SRS.
    assert query["CRS"] == ["EPSG:3857"] and "SRS" not in query
    bbox = [float(v) for v in query["BBOX"][0].split(",")]
    expected = tile_bounds_3857(12, 2185, 1421)
    assert all(abs(a - b) < 1e-6 for a, b in zip(bbox, expected)), (bbox, expected)

    # WMS 1.1.1: SRS, and lon/lat axis order throughout.
    v111 = registry.update(record["id"], {"wms_version": "1.1.1"})
    q111 = parse_qs(urlsplit(registry.getmap_url(v111, 12, 2185, 1421)).query)
    assert q111["SRS"] == ["EPSG:3857"] and "CRS" not in q111

    # WMS 1.3.0 + EPSG:4326 is the axis-order trap: the CRS declares lat,lon,
    # so BBOX must be swapped relative to every other combination. Sending
    # lon,lat here returns either an error or a plausible image of the wrong
    # place, which is the failure this assertion exists to prevent.
    geo = registry.update(record["id"], {"wms_version": "1.3.0", "wms_crs": "EPSG:4326"})
    geo_bbox = [float(v) for v in parse_qs(urlsplit(registry.getmap_url(geo, 12, 2185, 1421)).query)["BBOX"][0].split(",")]
    west, south, east, north = tile_bounds_4326(12, 2185, 1421)
    assert all(abs(a - b) < 1e-6 for a, b in zip(geo_bbox, [south, west, north, east])), geo_bbox

    # CRS:84 exists precisely to mean "4326 but lon,lat", so it is NOT swapped.
    crs84 = registry.update(record["id"], {"wms_crs": "CRS:84"})
    crs84_bbox = [float(v) for v in parse_qs(urlsplit(registry.getmap_url(crs84, 12, 2185, 1421)).query)["BBOX"][0].split(",")]
    assert all(abs(a - b) < 1e-6 for a, b in zip(crs84_bbox, [west, south, east, north])), crs84_bbox


def check_registry_crud(registry: LayerRegistry) -> None:
    jpeg = registry.create({"name": "Ortho", "kind": "wms", "endpoint": "https://gis.example.de/wms",
                            "wms_layers": "dop20", "image_format": "image/jpeg"})
    assert jpeg["tile_ext"] == "jpg"

    # Ids are derived from the name but must be unique, since they become
    # directory names in the tile cache and keys in map-pack manifests.
    twin = registry.create({"name": "Ortho", "kind": "wms", "endpoint": "https://gis.example.de/wms",
                            "wms_layers": "dop40"})
    assert twin["id"] != jpeg["id"], "duplicate names must not collide on id"

    # ...and an id must be stable across a rename, or a rename would orphan
    # every already-cached tile and every manifest pinning them.
    renamed = registry.update(jpeg["id"], {"name": "Orthophoto 20cm"})
    assert renamed["id"] == jpeg["id"] and renamed["name"] == "Orthophoto 20cm"

    # A partial update must still be validated as a whole.
    try:
        registry.update(jpeg["id"], {"wms_layers": ""})
        raise AssertionError("update cleared the required LAYERS parameter")
    except LayerError:
        pass

    # Extensions feed TTL expiry, LRU eviction and the /api/status size figures.
    assert {"png", "jpg"} <= registry.tile_extensions()

    # Deactivating must stop the layer being served immediately - `layer()`
    # returning None is what the tile route reads.
    assert registry.layer(jpeg["id"]) is not None
    registry.update(jpeg["id"], {"active": False})
    assert registry.layer(jpeg["id"]) is None
    assert jpeg["id"] not in {item["id"] for item in registry.list()}
    assert jpeg["id"] in {item["id"] for item in registry.list(include_inactive=True)}

    assert registry.delete(twin["id"]) is True
    assert registry.delete(twin["id"]) is False
    assert registry.layer("does-not-exist") is None


def check_public_layers(registry: LayerRegistry) -> None:
    record = registry.create({"name": "Hydranten", "kind": "wms", "endpoint": "https://gis.example.de/wms",
                              "wms_layers": "h", "overlay": True,
                              "attribution": '<script>alert(1)</script>', "licence": "CC-BY-4.0"})
    published = {item["id"]: item for item in registry.public_layers()}[record["id"]]
    # URL points at our own proxy, never at the upstream endpoint - that is
    # what routes tiles through the cache instead of the browser's network.
    assert published["url"] == f"/tiles/{record['id']}/{{z}}/{{x}}/{{y}}.png"
    assert "<script>" not in published["attribution"], "attribution is operator text and must be escaped"
    assert published["licence"] == "CC-BY-4.0" and published["overlay"] is True
    # The sanitised view must never leak the endpoint, which can carry a token.
    assert "endpoint" not in published and "gis.example.de" not in str(published)


def check_tile_cache_integration() -> None:
    """A registered layer must resolve through TileCache exactly like a
    built-in one - that equivalence is what makes map packs, pinning and
    pruning work on custom layers with no code of their own."""
    import dataclasses

    from nexfiremap.config import load_settings
    from nexfiremap.tiles import TileCache

    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "layers.sqlite3")
        try:
            registry = LayerRegistry(db)
            settings = dataclasses.replace(load_settings(), tile_cache_dir=Path(temp) / "tiles")
            cache = TileCache(settings, registry)

            record = registry.create({"name": "Ortho", "kind": "wms", "endpoint": "https://gis.example.de/wms",
                                      "wms_layers": "dop20", "image_format": "image/jpeg"})
            meta = cache.layer(record["id"])
            assert meta is not None and meta["kind"] == "wms" and meta["tile_ext"] == "jpg"

            # Same cache path layout as a built-in layer - this is precisely
            # why offline manifests and pruning need no changes.
            path = cache.path_for(record["id"], 12, 2185, 1421)
            assert path == settings.tile_cache_dir / record["id"] / "12" / "2185" / "1421.jpg"

            # A built-in id must never be shadowed by a registry row.
            assert cache.layer("osm")["url"].startswith("https://tile.openstreetmap.org")

            # The prune/stats walk must now see .jpg from the custom layer.
            from nexfiremap.tiles import _tile_extensions
            assert "jpg" in _tile_extensions(registry)

            # A cache built without a registry still works (tests, map-pack tooling).
            assert TileCache(settings).layer(record["id"]) is None
        finally:
            db.close()


def check_http_surface() -> None:
    """The registry has to reach the frontend through /api/config, or an
    added layer exists but never appears in the layer switcher."""
    import dataclasses

    from fastapi.testclient import TestClient

    from nexfiremap.api import create_app
    from nexfiremap.config import load_settings

    with tempfile.TemporaryDirectory() as temp:
        settings = dataclasses.replace(
            load_settings(), db_path=Path(temp) / "api.sqlite3",
            tile_cache_dir=Path(temp) / "tiles", lan_mode=False)
        with TestClient(create_app(settings)) as client:
            assert client.get("/api/layers").json() == []

            created = client.post("/api/layers", json={
                "name": "Kreis GIS Hydranten", "kind": "wms",
                "endpoint": "https://gis.example.de/wms", "wms_layers": "hydranten",
                "overlay": True, "licence": "dl-de/by-2-0"})
            assert created.status_code == 201, created.text
            layer_id = created.json()["id"]

            config = client.get("/api/config").json()
            # An `overlay` layer must land in overlays, not basemaps - a
            # hydrant layer belongs *over* the map, not instead of it.
            overlays = {item["id"]: item for item in config["overlays"] if item.get("custom")}
            assert layer_id in overlays, config["overlays"]
            assert overlays[layer_id]["url"] == f"/tiles/{layer_id}/{{z}}/{{x}}/{{y}}.png"
            assert overlays[layer_id]["licence"] == "dl-de/by-2-0"
            assert layer_id not in {item["id"] for item in config["basemaps"]}

            # A non-overlay layer goes the other way.
            base = client.post("/api/layers", json={
                "name": "Ortho", "kind": "xyz",
                "url_template": "https://gis.example.de/{z}/{x}/{y}.png"}).json()
            config = client.get("/api/config").json()
            assert base["id"] in {item["id"] for item in config["basemaps"]}

            # SSRF guard reaches the HTTP surface as a 400, not a 500.
            refused = client.post("/api/layers", json={
                "name": "evil", "kind": "wms", "endpoint": "file:///etc/passwd", "wms_layers": "x"})
            assert refused.status_code == 400, refused.status_code
            assert "http" in refused.json()["detail"]

            assert client.delete(f"/api/layers/{layer_id}").status_code == 200
            assert client.delete(f"/api/layers/{layer_id}").status_code == 404


def main() -> None:
    check_tile_bounds()
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "layers.sqlite3")
        try:
            registry = LayerRegistry(db)
            check_validation(registry)
            check_getmap(registry)
            check_registry_crud(registry)
            check_public_layers(registry)
        finally:
            db.close()
    check_tile_cache_integration()
    check_http_surface()
    print("Custom layer registry checks passed.")


if __name__ == "__main__":
    main()
