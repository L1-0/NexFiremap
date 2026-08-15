"""External identity: OIDC authorization-code + PKCE, and LDAP simple bind.

The OIDC provider is stood up as a real local HTTP server rather than mocked
at the client, so the discovery, token-exchange and userinfo round trips are
genuinely exercised - including the PKCE verifier the provider checks.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import http.server
import json
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from nexfiremap.api import create_app
from nexfiremap.config import load_settings
from nexfiremap.federation import (
    FederationError, LdapProvider, OidcProvider, _bind_request, _bind_result_code,
    _escape_dn, _parse_role_map, _unverified_jwt_claims, issue_session,
)
from nexfiremap.security import ROLES, SecurityManager

ISSUED_CODE = "test-authorization-code"
_received: dict[str, object] = {}


def _jwt(claims: dict) -> str:
    """A structurally valid JWT with an unverifiable signature - which is
    exactly what this flow tolerates, and only because the token arrives
    straight from the token endpoint over TLS."""
    def segment(payload: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{segment({'alg': 'RS256'})}.{segment(claims)}.bm90LWEtcmVhbC1zaWduYXR1cmU"


class Provider(http.server.BaseHTTPRequestHandler):
    """A minimal OpenID Connect provider: discovery, token, userinfo."""

    port = 0

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        base = f"http://127.0.0.1:{Provider.port}"
        if self.path.startswith("/.well-known/openid-configuration"):
            self._send({"issuer": base, "authorization_endpoint": f"{base}/authorize",
                        "token_endpoint": f"{base}/token", "userinfo_endpoint": f"{base}/userinfo"})
        elif self.path.startswith("/userinfo"):
            self._send({"sub": "u-1", "preferred_username": "m.huber",
                        "email": "m.huber@ff.example.de", "groups": ["ff-fuehrung", "ff-mannschaft"]})
        else:
            self._send({"error": "not_found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode())
        _received["token_request"] = {key: value[0] for key, value in form.items()}
        if form.get("code", [""])[0] != ISSUED_CODE:
            self._send({"error": "invalid_grant"}, 400)
            return
        self._send({"access_token": "at-1", "token_type": "Bearer",
                    "id_token": _jwt({"sub": "u-1", "preferred_username": "m.huber",
                                      "groups": ["ff-fuehrung"]})})

    def log_message(self, *args) -> None:
        pass


def _settings(temp: Path, port: int, **overrides):
    defaults = {
        "db_path": temp / "oidc.sqlite3", "tile_cache_dir": temp / "tiles",
        "lan_mode": True, "admin_password": "a-long-enough-password",
        "oidc_issuer": f"http://127.0.0.1:{port}",
        "oidc_client_id": "nexfiremap", "oidc_client_secret": "s3cret",
        "oidc_redirect_uri": "http://127.0.0.1:8000/auth/oidc/callback",
        "oidc_role_claim": "groups",
        "oidc_role_map": "ff-fuehrung=administrator,ff-mannschaft=field_editor",
    }
    return dataclasses.replace(load_settings(), **{**defaults, **overrides})


def check_https_required() -> None:
    """TLS is the entire trust anchor for this flow, so a plain-http issuer
    must be refused - except on loopback, where a test double runs."""
    with tempfile.TemporaryDirectory() as temp:
        settings = _settings(Path(temp), 0, oidc_issuer="http://idp.example.de")
        provider = OidcProvider(settings)
        try:
            asyncio.run(provider.begin())
            raise AssertionError("a plain-http issuer was accepted")
        except FederationError as exc:
            assert "https" in str(exc)


def check_full_flow(port: int) -> None:
    with tempfile.TemporaryDirectory() as temp:
        settings = _settings(Path(temp), port)
        provider = OidcProvider(settings)
        assert provider.enabled

        url = asyncio.run(provider.begin("/incident"))
        query = parse_qs(urlparse(url).query)
        assert query["response_type"] == ["code"]
        assert query["client_id"] == ["nexfiremap"]
        assert query["code_challenge_method"] == ["S256"]
        state, challenge = query["state"][0], query["code_challenge"][0]
        assert state and challenge

        claims, next_url = asyncio.run(provider.complete(ISSUED_CODE, state))
        assert next_url == "/incident", "the post-login destination must survive the round trip"

        # PKCE: the verifier we sent must hash to the challenge we advertised,
        # which is what stops an intercepted code being redeemed by anyone else.
        verifier = _received["token_request"]["code_verifier"]
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert expected == challenge, "the PKCE verifier does not match the challenge"

        # Claims come from the token endpoint and userinfo, both fetched
        # directly - never from anything the browser handed us.
        assert claims["preferred_username"] == "m.huber"
        assert claims["email"] == "m.huber@ff.example.de"

        username, role = provider.identity(claims)
        assert username == "m.huber"
        assert role == "administrator", "the group mapping must resolve"

        # A state is single-use, so a replayed callback cannot mint a second
        # session.
        try:
            asyncio.run(provider.complete(ISSUED_CODE, state))
            raise AssertionError("an OIDC state was redeemed twice")
        except FederationError as exc:
            assert "state" in str(exc)

        # An unknown state is refused outright (CSRF on the callback).
        try:
            asyncio.run(provider.complete(ISSUED_CODE, "forged-state"))
            raise AssertionError("a forged state was accepted")
        except FederationError:
            pass


def check_role_mapping(port: int) -> None:
    """A misconfigured mapping must under-grant, never over-grant."""
    with tempfile.TemporaryDirectory() as temp:
        provider = OidcProvider(_settings(Path(temp), port))
        # No group at all -> the configured default, not something privileged.
        _, role = provider.identity({"preferred_username": "gast", "groups": []})
        assert role == "viewer"
        # An unrecognised group -> default, not the first mapping.
        _, role = provider.identity({"preferred_username": "gast", "groups": ["irgendwas"]})
        assert role == "viewer"
        # A claim naming one of our roles directly is honoured.
        _, role = provider.identity({"preferred_username": "x", "groups": ["safety"]})
        assert role == "safety"
        # No username claim at all is an error, not an anonymous session.
        try:
            provider.identity({"groups": ["ff-fuehrung"]})
            raise AssertionError("a claim set with no username produced an identity")
        except FederationError:
            pass

    # An entry naming an unknown role is dropped rather than granting it.
    mapping = _parse_role_map("a=administrator,b=wizard,c=viewer")
    assert mapping == {"a": "administrator", "c": "viewer"}
    assert all(role in ROLES for role in mapping.values())


def check_unverified_jwt_helper() -> None:
    claims = _unverified_jwt_claims(_jwt({"sub": "u-1", "groups": ["x"]}))
    assert claims["sub"] == "u-1"
    # Malformed tokens degrade to "no claims" rather than raising - userinfo
    # is the authoritative source anyway.
    assert _unverified_jwt_claims("not-a-jwt") == {}
    assert _unverified_jwt_claims("a.b.c") == {}


def check_ldap_encoding() -> None:
    """The BER encoder and the DN escaping, which are the two places a
    hand-rolled LDAP client goes wrong."""
    request = _bind_request("uid=m.huber,ou=people,dc=ff,dc=de", "hunter2")
    assert request[0] == 0x30, "a BindRequest is a BER SEQUENCE"
    assert b"uid=m.huber" in request and b"hunter2" in request
    assert bytes([0x02, 0x01, 0x03]) in request, "LDAP version 3 must be declared"

    # Long-form BER length for a payload over 127 bytes.
    long_request = _bind_request("uid=" + "x" * 300, "p")
    assert long_request[1] & 0x80, "a payload over 127 bytes needs a long-form length"

    # resultCode 0 is success; anything else is a rejection.
    assert _bind_result_code(bytes([0x30, 0x0c, 0x02, 0x01, 0x01, 0x61, 0x07, 0x0a, 0x01, 0x00,
                                    0x04, 0x00, 0x04, 0x00])) == 0
    assert _bind_result_code(bytes([0x30, 0x0c, 0x02, 0x01, 0x01, 0x61, 0x07, 0x0a, 0x01, 0x31,
                                    0x04, 0x00, 0x04, 0x00])) == 0x31
    assert _bind_result_code(b"garbage") == -1

    # DN injection: a username containing DN metacharacters must not be able
    # to restructure the DN the template builds.
    assert _escape_dn("a,ou=admins") == "a\\,ou\\=admins"
    assert _escape_dn('x"y') == 'x\\"y'


def check_ldap_empty_password_refused() -> None:
    """The classic silent LDAP bypass: most directories accept a simple bind
    with an empty password as an *anonymous* bind and return success, turning
    "wrong password" into "logged in". It must be refused before the socket
    is even opened."""
    with tempfile.TemporaryDirectory() as temp:
        settings = dataclasses.replace(
            load_settings(), db_path=Path(temp) / "l.sqlite3",
            ldap_host="ldap.invalid", ldap_user_dn_template="uid={username},dc=x")
        provider = LdapProvider(settings)
        assert provider.enabled
        for username, password in (("user", ""), ("", "pw"), ("user", None)):
            try:
                asyncio.run(provider.authenticate(username, password or ""))
                raise AssertionError(f"({username!r}, {password!r}) was accepted")
            except FederationError as exc:
                assert "non-empty password" in str(exc) or "username" in str(exc)


def check_http_surface(port: int) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        with TestClient(create_app(_settings(root, port)), follow_redirects=False) as client:
            # The login screen must be able to learn what is offered before
            # anyone has a session.
            providers = client.get("/api/auth/providers")
            assert providers.status_code == 200, providers.text
            assert providers.json() == {"local": True, "oidc": True,
                                        "oidc_login_url": "/auth/oidc/login", "ldap": False}

            # The redirect pair is reachable pre-session - that is the point.
            redirect = client.get("/auth/oidc/login")
            assert redirect.status_code == 302
            location = redirect.headers["location"]
            assert location.startswith(f"http://127.0.0.1:{port}/authorize")
            state = parse_qs(urlparse(location).query)["state"][0]

            callback = client.get(f"/auth/oidc/callback?code={ISSUED_CODE}&state={state}")
            assert callback.status_code == 302, callback.text
            assert "nexfiremap_session" in callback.cookies

            # The federated session is an ordinary session: it satisfies the
            # same middleware that a password login does.
            session = client.get("/api/auth/session")
            assert session.status_code == 200, session.text
            assert session.json()["username"] == "m.huber"
            assert session.json()["role"] == "administrator"

            # Open-redirect guard: an absolute destination must be ignored.
            hijack = client.get("/auth/oidc/login?next=https://evil.example.com/")
            assert hijack.status_code == 302
            forged_state = parse_qs(urlparse(hijack.headers["location"]).query)["state"][0]
            landed = client.get(f"/auth/oidc/callback?code={ISSUED_CODE}&state={forged_state}")
            assert landed.headers["location"] == "/", landed.headers["location"]

            # Local password login still works - the break-glass path is never
            # disabled by configuring a federated provider.
            local = client.post("/api/auth/login",
                                json={"username": "admin", "password": "a-long-enough-password"})
            assert local.status_code == 200, local.text
            assert local.json()["role"] == "administrator"


def check_disabled_by_default() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = dataclasses.replace(
            load_settings(), db_path=root / "off.sqlite3", tile_cache_dir=root / "tiles",
            lan_mode=False, oidc_issuer="", ldap_host="")
        with TestClient(create_app(settings)) as client:
            assert client.get("/api/auth/providers").json() == {
                "local": True, "oidc": False, "oidc_login_url": None, "ldap": False}
            assert client.get("/auth/oidc/login").status_code == 400


def check_issue_session_rejects_unknown_role() -> None:
    with tempfile.TemporaryDirectory() as temp:
        from nexfiremap.db import Database
        db = Database(Path(temp) / "s.sqlite3")
        try:
            security = SecurityManager(dataclasses.replace(load_settings(), lan_mode=False), db)
            try:
                issue_session(security, "x", "superuser")
                raise AssertionError("an unknown role minted a session")
            except FederationError:
                pass
            session = issue_session(security, "x", "viewer")
            assert security.session(session.token) is not None
        finally:
            db.close()


def main() -> None:
    server = http.server.HTTPServer(("127.0.0.1", 0), Provider)
    Provider.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        check_https_required()
        check_full_flow(Provider.port)
        check_role_mapping(Provider.port)
        check_unverified_jwt_helper()
        check_ldap_encoding()
        check_ldap_empty_password_refused()
        check_http_surface(Provider.port)
        check_disabled_by_default()
        check_issue_session_rejects_unknown_role()
    finally:
        server.shutdown()
    print("OIDC and LDAP federation checks passed.")


if __name__ == "__main__":
    main()
