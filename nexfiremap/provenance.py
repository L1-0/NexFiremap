"""Unified, conservative provenance and freshness records for model outputs.

Every model-derived layer a plan scenario can attach (spread model runs,
event likelihood analysis, burn-scar detection, ...) needs the same basic
lineage answer for anyone relying on it operationally: what inputs fed it,
when it was generated versus when its *data* is actually anchored to
(``reference_at``, not ``generated_at`` - a job run today against three-day-
old detections is stale even though it just finished), and whether it's
still inside its own freshness window. `from_job` is the single place that
turns one job record into that answer, so every consumer (plan scenarios,
exports, the UI's staleness badge) sees an identically-shaped, identically-
computed record rather than each re-deriving their own notion of "stale."
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any


# Declared, not inferred: each job kind's real upstream data sources, used
# to populate the record's "sources" field. Kept as an explicit mapping
# (rather than introspecting the job/result) because the same job kind can
# have optional inputs (e.g. EUMETSAT corroboration) that shouldn't turn a
# provenance record's core source list into something order- or run-
# dependent.
MODEL_SOURCES = {
    "run_propagation": ["incident detections", "Copernicus DEM GLO-30", "ESA WorldCover", "Open-Meteo"],
    "run_ensemble_assimilation": ["incident detections", "Copernicus DEM GLO-30", "ESA WorldCover", "Open-Meteo"],
    "analyze_event": ["incident detections"],
    "validate_event": ["historical incident detections"],
    "analyze_burn_scar": ["Sentinel-2/Landsat imagery", "incident detections"],
}


def from_job(job: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Builds a provenance/freshness record from a completed job row.

    ``now`` is injectable (rather than always using the live clock) so
    staleness can be evaluated as of a specific moment - e.g. re-checking an
    older plan scenario's provenance as it looked/looks, or in tests,
    without needing to fake the system clock.
    """
    if job.get("status") != "done":
        raise ValueError("only a completed job can be attached to a plan scenario")
    params = json.loads(job.get("params_json") or "{}")
    result = json.loads(job.get("result_json") or "{}")
    kind = str(job.get("kind") or "unknown")
    # The data's own reference time (when its inputs were valid), preferring
    # the result's over the request params' over the job's finish time - the
    # first value actually present in that order of trust, since a result
    # that recorded its own reference_ts is authoritative over the request
    # that merely asked for one.
    reference = float(result.get("reference_ts") or params.get("reference_ts") or job.get("finished_at") or time.time())
    current = float(now if now is not None else time.time())
    # Fire-behaviour/likelihood outputs age out fast (conditions/detections
    # move quickly); burn-scar/validation analyses are tied to imagery/
    # historical data that doesn't shift hour to hour, hence the longer window.
    valid_hours = 6 if kind in {"run_propagation", "run_ensemble_assimilation", "analyze_event"} else 24
    warnings: list[str] = []
    if current > reference + valid_hours * 3600:
        warnings.append(f"model reference time is older than the {valid_hours}-hour operational freshness window")
    weather = result.get("weather") or {}
    if kind in {"run_propagation", "run_ensemble_assimilation"}:
        if not weather.get("hours_sampled"):
            warnings.append("weather input contains no sampled hours")
        if weather.get("hours_backfilled_recent", 0):
            warnings.append(f"{weather['hours_backfilled_recent']} recent weather hours were backfilled from forecast data")
        # A known, permanent gap (not something a future run can fix) - upstream
        # DEM/land-cover tile providers don't expose their own acquisition
        # dates, so this project can't report them - surfaced as a standing
        # warning rather than silently omitted.
        warnings.append("terrain and fuel source acquisition timestamps are not supplied by their upstream tiles")
    return {
        "schema": "nexfiremap-model-provenance/1",
        "job_id": job.get("id"), "model_kind": kind, "model_version": "NexFiremap 1.0",
        "generated_at": datetime.fromtimestamp(float(job.get("finished_at") or current), timezone.utc).isoformat(),
        "reference_at": datetime.fromtimestamp(reference, timezone.utc).isoformat(),
        "valid_until": datetime.fromtimestamp(reference + valid_hours * 3600, timezone.utc).isoformat(),
        "is_stale": current > reference + valid_hours * 3600,
        "sources": MODEL_SOURCES.get(kind, ["locally registered model inputs"]),
        "parameters": params, "weather_summary": weather, "warnings": warnings,
        "limitations": "Decision-support scenario only. Revalidate against current observations, weather, terrain, fuels and local doctrine before operational use.",
    }
