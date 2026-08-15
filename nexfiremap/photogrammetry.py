"""Suggesting a drone image's ground footprint from its own metadata.

`drone.py` georeferences a frame from four corner points the operator affirms.
That is honest but laborious: a pilot who has just flown a pass has to read
coordinates off something and type them in four times per frame, during an
incident. Meanwhile the aircraft already recorded where it was, how high, and
where the camera was pointing - DJI writes all of it into every JPEG.

So this module reads that metadata and *proposes* a footprint. What it
deliberately does not do is commit one. `drone.ingest_image` still stores
`georef_kind` as an operator affirmation, and the suggestion carries its
assumptions and its confidence so a human can reject it. The failure mode of
silent auto-georeferencing is imagery that looks authoritative and sits in the
wrong field, which during an incident is worse than no imagery.

**What the geometry actually assumes.** Corner rays are cast from the camera
through the image corners onto a *horizontal plane* at the terrain height below
the aircraft. That is a real assumption and it is wrong on slopes: on ground
falling away from the camera the true footprint is larger than computed, and on
rising ground smaller. The error is roughly proportional to relief across the
frame divided by height above ground, so it is small for a high nadir shot over
gentle terrain and unbounded for a low oblique one across a valley. Rather than
pretend otherwise, `suggest` refuses outright when the geometry cannot support
a bounded answer, and downgrades `confidence` when it is merely poor:

* a ray at or above the horizon means the frame contains sky - unbounded, refused;
* an oblique frame is accepted but reported with its tilt, because the far edge
  of a tilted frame carries far more positional error than the near edge;
* no height above ground means no scale at all - refused, never guessed.

Only Pillow and the standard library are used; there is no photogrammetric
bundle adjustment here and no attempt at one. This is the same accuracy tier as
the existing four-corner path - it just fills the corners in.
"""

from __future__ import annotations

import io
import math
import re
from typing import Any

from PIL import Image

#: Ray directions this close to horizontal are treated as pointing at the
#: horizon. Ground intersection distance goes as 1/sin(depression), so at 3
#: degrees a corner already lands ~19 heights away - far past where the
#: flat-earth assumption below means anything.
MIN_DEPRESSION_DEG = 3.0

#: Beyond this tilt from straight down, a frame is reported as oblique. Not a
#: refusal: an oblique shot of a fire front is often exactly what was wanted.
#: It caps `confidence`, and the reason travels with the suggestion.
NADIR_TOLERANCE_DEG = 20.0

#: Sensor widths (mm) for drone models whose EXIF gives a focal length in mm
#: but no focal-plane resolution to turn it into pixels. Only models verifiable
#: from published specifications are listed; an unknown model falls through to
#: the 35mm-equivalent route or is refused, rather than being guessed at.
SENSOR_WIDTH_MM: dict[str, float] = {
    "FC220": 6.16,     # Mavic Pro
    "FC330": 6.17,     # Phantom 4
    "FC300X": 6.17,    # Phantom 3
    "FC6310": 13.2,    # Phantom 4 Pro (1")
    "FC7303": 6.16,    # Mavic Air 2 wide
    "FC3170": 6.16,    # Mavic Air 2
    "FC3411": 13.2,    # Air 2S (1")
    "FC3582": 9.7,     # Mini 3 Pro
    "L1D-20c": 13.2,   # Mavic 2 Pro (Hasselblad 1")
    "M3M": 17.3,       # Mavic 3 Multispectral
}

#: XMP tags DJI writes, mapped to the name used here. The XMP packet is read
#: out of the raw file rather than through Pillow's `getxmp()`, which is absent
#: on older Pillow builds and silently returns {} for some DJI writers.
_XMP_TAGS = {
    "AbsoluteAltitude": "absolute_altitude",
    "RelativeAltitude": "relative_altitude",
    "GimbalRollDegree": "gimbal_roll",
    "GimbalYawDegree": "gimbal_yaw",
    "GimbalPitchDegree": "gimbal_pitch",
    "FlightRollDegree": "flight_roll",
    "FlightYawDegree": "flight_yaw",
    "FlightPitchDegree": "flight_pitch",
    "CalibratedFocalLength": "calibrated_focal_px",
    "GpsLatitude": "xmp_latitude",
    "GpsLongitude": "xmp_longitude",
    "GpsLatitude ": "xmp_latitude",
}


