"""Provider-neutral, replay-safe incident vehicle telemetry.

Any GPS-tracking device or app (a vehicle AVL unit, a phone app, a hand-held
GPS) can push position reports here once it's been issued a per-source
ingest token - the schema doesn't assume a particular vendor's field names,
just latitude/longitude/observed_at plus optional speed/heading/accuracy.

Raw observations are immutable: once a position report is accepted it is
never edited or deleted, only ever superseded by a later report. All
smoothing, freshness and segment quality information returned by this module
(``tracks``' segment splitting, ``interpolate``'s linear estimate, the
"stale"/"quality" flags) is derived and explicitly labelled as such, so a
consumer can always tell an as-reported value from a computed one.

"Replay-safe" means an ingest client can resend the same batch (after a
dropped response, a retry, a crash) without creating duplicate reports or
raising an error - see ``ingest``'s per-``external_id`` payload-hash check.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .config import Settings
from .operations import NotFoundError, OperationsStore, utcnow


log = logging.getLogger("nexfiremap.telemetry")


class TelemetryError(ValueError):
    """Invalid feed configuration or a rejected ingest batch/report."""


class TelemetryRateLimit(TelemetryError):
    """One feed source exceeded its configured requests-per-minute budget."""


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _time(value: Any) -> tuple[str, float]:
    """Parse an ISO 8601 timestamp, requiring an explicit timezone (an
    unqualified local time from a device with an unknown clock/timezone
    would be silently wrong rather than usefully approximate) - returns
    both the normalised ISO string (for storage/display) and the epoch
    seconds (for arithmetic/ordering)."""
    raw = _text(value, 80)
    if not raw:
        raise TelemetryError("observed_at is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TelemetryError("observed_at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise TelemetryError("observed_at must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z"), parsed.timestamp()


def _haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Great-circle distance in km - used to sanity-check implausible speed
    jumps (``tracks``) and to derive a speed estimate when a report doesn't
    carry its own (``ingest``'s ``quality["derived_speed_kmh"]``)."""
    radius = 6371.0088
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    term = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(term), math.sqrt(max(0.0, 1 - term)))


