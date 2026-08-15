"""Operator-editable settings, stored in the database.

Everything configurable in NexFiremap has until now come from environment
variables or `.env` (see `config.load_settings`). That is right for a
deployment a sysadmin owns, and wrong for the things an *operator* legitimately
needs to change during an incident - a FIRMS key that turns out to be missing,
the coordinate format the whole room reads, the tactical symbol profile.
Asking someone to edit a dotfile and restart the server is not a workable
answer at 03:00.

So this is a small override layer: a `app_settings` table read once at startup
and folded over the environment-derived `Settings`, plus an admin-only API to
edit it. The precedence is deliberate and stated in `apply_overrides`:

    code defaults  <  environment / .env  <  database overrides

Database last, because it is the layer a human edited most recently and most
deliberately. An operator who sets something in the UI and finds an old
environment value still winning would have no way to discover why.

**Secrets never come back out.** `public_view` returns whether a key is set and
a four-character tail to recognise it by, never the value. A settings page that
renders a working API key into the DOM is one screenshot or one shoulder away
from leaking it, and there is no legitimate reason for the browser to hold it -
the server is what uses it.

Two honesty properties this module is careful about:

* **Some changes need a restart and this says so.** `Settings` is frozen and is
  read at construction time by long-lived managers (`CacheManager` builds a
  `FirmsClient` with the map key; `AlertManager` builds an HTTP client). Storing
  a new key does not reach into those. `RESTART_REQUIRED` names the fields where
  that is true, so the UI can tell the operator rather than leaving them
  wondering why nothing happened.
* **Unknown keys are refused**, not stored. A typo that silently persists into a
  table nobody reads again is worse than an error.
"""

from __future__ import annotations

import json
import logging
from dataclasses import fields, replace
from datetime import datetime, timezone
from typing import Any

from .config import Settings

log = logging.getLogger("nexfiremap.settings")

#: Fields an operator may set from the UI, with the type each is coerced to.
#: A closed allowlist rather than "any Settings field": the dataclass also holds
#: filesystem paths, port numbers and the LAN-mode security gate, none of which
#: should be reachable from a web form.
EDITABLE: dict[str, type] = {
    # Credentials
    "map_key": str,
    "eumetsat_consumer_key": str,
    "eumetsat_consumer_secret": str,
    # Presentation
    "symbology_profile": str,
    # Data sources and behaviour
    "cap_feeds": list,
    "cache_days": int,
    "refresh_interval_minutes": int,
    "observation_stale_hours": int,
    "position_stale_seconds": int,
    "tile_contact": str,
}

#: Fields whose value is a credential. Never returned to a client; only their
#: presence and a short tail are.
SECRETS = {"map_key", "eumetsat_consumer_secret", "eumetsat_consumer_key"}

#: Fields that only take effect after a restart, because a long-lived manager
#: read them at construction time. Surfaced in the API so the UI can say so.
RESTART_REQUIRED = {"map_key", "eumetsat_consumer_key", "eumetsat_consumer_secret",
                    "cap_feeds", "cache_days", "refresh_interval_minutes"}

#: Purely client-side preferences with no `Settings` field behind them. Kept
#: here anyway so a room full of operators shares one default instead of each
#: browser's localStorage disagreeing - which is the actual complaint that
#: motivated a *global* coordinate framework.
CLIENT_PREFERENCES: dict[str, type] = {
    "coordinate_system": str,
    "custom_proj4": str,
}


