"""NMEA 0183 parsing: coordinate conversion, checksums, fix validity, merging.

The reference sentences below are the canonical NMEA 0183 examples, whose
expected decimal values are published and can therefore be asserted against
something other than this codebase's own arithmetic.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nexfiremap.ingest import IngestError
from nexfiremap.ingest import nmea

# The canonical NMEA 0183 reference sentences, verbatim - including their
# published *6A / *47 checksums, which is what makes them a check against
# something external rather than against this codebase's own arithmetic.
# 4807.038,N = 48 deg 07.038 min = 48.1173 deg; 01131.000,E = 11 deg 31.000 min
# = 11.51667 deg; 022.4 knots = 41.48 km/h.
RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"


def _with_checksum(body: str) -> str:
    """Rebuild a sentence's ``*hh`` trailer, so fixtures stay valid when edited."""
    payload = body.lstrip("$").split("*")[0]
    checksum = 0
    for character in payload:
        checksum ^= ord(character)
    return f"${payload}*{checksum:02X}"


def check_coordinate_conversion() -> None:
    (report,) = nmea.parse(RMC.encode(), callsign="FL 11/1")
    # ddmm.mmmm, NOT decimal degrees. Reading 4807.038 as 48.07038 would be
    # about 8 km wrong and would still pass every range check.
    assert abs(report["latitude"] - 48.1173) < 1e-4, report["latitude"]
    assert abs(report["longitude"] - 11.516667) < 1e-5, report["longitude"]
    # Knots to km/h (stored rounded to 3 decimal places).
    assert abs(report["speed_kmh"] - 22.4 * 1.852) < 1e-3, report["speed_kmh"]
    assert abs(report["heading_deg"] - 84.4) < 1e-9
    assert report["observed_at"] == "1994-03-23T12:35:19.000Z", report["observed_at"]
    assert report["callsign"] == "FL 11/1"
    assert report["external_id"] == "FL 11/1@1994-03-23T12:35:19.000Z"
    assert report["nmea_date_source"] == "sentence"


def check_two_digit_year_window() -> None:
    """NMEA has no century field. A flat "2000 + yy" reads the standard's own
    1994 reference sentence as 2094 and would file a replayed historical track
    a century in the future."""
    modern = _with_checksum("GPRMC,123519,A,4807.038,N,01131.000,E,0,0,140826,,")
    (report,) = nmea.parse(modern.encode(), callsign="X")
    assert report["observed_at"].startswith("2026-08-14"), report["observed_at"]

    legacy = _with_checksum("GPRMC,123519,A,4807.038,N,01131.000,E,0,0,230394,,")
    (report,) = nmea.parse(legacy.encode(), callsign="X")
    assert report["observed_at"].startswith("1994-03-23"), report["observed_at"]


def check_southern_western_hemispheres() -> None:
    south = _with_checksum("GPRMC,123519,A,3352.000,S,15112.000,E,000.0,000.0,230394,,")
    (report,) = nmea.parse(south.encode(), callsign="X")
    assert report["latitude"] < 0 and abs(report["latitude"] + 33.86667) < 1e-4
    assert report["longitude"] > 0 and abs(report["longitude"] - 151.2) < 1e-4

    west = _with_checksum("GPRMC,123519,A,4030.000,N,07400.000,W,000.0,000.0,230394,,")
    (report,) = nmea.parse(west.encode(), callsign="X")
    assert report["longitude"] < 0 and abs(report["longitude"] + 74.0) < 1e-6


def check_gga_fields() -> None:
    (report,) = nmea.parse(GGA.encode(), callsign="X")
    assert report["altitude_m"] == 545.4
    assert report["accuracy_m"] == 4.5, "HDOP 0.9 x 5 m nominal UERE"
    assert report["speed_kmh"] is None and report["heading_deg"] is None
    assert report["nmea_satellites"] == "08"
    # No RMC anywhere, so the calendar day came from the server clock - which
    # is wrong across a midnight boundary and must be recorded as such.
    assert report["nmea_date_source"] == "server_clock"
    assert report["observed_at"].startswith(datetime.now(timezone.utc).strftime("%Y-%m-%d"))


def check_burst_merges_and_dates() -> None:
    """A receiver emits GGA and RMC for the same fix. They must merge into one
    report carrying both sentences' information, not produce two near-identical
    positions that would double every track."""
    burst = "\r\n".join([
        "$GPGSA,A,3,04,05,,09,12,,,24,,,,,2.5,1.3,2.1*39",  # ignored, not a position
        GGA,
        RMC,
    ])
    (report,) = nmea.parse(burst.encode(), callsign="FL 11/1")
    assert sorted(report["nmea_sentences"]) == ["GGA", "RMC"]
    # RMC's speed/course AND GGA's altitude/accuracy - strictly more than
    # either sentence carries alone.
    assert report["speed_kmh"] is not None and report["altitude_m"] == 545.4
    assert report["accuracy_m"] == 4.5
    # The RMC's date must be applied to the GGA, which has none of its own.
    assert report["observed_at"] == "1994-03-23T12:35:19.000Z"
    assert report["nmea_date_source"] == "sentence"


