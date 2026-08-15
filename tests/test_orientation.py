"""North-south orientation of every raster and contour the map draws.

This file exists because two mirroring bugs shipped and the whole test suite
stayed green. The suite checks properties - ordering, monotonicity, mass
conservation - and every one of those is invariant under a vertical flip. A
mirrored isochrone is still monotone in time; a mirrored heat raster still sums
to the same mass. Only an assertion that ties a *known feature* to a *known
hemisphere of the grid* can catch it, so that is what every check below does:
put the signal off-centre, then assert it comes out on the side it went in.

The two conventions are both legitimate and both still in use (see geo.py):
`likelihood`'s grids are south-first, `terrain`'s are north-first. What is
tested here is that each output declares which it is and is converted once,
correctly - not that they agree on a convention, which they do not.
"""

from __future__ import annotations

import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from nexfiremap.geo import (
    ROW_ORIGIN_NORTH,
    ROW_ORIGIN_SOUTH,
    grid_geometry,
    row_to_lat,
    to_north_first,
)

BBOX = (10.0, 47.0, 11.0, 48.0)  # west, south, east, north


def main() -> None:
    check_helpers()
    check_png_rows()
    check_likelihood_raster_and_contours()
    check_terrain_isochrones()
    print("Raster/contour orientation checks passed.")


def check_helpers() -> None:
    """The primitives themselves, since everything below leans on them."""
    # Row 0 is whichever edge the caller names; the last row is the other.
    assert row_to_lat(BBOX, 0, 100, origin=ROW_ORIGIN_NORTH) == 48.0
    assert row_to_lat(BBOX, 100, 100, origin=ROW_ORIGIN_NORTH) == 47.0
    assert row_to_lat(BBOX, 0, 100, origin=ROW_ORIGIN_SOUTH) == 47.0
    assert row_to_lat(BBOX, 100, 100, origin=ROW_ORIGIN_SOUTH) == 48.0
    # Fractional rows are what marching squares actually returns.
    assert row_to_lat(BBOX, 25, 100, origin=ROW_ORIGIN_NORTH) == 47.75

    # The two conventions must be genuine mirrors of each other, or one of
    # them is wrong in a way the individual checks above would not show.
    for row in (0, 10, 33.5, 99, 100):
        north_first = row_to_lat(BBOX, row, 100, origin=ROW_ORIGIN_NORTH)
        south_first = row_to_lat(BBOX, 100 - row, 100, origin=ROW_ORIGIN_SOUTH)
        assert abs(north_first - south_first) < 1e-12, row

    ramp = np.arange(4, dtype=np.float64).reshape(4, 1)
    assert to_north_first(ramp, origin=ROW_ORIGIN_NORTH)[0, 0] == 0.0, "north-first must pass through"
    assert to_north_first(ramp, origin=ROW_ORIGIN_SOUTH)[0, 0] == 3.0, "south-first must flip"

    # An unnamed or misspelled origin must raise rather than pick one. The
    # whole point of the helper is that guessing is what caused the bugs.
    for bad in ("up", "", None):
        try:
            row_to_lat(BBOX, 0, 10, origin=bad)
            raise AssertionError(f"row_to_lat accepted origin={bad!r}")
        except ValueError:
            pass
        try:
            to_north_first(ramp, origin=bad)
            raise AssertionError(f"to_north_first accepted origin={bad!r}")
        except ValueError:
            pass


def _png_rows(png: bytes, width: int, height: int) -> np.ndarray:
    """Decode a PNG produced by rasterpng.encode_png back to RGBA rows.

    Hand-decoded rather than via Pillow so the check reads the actual bytes
    that go to the browser: filter bytes stripped, rows in file order. The
    encoder emits filter type 0 on every row, which this asserts.
    """
    idat = b""
    offset = 8
    while offset < len(png):
        length = int.from_bytes(png[offset:offset + 4], "big")
        tag = png[offset + 4:offset + 8]
        if tag == b"IDAT":
            idat += png[offset + 8:offset + 8 + length]
        offset += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4 + 1
    assert len(raw) == stride * height, (len(raw), stride * height)
    rows = []
    for index in range(height):
        chunk = raw[index * stride:(index + 1) * stride]
        assert chunk[0] == 0, "expected filter type 0"
        rows.append(np.frombuffer(chunk[1:], dtype=np.uint8).reshape(width, 4))
    return np.array(rows)


