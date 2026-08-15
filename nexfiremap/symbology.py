"""Tactical symbol codes for every feature type, in three profiles.

There is no single worldwide - or even Europe-wide - set of fire-brigade
tactical symbols; `docs/further_plan.md` surveys why. Germany uses the
*Taktische Zeichen im Bevoelkerungsschutz* recommendations (DV/FwDV 102),
US incident maps use ICS/NIMS conventions supplemented by NWCG PMS 936 and
NFPA 170, and TAK-family software addresses things by MIL-STD-2525-derived
SIDC strings and its own CoT type codes.

Rather than pick a winner, this module keeps one table keyed by
`FEATURE_TYPES` and gives each entry a code in *every* scheme at once. Three
consumers read it and each takes the column it needs:

  * `ingest/cot.py`      - ``cot_type``, both directions (CoT event type
                            <-> feature type), which is what makes an ATAK
                            marker arrive as the right kind of feature and a
                            NexFiremap feature render correctly on a TAK device.
  * `products.py`        - ``color`` and the profile's label, for map legends.
  * the frontend         - the whole table, published via `/api/operations/meta`
                            so `operations.js` never keeps a second copy that
                            can drift out of step with this one.

The practical guidance `further_plan.md` lands on - state the selected
standard on every map, always include a legend, never rely on colour alone -
is why `PROFILES` exists as an explicit, named choice rather than an implicit
default, and why `profile_label()` is the accessor products use: a symbol
that means one thing on a German Lagekarte and another on a US ICS map must
never be rendered without saying which reading applies.

`DEFAULT_PROFILE` is ``simplified_multinational`` because that is the value
the frontend has been stamping onto every feature's ``symbology_profile``
property already (`static/js/operations.js`), so existing records keep their
meaning rather than being retroactively reinterpreted.
"""

from __future__ import annotations

from typing import Any

from .operations import AREA_TYPES, FEATURE_TYPES, LINE_TYPES, POINT_TYPES


# The three renderings this table supports. "simplified_multinational" is a
# neutral house profile - plain shapes and English labels, safe to show to a
# multi-agency room where not everyone reads the same doctrine; the other two
# are the national systems proper.
PROFILES: dict[str, dict[str, str]] = {
    "simplified_multinational": {
        "label": "Simplified (multinational)",
        "authority": "NexFiremap house profile - neutral shapes, English labels",
    },
    "dv102": {
        "label": "Taktische Zeichen (DV 102)",
        "authority": "DV/FwDV 102 - Taktische Zeichen im Bevoelkerungsschutz (DE)",
    },
    "nfpa170_ics": {
        "label": "ICS / NFPA 170 (US)",
        "authority": "ICS/NIMS incident map conventions, NWCG PMS 936, NFPA 170 (US)",
    },
}

DEFAULT_PROFILE = "simplified_multinational"

# DV 102 colours the symbol by *organisation*, not by what the symbol depicts:
# red for the fire brigade, blue for THW/technical assistance, white-on-blue
# for command facilities, green for police, yellow for hazards. The palette
# below follows that convention where a feature type has an obvious owner and
# falls back to a hazard/behaviour reading otherwise, so a DE-profile map is
# at least not actively misleading about who owns what.
_FIRE = "#d92b2b"        # fire brigade / active fire behaviour
_COMMAND = "#1f5fbf"     # command and control
_HAZARD = "#e8a317"      # hazard, caution, restriction
_SAFETY = "#2e9e5b"      # safety, escape, refuge
_WATER = "#1f9ecf"       # water supply
_AIR = "#8e6fd8"         # air operations
_NEUTRAL = "#6b7280"     # administrative / boundary / uncertainty


def _entry(cot_type: str, sidc: str, dv102: str, color: str,
           en: str, de: str, ics: str = "") -> dict[str, str]:
    """One row of `SYMBOLS`.

    ``ics`` defaults to the English label because most feature types have no
    distinct ICS name - "staging area" is called a staging area on both
    sides of the Atlantic. Only the handful where US doctrine uses a genuinely
    different term (division/branch boundaries, drop points) override it.
    """
    return {"cot_type": cot_type, "sidc": sidc, "dv102_id": dv102, "color": color,
            "label_en": en, "label_de": de, "label_ics": ics or en}


