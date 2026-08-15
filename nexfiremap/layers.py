"""Operator-added map layers: WMS, WMTS and plain XYZ services.

The first thing a fire department's IT will ask is "can I add our Kreis-GIS
layer?" - the hydrants, the cadastre, the access routes, the hazard maps, the
county orthophoto. Those live on WMS/WMTS endpoints that every public
authority already runs, and none of them can be anticipated in `basemaps.py`.

So the design rule is: **a user-added layer is a row, not a code change.**
`BASEMAPS`/`OVERLAYS` stay as the built-in list; this module manages
everything an operator adds at runtime, and `TileCache` consults both through
one lookup. That single indirection is what makes the rest fall out for free -
because every downstream mechanism keys off the layer *id* and the
``layer/z/x/y`` path layout, an added WMS layer is proxied, disk-cached,
TTL-expired, LRU-evicted, pinned by a map-pack manifest and packaged for
offline use by exactly the same code that handles OpenStreetMap tiles. No
parallel path, nothing to keep in step.

Two kinds of source, one cache:

* ``xyz`` and RESTful ``wmts`` are already ``{z}/{x}/{y}`` templates, so they
  need no new fetch logic at all - `TileCache._fetch`'s existing substitution
  handles them unchanged.
* ``wms`` is the only genuinely new path: a GetMap request is a bbox query,
  not a tile request, so `getmap_url` converts the tile's own extent into one
  (see `geo.tile_bounds_3857`). The response is still a PNG that lands at the
  same cache path, which is why the offline machinery is none the wiser.

`probe` exists because asking an operator to hand-type a layer name out of a
GetCapabilities XML document is how you get typos that only surface as blank
tiles later. It fetches the capabilities document and returns the advertised
layers so the UI can offer a picker.
"""

from __future__ import annotations

import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode, urlparse, urlsplit, urlunsplit
from uuid import uuid4

import httpx

from . import netguard
from .config import Settings
from .db import Database
from .geo import tile_bounds_3857, tile_bounds_4326

log = logging.getLogger("nexfiremap.layers")

KINDS = {"xyz", "wmts", "wms"}

