"""Row/text/geometry helpers shared by every aggregate store.

None of these touch the database - they are pure functions over values -
which is exactly why they can be shared freely between the per-aggregate
stores without creating a dependency between the aggregates themselves.
Several of them (`_id`, `utcnow`, `_clean_text`, `_feature`,
`_validate_geometry`) are also imported directly by modules outside this
package (`merge.py`, `products.py`, `tactics.py`, `field_import.py`,
`security.py`, `drone.py`, `telemetry.py`) via `nexfiremap.operations`,
so the underscore prefix means "internal to the incident domain", not
"private to one module" - renaming one is a cross-module change.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .errors import OperationsError
from .vocab import LINE_TYPES, POINT_TYPES


def utcnow() -> str:
    """Current UTC time as an ISO 8601 string, second precision. Used for every
    created_at/updated_at/changed_at stamp so audit trails and revision
    history sort and compare consistently regardless of the host machine's
    local timezone."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id() -> str:
    """New random identifier for a record. UUIDs (rather than autoincrement
    ids) let two disconnected installations generate records independently
    without colliding when their packages are later merged."""
    return str(uuid4())


def _clean_text(value: Any, limit: int = 10000) -> str:
    """Coerce to a trimmed string and cap its length. Applied to every
    user-supplied text field so free-text notes can't blow past sane storage
    limits or carry stray whitespace into equality/audit comparisons."""
    return str(value or "").strip()[:limit]


def _json_load(value: str | None, default: Any) -> Any:
    """Best-effort JSON decode that falls back to `default` instead of
    raising, since stored JSON blobs (geometry/properties/payload columns)
    should never be allowed to take down a read path."""
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _plain(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite3.Row to a plain dict, or pass through None."""
    return dict(row) if row is not None else None


def _feature(row: sqlite3.Row) -> dict[str, Any]:
    """Reassemble a tactical_features row into a GeoJSON Feature. Geometry and
    free-form properties are stored as JSON columns for flexibility, but
    everything else on the row (status, revision, timestamps, ...) is also
    folded into `properties` so API consumers only ever deal with one
    GeoJSON Feature shape rather than a database row shape."""
    data = dict(row)
    geometry = _json_load(data.pop("geometry_json", None), None)
    properties = _json_load(data.pop("properties_json", None), {})
    return {"type": "Feature", "id": data["id"], "geometry": geometry,
            "properties": {**properties, **data}}


def _validate_geometry(geometry: Any, feature_type: str) -> dict[str, Any]:
    """Validate that `geometry` is a well-formed GeoJSON geometry of the kind
    required by `feature_type` (point features need Point geometry, etc.),
    and that every coordinate is a plausible lon/lat(/altitude) position.
    Raises OperationsError with a human-readable reason on the first problem
    found; this is the single gate features pass through on create/update so
    bad geometry can never reach the map or a shared package."""
    if not isinstance(geometry, dict):
        raise OperationsError("geometry must be a GeoJSON geometry object")
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    expected = "Point" if feature_type in POINT_TYPES else (
        "LineString" if feature_type in LINE_TYPES else "Polygon"
    )
    if kind != expected:
        raise OperationsError(f"{feature_type} requires {expected} geometry")
    minimum = 2 if kind in {"Point", "LineString"} else 1
    if not isinstance(coordinates, list) or len(coordinates) < minimum:
        raise OperationsError(f"invalid {kind} coordinates")
    if kind == "Polygon":
        # GeoJSON polygon rings must be closed (first position == last) and
        # need at least 4 positions to describe a non-degenerate ring.
        if any(not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1] for ring in coordinates):
            raise OperationsError("polygon rings require four positions and must be closed")
        positions = [p for ring in coordinates for p in ring]
    else:
        positions = [coordinates] if kind == "Point" else coordinates
    for position in positions:
        if not isinstance(position, list) or len(position) not in {2, 3}:
            raise OperationsError("each GeoJSON position must contain longitude, latitude, and optional altitude")
        if any(not isinstance(value, (int, float)) for value in position):
            raise OperationsError("geometry coordinates must be numeric")
        if not -180 <= float(position[0]) <= 180 or not -90 <= float(position[1]) <= 90:
            raise OperationsError("geometry longitude/latitude is outside the valid range")
    return {"type": kind, "coordinates": coordinates}