# CoT type grammar, for the codes below: "a-" is an atom (a thing that exists
# at a place), then affiliation (f=friendly, n=neutral, h=hostile, u=unknown),
# then battle dimension (G=ground, A=air), then a function hierarchy. "b-"
# prefixes are bits/events rather than atoms - "b-m-p-w" is a generic waypoint
# and "b-l-l" a line, which is what TAK clients draw user shapes as. Anything
# NexFiremap has no better match for maps to those generic drawing types, so it
# still round-trips through a TAK device instead of being dropped.
SYMBOLS: dict[str, dict[str, str]] = {
    # ---- command, staging, logistics (points) -------------------------
    "command_post": _entry("a-f-G-U-C", "SFGPUH----K", "Fuehrungsstelle", _COMMAND,
                           "Command post", "Fuehrungsstelle", "Incident command post"),
    "staging_area": _entry("a-f-G-I-B-A", "SFGPIBA---K", "Bereitstellungsraum", _COMMAND,
                           "Staging area", "Bereitstellungsraum"),
    "drop_point": _entry("a-f-G-I-U-T", "SFGPIUT---K", "Uebergabepunkt", _COMMAND,
                         "Drop point", "Uebergabepunkt", "Drop point (NWCG)"),
    "communications_repeater": _entry("a-f-G-U-C-R", "SFGPUUMRS-K", "Fernmeldebetriebsstelle", _COMMAND,
                                      "Communications repeater", "Fernmeldebetriebsstelle"),
    "weather_station": _entry("a-f-G-U-U-S-W", "SFGPUUMSEA", "Wetterstation", _NEUTRAL,
                              "Weather station", "Wetterstation"),
    "resource_position": _entry("a-f-G-E-V", "SFGPEV----K", "Einsatzmittel", _FIRE,
                                "Resource position", "Einsatzmittelposition"),

    # ---- water supply (points) ----------------------------------------
    "water_source": _entry("b-m-p-w", "SFGPIRP---K", "Loeschwasserentnahmestelle", _WATER,
                           "Water source", "Loeschwasserentnahmestelle"),
    "restricted_water_source": _entry("b-m-p-w", "SFGPIRP---K", "Loeschwasserentnahmestelle (bedingt)", _WATER,
                                      "Restricted water source", "Bedingt geeignete Entnahmestelle"),
    "dip_site": _entry("b-m-p-w", "SFGPIRP---K", "Wasserentnahme Luftfahrzeug", _WATER,
                       "Dip site", "Wasserentnahmestelle (Luft)"),

    # ---- air operations (points) --------------------------------------
    "helibase": _entry("a-f-A-M-H", "SFAPMFH---K", "Hubschrauberlandeplatz", _AIR,
                       "Helibase", "Hubschrauberbasis"),
    "helispot": _entry("a-f-A-M-H", "SFAPMFH---K", "Landestelle", _AIR,
                       "Helispot", "Landestelle"),

    # ---- safety (points) ----------------------------------------------
    "safety_zone": _entry("b-m-p-w", "SFGPGDAS--K", "Sicherheitsbereich", _SAFETY,
                          "Safety zone", "Sicherheitsbereich"),
    "lookout": _entry("a-f-G-U-U-S", "SFGPUUS---K", "Beobachter", _SAFETY,
                      "Lookout", "Beobachtungsstelle"),
    "anchor_point": _entry("b-m-p-w", "SFGPGDAP--K", "Ausgangspunkt", _SAFETY,
                           "Anchor point", "Ausgangspunkt"),
    "trigger_point": _entry("b-m-p-w", "SFGPGDAT--K", "Ausloesepunkt", _HAZARD,
                            "Trigger point", "Ausloesepunkt"),

    # ---- hazards and restrictions (points) ----------------------------
    "hazard": _entry("a-h-G", "SHGP------K", "Gefahrenstelle", _HAZARD,
                     "Hazard", "Gefahrenstelle"),
    "critical_value": _entry("a-n-G-I", "SNGPI-----K", "Schutzobjekt", _HAZARD,
                             "Critical value at risk", "Schutzwuerdiges Objekt"),
    "road_closure": _entry("a-f-G-I-R-C", "SFGPIRC---K", "Strassensperre", _HAZARD,
                           "Road closure", "Strassensperre"),

    # ---- observed fire behaviour (points) -----------------------------
    "spot_fire": _entry("a-h-G-U-C-F", "SHGPUCF---K", "Brandstelle", _FIRE,
                        "Spot fire", "Flugfeuer / Brandnest"),
    "smoke_report": _entry("a-u-G", "SUGP------K", "Rauchentwicklung", _FIRE,
                           "Smoke report", "Rauchmeldung"),
    "wind_observation": _entry("b-m-p-w", "SFGPUUMSEA", "Windmessung", _NEUTRAL,
                               "Wind observation", "Windbeobachtung"),

    # ---- lines --------------------------------------------------------
    "fire_perimeter": _entry("b-l-l", "SHGPUCF---K", "Brandrand", _FIRE,
                             "Fire perimeter", "Brandrand"),
    "active_edge": _entry("b-l-l", "SHGPUCF---K", "Aktiver Brandrand", _FIRE,
                          "Active edge", "Aktiver Brandrand"),
    "inactive_edge": _entry("b-l-l", "SNGPUCF---K", "Inaktiver Brandrand", _NEUTRAL,
                            "Inactive edge", "Inaktiver Brandrand"),
    "tactical_line": _entry("b-l-l", "SFGPDL----K", "Abwehrlinie", _COMMAND,
                            "Tactical line", "Taktische Linie", "Control line"),
    "escape_route": _entry("b-l-l", "SFGPDLE---K", "Rueckzugsweg", _SAFETY,
                           "Escape route", "Rueckzugsweg"),
    "division_boundary": _entry("b-l-l", "SFGPDLB---K", "Abschnittsgrenze", _NEUTRAL,
                                "Division boundary", "Abschnittsgrenze", "Division break"),
    "branch_boundary": _entry("b-l-l", "SFGPDLB---K", "Einsatzabschnittsgrenze", _NEUTRAL,
                              "Branch boundary", "Einsatzabschnittsgrenze", "Branch break"),
    "spread_arrow": _entry("b-l-l", "SHGPUCF---K", "Ausbreitungsrichtung", _FIRE,
                           "Spread direction", "Ausbreitungsrichtung"),
    "arrival_time_line": _entry("b-l-l", "SNGPUCF---K", "Ankunftszeitlinie", _NEUTRAL,
                                "Arrival-time line", "Ankunftszeitlinie", "Time-of-arrival isochrone"),
    "road_restriction": _entry("b-l-l", "SFGPIRC---K", "Strassenbeschraenkung", _HAZARD,
                               "Road restriction", "Strassenbeschraenkung"),

    # ---- areas --------------------------------------------------------
    "confirmed_perimeter": _entry("b-l-l", "SHGPUCF---K", "Bestaetigte Brandflaeche", _FIRE,
                                  "Confirmed perimeter", "Bestaetigter Brandrand"),
    "forecast_perimeter": _entry("b-l-l", "SNGPUCF---K", "Prognostizierte Brandflaeche", _HAZARD,
                                 "Forecast perimeter", "Prognostizierter Brandrand"),
    "burn_area": _entry("b-l-l", "SHGPUCF---K", "Brandflaeche", _FIRE,
                        "Burn area", "Brandflaeche"),
    "evacuation_area": _entry("b-l-l", "SFGPGDAE--K", "Raeumungsbereich", _HAZARD,
                              "Evacuation area", "Raeumungsbereich"),
    "structure_protection_area": _entry("b-l-l", "SFGPGDAP--K", "Objektschutzbereich", _SAFETY,
                                        "Structure protection area", "Objektschutzbereich"),
    "safety_zone_area": _entry("b-l-l", "SFGPGDAS--K", "Sicherheitsflaeche", _SAFETY,
                               "Safety zone area", "Sicherheitsflaeche"),
    "uncertainty_area": _entry("b-l-l", "SNGP------K", "Unsicherheitsbereich", _NEUTRAL,
                               "Uncertainty area", "Unsicherheitsbereich"),
}

