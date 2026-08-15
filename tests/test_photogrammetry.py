"""Footprint suggestion from drone metadata: geometry, gates and honesty.

The geometry is checked against values worked out independently of the
implementation - a nadir footprint's size follows from the field of view and
height by simple trigonometry, and its orientation follows from the yaw - so
these are not merely assertions that the code does what it does.

The other half of the file is about refusals. Every case where the metadata
cannot support a bounded answer must raise rather than return a plausible
looking rectangle, because a fabricated footprint is worse than none.
"""

from __future__ import annotations

import io
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from nexfiremap import photogrammetry as pg

# A DJI XMP packet in the shape the aircraft writes it. Values chosen so the
# expected footprint is computable by hand below.
XMP = """<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/"
  drone-dji:AbsoluteAltitude="+612.30"
  drone-dji:RelativeAltitude="+100.00"
  drone-dji:GimbalRollDegree="+0.00"
  drone-dji:GimbalYawDegree="{yaw}"
  drone-dji:GimbalPitchDegree="{pitch}"
  drone-dji:FlightYawDegree="+12.30"
  drone-dji:CalibratedFocalLength="{focal}"/>
</rdf:RDF></x:xmpmeta><?xpacket end="w"?>"""

WIDTH, HEIGHT = 4000, 3000
FOCAL_PX = 2000.0          # half-width / focal = 1 -> 45 degrees half-FOV across width
LAT, LON = 48.1372, 11.5755


def build_image(pitch: str = "-90.00", yaw: str = "+0.00", focal: str = "2000.0",
                gps: bool = True, xmp: bool = True) -> bytes:
    """A small JPEG carrying EXIF GPS and, optionally, a DJI XMP packet.

    Pillow cannot write XMP into a JPEG on every version, so the packet is
    spliced in as an APP1 segment directly - which is also what exercises
    `read_xmp`'s byte scanning rather than a Pillow code path.
    """
    image = Image.new("RGB", (200, 150), (90, 120, 60))
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "DJI", "FC3411"
    if gps:
        exif.get_ifd(0x8825).update({
            1: "N", 2: (48.0, 8.0, 13.92),
            3: "E", 4: (11.0, 34.0, 31.8),
            5: 0, 6: 612.3,
        })
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    content = buffer.getvalue()
    if not xmp:
        return content
    packet = XMP.format(pitch=pitch, yaw=yaw, focal=focal).encode("latin-1")
    segment = b"\xff\xe1" + (len(packet) + 2).to_bytes(2, "big") + packet
    # After SOI, before the rest - the position APP segments legitimately hold.
    return content[:2] + segment + content[2:]


def main() -> None:
    check_extraction()
    check_nadir_geometry()
    check_yaw_rotation()
    check_focal_chain()
    check_refusals()
    check_oblique()
    check_round_trip_into_ingest()
    print("Photogrammetry footprint checks passed.")


def check_round_trip_into_ingest() -> None:
    """A suggestion must be directly usable by the existing ingest path.

    This is the whole point of the feature: the corners it proposes have to
    satisfy `drone._corners` (four [lon, lat] pairs, non-self-intersecting) and
    produce a real georeferenced asset. A suggestion the operator cannot
    actually submit would be an elaborate way of doing nothing.
    """
    import dataclasses
    import tempfile

    from fastapi.testclient import TestClient

    from nexfiremap.api import create_app
    from nexfiremap.config import load_settings

    image = build_image()
    suggestion = pg.suggest(image)

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings = dataclasses.replace(load_settings(), db_path=root / "d.sqlite3",
                                       tile_cache_dir=root / "tiles", lan_mode=False)
        with TestClient(create_app(settings)) as client:
            incident = client.post("/api/operations/incidents", json={"name": "UAS"}).json()["incident"]
            mission = client.post(f"/api/operations/incidents/{incident['id']}/drone-missions",
                                  json={"name": "Pass 1"}).json()
            base = f"/api/operations/incidents/{incident['id']}/drone-missions/{mission['id']}"

            # The endpoint returns the same suggestion, and stores nothing.
            proposed = client.post(f"{base}/suggest-georeference", content=image)
            assert proposed.status_code == 200, proposed.text
            assert proposed.json()["corners"] == suggestion["corners"]
            assert proposed.json()["requires_confirmation"] is True
            assert client.get(f"{base}/assets").json() == [], "suggesting must not store an asset"

            # An image with no usable geometry is a 422 naming what is missing,
            # not a 500 and not a fabricated footprint.
            refused = client.post(f"{base}/suggest-georeference", content=build_image(xmp=False))
            assert refused.status_code == 422, refused.status_code
            assert "height" in refused.json()["detail"]

            # ...and the proposed corners go straight back through the ordinary
            # asset route, where georef_kind is still the operator's affirmation.
            created = client.post(
                f"{base}/assets",
                params={"filename": "DJI_0001.jpg", "corners": json.dumps(suggestion["corners"]),
                        "georef_kind": suggestion["georef_kind"]},
                content=image)
            assert created.status_code == 201, created.text
            asset = created.json()
            assert asset["georef_status"] == "operator_corners"
            assert asset["footprint"]["type"] == "Polygon"
            # The ring is closed by the ingest path, so five points for four corners.
            assert len(asset["footprint"]["coordinates"][0]) == 5