class PhotogrammetryError(ValueError):
    """The image carries no usable geometry, or the geometry is unbounded."""


def _float(value: Any) -> float | None:
    """Best-effort float, tolerating DJI's leading '+' and stray whitespace."""
    if value is None:
        return None
    try:
        return float(str(value).strip().lstrip("+"))
    except (TypeError, ValueError):
        return None


def read_xmp(content: bytes) -> dict[str, Any]:
    """DJI's flight and gimbal attitude, read straight from the XMP packet.

    Scanning the bytes rather than parsing XML on purpose: the packet is
    attacker-influenced data from a file just uploaded, an XML parser on it is
    a needless attack surface, and DJI writes these fields as both attributes
    and elements depending on firmware. A regex for the handful of tags that
    matter reads both spellings and cannot be induced to fetch anything.
    """
    # The packet is ASCII inside a binary file; decode loosely and only once.
    window = content[:512_000]
    try:
        text = window.decode("latin-1", errors="ignore")
    except Exception:  # noqa: BLE001 - defensive; latin-1 does not normally raise
        return {}

    found: dict[str, Any] = {}
    for tag, name in _XMP_TAGS.items():
        tag_re = re.escape(tag.strip())
        match = re.search(rf'(?:drone-dji:)?{tag_re}\s*=\s*"([^"]*)"', text)
        if match is None:
            match = re.search(rf'<(?:drone-dji:)?{tag_re}>([^<]*)</', text)
        if match is None:
            continue
        value = _float(match.group(1))
        if value is not None:
            found[name] = value
    return found


def _gps_from_exif(exif: Any) -> tuple[float | None, float | None, float | None]:
    """Latitude, longitude and GPS altitude from EXIF, in signed degrees."""
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:  # noqa: BLE001 - malformed EXIF must not break ingest
        return None, None, None
    if not gps:
        return None, None, None

    def degrees(value: Any, ref: Any, negative: str) -> float | None:
        if not value:
            return None
        try:
            d, m, s = (float(part) for part in value)
        except (TypeError, ValueError):
            return None
        result = d + m / 60.0 + s / 3600.0
        return -result if str(ref).upper().startswith(negative) else result

    latitude = degrees(gps.get(2), gps.get(1), "S")
    longitude = degrees(gps.get(4), gps.get(3), "W")
    altitude = _float(gps.get(6))
    # GPSAltitudeRef 1 means "below sea level"; ignoring it puts an aircraft in
    # a Dutch polder 4 m too high, which is small but free to get right.
    if altitude is not None and str(gps.get(5) or "0").strip() in {"1", "b'\\x01'"}:
        altitude = -altitude
    return latitude, longitude, altitude


def extract(content: bytes) -> dict[str, Any]:
    """Everything relevant this image knows about where it was taken.

    Never raises for missing metadata - an ordinary photograph simply yields a
    near-empty dict. `suggest` is where absence becomes an error, so a caller
    can inspect what was found before deciding.
    """
    metadata: dict[str, Any] = {}
    try:
        with Image.open(io.BytesIO(content)) as image:
            metadata["width"], metadata["height"] = image.size
            exif = image.getexif()
    except Exception as exc:  # noqa: BLE001 - unreadable images are drone.py's business
        raise PhotogrammetryError("image could not be read for metadata") from exc

    if exif:
        latitude, longitude, altitude = _gps_from_exif(exif)
        if latitude is not None:
            metadata["latitude"] = latitude
        if longitude is not None:
            metadata["longitude"] = longitude
        if altitude is not None:
            metadata["gps_altitude"] = altitude
        metadata["make"] = str(exif.get(0x010F) or "").strip()
        metadata["model"] = str(exif.get(0x0110) or "").strip()
        captured = exif.get(0x0132) or exif.get(0x9003)
        if captured:
            metadata["captured_at_exif"] = str(captured).strip()
        ifd = {}
        try:
            ifd = exif.get_ifd(0x8769) or {}
        except Exception:  # noqa: BLE001
            ifd = {}
        for tag, name in ((0x920A, "focal_length_mm"), (0xA405, "focal_length_35mm"),
                          (0xA20E, "focal_plane_x_resolution"), (0xA210, "focal_plane_unit")):
            value = _float(ifd.get(tag))
            if value is not None:
                metadata[name] = value
        model = str(ifd.get(0xA434) or "").strip()
        if model:
            metadata["lens_model"] = model

    metadata.update(read_xmp(content))
    # XMP GPS is the same fix as EXIF's but written at full precision by some
    # firmware, so it wins where both exist.
    for source, target in (("xmp_latitude", "latitude"), ("xmp_longitude", "longitude")):
        if metadata.get(source) is not None:
            metadata[target] = metadata.pop(source)
    return metadata