# Every feature type must have a row. Checked at import (not only in the test
# suite) because a feature type added to `operations/vocab.py` without a symbol
# would otherwise fail much later and much more confusingly - as a KeyError
# inside a CoT serialisation or a PDF legend, at the point someone is trying to
# produce a product during an incident.
_missing = FEATURE_TYPES - set(SYMBOLS)
if _missing:  # pragma: no cover - guards a developer mistake, not runtime input
    raise RuntimeError(f"symbology.SYMBOLS is missing feature types: {sorted(_missing)}")

# Reverse index for inbound CoT. Several feature types deliberately share a
# CoT type (there is no CoT code for "dip site" as distinct from any other
# water point), so this maps each code to the *first* feature type that claims
# it, ordered by geometry class: a CoT atom is a point, so a point type is
# always the better guess than a line or area type sharing the same code.
_COT_PRIORITY = sorted(SYMBOLS, key=lambda name: (
    0 if name in POINT_TYPES else 1 if name in LINE_TYPES else 2, name))
COT_TYPE_TO_FEATURE: dict[str, str] = {}
for _name in _COT_PRIORITY:
    COT_TYPE_TO_FEATURE.setdefault(SYMBOLS[_name]["cot_type"], _name)


# Which sprite in `static/vendor/symbols/tactical.svg` draws each feature
# type. Kept separate from `SYMBOLS` rather than added as a seventh column
# because it is a *rendering* concern with far fewer distinct values than there
# are feature types - a dozen glyphs cover 38 types, and several standards'
# codes legitimately share one shape. Anything unlisted falls back to
# "generic", so a new feature type renders as a plain marker instead of
# vanishing from the map.
GLYPHS: dict[str, str] = {
    "command_post": "command", "staging_area": "staging", "drop_point": "staging",
    "communications_repeater": "comms", "weather_station": "wind",
    "resource_position": "vehicle",
    "water_source": "water", "dip_site": "water",
    "restricted_water_source": "water-restricted",
    "helibase": "air", "helispot": "air",
    "safety_zone": "safety", "safety_zone_area": "safety",
    "lookout": "lookout", "anchor_point": "anchor", "trigger_point": "hazard",
    "hazard": "hazard", "critical_value": "protect",
    "structure_protection_area": "protect",
    "road_closure": "closure", "road_restriction": "closure",
    "spot_fire": "fire", "smoke_report": "smoke", "wind_observation": "wind",
    "fire_perimeter": "fire", "active_edge": "fire", "confirmed_perimeter": "fire",
    "burn_area": "fire", "forecast_perimeter": "fire", "spread_arrow": "fire",
    "evacuation_area": "hazard",
}

