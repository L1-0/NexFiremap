"""Fail-closed local sessions and role policy for explicit incident-LAN mode."""

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
    salt = salt or secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${value.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256": return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(raw_iterations))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


@dataclass
class Session:
    token: str
    csrf: str
    username: str
    role: str
    expires_at: float


class SecurityManager:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.enabled = settings.lan_mode
        self.session_seconds = settings.session_minutes * 60
        self.db = db
        self._sessions: dict[str, Session] = {}
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        if self.enabled:
            if len(settings.admin_password) < 12:
                raise RuntimeError("LAN mode requires NEXFIREMAP_ADMIN_PASSWORD with at least 12 characters")
            now = utcnow()
            row = db.conn.execute("SELECT id FROM local_accounts WHERE username='admin'").fetchone()
            if row is None:
                db.conn.execute("INSERT INTO local_accounts (id,username,role,password_hash,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                                (_id(), "admin", "administrator", password_hash(settings.admin_password), now, now))
            else:
                db.conn.execute("UPDATE local_accounts SET password_hash=?,role='administrator',active=1,updated_at=? WHERE username='admin'",
                                (password_hash(settings.admin_password), now))
            db.conn.commit()

    def login(self, username: str, password: str, client: str) -> Session | None:
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
        if role != "public":
            return True
        return path.startswith("/api/public/") or path in {"/api/auth/session", "/api/config"}

    @staticmethod
    def may_write(role: str, path: str) -> bool:
        if role == "administrator": return True
        if role == "field_editor": return any(token in path for token in ("/features", "/field-imports", "/resources", "/position-reports", "/assets"))
        if role == "plans": return any(token in path for token in ("/periods", "/scenarios", "/features", "/products", "/snapshots", "/drone-missions", "/mosaics", "/map-packs"))
        if role == "safety": return "/safety" in path or "/approve" in path
        return False