def check_extraction() -> None:
    metadata = pg.extract(build_image())
    # GPS out of EXIF's sexagesimal encoding, back to signed degrees.
    assert abs(metadata["latitude"] - LAT) < 1e-5, metadata["latitude"]
    assert abs(metadata["longitude"] - LON) < 1e-5, metadata["longitude"]
    assert metadata["model"] == "FC3411"
    # XMP read out of the raw bytes, including DJI's leading "+".
    assert metadata["relative_altitude"] == 100.0
    assert metadata["gimbal_pitch"] == -90.0
    assert metadata["calibrated_focal_px"] == 2000.0

    # A plain photograph with no drone metadata must come back near-empty
    # rather than raising - `suggest` is where absence becomes an error.
    plain = pg.extract(build_image(gps=False, xmp=False))
    assert "latitude" not in plain and "gimbal_pitch" not in plain
    assert plain["width"] and plain["height"]


def check_nadir_geometry() -> None:
    """A straight-down frame's footprint follows from the field of view.

    With focal = width/2 the half-angle across the width is exactly 45°, so at
    100 m the footprint spans 200 m east-west, and 3/4 of that north-south.
    """
    corners = pg.ground_corners(100.0, FOCAL_PX, WIDTH, HEIGHT, -90.0, 0.0)
    east = [x for x, _ in corners]
    north = [y for _, y in corners]
    assert abs((max(east) - min(east)) - 200.0) < 1e-6, max(east) - min(east)
    assert abs((max(north) - min(north)) - 150.0) < 1e-6, max(north) - min(north)
    # Centred on the aircraft, and image-up is north at yaw 0.
    assert abs(sum(east)) < 1e-9 and abs(sum(north)) < 1e-9
    assert corners[0][1] > 0 and corners[3][1] < 0, "image top must fall north of the camera"
    assert corners[0][0] < 0 and corners[1][0] > 0, "image left must fall west of the camera"

    suggestion = pg.suggest(build_image())
    assert suggestion["georef_kind"] == "nadir"
    assert suggestion["confidence"] == "high"
    assert suggestion["height_m"] == 100.0
    assert suggestion["requires_confirmation"] is True
    assert len(suggestion["corners"]) == 4
    assert all(len(point) == 2 for point in suggestion["corners"])

    # The corner ring must be in [lon, lat] order, GeoJSON's - the same order
    # drone.py's `_corners` validator expects.
    for longitude, latitude in suggestion["corners"]:
        assert abs(longitude - LON) < 0.01 and abs(latitude - LAT) < 0.01, (longitude, latitude)

    # The degrees that came back must agree with the metre-space geometry -
    # this is the check on offset_to_wgs84. The test JPEG is small, so its span
    # follows from its own pixel width: at nadir the footprint is exactly
    # width * height_m / focal_px across.
    with Image.open(io.BytesIO(build_image())) as probe:
        image_width = probe.width
    expected_m = image_width * 100.0 / FOCAL_PX
    longitudes = [point[0] for point in suggestion["corners"]]
    # A crude spherical constant, deliberately: agreeing with the module's
    # proper ellipsoidal radii to within 0.5% is the assertion, since a wrong
    # axis or a missing cos(latitude) would be out by far more than that.
    metres = (max(longitudes) - min(longitudes)) * 111_320 * math.cos(math.radians(LAT))
    assert abs(metres - expected_m) < expected_m * 0.005, (metres, expected_m)

    # An assumption an operator must see: flat ground.
    assert any("flat" in text.lower() for text in suggestion["assumptions"])


def check_yaw_rotation() -> None:
    """Yawing the camera 90° east must swing the footprint with it."""
    turned = pg.ground_corners(100.0, FOCAL_PX, WIDTH, HEIGHT, -90.0, 90.0)
    east = [x for x, _ in turned]
    north = [y for _, y in turned]
    # The long axis was east-west at yaw 0; at yaw 90 it is north-south.
    assert abs((max(east) - min(east)) - 150.0) < 1e-6, max(east) - min(east)
    assert abs((max(north) - min(north)) - 200.0) < 1e-6, max(north) - min(north)
    # Image-up now points east.
    assert turned[0][0] > 0 and turned[3][0] < 0

    # 180° must invert the frame, not reproduce it - a sign error in the yaw
    # rotation would leave the footprint looking correct but mirrored.
    flipped = pg.ground_corners(100.0, FOCAL_PX, WIDTH, HEIGHT, -90.0, 180.0)
    base = pg.ground_corners(100.0, FOCAL_PX, WIDTH, HEIGHT, -90.0, 0.0)
    assert all(abs(a[0] + b[0]) < 1e-9 and abs(a[1] + b[1]) < 1e-9
               for a, b in zip(base, flipped)), "yaw 180 must mirror the footprint through the camera"


