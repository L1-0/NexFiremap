"""Persistent thermal-source classifier - further_plan.md section 13:
separating recurring industrial heat (flares, kilns, refineries, power
plants, ...) from genuine wildfires, without ever deleting, hiding, or
mutating the underlying detections.

The doc's central rule: **"OSM creates a candidate, recurrence confirms
it, stationarity strengthens it, propagation or burn evidence overrides
it."** This module implements exactly that pipeline, using only free,
keyless data and a transparent, non-learned scoring rule - explicitly the
doc's own first recommended step ("1. Transparent rule-based score",
section 13.7), not the calibrated-classifier upgrade path it lists after
that, which needs a labelled historical archive this project doesn't have.

**Pipeline:**

1. **Candidates** ([[orbits]]-style free/keyless source): query OpenStreetMap's
   Overpass API for the doc's "strong"/"moderate" evidence tags (flares,
   kilns, refineries, power plants, works) within an area - cached in
   ``industrial_sources``/``industrial_query_cache`` so this stays a
   considerate, cache-first client of a shared public service.
2. **Recurrence/persistence**: for each candidate, look at this project's
   own cached FIRMS history nearby (``persistence_features``) - how many
   distinct days had a detection, how tight the cluster is (spatial
   stationarity), how consistent the FRP signature is (a rough radiometric
   fingerprint).
3. **Score**: a fixed-weight linear combination of that evidence
   (``score_source``) - deliberately simple and inspectable, not fit to
   any data, so its behaviour is exactly what's written below, not a black
   box.
4. **Match & override**: detections near a high-scoring candidate are
   reported as likely industrial - *unless* the event they belong to shows
   growth beyond the candidate's own footprint (``apply_wildfire_override``),
   the doc's "wildfire overrides industrial" safeguard.

**A real, honest limitation this project can't avoid**: the doc recommends
"multi-year, sensor-specific recurrence maps" (section 13, recommended
order item 3). NexFiremap only ever caches up to
``NEXFIREMAP_CACHE_DAYS`` (default 30) days of FIRMS history by design -
this was the original project spec, not an oversight. So "persistence"
here can only ever reflect *this server's own recent cached window*, not
years of behaviour. A source that looks industrial-consistent over 30 days
is real (if weaker) evidence. A source that *doesn't* is inconclusive, not
disproven. Nothing here is presented as more certain than that.

**Never deletes anything** (section 13.9): classification results are
returned by the scan job and (client-side) toggle the map's styling of
matching detections - the ``detections`` table itself is never touched.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time as time_module
from typing import Any

import httpx

from .geo import LAT_KM_PER_DEG, haversine_km, lon_km_per_deg
from .jobs import JobContext, register_kind

log = logging.getLogger("nexfiremap.industrial")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
QUERY_CACHE_MAX_AGE_S = 30 * 86400  # matches this project's own detection retention

# further_plan.md section 13.3's evidence tables. Only "strong" and
# "moderate" tags are queried - the doc's own "weak/misleading" list
# (landuse=industrial, warehouses, rail yards, ports, substations, ...) is
# deliberately excluded: those areas often contain no real heat source at
# all, and the doc explicitly warns against suppressing on them.
STRONG_TAGS: list[tuple[str, str]] = [
    ("man_made", "flare"),
    ("man_made", "kiln"),
    ("industrial", "refinery"),
    ("power", "plant"),
    ("man_made", "petroleum_well"),
]
MODERATE_TAGS: list[tuple[str, str]] = [
    ("man_made", "works"),
    ("man_made", "chimney"),
    ("power", "generator"),
]

# Rule-based score weights (further_plan.md section 13.7's O/P/S/R terms -
# growth (G) and burn evidence (B) are applied separately, per event, in
# apply_wildfire_override rather than folded into a static candidate
# score - see that function's docstring). Deliberately transparent fixed
# weights, not fit to data this project doesn't have - see the module
# docstring's note on the doc's own recommended "start with a rule, not a
# classifier" first step.
WEIGHT_OSM_EVIDENCE = 0.30
WEIGHT_PERSISTENCE = 0.30
WEIGHT_STATIONARITY = 0.25
WEIGHT_RADIOMETRIC = 0.15

# Chosen to favour precision over recall per the doc's own guidance
# (section 13.7): "suppression should require substantially stronger
# evidence than merely displaying a possible detection", because a false
# industrial classification hides a real fire.
THRESHOLD_PERSISTENT = 0.75
THRESHOLD_AMBIGUOUS = 0.45


def _snap_bbox(
    bbox: tuple[float, float, float, float], grid_deg: float = 1.0
) -> tuple[float, float, float, float]:
    """Round a bbox outward to a coarse grid so nearby, slightly different
    query boxes (e.g. two events a few km apart) share one cached Overpass
    query instead of each firing their own."""
    west, south, east, north = bbox
    return (
        math.floor(west / grid_deg) * grid_deg,
        math.floor(south / grid_deg) * grid_deg,
        math.ceil(east / grid_deg) * grid_deg,
        math.ceil(north / grid_deg) * grid_deg,
    )


# ------------------------------------------------------------------- OSM


def build_overpass_query(bbox: tuple[float, float, float, float]) -> str:
    west, south, east, north = bbox
    bbox_clause = f"({south},{west},{north},{east})"
    lines = ["[out:json][timeout:45];", "("]
    for key, value in STRONG_TAGS + MODERATE_TAGS:
        lines.append(f'  node["{key}"="{value}"]{bbox_clause};')
        lines.append(f'  way["{key}"="{value}"]{bbox_clause};')
    lines.append(");")
    lines.append("out center;")
    return "\n".join(lines)



# power=generator is only meaningful thermal-source evidence when the
# generator actually burns something. Non-combustion generation methods
# (solar, wind, hydro) are extremely common in OSM (rooftop solar panels
# especially) and match the tag but produce no heat signature at all - the
# doc explicitly calls out solar as belonging in a separate reflection-risk
# concern, not the persistent-heat candidate list (section 13.3). Caught by
# live-testing against a real, dense OSM area (Rotterdam) where solar
# rooftop installations were the majority of "moderate evidence" matches.
_NON_COMBUSTION_GENERATOR_SOURCES = {"solar", "wind", "hydro", "geothermal", "tidal", "wave"}
_NON_COMBUSTION_GENERATOR_METHODS = {"photovoltaic", "wind_turbine", "water-storage", "water-run-of-river"}


def _is_combustion_generator(tags: dict[str, str]) -> bool:
    source = tags.get("generator:source")
    method = tags.get("generator:method")
    if source is not None and source in _NON_COMBUSTION_GENERATOR_SOURCES:
        return False
    if method is not None and method in _NON_COMBUSTION_GENERATOR_METHODS:
        return False
    return True


def _evidence_class(tags: dict[str, str]) -> str | None:
    for key, value in STRONG_TAGS:
        if tags.get(key) == value:
            return "strong"
    for key, value in MODERATE_TAGS:
        if tags.get(key) == value:
            if key == "power" and value == "generator" and not _is_combustion_generator(tags):
                continue
            return "moderate"
    return None


def fetch_osm_candidates_sync(
    client: httpx.Client, bbox: tuple[float, float, float, float]
) -> list[dict[str, Any]]:
    """One Overpass query for the doc's strong/moderate evidence tags in
    ``bbox``. Free, keyless. Returns
    ``[{"osm_type","osm_id","lat","lon","evidence_class","tags"}, ...]``."""
    query = build_overpass_query(bbox)
    # Overpass's own [timeout:45] above is its server-side execution budget -
    # the HTTP timeout here needs real headroom past that for the response
    # to actually arrive, not just be computed - the public instance can be
    # slow under load, and this runs as a background job precisely so a
    # slow free shared service doesn't block the API.
    response = client.post(OVERPASS_URL, data={"data": query}, timeout=60.0)
    response.raise_for_status()
    payload = response.json()

    candidates: list[dict[str, Any]] = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        evidence_class = _evidence_class(tags)
        if evidence_class is None:
            continue
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        candidates.append(
            {
                "osm_type": el["type"],
                "osm_id": el["id"],
                "lat": lat,
                "lon": lon,
                "evidence_class": evidence_class,
                "tags": tags,
            }
        )
    return candidates


def query_cache_is_fresh(
    conn: sqlite3.Connection,
    bbox: tuple[float, float, float, float],
    max_age_s: float = QUERY_CACHE_MAX_AGE_S,
) -> bool:
    """Read-only freshness check, split out of ``ensure_osm_candidates`` so
    the API layer can decide whether a scan is worth *submitting as a
    background job* without ever doing the live Overpass fetch itself on
    the request path."""
    snapped = _snap_bbox(bbox)
    row = conn.execute(
        "SELECT fetched_at FROM industrial_query_cache WHERE west=? AND south=? AND east=? AND north=?",
        snapped,
    ).fetchone()
    return row is not None and (time_module.time() - row[0]) <= max_age_s


def ensure_osm_candidates(
    conn: sqlite3.Connection,
    bbox: tuple[float, float, float, float],
    max_age_s: float = QUERY_CACHE_MAX_AGE_S,
) -> list[dict[str, Any]]:
    """Candidates in ``bbox``, fetching from Overpass only if this area
    hasn't been queried recently. Always reads back from the local table
    (not just whatever this call itself fetched), so a cache hit from an
    earlier, differently-shaped query still surfaces correctly."""
    snapped = _snap_bbox(bbox)
    is_fresh = query_cache_is_fresh(conn, bbox, max_age_s)
    now = time_module.time()

    if not is_fresh:
        with httpx.Client(headers={"User-Agent": "NexFiremap/1.0 (industrial-source scan)"}) as client:
            fetched = fetch_osm_candidates_sync(client, snapped)
        conn.execute("BEGIN")
        conn.executemany(
            "INSERT INTO industrial_sources (osm_type, osm_id, latitude, longitude, "
            "evidence_class, tags_json, fetched_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (osm_type, osm_id) DO UPDATE SET "
            "latitude=excluded.latitude, longitude=excluded.longitude, "
            "evidence_class=excluded.evidence_class, tags_json=excluded.tags_json, "
            "fetched_at=excluded.fetched_at",
            [
                (c["osm_type"], c["osm_id"], c["lat"], c["lon"], c["evidence_class"], json.dumps(c["tags"]), int(now))
                for c in fetched
            ],
        )
        conn.execute(
            "INSERT INTO industrial_query_cache (west, south, east, north, fetched_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT (west, south, east, north) DO UPDATE SET fetched_at=excluded.fetched_at",
            (*snapped, int(now)),
        )
        conn.commit()

    west, south, east, north = bbox
    rows = conn.execute(
        "SELECT id, osm_type, osm_id, latitude, longitude, evidence_class, tags_json "
        "FROM industrial_sources WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?",
        (south, north, west, east),
    ).fetchall()
    return [
        {
            "id": r[0], "osm_type": r[1], "osm_id": r[2], "lat": r[3], "lon": r[4],
            "evidence_class": r[5], "tags": json.loads(r[6]),
        }
        for r in rows
    ]


# ------------------------------------------------------------- persistence


def _observed_days(
    conn: sqlite3.Connection, lat: float, lon: float, start_ts: float, end_ts: float
) -> int | None:
    """How many distinct UTC days within [start_ts, end_ts] had at least one
    valid satellite pass over this point - the correct denominator for
    persistence (further_plan.md section 13.4: a proper occupancy fraction
    "prevents cloudier locations from appearing less persistent simply
    because they were observed less often"), not raw calendar days.

    Reuses ``orbits.aoi_clear_passes`` with an empty detection list rather
    than adding a second orbit-propagation code path: with nothing to treat
    as coincident, it returns *every* geometrically-in-swath pass over this
    point, not just the "no fire seen" ones - exactly "was this even
    observed", independent of what (if anything) was seen.

    Returns ``None`` (not 0) when the optional ``orbits`` feature isn't
    available, so the caller can fall back to the simpler calendar-day
    denominator instead of treating "no orbit data" as "never observed".
    """
    try:
        from .orbits import aoi_clear_passes
    except ImportError:
        return None
    try:
        pad_deg = 0.01  # a token AOI - orbits treats the whole thing as one point anyway
        bbox = (lon - pad_deg, lat - pad_deg, lon + pad_deg, lat + pad_deg)
        passes = aoi_clear_passes(conn, bbox, [], start_ts, end_ts)
    except Exception:  # noqa: BLE001 - orbit propagation, degrade rather than fail scoring
        log.warning("Observation-opportunity lookup failed for (%s, %s)", lat, lon, exc_info=True)
        return None
    days = {time_module.gmtime(p["ts"])[:3] for p in passes}
    return len(days)


def persistence_features(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    radius_km: float,
    end_ts: float,
    window_days: int,
) -> dict[str, Any]:
    """Recurrence/stationarity/radiometric features from this project's own
    cached FIRMS history near ``(lat, lon)`` - see the module docstring for
    why this is a "within this server's cache window" measure, not the
    doc's multi-year one."""
    start_ts = end_ts - window_days * 86400
    lat_pad = radius_km / LAT_KM_PER_DEG
    lon_pad = radius_km / lon_km_per_deg(lat)
    rows = conn.execute(
        "SELECT latitude, longitude, acq_ts, acq_date, frp, daynight, instrument FROM detections "
        "WHERE acq_ts BETWEEN ? AND ? AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?",
        (start_ts, end_ts, lat - lat_pad, lat + lat_pad, lon - lon_pad, lon + lon_pad),
    ).fetchall()

    observed_days = _observed_days(conn, lat, lon, start_ts, end_ts)
    nearby = [r for r in rows if haversine_km(lat, lon, r[0], r[1]) <= radius_km]
    if not nearby:
        return {
            "detection_count": 0, "distinct_days": 0, "observed_days": observed_days,
            "span_days": 0.0, "first_ts": None, "last_ts": None,
            "centroid_lat": lat, "centroid_lon": lon,
            "spread_km": None, "frp_median": None, "frp_iqr": None, "day_night_ratio": None,
        }

    lats = [r[0] for r in nearby]
    lons = [r[1] for r in nearby]
    tss = [r[2] for r in nearby]
    days = {r[3] for r in nearby}
    frps = sorted(r[4] for r in nearby if r[4] is not None)
    day_count = sum(1 for r in nearby if r[5] == "D")
    night_count = sum(1 for r in nearby if r[5] == "N")

    centroid_lat = sum(lats) / len(lats)
    centroid_lon = sum(lons) / len(lons)
    spread_km = _stationarity_spread_km(nearby, centroid_lat, centroid_lon)

    frp_median = frps[len(frps) // 2] if frps else None
    if len(frps) >= 4:
        q1 = frps[len(frps) // 4]
        q3 = frps[(3 * len(frps)) // 4]
        frp_iqr = q3 - q1
    else:
        frp_iqr = None

    return {
        "detection_count": len(nearby),
        "distinct_days": len(days),
        "observed_days": observed_days,
        "span_days": (max(tss) - min(tss)) / 86400.0,
        "first_ts": min(tss),
        "last_ts": max(tss),
        "centroid_lat": centroid_lat,
        "centroid_lon": centroid_lon,
        "spread_km": spread_km,
        "frp_median": frp_median,
        "frp_iqr": frp_iqr,
        "day_night_ratio": (day_count / night_count) if night_count else None,
    }


def _stationarity_spread_km(
    nearby: list[tuple[Any, ...]], pooled_centroid_lat: float, pooled_centroid_lon: float
) -> float:
    """Mean distance from centroid, computed *within each instrument* and
    combined via the tightest group - not one flat spread pooling every
    sensor's points around one shared centroid.

    further_plan.md section 13.4 warns not to compare raw MODIS (~1km
    pixels) and VIIRS (~375m) coordinate spread directly: pooling them
    computes distances against a centroid dragged around by whichever
    sensor has coarser, noisier geolocation, inflating the reported spread
    for a source that one sensor alone would show as tightly stationary. If
    only one instrument is present this reduces to the plain pooled
    computation exactly (same numeric result as before this fix).
    """
    by_instrument: dict[Any, list[tuple[float, float]]] = {}
    for r in nearby:
        instrument = r[6] if len(r) > 6 else None
        by_instrument.setdefault(instrument, []).append((r[0], r[1]))

    if len(by_instrument) <= 1:
        points = next(iter(by_instrument.values()))
        return sum(
            haversine_km(pooled_centroid_lat, pooled_centroid_lon, la, lo) for la, lo in points
        ) / len(points)

    spreads = []
    for points in by_instrument.values():
        if not points:
            continue
        c_lat = sum(p[0] for p in points) / len(points)
        c_lon = sum(p[1] for p in points) / len(points)
        spreads.append(
            sum(haversine_km(c_lat, c_lon, la, lo) for la, lo in points) / len(points)
        )
    return min(spreads)


def score_source(
    persistence: dict[str, Any], evidence_class: str, window_days: int
) -> dict[str, Any]:
    """further_plan.md section 13.7's transparent rule-based score, folded
    down to what's actually computable from this project's own data (see
    module docstring). Returns a score in [0, 1] plus the classification
    bucket and a plain-language reason - never a bare number with no
    explanation, since a wrong industrial classification hides a real
    fire."""
    o = 1.0 if evidence_class == "strong" else 0.5

    # Persistence as a fraction of actual observation *opportunity*
    # (further_plan.md section 13.4), not raw calendar days, when the
    # optional orbits feature can supply it - a location a satellite only
    # genuinely passed over 20 days out of a 30-day window shouldn't be
    # scored as if 30 days were all available to see a detection on.
    # max(distinct_days, ...) guards against the orbit approximation
    # under-counting opportunity below what real detections already prove
    # happened.
    observed_days = persistence.get("observed_days")
    denominator = (
        max(persistence["distinct_days"], observed_days, 1)
        if observed_days is not None
        else max(1, window_days)
    )
    p = min(1.0, persistence["distinct_days"] / denominator)

    spread_km = persistence["spread_km"]
    s = max(0.0, 1.0 - spread_km / 2.0) if spread_km is not None else 0.0

    frp_median, frp_iqr = persistence["frp_median"], persistence["frp_iqr"]
    if frp_median and frp_iqr is not None:
        r = max(0.0, min(1.0, 1.0 - frp_iqr / (frp_median + 1e-6)))
    else:
        r = 0.5  # not enough FRP samples to say either way - neutral, not penalised

    score = (
        WEIGHT_OSM_EVIDENCE * o
        + WEIGHT_PERSISTENCE * p
        + WEIGHT_STATIONARITY * s
        + WEIGHT_RADIOMETRIC * r
    )
    score = round(min(1.0, max(0.0, score)), 4)

    if persistence["detection_count"] == 0:
        classification = "insufficient_evidence"
        reason = "OSM candidate with no cached detections nearby in this server's history."
    elif score >= THRESHOLD_PERSISTENT:
        classification = "persistent_industrial"
        spread_text = f"{spread_km:.2f}km" if spread_km is not None else "an untracked area"
        reason = (
            f"{evidence_class} OSM evidence + {persistence['distinct_days']}/{denominator} "
            f"observed day(s) with detections, compact within {spread_text}."
        )
    elif score >= THRESHOLD_AMBIGUOUS:
        classification = "ambiguous"
        reason = "Some industrial evidence, but not strong/consistent enough for high-confidence exclusion."
    else:
        classification = "insufficient_evidence"
        reason = "OSM candidate present, but recurrence/stationarity too weak to call it a persistent source."

    return {
        "score": score, "classification": classification, "reason": reason,
        "o": o, "p": p, "s": s, "r": r,
    }


# --------------------------------------------------------------- matching


def match_radius_km(persistence: dict[str, Any]) -> float:
    """Doc section 13.5's provisional matching region, simplified: a fixed
    geolocation allowance plus whatever spatial dispersion this candidate's
    own detection history actually shows, rather than one fixed radius for
    every sensor/facility alike."""
    base_km = 0.5
    dispersion = (persistence["spread_km"] or 0.0) * 2.0
    return round(base_km + dispersion, 3)


def match_detections(
    detections: list[dict[str, Any]], scored_sources: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Nearest scored candidate within its own match radius, for every
    detection - a read-only join, not a mutation of anything. Only
    candidates classified 'persistent_industrial' or 'ambiguous' are worth
    matching against - 'insufficient_evidence' candidates don't flag
    anything."""
    flaggable = [
        s for s in scored_sources if s["classification"] in ("persistent_industrial", "ambiguous")
    ]
    matches: dict[int, dict[str, Any]] = {}
    for det in detections:
        best = None
        best_dist = None
        for src in flaggable:
            dist = haversine_km(det["lat"], det["lon"], src["lat"], src["lon"])
            if dist <= src["match_radius_km"] and (best_dist is None or dist < best_dist):
                best, best_dist = src, dist
        if best is not None:
            matches[det["id"]] = {
                "source_id": best["id"],
                "classification": best["classification"],
                "score": best["score"],
                "distance_km": round(best_dist, 3),
            }
    return matches


def apply_wildfire_override(
    detections: list[dict[str, Any]],
    matches: dict[int, dict[str, Any]],
    scored_sources: list[dict[str, Any]],
) -> int:
    """Doc section 13.8: even a normally persistent source gets temporarily
    "unmasked" when detections expand beyond its historical envelope. A
    simplified stand-in for the doc's full 5-state event model: compares
    the spread of the event's *earliest* third of detections against its
    *latest* third, relative to each matched source's own match radius -
    if the fire has clearly grown past where a static source alone would
    explain it, every match against that source in this event is
    downgraded to 'possible_industrial_incident' rather than silently
    staying 'persistent_industrial'. Mutates ``matches`` in place and returns
    how many were downgraded."""
    if len(detections) < 4:
        return 0
    ordered = sorted(detections, key=lambda d: d["ts"])
    third = max(1, len(ordered) // 3)
    early, late = ordered[:third], ordered[-third:]

    by_source: dict[int, list[dict[str, Any]]] = {}
    for det in ordered:
        m = matches.get(det["id"])
        if m:
            by_source.setdefault(m["source_id"], []).append(det)

    downgraded = 0
    sources_by_id = {s["id"]: s for s in scored_sources}
    for source_id, members in by_source.items():
        src = sources_by_id.get(source_id)
        if src is None:
            continue
        early_ids = {d["id"] for d in early}
        late_ids = {d["id"] for d in late}
        early_here = [d for d in members if d["id"] in early_ids]
        late_here = [d for d in members if d["id"] in late_ids]
        if not early_here or not late_here:
            continue
        max_early_dist = max(haversine_km(d["lat"], d["lon"], src["lat"], src["lon"]) for d in early_here)
        max_late_dist = max(haversine_km(d["lat"], d["lon"], src["lat"], src["lon"]) for d in late_here)
        # Grown well beyond both the source's own footprint and where it
        # started - the signature of a real fire passing through, not a
        # static source flickering.
        if max_late_dist > src["match_radius_km"] * 2.0 and max_late_dist > max_early_dist * 1.5:
            for det in members:
                matches[det["id"]]["classification"] = "possible_industrial_incident"
                matches[det["id"]]["override_reason"] = (
                    f"detections grew from {max_early_dist:.2f}km to {max_late_dist:.2f}km "
                    f"from the matched source - beyond its {src['match_radius_km']:.2f}km footprint"
                )
                downgraded += 1
    return downgraded


# -------------------------------------------------------------- job kind


def scan_industrial_sources(params: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
    """Job body: fetch/refresh OSM candidates in ``bbox``, score each
    against this project's own cached detection history, match nearby
    detections, and (if ``event_id`` is given) apply the wildfire-override
    check using that event's own timeline."""
    bbox = tuple(float(v) for v in params["bbox"])  # west, south, east, north
    start_ts = float(params.get("start_ts") or time_module.time() - 30 * 86400)
    end_ts = float(params.get("end_ts") or time_module.time())
    window_days = int(params.get("window_days", 30))
    event_id = params.get("event_id")

    conn = sqlite3.connect(ctx.db_path, timeout=30.0)
    try:
        candidates = ensure_osm_candidates(conn, bbox)
        ctx.report_progress(20, note=f"{len(candidates)} OSM candidate(s)")

        scored_sources: list[dict[str, Any]] = []
        for i, cand in enumerate(candidates):
            persistence = persistence_features(
                conn, cand["lat"], cand["lon"], radius_km=2.0, end_ts=end_ts, window_days=window_days
            )
            scoring = score_source(persistence, cand["evidence_class"], window_days)
            radius_km = match_radius_km(persistence)
            scored_sources.append({**cand, **scoring, "match_radius_km": radius_km, "persistence": persistence})

            conn.execute(
                "INSERT INTO industrial_source_scores "
                "(source_id, score, classification, detection_count, distinct_days, span_days, "
                "spread_km, match_radius_km, window_days, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (source_id) DO UPDATE SET score=excluded.score, "
                "classification=excluded.classification, detection_count=excluded.detection_count, "
                "distinct_days=excluded.distinct_days, span_days=excluded.span_days, "
                "spread_km=excluded.spread_km, match_radius_km=excluded.match_radius_km, "
                "window_days=excluded.window_days, computed_at=excluded.computed_at",
                (
                    cand["id"], scoring["score"], scoring["classification"],
                    persistence["detection_count"], persistence["distinct_days"], persistence["span_days"],
                    persistence["spread_km"], radius_km, window_days, int(time_module.time()),
                ),
            )
            if (i + 1) % 5 == 0 or i + 1 == len(candidates):
                # Commit before reporting progress: report_progress writes
                # through its own short-lived connection (see jobs.py's
                # module docstring), and SQLite allows only one writer at a
                # time - an open transaction here would make that write
                # block on (or, without WAL, fail against) this one.
                conn.commit()
                ctx.report_progress(20 + int((i + 1) / max(1, len(candidates)) * 50), note=f"scored {i + 1}/{len(candidates)}")
        conn.commit()

        west, south, east, north = bbox
        det_rows = conn.execute(
            "SELECT id, latitude, longitude, acq_ts FROM detections "
            "WHERE acq_ts BETWEEN ? AND ? AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?",
            (start_ts, end_ts, south, north, west, east),
        ).fetchall()
        detections = [{"id": r[0], "lat": r[1], "lon": r[2], "ts": r[3]} for r in det_rows]
        matches = match_detections(detections, scored_sources)
        ctx.report_progress(80, note=f"{len(matches)} detection(s) matched to candidates")

        downgraded = 0
        if event_id is not None:
            event_det_ids = {
                r[0]
                for r in conn.execute(
                    "SELECT detection_id FROM event_members WHERE event_id = ?", (int(event_id),)
                ).fetchall()
            }
            event_detections = [d for d in detections if d["id"] in event_det_ids]
            downgraded = apply_wildfire_override(event_detections, matches, scored_sources)

        counts: dict[str, int] = {}
        for m in matches.values():
            counts[m["classification"]] = counts.get(m["classification"], 0) + 1

        ctx.report_progress(100, note="done")
        return {
            "bbox": list(bbox),
            "candidate_count": len(candidates),
            "sources": [
                {
                    "id": s["id"], "lat": s["lat"], "lon": s["lon"], "evidence_class": s["evidence_class"],
                    "score": s["score"], "classification": s["classification"], "reason": s["reason"],
                    "match_radius_km": s["match_radius_km"], "detection_count": s["persistence"]["detection_count"],
                }
                for s in scored_sources
            ],
            "matches": {str(k): v for k, v in matches.items()},
            "classification_counts": counts,
            "wildfire_overrides_applied": downgraded,
            "window_days": window_days,
        }
    finally:
        conn.close()


register_kind("scan_industrial_sources", scan_industrial_sources)
