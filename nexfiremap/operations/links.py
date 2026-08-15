"""Links from an incident to what the analytical side observed or produced.

This is the one edge between NexFiremap's two halves. Detections, event
clustering, propagation runs, ensembles, structures-at-risk and CAP warnings
answer *what is happening*; incidents, periods, scenarios, resources and safety
checks record *what we are doing about it*. Until this module existed the only
thing joining them was an operator retyping coordinates.

**Everything here crosses as a snapshot, never as a live reference**, and that
is the whole design rather than an implementation detail:

* events are re-clustered whenever the clustering job runs again, and their
  integer ids are not stable across runs;
* model runs are re-run with new weather, producing a different arrival surface
  under the same scenario;
* detections are purged on the retention window.

A plan line justified by "the 09:40 propagation run" has to keep meaning that
after the model is re-run at 11:00. So `snapshot_json` freezes what the operator
actually saw at link time - the event's bbox and detection count, the job id and
its generated-at, the alert's severity - and the referent id is kept alongside
purely so a *current* view can be offered as well, clearly labelled as a
re-read. This mirrors what `create_snapshot` does for whole incidents,
`incident_source_imports.original_blob` for uploads, and the derived-vs-reported
labelling throughout `telemetry.py`.

Re-linking is idempotent (`UNIQUE(incident_id, kind, ref_id)`): attaching the
same event twice refreshes the snapshot rather than accumulating rows, because
"attach to incident" is a button an operator will press twice.
"""

from __future__ import annotations

import json
from typing import Any

from ..db import Database
from .audit import AuditLog
from .base import AggregateStore
from .common import _clean_text, _id, utcnow
from .errors import NotFoundError, OperationsError
from .incidents import IncidentStore

#: What an incident can be linked to. A closed vocabulary for the same reason
#: `FEATURE_TYPES` is one: a package produced on one machine has to mean the
#: same thing when merged into another, and an open string field would let each
#: installation invent its own.
LINK_KINDS = {
    "event",            # a clustered fire event (events.id)
    "model_run",        # a propagation/ensemble job (jobs.id)
    "alert",            # a CAP public warning (alerts.identifier)
    "drone_asset",      # a georeferenced drone frame
    "import",           # a field-data import (incident_source_imports.id)
    "detection_window", # a bbox + time window of raw detections
    "structure",        # a structure at risk
}

#: Ceiling on one snapshot. Generous for the metadata these carry, but bounded:
#: a caller could otherwise park an entire GeoJSON FeatureCollection in here and
#: turn every incident export into a multi-megabyte document.
MAX_SNAPSHOT_BYTES = 256 * 1024


def _row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["snapshot"] = json.loads(item.pop("snapshot_json") or "{}")
    return item