def focal_pixels(metadata: dict[str, Any]) -> tuple[float | None, str]:
    """Focal length in pixels, and which route produced it.

    A fallback chain rather than one rule, because no single field is present
    across DJI firmware generations. The route is returned and stored with the
    suggestion: the routes differ in trustworthiness, and an operator judging a
    footprint deserves to know that its scale came from a hardcoded sensor
    width rather than from the aircraft's own calibration.
    """
    width = _float(metadata.get("width"))
    if not width:
        return None, "no image width"

    calibrated = _float(metadata.get("calibrated_focal_px"))
    if calibrated and calibrated > 0:
        # DJI's own calibration, already in pixels of the full-resolution
        # sensor. If the stored frame was downscaled, scale with it.
        return calibrated, "DJI CalibratedFocalLength"

    focal_mm = _float(metadata.get("focal_length_mm"))
    resolution = _float(metadata.get("focal_plane_x_resolution"))
    if focal_mm and resolution and resolution > 0:
        unit = int(metadata.get("focal_plane_unit") or 2)
        per_mm = {2: 25.4, 3: 10.0, 4: 1.0, 5: 0.001}.get(unit, 25.4)
        sensor_mm = width / (resolution / per_mm)
        if sensor_mm > 0:
            return focal_mm * width / sensor_mm, "EXIF focal length and focal-plane resolution"

    model = str(metadata.get("lens_model") or metadata.get("model") or "").strip()
    sensor_width = SENSOR_WIDTH_MM.get(model)
    if focal_mm and sensor_width:
        return focal_mm * width / sensor_width, f"EXIF focal length with known sensor width for {model}"

    equivalent = _float(metadata.get("focal_length_35mm"))
    if equivalent and equivalent > 0:
        # 35mm equivalence is defined on the 36mm frame width.
        return equivalent * width / 36.0, "EXIF 35mm-equivalent focal length"

    return None, "no focal length, sensor size or 35mm equivalent"


def _rotation(pitch_deg: float, yaw_deg: float) -> tuple[tuple[float, float, float], ...]:
    """Camera axes in world coordinates (X east, Y north, Z up).

    Built from the two angles that matter for a footprint. Roll is ignored on
    purpose: a gimbal holds roll at zero, and DJI reports it as such, so
    honouring a stray value would rotate the footprint on the strength of
    sensor noise. It is reported in the suggestion instead.
    """
    pitch = math.radians(pitch_deg)
    yaw = math.radians(yaw_deg)

    # Level camera looking north, then pitched about the east axis. At
    # pitch = -90 the axis points straight down and image-up points north.
    axis = (0.0, math.cos(pitch), math.sin(pitch))
    down = (0.0, math.sin(pitch), -math.cos(pitch))
    right = (1.0, 0.0, 0.0)

    def spin(vector: tuple[float, float, float]) -> tuple[float, float, float]:
        x, y, z = vector
        # Compass yaw runs clockwise from north, the opposite sense to the
        # mathematical convention, hence the sign arrangement here.
        return (x * math.cos(yaw) + y * math.sin(yaw),
                -x * math.sin(yaw) + y * math.cos(yaw),
                z)

    return spin(axis), spin(right), spin(down)


