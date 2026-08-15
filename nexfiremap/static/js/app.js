/* NexFiremap frontend.
   Talks to the local server only: the server owns the FIRMS cache, so the map
   never waits on NASA directly - it asks for what it needs, draws whatever is
   cached already, and redraws when the background fetch lands.

   An ES module (see context.js for the cross-module contracts). Leaflet,
   MapLibre, proj4 and mgrs are still the classic globals their vendored
   <script> tags define - those load before any module body runs, since module
   scripts are deferred and the vendor tags are not. */

import * as Coords from "./coords.js";
import { setMap, setSpreadAnalysis, getPrintView, emitMapContextMenu } from "./context.js";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const HOUR = 3600;
  const DAY = 86400;

  // Bins are fixed rather than data-derived so the legend means the same thing
  // as you pan around.
  const AGE_BINS = [
    { max: 6 * HOUR, label: "< 6h" },
    { max: 24 * HOUR, label: "< 24h" },
    { max: 3 * DAY, label: "< 3d" },
    { max: 7 * DAY, label: "< 7d" },
    { max: Infinity, label: "7d +" },
  ];

  const FRP_BINS = [
    { max: 5, label: "< 5" },
    { max: 20, label: "< 20" },
    { max: 50, label: "< 50" },
    { max: 100, label: "< 100" },
    { max: Infinity, label: "100 +" },
  ];

  // Zoom level at which pixel footprints (scan x track) become visually
  // distinguishable from a dot - below this a plain marker is faster and
  // just as informative.
  const FOOTPRINT_MIN_ZOOM = 8;

  // Below this zoom, the viewport is "most of a continent or more" - fire
  // data (and everything that piggybacks on it: coverage/events/industrial
  // autofetch) stays un-loaded rather than triggering an increasingly large
  // fetch/compute on every pan at world scale. Zooming in past this level
  // resumes loading automatically (see loadDetections's own gate below).
  const MIN_ACTIVE_ZOOM = 6;
  // About 2 km at mid latitudes - a VIIRS footprint radius plus margin, so a
  // zoomed-in view still contains the density field's own boundary.
  const SPREAD_TOPOLOGY_MIN_PAD_DEG = 0.02;

  const state = {
    config: null,
    features: {}, // { orbits, events, likelihood, imagery, terrain } - which optional modules loaded server-side
    sources: new Map(),      // id -> meta
    enabledSources: new Set(),
    instruments: [],         // ordered list for categorical slots
    days: 3,
    focusDay: null,          // ISO date when a histogram bar is selected
    colorBy: "age",
    renderMode: "topology",
    minFrp: 0,
    daynight: "",
    rows: [],
    summary: [],
    lastSpreadTopologyRange: null, // {earliest, latest} band cutoff ts of the last-rendered topology - set by drawSpreadTopology, read by renderLegend
    basemapTone: "dark",
    pollTimer: null,
    loadTimer: null,
    inflight: null,
    playback: {
      active: false,
      cursor: null,   // epoch seconds; null = show everything (default)
      min: null,
      max: null,
      timer: null,
      speed: 1,
    },
    coverageEnabled: false,
    industrialEnabled: false,
    eumetsatEnabled: false,
    events: [],
    selectedEventId: null,
    analysisMode: "heat", // "heat" | "arrival" | "burn" | "spread" | "ensemble"
    currentAnalysis: null, // { jobId, result } - likelihood/arrival, from analyze_event
    currentBurnScar: null, // { jobId, result } - from analyze_burn_scar, separate job
    currentPropagation: null, // { jobId, result } - from run_propagation, separate job
    currentEnsemble: null, // { jobId, result } - from run_ensemble_assimilation, separate job
  };

  /**
   * Reads a CSS custom property's current value off `<body>` so palette
   * decisions live in app.css (and can be re-themed there) instead of
   * being hardcoded in JS.
   * @param {string} name - a `--custom-property` name, e.g. "--fire-3".
   * @returns {string} the trimmed computed value (typically a hex color).
   */
  const css = (name) =>
    getComputedStyle(document.body).getPropertyValue(name).trim();

  /** The 5-step age/FRP color ramp (index 0 = recessive, 4 = prominent). @returns {string[]} */
  const fireRamp = () => [1, 2, 3, 4, 5].map((n) => css(`--fire-${n}`));
  /** The 5-step charcoal->purple time-spread ramp used by "Spread over Time" (index 0 = earliest/recessive, index 4 = latest/salient). @returns {string[]} */
  const timeRamp = () => [1, 2, 3, 4, 5].map((n) => css(`--time-${n}`));
  /** The 3 categorical swatches used when colorBy === "instrument". @returns {string[]} */
  const catColors = () => [css("--cat-1"), css("--cat-2"), css("--cat-3")];

  // Continuous charcoal->yellow->orange->red->purple interpolation across
  // timeRamp()'s 5 stops (see app.css's --time-* comment for how/why this
  // exact palette was chosen, and why it runs earliest=recessive/"ash" ->
  // latest=salient rather than the other way around) - a plain per-channel
  // sRGB lerp between the two stops `fraction` falls between, matching how
  // the CSS `linear-gradient` legend bar interpolates so the two never
  // visually disagree. `fraction` 0 = earliest (charcoal), 1 = latest
  // (purple).
  /**
   * @param {number} fraction - 0 (earliest/charcoal, recessive) .. 1 (latest/purple, salient), clamped.
   * @param {string[]} ramp - 5-stop hex ramp, as returned by timeRamp().
   * @returns {string} an `rgb(r, g, b)` string lerped between the two stops `fraction` falls between.
   */
  function timeSpreadColor(fraction, ramp) {
    const f = Math.max(0, Math.min(1, fraction));
    const segments = ramp.length - 1; // 4
    const pos = f * segments;
    const i = Math.min(segments - 1, Math.floor(pos));
    const t = pos - i;
    const a = hexToRgb(ramp[i]);
    const b = hexToRgb(ramp[i + 1]);
    const r = Math.round(a[0] + (b[0] - a[0]) * t);
    const g = Math.round(a[1] + (b[1] - a[1]) * t);
    const bl = Math.round(a[2] + (b[2] - a[2]) * t);
    return `rgb(${r}, ${g}, ${bl})`;
  }

  /** @param {string} hex - a `#rrggbb` (or `rrggbb`) color. @returns {[number, number, number]} r,g,b in 0-255. */
  function hexToRgb(hex) {
    const h = hex.trim().replace(/^#/, "");
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }

  // ----------------------------------------------------------- formatting

  const nf = new Intl.NumberFormat();

  /**
   * Locale-formatted count, abbreviated above 10K/1M (e.g. "12.3K") so stat
   * tiles and popups stay a fixed, glanceable width instead of growing
   * arbitrarily wide with cache size.
   * @param {number|null|undefined} value
   * @returns {string}
   */
  function compact(value) {
    if (value === null || value === undefined) return "-";
    if (value >= 1e6) return (value / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (value >= 1e4) return (value / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
    return nf.format(Math.round(value));
  }

  /** @param {string} iso - a "YYYY-MM-DD" date. @returns {string} locale short date (e.g. "3 Aug"), read as UTC. */
  function shortDate(iso) {
    const d = new Date(iso + "T00:00:00Z");
    return d.toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      timeZone: "UTC",
    });
  }

  /** @param {number} seconds - elapsed time, e.g. `now - detectionTimestamp`. @returns {string} a coarse relative-age label ("14 min ago", "3 h ago", "2 d ago"). */
  function ageLabel(seconds) {
    if (seconds < HOUR) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
    if (seconds < DAY) return `${Math.round(seconds / HOUR)} h ago`;
    return `${Math.round(seconds / DAY)} d ago`;
  }

  /** Escapes `&<>"'` for safe interpolation into innerHTML strings built throughout this file. @param {*} value @returns {string} */
  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Each viewport-driven panel loader (coverage/industrial/events/eumetsat)
  // fires its own fetch on pan/zoom/toggle with no built-in cancellation -
  // a slower-to-resolve older response landing after a newer one used to
  // just overwrite the map with stale-viewport data. `next()` before a
  // fetch and `isCurrent(token)` after it turns "am I still the latest
  // call" into a one-line check instead of four hand-rolled copies of the
  // same guard.
  /**
   * Factory for the request-staleness token pattern used by every
   * viewport-driven panel loader (coverage/industrial/events/eumetsat/map
   * search, one guard instance each - see e.g. `coverageGuard`,
   * `industrialGuard`, `eventsGuard`, `eumetsatGuard`, `searchGuard` below).
   *
   * Usage: call `next()` right before firing a fetch, capture the returned
   * token, then call `isCurrent(token)` after the response lands (and
   * again after any subsequent await) - if it returns false, a newer call
   * has since started and this response's data must not be applied.
   * @returns {{ next: () => number, isCurrent: (t: number) => boolean }}
   */
  function makeStaleGuard() {
    let token = 0;
    return { next: () => ++token, isCurrent: (t) => t === token };
  }

  // --------------------------------------------------------------- colours

  /**
   * Finds which bin a value falls in for a bins array shaped like
   * AGE_BINS/FRP_BINS (each `{ max }`, ascending, last entry `max: Infinity`).
   * @param {{max: number}[]} bins
   * @param {number} value
   * @returns {number} index of the first bin whose `max` exceeds `value`.
   */
  function binIndex(bins, value) {
    for (let i = 0; i < bins.length; i++) if (value < bins[i].max) return i;
    return bins.length - 1;
  }

  // The ramp itself is one fixed sequence, index 0 = most-recessive step,
  // index 4 = most-prominent (index 4 is the pale/bright end on a dark
  // basemap, the dark/saturated end on a light one - see the --fire-*
  // definitions in app.css). Both magnitudes here should make their "most"
  // end prominent and their "least" end recede: high FRP is naturally
  // index 4 already (FRP_BINS is ascending), but AGE_BINS is ordered
  // freshest-first, so age needs the index mirrored - otherwise the
  // freshest (most operationally important) detections would land on the
  // ramp's most-recessive step, the least visible choice on the map.
  /**
   * @param {Array} row - a detection row, positional fields per api.py's /api/detections (lat, lon, ts, frp, confidence, source_id, ...).
   * @param {number} now - epoch seconds, passed in rather than read fresh so a whole batch of rows is colored against one consistent "now".
   * @param {string[]} ramp - fireRamp(), 5 steps.
   * @param {string[]} cats - catColors(), 3 steps.
   * @returns {string} the hex/rgb fill color for this row under the current state.colorBy mode.
   */
  function colorForRow(row, now, ramp, cats) {
    if (state.colorBy === "instrument") {
      const meta = state.sources.get(row[5]);
      const idx = state.instruments.indexOf(meta ? meta.instrument : "");
      return cats[Math.max(0, idx) % cats.length];
    }
    if (state.colorBy === "frp") {
      return ramp[binIndex(FRP_BINS, row[3] === null ? 0 : row[3])];
    }
    const ageIdx = binIndex(AGE_BINS, Math.max(0, now - row[2]));
    return ramp[AGE_BINS.length - 1 - ageIdx];
  }

  // ------------------------------------------------------------- URL state
  //
  // #zoom/lat/lon - the same convention OpenStreetMap.org and the popular
  // leaflet-hash plugin use, so a copied link is both shareable and
  // human-readable. Written via replaceState (not location.hash =, and not
  // pushState) so panning around never pollutes browser back-button history
  // or fires a hashchange loop - it just keeps the current URL in sync.

  /** @returns {{zoom: number, lat: number, lon: number}|null} the parsed `#zoom/lat/lon` hash, or null if absent/malformed. */
  function readViewFromHash() {
    const m = /^#(\d+(?:\.\d+)?)\/(-?\d+(?:\.\d+)?)\/(-?\d+(?:\.\d+)?)$/.exec(
      window.location.hash
    );
    if (!m) return null;
    const [, zoom, lat, lon] = m.map(Number);
    if (!Number.isFinite(zoom) || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    return { zoom, lat, lon };
  }

  /** Syncs the URL hash to the map's current view; see the section comment above for why replaceState. */
  function writeViewToHash() {
    const c = map.getCenter();
    const hash = `#${map.getZoom()}/${c.lat.toFixed(5)}/${c.lng.toFixed(5)}`;
    if (window.location.hash !== hash) {
      history.replaceState(null, "", hash);
    }
  }

  // ------------------------------------------------------------------ map

  let map;
  let canvasRenderer;
  let basemapLayers = {};
  let overlayLayers = {};
  let activeBasemap = null;
  let pointLayer = null;
  let clusterLayer = null;
  let heatLayer = null;
  let spreadTopologyLayer = null;
  let spreadTopologyGeneration = 0; // bumped per submitSpreadTopologyJob() call - lets a slow/superseded job response detect it's stale and skip rendering
  let spreadTopologyDebounceTimer = null;
  let coverageLayer = null;
  let coverageRetryTimer = null;
  let industrialLayer = null;
  let industrialRetryTimer = null;
  let eumetsatLayer = null;
  let eumetsatRetryTimer = null;
  let eventsRetryTimer = null;
  let eventMarkersLayer = null;
  let analysisOverlay = null;
  let envelopeLayer = null;
  let calloutMarker = null;
  // The callout's "Updated Xm ago" text is baked into the divIcon's HTML at
  // creation time. Without this, it silently goes stale itself: an operator
  // who ran the model once and then left the tab open for the rest of the
  // shift would keep seeing "Updated 2m ago" hours later - a modelled-extent
  // label actively misrepresenting its own freshness. calloutRefreshTimer
  // (started once in init()) re-renders just that text node periodically.
  let calloutReferenceTs = null;

  /**
   * Builds the Leaflet map, its custom panes (z-ordering for
   * coverage/analysis/industrial layers relative to fire markers) and the
   * moveend/zoomend -> URL-hash + scheduleLoad wiring. Called once from init().
   * @param {object} config - the /api/config payload (used only for startup_bbox here).
   */
  function initMap(config) {
    // Restore the last-viewed spot rather than always opening on the whole
    // world: the URL's own #zoom/lat/lon wins first (works across restarts
    // and for a shared/bookmarked link alike), then the operator-configured
    // NEXFIREMAP_STARTUP_BBOX (exposed as config.startup_bbox), then the
    // previous world-view default as a last resort.
    const fromHash = readViewFromHash();
    const startupBbox = config && config.startup_bbox;
    let initialBounds = null;
    let initialView = { center: [20, 5], zoom: 3 }; // previous world-view default
    if (fromHash) {
      initialView = { center: [fromHash.lat, fromHash.lon], zoom: fromHash.zoom };
    } else if (startupBbox && startupBbox.length === 4) {
      const [west, south, east, north] = startupBbox;
      initialBounds = [[south, west], [north, east]];
    }

    map = L.map("map", {
      center: initialView.center,
      zoom: initialView.zoom,
      minZoom: 2,
      worldCopyJump: true,
      zoomControl: false, // added manually below, bottom-left
      attributionControl: false, // same - explicit order controls stacking
      preferCanvas: true,
    });
    if (initialBounds) map.fitBounds(initialBounds);
    // Both bottom-left, out of the way of the panel (right) and the
    // topbar/legend (top). For a *bottom* corner, Leaflet's own Control.addTo
    // prepends each new control before the corner's existing first child
    // (`indexOf("bottom") !== -1 ? corner.insertBefore(el, corner.firstChild)
    // : corner.appendChild(el)`, in the vendored leaflet.js) rather than
    // appending it - the opposite of a top corner, and easy to get backwards.
    // So whichever of these two is added *second* ends up visually on top,
    // and the attribution strip - the thing that actually belongs pinned in
    // the literal corner - needs to be added first for the zoom +/- control
    // to end up above it, not below.
    L.control.attribution({ position: "bottomleft" }).addTo(map);
    L.control.zoom({ position: "bottomleft" }).addTo(map);
    canvasRenderer = L.canvas({ padding: 0.35 });

    // Sits above tiles but below every detection layer regardless of add
    // order, and never intercepts clicks meant for fire markers.
    const coveragePane = map.createPane("coveragePane");
    coveragePane.style.zIndex = 350;
    coveragePane.style.pointerEvents = "none";

    // Event analysis rasters/envelopes: above coverage, still below the
    // observed-detection markers so "Observed" always reads on top of
    // "Estimated"/"Modelled", per further_plan.md's layering guidance.
    map.createPane("analysisPane").style.zIndex = 375;

    // Industrial/static-source markers: context for detections, so above
    // the analysis rasters but still under the fire markers themselves.
    map.createPane("industrialPane").style.zIndex = 390;

    map.on("moveend zoomend", () => {
      writeViewToHash();
      scheduleLoad(450);
    });
  }

  /**
   * Populates the basemap picker grid and the overlay checkbox list from
   * server config, and restores whichever basemap was last selected
   * (localStorage) or falls back to the configured default.
   * @param {object} config - the /api/config payload (basemaps[], overlays[]).
   */
  function buildBasemaps(config) {
    const grid = $("#basemap-grid");
    grid.innerHTML = "";

    config.basemaps.forEach((bm) => {
      // bm.url already points at our own /tiles proxy (see tiles.py), which
      // fetches upstream once and caches to disk - no CORS, no subdomains.
      const layer = L.tileLayer(bm.url, {
        attribution: bm.attribution,
        maxZoom: bm.max_zoom || 19,
        // When set, stops requesting tiles past the source's real
        // resolution and upscales its deepest native tile instead - lets
        // the map itself still zoom in further (for fire detections etc.)
        // without pointlessly re-requesting a source tile that ran out of
        // real detail (see e.g. esri-terrain in basemaps.py).
        ...(bm.max_native_zoom ? { maxNativeZoom: bm.max_native_zoom } : {}),
      });
      basemapLayers[bm.id] = { layer, meta: bm };

      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "basemap-tile";
      tile.dataset.basemapId = bm.id;
      tile.setAttribute("role", "menuitemradio");
      tile.setAttribute("aria-checked", "false");
      // A layer whose provider runs out of real resolution before max_zoom is
      // upscaled by Leaflet past that point - it goes soft, and an operator
      // reasonably reads that as "this imagery is inaccurate" rather than
      // "there is no more detail to show". Saying so in the tooltip and with a
      // small badge is the difference between a known limit and a suspected
      // fault. (esri-terrain, for instance, serves byte-identical tiles from
      // z10 onward - confirmed live; see basemaps.py.)
      const nativeCap = bm.max_native_zoom || null;
      tile.title = nativeCap
        ? `${bm.name} - full detail to zoom ${nativeCap}; enlarged beyond that`
        : bm.name;
      tile.innerHTML =
        `<span class="basemap-tile-thumb"><span class="basemap-tile-check" aria-hidden="true" hidden>✓</span></span>` +
        `<span class="basemap-tile-label">${escapeHtml(bm.name)}` +
        (nativeCap ? `<small class="basemap-tile-cap">to z${nativeCap}</small>` : "") +
        `</span>`;
      tile.addEventListener("click", () => {
        selectBasemap(bm.id);
        closeBasemapFlyout();
      });
      grid.appendChild(tile);
    });

    const chosen =
      localStorage.getItem("nexfiremap.basemap") ||
      (config.basemaps.find((b) => b.default) || config.basemaps[0]).id;
    selectBasemap(basemapLayers[chosen] ? chosen : config.basemaps[0].id);

    const overlays = $("#basemap-overlays");
    overlays.innerHTML = "";
    // Drone imagery stacks by capture time, newest on top: a later pass over
    // the same ground is by definition the more current picture of the fire, so
    // an older frame must never hide it. Sorting the *list* is not enough -
    // Leaflet draws same-pane tile layers in the order they were added to the
    // map, which is the order the operator happens to tick the boxes in - so
    // each layer also gets an explicit zIndex derived from its capture time.
    const captured = (ov) => Date.parse(ov.acquired_at || "") || 0;
    const timed = config.overlays.filter(captured).sort((a, b) => captured(a) - captured(b));
    const zIndexFor = (ov) => {
      const rank = timed.indexOf(ov);
      // Base 300 keeps these above the basemap (Leaflet's tilePane, zIndex 200)
      // and below the marker/overlay panes that carry the tactical picture -
      // imagery must not bury the symbols drawn on it.
      return rank < 0 ? 300 : 301 + rank;
    };

    config.overlays.forEach((ov) => {
      overlayLayers[ov.id] = L.tileLayer(ov.url, {
        attribution: ov.attribution,
        maxZoom: ov.max_zoom || 19,
        minZoom: ov.min_zoom || 0,
        pane: "shadowPane",
        zIndex: zIndexFor(ov),
      });
      const label = document.createElement("label");
      // Captured-at is the field the stacking order is derived from, so showing
      // it is what makes "why is that one on top?" answerable from the UI.
      const when = ov.acquired_at
        ? `<small class="overlay-when">${escapeHtml(String(ov.acquired_at).replace("T", " ").slice(0, 16))}</small>`
        : "";
      label.innerHTML = `<input type="checkbox" value="${ov.id}"><span>${escapeHtml(ov.name)}${when}</span>`;
      if (ov.limitations) label.title = ov.limitations;
      overlays.appendChild(label);
      label.querySelector("input").addEventListener("change", (e) => {
        if (e.target.checked) overlayLayers[ov.id].addTo(map);
        else map.removeLayer(overlayLayers[ov.id]);
      });
    });
  }

  /**
   * Swaps the active basemap layer, persists the choice, and - only when
   * the new basemap's light/dark tone actually differs from the current
   * one - re-renders everything whose styling depends on basemapTone
   * (legend, detections, coverage mesh).
   * @param {string} id - a basemap id from config.basemaps.
   */
  function selectBasemap(id) {
    const entry = basemapLayers[id];
    if (!entry) return;
    if (activeBasemap) map.removeLayer(activeBasemap.layer);
    entry.layer.addTo(map);
    entry.layer.bringToBack();
    activeBasemap = entry;
    localStorage.setItem("nexfiremap.basemap", id);

    const tone = entry.meta.dark ? "dark" : "light";
    if (tone !== state.basemapTone) {
      state.basemapTone = tone;
      document.body.dataset.basemapTone = tone;
      renderLegend();
      drawDetections();
      loadCoverage(); // re-tint the coverage mesh for the new tone
    } else {
      document.body.dataset.basemapTone = tone;
    }

    $$("#basemap-grid .basemap-tile").forEach((tile) => {
      const checked = tile.dataset.basemapId === id;
      tile.setAttribute("aria-checked", String(checked));
      tile.querySelector(".basemap-tile-check").hidden = !checked;
    });
    refreshBasemapToggleThumb();
  }

  // ------------------------------------------------------- basemap picker
  //
  // Live-preview thumbnails: each tile's background is a real tile image
  // from that basemap, centred on wherever the operator is currently
  // looking - not a generic icon - fetched through the same /tiles proxy
  // and cache the main map itself uses, so this costs nothing extra beyond
  // normal tile caching and still works fully offline once cached.
  /** @param {number} zoom - target preview zoom (see thumbnailUrl's own comment on how it's chosen). @returns {{x: number, y: number, z: number}} the slippy-map tile covering the map's current center at `zoom`. */
  function previewTileXYZ(zoom) {
    const center = map.getCenter();
    const n = 2 ** zoom;
    const x = Math.floor(((center.lng + 180) / 360) * n);
    const latRad = (center.lat * Math.PI) / 180;
    const y = Math.floor(
      ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n
    );
    return { x: ((x % n) + n) % n, y: Math.min(Math.max(y, 0), n - 1), z: zoom };
  }

  /** @param {object} bm - a basemap meta entry (from config.basemaps). @returns {string} a real /tiles-proxied tile URL suitable as a CSS background-image preview. */
  function thumbnailUrl(bm) {
    // A regional-overview zoom, not the map's own current zoom - a useful
    // small preview should show recognisable terrain/coastline/roads, not
    // a single street segment or a whole hemisphere, whatever the operator
    // happens to be zoomed to right now.
    const cap = bm.max_native_zoom || bm.max_zoom || 12;
    const zoom = Math.max(2, Math.min(9, cap));
    const { x, y, z } = previewTileXYZ(zoom);
    return bm.url.replace("{z}", z).replace("{x}", x).replace("{y}", y).replace("{r}", "");
  }

  /** Refreshes every basemap-grid tile's preview image against the map's current center. */
  function refreshBasemapThumbnails() {
    if (!map) return;
    $$("#basemap-grid .basemap-tile").forEach((tile) => {
      const entry = basemapLayers[tile.dataset.basemapId];
      if (!entry) return;
      tile.querySelector(".basemap-tile-thumb").style.backgroundImage = `url("${thumbnailUrl(entry.meta)}")`;
    });
  }

  /** Refreshes just the collapsed toggle button's own preview swatch (the active basemap only). */
  function refreshBasemapToggleThumb() {
    if (!map || !activeBasemap) return;
    $("#basemap-toggle-thumb").style.backgroundImage = `url("${thumbnailUrl(activeBasemap.meta)}")`;
  }

  /** Opens the basemap flyout, refreshing its thumbnails first (see wireBasemapPicker's lazy-refresh comment). */
  function openBasemapFlyout() {
    refreshBasemapThumbnails();
    $("#basemap-flyout").hidden = false;
    $("#basemap-toggle").setAttribute("aria-expanded", "true");
  }

  /** Closes the basemap flyout. */
  function closeBasemapFlyout() {
    $("#basemap-flyout").hidden = true;
    $("#basemap-toggle").setAttribute("aria-expanded", "false");
  }

  /** Wires the basemap-toggle button, its flyout's open/close/outside-click/Escape handling, and thumbnail refresh-on-pan. */
  function wireBasemapPicker() {
    const toggle = $("#basemap-toggle");
    toggle.addEventListener("click", () => {
      if ($("#basemap-flyout").hidden) openBasemapFlyout();
      else closeBasemapFlyout();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !$("#basemap-flyout").hidden) {
        closeBasemapFlyout();
        toggle.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest("#basemap-picker")) closeBasemapFlyout();
    });
    // A stale thumbnail (wherever the operator was looking last time the
    // flyout opened) beats a network request on every single pan/zoom -
    // refreshed lazily on open instead, in openBasemapFlyout() above.
    map.on("moveend", () => {
      if (!$("#basemap-flyout").hidden) refreshBasemapThumbnails();
    });

    alignBasemapPicker();
    let alignResizeTimer;
    window.addEventListener("resize", () => {
      clearTimeout(alignResizeTimer);
      alignResizeTimer = setTimeout(alignBasemapPicker, 150);
    });
  }

  // Sits immediately right of Leaflet's own zoom control, baseline-aligned
  // with its bottom edge - measured from the real rendered control rather
  // than a hardcoded guess at Leaflet's own margins (a first attempt at a
  // fixed CSS offset landed a few pixels off; Leaflet's actual per-corner
  // spacing isn't just the documented 10px control margin).
  /** Repositions #basemap-picker against the real rendered zoom control (see the comment above for why this can't just be a fixed CSS offset). Called on init, on window resize, and whenever the layout it depends on changes (e.g. applyAppMode). */
  function alignBasemapPicker() {
    const zoomEl = document.querySelector(".leaflet-control-zoom");
    const mapEl = document.getElementById("map");
    const picker = $("#basemap-picker");
    if (!zoomEl || !mapEl || !picker) return;
    const zoomRect = zoomEl.getBoundingClientRect();
    const mapRect = mapEl.getBoundingClientRect();
    picker.style.left = `${Math.round(zoomRect.right - mapRect.left + 8)}px`;
    picker.style.bottom = `${Math.round(mapRect.bottom - zoomRect.bottom)}px`;
  }

  // ------------------------------------------------------ map context menu
  //
  // No right-click handling existed at all before this - a right-click just
  // fell through to the browser's native menu, and there was no way to
  // remove a placed tactical marker except finding its popup's small
  // "remove" text button (still there, still works - see removeFeature() in
  // operations.js - just easy to miss). This section adds: a short
  // right-click (below the drag threshold) opens a menu of actions for that
  // point or, if it landed on an existing feature, that feature; a
  // right-click-and-drag shows a live grid+area overlay and opens a menu of
  // actions for the dragged area on release.
  //
  // Both menus render through the same generic showContextMenu(items,
  // screenPoint), exported from this module so operations.js can reuse it
  // for its own feature-specific edit/remove menus, and contribute
  // "Add tactical marker here" via context.js's onMapContextMenu registry
  // without app.js needing any Incident-Command-specific knowledge.

  const MAP_RECT_DRAG_THRESHOLD_PX = 6; // below this, a right-click is a click, not a drag
  // "Nice" round real-world distances for the drag-select grid's cell size -
  // the same idea L.Control.Scale uses for its own bar, reimplemented as a
  // small local helper rather than reaching into Leaflet's private
  // scale-control internals.
  const NICE_GRID_KM = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000];

  let contextMenuOpen = false;

  /** Generic reusable context menu: fills #map-context-menu with `items`
   * ({label, action, danger?, keepOpenMs?}[] - a falsy entry renders a
   * divider) and positions it at `screenPoint` ({x,y} in viewport pixels),
   * clamped so it never runs off the right/bottom edge. `keepOpenMs`
   * (used by the copy-to-clipboard items) runs `action` without closing
   * the menu first, then closes it after that many ms - long enough for
   * the button's own "Copied" feedback to actually be seen. Exported so
   * operations.js's feature-specific menus (edit/remove) reuse this
   * instead of duplicating menu-rendering. */
  function showContextMenu(items, screenPoint) {
    const el = $("#map-context-menu");
    el.innerHTML = "";
    items.forEach((item) => {
      if (!item) {
        el.appendChild(document.createElement("hr"));
        return;
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "menuitem");
      btn.textContent = item.label;
      if (item.danger) btn.dataset.danger = "";
      btn.addEventListener("click", () => {
        if (item.keepOpenMs) {
          item.action(btn);
          setTimeout(closeContextMenu, item.keepOpenMs);
        } else {
          closeContextMenu();
          item.action();
        }
      });
      el.appendChild(btn);
    });
    el.hidden = false;
    contextMenuOpen = true;
    // Measure after unhiding, so offsetWidth/Height reflect the real
    // rendered size - clamped to stay fully on-screen near an edge rather
    // than the natural "top-left corner at the cursor" placement running
    // off it.
    const rect = el.getBoundingClientRect();
    const left = Math.min(screenPoint.x, window.innerWidth - rect.width - 8);
    const top = Math.min(screenPoint.y, window.innerHeight - rect.height - 8);
    el.style.left = `${Math.max(8, left)}px`;
    el.style.top = `${Math.max(8, top)}px`;
  }

  function closeContextMenu() {
    if (!contextMenuOpen) return;
    $("#map-context-menu").hidden = true;
    contextMenuOpen = false;
  }

  /** Re-lays out the map for the print sheet and waits for its tiles.
   *
   * Printing changes the map's size (the print stylesheet gives #map the whole
   * page), so Leaflet has to be told to re-measure and then fetch the tiles
   * that newly came into view. The old code did `invalidateSize()` and then
   * `setTimeout(window.print, 150)` - a race it usually lost, because 150ms is
   * nowhere near enough for a tile round trip, let alone a cold cache. The
   * result was a print preview with a half-blank map, which is what "print to
   * PDF does not support previews" actually was.
   *
   * Waits on the active basemap's own `load` event, which Leaflet fires once
   * every visible tile has arrived. The timeout ceiling matters as much as the
   * wait: with the WAN down no tile will ever load, and printing what is on
   * screen beats refusing to print at all.
   * @param {number} [timeoutMs] - Give up waiting and print anyway.
   * @returns {Promise<void>} */
  function prepareMapForPrint(timeoutMs = 6000) {
    map.invalidateSize();
    // `activeBasemap` is what selectBasemap() maintains; there is no
    // state.basemapId.
    const active = activeBasemap?.layer;
    if (!active) return new Promise((resolve) => setTimeout(resolve, 250));
    return new Promise((resolve) => {
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        active.off("load", done);
        // One frame after the last tile, so the browser has actually painted
        // it before the print dialog snapshots the page.
        requestAnimationFrame(() => setTimeout(resolve, 60));
      };
      active.on("load", done);
      setTimeout(done, timeoutMs);
      // Leaflet only fires `load` at the end of an actual loading run, so when
      // the resize needed no new tiles the event never comes and this would
      // sit out the whole timeout before every print. `_loading` is GridLayer
      // internals, but it is the only signal for "there is nothing pending" -
      // guarded so a future Leaflet that drops it just falls back to the
      // timeout rather than breaking.
      requestAnimationFrame(() => {
        if (active._loading === false || active._loading === undefined) done();
      });
    });
  }

  export { showContextMenu, closeContextMenu, prepareMapForPrint };

  /** Copies `text` to the clipboard, falling back to a hidden textarea +
   * execCommand("copy") when the Clipboard API is unavailable -
   * navigator.clipboard requires a secure context (HTTPS or localhost),
   * and this app explicitly also runs on a plain-HTTP incident LAN, where
   * it's simply absent, not just occasionally blocked. Reports success or
   * failure directly on `btn` (the clicked menu item, paired with a
   * `keepOpenMs` item so the feedback is actually visible) rather than
   * silently doing nothing either way - a failed copy still logs the text
   * to the console so it's not lost. */
  async function copyText(text, btn) {
    const report = (msg) => { if (btn) btn.textContent = msg; };
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        report("Copied ✓");
        return;
      }
    } catch (err) {
      // Fall through to the legacy path below.
    }
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      report(ok ? "Copied ✓" : "Couldn't copy - see console");
      if (!ok) console.info("Copy this:", text);
    } catch (err) {
      report("Couldn't copy - see console");
      console.info("Copy this:", text);
    }
  }

  /** Picks a "nice" round real-world grid-cell size (in screen pixels, at
   * the map's current zoom/latitude) for the drag-select overlay - snaps
   * to whichever NICE_GRID_KM value keeps the resulting cell in a legible
   * ~40-100px range on screen. Computed once per drag (at its start
   * point), not per mousemove - the zoom doesn't change mid-drag. */
  function computeGridCell(atLatLng) {
    const p1 = map.latLngToContainerPoint(atLatLng);
    const p2 = map.containerPointToLatLng(L.point(p1.x + 100, p1.y));
    const metersPerPx = atLatLng.distanceTo(p2) / 100 || 1;
    const targetKm = (metersPerPx * 60) / 1000;
    let cellKm = NICE_GRID_KM[0];
    let bestDiff = Infinity;
    for (const km of NICE_GRID_KM) {
      const diff = Math.abs(Math.log(km) - Math.log(Math.max(targetKm, 1e-6)));
      if (diff < bestDiff) { bestDiff = diff; cellKm = km; }
    }
    return { cellKm, cellPx: (cellKm * 1000) / metersPerPx };
  }

  /** Real-world area (km²) of the lat/lon rectangle spanned by `sw`/`ne`,
   * via Leaflet's own LatLng.distanceTo() (haversine - no new projection
   * math needed) - averages the north/south edge widths for the
   * rectangle's own short-edge convergence rather than assuming a
   * constant width top-to-bottom. A UI readout, not a scientific
   * calculation, so this approximation is deliberately good enough, not
   * exact. */
  function rectAreaKm2(sw, ne) {
    const nw = L.latLng(ne.lat, sw.lng);
    const se = L.latLng(sw.lat, ne.lng);
    const width = (nw.distanceTo(ne) + sw.distanceTo(se)) / 2;
    const height = sw.distanceTo(nw);
    return (width * height) / 1e6;
  }

  /** Wires right-click gesture detection on the map: a short right-click
   * opens the point (or feature) menu; right-click-and-drag shows the
   * live grid+area overlay and opens the area menu on release. */
  function wireMapContextMenu() {
    const rectEl = $("#map-select-rect");
    const labelEl = $("#map-select-area-label");
    let drag = null; // {startPoint, startLatLng, moved, mapRect, cell} while tracking
    // Set by a non-drag mouseup, consumed by the *map's own* contextmenu
    // handler below - not shown directly from mouseup, deliberately: the
    // browser fires contextmenu right after mouseup, and if the click
    // landed on an existing feature, that layer's own contextmenu handler
    // (operations.js) runs first and calls stopPropagation, so this one
    // never fires at all and the feature-specific menu shows instead of a
    // conflicting generic one underneath it. Only a click that reaches
    // *here* landed on empty map.
    let pendingPointClick = null;

    map.on("contextmenu", (e) => {
      L.DomEvent.preventDefault(e); // always suppress the native menu, drag or click, feature or empty map
      if (pendingPointClick) {
        showPointContextMenu(pendingPointClick.latlng, pendingPointClick.screenPoint);
        pendingPointClick = null;
      }
    });

    map.on("mousedown", (e) => {
      if (e.originalEvent.button !== 2) return;
      L.DomEvent.preventDefault(e.originalEvent); // no native text-selection/drag-ghost while right-dragging
      pendingPointClick = null;
      drag = { startPoint: e.containerPoint, startLatLng: e.latlng, moved: false };
    });

    map.on("mousemove", (e) => {
      if (!drag) return;
      if (!drag.moved && e.containerPoint.distanceTo(drag.startPoint) < MAP_RECT_DRAG_THRESHOLD_PX) return;
      if (!drag.moved) {
        drag.moved = true;
        map.getContainer().classList.add("map-rect-selecting");
        drag.mapRect = map.getContainer().getBoundingClientRect();
        drag.cell = computeGridCell(drag.startLatLng);
      }
      updateSelectRect(drag, e.containerPoint);
    });

    function endDrag() {
      if (drag && drag.moved) map.getContainer().classList.remove("map-rect-selecting");
      rectEl.hidden = true;
      labelEl.hidden = true;
      const finished = drag;
      drag = null;
      return finished;
    }

    map.on("mouseup", (e) => {
      if (!drag || e.originalEvent.button !== 2) return;
      const finished = endDrag();
      const screenPoint = { x: e.originalEvent.clientX, y: e.originalEvent.clientY };
      if (finished.moved) {
        showAreaContextMenu(L.latLngBounds(finished.startLatLng, e.latlng), screenPoint);
      } else {
        // Not shown yet - see pendingPointClick's own comment above for why
        // the map's contextmenu handler is what actually shows this.
        pendingPointClick = { latlng: e.latlng, screenPoint };
      }
    });

    // A right-drag that ends outside the map (mouseup off the container,
    // e.g. released over the panel) or loses the window's focus entirely
    // would otherwise leave the overlay and .map-rect-selecting cursor
    // stuck forever - both just cancel the drag (no menu - there's no
    // reliable release point to act on) rather than leaving it hanging.
    document.addEventListener("mouseleave", () => { if (drag) endDrag(); });
    window.addEventListener("blur", () => { if (drag) endDrag(); });

    function updateSelectRect(d, endPoint) {
      const left = Math.min(d.startPoint.x, endPoint.x);
      const top = Math.min(d.startPoint.y, endPoint.y);
      const width = Math.abs(endPoint.x - d.startPoint.x);
      const height = Math.abs(endPoint.y - d.startPoint.y);

      rectEl.style.left = `${d.mapRect.left + left}px`;
      rectEl.style.top = `${d.mapRect.top + top}px`;
      rectEl.style.width = `${width}px`;
      rectEl.style.height = `${height}px`;
      rectEl.style.setProperty("--map-select-cell", `${d.cell.cellPx}px`);
      rectEl.hidden = false;

      const sw = map.containerPointToLatLng([left, top + height]);
      const ne = map.containerPointToLatLng([left + width, top]);
      labelEl.textContent = `${fmtArea(rectAreaKm2(sw, ne))} · grid ${d.cell.cellKm} km`;
      labelEl.style.left = `${d.mapRect.left + left + width / 2}px`;
      labelEl.style.top = `${d.mapRect.top + top + height + 8}px`;
      labelEl.style.transform = "translateX(-50%)";
      labelEl.hidden = false;
    }

    // Dismiss the menu the same way closeBasemapFlyout() already does
    // (Escape, click-outside) - plus (new here) immediately on
    // movestart/zoomstart, since this menu is viewport-fixed, not tied to
    // a map coordinate: panning away would otherwise leave it floating
    // over the wrong spot instead of closing.
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && contextMenuOpen) closeContextMenu();
    });
    document.addEventListener("click", (event) => {
      if (contextMenuOpen && !event.target.closest("#map-context-menu")) closeContextMenu();
    });
    map.on("movestart zoomstart", () => { if (contextMenuOpen) closeContextMenu(); });
  }

  /** Builds and shows the menu for a short right-click on empty map. */
  function showPointContextMenu(latlng, screenPoint) {
    const items = [
      {
        label: "Copy coordinates",
        keepOpenMs: 900,
        action: (btn) => copyText(Coords.format(latlng.lat, latlng.lng, Coords.currentSystem()), btn),
      },
      {
        label: "What's here",
        action: () => showWhatsHere(latlng),
      },
      {
        label: "Zoom in here",
        action: () => map.setView(latlng, Math.min(map.getMaxZoom(), map.getZoom() + 2)),
      },
    ];
    // Lets operations.js (or any future module) contribute more items -
    // e.g. "Add tactical marker here" - without this file needing any
    // domain-specific knowledge. addItem pushes in place; contributors run
    // synchronously before the menu is actually shown below.
    emitMapContextMenu({ kind: "point", latlng, addItem: (item) => items.push(item) });
    showContextMenu(items, screenPoint);
  }

  /** Reverse-geocodes `latlng` and surfaces the result as a one-off
   * status line inside the (already-closed) context menu's former spot -
   * simplest to just reuse the map's own popup at that point, consistent
   * with how every other "info about this spot" affordance on the map
   * already works (detection markers, event markers, tactical features). */
  async function showWhatsHere(latlng) {
    const popup = L.popup({ maxWidth: 280 })
      .setLatLng(latlng)
      .setContent("Looking up this location…")
      .openOn(map);
    try {
      const res = await fetch(`/api/geocode/reverse?lat=${latlng.lat}&lon=${latlng.lng}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.error) {
        popup.setContent(`Couldn't look this up: ${escapeHtml(data.error)}`);
      } else if (!data.result) {
        popup.setContent("No named place found here.");
      } else {
        popup.setContent(escapeHtml(data.result.label));
      }
    } catch (err) {
      popup.setContent(`Couldn't look this up: ${escapeHtml(err.message || String(err))}`);
    }
  }

  /** Builds and shows the menu for a completed right-click-drag area
   * selection. */
  function showAreaContextMenu(bounds, screenPoint) {
    const items = [
      {
        label: "Zoom to this area",
        action: () => map.fitBounds(bounds, { padding: [20, 20] }),
      },
      {
        label: "Copy bounding box",
        keepOpenMs: 900,
        // Plain WGS84 decimal degrees, not the operator's display
        // coordinate system - this is meant to be pasted back into the
        // app/API, which already speaks this exact west,south,east,north
        // convention everywhere else (see bboxParam()).
        action: (btn) => copyText(
          [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].map((v) => v.toFixed(5)).join(","),
          btn
        ),
      },
      null,
      {
        label: "Detect fire events in this area",
        action: () => { map.fitBounds(bounds, { padding: [20, 20] }); detectEvents(); },
      },
      {
        label: "Print this area",
        action: () => { map.fitBounds(bounds, { padding: [20, 20] }); getPrintView()?.() ?? window.print(); },
      },
    ];
    emitMapContextMenu({ kind: "area", bounds, addItem: (item) => items.push(item) });
    showContextMenu(items, screenPoint);
  }

  // ---------------------------------------------------------- app switcher
  //
  // Four modes over one shared map: NexFiremap (satellite detections, the
  // default), NexIncidentCommand, NexIngest and NexEventView, corresponding
  // to today's existing tool groups (see index.html's data-app attributes).
  // Nothing about the map, its layers or the operations.js/structures.js
  // modules is torn down or reloaded on switch - only which panel sections
  // are visible changes, so a tactical feature drawn under
  // NexIncidentCommand stays on the map while looking at NexFiremap's own
  // satellite layers.
  //
  // Deliberately toggles a CSS class, not the native `hidden` property:
  // several tagged elements already carry their own independent `hidden`
  // logic driven by runtime state (#ops-workspace until an incident is
  // selected, #eumetsat-section until a key is configured, #ops-account-admin
  // for non-administrators, #event-analysis until an event is selected...).
  // Fighting over the same `hidden` property would let whichever set it
  // last silently override the other reason something should stay hidden -
  // a class with `!important` and the native attribute simply OR together.
  const APP_MODES = ["firemap", "incident-command", "ingest", "eventview"];
  const APP_LABELS = {
    firemap: "NexFiremap",
    "incident-command": "NexIncidentCommand",
    ingest: "NexIngest",
    eventview: "NexEventView",
  };

  /**
   * Switches the visible app (see the section comment above for the
   * class-toggle-not-`hidden` rationale) and persists the choice.
   * @param {string} id - one of APP_MODES; falls back to "firemap" if unrecognised.
   */
  function applyAppMode(id) {
    if (!APP_MODES.includes(id)) id = "firemap";
    $$("[data-app]").forEach((el) => {
      const apps = el.dataset.app.split(/\s+/);
      el.classList.toggle("nf-app-hidden", !apps.includes(id));
    });
    $("#app-switcher-label").textContent = APP_LABELS[id];
    $$("#app-switcher-menu [data-app-select]").forEach((btn) =>
      btn.setAttribute("aria-checked", String(btn.dataset.appSelect === id))
    );
    try {
      localStorage.setItem("nexfiremap.app", id);
    } catch (_) {
      /* private mode/storage pressure - the switch itself still works */
    }
    // NexFiremap is map-first: entering it always starts the panel
    // collapsed. The other three apps are unusable without their tools
    // open, so entering any of them always starts it expanded. The
    // existing #btn-panel toggle (wireControls()) still overrides this
    // manually within whichever mode is active - this only decides what
    // happens at the moment of switching.
    const panel = $("#panel");
    panel.hidden = id === "firemap";
    $("#btn-panel").setAttribute("aria-expanded", String(!panel.hidden));
    // A layout dependent on which sections are now visible (or gone) shifts
    // under the zoom control's own right edge.
    alignBasemapPicker();
  }

  /** Closes the app-switcher dropdown menu. */
  function closeAppSwitcher() {
    $("#app-switcher-menu").hidden = true;
    $("#app-switcher-toggle").setAttribute("aria-expanded", "false");
  }

  /** Wires the app-switcher toggle/menu (open/close/outside-click/Escape) and restores the last-selected app from localStorage. */
  function wireAppSwitcher() {
    const toggle = $("#app-switcher-toggle");
    const menu = $("#app-switcher-menu");
    toggle.addEventListener("click", () => {
      if (menu.hidden) {
        menu.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
      } else {
        closeAppSwitcher();
      }
    });
    $$("#app-switcher-menu [data-app-select]").forEach((btn) => {
      btn.addEventListener("click", () => {
        applyAppMode(btn.dataset.appSelect);
        closeAppSwitcher();
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !menu.hidden) {
        closeAppSwitcher();
        toggle.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest("#app-switcher")) closeAppSwitcher();
    });
    let stored = null;
    try {
      stored = localStorage.getItem("nexfiremap.app");
    } catch (_) {
      /* private mode/storage pressure - falls back to the firemap default */
    }
    applyAppMode(stored || "firemap");
  }

  // -------------------------------------------------------------- drawing

  /** Removes whichever detection render-mode layer is currently on the map (at most one of these is ever non-null) and nulls out all four handles. */
  function clearLayers() {
    [pointLayer, clusterLayer, heatLayer, spreadTopologyLayer].forEach((layer) => {
      if (layer && map.hasLayer(layer)) map.removeLayer(layer);
    });
    pointLayer = clusterLayer = heatLayer = spreadTopologyLayer = null;
  }

  /** @returns {number} the circleMarker radius (px) for the map's current zoom - coarser zoom, smaller dots, so dense clusters stay legible. */
  function radiusForZoom() {
    const z = map.getZoom();
    if (z <= 3) return 2.5;
    if (z <= 5) return 3.5;
    if (z <= 8) return 4.5;
    return 5.5;
  }

  // A FIRMS row gives pixel footprint size as scan/track in km (along-scan /
  // along-track), not an orientation - so this draws an axis-aligned ellipse
  // rather than one rotated to the satellite's actual heading. That's a
  // documented simplification (further_plan.md's own Stage-1 framing: a
  // sensor-correct footprint, not a precisely oriented one), still far more
  // honest than a fixed-radius dot at every zoom level.
  const KM_PER_DEG_LAT = 110.574;

  /**
   * Builds an axis-aligned ellipse polygon approximating a FIRMS pixel
   * footprint (see the comment above for why axis-aligned, not
   * heading-oriented).
   * @param {number} lat @param {number} lon - footprint center.
   * @param {number} scanKm - along-scan (east-west-ish) footprint size, km.
   * @param {number} trackKm - along-track (north-south-ish) footprint size, km.
   * @param {number} [segments=24] - polygon vertex count.
   * @returns {[number, number][]} lat/lon vertex ring for L.polygon.
   */
  function ellipseLatLngs(lat, lon, scanKm, trackKm, segments = 24) {
    const latRad = (lat * Math.PI) / 180;
    const kmPerDegLon = 111.320 * Math.cos(latRad) || 0.0001;
    const semiLon = scanKm / 2 / kmPerDegLon;
    const semiLat = trackKm / 2 / KM_PER_DEG_LAT;
    const points = [];
    for (let i = 0; i < segments; i++) {
      const t = (i / segments) * 2 * Math.PI;
      points.push([lat + semiLat * Math.sin(t), lon + semiLon * Math.cos(t)]);
    }
    return points;
  }

  /** @param {Array} row - a detection row. @returns {string} the HTML for a detection marker's popup (lazily invoked via `marker.bindPopup(() => popupHtml(row))`, not built eagerly for every row). */
  function popupHtml(row) {
    const meta = state.sources.get(row[5]) || {};
    const when = new Date(row[2] * 1000);
    const conf = row[4] ? row[4][0].toUpperCase() + row[4].slice(1) : "-";
    const footprint =
      row[9] && row[10] ? `${row[9].toFixed(2)} × ${row[10].toFixed(2)} km` : "-";
    return `
      <h3>${meta.label || row[5]}</h3>
      <dl class="popup-grid">
        <dt>Detected</dt><dd>${when.toISOString().slice(0, 16).replace("T", " ")} UTC</dd>
        <dt>Age</dt><dd>${ageLabel(Date.now() / 1000 - row[2])}</dd>
        <dt>FRP</dt><dd>${row[3] === null ? "-" : row[3].toFixed(1) + " MW"}</dd>
        <dt>Confidence</dt><dd>${conf}</dd>
        <dt>Brightness</dt><dd>${row[8] === null ? "-" : row[8].toFixed(1) + " K"}</dd>
        <dt>Overpass</dt><dd>${row[7] === "N" ? "Night" : row[7] === "D" ? "Day" : "-"}</dd>
        <dt>Satellite</dt><dd>${row[6] || "-"}</dd>
        <dt>Pixel footprint</dt><dd>${footprint}</dd>
        <dt>Resolution</dt><dd>${meta.resolution_m ? meta.resolution_m + " m" : "-"}</dd>
        <dt>Position</dt><dd>${row[0].toFixed(4)}, ${row[1].toFixed(4)}</dd>
      </dl>`;
  }

  // Rows currently on screen, honouring a scrubbed/playing time cursor -
  // playback never re-fetches, it just narrows what's already loaded.
  /** @returns {Array[]} state.rows, filtered to state.playback.cursor when playback is scrubbed/active; all rows otherwise. */
  function visibleRows() {
    const cursor = state.playback.cursor;
    if (cursor === null) return state.rows;
    return state.rows.filter((row) => row[2] <= cursor);
  }

  // ------------------------------------------------------------- playback

  /** Recomputes playback min/max from state.rows (called after every reload) and repositions the cursor - see the inline "wasAtEnd" comment for the stay-pinned-to-now behaviour. */
  function updatePlaybackRange() {
    const pb = state.playback;
    if (!state.rows.length) {
      pb.min = pb.max = null;
      syncPlaybackUI();
      return;
    }
    const newMin = state.rows.reduce((m, r) => Math.min(m, r[2]), Infinity);
    const newMax = state.rows.reduce((m, r) => Math.max(m, r[2]), -Infinity);
    // If the cursor was parked at "now" (the common case), keep tracking
    // "now" as fresh data arrives instead of freezing at the old edge. A
    // cursor scrubbed further back is left alone so a paused/rewound view
    // survives a background refresh.
    const wasAtEnd = pb.cursor === null || pb.max === null || pb.cursor >= pb.max;
    pb.min = newMin;
    pb.max = newMax;
    pb.cursor = wasAtEnd ? newMax : Math.max(newMin, Math.min(pb.cursor, newMax));
    syncPlaybackUI();
  }

  /** Syncs the playback slider's min/max/value and enabled state, and disables playback entirely when there's not enough time spread in view to animate. */
  function syncPlaybackUI() {
    const pb = state.playback;
    const slider = $("#playback-slider");
    const btn = $("#btn-playback");
    if (pb.min === null || pb.max === null || pb.min === pb.max) {
      slider.disabled = true;
      btn.disabled = true;
      $("#playback-readout").textContent = "Not enough time spread in view to animate.";
      return;
    }
    slider.disabled = false;
    btn.disabled = false;
    slider.min = String(pb.min);
    slider.max = String(pb.max);
    slider.value = String(pb.cursor);
    updatePlaybackReadout();
  }

  /** Updates the playback readout's text to match the current cursor position. */
  function updatePlaybackReadout() {
    const pb = state.playback;
    const out = $("#playback-readout");
    if (pb.cursor === null || pb.max === null) return;
    if (pb.cursor >= pb.max) {
      out.textContent = "Showing all loaded detections.";
      return;
    }
    const when = new Date(pb.cursor * 1000).toISOString().slice(0, 16).replace("T", " ");
    out.textContent = `Showing detections up to ${when} UTC`;
  }

  /** Starts the acquisition-time animation: a 150ms-tick interval that advances the cursor and redraws, stopping itself automatically at pb.max. */
  function startPlayback() {
    const pb = state.playback;
    if (pb.min === null || pb.max === null || pb.min === pb.max) return;
    if (pb.cursor === null || pb.cursor >= pb.max) pb.cursor = pb.min; // replay from the start
    pb.active = true;
    const btn = $("#btn-playback");
    btn.textContent = "⏸";
    btn.setAttribute("aria-label", "Pause acquisition-time animation");

    const span = pb.max - pb.min;
    pb.timer = setInterval(() => {
      // ~100 ticks end to end at speed 1 (~15s full sweep at the 150ms tick).
      pb.cursor = Math.min(pb.max, pb.cursor + span * 0.01 * pb.speed);
      $("#playback-slider").value = String(pb.cursor);
      updatePlaybackReadout();
      drawDetections();
      if (pb.cursor >= pb.max) stopPlayback();
    }, 150);
  }

  /** Stops the playback animation timer and resets the play/pause button. */
  function stopPlayback() {
    const pb = state.playback;
    pb.active = false;
    clearInterval(pb.timer);
    pb.timer = null;
    const btn = $("#btn-playback");
    btn.textContent = "▶";
    btn.setAttribute("aria-label", "Play acquisition-time animation");
  }

  /**
   * Renders state.rows (via visibleRows()) onto the map in whichever
   * state.renderMode is active:
   *  - "topology" delegates entirely to drawSpreadTopology() - a
   *    server-computed contour, not a per-point style, so it returns early
   *    before any of the client-side per-row styling below runs.
   *  - "heat" builds a single L.heatLayer weighted by normalised FRP.
   *  - "cluster" groups per-row circleMarkers into a marker-cluster layer.
   *  - "points" (default) draws one marker per row directly on the map,
   *    switching each marker from a fixed-radius circleMarker to a
   *    sensor-accurate footprint ellipse (ellipseLatLngs) once the zoom is
   *    past FOOTPRINT_MIN_ZOOM and scan/track data is present - below that
   *    zoom an ellipse would be visually indistinguishable from a dot, so
   *    the cheaper circleMarker is used instead.
   * The entry point for every redraw trigger: pan/zoom settling, source
   * toggles, color-by/render-mode changes, basemap-tone flips, and each
   * playback tick.
   */
  function drawDetections() {
    clearLayers();

    // Both spread renderings come from the same job result; they differ
    // only in how drawSpreadTopology paints it (nested time bands vs one
    // translucent affected-area fill).
    if (state.renderMode === "topology" || state.renderMode === "extent") {
      drawSpreadTopology();
      return;
    }

    const rows = visibleRows();
    if (!rows.length) return;

    const now = Date.now() / 1000;
    const ramp = fireRamp();
    const cats = catColors();
    // The surface ring keeps a mark legible wherever it lands on the tiles.
    const ring = state.basemapTone === "dark" ? "#0d0d0d" : "#fcfcfb";
    const radius = radiusForZoom();
    const zoom = map.getZoom();
    const useFootprints =
      state.renderMode === "points" && zoom >= FOOTPRINT_MIN_ZOOM;

    if (state.renderMode === "heat") {
      const maxFrp = rows.reduce((m, r) => Math.max(m, r[3] || 0), 1);
      heatLayer = L.heatLayer(
        rows.map((r) => [r[0], r[1], Math.min(1, ((r[3] || 1) / maxFrp) * 0.8 + 0.2)]),
        {
          radius: 18,
          blur: 22,
          maxZoom: 11,
          minOpacity: 0.25,
          gradient: { 0.2: ramp[0], 0.45: ramp[2], 0.75: ramp[3], 1.0: ramp[4] },
        }
      ).addTo(map);
      return;
    }

    const markers = rows.map((row) => {
      const style = {
        renderer: canvasRenderer,
        color: ring,
        weight: 1,
        opacity: 0.85,
        fillColor: colorForRow(row, now, ramp, cats),
        fillOpacity: 0.92,
      };
      const marker =
        useFootprints && row[9] && row[10]
          ? L.polygon(ellipseLatLngs(row[0], row[1], row[9], row[10]), style)
          : L.circleMarker([row[0], row[1]], { ...style, radius });
      marker.bindPopup(() => popupHtml(row), { maxWidth: 280 });
      return marker;
    });

    if (state.renderMode === "cluster") {
      clusterLayer = L.markerClusterGroup({
        chunkedLoading: true,
        maxClusterRadius: 48,
        showCoverageOnHover: false,
        disableClusteringAtZoom: 12,
      });
      clusterLayer.addLayers(markers);
      clusterLayer.addTo(map);
    } else {
      pointLayer = L.layerGroup(markers).addTo(map);
    }
  }

  // "Spread over Time" can't be drawn from state.rows client-side like every
  // other render mode - the nested bands are a server-computed contour of
  // the *cumulative* detection footprint at up to 5 cutoffs, one per
  // distinct satellite overpass (see events.py's spread_topology), not a
  // per-point style function.
  //
  // drawDetections() (and everything that calls it: pan/zoom settling,
  // render-mode/basemap-tone switches, ...) can fire several times within
  // milliseconds of each other during one user action - each one used to
  // submit its own background job immediately, so a single settling pan
  // could queue half a dozen redundant jobs, and worse, created a real
  // race: whichever job happened to *finish* last won, even if an earlier
  // one (for the exact same final, now-settled view) had already
  // rendered a perfectly good result - an unrelated late failure could
  // blank out a result that was already correct on screen. Debouncing the
  // actual submission (this function) the same way loadDetections' own
  // pan/zoom trigger already is (scheduleLoad, app.js:1120) fixes both:
  // only the final, truly-settled view ever submits a job.
  /** Shows a "computing…" placeholder in the legend and (re)arms the 400ms debounce that eventually calls submitSpreadTopologyJob(). */
  function drawSpreadTopology() {
    clearTimeout(spreadTopologyDebounceTimer);
    const legend = $("#legend");
    legend.innerHTML = `<div class="legend-title">Spread over Time</div><p class="hint">Computing spread over time…</p>`;
    spreadTopologyDebounceTimer = setTimeout(submitSpreadTopologyJob, 400);
  }

  // Submits the job and polls it, the same submit/waitForJob idiom
  // detectEvents() uses (app.js:1521) - errors surface in the legend area
  // rather than leaving the map silently blank, since this is the one
  // render mode where "nothing drew", "still computing", and "the job
  // failed" would otherwise look identical.
  /**
   * Submits a spread_topology job for the current view/days/sources, waits
   * for it, then hands the resulting GeoJSON to renderSpreadTopologyLayer().
   * Guards every await point with `stillCurrent()` against
   * spreadTopologyGeneration so a superseded call (newer pan/zoom/render-
   * mode switch) never overwrites a more recent result - see makeStaleGuard's
   * comment for the same pattern in object form.
   */
  async function submitSpreadTopologyJob() {
    const myGeneration = ++spreadTopologyGeneration;
    const stillCurrent = () => spreadTopologyGeneration === myGeneration;
    const legend = $("#legend");
    const showLegendMessage = (msg) => {
      legend.innerHTML = `<div class="legend-title">Spread over Time</div><p class="hint">${escapeHtml(msg)}</p>`;
    };

    if (!state.enabledSources.size) {
      showLegendMessage("No sources enabled.");
      return;
    }

    try {
      const res = await fetch("/api/detections/spread_topology", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          // Padded, not the bare viewport. The bands are contours of a
          // detection-density field, so tracing one needs the *boundary* to be
          // inside the raster. Zoomed in far enough that the whole view sits
          // within a detection's own footprint, every cell is above the
          // contour level, there is no boundary to trace, and the layer came
          // back empty - the map simply lost its spread overlay the closer you
          // looked at the fire. Padding by a minimum ground distance keeps the
          // transition in frame at any zoom.
          bbox: spreadTopologyBbox(),
          days: state.days,
          sources: Array.from(state.enabledSources),
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { job_id } = await res.json();
      const job = await waitForJob(job_id, { timeoutMs: 60000, intervalMs: 1000 });
      if (!stillCurrent()) return; // superseded by a newer pan/zoom/render-mode switch

      const result = job.result;
      if (!result.files.topology) {
        state.lastSpreadTopologyRange = null;
        showLegendMessage("No detections in this view.");
        return;
      }
      const geoRes = await fetch(`/api/jobs/${job_id}/files/${result.files.topology}`);
      if (!geoRes.ok) throw new Error(`HTTP ${geoRes.status} fetching contour result`);
      const geo = await geoRes.json();
      if (!stillCurrent()) return;

      renderSpreadTopologyLayer(geo);
    } catch (err) {
      if (!stillCurrent()) return;
      console.error(err);
      showLegendMessage(`Couldn't compute: ${err.message || String(err)}`);
    }
  }

  /** @param {object} geo - the spread_topology GeoJSON FeatureCollection (each feature carrying cutoff_ts/band_index/band_fraction/detection_count - band_fraction may be absent on results produced before it was added, see the fillColor fallback below). Draws the layer, refreshes state.lastSpreadTopologyRange, and re-renders the legend. */
  function renderSpreadTopologyLayer(geo) {
    if (spreadTopologyLayer && map.hasLayer(spreadTopologyLayer)) map.removeLayer(spreadTopologyLayer);

    if (!geo.features.length) {
      state.lastSpreadTopologyRange = null;
      renderLegend();
      return;
    }

    const cutoffs = geo.features.map((f) => f.properties.cutoff_ts);
    state.lastSpreadTopologyRange = { earliest: Math.min(...cutoffs), latest: Math.max(...cutoffs) };

    const ramp = timeRamp();
    // Largest/latest band first, smallest/earliest last - mirrors
    // drawProbabilityEnvelopes' concentric-ring comment (app.js:1627), but
    // the size/color relationship here is the opposite of that function's:
    // there, a smaller mass is more *confident*; here, a smaller band is
    // *earlier* (cumulative construction - see spread_topology's docstring).
    // Near-opaque fill (not drawProbabilityEnvelopes' ~0.18), deliberately:
    // these bands are cumulative supersets of each other, so the innermost
    // area sits under every single band at once - a translucent fill (first
    // tried at 0.55) compounds across all of them there (alpha stacking:
    // 1-(1-0.55)^5 ~= 0.98), washing the center out to a muddy near-solid
    // blend regardless of which color is nominally "on top", which is
    // exactly backwards from what this encoding needs (each pixel should
    // show *one* band's actual color, the most recent one covering it).
    // Opaque fill sidesteps the compounding entirely: each later, smaller
    // band drawn on top cleanly overwrites the larger one beneath it in
    // its footprint, rather than blending into it - the correct read for
    // "this area's own most-recent color", not confidence density.
    const sorted = {
      ...geo,
      features: [...geo.features].sort((a, b) => b.properties.band_index - a.properties.band_index),
    };
    const ring = state.basemapTone === "dark" ? "#fcfcfb" : "#0d0d0d";

    // "Extent" mode: one translucent fill of the affected area instead of the
    // nested time bands - the plain "where is this fire" read, for briefings
    // and public-facing views where the progression is noise rather than
    // signal. Deliberately built from the *latest* band rather than from a
    // convex hull of the detection points: the latest band is already the
    // cumulative footprint, and it comes from the same density contour as
    // every other band, so it follows the real outer wall. A convex hull
    // would "fill valleys, lakes, unburned islands and disconnected
    // activity" - see events.py's module docstring quoting further_plan.md
    // section 5, which is exactly what this project refuses to draw.
    if (state.renderMode === "extent") {
      const latest = Math.max(...geo.features.map((f) => f.properties.band_index));
      spreadTopologyLayer = L.geoJSON(
        { ...geo, features: geo.features.filter((f) => f.properties.band_index === latest) },
        {
          style: () => ({
            color: SPREAD_EXTENT_OUTLINE,
            fillColor: SPREAD_EXTENT_FILL,
            weight: 2,
            opacity: 0.95,
            fillOpacity: 0.45,
          }),
          onEachFeature: (feature, layer) => {
            const when = new Date(feature.properties.cutoff_ts * 1000)
              .toISOString().slice(0, 16).replace("T", " ");
            layer.bindTooltip(
              `affected area as of ${when} UTC · ${feature.properties.detection_count} detection(s)`);
          },
        }
      ).addTo(map);
      renderLegend();
      return;
    }

    spreadTopologyLayer = L.geoJSON(sorted, {
      style: (feature) => ({
        // band_fraction (0=earliest..1=latest) interpolates continuously
        // across the 5-stop ramp instead of snapping to one of its 5
        // stops directly, so bands stay visually distinct even when
        // SPREAD_TOPOLOGY_BANDS produces more of them than the ramp has
        // stops (see events.py's spread_topology docstring). Falls back
        // to the old fixed-LUT position for job results cached before
        // band_fraction existed, so they still render sensibly.
        fillColor: timeSpreadColor(
          feature.properties.band_fraction ?? feature.properties.band_index / 4,
          ramp
        ),
        color: ring,
        weight: 1,
        opacity: 0.5,
        // 0.75 rather than the near-opaque 0.94 this used to carry: the bands
        // are cumulative supersets drawn latest-first, so each earlier band
        // paints over the larger one beneath it and only ever shows its own
        // colour - the alpha-stacking problem the old comment worried about
        // does not arise from the *fill* itself. At 0.75 the basemap stays
        // legible underneath, which is what an operator needs to relate the
        // burn progression to the terrain and roads it is crossing.
        fillOpacity: 0.75,
      }),
      onEachFeature: (feature, layer) => {
        const when = new Date(feature.properties.cutoff_ts * 1000).toISOString().slice(0, 16).replace("T", " ");
        layer.bindTooltip(`as of ${when} UTC · ${feature.properties.detection_count} detection(s)`);
      },
    }).addTo(map);

    renderLegend();
  }

  // --------------------------------------------------------------- legend

  /**
   * Rebuilds the #legend panel to match the current renderMode/colorBy:
   * a gradient bar with real UTC timestamps for "topology" mode, a
   * categorical swatch list for colorBy "instrument", or an age/FRP ramp
   * otherwise. Called after every mode/tone change so the legend never
   * shows stale bins for what's actually drawn.
   */
  function renderLegend() {
    const el = $("#legend");

    if (state.renderMode === "topology") {
      const range = state.lastSpreadTopologyRange;
      // Real timestamps, not bare "Earlier"/"Later" labels - this project's
      // established convention (see popupHtml's own "Detected" field) of
      // showing the actual value rather than a vague relative one. Falls
      // back to the plain labels only when nothing has rendered yet (job
      // still running, or none of the bands returned any contour).
      const fmt = (ts) => new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
      const labels = range
        ? [fmt(range.earliest), fmt(range.latest)]
        : ["Earlier", "Later"];
      // No inline gradient here - .legend-gradient's own CSS already builds
      // it from the same --time-* vars timeRamp() reads, so the legend bar
      // and the marks it describes can never drift out of sync.
      el.innerHTML = `
        <div class="legend-title">Spread over Time</div>
        <div class="legend-gradient"></div>
        <div class="legend-gradient-labels">
          ${labels.map((l) => `<span>${escapeHtml(l)}</span>`).join("")}
        </div>`;
      return;
    }

    const ramp = fireRamp();

    if (state.colorBy === "instrument") {
      const cats = catColors();
      el.innerHTML = `
        <div class="legend-title">Instrument</div>
        <div class="legend-items">
          ${state.instruments
            .map(
              (name, i) =>
                `<span class="legend-item"><span class="swatch" style="background:${
                  cats[i % cats.length]
                }"></span>${name}</span>`
            )
            .join("")}
        </div>`;
      return;
    }

    const bins = state.colorBy === "frp" ? FRP_BINS : AGE_BINS;
    const title =
      state.colorBy === "frp"
        ? "Fire radiative power (MW)"
        : "Time since detection";
    // FRP reads the ramp forward (low->high power = recede->prominent,
    // matching colorForRow) - age reads it mirrored, since colorForRow
    // mirrors it too - the legend has to use the exact same order as
    // what's actually drawn, or it lies about which color means what.
    const displayRamp = state.colorBy === "frp" ? ramp : [...ramp].reverse();
    el.innerHTML = `
      <div class="legend-title">${title}</div>
      <div class="legend-ramp">
        ${displayRamp.map((c) => `<span class="step" style="background:${c}"></span>`).join("")}
      </div>
      <div class="legend-scale">
        ${bins.map((b) => `<span>${b.label}</span>`).join("")}
      </div>`;
  }

  // ------------------------------------------------------------ histogram

  /**
   * Renders the per-day detection-count bar chart from state.summary as
   * hand-built SVG (no charting library) - bars inside the active `days`
   * window (or the selected focusDay) are highlighted; clicking a bar
   * toggles it as the focus day and reloads detections for just that day.
   */
  function renderHistogram() {
    const host = $("#histogram");
    const data = state.summary;
    host.innerHTML = "";
    if (!data.length) {
      host.innerHTML = `<p class="hint">No cached detections for this area yet.</p>`;
      return;
    }

    const width = host.clientWidth || 300;
    const height = 86;
    const padBottom = 14;
    const plotH = height - padBottom;
    const gap = 2;
    const n = data.length;
    const barW = Math.min(24, Math.max(2, (width - gap * (n - 1)) / n));
    const step = n > 1 ? (width - barW) / (n - 1) : 0;
    const max = Math.max(...data.map((d) => d.count), 1);

    const windowStart = new Date(Date.now() - state.days * DAY * 1000)
      .toISOString()
      .slice(0, 10);

    const dim = css("--fire-1");
    const accent = css("--fire-4");

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("preserveAspectRatio", "none");

    const ns = "http://www.w3.org/2000/svg";
    const make = (tag, attrs) => {
      const node = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
      return node;
    };

    data.forEach((d, i) => {
      const h = Math.max(1.5, (d.count / max) * (plotH - 4));
      const x = i * step;
      const y = plotH - h;
      const r = Math.min(4, h / 2, barW / 2);
      // Rounded data-end, square where it meets the baseline.
      const path = `M${x} ${plotH} L${x} ${y + r} Q${x} ${y} ${x + r} ${y}
                    L${x + barW - r} ${y} Q${x + barW} ${y} ${x + barW} ${y + r}
                    L${x + barW} ${plotH} Z`;
      const inWindow = d.day >= windowStart;
      const selected = state.focusDay === d.day;
      const bar = make("path", {
        d: path,
        class: "bar",
        fill: selected || (inWindow && !state.focusDay) ? accent : dim,
        "data-day": d.day,
      });
      bar.addEventListener("click", () => {
        state.focusDay = state.focusDay === d.day ? null : d.day;
        $("#btn-clear-day").hidden = !state.focusDay;
        renderHistogram();
        loadDetections();
      });
      bar.addEventListener("mousemove", (e) => showTooltip(e, d));
      bar.addEventListener("mouseleave", hideTooltip);
      svg.appendChild(bar);
    });

    svg.appendChild(
      make("line", { x1: 0, y1: plotH, x2: width, y2: plotH, class: "axis-line" })
    );

    const first = make("text", { x: 0, y: height - 2, class: "tick" });
    first.textContent = shortDate(data[0].day);
    const last = make("text", {
      x: width,
      y: height - 2,
      class: "tick",
      "text-anchor": "end",
    });
    last.textContent = shortDate(data[data.length - 1].day);
    const peak = make("text", { x: 0, y: 9, class: "tick" });
    peak.textContent = `peak ${compact(max)}`;
    svg.append(first, last, peak);

    host.appendChild(svg);
  }

  /** Positions and fills the shared #tooltip element for a hovered histogram bar. @param {MouseEvent} event @param {{day: string, count: number, frp_total: number}} d */
  function showTooltip(event, d) {
    const tip = $("#tooltip");
    tip.innerHTML = `<b>${compact(d.count)}</b> <span>detections</span><br><span>${shortDate(
      d.day
    )} · ${compact(d.frp_total)} MW total</span>`;
    tip.hidden = false;
    tip.style.left = `${Math.min(event.clientX + 12, window.innerWidth - 190)}px`;
    tip.style.top = `${event.clientY - 46}px`;
  }

  /** Hides the shared histogram tooltip. */
  function hideTooltip() {
    $("#tooltip").hidden = true;
  }

  // ----------------------------------------------------------- stat tiles

  /** Fills the count/FRP-total/peak-day/days-cached stat tiles from state.rows and state.summary. @param {object|null} meta - the detections response's own meta (used only for the "truncated" `+` suffix). */
  function renderStats(meta) {
    $("#stat-count").textContent = compact(state.rows.length) +
      (meta && meta.truncated ? "+" : "");
    const frpTotal = state.rows.reduce((sum, r) => sum + (r[3] || 0), 0);
    $("#stat-frp").innerHTML = `${compact(frpTotal)} <small>MW</small>`;

    if (state.summary.length) {
      const peak = state.summary.reduce((a, b) => (b.count > a.count ? b : a));
      $("#stat-peak").innerHTML = `${shortDate(peak.day)} <small>${compact(
        peak.count
      )}</small>`;
      $("#stat-days").innerHTML = `${state.summary.length} <small>days</small>`;
    } else {
      $("#stat-peak").textContent = "-";
      $("#stat-days").textContent = "-";
    }
  }

  // ------------------------------------------------------------ data load

  /** @returns {string} the map's current viewport as a "west,south,east,north" query param, longitude clamped to ±180 (worldCopyJump can otherwise report bounds outside that range). */
  /** The bbox to compute spread bands over: the viewport, padded outward.
   *
   * A contour needs both sides of its own level inside the raster. At high
   * zoom the viewport can sit entirely inside the burn footprint, where the
   * density field is above the contour level everywhere and marching squares
   * has nothing to trace - so the layer silently emptied exactly when the
   * operator zoomed in to look closely.
   *
   * `SPREAD_TOPOLOGY_MIN_PAD_DEG` is a floor rather than a pure percentage
   * because a percentage of a very small viewport is still very small; roughly
   * 2 km covers a VIIRS footprint radius plus margin, which is the scale the
   * density kernel works at.
   * @returns {string} "west,south,east,north". */
  function spreadTopologyBbox() {
    const b = map.getBounds();
    const padLon = Math.max((b.getEast() - b.getWest()) * 0.5, SPREAD_TOPOLOGY_MIN_PAD_DEG);
    const padLat = Math.max((b.getNorth() - b.getSouth()) * 0.5, SPREAD_TOPOLOGY_MIN_PAD_DEG);
    return [
      Math.max(-180, b.getWest() - padLon),
      Math.max(-90, b.getSouth() - padLat),
      Math.min(180, b.getEast() + padLon),
      Math.min(90, b.getNorth() + padLat),
    ].map((v) => v.toFixed(4)).join(",");
  }

  function bboxParam() {
    const b = map.getBounds();
    const west = Math.max(-180, b.getWest());
    const east = Math.min(180, b.getEast());
    return [west, b.getSouth(), east, b.getNorth()]
      .map((v) => v.toFixed(4))
      .join(",");
  }

  /** @returns {URLSearchParams} bbox + enabled sources + confidence/min-FRP/day-night filters for /api/detections (days/start/end are added separately by loadDetections, since focusDay changes which of those apply). */
  function filterParams() {
    const params = new URLSearchParams();
    params.set("bbox", bboxParam());
    params.set("sources", Array.from(state.enabledSources).join(","));
    const conf = $$(".f-conf:checked").map((el) => el.value);
    if (conf.length && conf.length < 3) params.set("confidence", conf.join(","));
    if (state.minFrp > 0) params.set("min_frp", String(state.minFrp));
    if (state.daynight) params.set("daynight", state.daynight);
    return params;
  }

  /**
   * Debounce entry point for loadDetections(): every filter/pan/zoom
   * trigger calls this (not loadDetections directly), so a burst of rapid
   * changes - a fast pan, several filter checkboxes toggled in succession -
   * collapses into one fetch fired `delay` ms after the last one, instead
   * of one fetch per intermediate step.
   * @param {number} [delay=250] - debounce window in ms; callers close to
   *   the user's own action (checkbox/slider changes) tend to pass a
   *   shorter one (120ms) than the map's own moveend/zoomend (450ms),
   *   since a still-settling pan gesture benefits from the longer wait.
   */
  function scheduleLoad(delay = 250) {
    clearTimeout(state.loadTimer);
    state.loadTimer = setTimeout(loadDetections, delay);
  }

  // The four piggyback layers only ever get (re)populated from inside
  // loadDetections' success path below - its two early-return branches
  // (zoomed out, no sources enabled) used to just skip that, leaving
  // whatever those layers last showed for a *different*, zoomed-in
  // viewport still on the map instead of clearing or refreshing it.
  /** Clears every viewport-piggybacked layer (coverage/industrial/eumetsat/events) and their retry timers - see the comment above for why this exists as its own function rather than being inlined at each early-return. */
  function clearAutofetchLayers() {
    const zoomHint = "Zoom in (or pick a source) to load this layer for the current view.";

    clearTimeout(coverageRetryTimer);
    coverageRetryTimer = null;
    if (coverageLayer) {
      map.removeLayer(coverageLayer);
      coverageLayer = null;
    }
    if (state.coverageEnabled) $("#coverage-status").textContent = zoomHint;

    clearTimeout(industrialRetryTimer);
    industrialRetryTimer = null;
    if (industrialLayer) {
      map.removeLayer(industrialLayer);
      industrialLayer = null;
    }
    if (state.industrialEnabled) $("#industrial-status").textContent = zoomHint;

    clearTimeout(eumetsatRetryTimer);
    eumetsatRetryTimer = null;
    if (eumetsatLayer) {
      map.removeLayer(eumetsatLayer);
      eumetsatLayer = null;
    }
    if (state.eumetsatEnabled) $("#eumetsat-status").textContent = zoomHint;

    clearTimeout(eventsRetryTimer);
    eventsRetryTimer = null;
    state.events = [];
    renderEventList();
    renderEventMarkers();
  }

  /**
   * The core viewport-driven fetch engine: pulls /api/detections +
   * /api/summary for the current bbox/filters, redraws the map, and
   * kicks off every layer that piggybacks on the same viewport (coverage,
   * events, industrial sources, EUMETSAT). Never called directly from UI
   * handlers - always through scheduleLoad()'s debounce.
   *
   * Cancellation: any in-flight call is aborted (via state.inflight's
   * AbortController) the moment a new call starts, so a slow response for
   * an old viewport can never land after - and overwrite - a newer one.
   * This is the same "only the latest request wins" goal as
   * makeStaleGuard()'s token pattern used elsewhere, but implemented with
   * AbortController instead because this fetch is expensive enough
   * (largest payload, drives the most downstream work) to be worth
   * actually cancelling over the network rather than just ignoring the
   * response once it arrives.
   */
  async function loadDetections() {
    // Below MIN_ACTIVE_ZOOM the viewport is "most of a continent or more" -
    // skip the fetch (and everything piggybacking on it: coverage/events/
    // industrial autofetch below) entirely rather than loading/computing for
    // that much of the world on every pan. Zooming in resumes automatically,
    // since this same function reruns on every moveend/zoomend.
    const zoomedOut = map.getZoom() < MIN_ACTIVE_ZOOM;
    if (zoomedOut) {
      state.rows = [];
      updatePlaybackRange();
      drawDetections();
      renderStats(null);
      clearAutofetchLayers();
      return;
    }

    if (!state.enabledSources.size) {
      state.rows = [];
      updatePlaybackRange();
      drawDetections();
      renderStats(null);
      clearAutofetchLayers();
      return;
    }

    const params = filterParams();
    if (state.focusDay) {
      params.set("start", state.focusDay);
      params.set("end", state.focusDay + "T23:59:59");
    } else {
      params.set("days", String(state.days));
    }
    params.set("autofetch", "true");
    params.set("limit", "40000");

    if (state.inflight) state.inflight.abort();
    const controller = new AbortController();
    state.inflight = controller;

    try {
      const [detRes, sumRes] = await Promise.all([
        fetch(`/api/detections?${params}`, { signal: controller.signal }),
        fetch(
          `/api/summary?${new URLSearchParams({
            bbox: bboxParam(),
            sources: Array.from(state.enabledSources).join(","),
            days: String(state.config.cache_days),
          })}`,
          { signal: controller.signal }
        ),
      ]);
      if (!detRes.ok) throw new Error(`detections: HTTP ${detRes.status}`);

      const detections = await detRes.json();
      state.rows = detections.rows;
      state.summary = sumRes.ok ? (await sumRes.json()).days : [];

      updatePlaybackRange();
      drawDetections();
      renderHistogram();
      renderStats(detections.meta);

      if (detections.meta.pending > 0) startPolling();
      loadCoverage(); // same viewport/day, so piggybacks on this debounce
      loadEvents(); // just a DB read - the expensive clustering stays a button
      loadIndustrialSources(); // also just a DB read - scanning stays a button
      loadEumetsatFires(); // ditto - a DB read, area-unbounded product downloads stay autofetch-gated by staleness only
    } catch (err) {
      if (err.name !== "AbortError") console.error(err);
    } finally {
      if (state.inflight === controller) state.inflight = null;
    }
  }

  // -------------------------------------------------------------- coverage

  const coverageGuard = makeStaleGuard();

  /**
   * Fetches and draws the satellite-coverage mesh for the current
   * viewport/focusDay, guarded by coverageGuard against a stale response
   * overwriting a newer one. Retries once after 5s if the response came
   * back with a job still queued (see the trailing comment on why one
   * retry, not a poll loop).
   */
  async function loadCoverage() {
    clearTimeout(coverageRetryTimer);
    coverageRetryTimer = null;
    const myToken = coverageGuard.next();

    if (!state.coverageEnabled) {
      if (coverageLayer) {
        map.removeLayer(coverageLayer);
        coverageLayer = null;
      }
      return;
    }

    const day = state.focusDay || new Date().toISOString().slice(0, 10);
    const params = new URLSearchParams({ bbox: bboxParam(), day, autofetch: "true" });
    const status = $("#coverage-status");

    try {
      const res = await fetch(`/api/coverage?${params}`);
      if (!coverageGuard.isCurrent(myToken)) return; // a newer call has since superseded this one
      if (!res.ok) {
        status.textContent = `Couldn't load satellite coverage (HTTP ${res.status}).`;
        return;
      }
      const geo = await res.json();

      if (coverageLayer) map.removeLayer(coverageLayer);
      const fill = state.basemapTone === "dark" ? "#ffffff" : "#0b0b0b";
      // Wide-swath polar orbiters cover almost the entire globe within a
      // day, so "seen at all today" is nearly always true and not worth
      // drawing - what's informative is *how recently*: fresher passes
      // stay a little more visible, older ones fade toward nothing. A
      // neutral tone (not the fire ramp) keeps "satellite looked here"
      // from being mistaken for "fire detected here".
      coverageLayer = L.geoJSON(geo, {
        pane: "coveragePane",
        style: (feature) => {
          const hoursAgo = feature.properties.hours_ago;
          const opacity =
            hoursAgo === null ? 0.02 : Math.max(0.02, 0.16 - hoursAgo / 24 * 0.14);
          return { fillColor: fill, fillOpacity: opacity, stroke: false };
        },
      }).addTo(map);

      const count = geo.features.length;
      status.textContent = count
        ? `${count} grid cell${count === 1 ? "" : "s"} tracked for ${day} - fresher passes shown brighter.`
        : `No coverage computed yet for ${day} in this view.`;

      // Swath propagation is a background job - if it just got queued, the
      // first response is typically empty - a single delayed retry usually
      // picks up the finished result without building a full poll loop.
      if (geo.meta && geo.meta.job_id && !coverageRetryTimer) {
        coverageRetryTimer = setTimeout(loadCoverage, 5000);
      }
    } catch (err) {
      if (!coverageGuard.isCurrent(myToken)) return;
      console.error(err);
      status.textContent = "Couldn't load satellite coverage (network error).";
    }
  }

  // ---------------------------------------------------------- industrial

  const INDUSTRIAL_STYLE = {
    persistent_industrial: { color: "#9a9a94", radius: 6, label: "persistent industrial" },
    ambiguous: { color: "#e0b84d", radius: 5, label: "ambiguous" },
    possible_industrial_incident: { color: "#fd7924", radius: 6, label: "possible incident - wildfire may have crossed a known source" },
    insufficient_evidence: { color: "#5c5c58", radius: 3, label: "candidate, not enough evidence yet" },
  };

  const industrialGuard = makeStaleGuard();

  /**
   * Fetches and draws known industrial-source candidates for the current
   * viewport, guarded by industrialGuard, styled per INDUSTRIAL_STYLE by
   * classification. Retries once after 6s if a scan job was just queued
   * for a not-yet-seen viewport.
   */
  async function loadIndustrialSources() {
    clearTimeout(industrialRetryTimer);
    industrialRetryTimer = null;
    const myToken = industrialGuard.next();

    if (industrialLayer) {
      map.removeLayer(industrialLayer);
      industrialLayer = null;
    }
    if (!state.industrialEnabled) return;

    const status = $("#industrial-status");
    try {
      const params = new URLSearchParams({ bbox: bboxParam(), autofetch: "true" });
      const res = await fetch(`/api/industrial/sources?${params}`);
      if (!industrialGuard.isCurrent(myToken)) return; // a newer call has since superseded this one
      if (!res.ok) {
        status.textContent = `Couldn't load industrial sources (HTTP ${res.status}).`;
        return;
      }
      const { sources, meta } = await res.json();

      industrialLayer = L.layerGroup();
      for (const src of sources) {
        const style = INDUSTRIAL_STYLE[src.classification] || INDUSTRIAL_STYLE.insufficient_evidence;
        const marker = L.circleMarker([src.lat, src.lon], {
          pane: "industrialPane",
          radius: style.radius,
          color: "#0b0b0a",
          weight: 1,
          fillColor: style.color,
          fillOpacity: 0.85,
        });
        // OSM tags are freely crowd-editable - escape before inserting into
        // popup HTML rather than trusting them like the rest of this
        // string's own (fixed, non-external) text.
        const tagList = Object.entries(src.tags || {})
          .map(([k, v]) => `${escapeHtml(k)}=${escapeHtml(v)}`)
          .join(", ");
        marker.bindPopup(
          `<strong>${escapeHtml(style.label)}</strong><br>` +
            `${escapeHtml(src.evidence_class)} OSM evidence · score ${src.score != null ? src.score.toFixed(2) : "n/a"}<br>` +
            `${src.detection_count ?? 0} cached detection(s) nearby, ${src.match_radius_km ?? "?"}km match radius<br>` +
            `<span class="hint">${tagList || "(no tags)"}</span>`
        );
        industrialLayer.addLayer(marker);
      }
      industrialLayer.addTo(map);

      status.textContent = sources.length
        ? `${sources.length} known candidate(s) in view - never hidden or deleted, just distinguished.`
        : meta.job_id
          ? `Scanning this view for the first time (queued automatically)…`
          : `No candidates known for this view (skipped: viewport too large to auto-scan - use "rescan now" to force it, or zoom in).`;

      // A scan was just queued for this view - the response above is
      // whatever was already cached (often nothing yet), so check back
      // once the background job's had a chance to land, same pattern as
      // the Coverage layer's own autofetch retry.
      if (meta.job_id && !industrialRetryTimer) {
        industrialRetryTimer = setTimeout(loadIndustrialSources, 6000);
      }
    } catch (err) {
      if (!industrialGuard.isCurrent(myToken)) return;
      console.error(err);
      status.textContent = "Couldn't load industrial sources (network error).";
    }
  }

  /** Manually triggers (and waits for) an industrial-source scan for the current viewport via the "rescan now" button, then reloads the layer. */
  async function scanIndustrial() {
    const btn = $("#btn-scan-industrial");
    const status = $("#industrial-status");
    btn.disabled = true;
    btn.textContent = "scanning OpenStreetMap + cached history…";
    try {
      const res = await fetch("/api/industrial/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // Pass the selected event when there is one. The server's
        // "wildfire overrides industrial" safeguard - which reclassifies a
        // persistent source as a possible incident once detections outgrow its
        // footprint - only runs for a named event, so omitting this left the
        // safeguard switched off from the UI no matter what the fire did.
        body: JSON.stringify({
          bbox: bboxParam(),
          ...(state.selectedEventId ? { event_id: state.selectedEventId } : {}),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const { job_id } = await res.json();
      const job = await waitForJob(job_id, { timeoutMs: 60000, intervalMs: 1500 });
      status.textContent =
        `Scanned: ${job.result.candidate_count} candidate(s) found, ` +
        `${Object.values(job.result.classification_counts || {}).reduce((a, b) => a + b, 0)} detection(s) matched.`;
      if (!state.industrialEnabled) {
        state.industrialEnabled = true;
        $("#industrial-toggle").checked = true;
      }
      await loadIndustrialSources();
    } catch (err) {
      status.textContent = `Scan failed: ${err.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = "rescan now";
    }
  }

  // ------------------------------------------------------------- eumetsat

  // Deliberately not the FIRMS/detections colour family (oranges/reds) -
  // this is a second, independently-sourced instrument, and should read as
  // visually distinct at a glance, not as more of the same dots.
  const EUMETSAT_STYLE = {
    low: { color: "#5fb0d9", radius: 3, opacity: 0.55 },
    medium: { color: "#2f8fc9", radius: 4, opacity: 0.75 },
    high: { color: "#0b5fa8", radius: 5, opacity: 0.95 },
  };

  const eumetsatGuard = makeStaleGuard();

  /**
   * Fetches and draws recent EUMETSAT MTG/FCI fire detections for the
   * current viewport, guarded by eumetsatGuard. A no-op (early return,
   * still clearing the layer) when the feature isn't enabled or its
   * consumer key isn't configured server-side. Retries once after 8s if a
   * fetch job was just queued.
   */
  async function loadEumetsatFires() {
    clearTimeout(eumetsatRetryTimer);
    eumetsatRetryTimer = null;
    const myToken = eumetsatGuard.next();

    if (eumetsatLayer) {
      map.removeLayer(eumetsatLayer);
      eumetsatLayer = null;
    }
    if (!state.eumetsatEnabled) return;

    const status = $("#eumetsat-status");
    try {
      const params = new URLSearchParams({ bbox: bboxParam(), autofetch: "true" });
      const res = await fetch(`/api/eumetsat/fires?${params}`);
      if (!eumetsatGuard.isCurrent(myToken)) return; // a newer call has since superseded this one
      if (!res.ok) {
        status.textContent = `Couldn't load EUMETSAT fires (HTTP ${res.status}).`;
        return;
      }
      const { fires, meta } = await res.json();

      if (!meta.available) {
        status.textContent = "Not configured - add EUMETSAT_CONSUMER_KEY/SECRET to .env (see README).";
        return;
      }

      eumetsatLayer = L.layerGroup();
      for (const fire of fires) {
        const style = EUMETSAT_STYLE[fire.confidence] || EUMETSAT_STYLE.low;
        const marker = L.circleMarker([fire.lat, fire.lon], {
          pane: "industrialPane", // same "context, not a detection" stacking as industrial sources
          radius: style.radius,
          color: "#06283d",
          weight: 1,
          fillColor: style.color,
          fillOpacity: style.opacity,
        });
        marker.bindPopup(
          `<strong>EUMETSAT MTG/FCI - ${fire.confidence} confidence</strong><br>` +
            (fire.probability != null ? `probability ${(fire.probability * 100).toFixed(0)}%<br>` : "") +
            `${ageLabel(Math.max(0, Date.now() / 1000 - fire.acq_ts))}`
        );
        eumetsatLayer.addLayer(marker);
      }
      eumetsatLayer.addTo(map);

      status.textContent = fires.length
        ? `${fires.length} independent detection(s) in the last 6h - ~10min revisit, Europe/Africa/Atlantic only.`
        : meta.job_id
          ? "Checking for recent EUMETSAT products (queued automatically)…"
          : "No independent detections in view in the last 6h.";

      if (meta.job_id && !eumetsatRetryTimer) {
        eumetsatRetryTimer = setTimeout(loadEumetsatFires, 8000);
      }
    } catch (err) {
      if (!eumetsatGuard.isCurrent(myToken)) return;
      console.error(err);
      status.textContent = "Couldn't load EUMETSAT fires (network error).";
    }
  }

  // ------------------------------------------------------------- fire events
  //
  // "Observed" (raw detections, already on screen) vs "Estimated" (the
  // likelihood/arrival-time rasters below) vs "Uncertainty" (envelope
  // contours) are kept as distinct, separately-labelled layers throughout -
  // further_plan.md's core ask - rather than one blended picture.

  const eventsGuard = makeStaleGuard();

  /**
   * Fetches and renders clustered fire events for the current viewport
   * (list + map bbox rectangles), guarded by eventsGuard. Retries once
   * after 4s if a clustering job was just queued for a not-yet-seen
   * viewport.
   */
  async function loadEvents() {
    clearTimeout(eventsRetryTimer);
    eventsRetryTimer = null;
    const myToken = eventsGuard.next();
    try {
      const params = new URLSearchParams({ bbox: bboxParam(), limit: "30", autofetch: "true" });
      const res = await fetch(`/api/events?${params}`);
      if (!eventsGuard.isCurrent(myToken)) return; // a newer call has since superseded this one
      if (!res.ok) {
        $("#event-list").innerHTML = `<p class="hint">Couldn't load events (HTTP ${res.status}).</p>`;
        return;
      }
      const { events: found, meta } = await res.json();
      state.events = found;
      renderEventList(meta);
      renderEventMarkers();

      // A clustering job was just queued for this view (first time seeing
      // it) - check back once it's had a chance to finish, same retry
      // pattern as Coverage/Industrial sources.
      if (meta.job_id && !eventsRetryTimer) {
        eventsRetryTimer = setTimeout(loadEvents, 4000);
      }
    } catch (err) {
      if (!eventsGuard.isCurrent(myToken)) return;
      console.error(err);
      $("#event-list").innerHTML = `<p class="hint">Couldn't load events (network error).</p>`;
    }
  }

  /**
   * Draws each event's clustering bbox as a dashed rectangle on the map.
   * `ev.params.wide_span` (server-computed - events.py flags a cluster
   * whose bbox exceeds the plausible single-fire span) is surfaced right
   * in the tooltip text, the same warning the list row's badge shows
   * (renderEventList below) and the persistent banner shows once the
   * event is opened (analyzeEvent) - three independent surfaces for the
   * one flag, since a marker can be reached without ever passing through
   * the other two.
   */
  function renderEventMarkers() {
    if (eventMarkersLayer) map.removeLayer(eventMarkersLayer);
    const boxes = state.events.map((ev) =>
      L.rectangle([[ev.bbox_south, ev.bbox_west], [ev.bbox_north, ev.bbox_east]], {
        pane: "analysisPane",
        color: "#e34948",
        weight: 1,
        dashArray: "4 3",
        fillOpacity: 0.03,
      }).bindTooltip(
        `Event #${ev.id} · ${ev.detection_count} detections` +
          (ev.params?.wide_span ? ` · ⚠ ${ev.params.span_km} km span, likely chained sources` : "")
      )
    );
    // Clicking the rectangle does what clicking the list row does: pan to the
    // event and analyze it. Previously these rectangles carried a tooltip and
    // nothing else, so the most obvious thing an operator can do - click the
    // fire on the map - silently did nothing, and the only way in was to find
    // the matching row in the side panel.
    boxes.forEach((box, index) => {
      const ev = state.events[index];
      box.on("click", (clickEvent) => {
        // Stop the map's own click handling: without this the click also
        // reaches whatever else is listening, and on a modal tool it would
        // both open the event and drop a waypoint.
        L.DomEvent.stopPropagation(clickEvent);
        map.fitBounds(
          [[ev.bbox_south, ev.bbox_west], [ev.bbox_north, ev.bbox_east]],
          { maxZoom: 13, padding: [40, 40] }
        );
        // Same guard the list row uses: with the likelihood module unavailable
        // server-side, analyzeEvent has nothing to run, so clicking still pans
        // to the event rather than failing.
        if (state.features.likelihood !== false) analyzeEvent(ev.id);
      });
    });
    eventMarkersLayer = L.layerGroup(boxes).addTo(map);
  }

  /**
   * Renders the event list panel; each row carries a `⚠ wide span` badge
   * when the event's own `params.wide_span` flag is set (see
   * renderEventMarkers' comment above for the flag's origin and its other
   * two surfaces).
   * @param {{job_id?: string}} [meta] - the /api/events response's own meta, used only for the "still clustering" empty-state message.
   */
  function renderEventList(meta) {
    const host = $("#event-list");
    if (!state.events.length) {
      host.innerHTML = meta && meta.job_id
        ? `<p class="hint">Looking for events in this view (automatic, first time seeing it)…</p>`
        : `<p class="hint">No events in this view. Pan/zoom to an active-fire area, or click "find in view".</p>`;
      return;
    }
    host.innerHTML = "";
    const noLikelihood = state.features.likelihood === false;
    state.events.forEach((ev) => {
      const row = document.createElement("div");
      row.className = "event-row" + (ev.id === state.selectedEventId ? " selected" : "");
      const when = ageLabel(Date.now() / 1000 - ev.last_seen);
      row.title = noLikelihood
        ? "Unavailable: the 'likelihood' module didn't load - see /api/config."
        : "Click to view and analyze this event";
      const wideSpan = ev.params?.wide_span;
      row.innerHTML = `<span>Event #${ev.id} <span class="meta">· ${ev.detection_count} pts · ${when}</span></span>` +
        (wideSpan
          ? `<span class="event-wide-span-badge" title="Spans ${ev.params.span_km} km - past the plausible single-fire bound. Likely several unrelated sources chained together (e.g. widespread agricultural burning), not one fire.">⚠ wide span</span>`
          : "");
      // One click does everything: pan to it and analyze it - no separate
      // "analyze" button to discover first.
      row.addEventListener("click", () => {
        map.fitBounds(
          [[ev.bbox_south, ev.bbox_west], [ev.bbox_north, ev.bbox_east]],
          { maxZoom: 13, padding: [40, 40] }
        );
        if (!noLikelihood) analyzeEvent(ev.id);
      });
      host.appendChild(row);
    });
  }

  /** Manually triggers (and waits for) event clustering for the current viewport via the "find in view" button, then reloads the event list/markers. */
  async function detectEvents() {
    const btn = $("#btn-detect-events");
    btn.disabled = true;
    btn.textContent = "detecting…";
    try {
      const res = await fetch("/api/events/detect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bbox: bboxParam(), days: state.config.cache_days }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { job_id } = await res.json();
      await waitForJob(job_id);
      await loadEvents();
    } catch (err) {
      // Previously silent: a failed detect (network blip, job error,
      // timeout) left the button re-enabled with no other trace - looked
      // identical to "no fires found in this view" instead of "the request
      // failed." Surface it in the same panel loadEvents() itself uses for
      // its own network-error case, so the two paths read consistently.
      console.error(err);
      $("#event-list").innerHTML = `<p class="hint">Couldn't detect events: ${escapeHtml(err.message || String(err))}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "find in view";
    }
  }

  /**
   * Polls `/api/jobs/{jobId}` on a fixed interval until the background job
   * reaches a terminal state, resolving with the job record on "done" and
   * throwing on "error", a 404 (permanently gone - not retried, see the
   * comment below), too many consecutive transient failures, or the
   * overall timeout. Shared by every one-shot background-job caller in
   * this file (detectEvents, submitSpreadTopologyJob, analyzeEvent,
   * checkBurnScar, runPropagation, runEnsemble, runValidation, ...) - the
   * one retry/backoff implementation all of them lean on rather than each
   * hand-rolling its own poll loop.
   * @param {string} jobId
   * @param {{timeoutMs?: number, intervalMs?: number}} [options] - callers
   *   tune both per job kind: a cheap job keeps the defaults, a slower one
   *   (ensemble, propagation, validation) passes a longer timeout and a
   *   longer poll interval so it doesn't hammer the server for a job it
   *   knows typically takes tens of seconds.
   * @returns {Promise<object>} the job record once `status === "done"`.
   * @throws {Error} on job failure, a 404, too many transient failures, or timeout.
   */
  async function waitForJob(jobId, { timeoutMs = 30000, intervalMs = 700 } = {}) {
    const deadline = Date.now() + timeoutMs;
    // A 404 (the job row was purged, or the id is simply wrong) is a
    // permanent condition, not "not ready yet" - polling it until the
    // deadline just produces a misleading generic timeout instead of the
    // real, immediately-knowable reason. A handful of transient 5xx (server
    // briefly busy) or network-level failures (WiFi drop, LAN blip - real
    // conditions on field hardware, not just a lab assumption) are tolerated
    // before treating those as real errors too, rather than either silently
    // retrying every non-2xx all the way to the deadline, or aborting on the
    // first dropped packet.
    let consecutiveFailures = 0;
    while (Date.now() < deadline) {
      let res;
      try {
        res = await fetch(`/api/jobs/${jobId}`);
      } catch (networkError) {
        consecutiveFailures += 1;
        if (consecutiveFailures >= 5) throw networkError;
        await new Promise((resolve) => setTimeout(resolve, intervalMs));
        continue;
      }
      if (res.status === 404) {
        throw new Error(`job ${jobId} not found (it may have been purged)`);
      }
      if (res.ok) {
        consecutiveFailures = 0;
        const job = await res.json();
        if (job.status === "done") return job;
        if (job.status === "error") throw new Error(job.error || "job failed");
      } else {
        consecutiveFailures += 1;
        if (consecutiveFailures >= 5) {
          throw new Error(`server returned HTTP ${res.status} while checking job ${jobId}`);
        }
      }
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }
    throw new Error("timed out waiting for the analysis job");
  }

  /** Removes the analysis overlay image, envelope/isochrone layer, and fire callout marker, resetting calloutReferenceTs too. */
  function clearAnalysisLayers() {
    if (analysisOverlay) {
      map.removeLayer(analysisOverlay);
      analysisOverlay = null;
    }
    if (envelopeLayer) {
      map.removeLayer(envelopeLayer);
      envelopeLayer = null;
    }
    if (calloutMarker) {
      map.removeLayer(calloutMarker);
      calloutMarker = null;
    }
    calloutReferenceTs = null;
  }

  // ------------------------------------------------------- envelope styling
  //
  // Filled, concentric probability-mass bands (core = smallest/most
  // confident area drawn on top, in the most saturated fill) rather than
  // bare outlines - the "encase the affected area in a polygon" look,
  // closer to how Google's fire-boundary maps read at a glance.
  const ENVELOPE_FILL = { 0.5: "#fd4f24", 0.8: "#fd7924", 0.9: "#fda65a" };
  const ENVELOPE_FILL_OPACITY = { 0.5: 0.4, 0.8: 0.24, 0.9: 0.13 };

  /**
   * Per-feature Leaflet style for one probability-mass band. The 50% (core)
   * band gets a solid outline and slightly heavier weight - it's the
   * headline "most likely" region (see showFireCallout) - while the
   * looser 80%/90% bands get a dashed outline and progressively fainter
   * fill, reading as "less certain, just bounding" rather than a firm edge.
   * @param {object} feature - a GeoJSON feature with `properties.probability_mass` (0.5, 0.8, or 0.9).
   * @returns {object} a Leaflet path style object.
   */
  function styleProbabilityEnvelope(feature) {
    const mass = feature.properties.probability_mass;
    return {
      color: "#fff2e2",
      weight: mass <= 0.5 ? 1.5 : 1,
      opacity: 0.6,
      dashArray: mass <= 0.5 ? null : "4 3",
      fillColor: ENVELOPE_FILL[mass] || "#fd7924",
      fillOpacity: ENVELOPE_FILL_OPACITY[mass] ?? 0.18,
    };
  }

  /**
   * Builds the probability-envelope GeoJSON layer (concentric confidence
   * bands around a fire's likely/estimated extent).
   * @param {object} geo - envelope FeatureCollection (from analyze_event/run_ensemble's `envelopes` file).
   * @returns {L.GeoJSON} an un-added layer (callers `.addTo(map)` it themselves, storing the handle in envelopeLayer).
   */
  function drawProbabilityEnvelopes(geo) {
    // Largest/least-confident mass first so smaller, more confident bands
    // layer visibly on top of it, producing the concentric-ring look.
    // That's the z-ordering trick the whole function turns on: Leaflet/SVG
    // draws features in array order, later ones on top, so pre-sorting by
    // descending mass here is what actually produces the "confident core
    // punched through a looser halo" read - styleProbabilityEnvelope alone
    // (color/opacity per band) wouldn't get there without this ordering.
    const sorted = {
      ...geo,
      features: [...geo.features].sort((a, b) => b.properties.probability_mass - a.properties.probability_mass),
    };
    return L.geoJSON(sorted, { pane: "analysisPane", style: styleProbabilityEnvelope });
  }

  // Fallback-only now (see drawIsochrones): styles the bare LineString
  // contours from an isochrone job result cached before fill polygons
  // existed. Soonest arrival brightest/thickest, furthest out
  // faintest/thinnest - a glance should read "spreads this way, this
  // fast" without hovering each line.
  // Affected-area fill: a solid outline over a light translucent interior,
  // the convention public-safety crisis maps use for "this area is
  // affected". Deliberately outside the --time-* ramp - this rendering
  // says nothing about *when*, only *where*, so borrowing a time colour
  // would imply a progression reading that is not being shown.
  const SPREAD_EXTENT_OUTLINE = "#b3261e";
  const SPREAD_EXTENT_FILL = "#f2b8b5";

  const ISOCHRONE_RAMP = ["#fd4f24", "#fd7924", "#fda65a", "#fdc98c", "#fde4c4"];

  /**
   * Builds the isochrone GeoJSON layer (modelled spread-arrival contours,
   * one ring per elapsed-hours cutoff), plus a permanent "+Nh" label per
   * ring. Isochrone regions nest cumulatively the same way spread_topology's
   * detection bands do (hour 3's reached-area is a superset of hour 1's -
   * see terrain.py's isochrone_contours), so this uses the identical
   * technique renderSpreadTopologyLayer does for visual consistency between
   * the two "spread over time" surfaces: fill by `kind: "fill"` polygons,
   * colored by the same reversed --time-* ramp via timeSpreadColor()
   * (soonest arrival = recessive "not yet reached" tone, furthest out =
   * most salient), drawn latest/largest-first so each sooner, smaller fill
   * cleanly overwrites the larger one it's nested inside instead of
   * blending with it. Falls back to the original bare-line ISOCHRONE_RAMP
   * styling when `geo` predates fill polygons (no `kind: "fill"` features
   * at all), so old cached results still render sensibly.
   * @param {object} geo - isochrone FeatureCollection (from run_propagation's `isochrones` file).
   * @returns {L.FeatureGroup} an un-added layer (callers `.addTo(map)` it themselves, storing the handle in envelopeLayer).
   */
  function drawIsochrones(geo) {
    const fills = geo.features.filter((f) => f.properties.kind === "fill");
    const group = L.featureGroup();
    const label = (feature, layer) =>
      layer.bindTooltip(`+${feature.properties.hours}h`, {
        permanent: true,
        direction: "center",
        className: "isochrone-label",
      });

    if (fills.length) {
      const sortedHours = [...new Set(fills.map((f) => f.properties.hours))].sort((a, b) => a - b);
      const ramp = timeRamp();
      const ring = state.basemapTone === "dark" ? "#fcfcfb" : "#0d0d0d";
      const sorted = {
        type: "FeatureCollection",
        features: [...fills].sort((a, b) => b.properties.hours - a.properties.hours),
      };
      L.geoJSON(sorted, {
        pane: "analysisPane",
        style: (feature) => {
          const idx = sortedHours.indexOf(feature.properties.hours);
          const fraction = sortedHours.length > 1 ? idx / (sortedHours.length - 1) : 0;
          return { fillColor: timeSpreadColor(fraction, ramp), color: ring, weight: 1, opacity: 0.5, fillOpacity: 0.94 };
        },
        onEachFeature: label,
      }).addTo(group);
    } else {
      const sortedHours = [...new Set(geo.features.map((f) => f.properties.hours))].sort((a, b) => a - b);
      const style = (feature) => {
        const idx = sortedHours.indexOf(feature.properties.hours);
        return {
          color: ISOCHRONE_RAMP[Math.min(idx, ISOCHRONE_RAMP.length - 1)],
          weight: Math.max(1, 2.6 - idx * 0.4),
          opacity: Math.max(0.35, 0.9 - idx * 0.14),
        };
      };
      L.geoJSON(geo, { pane: "analysisPane", style, onEachFeature: label }).addTo(group);
    }
    return group;
  }

  /** @param {number|null} km2 @returns {string|null} "N km² (M mi²)" with precision scaled to magnitude, or null when km2 is unknown. */
  function fmtArea(km2) {
    if (km2 == null) return null;
    const mi2 = km2 * 0.386102;
    const fmt = (v) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2));
    return `${fmt(km2)} km² (${fmt(mi2)} mi²)`;
  }

  /** @param {number} ts - epoch seconds. @returns {string} coarse "Xm/Xh/Xd ago" relative-time label, used by the fire callout's live-updating freshness text. */
  function relativeTime(ts) {
    const mins = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.round(hours / 24)}d ago`;
  }

  /**
   * Places the "🔥 Estimated fire extent"-style divIcon callout at the
   * centroid of the 50% (core, highest-confidence) probability band.
   * @param {object} geo - the envelope FeatureCollection this callout summarises.
   * @param {string} headline - e.g. "Estimated fire extent" or "Modelled impact area".
   * @param {number|null} referenceTs - epoch seconds the result is "as of"; feeds the live-updating "Updated Xm ago" text (see calloutReferenceTs/refreshFireCalloutFreshness).
   */
  function showFireCallout(geo, headline, referenceTs) {
    // The 50% (core) band is the headline area, matching the doc's
    // Observed/Estimated framing - it's the smallest region that still
    // holds half the model's own probability mass, not an arbitrary shape.
    const core = geo.features.find((f) => f.properties.probability_mass <= 0.5) || geo.features[0];
    if (!core) return;
    const layer = L.geoJSON(core);
    const center = layer.getBounds().getCenter();
    const areaText = fmtArea(core.properties.area_km2);

    const icon = L.divIcon({
      className: "fire-callout",
      html:
        `<div class="fire-callout-icon">🔥</div>` +
        `<div class="fire-callout-text">` +
        `<strong>${headline}</strong>` +
        (areaText ? `<span>Estimated area · ${areaText}</span>` : "") +
        (referenceTs ? `<span class="muted">Updated ${relativeTime(referenceTs)}</span>` : "") +
        `</div>`,
      iconSize: null,
      iconAnchor: [11, 11],
    });
    calloutMarker = L.marker(center, { icon, pane: "analysisPane", interactive: false }).addTo(map);
    calloutReferenceTs = referenceTs || null;
  }

  // Re-render the callout's own "Updated Xm ago" text against the wall
  // clock instead of letting it freeze at whatever it said when the layer
  // was drawn - see calloutReferenceTs above.
  /** Ticked periodically (setInterval in init()) to re-render just the callout's "Updated Xm ago" text node against the current wall clock; a no-op when no callout is showing. */
  function refreshFireCalloutFreshness() {
    if (!calloutMarker || !calloutReferenceTs) return;
    const el = calloutMarker.getElement();
    const span = el && el.querySelector(".fire-callout-text .muted");
    if (span) span.textContent = `Updated ${relativeTime(calloutReferenceTs)}`;
  }

  /**
   * Selects an event and kicks off every analysis it supports as
   * independent background jobs (see the comment further down for why
   * they're all fired up front rather than lazily per-click). Also resets
   * the analysis panel's UI to a clean "heat" state and shows the
   * persistent wide_span warning banner when this event's own
   * `params.wide_span` flag is set (see renderEventMarkers' comment for
   * the flag's origin).
   * @param {number} eventId
   */
  async function analyzeEvent(eventId) {
    state.selectedEventId = eventId;
    // A fresh event selection invalidates any burn-scar/propagation/ensemble
    // result from whichever event was previously selected.
    state.currentBurnScar = null;
    state.currentPropagation = null;
    state.currentEnsemble = null;
    state.analysisMode = "heat";
    $$("#event-analysis .seg button").forEach((b) =>
      b.setAttribute("aria-checked", String(b.dataset.layer === "heat"))
    );
    $("#btn-layer-burn").disabled = true;
    $("#btn-layer-spread").disabled = true;
    $("#btn-layer-ensemble").disabled = true;
    // Each action button only re-enables if its backend module actually
    // loaded - applyFeatureAvailability() already set the explanatory
    // tooltip on the ones that didn't.
    $("#btn-check-burn-scar").hidden = false;
    $("#btn-check-burn-scar").disabled = state.features.imagery === false;
    $("#btn-check-burn-scar").textContent = "check burn severity (Sentinel-2)";
    $("#btn-run-propagation").hidden = false;
    $("#btn-run-propagation").disabled = state.features.terrain === false;
    $("#btn-run-propagation").textContent = "run spread model (terrain + weather)";
    $("#btn-run-ensemble").hidden = false;
    $("#btn-run-ensemble").disabled = state.features.terrain === false;
    $("#btn-run-ensemble").textContent = "run Monte Carlo ensemble (~40 members)";
    $("#btn-validate-event").hidden = false;
    $("#btn-validate-event").disabled = state.features.validation === false;
    $("#btn-validate-event").textContent = "validate model (rolling backtest)";
    $("#validation-results").hidden = true;
    $("#validation-results").innerHTML = "";
    $("#btn-view-3d").hidden = false;

    renderEventList();
    $("#event-analysis").hidden = false;
    $("#h-analysis-event").textContent = `Event #${eventId}`;
    $("#analysis-status").textContent = "Analyzing…";
    // Same wide_span flag the list badge already showed - repeated here as
    // a persistent banner since an operator can open an event straight from
    // a map click (renderEventMarkers()'s bbox rectangles) without ever
    // seeing the list row it came from.
    const selected = state.events.find((ev) => ev.id === eventId);
    $("#event-wide-span-warning").hidden = !selected?.params?.wide_span;

    // Every analysis this event supports gets queued now, not on a later
    // click - each is an independent background job (idle OS priority, see
    // jobs.py), so queuing all of them up front costs nothing but wait time,
    // and means results are simply *ready* by the time someone reaches for
    // that layer instead of making them wait through a fresh 10-40s job
    // right when they ask for it. Fire-and-forget (not awaited here): each
    // function manages its own button/state and drops its own result if the
    // user has since selected a different event (see each function's own
    // `state.selectedEventId !== eventId` guard).
    if (state.features.imagery !== false) checkBurnScar(eventId, { autoTriggered: true });
    if (state.features.terrain !== false) {
      runPropagation(eventId, { autoTriggered: true });
      runEnsemble(eventId, { autoTriggered: true });
    }
    if (state.features.validation !== false) runValidation(eventId, { autoTriggered: true });

    try {
      const res = await fetch(`/api/events/${eventId}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { job_id } = await res.json();
      const job = await waitForJob(job_id);
      if (state.selectedEventId !== eventId) return; // user moved on - drop this stale result
      state.currentAnalysis = { jobId: job_id, result: job.result };
      renderAnalysisLayers();
    } catch (err) {
      if (state.selectedEventId !== eventId) return;
      $("#analysis-status").textContent = `Analysis failed: ${err.message}`;
    }
  }

  /**
   * Submits and waits for a burn-scar job (Sentinel-2/Landsat pre/post
   * comparison) for `eventId`. Shared shape with runPropagation/
   * runEnsemble/runValidation below: submit -> waitForJob -> guard against
   * `state.selectedEventId !== eventId` (the user may have selected a
   * different event while this was in flight) -> store the result and
   * enable its layer button.
   * @param {number} eventId
   * @param {{autoTriggered?: boolean}} [options] - true when queued
   *   automatically from analyzeEvent() rather than a direct button click;
   *   suppresses the error status text and skips auto-switching the
   *   visible layer, since an unrequested background job shouldn't yank
   *   the view away from whatever the operator is already looking at.
   */
  async function checkBurnScar(eventId, { autoTriggered = false } = {}) {
    const btn = $("#btn-check-burn-scar");
    btn.disabled = true;
    btn.textContent = "checking Sentinel-2/Landsat imagery (can take ~10-30s)…";
    try {
      const res = await fetch(`/api/events/${eventId}/burn-scar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { job_id } = await res.json();
      const job = await waitForJob(job_id, { timeoutMs: 60000, intervalMs: 1500 });
      if (state.selectedEventId !== eventId) return; // user moved on - drop this stale result

      if (!job.result.ok) {
        if (!autoTriggered) $("#analysis-status").textContent = `Burn severity unavailable: ${job.result.reason}`;
        btn.disabled = false;
        btn.textContent = "check burn severity (Sentinel-2)";
        return;
      }

      state.currentBurnScar = { jobId: job_id, result: job.result };
      $("#btn-layer-burn").disabled = false;
      btn.hidden = true;

      // Manual click: jump straight to showing it, that's the whole point of
      // clicking "check". Auto-triggered on event selection: finish quietly
      // in the background instead of yanking the view away from whatever
      // layer (usually the heat/likelihood one) the user is already looking at.
      if (!autoTriggered) {
        $$("#event-analysis .seg button").forEach((b) =>
          b.setAttribute("aria-checked", String(b.dataset.layer === "burn"))
        );
        state.analysisMode = "burn";
        renderAnalysisLayers();
      }
    } catch (err) {
      if (state.selectedEventId !== eventId) return;
      if (!autoTriggered) $("#analysis-status").textContent = `Burn severity check failed: ${err.message}`;
      btn.disabled = false;
      btn.textContent = "check burn severity (Sentinel-2)";
    }
  }

  /** Submits and waits for a spread-propagation (terrain + weather) job for `eventId`. Same submit/wait/stale-selection-guard shape as checkBurnScar above. @param {number} eventId @param {{autoTriggered?: boolean}} [options] */
  async function runPropagation(eventId, { autoTriggered = false } = {}) {
    const btn = $("#btn-run-propagation");
    btn.disabled = true;
    btn.textContent = "modelling spread (terrain + weather, can take ~10-20s)…";
    try {
      const res = await fetch(`/api/events/${eventId}/propagate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { job_id } = await res.json();
      const job = await waitForJob(job_id, { timeoutMs: 60000, intervalMs: 1500 });
      if (state.selectedEventId !== eventId) return; // user moved on - drop this stale result

      state.currentPropagation = { jobId: job_id, result: job.result };
      $("#btn-layer-spread").disabled = false;
      btn.hidden = true;

      if (!autoTriggered) {
        $$("#event-analysis .seg button").forEach((b) =>
          b.setAttribute("aria-checked", String(b.dataset.layer === "spread"))
        );
        state.analysisMode = "spread";
        renderAnalysisLayers();
      }
    } catch (err) {
      if (state.selectedEventId !== eventId) return;
      if (!autoTriggered) $("#analysis-status").textContent = `Spread model failed: ${err.message}`;
      btn.disabled = false;
      btn.textContent = "run spread model (terrain + weather)";
    }
  }

  /** Submits and waits for a Monte Carlo ensemble (~40 members) job for `eventId`. Same submit/wait/stale-selection-guard shape as checkBurnScar above. @param {number} eventId @param {{autoTriggered?: boolean}} [options] */
  async function runEnsemble(eventId, { autoTriggered = false } = {}) {
    const btn = $("#btn-run-ensemble");
    btn.disabled = true;
    btn.textContent = "running ensemble (many spread simulations, can take ~20-40s)…";
    try {
      const res = await fetch(`/api/events/${eventId}/ensemble`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ n_members: 40 }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const { job_id } = await res.json();
      const job = await waitForJob(job_id, { timeoutMs: 90000, intervalMs: 2000 });
      if (state.selectedEventId !== eventId) return; // user moved on - drop this stale result

      state.currentEnsemble = { jobId: job_id, result: job.result };
      $("#btn-layer-ensemble").disabled = false;
      btn.hidden = true;

      if (!autoTriggered) {
        $$("#event-analysis .seg button").forEach((b) =>
          b.setAttribute("aria-checked", String(b.dataset.layer === "ensemble"))
        );
        state.analysisMode = "ensemble";
        renderAnalysisLayers();
      }
    } catch (err) {
      if (state.selectedEventId !== eventId) return;
      if (!autoTriggered) $("#analysis-status").textContent = `Ensemble failed: ${err.message}`;
      btn.disabled = false;
      btn.textContent = "run Monte Carlo ensemble (~40 members)";
    }
  }

  /** Submits and waits for a rolling-holdout validation backtest for `eventId`. Same submit/wait/stale-selection-guard shape as checkBurnScar above; a failure here (e.g. too few detections) is expected and quiet when auto-triggered. @param {number} eventId @param {{autoTriggered?: boolean}} [options] */
  async function runValidation(eventId, { autoTriggered = false } = {}) {
    const btn = $("#btn-validate-event");
    const out = $("#validation-results");
    btn.disabled = true;
    btn.textContent = "backtesting against held-out detections…";
    if (!autoTriggered) {
      out.hidden = true;
      out.innerHTML = "";
    }
    try {
      const res = await fetch(`/api/events/${eventId}/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const { job_id } = await res.json();
      const job = await waitForJob(job_id, { timeoutMs: 90000, intervalMs: 2000 });
      if (state.selectedEventId !== eventId) return; // user moved on - drop this stale result
      renderValidationResults(job.result);
    } catch (err) {
      if (state.selectedEventId !== eventId) return;
      // A validation backtest needs at least 3 detections at different times
      // (rolling_holdout_splits' own minimum) - routine for a brand-new
      // event with only 1-2 detections so far, not worth surfacing as a
      // scary error when nobody asked for this run explicitly.
      if (!autoTriggered) {
        out.hidden = false;
        out.textContent = `Validation failed: ${err.message}`;
      }
    } finally {
      btn.disabled = false;
      btn.textContent = "validate model (rolling backtest)";
    }
  }

  /** Renders the validation backtest's per-model metrics table (kernel_density vs. classical baselines) into #validation-results. @param {object} result - the validate job's result payload. */
  function renderValidationResults(result) {
    const out = $("#validation-results");
    const metricCols = [
      ["precision", "Prec."], ["recall", "Recall"], ["f1", "F1"],
      ["jaccard_point_proxy", "Jaccard*"], ["brier_score", "Brier"],
      ["centroid_displacement_km", "Centroid Δ km"],
    ];
    const fmt = (v) => (v === null || v === undefined ? "-" : Number(v).toFixed(3));
    const rows = Object.entries(result.summary)
      .map(([name, m]) => `<tr><th scope="row">${name}</th>${metricCols.map(([k]) => `<td>${fmt(m[k])}</td>`).join("")}</tr>`)
      .join("");
    out.innerHTML = `
      <p class="hint">
        Rolling-holdout backtest · ${result.split_count} split(s) · scored against real held-out
        detections${result.clear_pass_negatives_used ? ` and ${result.clear_pass_negatives_used} confirmed clear satellite pass(es)` : ""}.
        <strong>kernel_density</strong> is the model this app actually serves; the rest are classical,
        non-learned baselines it should beat (further_plan.md §11). Precision/Recall/F1/Jaccard* are
        pooled across splits (summed TP/FP/FN, then one ratio) so F1 always equals the harmonic mean
        of the Precision/Recall shown alongside it. Jaccard* is a point-based stand-in for area IoU,
        not a true-perimeter score - this project has no curated reference perimeters to compute that
        against.
      </p>
      <table class="validation-table">
        <thead><tr><th scope="col">model</th>${metricCols.map(([, label]) => `<th scope="col">${label}</th>`).join("")}</tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    out.hidden = false;
  }

  // Bumped on every call so a slow envelope/isochrone fetch started by an
  // *earlier* call (a different analysis mode, or the same mode clicked
  // again) can tell it's been superseded once it finally resolves, and
  // skip adding its layer instead of stacking it on top of whatever
  // rendered in the meantime. state.currentAnalysis/currentEnsemble/
  // currentPropagation !== ... below only caught a *different event's*
  // stale result landing late - switching analysis mode within the *same*
  // event (heat -> arrival -> spread faster than one round-trip) still let
  // an old envelope layer leak onto the map alongside the new one - the
  // exact "overlapping translucent layers corrupt the age meaning" failure
  // further_plan.md warns against, reproduced here as a real race, not a
  // rendering choice.
  let analysisRenderGeneration = 0;

  /**
   * Publishes the currently-rendered spread/ensemble analysis (or `null`
   * when cleared) through context.js, so other modules/apps sharing this
   * map (e.g. structures.js's exposure layer) can react to it without this
   * file importing them back.
   * @param {object|null} detail
   */
  function publishSpreadAnalysis(detail) {
    setSpreadAnalysis(detail);
  }

  /**
   * Draws whichever analysis layer matches state.analysisMode (heat/
   * arrival/burn/spread/ensemble) from the already-fetched
   * state.currentAnalysis/currentBurnScar/currentPropagation/
   * currentEnsemble, plus (when the envelope toggle is on) fetches and
   * draws the matching envelope/isochrone contour and fire callout.
   * Guards every async envelope fetch with `stillCurrent()` against
   * analysisRenderGeneration - see the comment above for the same-event,
   * different-mode race this closes that the selectedEventId checks
   * elsewhere in this section don't cover.
   */
  function renderAnalysisLayers() {
    clearAnalysisLayers();
    publishSpreadAnalysis(null);
    const myGeneration = ++analysisRenderGeneration;
    const stillCurrent = () => analysisRenderGeneration === myGeneration;

    if (state.analysisMode === "ensemble") {
      const ens = state.currentEnsemble;
      if (!ens) return;
      const { jobId, result } = ens;
      analysisOverlay = L.imageOverlay(`/api/jobs/${jobId}/files/${result.files.probability}`, result.bounds, {
        pane: "analysisPane",
        opacity: 0.9,
        interactive: false,
      }).addTo(map);
      publishSpreadAnalysis({ map, jobId, result, mode: "ensemble" });

      if ($("#envelope-toggle").checked) {
        fetch(`/api/jobs/${jobId}/files/${result.files.envelopes}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((geo) => {
            if (!stillCurrent() || !geo || !geo.features.length || state.currentEnsemble !== ens) return;
            envelopeLayer = drawProbabilityEnvelopes(geo).addTo(map);
            showFireCallout(geo, "Modelled impact area", result.reference_ts);
          })
          .catch((err) => console.error(err));
      }

      $("#analysis-status").textContent =
        `Ensemble probability (${result.n_members} members, ESS ${result.effective_sample_size.toFixed(1)}) · ` +
        `seeded from ${result.seed_detections} early detection(s), scored against ${result.assimilated_observations} later ` +
        `observation(s) · Modelled physics ensemble, not an observation`;
      return;
    }

    if (state.analysisMode === "spread") {
      const prop = state.currentPropagation;
      if (!prop) return;
      const { jobId, result } = prop;
      analysisOverlay = L.imageOverlay(`/api/jobs/${jobId}/files/${result.files.spread_time}`, result.bounds, {
        pane: "analysisPane",
        opacity: 0.9,
        interactive: false,
      }).addTo(map);
      publishSpreadAnalysis({ map, jobId, result, mode: "spread" });

      if ($("#envelope-toggle").checked) {
        fetch(`/api/jobs/${jobId}/files/${result.files.isochrones}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((geo) => {
            if (!stillCurrent() || !geo || !geo.features.length || state.currentPropagation !== prop) return;
            envelopeLayer = drawIsochrones(geo).addTo(map);
            const soonest = Math.min(...geo.features.map((f) => f.properties.hours));
            const w = result.weather;
            const center = L.latLngBounds(result.bounds).getCenter();
            const icon = L.divIcon({
              className: "fire-callout",
              html:
                `<div class="fire-callout-icon">🔥</div>` +
                `<div class="fire-callout-text">` +
                `<strong>Modelled spread</strong>` +
                `<span>First front (isochrone) ~+${soonest}h · wind ${w.wind_speed_ms.toFixed(1)} m/s</span>` +
                `<span class="muted">Modelled physics estimate, not an observation</span>` +
                `</div>`,
              iconSize: null,
              iconAnchor: [11, 11],
            });
            calloutMarker = L.marker(center, { icon, pane: "analysisPane", interactive: false }).addTo(map);
          })
          .catch((err) => console.error(err));
      }

      const w = result.weather;
      $("#analysis-status").textContent =
        `Modelled spread (Rothermel-inspired + fast marching) · wind ${w.wind_speed_ms.toFixed(1)} m/s · ` +
        `${result.grid.nx}×${result.grid.ny} cells @ ${result.grid.res_m.toFixed(0)}m · ` +
        `isochrones shaded soonest (bright) → furthest out (faint) · ` +
        `wind/slope affect speed, not direction (isotropic solve) · Modelled, not an observation`;
      return;
    }

    if (state.analysisMode === "burn") {
      const scar = state.currentBurnScar;
      if (!scar) return;
      const { jobId, result } = scar;
      analysisOverlay = L.imageOverlay(`/api/jobs/${jobId}/files/${result.files.severity}`, result.bounds, {
        pane: "analysisPane",
        opacity: 0.95,
        interactive: false,
      }).addTo(map);

      const pre = result.pre_scene.date.slice(0, 10);
      const post = result.post_scene.date.slice(0, 10);
      $("#analysis-status").textContent =
        `Burn severity (${result.pre_scene.collection === "sentinel-2-l2a" ? "Sentinel-2" : "Landsat"}) · ` +
        `pre ${pre} → post ${post} · ~${result.estimated_burned_hectares.toLocaleString()} ha estimated burned · ` +
        `Observed optical evidence, not a model`;
      return;
    }

    const analysis = state.currentAnalysis;
    if (!analysis) return;
    const { jobId, result } = analysis;

    const fname = state.analysisMode === "arrival" ? result.files.arrival_time : result.files.active_heat;
    analysisOverlay = L.imageOverlay(`/api/jobs/${jobId}/files/${fname}`, result.bounds, {
      pane: "analysisPane",
      opacity: 0.9,
      interactive: false,
    }).addTo(map);

    if ($("#envelope-toggle").checked) {
      fetch(`/api/jobs/${jobId}/files/${result.files.envelopes}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((geo) => {
          if (!stillCurrent() || !geo || !geo.features.length || state.currentAnalysis !== analysis) return;
          envelopeLayer = drawProbabilityEnvelopes(geo).addTo(map);
          showFireCallout(geo, "Estimated fire extent", result.reference_ts);
        })
        .catch((err) => console.error(err));
    }

    const label = state.analysisMode === "arrival" ? "Arrival-time estimate" : "Active-heat likelihood";
    const clearNote = result.clear_pass_count
      ? ` · informed by ${result.clear_pass_count} clear satellite pass(es) (no-fire evidence)`
      : "";
    $("#analysis-status").textContent =
      `${label} · ${result.detection_count} detections · peak ${(result.max_probability * 100).toFixed(
        0
      )}% · ${result.grid.nx}×${result.grid.ny} cells @ ${result.grid.res_m.toFixed(0)}m · modelled estimate, not an observation${clearNote}`;
  }

  // ------------------------------------------------------------- 3D terrain
  //
  // A second map engine (Leaflet has no 3D/terrain support) shown as a
  // full-panel overlay rather than side-by-side with the 2D map, so the two
  // WebGL/canvas contexts never fight over the same space. Reuses whichever
  // analysis image is already on screen in 2D - same file, same bounds -
  // draped over real elevation instead of a flat plane.

  let map3d = null;

  /** @returns {{url: string, bounds: number[][], label: string}|null} the image URL/bounds/label for whichever analysis result matches state.analysisMode, or null if that mode has no result yet - the single source both the 2D image overlay and the 3D drape read from. */
  function currentOverlayInfo() {
    const mode = state.analysisMode;
    if (mode === "ensemble" && state.currentEnsemble) {
      const { jobId, result } = state.currentEnsemble;
      return { url: `/api/jobs/${jobId}/files/${result.files.probability}`, bounds: result.bounds, label: "Modelled ensemble probability" };
    }
    if (mode === "spread" && state.currentPropagation) {
      const { jobId, result } = state.currentPropagation;
      return { url: `/api/jobs/${jobId}/files/${result.files.spread_time}`, bounds: result.bounds, label: "Modelled spread arrival time" };
    }
    if (mode === "burn" && state.currentBurnScar) {
      const { jobId, result } = state.currentBurnScar;
      return { url: `/api/jobs/${jobId}/files/${result.files.severity}`, bounds: result.bounds, label: "Observed burn severity" };
    }
    if (state.currentAnalysis) {
      const { jobId, result } = state.currentAnalysis;
      const fname = mode === "arrival" ? result.files.arrival_time : result.files.active_heat;
      return { url: `/api/jobs/${jobId}/files/${fname}`, bounds: result.bounds, label: mode === "arrival" ? "Arrival-time estimate" : "Active-heat likelihood" };
    }
    return null;
  }

  /**
   * Lazily creates (once) and returns the singleton MapLibre GL 3D map,
   * built from a terrain-DEM raster-dem source (terrarium encoding, ~1.8x
   * exaggerated) plus an OpenTopoMap raster base - the bridge between the
   * 2D Leaflet engine used everywhere else in this file and MapLibre GL,
   * the only one of the two with real 3D/terrain rendering. Subsequent
   * calls are free no-ops that just return the existing instance, so
   * callers (open3DView) can call this unconditionally on every open.
   * @param {string} demUrl - server-relative URL to the terrain-DEM tile source (config.terrain_dem.url).
   * @param {string} topoUrl - server-relative URL to the topo basemap tile source.
   * @param {string} topoAttribution
   * @returns {maplibregl.Map}
   */
  function ensureMap3D(demUrl, topoUrl, topoAttribution) {
    if (map3d) return map3d;
    map3d = new maplibregl.Map({
      container: "map3d",
      style: {
        version: 8,
        sources: {
          "nfm-opentopo": { type: "raster", tiles: [location.origin + topoUrl], tileSize: 256, attribution: topoAttribution },
          "nfm-dem": { type: "raster-dem", tiles: [location.origin + demUrl], tileSize: 256, encoding: "terrarium", maxzoom: 15 },
        },
        layers: [{ id: "nfm-opentopo-layer", type: "raster", source: "nfm-opentopo" }],
        terrain: { source: "nfm-dem", exaggeration: 1.8 },
        sky: {},
      },
      attributionControl: { compact: true },
    });
    map3d.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    return map3d;
  }

  /**
   * Opens the 3D overlay panel and drapes the current 2D analysis image
   * (currentOverlayInfo()) over MapLibre terrain: creates the map on first
   * use (ensureMap3D), then either updates the existing "nfm-fire" image
   * source in place or adds it for the first time, since MapLibre sources
   * can't be swapped wholesale the way a Leaflet imageOverlay is replaced.
   * A no-op if there's nothing to show yet (no analysis result for the
   * current mode).
   */
  function open3DView() {
    const overlay = currentOverlayInfo();
    if (!overlay) return;
    const config = state.config;

    $("#map3d-wrap").hidden = false;
    const m = ensureMap3D(config.terrain_dem.url, config.basemaps.find((b) => b.id === "opentopomap")?.url || config.basemaps[0].url, config.basemaps.find((b) => b.id === "opentopomap")?.attribution || "");

    const [[south, west], [north, east]] = overlay.bounds;
    const coords = [
      [west, north],
      [east, north],
      [east, south],
      [west, south],
    ];

    const attach = () => {
      if (m.getSource("nfm-fire")) {
        m.getSource("nfm-fire").updateImage({ url: overlay.url, coordinates: coords });
      } else {
        m.addSource("nfm-fire", { type: "image", url: overlay.url, coordinates: coords });
        m.addLayer({ id: "nfm-fire-layer", type: "raster", source: "nfm-fire", paint: { "raster-opacity": 0.85 } });
      }
      m.fitBounds([[west, south], [east, north]], { padding: 40, duration: 0 });
      m.setPitch(55);
    };
    if (m.isStyleLoaded()) attach();
    else m.once("load", attach);

    $("#map3d-hint").textContent =
      `${overlay.label} draped over OpenTopoMap terrain (vertical relief exaggerated ~1.8x for legibility) · ` +
      `drag to orbit, scroll to zoom, right-drag/ctrl-drag to tilt.`;
  }

  /** Hides the 3D overlay panel (the MapLibre instance itself is left alive - see ensureMap3D - so reopening is instant). */
  function close3DView() {
    $("#map3d-wrap").hidden = true;
  }

  /** Tears down the whole event-analysis panel: clears map layers, the 3D view, and every state.current* result, then deselects the event. */
  function closeAnalysis() {
    clearAnalysisLayers();
    close3DView();
    state.currentAnalysis = null;
    state.currentBurnScar = null;
    state.currentPropagation = null;
    state.currentEnsemble = null;
    state.selectedEventId = null;
    $("#event-analysis").hidden = true;
    renderEventList();
  }

  // ---------------------------------------------------------- server status

  /** Shows/hides the "Downloading N chunks" topbar pill from the /api/status fetcher block. @param {{pending: number}} fetcher */
  function setFetchIndicator(fetcher) {
    const pill = $("#fetch-indicator");
    const busy = fetcher.pending > 0;
    pill.hidden = !busy;
    if (busy) {
      $("#fetch-text").textContent = `Downloading ${fetcher.pending} chunk${
        fetcher.pending === 1 ? "" : "s"
      }`;
    }
  }

  /**
   * Fetches `/api/status` and refreshes the fetch indicator, cache stat
   * panel, and API-budget readout. When the background chunk fetcher just
   * finished (`pending` dropped to 0), stops the poll loop and - unless
   * told not to - reloads detections so the newly-completed chunks appear
   * without the operator having to pan/zoom to trigger a refresh.
   * @param {boolean} [reloadWhenIdle=true] - false for the periodic
   *   "just check in" callers (init()'s own setInterval) that don't want
   *   an unsolicited reload competing with whatever the operator's doing;
   *   true for the polling loop actually watching a fetch to completion
   *   (startPolling), where reloading on idle is the whole point.
   */
  async function pollStatus(reloadWhenIdle = true) {
    try {
      const res = await fetch("/api/status");
      if (!res.ok) return;
      const status = await res.json();

      setFetchIndicator(status.fetcher);
      $("#cache-pill").textContent = `cache ${compact(status.cache.detections)}`;

      const kv = $("#cache-kv");
      const key = status.map_key;
      kv.innerHTML = `
        <dt>Rows cached</dt><dd>${nf.format(status.cache.detections)}</dd>
        <dt>Oldest day</dt><dd>${status.cache.first_day || "-"}</dd>
        <dt>Database</dt><dd>${(status.cache.db_size_bytes / 1048576).toFixed(1)} MB</dd>
        <dt>Chunks done</dt><dd>${nf.format(status.fetcher.completed)}</dd>
        <dt>Map tiles cached</dt><dd>${nf.format(status.tiles.tiles)} (${(
        status.tiles.bytes / 1048576
      ).toFixed(0)} MB)</dd>
        ${
          key && key.ok && key.transaction_limit
            ? `<dt>API budget</dt><dd>${key.current_transactions}/${key.transaction_limit}</dd>`
            : ""
        }`;

      const note = $("#cache-note");
      if (status.fetcher.last_error) {
        note.textContent = status.fetcher.last_error;
        note.style.color = "var(--warning)";
      } else {
        note.textContent = "";
      }

      if (status.fetcher.pending === 0) {
        stopPolling();
        if (reloadWhenIdle) loadDetections();
      }
    } catch (err) {
      console.error(err);
    }
  }

  /**
   * Starts the 3s background-chunk-download status poll loop (idempotent -
   * a second call while one's already running is a no-op). Triggered
   * whenever the server reports pending chunks: after loadDetections sees
   * `meta.pending > 0`, and after the manual "cache view"/"refresh" actions
   * queue new fetches.
   */
  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(() => pollStatus(true), 3000);
  }

  /** Stops the status poll loop started by startPolling() and clears state.pollTimer. Called by pollStatus itself once the fetcher goes idle. */
  function stopPolling() {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  // ------------------------------------------------------------------ init

  /**
   * Populates the source checkbox list from server config, seeds
   * state.sources/enabledSources/instruments, and wires each checkbox to
   * scheduleLoad(). Instruments are collected in first-seen order so
   * colorBy "instrument" assigns categorical colors consistently.
   * @param {object} config - the /api/config payload (sources[]).
   */
  function buildSourceList(config) {
    const host = $("#source-list");
    host.innerHTML = "";
    const instruments = [];

    config.sources.forEach((src) => {
      state.sources.set(src.id, src);
      if (!instruments.includes(src.instrument)) instruments.push(src.instrument);
      if (src.enabled) state.enabledSources.add(src.id);

      const label = document.createElement("label");
      if (!src.enabled) label.classList.add("disabled");
      label.innerHTML = `
        <input type="checkbox" value="${src.id}" ${src.enabled ? "checked" : "disabled"}>
        <span>${src.label}</span>`;
      label.title = src.enabled
        ? `${src.instrument} · ${src.resolution_m} m`
        : "Not enabled in NEXFIREMAP_SOURCES";
      host.appendChild(label);

      label.querySelector("input").addEventListener("change", (e) => {
        if (e.target.checked) state.enabledSources.add(src.id);
        else state.enabledSources.delete(src.id);
        scheduleLoad(120);
      });
    });

    state.instruments = instruments;
  }

  /**
   * Wires up essentially every control in the panel (days segment,
   * color-by/render-mode selects, playback, coverage/industrial/eumetsat
   * toggles, event-detection and per-layer analysis buttons, filters,
   * panel/print/cache buttons, ...) - the single init-time pass that
   * connects static DOM to the functions defined throughout this file.
   * Called once from init().
   */
  function wireControls() {
    $$("#days-seg button").forEach((btn) => {
      btn.addEventListener("click", () => {
        $$("#days-seg button").forEach((b) => b.setAttribute("aria-checked", "false"));
        btn.setAttribute("aria-checked", "true");
        state.days = Number(btn.dataset.days);
        state.focusDay = null;
        $("#btn-clear-day").hidden = true;
        renderHistogram();
        loadDetections();
      });
    });

    $("#color-by").addEventListener("change", (e) => {
      state.colorBy = e.target.value;
      renderLegend();
      drawDetections();
    });

    $("#render-mode").addEventListener("change", (e) => {
      state.renderMode = e.target.value;
      // Unlike colorBy, renderMode now changes which legend renders too
      // (topology has its own gradient legend, keyed on renderMode not
      // colorBy) - renderLegend() first for an instant fallback ("Earlier"/
      // "Later" until the async contour job resolves), same "show something
      // now, refine when the real data lands" pattern loadCoverage/loadEvents
      // already use elsewhere.
      renderLegend();
      drawDetections();
    });

    $("#btn-playback").addEventListener("click", () => {
      state.playback.active ? stopPlayback() : startPlayback();
    });

    $("#coverage-toggle").addEventListener("change", (e) => {
      state.coverageEnabled = e.target.checked;
      loadCoverage();
    });

    $("#industrial-toggle").addEventListener("change", (e) => {
      state.industrialEnabled = e.target.checked;
      loadIndustrialSources();
    });
    $("#btn-scan-industrial").addEventListener("click", scanIndustrial);

    $("#eumetsat-toggle").addEventListener("change", (e) => {
      state.eumetsatEnabled = e.target.checked;
      loadEumetsatFires();
    });

    $("#btn-detect-events").addEventListener("click", detectEvents);
    $("#btn-close-analysis").addEventListener("click", closeAnalysis);

    $$("#event-analysis .seg button").forEach((btn) => {
      btn.addEventListener("click", () => {
        $$("#event-analysis .seg button").forEach((b) => b.setAttribute("aria-checked", "false"));
        btn.setAttribute("aria-checked", "true");
        state.analysisMode = btn.dataset.layer;
        renderAnalysisLayers();
      });
    });

    $("#envelope-toggle").addEventListener("change", () => renderAnalysisLayers());

    $("#btn-check-burn-scar").addEventListener("click", () => {
      if (state.selectedEventId !== null) checkBurnScar(state.selectedEventId);
    });

    $("#btn-run-propagation").addEventListener("click", () => {
      if (state.selectedEventId !== null) runPropagation(state.selectedEventId);
    });

    $("#btn-run-ensemble").addEventListener("click", () => {
      if (state.selectedEventId !== null) runEnsemble(state.selectedEventId);
    });

    $("#btn-validate-event").addEventListener("click", () => {
      if (state.selectedEventId !== null) runValidation(state.selectedEventId);
    });

    $("#btn-view-3d").addEventListener("click", open3DView);
    $("#btn-close-3d").addEventListener("click", close3DView);

    $("#playback-slider").addEventListener("input", (e) => {
      stopPlayback(); // scrubbing by hand pauses any running animation
      state.playback.cursor = Number(e.target.value);
      updatePlaybackReadout();
      drawDetections();
    });

    $$(".f-conf").forEach((el) =>
      el.addEventListener("change", () => scheduleLoad(120))
    );

    const frp = $("#min-frp");
    frp.addEventListener("input", () => {
      state.minFrp = Number(frp.value);
      $("#frp-out").textContent = `${state.minFrp} MW`;
    });
    frp.addEventListener("change", () => scheduleLoad(120));

    $("#daynight").addEventListener("change", (e) => {
      state.daynight = e.target.value;
      scheduleLoad(120);
    });

    $("#btn-clear-day").addEventListener("click", () => {
      state.focusDay = null;
      $("#btn-clear-day").hidden = true;
      renderHistogram();
      loadDetections();
    });

    $("#btn-panel").addEventListener("click", () => {
      const panel = $("#panel");
      panel.hidden = !panel.hidden;
      $("#btn-panel").setAttribute("aria-expanded", String(!panel.hidden));
    });

    // Lives in the topbar (never data-app-tagged), so it's reachable from
    // every app, not just NexIncidentCommand's own print button. Delegates
    // to operations.js's richer incident-aware print when that module has
    // registered one (context.js's setPrintView, called from its init())
    // and always falls back to a bare window.print() otherwise - a click
    // here must never silently do nothing, the exact failure mode this
    // button's own generalisation was written to avoid (see
    // printOperationsMap()'s comment in operations.js). Still a runtime
    // lookup rather than a static import of printOperationsMap: operations.js
    // registers it partway through an async init() that a public-role
    // session returns early from, so "is it available yet" is a real
    // question an import couldn't answer.
    $("#btn-print-view").addEventListener("click", async () => {
      const printView = getPrintView();
      if (typeof printView === "function") { printView(); return; }
      // No incident-command print view registered (public/viewer session, or
      // operations.js not loaded): still wait for tiles before printing.
      await prepareMapForPrint();
      window.print();
    });

    // Ctrl+P and the browser's own print entry bypass the button entirely, so
    // the map still has to be re-measured for the print layout. This cannot
    // await - beforeprint is synchronous - but invalidateSize alone at least
    // gets the geometry right, and the tiles already on screen are painted.
    window.addEventListener("beforeprint", () => map.invalidateSize());

    $("#btn-cache-view").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const res = await fetch("/api/cache/ensure", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            bbox: bboxParam(),
            days: state.config.cache_days,
            sources: Array.from(state.enabledSources),
          }),
        });
        if (res.ok) startPolling();
      } finally {
        btn.disabled = false;
      }
    });

    $("#btn-refresh").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const res = await fetch("/api/cache/refresh", { method: "POST" });
        if (res.ok) startPolling();
      } finally {
        btn.disabled = false;
      }
    });

    let resizeTimer;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(renderHistogram, 200);
    });
  }

  // --------------------------------------------------------- map search
  //
  // Google-Maps-style search: place names via the server's own
  // /api/geocode proxy (server-side only, never a direct client call, see
  // geocode.py for why), plus a zero-network "lat, lon" shortcut that keeps
  // working with the WAN down.
  const searchGuard = makeStaleGuard();
  let searchMarker = null;
  let searchDebounceTimer = null;
  let searchResults = [];
  let searchActiveIndex = -1;

  /** @param {string} raw - the raw search-box text. @returns {{label: string, lat: number, lon: number, bounds: null, type: "coordinates"}|null} a synthetic local search result if `raw` parses as coordinates (any system coords.js recognises), else null - falls through to the network geocoder. */
  function parseCoordinateQuery(raw) {
    // Delegates to coords.js: decimal degrees, DMS, DDM, MGRS and UTM are
    // all self-describing enough to auto-detect; a bare "number number"
    // pair with none of those markers falls back to whatever coordinate
    // system is currently selected (see wireCoordSystemSelect() below),
    // WGS84 decimal degrees by default.
    // A bounding box is tried first: "50.7,6.3,50.9,6.5" is four numbers, and
    // parseDecimalPair would otherwise happily read the first two and silently
    // discard the rest, zooming to a corner of the box the operator pasted.
    const box = Coords.parseBbox(raw);
    if (box) {
      const bounds = [[box.south, box.west], [box.north, box.east]];
      return {
        label: `Area ${box.west.toFixed(4)}, ${box.south.toFixed(4)} → ${box.east.toFixed(4)}, ${box.north.toFixed(4)}`,
        detected: "bounding box",
        lat: (box.south + box.north) / 2, lon: (box.west + box.east) / 2,
        bounds, type: "coordinates",
      };
    }
    const point = Coords.parse(raw, Coords.currentSystem());
    if (!point) return null;
    const label = Coords.format(point.lat, point.lon, Coords.currentSystem());
    return {
      label: label || `${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}`,
      // Naming the format that was actually detected is what keeps an
      // auto-detecting parser honest: a grid reference misread as a decimal
      // pair lands somewhere plausible hundreds of km away, and without this
      // the operator has nothing to tell them why.
      detected: Coords.detectFormat(raw, Coords.currentSystem()),
      lat: point.lat, lon: point.lon, bounds: null, type: "coordinates",
    };
  }

  /** Populates the coordinate-system dropdown from coords.js's registry, restores the last-selected system (and any saved custom proj4 string), and wires changes to persist. */
  function wireCoordSystemSelect() {
    const select = $("#coord-system-select");
    const customInput = $("#coord-custom-proj4");
    select.innerHTML = Coords.SYSTEMS.map(
      (system) => `<option value="${system.id}">${escapeHtml(system.label)}</option>`
    ).join("");
    const stored = Coords.currentSystem();
    select.value = Coords.SYSTEMS.some((system) => system.id === stored) ? stored : "wgs84";
    customInput.hidden = select.value !== "custom";
    try {
      customInput.value = localStorage.getItem("nexfiremap.coords.customProj4") || "";
    } catch (_) {
      /* private mode/storage pressure - custom definition just starts blank */
    }
    select.addEventListener("change", () => {
      Coords.setSystem(select.value);
      customInput.hidden = select.value !== "custom";
    });
    customInput.addEventListener("change", () => {
      try {
        localStorage.setItem("nexfiremap.coords.customProj4", customInput.value.trim());
      } catch (_) {
        /* private mode/storage pressure - custom definition just won't persist */
      }
    });
  }

  /** Closes and clears the search results dropdown. */
  function closeSearchResults() {
    const list = $("#map-search-results");
    list.hidden = true;
    list.innerHTML = "";
    searchResults = [];
    searchActiveIndex = -1;
    $("#map-search-input").setAttribute("aria-expanded", "false");
  }

  /**
   * Renders the search results dropdown (list + optional status/error row)
   * and resets keyboard-selection state to the first item.
   * @param {object[]} items - result objects (each with at least label/lat/lon).
   * @param {string|null} statusText - an inline status/error line ("Searching…", "No matches.", ...), or null for none.
   * @param {boolean} isError - styles statusText as an error when true.
   */
  function renderSearchResults(items, statusText, isError) {
    const list = $("#map-search-results");
    searchResults = items;
    searchActiveIndex = items.length ? 0 : -1;
    const rows = items
      .map(
        (item, i) =>
          `<li role="option" id="map-search-opt-${i}" aria-selected="${i === 0}">` +
          `<button type="button" class="map-search-result" data-index="${i}">` +
          `<strong>${escapeHtml(item.label)}</strong>` +
          (item.type && item.type !== "coordinates"
            ? `<small>${escapeHtml([item.class, item.type].filter(Boolean).join(", "))}</small>`
            // For a coordinate hit, say which format was detected. An
            // auto-detecting parser that guesses wrong lands somewhere
            // plausible but distant, and this is the only clue the operator
            // gets that it read their grid reference as decimal degrees.
            : item.detected
              ? `<small>read as ${escapeHtml(item.detected)}</small>`
              : "") +
          `</button></li>`
      )
      .join("");
    const statusRow = statusText
      ? `<li class="map-search-status${isError ? " error" : ""}">${escapeHtml(statusText)}</li>`
      : "";
    list.innerHTML = statusRow + rows;
    list.hidden = !(rows || statusRow);
    $("#map-search-input").setAttribute("aria-expanded", String(!list.hidden));
  }

  /** Commits a chosen search result: fills the input, drops a pin, and pans/zooms the map to it (fitBounds if the result carries bounds, else a plain setView). @param {object|null} item */
  function selectSearchResult(item) {
    if (!item) return;
    $("#map-search-input").value = item.label;
    $("#map-search-clear").hidden = false;
    closeSearchResults();
    if (searchMarker) {
      map.removeLayer(searchMarker);
      searchMarker = null;
    }
    const icon = L.divIcon({
      className: "search-pin",
      html: `<div style="font-size:26px;line-height:1;transform:translate(-50%,-90%)">📍</div>`,
      iconSize: null,
      iconAnchor: [0, 0],
    });
    searchMarker = L.marker([item.lat, item.lon], { icon, interactive: false }).addTo(map);
    if (item.bounds && item.bounds.length === 4) {
      const [west, south, east, north] = item.bounds;
      map.fitBounds([[south, west], [north, east]], { maxZoom: 15, padding: [40, 40] });
    } else {
      map.setView([item.lat, item.lon], Math.max(map.getZoom(), 12));
    }
  }

  /**
   * Resolves a search query: a coordinate pair short-circuits locally
   * (parseCoordinateQuery), otherwise queries the server's geocode proxy,
   * guarded by searchGuard against a slower older query's response
   * landing after a newer one.
   * @param {string} query
   */
  async function runSearch(query) {
    // A raw "lat, lon" pair resolves instantly and locally, no network
    // needed, and still works with the command server's WAN link down.
    const local = parseCoordinateQuery(query);
    if (local) {
      renderSearchResults([local], null, false);
      return;
    }
    if (query.trim().length < 2) {
      closeSearchResults();
      return;
    }
    renderSearchResults([], "Searching…", false);
    const myToken = searchGuard.next();
    try {
      const res = await fetch(`/api/geocode?q=${encodeURIComponent(query)}`);
      if (!searchGuard.isCurrent(myToken)) return;
      if (!res.ok) {
        renderSearchResults([], `Search failed (HTTP ${res.status}).`, true);
        return;
      }
      const data = await res.json();
      if (!searchGuard.isCurrent(myToken)) return;
      if (data.error) {
        renderSearchResults([], data.error, true);
        return;
      }
      if (!data.results.length) {
        renderSearchResults([], "No matches.", false);
        return;
      }
      renderSearchResults(data.results, null, false);
    } catch (err) {
      if (!searchGuard.isCurrent(myToken)) return;
      renderSearchResults([], "Search is unreachable right now (network error).", true);
    }
  }

  /** Moves keyboard focus among search results by `delta` (wrapping), for ArrowUp/ArrowDown handling. @param {number} delta - +1 or -1. */
  function moveSearchSelection(delta) {
    if (!searchResults.length) return;
    searchActiveIndex = (searchActiveIndex + delta + searchResults.length) % searchResults.length;
    $$("#map-search-results [role=option]").forEach((el, i) =>
      el.setAttribute("aria-selected", String(i === searchActiveIndex))
    );
    const active = $(`#map-search-opt-${searchActiveIndex}`);
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  /** Wires the map-search input's debounced typing, keyboard navigation (Escape/Arrow/Enter), result clicks, and clear button. */
  function wireSearchControl() {
    const input = $("#map-search-input");
    const clearBtn = $("#map-search-clear");
    const list = $("#map-search-results");

    input.addEventListener("input", () => {
      clearBtn.hidden = !input.value;
      // Keeps the compact search box expanded while there is something in it,
      // so it does not shrink away mid-edit the moment focus moves to the
      // results list (see .map-search.is-active in app.css).
      $("#map-search").classList.toggle("is-active", Boolean(input.value));
      clearTimeout(searchDebounceTimer);
      const query = input.value;
      if (!query.trim()) {
        closeSearchResults();
        return;
      }
      // Debounced well past Nominatim's own 1 request/second usage-policy
      // floor (see geocode.py), so a normal typing burst never queues up
      // more than the one query the operator actually meant to run.
      searchDebounceTimer = setTimeout(() => runSearch(query), 400);
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeSearchResults();
        input.blur();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        moveSearchSelection(1);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        moveSearchSelection(-1);
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        if (searchActiveIndex >= 0) selectSearchResult(searchResults[searchActiveIndex]);
      }
    });

    list.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-index]");
      if (btn) selectSearchResult(searchResults[Number(btn.dataset.index)]);
    });

    clearBtn.addEventListener("click", () => {
      input.value = "";
      clearBtn.hidden = true;
      closeSearchResults();
      if (searchMarker) {
        map.removeLayer(searchMarker);
        searchMarker = null;
      }
      input.focus();
    });

    document.addEventListener("click", (event) => {
      if (!event.target.closest("#map-search")) closeSearchResults();
    });
  }

  // A feature whose backend module failed to import (missing optional
  // dependency) gets its button disabled with an explanatory tooltip,
  // rather than letting the user hit a 503 with no context.
  /** Disables (and adds an explanatory tooltip to) each control whose backend module failed to load, per state.features - called once from init() after config is fetched. */
  function applyFeatureAvailability() {
    const f = state.features;

    const coverageToggle = $("#coverage-toggle");
    if (f.orbits === false) {
      coverageToggle.disabled = true;
      coverageToggle.closest("label").title =
        "Unavailable: the 'orbits' module didn't load (missing skyfield?) - see /api/config.";
    }

    const detectBtn = $("#btn-detect-events");
    if (f.events === false) {
      detectBtn.disabled = true;
      detectBtn.title = "Unavailable: the 'events' module didn't load - see /api/config.";
    }

    if (f.imagery === false) {
      const btn = $("#btn-check-burn-scar");
      btn.title = "Unavailable: the 'imagery' module didn't load (missing rasterio?) - see /api/config.";
    }
    if (f.terrain === false) {
      $("#btn-run-propagation").title =
        "Unavailable: the 'terrain' module didn't load (missing scikit-fmm?) - see /api/config.";
      $("#btn-run-ensemble").title =
        "Unavailable: the 'terrain' module didn't load (missing scikit-fmm?) - see /api/config.";
    }
    if (f.validation === false) {
      const btn = $("#btn-validate-event");
      btn.title = "Unavailable: the 'validation' module didn't load - see /api/config.";
    }
    if (f.industrial === false) {
      $("#industrial-toggle").disabled = true;
      $("#btn-scan-industrial").disabled = true;
      $("#btn-scan-industrial").title = "Unavailable: the 'industrial' module didn't load - see /api/config.";
    }
  }

  /**
   * App entry point: fetches server config, builds the map and every
   * panel/picker, does the first detection load, and starts the periodic
   * status/callout-freshness background timers. Its promise rejection is
   * handled by the top-level `init().catch(...)` below, which renders a
   * visible "failed to start" banner instead of leaving a blank page on
   * any startup error.
   */
  async function init() {
    const res = await fetch("/api/config");
    if (!res.ok) throw new Error(`/api/config returned HTTP ${res.status}`);
    const config = await res.json();
    state.config = config;
    // Which optional analysis modules actually loaded server-side (a heavy
    // dependency like rasterio might be missing) - gates the corresponding
    // buttons instead of letting a request 503 with no explanation.
    state.features = config.features || {};

    // The coordinate framework an administrator set for the whole incident.
    // Applied once per change rather than on every load, so it reaches browsers
    // that already made a local choice without overriding one made since - see
    // Coords.adoptSharedSystem.
    // Order matters: the definition has to be in place before "custom" becomes
    // the active system, or the first format() call after adoption finds no
    // projection and renders nothing.
    Coords.adoptSharedProj4(config.client_settings?.custom_proj4);
    Coords.adoptSharedSystem(config.client_settings?.coordinate_system);

    initMap(config);
    // Deliberate global, and the only one left: the CDP-driven browser test
    // suite (tests/test_browser_workflows.py) drives the map through
    // Runtime.evaluate, which has no way to reach an ES module binding. No
    // application module reads it - they all take the map from context.js's
    // whenMap() below, so nothing here depends on <script> order any more.
    window.NexFiremapMap = map;
    setMap(map);
    writeViewToHash(); // seed the URL immediately, even before any pan/zoom
    buildBasemaps(config);
    buildSourceList(config);
    wireControls();
    wireSearchControl();
    wireCoordSystemSelect();
    wireBasemapPicker();
    wireAppSwitcher();
    wireMapContextMenu();
    applyFeatureAvailability();
    renderLegend();

    $("#setup").hidden = config.has_map_key;
    // Also gated server-side (settings.has_eumetsat_key) - hiding the
    // section when no account is configured avoids a toggle that would
    // just always say "not configured" for the common case of someone not
    // having registered for this optional source.
    $("#eumetsat-section").hidden = !(config.has_eumetsat_key && state.features.eumetsat !== false);

    await loadDetections();
    await pollStatus(false);
    setInterval(() => {
      if (!state.pollTimer) pollStatus(false);
    }, 30000);
    setInterval(refreshFireCalloutFreshness, 60000);
  }

  init().catch((err) => {
    console.error(err);
    document.body.insertAdjacentHTML(
      "beforeend",
      `<div class="setup" style="display:block"><h2>NexFiremap failed to start</h2><p>${escapeHtml(err)}</p></div>`
    );
  });