DEFAULT_GLYPH = "generic"

#: Where the sprite sheet is served from. Vendored, never a CDN - the map has
#: to draw with the WAN down, which is the whole premise of this project.
SPRITE_URL = "/static/vendor/symbols/tactical.svg"


def glyph(feature_type: str) -> str:
    """Sprite id for a feature type, for ``<use href="#nf-sym-...">``."""
    return GLYPHS.get(feature_type, DEFAULT_GLYPH)


def symbol(feature_type: str) -> dict[str, str]:
    """The full symbol row for a feature type.

    Falls back to the generic CoT waypoint/line codes for an unknown type
    rather than raising: this is called on the CoT *output* path, where a
    feature type this table hasn't caught up with should still leave the
    server as a drawable generic marker instead of failing the whole export.
    """
    found = SYMBOLS.get(feature_type)
    if found is not None:
        return found
    generic = "b-l-l" if feature_type in (LINE_TYPES | AREA_TYPES) else "b-m-p-w"
    return _entry(generic, "SUGP------K", "", _NEUTRAL,
                  feature_type.replace("_", " "), feature_type.replace("_", " "))


def cot_type(feature_type: str) -> str:
    """CoT event type for a feature type - the outbound half of the mapping."""
    return symbol(feature_type)["cot_type"]