class LinkStore(AggregateStore):
    """CRUD over `incident_links`, plus the incident's area of interest.

    AOI lives here rather than in `IncidentStore` because it exists for the
    same reason the links do - it is what lets the analytical side ask "does
    this new detection concern anyone?" - and because every consumer of one
    wants the other.
    """

    def __init__(self, db: Database, audit: AuditLog, incidents: IncidentStore) -> None:
        super().__init__(db, audit)
        self.incidents = incidents

    # -------------------------------------------------------------- links

    def add_link(self, incident_id: str, kind: str, ref_id: str,
                 snapshot: dict[str, Any], note: str = "",
                 actor: str = "local operator") -> dict[str, Any]:
        """Link an incident to something the analytical side produced.

        `snapshot` is required rather than optional: a link with no snapshot is
        a live reference, which is precisely what this module exists to avoid.
        An empty dict is refused for the same reason.
        """
        self.incidents.get_incident(incident_id)
        kind = _clean_text(kind, 40)
        if kind not in LINK_KINDS:
            raise OperationsError(f"link kind must be one of {', '.join(sorted(LINK_KINDS))}")
        reference = _clean_text(ref_id, 200)
        if not reference:
            raise OperationsError("ref_id is required")
        if not isinstance(snapshot, dict) or not snapshot:
            raise OperationsError(
                "a link requires a non-empty snapshot - links freeze what was observed, "
                "they are not live references")
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            raise OperationsError(f"link snapshot exceeds {MAX_SNAPSHOT_BYTES} bytes")

        now = utcnow()
        # `linked_at` is stamped into the snapshot itself, not just the row, so
        # a snapshot that travels into an export or a product still says when it
        # was taken without needing its row for context.
        payload = {**snapshot, "linked_at": now}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        existing = self.db.conn.execute(
            "SELECT id FROM incident_links WHERE incident_id=? AND kind=? AND ref_id=?",
            (incident_id, kind, reference)).fetchone()
        link_id = str(existing["id"]) if existing else _id()
        with self.db._write_lock:
            try:
                # Idempotent by design: "attach to incident" is a button, and a
                # second press should refresh what was captured rather than
                # leave two conflicting records of the same relationship.
                self.db.conn.execute(
                    "INSERT INTO incident_links (id,incident_id,kind,ref_id,snapshot_json,note,actor,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(incident_id,kind,ref_id) DO UPDATE SET "
                    "snapshot_json=excluded.snapshot_json,note=excluded.note,"
                    "actor=excluded.actor,created_at=excluded.created_at",
                    (link_id, incident_id, kind, reference, encoded,
                     _clean_text(note, 1000), _clean_text(actor, 200) or "local operator", now))
                self.audit.record(incident_id, "link", link_id,
                                  "update" if existing else "create", 1,
                                  {"kind": kind, "ref_id": reference, "snapshot": payload}, actor)
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return self.get_link(incident_id, link_id)

    def get_link(self, incident_id: str, link_id: str) -> dict[str, Any]:
        row = self.db.conn.execute(
            "SELECT * FROM incident_links WHERE id=? AND incident_id=?",
            (str(link_id), incident_id)).fetchone()
        if row is None:
            raise NotFoundError("link not found")
        return _row(row)

    def list_links(self, incident_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        self.incidents.get_incident(incident_id)
        if kind:
            rows = self.db.conn.execute(
                "SELECT * FROM incident_links WHERE incident_id=? AND kind=? ORDER BY created_at DESC",
                (incident_id, kind)).fetchall()
        else:
            rows = self.db.conn.execute(
                "SELECT * FROM incident_links WHERE incident_id=? ORDER BY created_at DESC",
                (incident_id,)).fetchall()
        return [_row(row) for row in rows]

    def remove_link(self, incident_id: str, link_id: str, actor: str = "local operator") -> bool:
        """Remove a link.

        A hard delete, unlike tactical features' soft delete: a link is a
        statement about *this* incident's own record rather than an entity other
        packages may reference by id, and the audit entry below preserves that
        it once existed. The snapshot is recorded in that audit payload, so
        removing a link never destroys the evidence of what was seen.
        """
        link = self.get_link(incident_id, link_id)
        with self.db._write_lock:
            try:
                cursor = self.db.conn.execute(
                    "DELETE FROM incident_links WHERE id=? AND incident_id=?", (link_id, incident_id))
                self.audit.record(incident_id, "link", link_id, "delete", 1, link, actor)
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return cursor.rowcount > 0

    # ---------------------------------------------------------------- AOI

    def set_aoi(self, incident_id: str, geometry: dict[str, Any] | None,
                actor: str = "local operator") -> dict[str, Any]:
        """Set or clear the incident's area of interest.

        Always stored as a Polygon, including when the operator dragged a
        rectangle: one geometry type downstream means the watch evaluation, the
        products and the frontend never branch on shape. `None` clears it.
        """
        self.incidents.get_incident(incident_id)
        encoded = None
        if geometry is not None:
            geometry = normalise_aoi(geometry)
            encoded = json.dumps(geometry, separators=(",", ":"))
        now = utcnow()
        with self.db._write_lock:
            try:
                self.db.conn.execute(
                    "UPDATE incidents SET aoi_json=?,updated_at=? WHERE id=?",
                    (encoded, now, incident_id))
                self.audit.record(incident_id, "incident", incident_id, "set_aoi", 1,
                                  {"aoi": geometry}, actor)
                self.db.conn.commit()
            except Exception:
                self.db.conn.rollback()
                raise
        return {"incident_id": incident_id, "aoi": geometry}

    def get_aoi(self, incident_id: str) -> dict[str, Any] | None:
        row = self.db.conn.execute(
            "SELECT aoi_json FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if row is None:
            raise NotFoundError("incident not found")
        return json.loads(row["aoi_json"]) if row["aoi_json"] else None

    def incidents_covering(self, lat: float, lon: float, *, active_only: bool = True
                           ) -> list[dict[str, Any]]:
        """Every incident whose AOI contains a point.

        The reverse direction, and what makes an AOI more than decoration: it
        answers "does this new detection concern anyone?" - see the watch
        evaluation. Incidents with no AOI are excluded rather than matched on
        their centre point, because "we did not draw an area" is not the same
        claim as "this point is inside our area".
        """
        clause = " AND status='active'" if active_only else ""
        results = []
        for row in self.db.conn.execute(
            f"SELECT id,name,status,aoi_json FROM incidents WHERE aoi_json IS NOT NULL{clause}"
        ).fetchall():
            try:
                geometry = json.loads(row["aoi_json"])
            except (TypeError, ValueError):
                continue
            if point_in_polygon(lon, lat, geometry):
                results.append({"id": row["id"], "name": row["name"], "status": row["status"]})
        return results


# ------------------------------------------------------------- geometry

def normalise_aoi(geometry: dict[str, Any]) -> dict[str, Any]:
    """Coerce an AOI to a closed GeoJSON Polygon.

    Accepts a Polygon or a ``[west, south, east, north]`` bbox, because the two
    ways an operator supplies one - drawing a shape or dragging a rectangle -
    naturally produce different things, and every consumer downstream should
    see only one.
    """
    if isinstance(geometry, (list, tuple)) and len(geometry) == 4:
        west, south, east, north = (float(value) for value in geometry)
        geometry = {"type": "Polygon", "coordinates": [[
            [west, south], [east, south], [east, north], [west, north], [west, south]]]}
    if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
        raise OperationsError("an AOI must be a GeoJSON Polygon or a [west,south,east,north] bbox")
    rings = geometry.get("coordinates")
    if not isinstance(rings, list) or not rings or not isinstance(rings[0], list) or len(rings[0]) < 3:
        raise OperationsError("an AOI polygon needs an outer ring of at least three points")
    ring = [[float(point[0]), float(point[1])] for point in rings[0]]
    for longitude, latitude in ring:
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise OperationsError("AOI coordinates are outside the valid range")
    if ring[0] != ring[-1]:
        ring.append(list(ring[0]))
    return {"type": "Polygon", "coordinates": [ring]}


def aoi_bbox(geometry: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    """The ``(west, south, east, north)`` extent of an AOI, for indexed queries."""
    if not geometry:
        return None
    ring = geometry.get("coordinates", [[]])[0]
    if not ring:
        return None
    longitudes = [float(point[0]) for point in ring]
    latitudes = [float(point[1]) for point in ring]
    return (min(longitudes), min(latitudes), max(longitudes), max(latitudes))


def point_in_polygon(lon: float, lat: float, geometry: dict[str, Any] | None) -> bool:
    """Ray-casting point-in-polygon over an AOI's outer ring.

    Hand-rolled rather than pulled from shapely for the same reason the WKB and
    shapefile readers are: this is a dozen lines against a dependency that would
    dwarf it, on a project that ships to field laptops.

    A bbox pre-filter comes first because this is called on the telemetry ingest
    hot path once per position per incident, and the overwhelming majority of
    those are nowhere near the polygon.
    """
    bounds = aoi_bbox(geometry)
    if bounds is None:
        return False
    west, south, east, north = bounds
    if not (west <= lon <= east and south <= lat <= north):
        return False

    ring = geometry["coordinates"][0]
    inside = False
    for index in range(len(ring) - 1):
        x1, y1 = float(ring[index][0]), float(ring[index][1])
        x2, y2 = float(ring[index + 1][0]), float(ring[index + 1][1])
        # The (y1 > lat) != (y2 > lat) test counts an edge only once at a shared
        # vertex, which is what keeps a point level with a vertex from being
        # double-counted and reported as outside.
        if (y1 > lat) != (y2 > lat):
            crossing = x1 + (lat - y1) / (y2 - y1) * (x2 - x1)
            if lon < crossing:
                inside = not inside
    return inside


__all__ = ["LINK_KINDS", "MAX_SNAPSHOT_BYTES", "LinkStore", "aoi_bbox",
           "normalise_aoi", "point_in_polygon"]