class SettingsError(ValueError):
    """An unknown setting, or a value that cannot be coerced."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _coerce(name: str, value: Any, kind: type) -> Any:
    """Coerce one incoming value, raising rather than storing something odd."""
    if kind is int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{name} must be a whole number") from exc
    if kind is list:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise SettingsError(f"{name} must be a list or a comma-separated string")
    return str(value if value is not None else "").strip()


def read_overrides(db: Any) -> dict[str, Any]:
    """Every stored override, decoded. Malformed rows are skipped, not fatal.

    A corrupt row must not stop the server booting: an operator who cannot
    start the application has no way to fix the setting that stopped it.
    """
    overrides: dict[str, Any] = {}
    try:
        rows = db.conn.execute("SELECT key, value_json FROM app_settings").fetchall()
    except Exception as exc:  # noqa: BLE001 - table may predate this feature
        log.debug("No stored settings available: %s", exc)
        return overrides
    for row in rows:
        try:
            overrides[str(row["key"])] = json.loads(row["value_json"])
        except (TypeError, ValueError):
            log.warning("Ignoring unreadable stored setting %r", row["key"])
    return overrides


def apply_overrides(settings: Settings, overrides: dict[str, Any]) -> Settings:
    """Fold stored overrides over the environment-derived settings.

    Database wins over environment, which wins over the code defaults - see the
    module docstring for why that order and not the reverse. Only fields in
    `EDITABLE` are applied even if something else somehow reached the table,
    and only non-empty values: a blank stored credential means "not set here",
    which must fall through to the environment rather than blanking a key an
    administrator deliberately put in `.env`.
    """
    known = {field.name for field in fields(settings)}
    changes: dict[str, Any] = {}
    for name, value in overrides.items():
        if name not in EDITABLE or name not in known:
            continue
        if value in ("", [], None):
            continue
        changes[name] = value
    if not changes:
        return settings
    log.info("Applying %d stored setting override(s): %s", len(changes), ", ".join(sorted(changes)))
    return replace(settings, **changes)


def public_view(settings: Settings, overrides: dict[str, Any]) -> dict[str, Any]:
    """The settings page's payload. Contains no secret values.

    For a credential this reports only whether one is configured, a
    four-character tail to tell two keys apart, and where it came from -
    enough to answer "is the right key in?" without ever putting the key on the
    wire. `source` matters because "I set it in the UI" and "it came from .env"
    behave differently on the next deploy.
    """
    result: dict[str, Any] = {"fields": {}, "restart_required": sorted(RESTART_REQUIRED),
                              "client": {}}
    for name in EDITABLE:
        value = getattr(settings, name, None)
        stored = name in overrides and overrides[name] not in ("", [], None)
        entry: dict[str, Any] = {
            "source": "database" if stored else ("environment" if value else "unset"),
            "restart_required": name in RESTART_REQUIRED,
        }
        if name in SECRETS:
            text = str(value or "")
            entry["configured"] = bool(text)
            # A tail, not a prefix: many key formats share a common prefix, and
            # the last characters are what actually distinguish two keys.
            entry["hint"] = f"…{text[-4:]}" if len(text) >= 4 else ("set" if text else "")
        else:
            entry["value"] = value
        result["fields"][name] = entry
    for name in CLIENT_PREFERENCES:
        result["client"][name] = overrides.get(name, "")
    return result


def write(db: Any, updates: dict[str, Any], actor: str = "local operator") -> list[str]:
    """Store settings, returning the names that need a restart to take effect.

    Rejects unknown keys outright: a typo silently persisted into a table
    nobody reads again is a worse outcome than an error the operator sees
    immediately.
    """
    if not isinstance(updates, dict) or not updates:
        raise SettingsError("no settings supplied")
    unknown = set(updates) - set(EDITABLE) - set(CLIENT_PREFERENCES)
    if unknown:
        raise SettingsError(f"unknown setting(s): {', '.join(sorted(unknown))}")

    coerced: dict[str, Any] = {}
    for name, value in updates.items():
        kind = EDITABLE.get(name) or CLIENT_PREFERENCES[name]
        coerced[name] = _coerce(name, value, kind)
    if "symbology_profile" in coerced:
        from .symbology import PROFILES
        if coerced["symbology_profile"] not in PROFILES:
            raise SettingsError(f"symbology_profile must be one of {', '.join(sorted(PROFILES))}")

    now = _now()
    with db._write_lock:
        try:
            for name, value in coerced.items():
                db.conn.execute(
                    "INSERT INTO app_settings (key,value_json,updated_by,updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,"
                    "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                    (name, json.dumps(value), str(actor)[:200], now))
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
    # Deliberately logs the *names* only. A settings write that echoed the new
    # FIRMS key into the log file would undo the whole point of never returning
    # it over the API.
    log.info("Settings updated by %s: %s", actor, ", ".join(sorted(coerced)))
    return sorted(name for name in coerced if name in RESTART_REQUIRED)


def clear(db: Any, name: str, actor: str = "local operator") -> None:
    """Remove one override, falling back to the environment value beneath it."""
    if name not in EDITABLE and name not in CLIENT_PREFERENCES:
        raise SettingsError(f"unknown setting {name!r}")
    with db._write_lock:
        try:
            db.conn.execute("DELETE FROM app_settings WHERE key=?", (name,))
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
    log.info("Setting %s cleared by %s", name, actor)


__all__ = ["CLIENT_PREFERENCES", "EDITABLE", "RESTART_REQUIRED", "SECRETS",
           "SettingsError", "apply_overrides", "clear", "public_view", "read_overrides", "write"]