def ground_corners(height_m: float, focal_px: float, width: int, height: int,
                   pitch_deg: float, yaw_deg: float) -> list[tuple[float, float]]:
    """Where the four image corners land, in metres east/north of the camera.

    Casts a ray through each corner onto the horizontal plane `height_m` below
    the camera. Raises rather than returning a very large number when a ray
    approaches the horizon: past that point the intersection races off to
    infinity and any value returned would be a fiction with a plausible shape.

    Returned in image order - top-left, top-right, bottom-right, bottom-left -
    so the resulting ring stays consistently wound for the caller.
    """
    if height_m <= 0:
        raise PhotogrammetryError("height above ground must be positive")
    if focal_px <= 0:
        raise PhotogrammetryError("focal length must be positive")

    axis, right, down = _rotation(pitch_deg, yaw_deg)
    half_w, half_h = width / 2.0, height / 2.0
    limit = math.sin(math.radians(MIN_DEPRESSION_DEG))

    corners: list[tuple[float, float]] = []
    for u, v in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)):
        direction = tuple(axis[i] * focal_px + right[i] * u + down[i] * v for i in range(3))
        length = math.sqrt(sum(component * component for component in direction))
        if length == 0:
            raise PhotogrammetryError("degenerate camera geometry")
        dx, dy, dz = (component / length for component in direction)
        if dz > -limit:
            raise PhotogrammetryError(
                "the frame includes the horizon or sky, so its ground footprint is unbounded")
        scale = height_m / -dz
        corners.append((dx * scale, dy * scale))
    return corners


def offset_to_wgs84(latitude: float, longitude: float, east_m: float, north_m: float) -> list[float]:
    """A metre offset applied to a WGS84 point, as [lon, lat].

    A local flat approximation on the WGS84 ellipsoid's local radii. Good to
    well under a metre over the few hundred metres a drone frame covers, which
    is an order of magnitude finer than the footprint's own uncertainty.
    """
    phi = math.radians(latitude)
    # Meridional and normal radii of curvature (WGS84).
    a, e2 = 6378137.0, 6.69437999014e-3
    w = math.sqrt(1 - e2 * math.sin(phi) ** 2)
    meridional = a * (1 - e2) / (w ** 3)
    normal = a / w
    return [longitude + math.degrees(east_m / (normal * math.cos(phi))),
            latitude + math.degrees(north_m / meridional)]


