"""Polling and storage for official CAP public warnings.

`ingest/cap.py` turns bytes into alert records; this module is the stateful
half - it decides *when* to fetch, keeps what it got, and prunes what has
expired. It follows `CacheManager`'s shape deliberately: a background loop
started from `lifespan()`, its own `httpx.AsyncClient` closed on `stop()`, and
a failure policy of "log it and try again next cycle" rather than raising.

That failure policy is the point rather than laziness. NexFiremap is built to
keep working with the WAN down during an incident, so a CAP feed being
unreachable is an expected operating state, not an error. The already-stored
warnings stay on the map and stay queryable; only their freshness ages. The
one thing that must never happen is a warning feed being unavailable taking
down the fire map with it.

Two publishing styles are handled, because publishers differ and an operator
should not have to know which they configured: alerts inline in an Atom/RSS
feed, and an index of links to individual CAP documents (the DWD's style),
which `_poll_feed` follows.

Expiry is enforced on read *and* on the prune pass. That redundancy is
intentional: the prune runs on a timer, so between passes there is a window
where an expired warning is still in the table, and an expired evacuation
notice still drawn on a situation map is worse than no warning at all.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from . import netguard
from .config import Settings
from .db import Database
from .ingest import IngestError
from .ingest import cap

log = logging.getLogger("nexfiremap.alerts")

#: How many linked CAP documents one index feed may pull per cycle. A national
#: feed during severe weather can list hundreds; fetching all of them every
#: cycle would be both slow and rude to a public authority's server.
MAX_LINKED_DOCUMENTS = 60

#: Cap on stored alerts, enforced oldest-first. A field laptop's disk is the
#: constraint being respected here, the same reasoning as the tile cache's
#: size budget and the job queue's depth limit.
MAX_STORED_ALERTS = 5_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _epoch(value: str | None) -> float | None:
    """ISO timestamp to epoch seconds, or ``None`` if absent/unparseable.

    Unparseable is treated as absent rather than as an error: a publisher with
    a malformed ``<expires>`` should leave the warning showing (no expiry
    known) rather than have the whole poll fail.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _bounds(geometry: dict[str, Any] | None) -> tuple[float | None, ...]:
    """Bounding box of an alert geometry, for the indexed viewport query.

    Walks the coordinate tree generically rather than switching on geometry
    type, so Polygon and MultiPolygon (and a degenerate Point circle) are all
    handled by the same code - the same approach `products._all_points` takes.
    """
    if not geometry:
        return (None, None, None, None)

    def walk(value: Any) -> list[tuple[float, float]]:
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(v, (int, float)) for v in value[:2]):
            return [(float(value[0]), float(value[1]))]
        if not isinstance(value, list):
            return []
        return [point for child in value for point in walk(child)]

    points = walk(geometry.get("coordinates", []))
    if not points:
        return (None, None, None, None)
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _row(row: Any) -> dict[str, Any]:
    """Storage row to API dict.

    ``raw_xml`` is dropped: it is kept for provenance and audit, but a viewport
    query returning several hundred alerts should not carry a few hundred
    kilobytes of source XML to the browser. Same reasoning as
    `field_import.list_imports` excluding `original_blob`.
    """
    item = dict(row)
    item.pop("raw_xml", None)
    item["geometry"] = json.loads(item.pop("geometry_json") or "null")
    item["properties"] = json.loads(item.pop("properties_json") or "{}")
    return item


