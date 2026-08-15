"""Client for the NASA FIRMS area API.

Docs: https://firms.modaps.eosdis.nasa.gov/api/area/

The area endpoint returns CSV for a bounding box, a source and a day range:

    /api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{DAY_RANGE}/{START_DATE}

FIRMS answers errors with HTTP 200 and a plain-text body, so every response has
to be sniffed rather than trusted.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("nexfiremap.firms")

BASE_URL = "https://firms.modaps.eosdis.nasa.gov"
AREA_URL = BASE_URL + "/api/area/csv/{key}/{source}/{area}/{day_range}/{start}"
KEY_STATUS_URL = BASE_URL + "/mapserver/mapkey_status/"

# Column aliases across VIIRS / MODIS / Landsat products.
_BRIGHTNESS_KEYS = ("bright_ti4", "brightness", "bright_t4")
_BRIGHTNESS2_KEYS = ("bright_ti5", "bright_t31", "bright_t5")


class FirmsError(RuntimeError):
    """FIRMS returned something that is not usable CSV.

    ``retryable`` (checked by the caller's autofetch/retry loop) defaults to
    ``True`` here and is overridden ``False`` on the subclasses below whose
    condition a retry can never fix (bad key, unsupported source) - so a
    scheduler can safely back off and retry generically without needing to
    special-case every FIRMS failure mode itself.
    """

    retryable = True


class FirmsAuthError(FirmsError):
    """The map key is missing, invalid, or not yet activated."""

    retryable = False


class FirmsRateLimitError(FirmsError):
    """Transaction limit hit; back off and try again later."""

    retryable = True


class FirmsSourceError(FirmsError):
    """The requested source is not available for this key/area."""

    retryable = False


class FirmsDayRangeError(FirmsError):
    """The requested day range was rejected; retry with a smaller chunk."""

    retryable = True


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _to_float(value: Any) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalise_confidence(raw: str) -> tuple[int | None, str | None]:
    """FIRMS reports confidence as 0-100 (MODIS) or l/n/h (VIIRS)."""
    text = _clean(raw)
    if not text:
        return None, None
    try:
        pct = int(float(text))
    except ValueError:
        letter = text[0].lower()
        return None, {"l": "low", "n": "nominal", "h": "high"}.get(letter, "nominal")
    if pct < 30:
        level = "low"
    elif pct < 80:
        level = "nominal"
    else:
        level = "high"
    return pct, level


def _normalise_time(raw: str) -> str | None:
    """Acquisition time comes as HHMM, sometimes without the leading zero.

    Returns ``None`` when the field is missing or has no digits at all, so
    the caller drops the row rather than recording a fabricated midnight -
    the same treatment already given to a missing lat/lon/acq_date.
    """
    digits = "".join(ch for ch in _clean(raw) if ch.isdigit())
    return digits.zfill(4)[:4] if digits else None


def _first(row: dict[str, str], keys: tuple[str, ...]) -> float | None:
    """First numeric value found among a product's brightness-column
    aliases (see ``_BRIGHTNESS_KEYS``/``_BRIGHTNESS2_KEYS``) - VIIRS, MODIS
    and Landsat OLI each name the same physical quantity differently."""
    for key in keys:
        if key in row:
            value = _to_float(row[key])
            if value is not None:
                return value
    return None


def _normalise_row(row: dict[str, str], source: str) -> dict[str, Any] | None:
    """One raw FIRMS CSV row -> a canonical detection dict, or ``None`` if
    the row is missing a field this module treats as mandatory (position or
    acquisition time) - such rows are dropped rather than stored with
    fabricated values, since a wrong lat/lon/time is worse than a missing
    detection."""
    lat = _to_float(row.get("latitude"))
    lon = _to_float(row.get("longitude"))
    if lat is None or lon is None:
        return None

    acq_date = _clean(row.get("acq_date"))
    if not acq_date:
        return None
    acq_time = _normalise_time(row.get("acq_time", ""))
    if acq_time is None:
        return None

    try:
        stamp = datetime.strptime(f"{acq_date} {acq_time}", "%Y-%m-%d %H%M").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None

    confidence_raw = _clean(row.get("confidence"))
    confidence_pct, confidence_level = _normalise_confidence(confidence_raw)

    daynight = _clean(row.get("daynight")).upper()[:1] or None  # FIRMS sends "D"/"N" (or a full word on some products) - first letter covers both

    return {
        "source": source,
        "satellite": _clean(row.get("satellite")) or None,
        "instrument": _clean(row.get("instrument")) or None,
        "latitude": round(lat, 5),
        "longitude": round(lon, 5),
        "acq_date": acq_date,
        "acq_time": acq_time,
        "acq_ts": int(stamp.timestamp()),
        "brightness": _first(row, _BRIGHTNESS_KEYS),
        "brightness2": _first(row, _BRIGHTNESS2_KEYS),
        "scan": _to_float(row.get("scan")),
        "track": _to_float(row.get("track")),
        "confidence_raw": confidence_raw or None,
        "confidence_pct": confidence_pct,
        "confidence_level": confidence_level,
        "frp": _to_float(row.get("frp")),
        "daynight": daynight,
        "version": _clean(row.get("version")) or None,
        # FIRMS's area CSV has no discrete per-row id of its own (unlike
        # some other NASA products' granule ids) - the closest honest
        # equivalent to further_plan.md's "original source row identifier"
        # is keeping the untouched raw row, so any field this parser
        # doesn't otherwise map stays recoverable instead of being thrown
        # away at normalisation.
        "raw_json": json.dumps(row, sort_keys=True),
    }


def _raise_for_body(text: str, source: str) -> None:
    """FIRMS signals failure in the body, so classify it before parsing.

    All checks are substring matches on the response's first 600 characters
    (FIRMS has no error code field, just a short plain-text or HTML message),
    ordered from most to least specific so a message that happens to contain
    several trigger words is classified by its most actionable cause first."""
    head = text[:600].lower()

    if "map_key" in head and "invalid" in head or "invalid mapkey" in head:
        raise FirmsAuthError(
            "FIRMS rejected the map key. Check FIRMS_MAP_KEY in your .env file."
        )
    if "transaction limit" in head or "rate limit" in head or "too many" in head:
        raise FirmsRateLimitError(
            "FIRMS transaction limit reached (5000 per 10 minutes). Backing off."
        )
    if "day range" in head or "invalid range" in head:
        raise FirmsDayRangeError(f"FIRMS rejected the day range: {text[:200].strip()}")
    if "invalid source" in head or "unknown source" in head or "not a valid source" in head:
        raise FirmsSourceError(f"FIRMS does not accept source {source}: {text[:200].strip()}")
    if "invalid" in head or "error" in head or head.lstrip().startswith("<"):
        raise FirmsError(f"Unexpected FIRMS response for {source}: {text[:200].strip()}")

    # A valid payload always carries the coordinate header, even when empty.
    if "latitude" not in head or "longitude" not in head:
        raise FirmsError(f"Unrecognised FIRMS payload for {source}: {text[:200].strip()}")


class FirmsClient:
    """Thin async wrapper around the FIRMS area CSV endpoint: builds the
    URL, classifies HTTP-level and body-level failures into the typed
    errors above, and normalises every row it manages to parse. Holds one
    ``httpx.AsyncClient`` for its lifetime so connections/keep-alive are
    reused across repeated polls rather than reconnecting each call."""

    def __init__(
        self,
        map_key: str,
        *,
        timeout: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.map_key = map_key
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": "NexFiremap/1.0 (local cache server)"},
            follow_redirects=True,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_area(
        self,
        source: str,
        area: str,
        start: date,
        day_range: int,
    ) -> list[dict[str, Any]]:
        """Fetch detections for one area/source/date-window as normalised rows.

        ``area`` is either ``"west,south,east,north"`` or the literal ``"world"``.
        """
        if not self.map_key:
            raise FirmsAuthError("No FIRMS map key configured.")

        url = AREA_URL.format(
            key=self.map_key,
            source=source,
            area=area,
            day_range=day_range,
            start=start.isoformat(),
        )

        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise FirmsError(f"Network error talking to FIRMS: {exc}") from exc

        if response.status_code == 429:
            raise FirmsRateLimitError("FIRMS returned HTTP 429 (transaction limit).")
        if response.status_code >= 500:
            raise FirmsError(f"FIRMS server error HTTP {response.status_code}.")
        if response.status_code == 401 or response.status_code == 403:
            raise FirmsAuthError(f"FIRMS denied the request (HTTP {response.status_code}).")
        if response.status_code >= 400:
            raise FirmsError(f"FIRMS returned HTTP {response.status_code}: {response.text[:200]}")

        text = response.text
        _raise_for_body(text, source)

        reader = csv.DictReader(io.StringIO(text))
        rows: list[dict[str, Any]] = []
        for raw in reader:
            # Header casing varies across FIRMS products/sources - normalise
            # to lower-case here so every lookup in _normalise_row only
            # needs to check one spelling.
            normalised = _normalise_row(
                {(k or "").strip().lower(): v for k, v in raw.items() if k}, source
            )
            if normalised is not None:
                rows.append(normalised)
        return rows

    async def key_status(self) -> dict[str, Any]:
        """Transaction budget for the configured key."""
        if not self.map_key:
            return {"ok": False, "error": "no map key configured"}
        try:
            # A short timeout of its own, rather than the client's fetch
            # timeout (90 s by default, sized for a multi-day CSV download).
            # This call answers "how much of my key's budget is left" for a
            # status panel; on a blackholing network the fetch timeout would
            # hold that panel's request open for a minute and a half to report
            # a number nobody is waiting on. The result - success or failure -
            # is cached for 60 s by the caller either way.
            response = await self._client.get(
                KEY_STATUS_URL, params={"MAP_KEY": self.map_key},
                timeout=httpx.Timeout(10.0),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc)}

        try:
            payload = response.json()
        except ValueError:
            text = response.text.strip()
            if "invalid" in text.lower():
                return {"ok": False, "error": "invalid map key"}
            return {"ok": False, "error": text[:200] or "unreadable response"}

        # The endpoint nests the numbers under a per-key object, but has been
        # observed to sometimes return the fields flat at the top level
        # instead - fall back to the raw payload rather than raising KeyError.
        if isinstance(payload, dict):
            inner = payload.get(self.map_key)
            data = inner if isinstance(inner, dict) else payload
            return {
                "ok": True,
                "current_transactions": data.get("current_transactions"),
                "transaction_limit": data.get("transaction_limit"),
                "transaction_interval": data.get("transaction_interval"),
            }
        return {"ok": False, "error": "unexpected map key status payload"}