def check_multiple_fixes_ordered() -> None:
    later = _with_checksum("GPRMC,123619.00,A,4808.038,N,01132.000,E,030.0,090.0,230394,,")
    reports = nmea.parse((RMC + "\n" + later).encode(), callsign="X")
    assert len(reports) == 2
    assert [r["observed_at"] for r in reports] == sorted(r["observed_at"] for r in reports)
    assert len({r["external_id"] for r in reports}) == 2, "distinct fixes need distinct replay keys"


def check_invalid_fixes_skipped() -> None:
    # RMC status V = void. Still carries coordinates, which would otherwise be
    # stored as a current position.
    void = _with_checksum("GPRMC,123519,V,4807.038,N,01131.000,E,000.0,000.0,230394,,")
    try:
        nmea.parse(void.encode(), callsign="X")
        raise AssertionError("a void RMC was ingested")
    except IngestError:
        pass

    # GGA fix quality 0 = no fix; 6 = dead reckoning, an estimate not an
    # observation. Neither may be recorded as an observed position.
    for quality in ("0", "6"):
        sentence = _with_checksum(f"GPGGA,123519,4807.038,N,01131.000,E,{quality},08,0.9,545.4,M,46.9,M,,")
        try:
            nmea.parse(sentence.encode(), callsign="X")
            raise AssertionError(f"GGA fix quality {quality} was ingested")
        except IngestError:
            pass

    # ...but a void sentence mixed with a good one must not discard the good one.
    assert len(nmea.parse((void + "\n" + RMC).encode(), callsign="X")) == 1


def check_checksum_enforced() -> None:
    # A corrupted line over a flaky serial/radio link must be rejected, not
    # ingested as a position the vehicle was never at.
    corrupt = RMC.replace("4807.038", "4907.038")  # checksum no longer matches
    try:
        nmea.parse(corrupt.encode(), callsign="X")
        raise AssertionError("a corrupted sentence was accepted")
    except IngestError as exc:
        assert "checksum" in str(exc)

    # A missing checksum is legal in NMEA 0183 and some receivers omit it.
    no_checksum = RMC.split("*")[0]
    assert nmea.parse(no_checksum.encode(), callsign="X")


def check_talker_ids() -> None:
    """Modern multi-constellation receivers emit GN, not GP. Matching the
    talker id rather than the last three characters would silently ignore
    every sentence from a current GNSS chip."""
    for talker in ("GP", "GN", "GL", "GA", "GB"):
        sentence = _with_checksum(f"{talker}RMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,,")
        (report,) = nmea.parse(sentence.encode(), callsign="X")
        assert abs(report["latitude"] - 48.1173) < 1e-4, talker


def check_rejections() -> None:
    for label, payload, kwargs in (
        ("no callsign", RMC.encode(), {"callsign": ""}),
        ("empty", b"   ", {"callsign": "X"}),
        ("no position sentence", b"$GPGSV,3,1,11,04,44,127,42*7B", {"callsign": "X"}),
        ("bad minutes", _with_checksum("GPRMC,123519,A,4877.038,N,01131.000,E,0,0,230394,,").encode(), {"callsign": "X"}),
        ("truncated coordinate", _with_checksum("GPRMC,123519,A,7.0,N,01131.000,E,0,0,230394,,").encode(), {"callsign": "X"}),
        ("bad time", _with_checksum("GPRMC,12,A,4807.038,N,01131.000,E,0,0,230394,,").encode(), {"callsign": "X"}),
    ):
        try:
            nmea.parse(payload, **kwargs)
            raise AssertionError(f"{label} was accepted")
        except IngestError:
            pass


def check_contract_shape() -> None:
    """The output must be exactly what TelemetryManager.ingest reads, or an
    NMEA batch would take a different validation path from a JSON one."""
    (report,) = nmea.parse(RMC.encode(), callsign="X")
    for key in ("external_id", "callsign", "observed_at", "latitude", "longitude",
                "altitude_m", "speed_kmh", "heading_deg", "accuracy_m"):
        assert key in report, key
    assert nmea.CONTRACT == "position"


def main() -> None:
    check_coordinate_conversion()
    check_two_digit_year_window()
    check_southern_western_hemispheres()
    check_gga_fields()
    check_burst_merges_and_dates()
    check_multiple_fixes_ordered()
    check_invalid_fixes_skipped()
    check_checksum_enforced()
    check_talker_ids()
    check_rejections()
    check_contract_shape()
    print("NMEA 0183 checks passed.")


if __name__ == "__main__":
    main()
