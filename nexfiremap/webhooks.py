"""Inbound webhook registry for dispatch/CAD systems.

The stateful half of `ingest/webhook.py`: it owns the hook definitions, their
credentials, and the dead-letter record of payloads that could not be mapped.

Two design choices carry most of the weight:

*Hooks route through an existing position feed.* A hook does not write rows
itself - it resolves to a ``position_feed_sources`` row and calls
`TelemetryManager.ingest` with that feed's token, exactly like every other
transport. So a CAD integration inherits the batch limits, the replay-safety
hash, the derived speed/quality flags and the audit entry without
reimplementing any of them, and an operator can revoke it by rotating the
feed's token.

*A failed payload is kept, not dropped.* Vendor JSON is usually undocumented
and frequently changes, so the only practical way to build or repair a mapping
is to look at what actually arrived. `record_failure` stores the body (bounded)
and `describe` on the adapter turns it into a list of candidate paths. Dropping
it would turn every integration problem into "it doesn't work" with nothing to
go on.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .db import Database
from .ingest import IngestError
from .ingest import webhook as webhook_adapter
from .operations import NotFoundError

log = logging.getLogger("nexfiremap.webhooks")

#: How many failed payloads to keep per hook. Enough to diagnose a mapping
#: against a handful of real messages; small enough that a misconfigured CAD
#: hammering the endpoint cannot fill a field laptop's disk.
DEADLETTER_PER_HOOK = 50

#: Largest body accepted, mirroring the feed endpoint's own ceiling. Enforced
#: before parsing, since this endpoint is reachable with only a token.
MAX_BODY_BYTES = 512 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(row: Any) -> dict[str, Any]:
    """Storage row to API dict, with the token hash stripped unconditionally.

    Same rule as `telemetry._row`: a hash must never reach an API response even
    on a read-only route, because a leaked hash plus knowledge of the scheme is
    enough to impersonate the sender.
    """
    item = dict(row)
    item.pop("token_hash", None)
    item["mapping"] = json.loads(item.pop("mapping_json") or "{}")
    item["active"] = bool(item.get("active", 1))
    return item


class WebhookError(ValueError):
    """An invalid hook definition or an unusable payload."""


class WebhookManager:
    """CRUD for `webhooks`, plus the receive path and its dead-letter store."""

    def __init__(self, db: Database, telemetry: Any) -> None:
        self.db = db
        self.telemetry = telemetry

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, data: dict[str, Any], actor: str = "local operator") -> dict[str, Any]:
        """Register a hook and issue its token.

        The token is returned only here and from `rotate_token`; only its
        SHA-256 is stored, so it cannot be recovered later even with database
        access - only rotated. Identical contract to a position feed's ingest
        token, deliberately.
        """
        name = str(data.get("name") or "").strip()[:200]
        incident_id = str(data.get("incident_id") or "").strip()
        source_id = str(data.get("source_id") or "").strip()
        if not name or not incident_id or not source_id:
            raise WebhookError("name, incident_id and source_id are required")
        # The feed must exist and belong to this incident, or every delivery
        # would authenticate fine and then fail at ingest with a confusing error.
        if self.db.conn.execute(
            "SELECT 1 FROM position_feed_sources WHERE id=? AND incident_id=?",
            (source_id, incident_id),
        ).fetchone() is None:
            raise WebhookError("source_id does not name a position feed on this incident")
        try:
            mapping = webhook_adapter.validate_mapping(data.get("mapping"))
        except IngestError as exc:
            raise WebhookError(str(exc)) from exc

        token, hook_id, now = secrets.token_urlsafe(32), uuid4().hex, _now()
        with self.db._write_lock:
            try:
                self.db.conn.execute(
                    "INSERT INTO webhooks (id,name,incident_id,source_id,token_hash,mapping_json,"
                    "created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (hook_id, name, incident_id, source_id, self._token_hash(token),
                     json.dumps(mapping, sort_keys=True, separators=(",", ":")),
                     str(actor)[:200], now, now))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return {**self.get(hook_id), "token": token, "token_shown_once": True}

    def get(self, hook_id: str) -> dict[str, Any]:
        row = self.db.conn.execute("SELECT * FROM webhooks WHERE id=?", (str(hook_id),)).fetchone()
        if row is None:
            raise NotFoundError("webhook not found")
        return _row(row)

    def list(self, incident_id: str | None = None) -> list[dict[str, Any]]:
        if incident_id:
            rows = self.db.conn.execute(
                "SELECT * FROM webhooks WHERE incident_id=? ORDER BY name", (incident_id,)).fetchall()
        else:
            rows = self.db.conn.execute("SELECT * FROM webhooks ORDER BY name").fetchall()
        return [_row(row) for row in rows]

    def update(self, hook_id: str, data: dict[str, Any]) -> dict[str, Any]:
        current = self.get(hook_id)
        changes: dict[str, Any] = {}
        if "name" in data and data["name"] is not None:
            changes["name"] = str(data["name"]).strip()[:200]
        if "active" in data and data["active"] is not None:
            changes["active"] = int(bool(data["active"]))
        if data.get("mapping") is not None:
            try:
                changes["mapping_json"] = json.dumps(
                    webhook_adapter.validate_mapping(data["mapping"]), sort_keys=True, separators=(",", ":"))
            except IngestError as exc:
                raise WebhookError(str(exc)) from exc
        if not changes:
            return current
        changes["updated_at"] = _now()
        assignments = ",".join(f"{key}=?" for key in changes)
        with self.db._write_lock:
            try:
                self.db.conn.execute(f"UPDATE webhooks SET {assignments} WHERE id=?",
                                     (*changes.values(), hook_id))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return self.get(hook_id)

    def rotate_token(self, hook_id: str) -> dict[str, Any]:
        self.get(hook_id)
        token = secrets.token_urlsafe(32)
        with self.db._write_lock:
            try:
                self.db.conn.execute("UPDATE webhooks SET token_hash=?,updated_at=? WHERE id=?",
                                     (self._token_hash(token), _now(), hook_id))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return {**self.get(hook_id), "token": token, "token_shown_once": True}

    def delete(self, hook_id: str) -> bool:
        with self.db._write_lock:
            try:
                cursor = self.db.conn.execute("DELETE FROM webhooks WHERE id=?", (str(hook_id),))
                self.db.conn.execute("DELETE FROM webhook_deadletter WHERE hook_id=?", (str(hook_id),))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return cursor.rowcount > 0

    # ------------------------------------------------------------- receive

    def receive(self, hook_id: str, token: str, raw: bytes) -> dict[str, Any]:
        """Authenticate, map, and ingest one delivery.

        Token comparison is constant-time for the same reason
        `TelemetryManager.ingest`'s is: this is a secret compared on a path an
        untrusted caller can reach repeatedly, so a timing side channel would
        leak it a byte at a time.
        """
        row = self.db.conn.execute("SELECT * FROM webhooks WHERE id=?", (str(hook_id),)).fetchone()
        if row is None or not row["active"]:
            raise WebhookError("webhook is unavailable")
        if not token or not hmac.compare_digest(self._token_hash(token), str(row["token_hash"])):
            raise PermissionError("invalid webhook token")
        if len(raw) > MAX_BODY_BYTES:
            raise WebhookError(f"webhook body exceeds {MAX_BODY_BYTES} bytes")

        mapping = json.loads(row["mapping_json"] or "{}")
        try:
            reports = webhook_adapter.parse(raw, mapping=mapping, default_callsign=str(row["name"]))
        except IngestError as exc:
            # The payload authenticated but did not fit the mapping: keep it so
            # the mapping can be repaired against what the vendor really sends.
            self.record_failure(hook_id, str(exc), raw)
            raise WebhookError(f"payload did not match this webhook's mapping: {exc}") from exc

        # The hook's own token has been verified above, so this uses
        # telemetry's already-authenticated path. It cannot call `ingest`:
        # only the SHA-256 of the *feed's* token is stored, so there is no way
        # to produce a matching one - and keeping a second plaintext copy of it
        # here would be a worse answer than sharing the write path.
        result = self.telemetry.ingest_authenticated(str(row["source_id"]), reports)
        with self.db._write_lock:
            try:
                self.db.conn.execute(
                    "UPDATE webhooks SET last_received_at=?,accepted=accepted+?,updated_at=? WHERE id=?",
                    (_now(), int(result.get("accepted", 0)), _now(), hook_id))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return result

    def record_failure(self, hook_id: str, error: str, raw: bytes) -> None:
        """Store one unmappable payload, then trim to `DEADLETTER_PER_HOOK`."""
        with self.db._write_lock:
            try:
                self.db.conn.execute(
                    "INSERT INTO webhook_deadletter (id,hook_id,received_at,error,body) VALUES (?,?,?,?,?)",
                    (uuid4().hex, hook_id, _now(), str(error)[:500],
                     raw.decode("utf-8", "replace")[:MAX_BODY_BYTES]))
                self.db.conn.execute(
                    "DELETE FROM webhook_deadletter WHERE hook_id=? AND id NOT IN "
                    "(SELECT id FROM webhook_deadletter WHERE hook_id=? ORDER BY received_at DESC LIMIT ?)",
                    (hook_id, hook_id, DEADLETTER_PER_HOOK))
                self.db.conn.execute("UPDATE webhooks SET rejected=rejected+1 WHERE id=?", (hook_id,))
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        log.warning("Webhook %s rejected a payload: %s", hook_id, error)

    def failures(self, hook_id: str) -> list[dict[str, Any]]:
        """Recent unmappable payloads, each with the candidate paths it
        contains - which is what an operator builds the mapping from."""
        self.get(hook_id)
        results = []
        for row in self.db.conn.execute(
            "SELECT * FROM webhook_deadletter WHERE hook_id=? ORDER BY received_at DESC",
            (str(hook_id),),
        ).fetchall():
            item = dict(row)
            try:
                item["suggested_paths"] = webhook_adapter.describe(item["body"].encode())["paths"]
            except IngestError:
                item["suggested_paths"] = []
            results.append(item)
        return results


__all__ = ["DEADLETTER_PER_HOOK", "MAX_BODY_BYTES", "WebhookError", "WebhookManager"]
