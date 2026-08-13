/* NexFiremap frontend.
   Talks to the local server only: the server owns the FIRMS cache, so the map
   never waits on NASA directly - it asks for what it needs, draws whatever is
   cached already, and redraws when the background fetch lands. */

(() => {
  "use strict";

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

  const state = {
    config: null,
    features: {}, // { orbits, events, likelihood, imagery, terrain } - which optional modules loaded server-side
    sources: new Map(),      // id -> meta
    enabledSources: new Set(),
    instruments: [],         // ordered list for categorical slots
    days: 3,
    focusDay: null,          // ISO date when a histogram bar is selected
    colorBy: "age",
    renderMode: "points",
    minFrp: 0,
    daynight: "",
    rows: [],
    summary: [],
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

  const css = (name) =>
    getComputedStyle(document.body).getPropertyValue(name).trim();

  const fireRamp = () => [1, 2, 3, 4, 5].map((n) => css(`--fire-${n}`));
  const catColors = () => [css("--cat-1"), css("--cat-2"), css("--cat-3")];

  // ----------------------------------------------------------- formatting

  const nf = new Intl.NumberFormat();

  function compact(value) {
    if (value === null || value === undefined) return "-";
    if (value >= 1e6) return (value / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (value >= 1e4) return (value / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
    return nf.format(Math.round(value));
  }

  function shortDate(iso) {
    const d = new Date(iso + "T00:00:00Z");
    return d.toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      timeZone: "UTC",
    });
  }

  function ageLabel(seconds) {
    if (seconds < HOUR) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
    if (seconds < DAY) return `${Math.round(seconds / HOUR)} h ago`;
    return `${Math.round(seconds / DAY)} d ago`;
  }

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
  function makeStaleGuard() {
    let token = 0;
    return { next: () => ++token, isCurrent: (t) => t === token };
  }

  // --------------------------------------------------------------- colours

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

  function readViewFromHash() {
    const m = /^#(\d+(?:\.\d+)?)\/(-?\d+(?:\.\d+)?)\/(-?\d+(?:\.\d+)?)$/.exec(
      window.location.hash
    );
    if (!m) return null;
    const [, zoom, lat, lon] = m.map(Number);
    if (!Number.isFinite(zoom) || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    return { zoom, lat, lon };
  }

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
      tile.title = bm.name;
      tile.innerHTML =
        `<span class="basemap-tile-thumb"><span class="basemap-tile-check" aria-hidden="true" hidden>✓</span></span>` +
        `<span class="basemap-tile-label">${escapeHtml(bm.name)}</span>`;
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
    config.overlays.forEach((ov) => {
      overlayLayers[ov.id] = L.tileLayer(ov.url, {
        attribution: ov.attribution,
        maxZoom: ov.max_zoom || 19,
        pane: "shadowPane",
      });
      const label = document.createElement("label");
      label.innerHTML = `<input type="checkbox" value="${ov.id}"><span>${escapeHtml(ov.name)}</span>`;
      overlays.appendChild(label);
      label.querySelector("input").addEventListener("change", (e) => {
        if (e.target.checked) overlayLayers[ov.id].addTo(map);
        else map.removeLayer(overlayLayers[ov.id]);
      });
    });
  }

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

  function refreshBasemapThumbnails() {
    if (!map) return;
    $$("#basemap-grid .basemap-tile").forEach((tile) => {
      const entry = basemapLayers[tile.dataset.basemapId];
      if (!entry) return;
      tile.querySelector(".basemap-tile-thumb").style.backgroundImage = `url("${thumbnailUrl(entry.meta)}")`;
    });
  }

  function refreshBasemapToggleThumb() {
    if (!map || !activeBasemap) return;
    $("#basemap-toggle-thumb").style.backgroundImage = `url("${thumbnailUrl(activeBasemap.meta)}")`;
  }

  function openBasemapFlyout() {
    refreshBasemapThumbnails();
    $("#basemap-flyout").hidden = false;
    $("#basemap-toggle").setAttribute("aria-expanded", "true");
  }

  function closeBasemapFlyout() {
    $("#basemap-flyout").hidden = true;
    $("#basemap-toggle").setAttribute("aria-expanded", "false");
  }

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

  function closeAppSwitcher() {
    $("#app-switcher-menu").hidden = true;
    $("#app-switcher-toggle").setAttribute("aria-expanded", "false");
  }

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

  function clearLayers() {
    [pointLayer, clusterLayer, heatLayer].forEach((layer) => {
      if (layer && map.hasLayer(layer)) map.removeLayer(layer);
    });
    pointLayer = clusterLayer = heatLayer = null;
  }

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
  function visibleRows() {
    const cursor = state.playback.cursor;
    if (cursor === null) return state.rows;
    return state.rows.filter((row) => row[2] <= cursor);
  }

  // ------------------------------------------------------------- playback

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

  function stopPlayback() {
    const pb = state.playback;
    pb.active = false;
    clearInterval(pb.timer);
    pb.timer = null;
    const btn = $("#btn-playback");
    btn.textContent = "▶";
    btn.setAttribute("aria-label", "Play acquisition-time animation");
  }

  function drawDetections() {
    clearLayers();
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

  // --------------------------------------------------------------- legend

  function renderLegend() {
    const el = $("#legend");
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

  function showTooltip(event, d) {
    const tip = $("#tooltip");
    tip.innerHTML = `<b>${compact(d.count)}</b> <span>detections</span><br><span>${shortDate(
      d.day
    )} · ${compact(d.frp_total)} MW total</span>`;
    tip.hidden = false;
    tip.style.left = `${Math.min(event.clientX + 12, window.innerWidth - 190)}px`;
    tip.style.top = `${event.clientY - 46}px`;
  }

  function hideTooltip() {
    $("#tooltip").hidden = true;
  }

  // ----------------------------------------------------------- stat tiles

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

  function bboxParam() {
    const b = map.getBounds();
    const west = Math.max(-180, b.getWest());
    const east = Math.min(180, b.getEast());
    return [west, b.getSouth(), east, b.getNorth()]
      .map((v) => v.toFixed(4))
      .join(",");
  }

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

  function scheduleLoad(delay = 250) {
    clearTimeout(state.loadTimer);
    state.loadTimer = setTimeout(loadDetections, delay);
  }

  // The four piggyback layers only ever get (re)populated from inside
  // loadDetections' success path below - its two early-return branches
  // (zoomed out, no sources enabled) used to just skip that, leaving
  // whatever those layers last showed for a *different*, zoomed-in
  // viewport still on the map instead of clearing or refreshing it.
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

  async function scanIndustrial() {
    const btn = $("#btn-scan-industrial");
    const status = $("#industrial-status");
    btn.disabled = true;
    btn.textContent = "scanning OpenStreetMap + cached history…";
    try {
      const res = await fetch("/api/industrial/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bbox: bboxParam() }),
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

  function renderEventMarkers() {
    if (eventMarkersLayer) map.removeLayer(eventMarkersLayer);
    const boxes = state.events.map((ev) =>
      L.rectangle([[ev.bbox_south, ev.bbox_west], [ev.bbox_north, ev.bbox_east]], {
        pane: "analysisPane",
        color: "#e34948",
        weight: 1,
        dashArray: "4 3",
        fillOpacity: 0.03,
      }).bindTooltip(`Event #${ev.id} · ${ev.detection_count} detections`)
    );
    eventMarkersLayer = L.layerGroup(boxes).addTo(map);
  }

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
      row.innerHTML = `<span>Event #${ev.id} <span class="meta">· ${ev.detection_count} pts · ${when}</span></span>`;
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

  function drawProbabilityEnvelopes(geo) {
    // Largest/least-confident mass first so smaller, more confident bands
    // layer visibly on top of it, producing the concentric-ring look.
    const sorted = {
      ...geo,
      features: [...geo.features].sort((a, b) => b.properties.probability_mass - a.properties.probability_mass),
    };
    return L.geoJSON(sorted, { pane: "analysisPane", style: styleProbabilityEnvelope });
  }

  // Same fire-ramp family as the probability envelopes: soonest arrival
  // brightest/thickest, furthest out faintest/thinnest - a glance should
  // read "spreads this way, this fast" without hovering each line.
  const ISOCHRONE_RAMP = ["#fd4f24", "#fd7924", "#fda65a", "#fdc98c", "#fde4c4"];

  function drawIsochrones(geo) {
    const sortedHours = [...new Set(geo.features.map((f) => f.properties.hours))].sort((a, b) => a - b);
    const style = (feature) => {
      const idx = sortedHours.indexOf(feature.properties.hours);
      return {
        color: ISOCHRONE_RAMP[Math.min(idx, ISOCHRONE_RAMP.length - 1)],
        weight: Math.max(1, 2.6 - idx * 0.4),
        opacity: Math.max(0.35, 0.9 - idx * 0.14),
      };
    };
    return L.geoJSON(geo, {
      pane: "analysisPane",
      style,
      onEachFeature: (feature, layer) =>
        layer.bindTooltip(`+${feature.properties.hours}h`, {
          permanent: true,
          direction: "center",
          className: "isochrone-label",
        }),
    });
  }

  function fmtArea(km2) {
    if (km2 == null) return null;
    const mi2 = km2 * 0.386102;
    const fmt = (v) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2));
    return `${fmt(km2)} km² (${fmt(mi2)} mi²)`;
  }

  function relativeTime(ts) {
    const mins = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.round(hours / 24)}d ago`;
  }

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
  function refreshFireCalloutFreshness() {
    if (!calloutMarker || !calloutReferenceTs) return;
    const el = calloutMarker.getElement();
    const span = el && el.querySelector(".fire-callout-text .muted");
    if (span) span.textContent = `Updated ${relativeTime(calloutReferenceTs)}`;
  }

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

  function publishSpreadAnalysis(detail) {
    window.NexFiremapSpreadAnalysis = detail;
    window.dispatchEvent(new CustomEvent("nexfiremap:spread-analysis", { detail }));
  }

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

  function close3DView() {
    $("#map3d-wrap").hidden = true;
  }

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

  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(() => pollStatus(true), 3000);
  }

  function stopPolling() {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  // ------------------------------------------------------------------ init

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

  function parseCoordinateQuery(raw) {
    // Delegates to coords.js: decimal degrees, DMS, DDM, MGRS and UTM are
    // all self-describing enough to auto-detect; a bare "number number"
    // pair with none of those markers falls back to whatever coordinate
    // system is currently selected (see wireCoordSystemSelect() below),
    // WGS84 decimal degrees by default.
    if (typeof window.NexCoords === "undefined") return null;
    const point = window.NexCoords.parse(raw, window.NexCoords.currentSystem());
    if (!point) return null;
    const label = window.NexCoords.format(point.lat, point.lon, window.NexCoords.currentSystem());
    return { label: label || `${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}`, lat: point.lat, lon: point.lon, bounds: null, type: "coordinates" };
  }

  function wireCoordSystemSelect() {
    if (typeof window.NexCoords === "undefined") return;
    const select = $("#coord-system-select");
    const customInput = $("#coord-custom-proj4");
    select.innerHTML = window.NexCoords.SYSTEMS.map(
      (system) => `<option value="${system.id}">${escapeHtml(system.label)}</option>`
    ).join("");
    const stored = window.NexCoords.currentSystem();
    select.value = window.NexCoords.SYSTEMS.some((system) => system.id === stored) ? stored : "wgs84";
    customInput.hidden = select.value !== "custom";
    try {
      customInput.value = localStorage.getItem("nexfiremap.coords.customProj4") || "";
    } catch (_) {
      /* private mode/storage pressure - custom definition just starts blank */
    }
    select.addEventListener("change", () => {
      window.NexCoords.setSystem(select.value);
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

  function closeSearchResults() {
    const list = $("#map-search-results");
    list.hidden = true;
    list.innerHTML = "";
    searchResults = [];
    searchActiveIndex = -1;
    $("#map-search-input").setAttribute("aria-expanded", "false");
  }

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

  function moveSearchSelection(delta) {
    if (!searchResults.length) return;
    searchActiveIndex = (searchActiveIndex + delta + searchResults.length) % searchResults.length;
    $$("#map-search-results [role=option]").forEach((el, i) =>
      el.setAttribute("aria-selected", String(i === searchActiveIndex))
    );
    const active = $(`#map-search-opt-${searchActiveIndex}`);
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  function wireSearchControl() {
    const input = $("#map-search-input");
    const clearBtn = $("#map-search-clear");
    const list = $("#map-search-results");

    input.addEventListener("input", () => {
      clearBtn.hidden = !input.value;
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

  async function init() {
    const res = await fetch("/api/config");
    if (!res.ok) throw new Error(`/api/config returned HTTP ${res.status}`);
    const config = await res.json();
    state.config = config;
    // Which optional analysis modules actually loaded server-side (a heavy
    // dependency like rasterio might be missing) - gates the corresponding
    // buttons instead of letting a request 503 with no explanation.
    state.features = config.features || {};

    initMap(config);
    window.NexFiremapMap = map;
    window.dispatchEvent(new CustomEvent("nexfiremap:ready", { detail: { map } }));
    writeViewToHash(); // seed the URL immediately, even before any pan/zoom
    buildBasemaps(config);
    buildSourceList(config);
    wireControls();
    wireSearchControl();
    wireCoordSystemSelect();
    wireBasemapPicker();
    wireAppSwitcher();
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
})();
