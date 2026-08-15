"""Inbound webhooks from dispatch/CAD systems.

Two very different audiences share this module, and the split matters:

*Administration* (`/api/webhooks...`) is administrator-only, because creating
a hook mints a credential and defines what an outside system may write - an
account-management action, the same reasoning as position-feed administration.

*Delivery* (`POST /api/ingest/webhook/{hook_id}`) is called by the CAD system
itself, which has no browser session. It authenticates with the hook's own
`X-Webhook-Token`, so `SecurityMiddleware` carves this exact path out of the
session gate - the same arrangement as the position-feed ingest endpoint, and
kept just as narrow.

`/failures` exists because a CAD vendor's JSON is usually undocumented: it
returns the payloads that could not be mapped *together with the candidate
paths found in them*, which is what an operator actually builds the mapping
from.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..schemas import WebhookCreateRequest, WebhookUpdateRequest
from ..webhooks import MAX_BODY_BYTES, WebhookManager
from .common import _json, _model_payload, _operator, _require_administrator

router = APIRouter()


@router.get("/api/webhooks")
async def api_webhooks(request: Request, incident_id: str | None = None) -> Response:
    _require_administrator(request)
    manager: WebhookManager = request.app.state.webhooks
    return _json(await asyncio.to_thread(manager.list, incident_id))


@router.post("/api/webhooks")
async def api_webhook_create(request: Request, body: WebhookCreateRequest) -> Response:
    _require_administrator(request)
    manager: WebhookManager = request.app.state.webhooks
    return _json(await asyncio.to_thread(manager.create, _model_payload(body), _operator(request)), 201)


@router.patch("/api/webhooks/{hook_id}")
async def api_webhook_update(request: Request, hook_id: str, body: WebhookUpdateRequest) -> Response:
    _require_administrator(request)
    manager: WebhookManager = request.app.state.webhooks
    return _json(await asyncio.to_thread(manager.update, hook_id, _model_payload(body)))


@router.post("/api/webhooks/{hook_id}/rotate-token")
async def api_webhook_rotate(request: Request, hook_id: str) -> Response:
    _require_administrator(request)
    manager: WebhookManager = request.app.state.webhooks
    return _json(await asyncio.to_thread(manager.rotate_token, hook_id))


@router.get("/api/webhooks/{hook_id}/failures")
async def api_webhook_failures(request: Request, hook_id: str) -> Response:
    """Payloads that arrived but could not be mapped, with the paths they
    contain - the raw material for repairing a mapping against what a vendor
    actually sends rather than what their documentation claims."""
    _require_administrator(request)
    manager: WebhookManager = request.app.state.webhooks
    return _json(await asyncio.to_thread(manager.failures, hook_id))


@router.delete("/api/webhooks/{hook_id}")
async def api_webhook_delete(request: Request, hook_id: str) -> Response:
    _require_administrator(request)
    manager: WebhookManager = request.app.state.webhooks
    if not await asyncio.to_thread(manager.delete, hook_id):
        raise HTTPException(404, "webhook not found")
    return _json({"deleted": True, "id": hook_id})


@router.post("/api/ingest/webhook/{hook_id}")
async def api_webhook_receive(request: Request, hook_id: str) -> Response:
    """One delivery from a CAD/dispatch system.

    Body size is checked before any parsing, as on the feed endpoint: this
    path is reachable with only a token, so nothing unbounded may be buffered
    or parsed on the strength of that alone.
    """
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_BODY_BYTES:
                raise HTTPException(413, "webhook body is too large")
        except ValueError as exc:
            raise HTTPException(400, "invalid Content-Length") from exc
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "webhook body is too large")

    manager: WebhookManager = request.app.state.webhooks
    # Header first, query parameter as the fallback: some CAD products can
    # only be configured with a URL, exactly like the OsmAnd trackers.
    token = request.headers.get("X-Webhook-Token", "") or request.query_params.get("token", "")
    try:
        result = await asyncio.to_thread(manager.receive, hook_id, token, raw)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    return _json(result, 202)
