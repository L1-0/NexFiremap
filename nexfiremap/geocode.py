"""Free, keyless place-name search via the public Nominatim API.

This is the only network call in the project that happens *while a human is
actively typing*, so it gets its own, stricter care beyond the usual
cache-what-you-fetch pattern in tiles.py/cache.py:

- Server-proxied only. The browser's own Content-Security-Policy
  (``connect-src 'self'``, set in api.py) would block a direct client-side
  call to nominatim.openstreetmap.org anyway - this module is the one place
  that's allowed to reach it, exactly like /tiles/... proxies basemap
  providers instead of letting the browser hit them directly.
- One upstream call in flight at a time, gated to Nominatim's own published
  usage policy of at most 1 request/second
  (https://operations.osmfoundation.org/policies/nominatim/) - regardless of
  how many incident-LAN clients are typing into the search box at once, or
  how fast any one of them types. A debounced client still isn't a *trusted*
  rate limit; this is the actual one.
- An identifying User-Agent (reusing the same NEXFIREMAP_CONTACT already
  documented for tile fetches - one contact point covers both policies)
  instead of a generic library default, so a misbehaving deployment is
  reachable instead of just silently blocked.
- A small in-process result cache, since the same place name gets searched
  repeatedly across a shift/incident-LAN and Nominatim's policy explicitly
  asks callers not to re-request what they already have. Not persisted to
  disk: place search is a convenience, not operational record, and losing
  the cache on restart just costs a handful of re-fetches, not correctness.
- Never raises past its own boundary. A failed/unreachable/slow upstream
  reports itself as an explicit `error` string in the response rather than a
  500 or a silently empty result list indistinguishable from "no matches" -
  the same silent-failure trap this project's own audit found and fixed
  elsewhere (an empty dropdown must not look identical to "still offline").
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("nexfiremap.geocode")

# Distinct from a real cached "no address here" (a legitimate answer for
# open ocean/remote wilderness - both real cases for a wildfire map, stored
# as `None`) - a sentinel, not None, is what actually means "not cached
# yet, go fetch it". Conflating the two would make an already-confirmed
# "nothing here" re-fetch forever, exactly the kind of None-means-two-
# things bug this project's own audit exists to catch elsewhere.
_MISSING = object()

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
CACHE_TTL_S = 7 * 86400  # place names/coordinates don't move; a week is plenty
CACHE_MAX_ENTRIES = 500
MIN_REQUEST_INTERVAL_S = 1.05  # Nominatim policy floor (1 req/s) plus a small margin
MAX_QUERY_CHARS = 200
# Cache key precision for reverse lookups - about 1m at the equator, far
# finer than Nominatim's own address-level resolution, so two right-clicks
# a few pixels apart in the same spot still share a cache entry instead of
# each paying their own upstream request.
REVERSE_CACHE_DECIMALS = 5


class GeocodeService:
    """Owns the single shared HTTP client, in-process cache, and the
    lock/timestamp pair that enforces Nominatim's 1 req/s policy - one
    instance per running server, not per request, so the rate limit and
    cache are actually shared across concurrent search callers."""

    def __init__(self, settings: Settings) -> None:
        # Reuses the tile User-Agent/contact string - both are OSM-family
        # usage policies asking for the same thing (an identifiable client).
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0),
            headers={"User-Agent": settings.tile_user_agent},
            follow_redirects=True,
        )
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._reverse_cache: dict[tuple[float, float], tuple[float, dict[str, Any] | None]] = {}
        # Shared with search()'s own throttle below - reverse() calls the
        # same upstream Nominatim service, so both must serialize through
        # one lock/timestamp pair (Nominatim's 1 req/s policy is per
        # client, not per endpoint) rather than each getting its own budget.
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def stop(self) -> None:
        await self._client.aclose()

    def _cached(self, key: str) -> list[dict[str, Any]] | None:
        """Returns the cached result list if present and not yet expired,
        else None - callers treat None as "go fetch it", never as "empty
        result", so an expired/absent entry is indistinguishable on purpose."""
        entry = self._cache.get(key)
        if entry and time.time() - entry[0] < CACHE_TTL_S:
            return entry[1]
        return None

    async def search(self, query: str, *, limit: int = 6) -> dict[str, Any]:
        """Cached, rate-limited place-name search. Always returns a dict with
        an ``error`` key (None on success) rather than raising, so callers
        (and the frontend dropdown) can distinguish "no matches" from
        "upstream is unreachable" without a try/except."""
        q = (query or "").strip()[:MAX_QUERY_CHARS]
        if len(q) < 2:
            return {"query": q, "results": [], "error": None}

        key = q.lower()
        cached = self._cached(key)
        if cached is not None:
            return {"query": q, "results": cached, "error": None, "cached": True}

        async with self._lock:
            # Re-check inside the lock: several clients (or one impatient
            # one) can queue up behind the same in-flight query, and once
            # the first caller populates the cache the rest should read it
            # instead of each paying their own upstream request.
            cached = self._cached(key)
            if cached is not None:
                return {"query": q, "results": cached, "error": None, "cached": True}

            # Enforced here, inside the lock, rather than trusting the
            # frontend's own debounce - that's a UX nicety, not something a
            # shared upstream policy can rely on to actually hold the line.
            wait = MIN_REQUEST_INTERVAL_S - (time.time() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)

            try:
                response = await self._client.get(NOMINATIM_URL, params={
                    "q": q, "format": "jsonv2", "limit": str(max(1, min(limit, 10))),
                    "addressdetails": "0",
                })
            except httpx.HTTPError as exc:
                log.warning("Nominatim search failed for %r: %s", q, exc)
                return {"query": q, "results": [], "error": "Place search is unreachable right now."}
            finally:
                self._last_request = time.time()

            if response.status_code != 200:
                log.warning("Nominatim HTTP %d for %r", response.status_code, q)
                return {"query": q, "results": [], "error": f"Place search returned HTTP {response.status_code}."}

            try:
                payload = response.json()
            except ValueError:
                return {"query": q, "results": [], "error": "Place search returned an unreadable response."}

            results = [row for row in (self._row(item) for item in payload[:limit]) if row is not None]
            if len(self._cache) >= CACHE_MAX_ENTRIES:
                oldest_key = min(self._cache, key=lambda existing: self._cache[existing][0])
                self._cache.pop(oldest_key, None)
            self._cache[key] = (time.time(), results)
            return {"query": q, "results": results, "error": None, "cached": False}

    async def reverse(self, lat: float, lon: float) -> dict[str, Any]:
        """Cached, rate-limited reverse lookup ("what's here") - same
        always-a-dict-with-an-error-key contract as search(), and shares
        its cache/rate-limit machinery (see __init__'s comment) rather than
        duplicating it. ``result`` is None (not an error) for real ocean/
        remote-wilderness coordinates Nominatim has no address for."""
        key = (round(lat, REVERSE_CACHE_DECIMALS), round(lon, REVERSE_CACHE_DECIMALS))
        cached = self._cached_reverse(key)
        if cached is not _MISSING:
            return {"lat": lat, "lon": lon, "result": cached, "error": None, "cached": True}

        async with self._lock:
            cached = self._cached_reverse(key)
            if cached is not _MISSING:
                return {"lat": lat, "lon": lon, "result": cached, "error": None, "cached": True}

            wait = MIN_REQUEST_INTERVAL_S - (time.time() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)

            try:
                response = await self._client.get(NOMINATIM_REVERSE_URL, params={
                    "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "jsonv2", "addressdetails": "0",
                })
            except httpx.HTTPError as exc:
                log.warning("Nominatim reverse lookup failed for %.5f,%.5f: %s", lat, lon, exc)
                return {"lat": lat, "lon": lon, "result": None, "error": "Reverse lookup is unreachable right now."}
            finally:
                self._last_request = time.time()

            if response.status_code != 200:
                log.warning("Nominatim reverse HTTP %d for %.5f,%.5f", response.status_code, lat, lon)
                return {"lat": lat, "lon": lon, "result": None, "error": f"Reverse lookup returned HTTP {response.status_code}."}

            try:
                payload = response.json()
            except ValueError:
                return {"lat": lat, "lon": lon, "result": None, "error": "Reverse lookup returned an unreadable response."}

            # A coordinate with no nearby addressable feature (open ocean,
            # remote wilderness - both real cases for a wildfire map) comes
            # back as an "error" field in the payload itself, not an HTTP
            # error - that's a genuine "nothing here", not a failure, so it
            # caches and returns the same as a real match, just with
            # result=None.
            row = None if "error" in payload else self._row(payload)
            if len(self._reverse_cache) >= CACHE_MAX_ENTRIES:
                oldest_key = min(self._reverse_cache, key=lambda existing: self._reverse_cache[existing][0])
                self._reverse_cache.pop(oldest_key, None)
            self._reverse_cache[key] = (time.time(), row)
            return {"lat": lat, "lon": lon, "result": row, "error": None, "cached": False}

    def _cached_reverse(self, key: tuple[float, float]) -> dict[str, Any] | None | object:
        """Returns the cached row, ``None`` (a real cached "no address
        here"), or the ``_MISSING`` sentinel (not cached/expired - go
        fetch it). Three distinguishable outcomes, not two - see
        ``_MISSING``'s own comment for why collapsing the first two would
        be a real bug, not just imprecise typing."""
        entry = self._reverse_cache.get(key)
        if entry and time.time() - entry[0] < CACHE_TTL_S:
            return entry[1]
        return _MISSING

    @staticmethod
    def _row(item: dict[str, Any]) -> dict[str, Any] | None:
        """Normalizes one Nominatim result into this project's own shape.
        Returns None (dropped by the caller) rather than raising on a
        malformed entry - one bad row from upstream shouldn't fail the
        whole search."""
        try:
            lat, lon = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        bounds = None
        bbox = item.get("boundingbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            try:
                south, north, west, east = (float(value) for value in bbox)
                bounds = [west, south, east, north]
            except (TypeError, ValueError):
                bounds = None
        return {
            "label": item.get("display_name") or f"{lat:.4f}, {lon:.4f}",
            "lat": lat, "lon": lon, "bounds": bounds,
            "type": item.get("type"), "class": item.get("class"),
        }
