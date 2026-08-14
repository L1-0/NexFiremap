# Wind map implementation plan

## Operational contract

- [x] Build the map only from already-retained incident wind observations and attached model-run weather provenance. Do not fetch the network during display.
- [x] Preserve meteorological direction (`from` degrees), speed, gust, height, source, observation time and confidence.
- [x] Exclude future observations from a historical view and label stale inputs.
- [x] Use attached model weather only as AOI-wide background forcing, never as terrain-resolved truth.
- [x] Interpolate east/north vector components, not degree values, so north-wrap and opposing winds behave correctly.
- [x] When background exists, interpolate observed residuals over it. Otherwise interpolate observations directly.
- [x] Return source points, derived grid vectors, support distance/count, disagreement and limitations.
- [x] Render temporal wind arrows with an explicit time, age window, legend and measured/derived distinction.
- [x] Keep complex-terrain downscaling, live station adapters and forecast acquisition as external follow-on integrations.

## Trust and algorithm

Structured `wind_observation` and `weather_station` point features are authoritative only to the level stated by their status, confidence, age and source. The required fields are `wind_speed_ms` and `wind_from_deg`. Gust and measurement height are optional. Conservative import parsing accepts explicit m/s, km/h or knot units and cardinal/degree direction, while ambiguous text is rejected and counted.

At a requested historical time, only observations at or before that instant and within the selected look-back window participate. Meteorological direction is converted to east/north flow components. Inverse-distance-squared weights are multiplied by an exponential age weight. If an attached propagation/ensemble provenance record supplies wind, it forms a uniform background and observations interpolate corrections to that vector. Output converts the result back to meteorological `from` degrees.

This is a situational wind visualization, not WindNinja-grade terrain downscaling. A single point produces a uniform field. Two or more points produce a labelled IDW estimate. Vector disagreement, nearest-support distance, sample count and stale/background warnings remain visible.

## API

`GET /api/operations/incidents/{incident}/wind-field?bbox=W,S,E,N&at=ISO&window_hours=6&grid=12&scenario_id=...`

The response is GeoJSON with `vectors` and `observations` feature collections plus provenance, temporal window, method and limitations.

## External acceptance gates

Brigade acceptance still requires station/gateway schemas, instrument height/exposure rules, calibration and clock checks, representative valley/ridge validation, a forecast-provider policy, terrain-downscaling validation, target-tablet performance and a disconnected operational exercise.
