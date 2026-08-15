/* Official CAP public warnings as a map overlay.

   Deliberately a standalone ES module with its own Leaflet control rather
   than another branch inside app.js. Two reasons: the layer is optional (an
   install with no NEXFIREMAP_CAP_FEEDS configured has nothing to draw, and
   this module then adds no control at all), and keeping it self-contained
   means it reaches the map through context.js's registry exactly like
   operations.js and structures.js do, instead of needing app.js to know it
   exists.

   The warnings drawn here come from a government publisher, not from
   NexFiremap's own analysis, so the styling keeps them visually distinct
   from tactical features: dashed outlines, low fill opacity, and never on
   top of the operational layers. An official warning is context for the
   incident picture, not part of it. */

import { whenMap, fetchJson } from "./context.js";

/* CAP's own severity vocabulary, most severe first, with the colours the
   popup and the outline share. Unknown gets a neutral grey rather than being
   dropped: a warning whose severity the publisher declined to state is still
   a warning, and guessing one would be inventing information. */
const SEVERITY_STYLE = {
  Extreme: { color: "#7f1d1d", fill: "#b3261e", weight: 2.5 },
  Severe: { color: "#b3261e", fill: "#e0457b", weight: 2 },
  Moderate: { color: "#c26a00", fill: "#e8a317", weight: 1.8 },
  Minor: { color: "#8a7100", fill: "#f2e309", weight: 1.5 },
  Unknown: { color: "#6b7280", fill: "#9ca3af", weight: 1.5 },
};

/* Refetch is debounced behind map movement rather than run on every `move`
   event: a single pan fires dozens, and each would otherwise be a request to
   the server (and a re-render of up to several hundred polygons). */
const REFRESH_DEBOUNCE_MS = 400;

const state = { map: null, layer: null, enabled: false, timer: 0, token: 0, count: 0, control: null };

/** Escapes a value for interpolation into innerHTML. Alert text is written by
 * an external publisher, so it is untrusted input by definition - the server
 * stores it verbatim for provenance and it must be escaped here, at the point
 * it becomes markup.
 * @param {*} value - Value to escape; nullish becomes an empty string.
 * @returns {string} Escaped text. */
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** Formats an ISO timestamp in the reader's local zone, or "" when absent.
 * Local rather than UTC because this string is read off a screen by someone
 * deciding whether a warning is still current.
 * @param {?string} iso - ISO 8601 timestamp.
 * @returns {string} Localised text, or "". */
function localTime(iso) {
  if (!iso) return "";
  const value = new Date(iso);
  return Number.isNaN(value.getTime()) ? "" : value.toLocaleString();
}

/** @param {object} properties - Alert properties from /api/alerts.
 * @returns {object} A Leaflet path style. */
function styleFor(properties) {
  const style = SEVERITY_STYLE[properties.severity] || SEVERITY_STYLE.Unknown;
  return {
    color: style.color,
    fillColor: style.fill,
    weight: style.weight,
    // Dashed and lightly filled so an official warning never reads as one of
    // our own tactical polygons, and never obscures what is drawn beneath it.
    dashArray: "6 4",
    fillOpacity: 0.18,
    opacity: 0.9,
  };
}

/** @param {object} properties - Alert properties.
 * @returns {string} Popup markup. */
function popupFor(properties) {
  const expires = localTime(properties.expires);
  const rows = [
    ["Severity", properties.severity],
    ["Urgency", properties.urgency],
    ["Certainty", properties.certainty],
    ["Issued", localTime(properties.sent)],
    ["Expires", expires || "until cancelled"],
    ["Sender", properties.sender],
  ].filter(([, value]) => value);
  return (
    `<div class="alert-popup"><strong>${escapeHtml(properties.headline || properties.event)}</strong>` +
    `<p class="alert-popup-source">Official public warning - not a NexFiremap analysis</p>` +
    (properties.description ? `<p>${escapeHtml(properties.description)}</p>` : "") +
    (properties.instruction ? `<p><em>${escapeHtml(properties.instruction)}</em></p>` : "") +
    `<dl>${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("")}</dl>` +
    `<p><a href="/api/alerts/${encodeURIComponent(properties.identifier)}/original" target="_blank" rel="noopener">source CAP document</a></p></div>`
  );
}