def check_focal_chain() -> None:
    """Each route to a focal length works, and each is named in the result."""
    calibrated, route = pg.focal_pixels({"width": 4000, "calibrated_focal_px": 2000.0})
    assert calibrated == 2000.0 and "Calibrated" in route

    # 35mm equivalence is defined on the 36 mm frame width.
    equivalent, route = pg.focal_pixels({"width": 4000, "focal_length_35mm": 18.0})
    assert abs(equivalent - 2000.0) < 1e-9 and "35mm" in route

    # A known model's sensor width turns millimetres into pixels.
    known, route = pg.focal_pixels({"width": 5472, "focal_length_mm": 8.8, "model": "FC6310"})
    assert abs(known - 8.8 * 5472 / 13.2) < 1e-6 and "FC6310" in route

    # Focal-plane resolution, in inches (unit 2) - the general EXIF route.
    from_plane, route = pg.focal_pixels(
        {"width": 4000, "focal_length_mm": 10.0, "focal_plane_x_resolution": 10160.0, "focal_plane_unit": 2})
    assert abs(from_plane - 10.0 * 4000 / 10.0) < 1e-6, from_plane

    # An unknown camera with only millimetres is refused, not guessed.
    missing, reason = pg.focal_pixels({"width": 4000, "focal_length_mm": 8.8, "model": "NOT-A-REAL-CAM"})
    assert missing is None and "focal length" in reason


def check_refusals() -> None:
    """Every unbounded or unknowable case raises instead of inventing a shape."""

    def refuses(content: bytes, fragment: str, **kwargs) -> None:
        try:
            pg.suggest(content, **kwargs)
        except pg.PhotogrammetryError as exc:
            assert fragment in str(exc), f"expected {fragment!r}, got {exc}"
            return
        raise AssertionError(f"accepted an image it should have refused ({fragment})")

    refuses(build_image(gps=False), "no GPS position")
    # An ordinary geotagged photograph: it has a position and even a GPS
    # altitude, but no relative altitude and no gimbal attitude, so there is no
    # height above ground and nothing to point the camera with.
    refuses(build_image(xmp=False), "no height above ground")
    refuses(build_image(focal="0"), "camera scale")

    # A frame tilted to the horizon has no bounded footprint at all. This is
    # the gate that matters most: the maths still produces numbers here, just
    # enormous ones, so without the check it would return a confident-looking
    # rectangle kilometres across.
    refuses(build_image(pitch="-1.0"), "horizon")
    refuses(build_image(pitch="+10.0"), "horizon")

    # Height must come from somewhere real.
    metadata_only_absolute = build_image().replace(b'RelativeAltitude="+100.00"',
                                                   b'RelativeAltitude="+0.00"    ')
    refuses(metadata_only_absolute, "no height above ground")
    # ...but with terrain supplied, the same image resolves.
    resolved = pg.suggest(metadata_only_absolute, terrain_elevation_m=512.3)
    assert abs(resolved["height_m"] - 100.0) < 0.01, resolved["height_m"]
    assert resolved["height_source"].startswith("absolute")
    assert resolved["confidence"] == "medium", "a modelled height is less certain than a measured one"

    # Terrain above the aircraft is a contradiction, not a negative height.
    refuses(metadata_only_absolute, "at or below the terrain", terrain_elevation_m=900.0)

    # The primitives guard themselves too, independently of `suggest`.
    for bad in ((0.0, FOCAL_PX), (100.0, 0.0), (-5.0, FOCAL_PX)):
        try:
            pg.ground_corners(bad[0], bad[1], WIDTH, HEIGHT, -90.0, 0.0)
            raise AssertionError(f"ground_corners accepted {bad}")
        except pg.PhotogrammetryError:
            pass


def check_oblique() -> None:
    """An oblique frame is allowed, but reported as such and downgraded."""
    suggestion = pg.suggest(build_image(pitch="-45.00"))
    assert suggestion["georef_kind"] == "oblique"
    assert suggestion["confidence"] == "low"
    assert suggestion["tilt_from_nadir_deg"] == 45.0
    assert any("oblique" in text.lower() for text in suggestion["assumptions"])

    # The footprint must lie forward of the aircraft, not centred on it - a
    # tilted camera looks ahead. At yaw 0 that means north.
    northings = [point[1] for point in suggestion["corners"]]
    assert min(northings) > LAT, "an oblique frame's footprint must sit ahead of the camera"

    # And it must be a trapezoid, not a rectangle: the far edge is wider.
    corners = pg.ground_corners(100.0, FOCAL_PX, WIDTH, HEIGHT, -45.0, 0.0)
    far_width = abs(corners[1][0] - corners[0][0])
    near_width = abs(corners[2][0] - corners[3][0])
    assert far_width > near_width * 1.5, (far_width, near_width)


if __name__ == "__main__":
    main()