def suggest(content: bytes, *, terrain_elevation_m: float | None = None) -> dict[str, Any]:
    """Propose a footprint for one drone image. Never commits anything.

    The return value is a suggestion an operator confirms or rejects: the four
    corners in the shape `drone.ingest_image` already accepts, plus the
    assumptions behind them, the height source, the focal-length route and a
    confidence. `assumptions` is the point of the whole return shape - a
    footprint with no stated basis invites exactly the misplaced trust this
    module's docstring warns about.

    :param content: The image file's bytes.
    :param terrain_elevation_m: Ground elevation under the aircraft, if known.
        Used only when the image reports absolute altitude and no relative one;
        the aircraft's own barometric relative altitude is preferred where
        present, being measured against the takeoff point rather than a model.
    :raises PhotogrammetryError: When the metadata cannot support a bounded
        footprint. The message says which piece was missing.
    """
    metadata = extract(content)
    width, height = metadata.get("width"), metadata.get("height")
    if not width or not height:
        raise PhotogrammetryError("image dimensions are unreadable")

    latitude, longitude = metadata.get("latitude"), metadata.get("longitude")
    if latitude is None or longitude is None:
        raise PhotogrammetryError("no GPS position in this image")
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        raise PhotogrammetryError("the recorded GPS position is out of range")

    assumptions: list[str] = []

    # Height above *ground*, which is what sets scale. Relative altitude is the
    # aircraft's own measurement from its takeoff point and is preferred; an
    # absolute altitude needs terrain beneath it to become a height at all.
    relative = _float(metadata.get("relative_altitude"))
    absolute = _float(metadata.get("absolute_altitude"))
    if absolute is None:
        absolute = _float(metadata.get("gps_altitude"))
    if relative is not None and relative > 0:
        height_m, height_source = relative, "barometric height above takeoff point"
        assumptions.append("Takeoff point and the imaged ground are at the same elevation.")
    elif absolute is not None and terrain_elevation_m is not None:
        height_m = absolute - terrain_elevation_m
        height_source = "absolute altitude minus terrain elevation"
        if height_m <= 0:
            raise PhotogrammetryError(
                "absolute altitude is at or below the terrain elevation - the height is not usable")
    else:
        raise PhotogrammetryError(
            "no height above ground: the image has neither a relative altitude nor "
            "an absolute altitude with terrain elevation to subtract")

    focal_px, focal_route = focal_pixels(metadata)
    if focal_px is None:
        raise PhotogrammetryError(f"camera scale cannot be established: {focal_route}")

    # Gimbal attitude. Pitch is the one that must be present: without it there
    # is no way to know whether the camera looked down or at the horizon, and
    # assuming nadir would fabricate a footprint for an oblique shot.
    pitch = _float(metadata.get("gimbal_pitch"))
    if pitch is None:
        raise PhotogrammetryError("no gimbal pitch: the camera's direction is unknown")
    yaw = _float(metadata.get("gimbal_yaw"))
    if yaw is None:
        yaw = _float(metadata.get("flight_yaw"))
        if yaw is None:
            raise PhotogrammetryError("no gimbal or flight yaw: the frame's orientation is unknown")
        assumptions.append("Camera heading taken from the aircraft's yaw, the gimbal's being absent.")

    corners_m = ground_corners(height_m, focal_px, int(width), int(height), pitch, yaw)
    corners = [offset_to_wgs84(latitude, longitude, east, north) for east, north in corners_m]

    tilt = abs(pitch + 90.0)
    oblique = tilt > NADIR_TOLERANCE_DEG
    roll = _float(metadata.get("gimbal_roll")) or 0.0

    assumptions.append("Ground is flat and horizontal across the frame; slopes shift the footprint.")
    if oblique:
        assumptions.append(
            f"Frame is oblique ({tilt:.0f}° from straight down) - the far edge is much less "
            "certain than the near edge.")
    if abs(roll) > 5.0:
        assumptions.append(f"Gimbal roll of {roll:.0f}° is ignored; the footprint is not rotated for it.")

    # Confidence is a coarse three-way judgement, not a probability. Anything
    # finer would imply a calibration this module does not have.
    if oblique or focal_route.startswith("EXIF 35mm"):
        confidence = "low"
    elif height_source.startswith("absolute") or focal_route.startswith("EXIF focal length with known"):
        confidence = "medium"
    else:
        confidence = "high"

    span_m = max(math.dist(corners_m[0], corners_m[2]), math.dist(corners_m[1], corners_m[3]))
    return {
        "corners": corners,
        "georef_kind": "nadir" if not oblique else "oblique",
        "confidence": confidence,
        "height_m": round(height_m, 2),
        "height_source": height_source,
        "focal_route": focal_route,
        "gimbal_pitch": pitch,
        "gimbal_yaw": yaw,
        "tilt_from_nadir_deg": round(tilt, 1),
        "ground_span_m": round(span_m, 1),
        "camera": {"make": metadata.get("make", ""), "model": metadata.get("model", "")},
        "captured_at": metadata.get("captured_at_exif"),
        "position": [longitude, latitude],
        "assumptions": assumptions,
        # Stated rather than implied: this is a proposal, and `drone.py` still
        # requires an operator to affirm the frame before it is written.
        "requires_confirmation": True,
    }


__all__ = ["MIN_DEPRESSION_DEG", "NADIR_TOLERANCE_DEG", "PhotogrammetryError", "SENSOR_WIDTH_MM",
           "extract", "focal_pixels", "ground_corners", "offset_to_wgs84", "read_xmp", "suggest"]