#: Formats we are willing to cache and serve. Restricted to raster images the
#: browser can draw directly - a misconfigured WMS happily returns
#: `application/vnd.ogc.se_xml` (its error document) with HTTP 200, and
#: caching that as if it were a tile would poison the layer until the TTL
#: expires. See `TileCache`'s content-type check on the WMS path.
IMAGE_FORMATS = {
    "image/png": "png",
    "image/png8": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

#: Layer ids become path segments under the tile cache directory, so they are
#: restricted to characters that cannot escape it or confuse a filesystem.
#: This is the same reasoning as `products.SAFE_NAME`.
_SAFE_ID = re.compile(r"[^a-z0-9_-]+")

#: Prefix on every generated id, so a custom layer is always distinguishable
#: from a built-in one at a glance - in the cache directory, in a map-pack
#: manifest, and in a bug report.
ID_PREFIX = "custom-"


class LayerError(ValueError):
    """An invalid layer definition, or an endpoint that could not be probed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _slug(value: str) -> str:
    """A filesystem- and URL-safe layer id derived from the display name."""
    base = _SAFE_ID.sub("-", str(value or "").strip().lower()).strip("-")
    return (base or uuid4().hex[:8])[:48]


def _require_http_url(value: str, field: str) -> str:
    """Reject anything that is not a plain http(s) URL.

    This is server-side request forgery protection, not tidiness: these URLs
    are fetched *by the server*, so allowing ``file://`` would turn "add a map
    layer" into "read a file off the host", and a bare host with no scheme
    would be resolved inconsistently. Administrator-only endpoints still get
    this check - the role gate limits who can ask, not what the server will
    then connect to.
    """
    url = _text(value, 2000)
    if not url:
        raise LayerError(f"{field} is required")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LayerError(f"{field} must be an http:// or https:// URL")
    return url


def _row(row: Any) -> dict[str, Any]:
    item = dict(row)
    for flag in ("transparent", "overlay", "active"):
        if flag in item:
            item[flag] = bool(item[flag])
    return item


class LayerRegistry:
    """CRUD for `custom_layers` plus the two things `TileCache` needs from it:
    `layer()` (metadata by id) and `getmap_url()` (a WMS tile request)."""

    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings
        # Built once per call rather than cached: the table is tiny (an
        # operator adds a handful of layers, not thousands) and a cache here
        # would need invalidating on every write from every thread, which is
        # more machinery than the lookup costs.
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------- reads

    def list(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        where = "" if include_inactive else " WHERE active=1"
        return [_row(row) for row in self.db.conn.execute(
            f"SELECT * FROM custom_layers{where} ORDER BY name"
        ).fetchall()]

    def get(self, layer_id: str) -> dict[str, Any] | None:
        row = self.db.conn.execute(
            "SELECT * FROM custom_layers WHERE id=?", (str(layer_id),)
        ).fetchone()
        return None if row is None else _row(row)

    def layer(self, layer_id: str) -> dict[str, Any] | None:
        """Metadata in the shape `TileCache` expects of a built-in layer.

        Deliberately mirrors `basemaps.py`'s dict keys (``id``/``url``/
        ``tile_ext``/``max_zoom``/...) so `TileCache` can treat a registry
        layer and a built-in one identically instead of branching on origin at
        every use site. Inactive layers return ``None`` - the same answer as an
        unknown id - so deactivating a layer immediately stops serving it
        without needing a separate check in the tile route.
        """
        record = self.get(layer_id)
        if record is None or not record["active"]:
            return None
        return {
            "id": record["id"],
            "name": record["name"],
            # `url` is only meaningful for the template kinds; the WMS branch
            # in TileCache._fetch builds its URL per tile from `getmap_url`
            # instead, and never reads this.
            "url": record["url_template"],
            "kind": record["kind"],
            "tile_ext": record["tile_ext"],
            "attribution": record["attribution"],
            "min_zoom": record["min_zoom"],
            "max_zoom": record["max_zoom"],
            "custom": True,
            "overlay": record["overlay"],
        }

    def public_layers(self) -> list[dict[str, Any]]:
        """Layer metadata for `/api/config`, URLs pointing at our own proxy.

        Same contract and same shape as `OfflineSourceManager.public_layers`,
        so `routes/meta.py` merges all three sources (built-in, offline
        MBTiles, custom) into one list the frontend's layer switcher builds
        itself from without knowing the difference.

        Attribution is HTML-escaped for the same reason it is there: the value
        is operator-supplied text that the frontend renders into the map's
        attribution control.
        """
        return [{
            "id": item["id"],
            "name": item["name"],
            "url": f"/tiles/{item['id']}/{{z}}/{{x}}/{{y}}.{item['tile_ext']}",
            "attribution": html.escape(item["attribution"]),
            "min_zoom": item["min_zoom"],
            "max_zoom": item["max_zoom"],
            "dark": False,
            "custom": True,
            "overlay": bool(item["overlay"]),
            "source_kind": item["kind"],
            "licence": item["licence"],
            "limitations": item["limitations"],
        } for item in self.list()]

    def tile_extensions(self) -> set[str]:
        """Every file extension custom layers can be cached with.

        `TileCache` needs this so TTL expiry, LRU eviction and the /api/status
        size figures actually walk custom-layer tiles. Without it a WMS layer
        serving JPEG would be invisible to all three - it would never expire,
        never be evicted, and never be counted against the cache budget.
        """
        return {str(row[0]) for row in self.db.conn.execute(
            "SELECT DISTINCT tile_ext FROM custom_layers"
        ).fetchall() if row[0]}

    # ------------------------------------------------------------ writes

    def _validate(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalise and check one layer definition.

        The kind-specific branch is the substance: a `wms` row with no
        `wms_layers` and an `xyz` row with no `{z}/{x}/{y}` template are both
        rows that look fine until the first tile request fails, so both are
        rejected at write time instead.
        """
        kind = _text(data.get("kind"), 10).lower()
        if kind not in KINDS:
            raise LayerError(f"layer kind must be one of {', '.join(sorted(KINDS))}")
        name = _text(data.get("name"), 200)
        if not name:
            raise LayerError("layer name is required")

        image_format = _text(data.get("image_format"), 60).lower() or "image/png"
        if image_format not in IMAGE_FORMATS:
            raise LayerError(f"image format must be one of {', '.join(sorted(IMAGE_FORMATS))}")

        record: dict[str, Any] = {
            "name": name,
            "kind": kind,
            "url_template": "",
            "endpoint": "",
            "wms_layers": "",
            "wms_styles": _text(data.get("wms_styles"), 300),
            "wms_version": _text(data.get("wms_version"), 10) or "1.3.0",
            "wms_crs": _text(data.get("wms_crs"), 40).upper() or "EPSG:3857",
            "image_format": image_format,
            "tile_ext": IMAGE_FORMATS[image_format],
            "transparent": int(bool(data.get("transparent", True))),
            "overlay": int(bool(data.get("overlay", False))),
            "attribution": _text(data.get("attribution"), 500),
            "licence": _text(data.get("licence"), 500),
            "limitations": _text(data.get("limitations"), 1000),
            "min_zoom": max(0, min(22, int(data.get("min_zoom") or 0))),
            "max_zoom": max(0, min(22, int(data.get("max_zoom") or 19))),
            "active": int(bool(data.get("active", True))),
        }
        if record["min_zoom"] > record["max_zoom"]:
            raise LayerError("min_zoom cannot exceed max_zoom")

        if kind == "wms":
            record["endpoint"] = _require_http_url(data.get("endpoint") or data.get("url"), "endpoint")
            record["wms_layers"] = _text(data.get("wms_layers") or data.get("layers"), 500)
            if not record["wms_layers"]:
                raise LayerError("a WMS layer requires wms_layers (the LAYERS parameter)")
            if record["wms_version"] not in {"1.1.1", "1.3.0"}:
                raise LayerError("WMS version must be 1.1.1 or 1.3.0")
            if record["wms_crs"] not in {"EPSG:3857", "EPSG:4326", "CRS:84"}:
                raise LayerError("WMS CRS must be EPSG:3857, EPSG:4326 or CRS:84")
        else:
            template = _require_http_url(data.get("url_template") or data.get("url"), "url_template")
            missing = [token for token in ("{z}", "{x}", "{y}") if token not in template]
            if missing:
                raise LayerError(f"tile URL template is missing {', '.join(missing)}")
            record["url_template"] = template
        return record

    def create(self, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        record = self._validate(data)
        # The id is derived from the name but must be unique *and* stable,
        # because it is baked into on-disk cache paths and into map-pack
        # manifests - renaming a layer later must not orphan its cached tiles,
        # so the id is assigned once here and never regenerated on update.
        base = f"{ID_PREFIX}{_slug(record['name'])}"
        layer_id, suffix = base, 2
        while self.get(layer_id) is not None:
            layer_id, suffix = f"{base}-{suffix}", suffix + 1

        now = _now()
        columns = ["id", *record, "added_by", "created_at", "updated_at"]
        values = [layer_id, *record.values(), _text(actor, 200) or "local operator", now, now]
        with self.db._write_lock:
            try:
                self.db.conn.execute(
                    f"INSERT INTO custom_layers ({','.join(columns)}) "
                    f"VALUES ({','.join('?' * len(values))})", values,
                )
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        log.info("Custom layer %r added (%s)", layer_id, record["kind"])
        return self.get(layer_id) or {}

    def update(self, layer_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Replace a layer's definition, keeping its id.

        Merging over the existing row (rather than requiring a full
        definition) means a caller can flip ``active`` without resending an
        endpoint and layer list - but the merged result still goes through
        `_validate`, so a partial update can never produce a row that would
        have been rejected as a create.
        """
        current = self.get(layer_id)
        if current is None:
            raise LayerError("custom layer not found")
        merged = {**current, **{k: v for k, v in data.items() if v is not None}}
        record = self._validate(merged)
        assignments = ",".join(f"{key}=?" for key in record)
        with self.db._write_lock:
            try:
                self.db.conn.execute(
                    f"UPDATE custom_layers SET {assignments},updated_at=? WHERE id=?",
                    (*record.values(), _now(), layer_id),
                )
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return self.get(layer_id) or {}

    def delete(self, layer_id: str) -> bool:
        """Remove a layer definition.

        Cached tiles on disk are deliberately left alone: they are already
        bounded by the cache's TTL and size budget, and a map pack built from
        this layer may still be pinning them for an incident that has not
        ended. Letting the normal prune reclaim them avoids deleting
        operational data as a side effect of tidying a layer list.
        """
        with self.db._write_lock:
            try:
                cursor = self.db.conn.execute("DELETE FROM custom_layers WHERE id=?", (str(layer_id),))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return cursor.rowcount > 0

    # -------------------------------------------------------- WMS GetMap

    def getmap_url(self, record: dict[str, Any], z: int, x: int, y: int, *, size: int = 256) -> str:
        """A WMS GetMap URL covering exactly one XYZ tile.

        The conversion is the whole trick: an XYZ client asks for tile
        ``z/x/y``, a WMS server answers bbox queries, so the tile's own
        projected extent becomes the BBOX and the response drops into the
        tile cache as if it had been a tile all along.

        Two WMS quirks are handled here rather than left to the caller:

        * **Axis order.** WMS 1.3.0 follows the CRS's declared axis order, so
          EPSG:4326 means *lat,lon* - the reverse of 1.1.1's ``SRS`` with
          lon,lat. Getting this wrong yields a server error or, worse, a
          plausible-looking image of the wrong place. CRS:84 exists precisely
          to mean "4326 but lon,lat", and is passed through unswapped.
        * **Parameter name.** 1.1.1 spells it ``SRS``, 1.3.0 spells it ``CRS``.

        Existing query parameters on the endpoint are preserved - some
        authorities embed an access token or a map definition in the URL - and
        our own parameters are appended after them.
        """
        crs = str(record.get("wms_crs") or "EPSG:3857").upper()
        version = str(record.get("wms_version") or "1.3.0")
        if crs == "EPSG:3857":
            minx, miny, maxx, maxy = tile_bounds_3857(z, x, y)
        else:
            minx, miny, maxx, maxy = tile_bounds_4326(z, x, y)
        if crs == "EPSG:4326" and version == "1.3.0":
            bbox = (miny, minx, maxy, maxx)
        else:
            bbox = (minx, miny, maxx, maxy)

        params = {
            "SERVICE": "WMS",
            "REQUEST": "GetMap",
            "VERSION": version,
            "LAYERS": str(record.get("wms_layers") or ""),
            "STYLES": str(record.get("wms_styles") or ""),
            "FORMAT": str(record.get("image_format") or "image/png"),
            "TRANSPARENT": "TRUE" if record.get("transparent", True) else "FALSE",
            "WIDTH": str(size),
            "HEIGHT": str(size),
            "BBOX": ",".join(f"{value:.9f}" for value in bbox),
            ("CRS" if version == "1.3.0" else "SRS"): crs,
        }
        parts = urlsplit(str(record.get("endpoint") or ""))
        query = "&".join(filter(None, [parts.query, urlencode(params, quote_via=quote)]))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))

    # ------------------------------------------------------------- probe

    async def probe(self, endpoint: str, *, kind: str = "wms", timeout: float = 20.0) -> dict[str, Any]:
        """Fetch and summarise a service's capabilities document.

        Exists so the UI can offer a picker instead of asking an operator to
        transcribe a layer name out of XML - a typo there produces blank tiles
        with no error, which is among the least debuggable failure modes a
        field user can hit.

        Only the fields needed to *build a layer row* are extracted (name,
        title, CRS list, formats); the rest of a capabilities document is
        large and irrelevant here.
        """
        url = _require_http_url(endpoint, "endpoint")
        if kind not in KINDS:
            raise LayerError(f"probe kind must be one of {', '.join(sorted(KINDS))}")
        service = "WMTS" if kind == "wmts" else "WMS"
        parts = urlsplit(url)
        params = {"SERVICE": service, "REQUEST": "GetCapabilities"}
        if service == "WMS":
            params["VERSION"] = "1.3.0"
        query = "&".join(filter(None, [parts.query, urlencode(params)]))
        capabilities_url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))

        headers = {"User-Agent": self.settings.tile_user_agent} if self.settings else {}
        try:
            # Trusted: an administrator supplied this endpoint, and reaching a
            # WMS on the incident LAN is the point of the feature. Loopback and
            # link-local (the cloud metadata address) are still refused, and the
            # hook re-checks each redirect hop.
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True, headers=headers,
                event_hooks={"request": [netguard.request_guard(trusted=True)]},
            ) as client:
                response = await client.get(capabilities_url)
        except httpx.HTTPError as exc:
            # A probe failure is an ordinary outcome on an incident LAN with no
            # WAN, so it is reported as a clear 4xx-able error rather than
            # allowed to surface as a 500.
            raise LayerError(f"could not reach {service} endpoint: {exc}") from exc
        if response.status_code != 200:
            raise LayerError(f"{service} endpoint returned HTTP {response.status_code}")

        text = response.text
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            # Same XXE guard as every other XML parse in this codebase - and
            # this one parses a document from an arbitrary operator-supplied
            # host, which is exactly the untrusted case it exists for.
            raise LayerError("capabilities document contains XML entity declarations")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise LayerError(f"capabilities document is not valid XML: {exc}") from exc

        return {"service": service, "endpoint": url,
                "layers": _wmts_layers(root) if service == "WMTS" else _wms_layers(root),
                "formats": sorted({node.text.strip() for node in root.iter()
                                   if node.tag.endswith("Format") and node.text
                                   and node.text.strip() in IMAGE_FORMATS})}


def _wms_layers(root: ET.Element) -> list[dict[str, Any]]:
    """Named layers from a WMS GetCapabilities document.

    ``{*}`` matches any namespace: WMS 1.1.1 documents are unnamespaced while
    1.3.0 uses ``http://www.opengis.net/wms``, and matching by local name only
    avoids enumerating both. Layers without a ``<Name>`` are containers used
    purely for grouping and cannot be requested, so they are skipped.
    """
    layers = []
    for node in root.iter():
        if not node.tag.endswith("Layer"):
            continue
        name = node.findtext("{*}Name") or node.findtext("Name")
        if not name:
            continue
        crs = sorted({(value.text or "").strip() for value in list(node)
                      if value.tag.endswith(("CRS", "SRS")) and value.text})
        layers.append({
            "name": name.strip(),
            "title": (node.findtext("{*}Title") or node.findtext("Title") or name).strip(),
            "abstract": (node.findtext("{*}Abstract") or node.findtext("Abstract") or "").strip()[:500],
            "crs": crs,
            "queryable": node.get("queryable") in {"1", "true"},
        })
    return layers


def _wmts_layers(root: ET.Element) -> list[dict[str, Any]]:
    """Named layers from a WMTS GetCapabilities document.

    WMTS puts the identifier in the OWS namespace rather than its own, hence
    the ``{*}Identifier`` lookup, and carries the RESTful tile URL template in
    a ``ResourceURL`` element - which, when present, is what makes a WMTS layer
    usable as a plain XYZ template with no per-tile work at all.
    """
    layers = []
    for node in root.iter():
        if not node.tag.endswith("Layer"):
            continue
        identifier = node.findtext("{*}Identifier")
        if not identifier:
            continue
        resource = None
        for child in node.iter():
            if child.tag.endswith("ResourceURL") and child.get("resourceType") == "tile":
                resource = child.get("template")
                break
        layers.append({
            "name": identifier.strip(),
            "title": (node.findtext("{*}Title") or identifier).strip(),
            "template": _wmts_template(resource) if resource else "",
            "formats": sorted({(f.text or "").strip() for f in node if f.tag.endswith("Format") and f.text}),
        })
    return layers


def _wmts_template(template: str) -> str:
    """Rewrite a WMTS RESTful template into the ``{z}/{x}/{y}`` form the tile
    cache already understands, so a WMTS layer needs no fetch logic of its own.

    ``{TileMatrix}``/``{TileRow}``/``{TileCol}`` are WMTS's spelling of
    zoom/y/x. This only works when the layer's tile matrix set is the standard
    web-mercator one (``GoogleMapsCompatible``); a layer on a bespoke matrix
    set will not line up, which is why the probe result also exposes the raw
    template for an operator to inspect.
    """
    return (template.replace("{TileMatrix}", "{z}")
                    .replace("{TileRow}", "{y}")
                    .replace("{TileCol}", "{x}")
                    .replace("{TileMatrixSet}", "GoogleMapsCompatible"))


__all__ = ["ID_PREFIX", "IMAGE_FORMATS", "KINDS", "LayerError", "LayerRegistry"]
