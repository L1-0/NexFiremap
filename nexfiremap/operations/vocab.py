"""The fixed domain vocabularies of the operational record.

The tuples/sets below are enforced on every write (see the validators in
the per-aggregate stores and in `common._validate_geometry`) so that a
package produced on one machine can be merged into another without
silently inventing new enum values. Treat these as the single source of
truth: add a feature type, status, etc. here first, then teach the UI
about it.

They sit in their own module - separate from the stores that enforce them
and from `common.py`'s helpers - for two reasons. They are read far more
often than they are changed, and several modules outside this package
(`field_import.py`, `products.py`, `routes/meta.py`, which publishes the
vocabulary to the frontend) import them without wanting anything else the
operational store does. Keeping them dependency-free means importing the
vocabulary can never drag in the database layer.

Every name here is re-exported from `nexfiremap.operations`, which is the
path existing callers use; both spellings resolve to these same objects.
"""

from __future__ import annotations


SAFETY_CHECKS: tuple[tuple[str, str], ...] = (
    ("hazards", "Hazards identified"),
    ("lookouts", "Lookouts assigned"),
    ("communications", "Communications plan recorded"),
    ("escape_routes", "Escape routes identified"),
    ("safety_zones", "Safety zones identified"),
    ("medical", "Medical extraction route recorded"),
    ("alternative_access", "Alternative access and egress checked"),
    ("withdrawal_triggers", "Withdrawal trigger points defined"),
    ("weather_update", "Weather and fire-behaviour update is current"),
)

POINT_TYPES = {
    "anchor_point", "lookout", "safety_zone", "trigger_point", "hazard",
    "staging_area", "command_post", "drop_point", "water_source",
    "restricted_water_source", "helibase", "helispot", "dip_site",
    "weather_station", "communications_repeater", "road_closure",
    "resource_position",
    "critical_value", "spot_fire", "smoke_report", "wind_observation",
}
LINE_TYPES = {
    "fire_perimeter", "active_edge", "inactive_edge", "tactical_line",
    "escape_route", "division_boundary", "branch_boundary", "spread_arrow",
    "arrival_time_line", "road_restriction",
}
AREA_TYPES = {
    "confirmed_perimeter", "forecast_perimeter", "evacuation_area",
    "structure_protection_area", "safety_zone_area", "burn_area",
    "uncertainty_area",
}
FEATURE_TYPES = POINT_TYPES | LINE_TYPES | AREA_TYPES
# Subset of feature types that represent an observed fact about fire behaviour
# (as opposed to a plan/resource marker); used by progression() to build a
# time-sliced view of what was known to be true at a given moment.
OBSERVATION_TYPES = {
    "confirmed_perimeter", "fire_perimeter", "active_edge", "inactive_edge",
    "spot_fire", "smoke_report", "wind_observation", "burn_area",
}

SCENARIO_KINDS = {"primary", "contingency", "alternative", "worst_case"}
SCENARIO_STATUSES = {"draft", "approved", "retired"}
INCIDENT_STATUSES = {"active", "contained", "closed"}
PERIOD_STATUSES = {"draft", "active", "closed"}
RESOURCE_STATUSES = {"available", "assigned", "working", "returning", "unavailable"}
FEATURE_STATUSES = {
    "unconfirmed", "observed", "confirmed", "proposed", "planned",
    "under_construction", "completed", "held", "breached", "abandoned",
    "patrol", "mop_up", "inactive", "active",
}
