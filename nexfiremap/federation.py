"""External identity providers - OIDC and LDAP - minting local sessions.

The first thing a fire department's IT asks about a new server is how it
authenticates. `SecurityManager` is already the single choke point for that,
which makes this contained: **an external provider only ever mints the
existing local session.** Nothing downstream - the role gate, the CSRF check,
the audit actor - learns that a login came from anywhere else.

Two constraints shape everything here.

**The local admin password stays as break-glass.** An incident LAN's identity
provider may well be on the far side of the WAN link that just went down,
which is precisely when the map matters most. Local password login is never
disabled by configuring OIDC or LDAP; the fallback ordering is documented in
`docs/INCIDENT_LAN_RUNBOOK.md`.

**No local JWT signature verification.** NexFiremap is stdlib-only, and RS256
verification against a JWKS needs an RSA implementation this project will not
hand-roll (hand-rolled signature verification is far more dangerous than no
signature verification, because it looks like security). So the OIDC flow here
never trusts a token it was *handed*: it uses the authorization-code flow with
PKCE and reads claims from the token endpoint's own response, plus the
userinfo endpoint, both fetched directly from the provider over TLS.

OIDC Core section 3.1.3.7 explicitly permits skipping ID-token signature
validation when the token is obtained directly from the token endpoint over a
TLS-protected channel, which is exactly this flow. **The consequence, stated
plainly rather than buried: the provider's TLS certificate is the entire trust
anchor.** Certificate verification must therefore never be relaxed - there is
deliberately no "insecure"/"verify=False" option anywhere in this module, and
an `http://` issuer is refused.

LDAP is scoped to *authentication only*: a simple bind proves the password,
and the role comes from a configured DN->role map rather than a group search.
A group search would need a much larger BER implementation, and claiming to
read group membership while actually guessing it would be worse than not
offering it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import ssl
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from .config import Settings
from .security import ROLES, SecurityManager, Session

log = logging.getLogger("nexfiremap.federation")

#: How long an in-flight authorization request stays valid. Short, because it
#: only has to survive a redirect to the provider and back.
STATE_TTL_SECONDS = 600

#: Cap on concurrent in-flight logins, so an unauthenticated caller hitting
#: /auth/oidc/login repeatedly cannot grow this dict without bound.
MAX_PENDING_STATES = 200


class FederationError(ValueError):
    """A misconfigured provider, or a failed external authentication."""


def _require_https(url: str, field: str) -> str:
    """Reject a non-TLS provider URL.

    Load-bearing rather than pedantic: with no local signature verification,
    TLS *is* the authentication of the provider's responses (see the module
    docstring). Over plain http the whole flow would be forgeable by anyone on
    the path. Loopback is allowed so a test double can run without a
    certificate.
    """
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return url.strip()
    if parsed.scheme != "https" or not parsed.netloc:
        raise FederationError(f"{field} must be an https:// URL (TLS is the trust anchor for this flow)")
    return url.strip()


class OidcProvider:
    """Authorization-code + PKCE login against an OpenID Connect provider."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # state -> (code_verifier, created_at, next_url). In memory only, like
        # sessions themselves: a restart invalidates in-flight logins, which
        # is harmless (the user simply clicks login again) and avoids
        # persisting anything that behaves like a credential.
        self._pending: dict[str, tuple[str, float, str]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.oidc_issuer and self.settings.oidc_client_id)

    def _discovery_url(self) -> str:
        issuer = _require_https(self.settings.oidc_issuer, "OIDC issuer").rstrip("/")
        return f"{issuer}/.well-known/openid-configuration"

    async def _discover(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """Fetch the provider's endpoint metadata.

        Discovery rather than three separate settings: every provider publishes
        this document, and an operator mistyping one of three URLs is a far
        more likely failure than the document being unavailable.
        """
        try:
            response = await client.get(self._discovery_url())
        except httpx.HTTPError as exc:
            # An unreachable identity provider is an *expected* state on an
            # incident LAN whose WAN link has failed - which is precisely when
            # the map matters most. It has to surface as a clear 4xx telling
            # the operator to use local login, never as a 500: a server error
            # on the sign-in page reads as "the tool is broken" rather than
            # "use the break-glass path", and that misreading costs time
            # during an incident.
            raise FederationError(
                f"the identity provider is unreachable ({exc}); sign in with a local account instead"
            ) from exc
        if response.status_code != 200:
            raise FederationError(f"OIDC discovery returned HTTP {response.status_code}")
        document = response.json()
        for required in ("authorization_endpoint", "token_endpoint"):
            if not document.get(required):
                raise FederationError(f"OIDC discovery document has no {required}")
        return document

    def _prune(self) -> None:
        cutoff = time.time() - STATE_TTL_SECONDS
        for state in [key for key, value in self._pending.items() if value[1] < cutoff]:
            self._pending.pop(state, None)

    async def begin(self, next_url: str = "/") -> str:
        """Start a login: returns the provider URL to redirect the browser to.

        ``state`` defends against CSRF on the callback (an attacker cannot
        make a victim's browser complete a login the victim did not start), and
        PKCE (``code_challenge``) defends against an intercepted authorization
        code being redeemed by anyone but us. Both matter here: without local
        token verification, these are the flow's integrity guarantees.
        """
        if not self.enabled:
            raise FederationError("OIDC is not configured")
        self._prune()
        if len(self._pending) >= MAX_PENDING_STATES:
            raise FederationError("too many OIDC logins in flight; try again shortly")

        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(32)
        self._pending[state] = (verifier, time.time(), next_url)

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            document = await self._discover(client)
        query = urlencode({
            "response_type": "code",
            "client_id": self.settings.oidc_client_id,
            "redirect_uri": self.settings.oidc_redirect_uri,
            "scope": self.settings.oidc_scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        separator = "&" if "?" in document["authorization_endpoint"] else "?"
        return f"{document['authorization_endpoint']}{separator}{query}"

    async def complete(self, code: str, state: str) -> tuple[dict[str, Any], str]:
        """Exchange the code for claims. Returns ``(claims, next_url)``.

        The single-use ``pop`` is deliberate: a state may be redeemed exactly
        once, so a replayed callback cannot mint a second session.
        """
        if not self.enabled:
            raise FederationError("OIDC is not configured")
        self._prune()
        pending = self._pending.pop(str(state or ""), None)
        if pending is None:
            raise FederationError("unknown or expired OIDC login state")
        verifier, _created, next_url = pending
        if not code:
            raise FederationError("the identity provider returned no authorization code")

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            document = await self._discover(client)
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.oidc_redirect_uri,
                "client_id": self.settings.oidc_client_id,
                "code_verifier": verifier,
            }
            if self.settings.oidc_client_secret:
                data["client_secret"] = self.settings.oidc_client_secret
            try:
                token_response = await client.post(
                    _require_https(document["token_endpoint"], "OIDC token endpoint"), data=data)
            except httpx.HTTPError as exc:
                # Same reasoning as `_discover`: a provider that vanished
                # mid-flow is a 4xx pointing at local login, not a 500.
                raise FederationError(f"the identity provider is unreachable ({exc})") from exc
            if token_response.status_code != 200:
                raise FederationError(f"OIDC token exchange returned HTTP {token_response.status_code}")
            tokens = token_response.json()

            claims: dict[str, Any] = {}
            # The ID token's *payload* is read without verifying its signature,
            # which is safe only because this response came straight from the
            # token endpoint over TLS (see the module docstring). It is used
            # for convenience; userinfo below is the authoritative source.
            if tokens.get("id_token"):
                claims.update(_unverified_jwt_claims(tokens["id_token"]))

            userinfo_endpoint = document.get("userinfo_endpoint")
            if userinfo_endpoint and tokens.get("access_token"):
                try:
                    userinfo = await client.get(
                        _require_https(userinfo_endpoint, "OIDC userinfo endpoint"),
                        headers={"Authorization": f"Bearer {tokens['access_token']}"})
                except httpx.HTTPError as exc:
                    # Unlike the two calls above this one is not fatal: the
                    # token exchange already succeeded, so the ID token's
                    # claims are in hand and the login can complete on those
                    # alone. Userinfo is the richer source, not the only one.
                    log.warning("OIDC userinfo endpoint unreachable (%s); using ID token claims", exc)
                else:
                    if userinfo.status_code == 200:
                        claims.update(userinfo.json())

        if not claims:
            raise FederationError("the identity provider returned no usable claims")
        return claims, next_url

    def identity(self, claims: dict[str, Any]) -> tuple[str, str]:
        """Map provider claims onto a username and one of our roles.

        The role claim is configurable because providers disagree entirely on
        where group membership lives (``groups``, ``roles``, ``realm_access``,
        a custom namespace). An unmatched or absent role falls back to the
        configured default rather than to something privileged - a
        misconfigured mapping must under-grant, never over-grant.
        """
        username = str(claims.get(self.settings.oidc_username_claim)
                       or claims.get("preferred_username")
                       or claims.get("email")
                       or claims.get("sub") or "").strip()[:100]
        if not username:
            raise FederationError("the identity provider returned no username claim")

        raw = claims.get(self.settings.oidc_role_claim)
        candidates = raw if isinstance(raw, list) else [raw] if raw else []
        mapping = _parse_role_map(self.settings.oidc_role_map)
        for candidate in candidates:
            mapped = mapping.get(str(candidate).strip().lower())
            if mapped in ROLES:
                return username, mapped
            if str(candidate).strip().lower() in ROLES:
                return username, str(candidate).strip().lower()
        default = self.settings.oidc_default_role
        return username, default if default in ROLES else "viewer"


def _unverified_jwt_claims(token: str) -> dict[str, Any]:
    """Decode a JWT payload **without** verifying its signature.

    Named to make that unmistakable at every call site. Only ever applied to a
    token received directly from the token endpoint over TLS - see the module
    docstring for why that is permitted and what it costs.
    """
    import json

    parts = str(token).split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, UnicodeDecodeError):
        return {}


def _parse_role_map(raw: str) -> dict[str, str]:
    """Parse ``"fire-admins=administrator,fire-crew=field_editor"``.

    An entry naming an unknown role is dropped with a warning rather than
    failing startup, matching every other config helper - but the effect of a
    typo is that nobody gets that role, never that everybody does.
    """
    mapping: dict[str, str] = {}
    for item in (part.strip() for part in str(raw or "").split(",") if part.strip()):
        group, _, role = item.partition("=")
        role = role.strip().lower()
        if not group.strip() or role not in ROLES:
            log.warning("Ignoring role mapping %r (role must be one of %s)", item, ", ".join(sorted(ROLES)))
            continue
        mapping[group.strip().lower()] = role
    return mapping


# ---------------------------------------------------------------------- LDAP

#: BER/ASN.1 tags used by the LDAP simple-bind exchange. Only what a bind
#: needs - this is not a general LDAP client.
_BER_SEQUENCE = 0x30
_BER_INTEGER = 0x02
_BER_OCTET_STRING = 0x04
_BER_ENUMERATED = 0x0A
_LDAP_BIND_REQUEST = 0x60
_LDAP_BIND_RESPONSE = 0x61


def _ber_length(value: int) -> bytes:
    """Encode a BER length: short form under 128, long form above."""
    if value < 0x80:
        return bytes([value])
    encoded = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def _ber(tag: int, payload: bytes) -> bytes:
    return bytes([tag]) + _ber_length(len(payload)) + payload


def _bind_request(dn: str, password: str, message_id: int = 1) -> bytes:
    """A BindRequest for LDAPv3 simple authentication.

    Hand-rolled because a simple bind is a fixed six-field structure, and this
    module only ever needs to ask "are these credentials valid" - not to
    search, compare, or modify. `ldap3` would be a large dependency for one
    message pair.
    """
    body = (_ber(_BER_INTEGER, bytes([3]))                       # LDAP version 3
            + _ber(_BER_OCTET_STRING, dn.encode("utf-8"))
            + _ber(0x80, password.encode("utf-8")))              # [0] simple
    return _ber(_BER_SEQUENCE,
                _ber(_BER_INTEGER, bytes([message_id])) + _ber(_LDAP_BIND_REQUEST, body))


def _bind_result_code(response: bytes) -> int:
    """Extract a BindResponse's resultCode; 0 means success.

    Walks to the response tag rather than assuming fixed offsets, because the
    preceding messageId is variable-length.
    """
    index = 0
    while index < len(response):
        if response[index] == _LDAP_BIND_RESPONSE:
            # BindResponse: tag, length, then ENUMERATED resultCode first.
            cursor = index + 1
            length_byte = response[cursor]
            cursor += 1 + (length_byte & 0x7F if length_byte & 0x80 else 0)
            if cursor + 2 < len(response) and response[cursor] == _BER_ENUMERATED:
                return int(response[cursor + 2])
            return -1
        index += 1
    return -1


class LdapProvider:
    """Password verification by LDAP simple bind. Authentication only."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.ldap_host and self.settings.ldap_user_dn_template)

    async def authenticate(self, username: str, password: str) -> tuple[str, str]:
        """Bind as the user; return ``(username, role)`` on success.

        An empty password is refused before the socket is opened: LDAP treats
        a simple bind with an empty password as an *anonymous* bind, which most
        directories accept - so passing one through would turn "wrong password"
        into "logged in". This is a classic and completely silent LDAP
        authentication bypass.
        """
        if not self.enabled:
            raise FederationError("LDAP is not configured")
        if not username or not password:
            raise FederationError("LDAP requires a username and a non-empty password")
        # The template keeps the DN structure in configuration where it
        # belongs; escaping the username guards against DN injection.
        dn = self.settings.ldap_user_dn_template.replace("{username}", _escape_dn(username))

        context = ssl.create_default_context() if self.settings.ldap_use_tls else None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.settings.ldap_host, self.settings.ldap_port, ssl=context),
                timeout=15)
        except (OSError, asyncio.TimeoutError) as exc:
            # An unreachable directory is an expected state on an incident LAN
            # with the WAN down - which is exactly why the local admin password
            # remains available as break-glass.
            raise FederationError(f"LDAP server is unreachable: {exc}") from exc
        try:
            writer.write(_bind_request(dn, password))
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=15)
        except (OSError, asyncio.TimeoutError) as exc:
            raise FederationError(f"LDAP bind failed: {exc}") from exc
        finally:
            writer.close()

        code = _bind_result_code(response)
        if code != 0:
            raise FederationError("LDAP credentials were rejected")

        mapping = _parse_role_map(self.settings.ldap_role_map)
        role = mapping.get(username.strip().lower())
        if role not in ROLES:
            default = self.settings.ldap_default_role
            role = default if default in ROLES else "viewer"
        return username.strip()[:100], role


def _escape_dn(value: str) -> str:
    """Escape the RFC 4514 special characters in a DN component.

    Without this, a username containing ``,`` or ``)`` could restructure the
    DN the template builds - the LDAP equivalent of SQL injection.
    """
    out = []
    for character in str(value)[:100]:
        if character in ',+"\\<>;=' or character in "\x00":
            out.append("\\" + character)
        else:
            out.append(character)
    return "".join(out).strip()


def issue_session(security: SecurityManager, username: str, role: str) -> Session:
    """Mint a local session for an externally-authenticated user.

    The whole federation design in one function: whatever proved the identity,
    what comes out is an ordinary `Session`, so the middleware, the role gate
    and the CSRF check all behave exactly as they do for a password login.
    """
    if role not in ROLES:
        raise FederationError(f"unknown role {role!r}")
    session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(24),
                      username, role, time.time() + security.session_seconds)
    with security._lock:
        security._sessions[session.token] = session
    log.info("Federated login issued a session for %r as %r", username, role)
    return session


__all__ = ["MAX_PENDING_STATES", "STATE_TTL_SECONDS", "FederationError",
           "LdapProvider", "OidcProvider", "issue_session"]