class AlertManager:
    """Polls configured CAP feeds, stores what they publish, prunes what expires."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.feeds = list(settings.cap_feeds)
        self._client: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        # Surfaced through /api/status so an operator can tell "no warnings in
        # this area" apart from "the feed has been unreachable for an hour" -
        # a distinction that matters a great deal during an incident.
        self.last_poll_at: str | None = None
        self.last_error: str | None = None
        self.polls = 0
        self.stored = 0

    @property
    def enabled(self) -> bool:
        return bool(self.feeds)

    async def start(self) -> None:
        """Begin polling, if any feeds are configured.

        With none configured this is a no-op and no client or task is created -
        an install that never wanted CAP pays nothing for it, and
        `/api/config`'s feature flag reports it as unavailable.
        """
        if not self.enabled:
            log.info("No CAP alert feeds configured; alert polling disabled")
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.request_timeout_s),
            headers={"User-Agent": self.settings.tile_user_agent},
            follow_redirects=True,
            # Untrusted: `_poll_feed` follows links taken out of the feed
            # *document*, so a spoofed or compromised upstream can name any
            # address it likes and have this server connect to it blind. The
            # hook fires per request, redirect hops included - a public URL
            # answering 302 to an internal one is the obvious way past a check
            # done only on the URL an operator configured.
            event_hooks={"request": [netguard.request_guard(trusted=False)]},
        )
        self._task = asyncio.create_task(self._loop(), name="cap-poll")
        log.info("CAP alert polling started for %d feed(s)", len(self.feeds))

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._client:
            await self._client.aclose()

    # -------------------------------------------------------------- loop

    async def _loop(self) -> None:
        """Poll every feed on the configured interval, forever.

        The first poll happens immediately rather than after one interval, so a
        server started at the beginning of an incident has official warnings on
        the map straight away instead of up to fifteen minutes later.
        """
        interval = max(60, self.settings.cap_poll_minutes * 60)
        while not self._stopping:
            try:
                await self.poll_now()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                # The loop must outlive any single failure; a crashed poll
                # task would silently stop all warning updates for the rest of
                # the server's life.
                log.exception("CAP alert poll cycle failed")
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

    async def poll_now(self) -> dict[str, Any]:
        """One pass over every configured feed. Also the manual-refresh path."""
        if not self._client:
            return {"polled": 0, "stored": 0, "feeds": []}
        results = []
        total = 0
        for url in self.feeds:
            stored, error = await self._poll_feed(url)
            total += stored
            results.append({"feed": url, "stored": stored, "error": error})
        self.polls += 1
        self.last_poll_at = _now()
        self.last_error = next((r["error"] for r in results if r["error"]), None)
        removed = await asyncio.to_thread(self.prune_now)
        return {"polled": len(self.feeds), "stored": total, "pruned": removed, "feeds": results}

    async def _poll_feed(self, url: str) -> tuple[int, str | None]:
        """Fetch one feed and store whatever it yields.

        Handles both publishing styles: if the document parses as CAP directly
        the alerts were inline, otherwise it is treated as an index and its
        links are followed. Trying inline first (rather than sniffing the
        content type) is deliberate - publishers label CAP as
        ``application/xml``, ``text/xml`` and ``application/cap+xml``
        interchangeably, so the document's own shape is the more reliable
        signal.
        """
        assert self._client is not None
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            # The expected state with the WAN down. Logged at warning, not
            # error, and explicitly not raised.
            log.warning("CAP feed unreachable (%s): %s", url, exc)
            return 0, f"unreachable: {exc}"
        if response.status_code != 200:
            log.warning("CAP feed %s returned HTTP %d", url, response.status_code)
            return 0, f"HTTP {response.status_code}"

        raw = response.content
        try:
            records = cap.parse(raw)
            return await asyncio.to_thread(self._store, records, url, raw.decode("utf-8", "replace")), None
        except IngestError:
            pass  # not an inline CAP document - fall through to the index case

        links = [link for link in cap.feed_links(raw) if link != url][:MAX_LINKED_DOCUMENTS]
        if not links:
            log.warning("CAP feed %s is neither a CAP document nor a link index", url)
            return 0, "unrecognised feed format"

        stored = 0
        for link in links:
            try:
                document = await self._client.get(link)
                if document.status_code != 200:
                    continue
                records = cap.parse(document.content)
            except (httpx.HTTPError, IngestError) as exc:
                # One bad document must not abandon the rest of the index.
                log.debug("CAP document %s skipped: %s", link, exc)
                continue
            stored += await asyncio.to_thread(
                self._store, records, url, document.content.decode("utf-8", "replace"))
        return stored, None

    # ------------------------------------------------------------ writes

    def _store(self, records: list[dict[str, Any]], feed_id: str, raw_xml: str) -> int:
        """Upsert alert records, keyed on the publisher's own identifier.

        ``INSERT ... ON CONFLICT DO UPDATE`` rather than delete-then-insert so
        ``received_at`` survives an update - when a warning was *first* seen is
        part of the incident record and must not be rewritten every time the
        publisher reissues it.
        """
        if not records:
            return 0
        now = _now()
        with self.db._write_lock:
            try:
                for record in records:
                    west, south, east, north = _bounds(record.get("geometry"))
                    properties = {key: value for key, value in record.items() if key not in {
                        "identifier", "sender", "sent", "event", "headline", "description",
                        "severity", "urgency", "certainty", "effective", "expires", "geometry"}}
                    self.db.conn.execute(
                        "INSERT INTO alerts (identifier,feed_id,sender,sent,event,headline,description,"
                        "severity,severity_rank,urgency,certainty,effective,expires,geometry_json,"
                        "properties_json,raw_xml,west,south,east,north,received_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(identifier) DO UPDATE SET "
                        "feed_id=excluded.feed_id,sender=excluded.sender,sent=excluded.sent,"
                        "event=excluded.event,headline=excluded.headline,description=excluded.description,"
                        "severity=excluded.severity,severity_rank=excluded.severity_rank,"
                        "urgency=excluded.urgency,certainty=excluded.certainty,"
                        "effective=excluded.effective,expires=excluded.expires,"
                        "geometry_json=excluded.geometry_json,properties_json=excluded.properties_json,"
                        "raw_xml=excluded.raw_xml,west=excluded.west,south=excluded.south,"
                        "east=excluded.east,north=excluded.north,updated_at=excluded.updated_at",
                        (record["identifier"], feed_id, record["sender"], record["sent"], record["event"],
                         record["headline"], record["description"], record["severity"],
                         int(properties.get("severity_rank", len(cap.SEVERITIES))), record["urgency"],
                         record["certainty"], record["effective"], record["expires"],
                         json.dumps(record["geometry"], separators=(",", ":")) if record["geometry"] else None,
                         json.dumps(properties, sort_keys=True, separators=(",", ":")),
                         raw_xml[:200_000], west, south, east, north, now, now),
                    )
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        self.stored += len(records)
        return len(records)

    def prune_now(self) -> int:
        """Drop expired alerts, then trim to `MAX_STORED_ALERTS`.

        An alert with no ``expires`` is kept indefinitely: CAP treats a missing
        expiry as "until cancelled", and guessing a lifetime for an official
        warning would be inventing information the publisher declined to give.
        Those rows are still bounded by the size trim below.
        """
        now = _now()
        with self.db._write_lock:
            try:
                cursor = self.db.conn.execute(
                    "DELETE FROM alerts WHERE expires IS NOT NULL AND expires < ?", (now,))
                removed = cursor.rowcount
                overflow = self.db.conn.execute(
                    "SELECT COUNT(*) FROM alerts").fetchone()[0] - MAX_STORED_ALERTS
                if overflow > 0:
                    self.db.conn.execute(
                        "DELETE FROM alerts WHERE identifier IN "
                        "(SELECT identifier FROM alerts ORDER BY received_at LIMIT ?)", (overflow,))
                    removed += overflow
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        if removed:
            log.info("Pruned %d expired/overflow alert(s)", removed)
        return removed

    # ------------------------------------------------------------- reads

    def query(self, bbox: tuple[float, float, float, float] | None = None, *,
              severity: str | None = None, include_expired: bool = False) -> dict[str, Any]:
        """Alerts as a FeatureCollection for the map overlay.

        Expiry is re-checked here rather than trusted to the prune timer - see
        the module docstring on why an expired warning must never render.

        Alerts with no geometry (geocode-only, e.g. a WARNCELLID with no
        polygon) are returned in a separate ``without_geometry`` list rather
        than dropped: they are real warnings that simply cannot be drawn, and
        silently omitting them would under-report the situation.
        """
        clauses, params = [], []
        if not include_expired:
            clauses.append("(expires IS NULL OR expires >= ?)")
            params.append(_now())
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if bbox:
            west, south, east, north = bbox
            # Standard box-overlap test, not containment: an alert covering a
            # whole federal state must still show when the viewport is a
            # single valley inside it.
            clauses.append("(west IS NOT NULL AND west <= ? AND east >= ? AND south <= ? AND north >= ?)")
            params.extend([east, west, north, south])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = [_row(row) for row in self.db.conn.execute(
            f"SELECT * FROM alerts{where} ORDER BY severity_rank, sent DESC", params).fetchall()]

        features, plain = [], []
        for item in rows:
            geometry = item.pop("geometry")
            properties = {**item.pop("properties"), **item}
            if geometry is None:
                plain.append(properties)
                continue
            features.append({"type": "Feature", "id": item["identifier"],
                             "geometry": geometry, "properties": properties})
        return {"type": "FeatureCollection", "features": features,
                "without_geometry": plain, "severities": list(cap.SEVERITIES),
                "last_poll_at": self.last_poll_at, "last_error": self.last_error,
                "feeds": list(self.feeds)}

    def original(self, identifier: str) -> str | None:
        """The alert's source XML exactly as published, for audit."""
        row = self.db.conn.execute(
            "SELECT raw_xml FROM alerts WHERE identifier=?", (identifier,)).fetchone()
        return None if row is None else str(row["raw_xml"])

    def status(self) -> dict[str, Any]:
        """Poll health for /api/status - lets an operator distinguish "no
        warnings here" from "we have not heard from the feed in an hour"."""
        total = self.db.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        return {"enabled": self.enabled, "feeds": list(self.feeds), "stored": total,
                "polls": self.polls, "last_poll_at": self.last_poll_at, "last_error": self.last_error}


__all__ = ["MAX_LINKED_DOCUMENTS", "MAX_STORED_ALERTS", "AlertManager"]
