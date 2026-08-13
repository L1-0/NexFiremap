"""Fail-closed local sessions and role policy for explicit incident-LAN mode.

By default (``lan_mode`` off) NexFiremap runs single-user on loopback and
none of this is engaged - `SecurityManager.enabled` is False and every
caller-facing check below is expected to be bypassed upstream. Once an
operator opts into LAN mode (multiple people on a shared incident network),
this module becomes the only thing standing between "trusted incident
network" and "arbitrary read/write access": sessions are opaque bearer
tokens kept in memory only (nothing persists across a restart, which is
deliberate - see `Session`/`_sessions`), and every route's required
permission is decided by simple prefix/substring matching against the
request path in `may_read`/`may_write` rather than a per-route table, so
adding a new role-restricted endpoint means updating the matching rule here
too.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .db import Database
from .operations import _id, utcnow


ROLES = {"viewer", "field_editor", "plans", "safety", "administrator", "public"}


def password_hash(password: str, *, iterations: int = 600_000, salt: bytes | None = None) -> str:
    """PBKDF2-HMAC-SHA256 with a random per-password salt, encoded as
    ``algorithm$iterations$salt$hash`` so a future iteration-count bump
    doesn't invalidate already-stored hashes - `verify_password` reads the
    iteration count back out of the stored string instead of assuming the
    current default. 600k iterations follows OWASP's current PBKDF2-SHA256
    minimum guidance."""
    salt = salt or secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${value.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Recomputes the hash with the *stored* algorithm/iterations/salt and
    compares in constant time (`hmac.compare_digest`) rather than `==`, so
    the comparison itself can't leak timing information about how much of
    the hash matched. Any malformed/foreign-format stored value is treated
    as "does not verify" rather than raising - a corrupt password_hash
    column should never turn into a 500 that looks like a different kind of
    failure to an attacker."""
    try:
        algorithm, raw_iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256": return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(raw_iterations))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


@dataclass
class Session:
    """An active login. ``token`` authenticates the session (sent back on
    every request); ``csrf`` is a second, separate value the frontend must
    echo on state-changing requests - keeping the two distinct means a
    token alone (which could leak via a logged URL, say) isn't sufficient
    to forge a write, and a bare CSRF value isn't sufficient to read either.
    Held only in `SecurityManager._sessions` (process memory), so every
    session ends on restart - acceptable, even desirable, for an
    incident-scoped deployment that isn't meant to persist logins across
    days/incidents."""

    token: str
    csrf: str
    username: str
    role: str
    expires_at: float


