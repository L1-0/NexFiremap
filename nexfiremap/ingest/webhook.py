"""Generic mapped-JSON ingest - the escape hatch for dispatch/CAD systems.

CAD integration is where standards go to die. Alamos FE2, Divera 24/7, and
every US CAD vendor fire a webhook with a JSON body of their own invention,
documented nowhere, and different again after the next release. Chasing each
vendor's API means a module per vendor that rots the moment they change a
field name.

So this does the opposite: one endpoint, and an operator-supplied **mapping**
that says which paths in *their* payload correspond to the fields NexFiremap
needs. Adding a new CAD system becomes a row in a table, not a release.

The mapping is deliberately not a language:

    {"callsign": "$.unit.name", "latitude": "$.location.lat",
     "observed_at": "$.timestamp", "note": "unit {$.unit.name} dispatched"}

A value is either a **dotted path** into the payload (``$.a.b[0].c``) or a
**literal template** with paths interpolated into it. There is no arithmetic,
no conditionals, no function calls, and above all no `eval`. That restriction
is the security property: this endpoint is authenticated only by a per-hook
token and receives arbitrary JSON from an outside system, so a mapping DSL
with an expression evaluator would be a remote-code-execution surface reachable
by anyone who ever learns a hook URL. Everything here is lookup and string
substitution, and `tests/test_webhook.py` asserts that expressions are *not*
evaluated.

The other half of making an undocumented feed workable is `describe`: rather
than rejecting a payload that does not match the mapping, the caller stores it
raw (bounded) so an operator can look at what the vendor actually sent and fix
the mapping - which is the difference between a debuggable integration and a
support ticket.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import (
    CONTRACT_POSITION, IngestError, clean_text, coerce_float, iso_timestamp, position_report,
)

NAME = "webhook"
CONTRACT = CONTRACT_POSITION
#: Deliberately empty: this adapter must never be selected by content-type
#: sniffing. It cannot parse anything without a mapping, and claiming
#: ``application/json`` would make `adapter_for_media_type` hand it every
#: ordinary JSON batch posted to the position-feed endpoint - which then fails
#: with "a mapping is required", breaking the native feed for every existing
#: client. It is only ever invoked explicitly, by `WebhookManager.receive`,
#: which knows which mapping applies.
MEDIA_TYPES: tuple[str, ...] = ()

#: A path reference: ``$.a.b`` or ``$.a[0].b``. Anchored at ``$`` so a plain
#: string like "Engine 11" is unambiguously a literal, not a lookup.
PATH_PATTERN = re.compile(r"\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])+")

#: Ceiling on how deep a path may go, so a pathological mapping cannot walk a
#: deeply-nested payload forever.
MAX_PATH_DEPTH = 12

#: Fields a mapping may target. Restricting the *targets* as well as the
#: expressions means a mapping cannot inject arbitrary keys into a stored
#: report - only the ones the position contract actually has.
MAPPABLE_FIELDS = {
    "external_id", "callsign", "observed_at", "latitude", "longitude",
    "altitude_m", "speed_kmh", "heading_deg", "accuracy_m", "resource_id",
}


def _lookup(payload: Any, path: str) -> Any:
    """Resolve one ``$.a.b[0]`` path against a payload, or ``None``.

    Missing is ``None`` rather than an error because a CAD system routinely
    omits fields it has nothing for (no GPS yet, no unit assigned), and one
    absent optional field must not reject the whole message.
    """
    current = current_payload = payload
    steps = re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", path)
    if len(steps) > MAX_PATH_DEPTH:
        raise IngestError(f"mapping path {path!r} is nested more than {MAX_PATH_DEPTH} levels deep")
    for name, index in steps:
        if name:
            if not isinstance(current, dict):
                return None
            current = current.get(name)
        else:
            if not isinstance(current, list):
                return None
            position = int(index)
            if position >= len(current):
                return None
            current = current[position]
        if current is None:
            return None
    del current_payload
    return current


def resolve(expression: Any, payload: Any) -> Any:
    """Evaluate one mapping expression - a bare path, a template, or a literal.

    A bare path returns the payload's own value with its *type intact*, which
    matters: a latitude arriving as a JSON number must stay a number rather
    than being stringified and re-parsed. Only a template (a path embedded in
    surrounding text) produces a string, because that is what interpolation
    means.
    """
    if not isinstance(expression, str):
        return expression  # a literal number/bool in the mapping
    text = expression.strip()
    if not text:
        return None
    match = PATH_PATTERN.fullmatch(text)
    if match:
        return _lookup(payload, text)
    if "$." not in text:
        return text  # a plain literal, e.g. a fixed callsign for a single-unit hook
    # Template: substitute each path, stringifying only here.
    return PATH_PATTERN.sub(
        lambda found: "" if (value := _lookup(payload, found.group(0))) is None else str(value), text)


def validate_mapping(mapping: Any) -> dict[str, Any]:
    """Check a mapping before it is stored, so a bad one fails at configuration
    time rather than at 3am when the first real dispatch arrives."""
    if not isinstance(mapping, dict) or not mapping:
        raise IngestError("mapping must be a non-empty object")
    unknown = set(mapping) - MAPPABLE_FIELDS
    if unknown:
        raise IngestError(f"mapping targets unknown fields: {', '.join(sorted(unknown))}; "
                          f"allowed: {', '.join(sorted(MAPPABLE_FIELDS))}")
    for field, expression in mapping.items():
        if isinstance(expression, str):
            for found in PATH_PATTERN.findall(expression):
                if len(re.findall(r"\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\]", found)) > MAX_PATH_DEPTH:
                    raise IngestError(f"mapping for {field!r} is nested too deeply")
        elif not isinstance(expression, (int, float, bool)) and expression is not None:
            raise IngestError(f"mapping for {field!r} must be a string, number or boolean")
    for required in ("callsign", "latitude", "longitude"):
        if required not in mapping:
            raise IngestError(f"mapping must provide {required!r}")
    return dict(mapping)


def parse(raw: bytes, *, mapping: dict[str, Any] | None = None,
          default_callsign: str = "cad", **_context: Any) -> list[dict[str, Any]]:
    """Apply a mapping to a webhook payload, yielding position reports.

    Accepts a single object or a list of them - vendors differ on whether a
    batch is one request with an array or several requests - so a caller does
    not have to know which shape their CAD produces.
    """
    if mapping is None:
        raise IngestError("a mapping is required to interpret a webhook payload")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IngestError(f"webhook body must be JSON: {exc}") from exc

    messages = payload if isinstance(payload, list) else [payload]
    if len(messages) > 500:
        raise IngestError("webhook payload contains more than 500 messages")

    reports = []
    for message in messages:
        values = {field: resolve(expression, message) for field, expression in mapping.items()}
        callsign = clean_text(values.get("callsign"), 200) or clean_text(default_callsign, 200)
        latitude = coerce_float(values.get("latitude"), "latitude", low=-90, high=90, allow_none=False)
        longitude = coerce_float(values.get("longitude"), "longitude", low=-180, high=180, allow_none=False)
        observed_at = iso_timestamp(values["observed_at"]) if values.get("observed_at") else None
        if observed_at is None:
            from datetime import datetime, timezone
            observed_at = iso_timestamp(datetime.now(timezone.utc).isoformat())
        reports.append(position_report(
            # `external_id` is the replay key, and mapping it is the single
            # easiest way to misconfigure a hook. It must identify **this
            # position report**, not the incident or the vehicle: pointing it
            # at a dispatch/Einsatz number (the obvious-looking choice, since
            # every CAD has one) makes every later position for that incident
            # collide, and `TelemetryManager.ingest` correctly rejects them
            # with "external_id was already used with different position
            # content" - so a moving vehicle silently stops updating after its
            # first fix.
            #
            # Map it only when the CAD emits a genuinely per-message id.
            # Leaving it unmapped is the safe default and gives the same
            # callsign+timestamp key every other adapter here uses.
            external_id=clean_text(values.get("external_id"), 200) or f"{callsign}@{observed_at}",
            callsign=callsign,
            observed_at=observed_at,
            latitude=latitude,
            longitude=longitude,
            altitude_m=values.get("altitude_m"),
            speed_kmh=values.get("speed_kmh"),
            heading_deg=values.get("heading_deg"),
            accuracy_m=values.get("accuracy_m"),
            resource_id=clean_text(values.get("resource_id"), 64) or None,
            extra={"protocol": "webhook"},
        ))
    if not reports:
        raise IngestError("webhook payload contained no messages")
    return reports


def describe(raw: bytes, *, limit: int = 60) -> dict[str, Any]:
    """Summarise an unmapped payload as a list of candidate paths and values.

    This is what makes an undocumented CAD feed tractable: an operator points
    the vendor at the hook, looks at what actually arrived, and builds the
    mapping from the paths listed here rather than from documentation that may
    not exist. Values are truncated because the point is to recognise a field,
    not to store the payload twice.
    """
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IngestError(f"webhook body must be JSON: {exc}") from exc

    found: list[dict[str, Any]] = []

    def walk(node: Any, path: str, depth: int) -> None:
        if len(found) >= limit or depth > MAX_PATH_DEPTH:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}", depth + 1)
        elif isinstance(node, list):
            # Only the first element: a 500-entry array would otherwise
            # produce 500 near-identical suggestions and bury the useful ones.
            if node:
                walk(node[0], f"{path}[0]", depth + 1)
        else:
            found.append({"path": path, "value": str(node)[:120],
                          "type": type(node).__name__})

    walk(payload if not isinstance(payload, list) else (payload[0] if payload else {}), "$", 0)
    return {"paths": found, "truncated": len(found) >= limit,
            "was_list": isinstance(payload, list)}


__all__ = ["CONTRACT", "MAPPABLE_FIELDS", "MAX_PATH_DEPTH", "MEDIA_TYPES", "NAME",
           "PATH_PATTERN", "describe", "parse", "resolve", "validate_mapping"]
