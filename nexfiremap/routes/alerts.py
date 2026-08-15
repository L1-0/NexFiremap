"""Official CAP public warnings as a map layer.

Reads are unrestricted (every authenticated role may see a warning - a viewer
who cannot be told about an evacuation order is a safety problem, not a
security feature). The manual refresh is administrator-only because it makes
the server reach out to a third-party government endpoint on demand, the same
reasoning as `routes/layers.py`'s probe.

`/api/alerts` deliberately returns a FeatureCollection *plus* a
`without_geometry` list. Geocode-only alerts - a German WARNCELLID or a US
SAME code with no polygon attached - are real warnings that simply cannot be
drawn, and dropping them silently would under-report the situation to the
person reading the map.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..alerts import AlertManager
from .common import _json, _parse_bbox, _require_administrator

router = APIRouter()


@router.get("/api/alerts")
async def api_alerts(
    request: Request, bbox: str | None = None, severity: str | None = None,
    include_expired: bool = False,
) -> Response:
    manager: AlertManager = request.app.state.alerts
    return _json(await asyncio.to_thread(
        manager.query, _parse_bbox(bbox), severity=severity, include_expired=include_expired,
    ))


@router.get("/api/alerts/status")
async def api_alerts_status(request: Request) -> Response:
    """Poll health, so an operator can tell "no warnings in this area" apart
    from "the feed has been unreachable for an hour" - a distinction that
    matters a great deal when the WAN is unreliable."""
    manager: AlertManager = request.app.state.alerts
    return _json(await asyncio.to_thread(manager.status))


@router.get("/api/alerts/{identifier:path}/original")
async def api_alert_original(request: Request, identifier: str) -> Response:
    """The alert's source XML exactly as published.

    ``:path`` because CAP identifiers are publisher-chosen and routinely
    contain slashes and URNs; matching a plain path segment would 404 on most
    real-world identifiers.
    """
    manager: AlertManager = request.app.state.alerts
    raw = await asyncio.to_thread(manager.original, identifier)
    if raw is None:
        raise HTTPException(404, "alert not found")
    return Response(content=raw, media_type="application/cap+xml",
                    headers={"Cache-Control": "no-store"})


@router.post("/api/alerts/refresh")
async def api_alerts_refresh(request: Request) -> Response:
    _require_administrator(request)
    manager: AlertManager = request.app.state.alerts
    if not manager.enabled:
        raise HTTPException(400, "no CAP feeds are configured (set NEXFIREMAP_CAP_FEEDS)")
    return _json(await manager.poll_now())