def _bearing(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Initial compass bearing (0-360 deg) from a to b - used by
    ``interpolate`` to report a direction of travel alongside its estimated position."""
    p1, p2, dl = math.radians(a_lat), math.radians(b_lat), math.radians(b_lon - a_lon)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _row(row: Any) -> dict[str, Any]:
    """SQLite row -> API dict: expand the ``*_json`` columns back into
    nested objects and strip ``token_hash`` unconditionally - the ingest
    token's hash must never reach an API response, even read-only ones,
    since a leaked hash together with knowledge of the hashing scheme would
    let someone impersonate the feed (see ``_token_hash``/``ingest``)."""
    item = dict(row)
    for field in ("metadata_json", "quality_json", "raw_json"):
        if field in item:
            item[field.removesuffix("_json")] = json.loads(item.pop(field) or "{}")
    item.pop("token_hash", None)
    return item


class TelemetryManager:
    """CRUD for position-feed sources plus the ingest/query paths that use
    them: ``ingest`` (device -> server, token-authenticated), ``latest``/
    ``tracks``/``interpolate`` (server -> map UI, incident-authenticated)."""

    def __init__(self, store: OperationsStore, settings: Settings) -> None:
        self.store = store
        self.db = store.db
        self.settings = settings
        self._rate_lock = threading.Lock()
        self._request_times: dict[str, deque[float]] = {}

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_source(self, incident_id: str, data: dict[str, Any], actor: str) -> dict[str, Any]:
        """Register a new position-feed source and issue its ingest token.
        The token is returned only here (and from ``rotate_token``) - only
        its SHA-256 is ever stored, so it can't be recovered later even by
        an operator with database access, only rotated."""
        self.store.get_incident(incident_id)
        name = _text(data.get("name"), 200)
        if not name:
            raise TelemetryError("feed name is required")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise TelemetryError("metadata must be an object")
        token, source_id, now = secrets.token_urlsafe(32), uuid4().hex, utcnow()
        values = (source_id, incident_id, name, _text(data.get("provider"), 200),
                  _text(data.get("device_kind"), 80) or "vehicle_gps", self._token_hash(token),
                  json.dumps(metadata, sort_keys=True, separators=(",", ":")), actor[:200], now, now)
        with self.db._write_lock:
            self.db.conn.execute(
                "INSERT INTO position_feed_sources "
                "(id,incident_id,name,provider,device_kind,token_hash,metadata_json,created_by,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", values,
            )
            self.store._audit(incident_id, "position_feed", source_id, "create", 1,
                              {"name": name, "provider": values[3], "device_kind": values[4]}, actor)
            self.db.conn.commit()
        result = self.get_source(incident_id, source_id)
        result["ingest_token"] = token
        result["token_shown_once"] = True
        return result

    def list_sources(self, incident_id: str) -> list[dict[str, Any]]:
        self.store.get_incident(incident_id)
        return [_row(row) for row in self.db.conn.execute(
            "SELECT * FROM position_feed_sources WHERE incident_id=? ORDER BY name", (incident_id,)
        ).fetchall()]

    def get_source(self, incident_id: str, source_id: str) -> dict[str, Any]:
        row = self.db.conn.execute(
            "SELECT * FROM position_feed_sources WHERE id=? AND incident_id=?", (source_id, incident_id)
        ).fetchone()
        if row is None:
            raise NotFoundError("position feed not found")
        return _row(row)

    def update_source(self, incident_id: str, source_id: str, data: dict[str, Any], actor: str) -> dict[str, Any]:
        current = self.get_source(incident_id, source_id)
        expected = int(data.get("expected_revision") or 0)
        if expected != int(current["revision"]):
            raise TelemetryError("position feed revision conflict")
        changes: dict[str, Any] = {}
        for key, limit in (("name", 200), ("provider", 200), ("device_kind", 80)):
            if key in data:
                changes[key] = _text(data[key], limit)
        if "active" in data:
            changes["active"] = int(bool(data["active"]))
        if "metadata" in data:
            if not isinstance(data["metadata"], dict):
                raise TelemetryError("metadata must be an object")
            changes["metadata_json"] = json.dumps(data["metadata"], sort_keys=True, separators=(",", ":"))
        if not changes:
            return current
        changes["updated_at"], changes["revision"] = utcnow(), expected + 1
        assignments = ",".join(f"{key}=?" for key in changes)
        with self.db._write_lock:
            result = self.db.conn.execute(
                f"UPDATE position_feed_sources SET {assignments} WHERE id=? AND incident_id=? AND revision=?",
                (*changes.values(), source_id, incident_id, expected),
            )
            if result.rowcount != 1:
                self.db.conn.rollback()
                raise TelemetryError("position feed revision conflict")
            self.store._audit(incident_id, "position_feed", source_id, "update", expected + 1, changes, actor)
            self.db.conn.commit()
        return self.get_source(incident_id, source_id)

    def rotate_token(self, incident_id: str, source_id: str, actor: str) -> dict[str, Any]:
        """Issue a fresh ingest token, invalidating the old one immediately
        - for a suspected-leaked token or routine credential hygiene."""
        current = self.get_source(incident_id, source_id)
        token, now, revision = secrets.token_urlsafe(32), utcnow(), int(current["revision"]) + 1
        with self.db._write_lock:
            self.db.conn.execute(
                "UPDATE position_feed_sources SET token_hash=?,updated_at=?,revision=? WHERE id=? AND incident_id=?",
                (self._token_hash(token), now, revision, source_id, incident_id),
            )
            self.store._audit(incident_id, "position_feed", source_id, "rotate_token", revision, {}, actor)
            self.db.conn.commit()
        return {**self.get_source(incident_id, source_id), "ingest_token": token, "token_shown_once": True}

    def ingest(self, source_id: str, token: str, reports: list[dict[str, Any]]) -> dict[str, Any]:
        """Accept a batch of position reports from one authenticated device.

        Token check uses ``hmac.compare_digest`` (constant-time) rather than
        ``==`` so comparing the hash can't leak timing information about how
        much of it matched - standard practice for any secret comparison
        reachable by an untrusted caller. Per-source rate limiting is a
        sliding one-minute window kept in memory (``_request_times``), not
        persisted - acceptable because it resets to "no history" on a
        restart, which only ever makes the limit temporarily more permissive,
        never less.
        """
        source_row = self.db.conn.execute("SELECT * FROM position_feed_sources WHERE id=?", (source_id,)).fetchone()
        if source_row is None or not source_row["active"]:
            raise TelemetryError("position feed is unavailable")
        if not token or not hmac.compare_digest(self._token_hash(token), str(source_row["token_hash"])):
            raise PermissionError("invalid feed token")
        return self._accept(source_row, reports)

    def ingest_authenticated(self, source_id: str, reports: list[dict[str, Any]]) -> dict[str, Any]:
        """`ingest` for a caller that has **already authenticated by other means**.

        Exists because the feed token is stored only as a SHA-256, so a
        server-side component cannot reproduce it to call `ingest`. The
        webhook receiver is the case that forced this: it authenticates the
        caller against the *hook's* own credential and then has to write to
        the feed the hook is registered against. The alternatives were worse -
        keeping a second plaintext copy of the feed token in the webhooks
        table (another secret at rest, another thing to rotate), or
        duplicating the whole validation and write path.

        The security contract is therefore explicit and narrow: **this method
        performs no authentication whatsoever**. It is not reachable from any
        route directly, and every caller must have verified a credential of its
        own first. Everything after the token check - rate limiting, batch
        limits, replay-safety, quality flags, the resource-position update and
        the audit entry - is identical to `ingest`, which is the entire point
        of sharing this path rather than writing rows separately.
        """
        source_row = self.db.conn.execute("SELECT * FROM position_feed_sources WHERE id=?", (source_id,)).fetchone()
        if source_row is None or not source_row["active"]:
            raise TelemetryError("position feed is unavailable")
        return self._accept(source_row, reports)

    def _accept(self, source_row: Any, reports: list[dict[str, Any]]) -> dict[str, Any]:
        """Everything `ingest` does once the caller is known to be authorised.

        Split out so `ingest` and `ingest_authenticated` cannot drift: the
        difference between them is exactly one token comparison, and it should
        be visible as exactly that rather than as two similar-looking method
        bodies.
        """
        source_id = str(source_row["id"])
        now_monotonic = time.monotonic()
        with self._rate_lock:
            history = self._request_times.setdefault(source_id, deque())
            while history and history[0] <= now_monotonic - 60:
                history.popleft()
            if len(history) >= self.settings.telemetry_requests_per_minute:
                raise TelemetryRateLimit("position feed request rate exceeded")
            history.append(now_monotonic)
        if not isinstance(reports, list) or not 1 <= len(reports) <= self.settings.telemetry_max_batch:
            raise TelemetryError(f"positions must contain 1 to {self.settings.telemetry_max_batch} reports")

        incident_id, received_at = str(source_row["incident_id"]), utcnow()
        received_epoch = datetime.fromisoformat(received_at).timestamp()
        prepared: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in reports:
            if not isinstance(raw, dict):
                raise TelemetryError("each position must be an object")
            external_id, callsign = _text(raw.get("external_id"), 200), _text(raw.get("callsign"), 200)
            if not external_id or not callsign:
                raise TelemetryError("external_id and callsign are required")
            if external_id in seen:
                raise TelemetryError("external_id is duplicated within the batch")
            seen.add(external_id)
            observed_at, epoch = _time(raw.get("observed_at"))
            if epoch > received_epoch + 300:
                raise TelemetryError("observed_at is more than five minutes in the future")
            try:
                lat, lon = float(raw["latitude"]), float(raw["longitude"])
            except (KeyError, TypeError, ValueError) as exc:
                raise TelemetryError("latitude and longitude must be numbers") from exc
            if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise TelemetryError("latitude or longitude is outside the valid range")
            numeric: dict[str, float | None] = {}
            for key in ("altitude_m", "speed_kmh", "heading_deg", "accuracy_m"):
                value = raw.get(key)
                try:
                    numeric[key] = None if value is None else float(value)
                except (TypeError, ValueError) as exc:
                    raise TelemetryError(f"{key} must be numeric") from exc
                if numeric[key] is not None and not math.isfinite(numeric[key]):
                    raise TelemetryError(f"{key} must be finite")
            if numeric["speed_kmh"] is not None and numeric["speed_kmh"] < 0:
                raise TelemetryError("speed_kmh cannot be negative")
            if numeric["accuracy_m"] is not None and numeric["accuracy_m"] < 0:
                raise TelemetryError("accuracy_m cannot be negative")
            if numeric["heading_deg"] is not None and not 0 <= numeric["heading_deg"] <= 360:
                raise TelemetryError("heading_deg must be between 0 and 360")
            resource_id = _text(raw.get("resource_id"), 64) or None
            if resource_id and self.db.conn.execute(
                "SELECT 1 FROM incident_resources WHERE id=? AND incident_id=?", (resource_id, incident_id)
            ).fetchone() is None:
                raise TelemetryError("resource_id does not belong to this incident")
            canonical = {"external_id": external_id, "callsign": callsign, "resource_id": resource_id,
                         "observed_at": observed_at, "latitude": lat, "longitude": lon, **numeric}
            # Preserve the complete sender object (including provider-specific
            # fields) as evidence. Canonical values live in dedicated columns.
            raw_json = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            # payload_sha256 is the replay-safety key: hashing the sorted-key
            # JSON (not the raw bytes) means a semantically-identical resend
            # with different key ordering or whitespace still hashes the same.
            prepared.append({**canonical, "observed_epoch": epoch, "raw_json": raw_json,
                             "payload_sha256": hashlib.sha256(raw_json.encode()).hexdigest()})

        accepted = replayed = 0
        with self.db._write_lock:
            try:
                for item in prepared:
                    existing = self.db.conn.execute(
                        "SELECT payload_sha256 FROM vehicle_position_reports WHERE source_id=? AND external_id=?",
                        (source_id, item["external_id"]),
                    ).fetchone()
                    if existing is not None:
                        # Same external_id seen before: if the payload hash
                        # matches, this is a harmless resend of a batch
                        # already accepted (client retry after a dropped
                        # response, etc.) - count it and move on rather than
                        # erroring. A different hash under the same
                        # external_id means the device reused an id for a
                        # genuinely different report, which is a real data
                        # problem worth surfacing rather than silently
                        # overwriting history.
                        if hmac.compare_digest(str(existing[0]), item["payload_sha256"]):
                            replayed += 1
                            continue
                        raise TelemetryError("external_id was already used with different position content")
                    previous = self.db.conn.execute(
                        "SELECT observed_epoch,latitude,longitude FROM vehicle_position_reports "
                        "WHERE source_id=? AND callsign=? ORDER BY observed_epoch DESC LIMIT 1",
                        (source_id, item["callsign"]),
                    ).fetchone()
                    # Quality flags are derived and stored alongside the raw
                    # report (never mutate the report itself) so a consumer
                    # can distinguish "this is what the device sent" from
                    # "this is what we inferred about it" - matches the
                    # module docstring's immutability guarantee.
                    quality: dict[str, Any] = {"late": received_epoch - item["observed_epoch"] > self.settings.position_stale_seconds,
                                               "out_of_order": bool(previous and item["observed_epoch"] < previous[0]),
                                               "poor_accuracy": bool(item["accuracy_m"] is not None and item["accuracy_m"] > 100)}
                    if previous and item["observed_epoch"] > previous[0]:
                        hours = (item["observed_epoch"] - previous[0]) / 3600
                        derived_speed = _haversine_km(previous[1], previous[2], item["latitude"], item["longitude"]) / hours
                        quality["derived_speed_kmh"] = round(derived_speed, 3)
                        quality["implausible_speed"] = derived_speed > self.settings.position_max_speed_kmh
                    else:
                        quality["implausible_speed"] = False
                    report_id = uuid4().hex
                    self.db.conn.execute(
                        "INSERT INTO vehicle_position_reports "
                        "(id,incident_id,source_id,resource_id,external_id,callsign,observed_at,observed_epoch,received_at,"
                        "latitude,longitude,altitude_m,speed_kmh,heading_deg,accuracy_m,quality_json,raw_json,payload_sha256) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (report_id, incident_id, source_id, item["resource_id"], item["external_id"], item["callsign"],
                         item["observed_at"], item["observed_epoch"], received_at, item["latitude"], item["longitude"],
                         item["altitude_m"], item["speed_kmh"], item["heading_deg"], item["accuracy_m"],
                         json.dumps(quality, sort_keys=True, separators=(",", ":")), item["raw_json"], item["payload_sha256"]),
                    )
                    if item["resource_id"]:
                        # The WHERE clause's own freshness check
                        # (position_at IS NULL OR < this report's time)
                        # matters because a batch's reports aren't
                        # necessarily in chronological order and a resource
                        # can be tracked by more than one feed - this update
                        # is a no-op rather than a regression whenever a
                        # newer position for the same resource already won.
                        self.db.conn.execute(
                            "UPDATE incident_resources SET latitude=?,longitude=?,position_at=?,updated_at=?,revision=revision+1 "
                            "WHERE id=? AND incident_id=? AND (position_at IS NULL OR position_at < ?)",
                            (item["latitude"], item["longitude"], item["observed_at"], received_at,
                             item["resource_id"], incident_id, item["observed_at"]),
                        )
                    accepted += 1
                self.db.conn.execute("UPDATE position_feed_sources SET last_received_at=?,updated_at=? WHERE id=?",
                                     (received_at, received_at, source_id))
                if accepted:
                    self.store._audit(incident_id, "position_feed", source_id, "ingest", int(source_row["revision"]),
                                      {"accepted": accepted, "replayed": replayed, "received_at": received_at},
                                      f"feed:{source_id}")
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        # Safety evaluation runs *after* the transaction has committed, never
        # inside it. A position that is stored is a unit that stays on the map;
        # an evaluation that fails - a pruned job directory, a missing optional
        # dependency - must not be able to roll back the position that
        # triggered it. `evaluate_position` returns warnings-or-nothing and
        # never raises, and this is wrapped as well because the one thing worse
        # than an unraised warning is a lost position.
        warnings: list[dict[str, Any]] = []
        if accepted:
            try:
                warnings = self._evaluate_safety(incident_id, prepared)
            except Exception:  # noqa: BLE001 - see above
                log.debug("Safety evaluation failed for incident %s", incident_id, exc_info=True)

        return {"source_id": source_id, "incident_id": incident_id, "accepted": accepted,
                "warnings": warnings,
                "replayed": replayed, "received_at": received_at}

    def _evaluate_safety(self, incident_id: str, prepared: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Check each unit's newest position against hazards and the model.

        Only the *newest* position per callsign is evaluated, not every report
        in the batch: a device catching up after a signal gap can deliver
        several minutes of history at once, and warning about where a crew was
        ten minutes ago - possibly repeatedly - would bury the one warning that
        is about where they are now.
        """
        from .safety import evaluate_position, record_warnings

        newest: dict[str, dict[str, Any]] = {}
        for item in prepared:
            current = newest.get(item["callsign"])
            if current is None or item["observed_epoch"] > current["observed_epoch"]:
                newest[item["callsign"]] = item

        warnings: list[dict[str, Any]] = []
        for callsign, item in newest.items():
            warnings.extend(evaluate_position(
                self.db, self.settings, incident_id, callsign=callsign,
                latitude=item["latitude"], longitude=item["longitude"]))
        if warnings:
            record_warnings(self.store, incident_id, warnings)
            for warning in warnings:
                log.warning("SAFETY: %s", warning["message"])
        return warnings

    def latest(self, incident_id: str) -> dict[str, Any]:
        """Most recent position per (source, callsign), as a point
        FeatureCollection for the live map layer - one marker per tracked
        vehicle/person, not one per historical report."""
        self.store.get_incident(incident_id)
        rows = self.db.conn.execute(
            "SELECT * FROM (SELECT r.*, ROW_NUMBER() OVER (PARTITION BY source_id,callsign ORDER BY observed_epoch DESC) AS rn "
            "FROM vehicle_position_reports r WHERE incident_id=?) WHERE rn=1 ORDER BY callsign", (incident_id,)
        ).fetchall()
        now = datetime.now(timezone.utc).timestamp()
        features = []
        for row in rows:
            item = _row(row); item.pop("rn", None); item.pop("raw", None)
            item["age_seconds"] = max(0, round(now - item["observed_epoch"], 1))
            item["stale"] = item["age_seconds"] > self.settings.position_stale_seconds
            features.append({"type": "Feature", "id": item["id"],
                             "geometry": {"type": "Point", "coordinates": [item.pop("longitude"), item.pop("latitude")]},
                             "properties": item})
        return {"type": "FeatureCollection", "features": features,
                "freshness_threshold_seconds": self.settings.position_stale_seconds}

    def tracks(self, incident_id: str, start: str | None = None, end: str | None = None,
               gap_seconds: int = 900) -> dict[str, Any]:
        """Historical path per (source, callsign) as LineString/Point
        features, split into separate segments wherever the trail isn't
        trustworthy as a continuous path: a time gap bigger than
        ``gap_seconds`` (device was off/out of coverage - joining across it
        would draw a straight line through wherever it actually went) or an
        implausible speed jump between consecutive reports (bad GPS fix,
        clock skew, or a genuinely different device reusing a callsign)."""
        self.store.get_incident(incident_id)
        params: list[Any] = [incident_id]; where = ["incident_id=?"]
        if start:
            where.append("observed_epoch>=?"); params.append(_time(start)[1])
        if end:
            where.append("observed_epoch<=?"); params.append(_time(end)[1])
        rows = self.db.conn.execute(
            f"SELECT * FROM vehicle_position_reports WHERE {' AND '.join(where)} ORDER BY source_id,callsign,observed_epoch,id", params
        ).fetchall()
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            item = _row(row); grouped.setdefault((item["source_id"], item["callsign"]), []).append(item)
        features = []
        for (source_id, callsign), points in grouped.items():
            segments: list[list[dict[str, Any]]] = []
            current: list[dict[str, Any]] = []
            for point in points:
                split_reason = None
                if current:
                    delta = point["observed_epoch"] - current[-1]["observed_epoch"]
                    speed = _haversine_km(current[-1]["latitude"], current[-1]["longitude"],
                                          point["latitude"], point["longitude"]) / max(delta / 3600, 1e-9)
                    if delta > gap_seconds: split_reason = "time_gap"
                    elif delta <= 0 or speed > self.settings.position_max_speed_kmh: split_reason = "implausible_jump"
                if split_reason and current:
                    segments.append(current); current = []
                current.append(point)
            if current: segments.append(current)
            for index, segment in enumerate(segments):
                if len(segment) == 1:
                    geometry = {"type": "Point", "coordinates": [segment[0]["longitude"], segment[0]["latitude"]]}
                else:
                    geometry = {"type": "LineString", "coordinates": [[p["longitude"], p["latitude"]] for p in segment]}
                features.append({"type": "Feature", "geometry": geometry,
                                 "properties": {"source_id": source_id, "callsign": callsign, "segment": index,
                                                "point_count": len(segment), "starts_at": segment[0]["observed_at"],
                                                "ends_at": segment[-1]["observed_at"], "raw_observation_ids": [p["id"] for p in segment]}})
        return {"type": "FeatureCollection", "features": features, "gap_seconds": gap_seconds,
                "max_speed_kmh": self.settings.position_max_speed_kmh}

    def interpolate(self, incident_id: str, at: str, *, source_id: str, callsign: str,
                    maximum_gap_seconds: int = 600) -> dict[str, Any]:
        """Estimate where a tracked vehicle/person was at an arbitrary
        timestamp between two real reports, by linear interpolation in
        WGS84 degrees (adequate for the short local distances/times this is
        used over - not a geodesic solution). Refuses to interpolate across
        a gap bigger than ``maximum_gap_seconds`` - past that, a straight
        line is more likely to mislead than help, matching ``tracks``'
        segment-splitting reasoning. The result is explicitly labelled
        ``"estimated": True`` (except at an exact-match timestamp) so a
        caller never confuses it with a raw observation."""
        target_iso, target = _time(at)
        before = self.db.conn.execute(
            "SELECT * FROM vehicle_position_reports WHERE incident_id=? AND source_id=? AND callsign=? "
            "AND observed_epoch<=? ORDER BY observed_epoch DESC LIMIT 1", (incident_id, source_id, callsign, target)
        ).fetchone()
        after = self.db.conn.execute(
            "SELECT * FROM vehicle_position_reports WHERE incident_id=? AND source_id=? AND callsign=? "
            "AND observed_epoch>=? ORDER BY observed_epoch LIMIT 1", (incident_id, source_id, callsign, target)
        ).fetchone()
        if before is None or after is None:
            raise NotFoundError("position cannot be bracketed at the requested time")
        left, right = _row(before), _row(after); span = right["observed_epoch"] - left["observed_epoch"]
        if span > maximum_gap_seconds:
            raise TelemetryError("position gap exceeds interpolation limit")
        ratio = 0.0 if span == 0 else (target - left["observed_epoch"]) / span
        lat = left["latitude"] + (right["latitude"] - left["latitude"]) * ratio
        lon = left["longitude"] + (right["longitude"] - left["longitude"]) * ratio
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"source_id": source_id, "callsign": callsign, "at": target_iso,
                               "estimated": span != 0, "method": "linear_wgs84", "gap_seconds": span,
                               "from_observation_id": left["id"], "to_observation_id": right["id"],
                               "bearing_deg": _bearing(left["latitude"], left["longitude"], right["latitude"], right["longitude"])}}
