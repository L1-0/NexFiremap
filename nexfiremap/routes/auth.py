"""Session login/logout and administrator-only account management.

These paths are special-cased in `SecurityMiddleware` (see api.py):
`/api/auth/login` is on the public allowlist (a client has no session yet
when it calls it), `/api/auth/logout` is exempt from the write-permission
table (every role must be able to log itself out), and
`/api/auth/accounts` gets an explicit administrator check ahead of the
generic role gate. Nothing here re-implements those checks - the
middleware has already run by the time any handler below is entered.
"""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..security import SecurityManager
from ..schemas import AccountCreateRequest, LoginRequest
from .common import _json

router = APIRouter()


@router.post("/api/auth/login")
async def api_login(request: Request, body: LoginRequest) -> Response:
    settings = request.app.state.settings
    security: SecurityManager = request.app.state.security
    if not security.enabled:
        return _json({"enabled": False, "username": "local operator", "role": "administrator", "csrf": ""})
    client = request.client.host if request.client else "unknown"
    session = await asyncio.to_thread(security.login, body.username, body.password, client)
    if session is None:
        raise HTTPException(401, "invalid credentials or login rate limit reached")
    response = _json({"enabled": True, "username": session.username, "role": session.role,
                      "csrf": session.csrf, "expires_at": session.expires_at})
    response.set_cookie("nexfiremap_session", session.token, httponly=True, samesite="strict",
                        secure=bool(settings.tls_cert_file), max_age=security.session_seconds, path="/")
    return response


@router.get("/api/auth/session")
async def api_auth_session(request: Request) -> Response:
    security: SecurityManager = request.app.state.security
    if not security.enabled:
        return _json({"enabled": False, "username": "local operator", "role": "administrator", "csrf": ""})
    session = security.session(request.cookies.get("nexfiremap_session"))
    if session is None: raise HTTPException(401, "authentication required")
    return _json({"enabled": True, "username": session.username, "role": session.role,
                  "csrf": session.csrf, "expires_at": session.expires_at})


@router.post("/api/auth/logout")
async def api_logout(request: Request) -> Response:
    security: SecurityManager = request.app.state.security
    security.logout(request.cookies.get("nexfiremap_session"))
    response = _json({"logged_out": True}); response.delete_cookie("nexfiremap_session", path="/")
    return response


@router.get("/api/auth/accounts")
async def api_accounts(request: Request) -> Response:
    security: SecurityManager = request.app.state.security
    return _json(await asyncio.to_thread(security.accounts))


@router.post("/api/auth/accounts")
async def api_account_create(request: Request, body: AccountCreateRequest) -> Response:
    security: SecurityManager = request.app.state.security
    try:
        account = await asyncio.to_thread(
            security.create_account, body.username, body.role, body.password
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "username already exists") from exc
    return _json(account, 201)
