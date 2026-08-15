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
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from ..federation import FederationError, LdapProvider, OidcProvider, issue_session
from ..security import SecurityManager
from ..schemas import AccountCreateRequest, LoginRequest
from .common import _json

log = logging.getLogger("nexfiremap.api")

router = APIRouter()


def _session_response(request: Request, session) -> Response:
    """One place that turns a Session into the cookie + body every login path
    returns, so a password login and a federated one are indistinguishable to
    the frontend and to the middleware."""
    settings = request.app.state.settings
    security: SecurityManager = request.app.state.security
    response = _json({"enabled": True, "username": session.username, "role": session.role,
                      "csrf": session.csrf, "expires_at": session.expires_at})
    response.set_cookie("nexfiremap_session", session.token, httponly=True, samesite="strict",
                        secure=bool(settings.tls_cert_file), max_age=security.session_seconds, path="/")
    return response


@router.post("/api/auth/login")
async def api_login(request: Request, body: LoginRequest) -> Response:
    """Local password login, and - when LDAP is configured - a directory bind.

    Local accounts are tried first and are never disabled by configuring a
    directory: an incident LAN's LDAP server may be unreachable exactly when
    the map matters most, so the local admin password stays as break-glass.
    The directory is only consulted after the local check fails, so an outage
    degrades to "local accounts still work" rather than "nobody can log in".
    """
    security: SecurityManager = request.app.state.security
    if not security.enabled:
        return _json({"enabled": False, "username": "local operator", "role": "administrator", "csrf": ""})
    client = request.client.host if request.client else "unknown"
    session = await asyncio.to_thread(security.login, body.username, body.password, client)
    if session is not None:
        return _session_response(request, session)

    ldap: LdapProvider = request.app.state.ldap
    if ldap.enabled:
        try:
            username, role = await ldap.authenticate(body.username, body.password)
        except FederationError as exc:
            log.info("LDAP login for %r failed: %s", body.username, exc)
        else:
            return _session_response(request, issue_session(security, username, role))
    raise HTTPException(401, "invalid credentials or login rate limit reached")


@router.get("/auth/oidc/login")
async def api_oidc_login(request: Request, next: str = Query("/")) -> Response:
    """Redirect to the identity provider to start an authorization-code flow."""
    provider: OidcProvider = request.app.state.oidc
    if not provider.enabled:
        raise HTTPException(400, "OIDC is not configured")
    # Only a local path may be returned to, so a crafted link cannot use the
    # provider callback to bounce a user to an attacker's site after a real login.
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    return RedirectResponse(await provider.begin(target), status_code=302)


@router.get("/auth/oidc/callback")
async def api_oidc_callback(request: Request, code: str = Query(""), state: str = Query("")) -> Response:
    """Exchange the authorization code and mint an ordinary local session."""
    provider: OidcProvider = request.app.state.oidc
    security: SecurityManager = request.app.state.security
    if not provider.enabled:
        raise HTTPException(400, "OIDC is not configured")
    claims, next_url = await provider.complete(code, state)
    username, role = provider.identity(claims)
    session = issue_session(security, username, role)
    # A redirect rather than JSON: the browser arrives here from the provider,
    # so the user has to land back in the app, not on an API response.
    # samesite="lax" (not "strict") because this navigation is cross-site by
    # construction - a strict cookie would not be sent and the user would
    # arrive logged out.
    response = RedirectResponse(next_url, status_code=302)
    settings = request.app.state.settings
    response.set_cookie("nexfiremap_session", session.token, httponly=True, samesite="lax",
                        secure=bool(settings.tls_cert_file), max_age=security.session_seconds, path="/")
    return response


@router.get("/api/auth/providers")
async def api_auth_providers(request: Request) -> Response:
    """Which login methods this install offers, for the login screen.

    Local password login is always listed: it is the documented break-glass
    path and is never disabled by configuring a federated provider.
    """
    return _json({
        "local": True,
        "oidc": request.app.state.oidc.enabled,
        "oidc_login_url": "/auth/oidc/login" if request.app.state.oidc.enabled else None,
        "ldap": request.app.state.ldap.enabled,
    })


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