class SecurityManager:
    """Fail-closed by construction: with LAN mode off, `enabled` is False and
    this class does the minimum (still hashes/verifies passwords for the
    account endpoints) without gating anything; with it on, the constructor
    itself refuses to start on a weak/missing admin password rather than
    booting into a state an operator might mistake for "secured"."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.enabled = settings.lan_mode
        self.session_seconds = settings.session_minutes * 60
        self.db = db
        self._sessions: dict[str, Session] = {}
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        if self.enabled:
            # Enforced at startup, not just in create_account: a short/empty
            # admin password would otherwise let LAN mode boot "successfully"
            # while being trivially crackable - fail loudly here instead.
            if len(settings.admin_password) < 12:
                raise RuntimeError("LAN mode requires NEXFIREMAP_ADMIN_PASSWORD with at least 12 characters")
            now = utcnow()
            # Upsert the admin account from settings on every startup (not
            # just first run), so rotating NEXFIREMAP_ADMIN_PASSWORD in the
            # environment and restarting is the supported way to change it -
            # no separate "reset admin password" flow needed.
            row = db.conn.execute("SELECT id FROM local_accounts WHERE username='admin'").fetchone()
            if row is None:
                db.conn.execute("INSERT INTO local_accounts (id,username,role,password_hash,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                                (_id(), "admin", "administrator", password_hash(settings.admin_password), now, now))
            else:
                db.conn.execute("UPDATE local_accounts SET password_hash=?,role='administrator',active=1,updated_at=? WHERE username='admin'",
                                (password_hash(settings.admin_password), now))
            db.conn.commit()

    def login(self, username: str, password: str, client: str) -> Session | None:
        """Password login with per-client rate limiting (10 attempts/60s,
        keyed by whatever caller-supplied ``client`` identifier the route
        passes in - typically an IP) to blunt brute-forcing without needing
        an external throttling layer. Every failure path (unknown user,
        inactive account, wrong password) returns the same ``None`` rather
        than a distinguishing error, so a caller can't use the response to
        enumerate valid usernames."""
        now = time.time()
        with self._lock:
            attempts = [value for value in self._attempts.get(client, []) if now - value < 60]
            if len(attempts) >= 10: return None
            attempts.append(now); self._attempts[client] = attempts
        row = self.db.conn.execute("SELECT username,role,password_hash,active FROM local_accounts WHERE username=?", (username,)).fetchone()
        if row is None or not row["active"] or not verify_password(password, row["password_hash"]): return None
        session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(24), row["username"], row["role"], now + self.session_seconds)
        with self._lock: self._sessions[session.token] = session
        return session

    def session(self, token: str | None) -> Session | None:
        """Looks up and validates a session token, evicting it the moment
        it's found expired - so an expired token is cleaned up lazily on
        its next use rather than needing a separate sweep/GC pass."""
        if not token: return None
        with self._lock:
            session = self._sessions.get(token)
            if not session or session.expires_at <= time.time():
                self._sessions.pop(token, None); return None
            return session

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock: self._sessions.pop(token, None)

    def accounts(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.conn.execute(
            "SELECT id,username,role,active,created_at,updated_at FROM local_accounts ORDER BY username"
        ).fetchall()]

    def create_account(self, username: str, role: str, password: str) -> dict[str, Any]:
        """Creates a new local account. Relies on the ``local_accounts``
        table's own UNIQUE constraint on username to reject duplicates (via
        the exception path below) rather than a separate pre-check, so a
        race between two concurrent create-account calls for the same name
        can't both succeed."""
        username = username.strip()
        if not username or len(username) > 100:
            raise ValueError("username must contain 1 to 100 characters")
        if role not in ROLES:
            raise ValueError("unknown role")
        if len(password) < 12:
            raise ValueError("password must contain at least 12 characters")
        account_id, now = _id(), utcnow()
        with self.db._write_lock:
            try:
                self.db.conn.execute(
                    "INSERT INTO local_accounts (id,username,role,password_hash,active,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",
                    (account_id, username, role, password_hash(password), now, now),
                )
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return {"id": account_id, "username": username, "role": role, "active": 1,
                "created_at": now, "updated_at": now}

    @staticmethod
    def may_read(role: str, path: str) -> bool:
        """Every authenticated role can read everything - the roles only
        differ on what they may *write* (see `may_write`). Only "public"
        (the unauthenticated/no-session role) is read-restricted, to a
        small explicit allowlist of routes safe to expose without a login
        (public-facing summary endpoints, the session/config probes the
        frontend needs before a user has logged in at all)."""
        if role != "public":
            return True
        return path.startswith("/api/public/") or path in {"/api/auth/session", "/api/config"}

    @staticmethod
    def may_write(role: str, path: str) -> bool:
        """Coarse, path-substring-based write authorization: each
        operational role is scoped to the route families it needs
        (field_editor to on-the-ground data entry, plans to planning
        artifacts, safety to safety approvals/sign-off), and anything not
        explicitly matched is denied. "viewer" and "public" fall through to
        the final `return False` - neither role ever gets write access.
        Substring matching (not exact-prefix) is deliberate: it also covers
        nested sub-resource routes (e.g. a feature's comments) without
        listing each one here."""
        if role == "administrator": return True
        if role == "field_editor": return any(token in path for token in ("/features", "/field-imports", "/resources", "/position-reports", "/assets"))
        if role == "plans": return any(token in path for token in ("/periods", "/scenarios", "/features", "/products", "/snapshots", "/drone-missions", "/mosaics", "/map-packs"))
        if role == "safety": return "/safety" in path or "/approve" in path
        return False