/** Fetches the alerts intersecting the current viewport and redraws.
 *
 * The `token` guard discards a response that arrived after a newer request
 * was already issued - without it a slow reply for an old viewport can
 * overwrite the layer with warnings for somewhere the operator has already
 * panned away from.
 * @returns {Promise<void>} */
async function refresh() {
  if (!state.map || !state.enabled) return;
  const bounds = state.map.getBounds();
  const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()]
    .map((value) => value.toFixed(4)).join(",");
  const token = ++state.token;
  let payload;
  try {
    payload = await fetchJson(`/api/alerts?bbox=${bbox}`);
  } catch (error) {
    // A failed alert fetch must never break the fire map. The layer keeps
    // whatever it last drew and the control reports the problem.
    if (token === state.token) setStatus(`warnings unavailable: ${error.message}`);
    return;
  }
  if (token !== state.token) return;

  state.layer.clearLayers();
  payload.features.forEach((feature) => {
    L.geoJSON(feature, { style: () => styleFor(feature.properties) })
      .bindPopup(popupFor(feature.properties))
      .addTo(state.layer);
  });
  state.count = payload.features.length;

  /* Geocode-only alerts (a German WARNCELLID, a US SAME code) have no polygon
     and cannot be drawn at all. Reporting the count in the control is what
     stops the map silently understating the situation - "3 more not mapped"
     is very different information from nothing. */
  const unmapped = (payload.without_geometry || []).length;
  const parts = [`${state.count} warning${state.count === 1 ? "" : "s"}`];
  if (unmapped) parts.push(`${unmapped} without a mappable area`);
  if (payload.last_error) parts.push(`feed problem: ${payload.last_error}`);
  setStatus(parts.join(" - "));
}

/** Debounces refresh behind map movement. @returns {void} */
function scheduleRefresh() {
  window.clearTimeout(state.timer);
  state.timer = window.setTimeout(refresh, REFRESH_DEBOUNCE_MS);
}

/** @param {string} text - Status line shown under the control's checkbox. */
function setStatus(text) {
  if (!state.control) return;
  const line = state.control.querySelector("[data-alerts-status]");
  if (line) line.textContent = text;
}

/** Builds the layer toggle as a Leaflet control.
 *
 * A control rather than an entry in the existing overlay list because this
 * layer also has to report *why* it might be showing nothing (feed
 * unreachable, warnings with no mappable area), which a plain checkbox has
 * nowhere to say.
 * @param {L.Map} map - The Leaflet map.
 * @returns {void} */
function addControl(map) {
  /* bottomright, not topright: the app's own .topbar spans the full width down
     to about 96px, so a top-anchored Leaflet control renders *underneath* it -
     visible only as a sliver, and with its checkbox not actually clickable
     because the toolbar's buttons win the hit test. Bottom-left is taken by
     the zoom controls and the attribution, which leaves this corner. */
  const control = L.control({ position: "bottomright" });
  control.onAdd = () => {
    const container = L.DomUtil.create("div", "leaflet-bar alerts-control");
    container.innerHTML =
      `<label class="alerts-toggle"><input type="checkbox" data-alerts-toggle> Official warnings</label>` +
      `<span class="alerts-status" data-alerts-status></span>`;
    // Without this a click on the checkbox also pans/zooms the map underneath.
    L.DomEvent.disableClickPropagation(container);
    L.DomEvent.disableScrollPropagation(container);
    container.querySelector("[data-alerts-toggle]").addEventListener("change", (event) => {
      state.enabled = event.target.checked;
      if (state.enabled) {
        state.layer.addTo(map);
        refresh();
      } else {
        map.removeLayer(state.layer);
        setStatus("");
      }
    });
    state.control = container;
    return container;
  };
  control.addTo(map);
}

/** Wires the overlay up once the map exists and the server reports that CAP
 * feeds are actually configured.
 *
 * The feature check is what keeps an install that never wanted CAP from
 * growing a control that can only ever be empty - the same gating every other
 * optional feature uses via /api/config's `features` map.
 * @returns {Promise<void>} */
async function init() {
  let config;
  try {
    config = await fetchJson("/api/config");
  } catch {
    return; // app.js reports config failures; this module stays silent.
  }
  if (!config.features || !config.features.alerts) return;

  whenMap((map) => {
    state.map = map;
    state.layer = L.layerGroup();
    addControl(map);
    map.on("moveend zoomend", scheduleRefresh);
  });
}

init();