def check_png_rows() -> None:
    """A PNG's first row must be the north edge, whatever the grid's order.

    This is the assertion the mirrored likelihood rasters would have failed:
    PNG rows are stored top-to-bottom and Leaflet paints the first one at the
    northern edge of the overlay bounds, so "north-first" is not a convention
    to choose - it is what the format and the renderer already mean.
    """
    from nexfiremap.likelihood import render_probability_png

    height, width = 8, 4
    # A signal in one hemisphere only: hot along one edge, zero elsewhere.
    hot_at_row0 = np.zeros((height, width), dtype=np.float64)
    hot_at_row0[0, :] = 1.0

    # Declared north-first: row 0 is north, so the PNG's first row is the hot one.
    rows = _png_rows(render_probability_png(hot_at_row0, origin=ROW_ORIGIN_NORTH), width, height)
    assert rows[0, 0, 3] > rows[-1, 0, 3], "north-first raster must keep its row 0 at the top"

    # Declared south-first: row 0 is south, so it must end up at the *bottom*.
    rows = _png_rows(render_probability_png(hot_at_row0, origin=ROW_ORIGIN_SOUTH), width, height)
    assert rows[-1, 0, 3] > rows[0, 0, 3], "south-first raster must be flipped before encoding"

    # Same for the recency ramp, which has its own normalisation path and so
    # could regress independently.
    from nexfiremap.likelihood import render_recency_png

    fresh_at_row0 = np.full((height, width), 100.0)
    fresh_at_row0[0, :] = 0.0
    rows = _png_rows(render_recency_png(fresh_at_row0, origin=ROW_ORIGIN_SOUTH), width, height)
    assert rows[-1, 0, 3] > rows[0, 0, 3], "south-first recency raster must be flipped"
    rows = _png_rows(render_recency_png(fresh_at_row0, origin=ROW_ORIGIN_NORTH), width, height)
    assert rows[0, 0, 3] > rows[-1, 0, 3], "north-first recency raster must pass through"

    # Neither renderer may be callable without saying which end row 0 is.
    for render in (render_probability_png, render_recency_png):
        try:
            render(hot_at_row0)
            raise AssertionError(f"{render.__name__} rendered without an origin")
        except TypeError:
            pass


def check_likelihood_raster_and_contours() -> None:
    """A detection in the north half must produce heat and contours up there.

    End to end through the real kernel, so it covers the grid construction as
    well as the rendering: if `grid_xy_m` ever changed which edge row 0 is,
    this fails even though every helper above still passes.
    """
    from nexfiremap.likelihood import active_heat_raster, probability_envelopes

    geom = grid_geometry(BBOX, desired_res_m=2000.0)
    north_lat = 47.8  # well into the northern half of the bbox
    detections = [{"lat": north_lat, "lon": 10.5, "ts": 1_760_000_000.0,
                   "frp": 50.0, "confidence": "high", "satellite": "N", "instrument": "VIIRS"}]

    raster = active_heat_raster(detections, geom, reference_ts=1_760_000_100.0)
    assert raster.shape == (geom["ny"], geom["nx"])
    assert raster.max() > 0, "the kernel produced no heat at all"

    # Grids here are south-first, so a northern detection peaks in the *upper*
    # half of the row index.
    peak_row = int(np.unravel_index(np.argmax(raster), raster.shape)[0])
    peak_lat = row_to_lat(BBOX, peak_row, geom["ny"], origin=ROW_ORIGIN_SOUTH)
    assert abs(peak_lat - north_lat) < 0.1, f"heat peaked at {peak_lat}, detection was at {north_lat}"

    # ...and the contours drawn from it must enclose the same place.
    envelopes = probability_envelopes(raster, geom)
    assert envelopes, "no probability envelopes produced"
    lats = [point[1] for feature in envelopes
            for point in feature["geometry"]["coordinates"][0]]
    assert abs(sum(lats) / len(lats) - north_lat) < 0.15, \
        f"envelopes centred on {sum(lats) / len(lats)}, detection was at {north_lat}"


def check_terrain_isochrones() -> None:
    """An arrival-time low in the north must contour in the north.

    `isochrone_contours` reads a north-first grid. It previously used the
    south-first formula, mirroring every isochrone about the AOI's centre
    latitude while the PNG from the same array stayed correct - so the vector
    and raster layers of one job disagreed with each other.
    """
    from nexfiremap.terrain import isochrone_contours

    geom = grid_geometry(BBOX, desired_res_m=2000.0)
    ny, nx = geom["ny"], geom["nx"]

    # Travel time rising with distance from a source in the north half. Row 0
    # is north here, so the source sits at a low row index.
    source_row = ny // 5
    rows = np.arange(ny)[:, None]
    cols = np.arange(nx)[None, :]
    distance = np.sqrt((rows - source_row) ** 2 + (cols - nx / 2) ** 2)
    travel_time_s = distance * 600.0  # 10 min per cell

    features = isochrone_contours(travel_time_s, geom, hours=(1.0, 2.0))
    assert features, "no isochrones produced"
    lats = [point[1] for feature in features
            for point in feature["geometry"]["coordinates"]
            if feature["geometry"]["type"] == "LineString"]
    assert lats, "no isochrone linestrings produced"

    source_lat = row_to_lat(BBOX, source_row, ny, origin=ROW_ORIGIN_NORTH)
    centre = sum(lats) / len(lats)
    assert abs(centre - source_lat) < 0.12, \
        f"isochrones centred on {centre}, source was at {source_lat} (mirrored?)"
    # The sharpest form of the same check: the source is north of the bbox
    # centre, so the isochrones must be too. A mirrored result lands south of
    # it, which is what shipped.
    assert centre > (BBOX[1] + BBOX[3]) / 2.0, "isochrones fell in the wrong hemisphere"


if __name__ == "__main__":
    main()
