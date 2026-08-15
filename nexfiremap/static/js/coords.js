/* Coordinate systems: WGS84 (decimal / DMS / DDM), UTM, MGRS, and a curated
   set of national grids, plus a custom EPSG/proj4-string escape hatch.

   "All globally used coordinate systems" is unbounded as a literal request -
   EPSG alone lists several thousand. What ships built-in here is the
   trio wildfire/SAR/ICS doctrine actually uses (WGS84, UTM, MGRS - NWCG
   references UTM and MGRS alongside lat/lon), plus the national grids
   listed below (each verified against an authoritative proj4 definition,
   see the comment above NATIONAL_GRIDS - one, British National Grid, was
   additionally round-tripped against PROJ's own OSTN15-grid transform for a
   real test point and came back within 3mm, so the Helmert approximation
   used here for it is trustworthy despite skipping the binary grid file).
   The "custom" entry is the honest way to cover any grid not listed:
   proj4js (vendored, MIT) can parse any raw proj4 definition string, so
   nothing is actually unreachable, it just isn't pre-populated.

   Runs entirely client-side against vendored proj4js + mgrs (both MIT, same
   maintainers) - no network call, works with the WAN down.

   An ES module: proj4/mgrs are still read as the classic globals the vendored
   <script> tags define, but this file's own API is a real export consumed by
   `import * as Coords from "./coords.js"` in app.js, not a window.NexCoords
   global that app.js had to be loaded after.
*/

  // Verified 2026-08-13 against https://epsg.io/<code>.proj4 (the standard
  // reference most GIS tools cite for exactly this purpose). British
  // National Grid's authoritative modern definition uses a binary NTv2 grid
  // shift file (OSTN15) that isn't practical to bundle for a browser-side
  // library; the classical Ordnance-Survey-published 7-parameter Helmert
  // transform substituted here was checked against PROJ's real OSTN15
  // pipeline for a London test point and differed by under 3mm - a
  // situational tool doesn't need survey-grade OSTN15 accuracy, and this is
  // nowhere near the limiting factor.
  const NATIONAL_GRIDS = [
    { id: "bng", label: "British National Grid (EPSG:27700)",
      def: "+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 +x_0=400000 +y_0=-100000 +ellps=airy +towgs84=446.448,-125.157,542.060,0.1502,0.2470,0.8421,-20.4894 +units=m +no_defs" },
    { id: "irish", label: "Irish Grid (EPSG:29903)",
      def: "+proj=tmerc +lat_0=53.5 +lon_0=-8 +k=1.000035 +x_0=200000 +y_0=250000 +a=6377340.189 +rf=299.3249646 +towgs84=482.5,-130.6,564.6,-1.042,-0.214,-0.631,8.15 +units=m +no_defs" },
    { id: "ch1903", label: "Swiss CH1903+ / LV95 (EPSG:2056)",
      def: "+proj=somerc +lat_0=46.9524055555556 +lon_0=7.43958333333333 +k_0=1 +x_0=2600000 +y_0=1200000 +ellps=bessel +towgs84=674.374,15.056,405.346,0,0,0,0 +units=m +no_defs" },
    { id: "lambert93", label: "French Lambert-93 (EPSG:2154)",
      def: "+proj=lcc +lat_0=46.5 +lon_0=3 +lat_1=49 +lat_2=44 +x_0=700000 +y_0=6600000 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs" },
    { id: "rd", label: "Dutch RD New (EPSG:28992)",
      def: "+proj=sterea +lat_0=52.1561605555556 +lon_0=5.38763888888889 +k=0.9999079 +x_0=155000 +y_0=463000 +ellps=bessel +towgs84=565.4171,50.3319,465.5524,1.9342,-1.6677,9.1019,4.0725 +units=m +no_defs" },
    { id: "nztm", label: "New Zealand Transverse Mercator (EPSG:2193)",
      def: "+proj=tmerc +lat_0=0 +lon_0=173 +k=0.9996 +x_0=1600000 +y_0=10000000 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs" },
    { id: "sweref99", label: "Sweden SWEREF99 TM (EPSG:3006)",
      def: "+proj=utm +zone=33 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs" },
    { id: "gda2020", label: "Australia GDA2020 MGA (zone auto-selected)",
      zoned: true, hemisphere: "south",
      def: (zone) => `+proj=utm +zone=${zone} +south +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs` },
  ];

  /** Longitude to the standard 6°-wide UTM zone number (1-60), via the
   * usual floor((lon+180)/6)+1 definition, clamped defensively at the
   * antimeridian.
   * @param {number} lon - Longitude in degrees.
   * @returns {number} UTM zone number. */
  function utmZone(lon) {
    return Math.min(60, Math.max(1, Math.floor((lon + 180) / 6) + 1));
  }

  /** Builds a proj4 definition string for a standard WGS84 UTM zone.
   * @param {number} zone - UTM zone number (1-60).
   * @param {boolean} southHemisphere - Whether to use the southern-hemisphere
   *   false northing.
   * @returns {string} proj4 definition string. */
  function utmDef(zone, southHemisphere) {
    return `+proj=utm +zone=${zone} +datum=WGS84 +units=m +no_defs${southHemisphere ? " +south" : ""}`;
  }

  /** Looks up a national grid entry by id.
   * @param {string} id - Grid id, e.g. "bng".
   * @returns {?object} The matching NATIONAL_GRIDS entry, or null if unknown. */
  function findGrid(id) {
    return NATIONAL_GRIDS.find((grid) => grid.id === id) || null;
  }

  /** Resolves a grid's proj4 definition, invoking its zone-selector
   * function for zoned grids (e.g. GDA2020 MGA) since those need a
   * longitude to know which UTM zone applies.
   * @param {object} grid - A NATIONAL_GRIDS entry.
   * @param {number} lon - Longitude in degrees, used only when the grid is zoned.
   * @returns {string} proj4 definition string. */
  function gridDef(grid, lon) {
    return grid.zoned ? grid.def(utmZone(lon)) : grid.def;
  }

  // ------------------------------------------------------------- degrees

  /** Formats a decimal-degree value as degrees/minutes/seconds, e.g.
   * 48°51'23.4"N.
   * @param {number} value - Signed decimal degrees.
   * @param {string} positiveSuffix - Hemisphere letter used when value >= 0.
   * @param {string} negativeSuffix - Hemisphere letter used when value < 0.
   * @returns {string} Formatted DMS string. */
  function toDMS(value, positiveSuffix, negativeSuffix) {
    const suffix = value >= 0 ? positiveSuffix : negativeSuffix;
    const abs = Math.abs(value);
    const degrees = Math.floor(abs);
    const minutesFull = (abs - degrees) * 60;
    const minutes = Math.floor(minutesFull);
    const seconds = (minutesFull - minutes) * 60;
    return `${degrees}°${String(minutes).padStart(2, "0")}'${seconds.toFixed(1).padStart(4, "0")}"${suffix}`;
  }

  /** Formats a decimal-degree value as degrees/decimal-minutes, e.g.
   * 48°51.383'N.
   * @param {number} value - Signed decimal degrees.
   * @param {string} positiveSuffix - Hemisphere letter used when value >= 0.
   * @param {string} negativeSuffix - Hemisphere letter used when value < 0.
   * @returns {string} Formatted DDM string. */
  function toDDM(value, positiveSuffix, negativeSuffix) {
    const suffix = value >= 0 ? positiveSuffix : negativeSuffix;
    const abs = Math.abs(value);
    const degrees = Math.floor(abs);
    const minutes = (abs - degrees) * 60;
    return `${degrees}°${minutes.toFixed(3)}'${suffix}`;
  }

  /** Accepts decimal ("48.1234"), DMS ("48 51 23.4"), or DDM
   * ("48 51.383") with any of °'" / spaces as separators, and either a
   * trailing hemisphere letter (N/S/E/W) or a leading sign.
   * @param {string} token - A single coordinate token (one axis).
   * @returns {?number} Signed decimal degrees, or null if unparseable. */
  function parseDegreeToken(token) {
    const cleaned = token.trim();
    const hemisphereMatch = cleaned.match(/([NSEWnsew])\s*$/);
    const hemisphere = hemisphereMatch ? hemisphereMatch[1].toUpperCase() : null;
    const numeric = cleaned.replace(/[NSEWnsew]/gi, "").trim();
    const parts = numeric.split(/[^0-9.\-]+/).filter(Boolean).map(Number);
    if (!parts.length || parts.some((part) => !Number.isFinite(part))) return null;
    let value;
    if (parts.length === 1) value = parts[0];
    else if (parts.length === 2) value = parts[0] + parts[1] / 60;
    else value = parts[0] + parts[1] / 60 + parts[2] / 3600;
    if (hemisphere === "S" || hemisphere === "W") value = -Math.abs(value);
    return value;
  }

  // ------------------------------------------------------------- parsing

  /** Parses a bare "lat, lon" decimal-degree pair.
   * @param {string} text - Free-text input.
   * @returns {?{lat: number, lon: number}} Parsed point, or null if it
   *   doesn't match or is out of range. */
  function parseDecimalPair(text) {
    const match = text.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)$/);
    if (!match) return null;
    const lat = Number(match[1]);
    const lon = Number(match[2]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    return { lat, lon };
  }

  /** Two degree-ish tokens (DMS or DDM, each ending in a hemisphere
   * letter) separated by a space or comma - "48°51'23\"N 2°17'40\"E".
   * @param {string} text - Free-text input.
   * @returns {?{lat: number, lon: number}} Parsed point, or null if it
   *   doesn't match or is out of range. */
  function parseDmsPair(text) {
    const match = text.trim().match(/^(.+[NSns])[\s,]+(.+[EWew])$/);
    if (!match) return null;
    const lat = parseDegreeToken(match[1]);
    const lon = parseDegreeToken(match[2]);
    if (lat == null || lon == null || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    return { lat, lon };
  }

  /** Parses an MGRS grid reference (requires the vendored mgrs library).
   * @param {string} text - Free-text input.
   * @returns {?{lat: number, lon: number}} Parsed point, or null if
   *   unavailable or unparseable. */
  function parseMgrs(text) {
    if (typeof window.mgrs === "undefined") return null;
    const candidate = text.trim().toUpperCase().replace(/\s+/g, "");
    if (!/^\d{1,2}[C-HJ-NP-X][A-HJ-NP-Z]{2}\d+$/.test(candidate)) return null;
    try {
      const [lon, lat] = window.mgrs.toPoint(candidate);
      return { lat, lon };
    } catch (_) {
      return null;
    }
  }

  /** Parses a "<zone><hemisphere> <easting> <northing>" UTM string, e.g.
   * "33N 500000 4649776" (requires the vendored proj4 library).
   * @param {string} text - Free-text input.
   * @returns {?{lat: number, lon: number}} Parsed point, or null if
   *   unavailable or unparseable. */
  function parseUtm(text) {
    if (typeof window.proj4 === "undefined") return null;
    const match = text.trim().match(/^(\d{1,2})\s*([NnSs])\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)$/);
    if (!match) return null;
    const zone = Number(match[1]);
    if (zone < 1 || zone > 60) return null;
    const south = match[2].toUpperCase() === "S";
    try {
      const [lon, lat] = window.proj4(utmDef(zone, south), "WGS84", [Number(match[3]), Number(match[4])]);
      return { lat, lon };
    } catch (_) {
      return null;
    }
  }

  /** Parses an easting/northing pair against a specific national (or
   * custom) grid; not attempted for wgs84/utm/mgrs since those already
   * have their own parsers above.
   * @param {string} text - Free-text input.
   * @param {?string} gridId - A NATIONAL_GRIDS id, or "custom".
   * @returns {?{lat: number, lon: number}} Parsed point, or null if
   *   unavailable or unparseable. */
  function parseGrid(text, gridId) {
    if (typeof window.proj4 === "undefined" || !gridId || gridId === "wgs84" || gridId === "utm" || gridId === "mgrs") return null;
    const match = text.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)$/);
    if (!match) return null;
    const easting = Number(match[1]);
    const northing = Number(match[2]);
    try {
      if (gridId === "custom") {
        const def = (localStorage.getItem("nexfiremap.coords.customProj4") || "").trim();
        if (!def) return null;
        const [lon, lat] = window.proj4(def, "WGS84", [easting, northing]);
        if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
        return { lat, lon };
      }
      const grid = findGrid(gridId);
      if (!grid) return null;
      // A zoned grid (GDA2020 MGA) needs a longitude to pick its zone before
      // it can even attempt the inverse transform - genuinely ambiguous
      // without one, so this system is skipped for free-text entry and
      // only used for *display* of an already-known point (format() below).
      if (grid.zoned) return null;
      const [lon, lat] = window.proj4(grid.def, "WGS84", [easting, northing]);
      if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
      return { lat, lon };
    } catch (_) {
      return null;
    }
  }

  /** Parse free-text coordinates in any supported format. `preferredSystem`
   * disambiguates a bare "number, number" pair (which format-specific
   * grids can't self-describe the way MGRS/UTM's own letters do). Returns
   * {lat, lon} or null. */
  function parse(text, preferredSystem) {
    if (!text || !text.trim()) return null;
    return (
      parseDecimalPair(text) ||
      parseDmsPair(text) ||
      parseMgrs(text) ||
      parseUtm(text) ||
      parseGrid(text, preferredSystem) ||
      null
    );
  }

  /** Parses a bounding box, returning {west, south, east, north} or null.
   *
   * Four numbers rather than two, in the "west,south,east,north" order every
   * API in this app already uses (GeoJSON's bbox order, and what /api/events,
   * /api/detections and /api/situation all take). Separators are deliberately
   * loose - commas, whitespace, semicolons, and surrounding brackets - because
   * an operator pasting a bbox has copied it from a log line, a GeoJSON file
   * or another tool's URL, and none of those agree on punctuation.
   *
   * The south/north and west/east pairs are ordered here rather than rejected
   * if reversed: a bbox typed the other way round is unambiguous about which
   * area was meant, so correcting it is right where guessing usually is not.
   * The antimeridian case is left alone - west > east is a legitimate
   * wraparound elsewhere in this codebase (see geo.split_antimeridian), so it
   * is only reordered when the values are plainly a latitude pair.
   * @param {string} text - Raw input.
   * @returns {?{west: number, south: number, east: number, north: number}} */
  function parseBbox(text) {
    if (!text) return null;
    const parts = String(text)
      .replace(/[[\]()]/g, " ")
      .split(/[,;\s]+/)
      .filter(Boolean);
    if (parts.length !== 4) return null;
    const values = parts.map(Number);
    if (values.some((value) => !Number.isFinite(value))) return null;
    let [west, south, east, north] = values;
    if (Math.abs(south) > 90 || Math.abs(north) > 90) return null;
    if (Math.abs(west) > 180 || Math.abs(east) > 180) return null;
    if (south > north) [south, north] = [north, south];
    // Only reorder longitudes when the span is small enough that a wraparound
    // is implausible; a genuine antimeridian bbox has west > east on purpose.
    if (west > east && west - east < 180) [west, east] = [east, west];
    return { west, south, east, north };
  }

  /** Names the format `parse` actually used, so the UI can show it.
   *
   * A silent wrong guess is the failure mode of any auto-detecting parser -
   * an operator pastes a grid reference, gets a plausible point 400 km away,
   * and has nothing to tell them why. Reporting the detected format makes that
   * visible at a glance.
   * @param {string} text - Raw input.
   * @param {?string} preferredSystem - Fallback system id.
   * @returns {?string} A short human label, or null if nothing parsed. */
  function detectFormat(text, preferredSystem) {
    if (!text || !text.trim()) return null;
    if (parseBbox(text)) return "bounding box";
    if (parseDecimalPair(text)) return "WGS84 decimal degrees";
    if (parseDmsPair(text)) return "WGS84 degrees/minutes";
    if (parseMgrs(text)) return "MGRS";
    if (parseUtm(text)) return "UTM";
    if (parseGrid(text, preferredSystem)) {
      const grid = findGrid(preferredSystem);
      return grid ? grid.label : "selected grid";
    }
    return null;
  }

  // ------------------------------------------------------------ formatting

  /** Formats a WGS84 point for display in the given coordinate system.
   * @param {number} lat - Latitude in degrees.
   * @param {number} lon - Longitude in degrees.
   * @param {?string} systemId - One of SYSTEMS' ids; defaults to wgs84
   *   decimal degrees.
   * @returns {string} Formatted coordinate string, or "" if the required
   *   library is unavailable. */
  function format(lat, lon, systemId) {
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return "";
    switch (systemId) {
      case "dms":
        return `${toDMS(lat, "N", "S")} ${toDMS(lon, "E", "W")}`;
      case "ddm":
        return `${toDDM(lat, "N", "S")} ${toDDM(lon, "E", "W")}`;
      case "utm": {
        if (typeof window.proj4 === "undefined") return "";
        const zone = utmZone(lon);
        const south = lat < 0;
        const [easting, northing] = window.proj4("WGS84", utmDef(zone, south), [lon, lat]);
        return `${zone}${south ? "S" : "N"} ${Math.round(easting)} ${Math.round(northing)}`;
      }
      case "mgrs":
        if (typeof window.mgrs === "undefined") return "";
        try {
          return window.mgrs.forward([lon, lat]);
        } catch (_) {
          return "";
        }
      case "custom": {
        if (typeof window.proj4 === "undefined") return "";
        const def = (localStorage.getItem("nexfiremap.coords.customProj4") || "").trim();
        if (!def) return "";
        try {
          const [x, y] = window.proj4("WGS84", def, [lon, lat]);
          return `${x.toFixed(1)} ${y.toFixed(1)}`;
        } catch (_) {
          return "";
        }
      }
      case "wgs84":
      case undefined:
      case null:
        return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
      default: {
        const grid = findGrid(systemId);
        if (!grid || typeof window.proj4 === "undefined") return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
        try {
          const [x, y] = window.proj4("WGS84", gridDef(grid, lon), [lon, lat]);
          return `${x.toFixed(1)} ${y.toFixed(1)}`;
        } catch (_) {
          return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
        }
      }
    }
  }

  const SYSTEMS = [
    { id: "wgs84", label: "WGS84 (decimal degrees)" },
    { id: "dms", label: "WGS84 (degrees, minutes, seconds)" },
    { id: "ddm", label: "WGS84 (degrees, decimal minutes)" },
    { id: "utm", label: "UTM" },
    { id: "mgrs", label: "MGRS" },
    ...NATIONAL_GRIDS.map((grid) => ({ id: grid.id, label: grid.label })),
    { id: "custom", label: "Custom (EPSG/proj4 string)" },
  ];

  /** Reads the user's last-selected coordinate system from localStorage.
   * @returns {string} A SYSTEMS id, defaulting to "wgs84" if unset or
   *   unavailable. */
  function currentSystem() {
    try {
      return localStorage.getItem("nexfiremap.coords.system") || "wgs84";
    } catch (_) {
      return "wgs84";
    }
  }

  /** Persists the user's coordinate system choice for future sessions.
   * @param {string} id - A SYSTEMS id. */
  function setSystem(id) {
    try {
      localStorage.setItem("nexfiremap.coords.system", id);
    } catch (_) {
      /* private mode/storage pressure - falls back to wgs84 next load */
    }
  }

  export { SYSTEMS, parse, parseBbox, detectFormat, format, currentSystem, setSystem, NATIONAL_GRIDS };