def feature_type_for_cot(value: str, *, default: str = "spot_fire") -> str:
    """Feature type for an inbound CoT event type - the inbound half.

    CoT types are hierarchical and senders are free to be more specific than
    this table is (``a-f-G-E-V-A-T-H`` for a particular vehicle class, where
    we only know ``a-f-G-E-V``). Matching the longest known prefix rather than
    requiring equality is what lets an unrecognised-but-more-detailed type from
    a real ATAK device still land on a sensible feature type instead of the
    fallback.
    """
    raw = str(value or "").strip()
    if not raw:
        return default
    if raw in COT_TYPE_TO_FEATURE:
        return COT_TYPE_TO_FEATURE[raw]
    best = ""
    for known in COT_TYPE_TO_FEATURE:
        if raw.startswith(known + "-") and len(known) > len(best):
            best = known
    return COT_TYPE_TO_FEATURE[best] if best else default


def label(feature_type: str, profile: str = DEFAULT_PROFILE) -> str:
    """Human label for a feature type in one profile.

    An unknown profile falls back to the default rather than raising - a
    stored feature carrying a profile name this build doesn't know (an older
    or newer package imported via `import_bundle`) should still render.
    """
    row = symbol(feature_type)
    if profile == "dv102":
        return row["label_de"] or row["label_en"]
    if profile == "nfpa170_ics":
        return row["label_ics"] or row["label_en"]
    return row["label_en"]


def profile_label(profile: str) -> str:
    """Display name of a profile, for the legend line on every product."""
    return PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])["label"]


def profile_authority(profile: str) -> str:
    """The standard a profile claims to follow, named in full for map footers.

    `further_plan.md`'s conclusion for multinational operations is to state
    the selected standard on every map; this is the string that does it.
    """
    return PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])["authority"]


def normalise_profile(value: Any) -> str:
    """Coerce caller input to a known profile name, defaulting when unknown."""
    name = str(value or "").strip().lower()
    return name if name in PROFILES else DEFAULT_PROFILE


def table(profile: str = DEFAULT_PROFILE) -> dict[str, Any]:
    """The whole vocabulary, resolved for one profile, as the frontend gets it.

    Published by `/api/operations/meta` so `operations.js` renders markers and
    legends from this module rather than from a second hand-maintained copy.
    """
    resolved = normalise_profile(profile)
    return {
        "profile": resolved,
        "profile_label": profile_label(resolved),
        "profile_authority": profile_authority(resolved),
        "profiles": [{"id": key, **value} for key, value in PROFILES.items()],
        "sprite_url": SPRITE_URL,
        "symbols": {
            name: {"label": label(name, resolved), "color": row["color"], "glyph": glyph(name),
                   "sidc": row["sidc"], "dv102_id": row["dv102_id"], "cot_type": row["cot_type"],
                   "geometry": "Point" if name in POINT_TYPES else
                               "LineString" if name in LINE_TYPES else "Polygon"}
            for name, row in SYMBOLS.items()
        },
    }


__all__ = [
    "COT_TYPE_TO_FEATURE", "DEFAULT_GLYPH", "DEFAULT_PROFILE", "GLYPHS", "PROFILES",
    "SPRITE_URL", "SYMBOLS",
    "cot_type", "feature_type_for_cot", "glyph", "label", "normalise_profile",
    "profile_authority", "profile_label", "symbol", "table",
]
