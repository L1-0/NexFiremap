"""Unified, conservative provenance and freshness records for model outputs."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any


MODEL_SOURCES = {
    "run_propagation": ["incident detections", "Copernicus DEM GLO-30", "ESA WorldCover", "Open-Meteo"],
    "run_ensemble_assimilation": ["incident detections", "Copernicus DEM GLO-30", "ESA WorldCover", "Open-Meteo"],
    "analyze_event": ["incident detections"],
    "validate_event": ["historical incident detections"],
    "analyze_burn_scar": ["Sentinel-2/Landsat imagery", "incident detections"],
}


def from_job(job: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    if job.get("status") != "done":
        raise ValueError("only a completed job can be attached to a plan scenario")
    params = json.loads(job.get("params_json") or "{}")
    result = json.loads(job.get("result_json") or "{}")
    kind = str(job.get("kind") or "unknown")
    reference = float(result.get("reference_ts") or params.get("reference_ts") or job.get("finished_at") or time.time())
    current = float(now if now is not None else time.time())
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
