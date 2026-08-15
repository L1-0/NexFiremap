"""Symbol-table coverage, profile resolution and CoT type round-tripping."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap import symbology
from nexfiremap.operations import AREA_TYPES, FEATURE_TYPES, LINE_TYPES, POINT_TYPES


def main() -> None:
    # Every feature type has a row in every profile. This is the check that
    # catches a feature type added to operations/vocab.py without a symbol -
    # the module raises at import, but assert it explicitly so the failure
    # names the gap instead of surfacing as an ImportError.
    assert set(symbology.SYMBOLS) == FEATURE_TYPES, sorted(FEATURE_TYPES ^ set(symbology.SYMBOLS))
    for feature_type in sorted(FEATURE_TYPES):
        row = symbology.SYMBOLS[feature_type]
        for column in ("cot_type", "sidc", "color", "label_en", "label_de", "label_ics"):
            assert row[column], f"{feature_type} has an empty {column}"
        for profile in symbology.PROFILES:
            assert symbology.label(feature_type, profile), f"{feature_type} has no label in {profile}"

    # Colours are hex triples - products.py and the frontend both feed these
    # straight to a renderer, which would silently draw black for a typo.
    for feature_type, row in symbology.SYMBOLS.items():
        assert row["color"].startswith("#") and len(row["color"]) == 7, f"{feature_type}: {row['color']}"

    # Outbound then inbound: every point type must survive a CoT round trip
    # back to *something* point-shaped, or an ATAK marker would arrive as a
    # feature type whose geometry _validate_geometry then rejects.
    for feature_type in sorted(POINT_TYPES):
        resolved = symbology.feature_type_for_cot(symbology.cot_type(feature_type))
        assert resolved in POINT_TYPES, f"{feature_type} -> {resolved}"

    # Longest-prefix matching: a more specific real-world CoT type than the
    # table knows should still resolve, not fall back.
    assert symbology.feature_type_for_cot("a-f-G-U-C") == "command_post"
    assert symbology.feature_type_for_cot("a-f-G-E-V-A-T-H") == "resource_position"
    assert symbology.feature_type_for_cot("a-f-G-E-VXYZ") == "spot_fire", "prefix match must respect the '-' boundary"
    assert symbology.feature_type_for_cot("") == "spot_fire"
    assert symbology.feature_type_for_cot("zzz", default="hazard") == "hazard"

    # Unknown feature types degrade to a drawable generic rather than raising,
    # since this runs on the CoT export path during an incident.
    generic_point = symbology.symbol("not_a_real_type")
    assert generic_point["cot_type"] == "b-m-p-w"

    # Profiles: unknown names fall back instead of raising, because an imported
    # incident package may carry a profile this build does not know.
    assert symbology.normalise_profile("DV102") == "dv102"
    assert symbology.normalise_profile(None) == symbology.DEFAULT_PROFILE
    assert symbology.normalise_profile("martian") == symbology.DEFAULT_PROFILE
    assert symbology.label("command_post", "dv102") == "Fuehrungsstelle"
    assert symbology.label("division_boundary", "nfpa170_ics") == "Division break"
    assert symbology.label("command_post", "martian") == "Command post"

    # Every profile names the standard it follows - further_plan.md's guidance
    # is to state the selected standard on every map, so this string must exist.
    for profile in symbology.PROFILES:
        assert symbology.profile_authority(profile)
        assert symbology.profile_label(profile)

    # The published table is what the frontend renders from; geometry class has
    # to agree with the vocabulary or markers land on the wrong layer.
    published = symbology.table("dv102")
    assert published["profile"] == "dv102"
    assert len(published["symbols"]) == len(FEATURE_TYPES)
    for name, entry in published["symbols"].items():
        expected = "Point" if name in POINT_TYPES else "LineString" if name in LINE_TYPES else "Polygon"
        assert entry["geometry"] == expected, f"{name}: {entry['geometry']} != {expected}"
    assert {p["id"] for p in published["profiles"]} == set(symbology.PROFILES)
    assert set(AREA_TYPES) <= set(published["symbols"])

    check_sprites()
    check_feature_normalisation()
    print("Symbology table checks passed.")


def check_sprites() -> None:
    """Every glyph the table names must exist in the vendored sprite sheet.

    A missing sprite is invisible in code review and produces an empty marker
    on the map - a feature that is present in the data but cannot be seen,
    which is the worst possible failure mode for a tactical symbol."""
    import xml.etree.ElementTree as ET

    sheet = Path(__file__).resolve().parent.parent / "nexfiremap" / "static" / "vendor" / "symbols" / "tactical.svg"
    assert sheet.is_file(), "the vendored sprite sheet is missing"
    defined = {element.get("id") for element in ET.parse(sheet).iter()
               if element.tag.endswith("symbol")}

    for profile in symbology.PROFILES:
        used = {f"nf-sym-{entry['glyph']}" for entry in symbology.table(profile)["symbols"].values()}
        assert used <= defined, f"{profile} references missing sprites: {sorted(used - defined)}"

    # The fallback must exist, since it is what an unmapped feature type gets.
    assert f"nf-sym-{symbology.DEFAULT_GLYPH}" in defined
    assert symbology.glyph("not_a_real_type") == symbology.DEFAULT_GLYPH
    # No CDN: the sheet is served from our own static mount.
    assert symbology.SPRITE_URL.startswith("/static/")


def check_feature_normalisation() -> None:
    """A feature's stored symbology_profile must be normalised on write - and
    an unknown one must normalise rather than raise, or an incident package
    from a build that knows a newer profile could not be imported."""
    import tempfile

    from nexfiremap.db import Database
    from nexfiremap.operations import OperationsStore, default_period

    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "sym.sqlite3")
        try:
            store = OperationsStore(db)
            incident = store.create_incident({"name": "Sym"}, "IC")
            period = store.create_period(incident["id"], default_period(), "IC")
            base = {"period_id": period["id"], "feature_type": "command_post",
                    "geometry": {"type": "Point", "coordinates": [11.5, 48.1]}}

            created = store.create_feature(
                incident["id"], {**base, "properties": {"symbology_profile": "DV102"}}, "IC")
            assert created["properties"]["symbology_profile"] == "dv102"

            future = store.create_feature(
                incident["id"], {**base, "properties": {"symbology_profile": "from-a-newer-build"}}, "IC")
            assert future["properties"]["symbology_profile"] == symbology.DEFAULT_PROFILE

            # The update path must normalise too, or an edit could reintroduce
            # a value the create path rejected.
            updated = store.update_feature(
                incident["id"], created["properties"]["id"],
                {"properties": {"symbology_profile": "NFPA170_ICS"}},
                created["properties"]["revision"], "IC")
            assert updated["properties"]["symbology_profile"] == "nfpa170_ics"
        finally:
            db.close()


if __name__ == "__main__":
    main()
