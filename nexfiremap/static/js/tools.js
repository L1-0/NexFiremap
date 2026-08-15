/* The map tool palette: one tool owns the left click at a time.

   Left-click in this app was subscribed by three modules simultaneously -
   operations.js binds `map.on("click", drawClick)` while sketching, markers and
   features open their own popups, app.js has its own handlers - and nothing
   made that visible or exclusive. "What does clicking do right now?" had no
   answer you could point at.

   This palette makes it explicit. Exactly one tool is active; it owns the
   click; switching tools tears the previous one down through a single code
   path. **`select` is the default and reproduces the previous behaviour
   exactly**, so anyone who ignores the toolbar sees no change at all.

   The waypoint/area tool is the reason this exists. `/api/situation` can
   already answer everything the app knows about a point, but the only way to
   reach it was a right-click menu item. And drawing an area meant choosing
   *what it is* from a dropdown before you knew *what shape it is* - backwards
   from how an operator works. Here: click once to ask about a point, keep
   clicking to grow that point into an area, then decide what the area means.

   The one way a palette like this goes wrong is a tool that fails to release
   the click when deselected, leaving the map feeling broken in a way that is
   hard to attribute. Two guards: `deactivate()` is only ever called by
   `setTool` (never by a tool itself), and Escape unconditionally returns to
   select and tears down whatever was active. */

import {
  whenMap, fetchJson, setActiveTool, getActiveTool, getActiveIncident,
} from "./context.js";

const SPRITE_URL = "/static/vendor/symbols/tools.svg";

/* A dedicated Leaflet pane for whatever the active tool draws.
   While a tool owns the click, every *other* pane is made click-through in CSS
   (see body.nf-tool-* rules in app.css). Without that, clicking inside an
   analysis polygon, an event outline or an alert area does nothing at all -
   those layers are interactive and swallow the click before it reaches the
   map - which is exactly backwards, since those polygons cover the ground an
   operator most wants to ask about. The tool's own pane stays interactive so
   its vertex handles remain draggable. */
const TOOL_PANE = "nfTools";

/** @type {Map<string, object>} */
const tools = new Map();
let map = null;
let container = null;

/** Escapes a value for interpolation into innerHTML. Place names, incident
 * names and feature titles all reach this from operator or geocoder input.
 * @param {*} value - Value to escape; nullish becomes an empty string.
 * @returns {string} Escaped text. */
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** POSTs/PUTs JSON with the session's CSRF token when LAN mode requires one.
 * @param {string} url - Request URL.
 * @param {object} body - JSON body.
 * @param {string} [method] - HTTP method.
 * @returns {Promise<object>} Parsed JSON payload. */
async function writeJson(url, body, method = "POST") {
  const headers = { "Content-Type": "application/json" };
  try {
    const session = await fetchJson("/api/auth/session");
    if (session.csrf) headers["X-CSRF-Token"] = session.csrf;
  } catch {
    /* loopback single-user mode has no session; the request itself will 401 if it matters */
  }
  const response = await fetch(url, { method, headers, body: JSON.stringify(body) });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
  return payload;
}

// ------------------------------------------------------------- the palette

/** Registers a tool. Tools are plain objects with `activate`/`deactivate`, so a
 * module can contribute one (operations.js registers its sketch tool) without
 * this file knowing anything about its domain.
 *
 * An entry may instead supply `action`, which makes it a one-shot button rather
 * than a mode: it runs and the active tool is left alone. That distinction is
 * the palette's central contract showing through - a mode claims the left
 * click, and something like the settings pane has no business claiming it. The
 * alternative (a settings "tool" that opens a dialog and then hands the click
 * back) would put the map in a state where what a click does depends on a
 * dialog nobody can see any more.
 * @param {object} tool - {id, label, key, hint, activate, deactivate, hasUnsavedWork?}
 *   or {id, label, key, action} for a one-shot button. */
export function registerTool(tool) {
  tools.set(tool.id, tool);
  if (container) renderPalette();
}

/** Switches tools, tearing down the previous one.
 *
 * The single code path a `deactivate()` ever runs through - see the module
 * docstring. A tool holding unsaved work is asked about rather than silently
 * discarded, matching the care startDraw()/cancelEdit() already take with each
 * other in operations.js.
 * @param {string} id - Tool id to activate.
 * @param {{force?: boolean}} [options] - force skips the unsaved-work prompt.
 * @returns {boolean} Whether the switch happened. */
export function setTool(id, options = {}) {
  // One-shot entries run without disturbing the active tool - see registerTool.
  const target = tools.get(id);
  if (target?.action) { target.action(); return true; }
  const current = tools.get(getActiveTool());
  if (current && current.id === id) return true;
  if (current && !options.force && current.hasUnsavedWork?.()) {
    if (!window.confirm(`${current.label} has unsaved work. Discard it?`)) return false;
  }
  try {
    current?.deactivate?.();
  } catch (error) {
    // A tool that throws on teardown must not strand the palette in its state -
    // that is precisely the "map feels broken" failure this design guards against.
    console.warn("tool deactivate failed", current?.id, error);
  }
  document.body.classList.remove(`nf-tool-${current?.id || "select"}`);
  setActiveTool(id);
  document.body.classList.add(`nf-tool-${id}`);
  tools.get(id)?.activate?.();
  renderPalette();
  return true;
}

/** Recomputes every tool's enabled state and redraws the palette.
 *
 * A tool's `available()` reads live application state - the Sketch tool asks
 * whether an incident and an operational period are selected - but the palette
 * only ever rendered on `registerTool` and on a tool switch. Nothing redrew it
 * when that state changed, so selecting an incident and a period left the
 * Sketch button disabled, still showing "select an incident and operational
 * period first", and the tactical-drawing entry point was unreachable for the
 * whole session. The state was right; the button had simply been painted once,
 * before it was true.
 *
 * Exported so the module that owns the state can say when it changed, rather
 * than this file polling or guessing at a domain it deliberately knows nothing
 * about.
 */
export function refreshTools() {
  renderPalette();
}

/** Rebuilds the palette's buttons to match the registered tools and the
 * active one. Cheap enough to redo wholesale on every change. */
function renderPalette() {
  if (!container) return;
  const active = getActiveTool();
  // Modes first, one-shot buttons last - by *kind*, not by registration order.
  // Order of registration is really module evaluation order, which is not a
  // design decision: settings.js registers at module scope while the drawing
  // tools wait for whenMap(), so a settings button would otherwise sit above
  // Select purely because it needs no map. A stable sort keeps each group in
  // its own registration order.
  const ordered = [...tools.values()].sort((a, b) => Number(!!a.action) - Number(!!b.action));
  container.innerHTML = ordered.map((tool) => {
    const disabled = tool.available && !tool.available();
    // A one-shot button is never "the active tool", so it gets neither the
    // active styling nor aria-pressed - it is a button, not a toggle, and a
    // screen reader should not announce it as one.
    const pressed = tool.action ? "" : `aria-pressed="${tool.id === active}"`;
    return `<button type="button" class="nf-tool${
        !tool.action && tool.id === active ? " is-active" : ""}${tool.action ? " is-action" : ""}"
      data-tool="${escapeHtml(tool.id)}" ${disabled ? "disabled" : ""}
      ${pressed}
      title="${escapeHtml(tool.label)}${tool.key ? ` (${tool.key.toUpperCase()})` : ""}${
        disabled && tool.unavailableHint ? ` - ${escapeHtml(tool.unavailableHint)}` : ""}">
      <svg viewBox="0 0 24 24" aria-hidden="true"><use href="#nf-tool-${escapeHtml(tool.id)}"/></svg>
      <span class="nf-tool-label">${escapeHtml(tool.label)}</span>
    </button>`;
  }).join("");
}

/** Injects the vendored icon sprite. `<use href>` cannot reference an external
 * file (Chrome dropped cross-document SVG references), so the sheet has to live
 * in this document. A failure is non-fatal: the buttons still work, they just
 * show their text labels.
 * @returns {Promise<void>} */
async function loadSprites() {
  if (document.getElementById("nf-tool-sprites")) return;
  try {
    const response = await fetch(SPRITE_URL);
    if (!response.ok) return;
    const holder = document.createElement("div");
    holder.id = "nf-tool-sprites";
    holder.hidden = true;
    holder.innerHTML = await response.text();
    document.body.appendChild(holder);
  } catch {
    /* offline: labels carry the meaning */
  }
}

// -------------------------------------------------- the waypoint/area tool

/* One state machine, not two. A waypoint that gains vertices simply becomes an
   area - which is the whole point: an operator outlines a shape first and
   decides what it means afterwards, rather than picking a feature type from a
   dropdown before they know the shape. */

const shape = {
  /** @type {Array<Array<number>>} [lon, lat] in click order. */
  points: [],
  /** @type {?L.LayerGroup} */
  layer: null,
  /** @type {Array<L.Marker>} */
  handles: [],
  panel: null,
};

/** GeoJSON for the current vertex list, or null when there is nothing to make.
 *
 * Exported because it is a pure function of the vertex list and is where the
 * shape's *meaning* is decided - one point is a Point, two a LineString, three
 * or more a closed Polygon. Tested directly rather than through the browser.
 * @param {Array<Array<number>>} points - [lon, lat] pairs.
 * @returns {?object} A GeoJSON geometry. */
export function geometryFor(points) {
  if (!points.length) return null;
  if (points.length === 1) return { type: "Point", coordinates: [...points[0]] };
  if (points.length === 2) return { type: "LineString", coordinates: points.map((p) => [...p]) };
  // Closed here as well as server-side (normalise_aoi/_validate_geometry both
  // close rings) so the on-map preview matches what will be stored.
  const ring = points.map((p) => [...p]);
  ring.push([...points[0]]);
  return { type: "Polygon", coordinates: [ring] };
}

/** Centroid of the vertex list, for "ask about this area" and for anchoring
 * the commit popup.
 * @param {Array<Array<number>>} points - [lon, lat] pairs.
 * @returns {?Array<number>} [lon, lat]. */
export function centroidOf(points) {
  if (!points.length) return null;
  const lon = points.reduce((sum, p) => sum + p[0], 0) / points.length;
  const lat = points.reduce((sum, p) => sum + p[1], 0) / points.length;
  return [lon, lat];
}

/** Bounding box of the vertex list as "west,south,east,north".
 * @param {Array<Array<number>>} points - [lon, lat] pairs.
 * @returns {?string} */
export function bboxOf(points) {
  if (!points.length) return null;
  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)]
    .map((value) => value.toFixed(5)).join(",");
}

/** Redraws the preview and its vertex handles from `shape.points`.
 *
 * Dashed white, matching operations.js's redrawSketch() so the two drawing
 * surfaces look like one application rather than two features bolted together. */
function redraw() {
  if (!shape.layer) return;
  shape.layer.clearLayers();
  shape.handles = [];
  const geometry = geometryFor(shape.points);
  if (!geometry) return;

  if (geometry.type === "Polygon") {
    shape.layer.addLayer(L.polygon(geometry.coordinates[0].map(([lon, lat]) => [lat, lon]),
      { color: "#fff", weight: 2, dashArray: "5 5", fillOpacity: 0.12 }));
  } else if (geometry.type === "LineString") {
    shape.layer.addLayer(L.polyline(geometry.coordinates.map(([lon, lat]) => [lat, lon]),
      { color: "#fff", weight: 2, dashArray: "5 5" }));
  }

  shape.points.forEach(([lon, lat], index) => {
    const handle = L.marker([lat, lon], {
      draggable: true,
      icon: L.divIcon({ className: "nf-vertex", html: "<span></span>",
                        iconSize: [14, 14], iconAnchor: [7, 7] }),
    });
    handle.on("drag", (event) => {
      shape.points[index] = [event.latlng.lng, event.latlng.lat];
      redrawPreviewOnly();
    });
    handle.on("dragend", () => { redraw(); refreshPanel(); });
    // Right-click removes a vertex. The shape degrades polygon -> line ->
    // waypoint rather than hitting a dead end, so there is no state an operator
    // can get stuck in by removing one point too many.
    handle.on("contextmenu", (event) => {
      L.DomEvent.stop(event);
      shape.points.splice(index, 1);
      redraw();
      refreshPanel();
    });
    shape.layer.addLayer(handle);
  });
}

/** Redraws only the outline during a drag - rebuilding the handles mid-drag
 * would destroy the marker currently being dragged. */
function redrawPreviewOnly() {
  const geometry = geometryFor(shape.points);
  if (!geometry || !shape.layer) return;
  shape.layer.getLayers()
    .filter((layer) => layer instanceof L.Polyline)
    .forEach((layer) => {
      const ring = geometry.type === "Polygon" ? geometry.coordinates[0] : geometry.coordinates;
      layer.setLatLngs(ring.map(([lon, lat]) => [lat, lon]));
    });
}

/** Adds a vertex and refreshes everything that depends on the shape.
 * @param {L.LatLng} latlng - Clicked position. */
function addVertex(latlng) {
  shape.points.push([latlng.lng, latlng.lat]);
  redraw();
  refreshPanel();
}

/** Rebuilds the side panel for the current shape: a situation report while it
 * is a single waypoint, a commit bar once it is an area. */
async function refreshPanel() {
  if (!shape.panel) return;
  const count = shape.points.length;
  if (!count) { shape.panel.hidden = true; shape.panel.innerHTML = ""; return; }
  shape.panel.hidden = false;

  if (count === 1) {
    await renderSituation(shape.points[0]);
    return;
  }
  renderCommit();
}

/** Renders everything the app knows about one point.
 *
 * This is the "show all about that waypoint" behaviour - a renderer for
 * /api/situation, not a second source of truth. A provider the server could not
 * consult is named rather than omitted, preserving the distinction between "we
 * did not ask" and "there is nothing there".
 * @param {Array<number>} point - [lon, lat].
 * @returns {Promise<void>} */
async function renderSituation(point) {
  const [lon, lat] = point;
  shape.panel.innerHTML =
    `<header><strong>Waypoint</strong><span>${lat.toFixed(5)}, ${lon.toFixed(5)}</span></header>` +
    `<p class="nf-panel-hint">Gathering situation…</p>`;
  let report;
  try {
    report = await fetchJson(`/api/situation?lat=${lat.toFixed(5)}&lon=${lon.toFixed(5)}`);
  } catch (error) {
    // Must fail soft: the drawing tool has to keep working with the WAN down.
    shape.panel.innerHTML =
      `<header><strong>Waypoint</strong><span>${lat.toFixed(5)}, ${lon.toFixed(5)}</span></header>` +
      `<p class="nf-panel-hint">Situation unavailable: ${escapeHtml(error.message)}</p>` +
      `<p class="nf-panel-hint">Keep clicking to outline an area.</p>`;
    return;
  }
  const rows = (report.sections || []).filter((s) => s.summary)
    .map((s) => `<dt>${escapeHtml(s.label)}</dt><dd>${escapeHtml(s.summary)}</dd>`).join("");
  const missing = (report.unavailable || []).length
    ? `<p class="nf-panel-hint">Not consulted: ${escapeHtml(report.unavailable.join(", "))}</p>` : "";
  shape.panel.innerHTML =
    `<header><strong>Waypoint</strong><span>${lat.toFixed(5)}, ${lon.toFixed(5)}</span></header>` +
    (rows ? `<dl>${rows}</dl>` : "<p class='nf-panel-hint'>Nothing known here.</p>") + missing +
    `<p class="nf-panel-hint">Click again to outline an area · Esc to clear</p>`;
}

/** Renders the commit bar for a drawn area.
 *
 * The feature-type list comes from the server's own vocabulary
 * (/api/operations/meta's geometry.Polygon) rather than a hardcoded client
 * list, so adding an area type server-side makes it appear here automatically -
 * exactly as it already does in the sketch dropdown.
 * @returns {Promise<void>} */
async function renderCommit() {
  const count = shape.points.length;
  const incident = getActiveIncident();
  const closed = count >= 3;
  let areaTypes = [];
  let labels = {};
  try {
    const meta = await fetchJson("/api/operations/meta");
    areaTypes = meta.geometry?.Polygon || [];
    labels = meta.symbology?.symbols || {};
  } catch {
    /* offline: the AOI and query actions still work without the vocabulary */
  }

  const options = areaTypes
    .map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(labels[type]?.label || type.replace(/_/g, " "))}</option>`)
    .join("");

  shape.panel.innerHTML =
    `<header><strong>${closed ? "Area" : "Line"}</strong><span>${count} point${count === 1 ? "" : "s"}</span></header>` +
    (closed ? "" : "<p class='nf-panel-hint'>Add one more point to close an area.</p>") +
    `<p class="nf-panel-hint">Drag a point to move it · right-click a point to remove it</p>` +
    (closed && incident && options
      ? `<label class="nf-panel-field"><span>Save as</span>
           <select data-area-type>${options}</select></label>
         <button type="button" class="nf-panel-btn" data-commit="feature">Add to ${escapeHtml(incident.name)}</button>`
      : "") +
    (closed && incident
      ? `<button type="button" class="nf-panel-btn" data-commit="aoi">Set as area of interest</button>` : "") +
    (closed && !incident
      ? `<button type="button" class="nf-panel-btn" data-commit="incident">Create incident from this area</button>` : "") +
    (closed ? `<button type="button" class="nf-panel-btn ghost" data-commit="query">What is in here?</button>` : "") +
    `<button type="button" class="nf-panel-btn ghost" data-commit="cancel">Clear</button>`;
}

/** Applies whichever commit action was pressed.
 * @param {string} action - One of feature/aoi/incident/query/cancel.
 * @returns {Promise<void>} */
async function commit(action) {
  const incident = getActiveIncident();
  const geometry = geometryFor(shape.points);
  if (action === "cancel" || !geometry) { reset(); return; }

  try {
    if (action === "aoi" && incident) {
      await writeJson(`/api/operations/incidents/${incident.id}/aoi`, { aoi: geometry }, "PUT");
      note(`Area of interest set for ${incident.name}.`);
    } else if (action === "feature" && incident) {
      const type = shape.panel.querySelector("[data-area-type]")?.value;
      await writeJson(`/api/operations/incidents/${incident.id}/features`, {
        feature_type: type, geometry, title: `Drawn ${String(type).replace(/_/g, " ")}`,
      });
      note(`Added to ${incident.name}.`);
    } else if (action === "incident") {
      const created = await writeJson("/api/operations/incidents/from_context",
        { bbox: bboxOf(shape.points) });
      note(`Created ${created.incident?.name || "incident"}.`);
    } else if (action === "query") {
      const centre = centroidOf(shape.points);
      await renderSituation(centre);
      return; // keep the shape - the operator asked a question, not made a change
    }
    reset();
  } catch (error) {
    note(`Could not save: ${error.message}`, true);
  }
}

/** Shows a one-line result under the palette.
 * @param {string} message - Text to show.
 * @param {boolean} [isError] - Style as a problem. */
function note(message, isError = false) {
  if (!shape.panel) return;
  shape.panel.hidden = false;
  shape.panel.innerHTML =
    `<p class="nf-panel-hint${isError ? " is-error" : ""}">${escapeHtml(message)}</p>`;
  if (!isError) window.setTimeout(() => { if (shape.panel) shape.panel.hidden = true; }, 4000);
}

/** Clears the shape and its panel without changing the active tool. */
function reset() {
  shape.points = [];
  shape.layer?.clearLayers();
  shape.handles = [];
  if (shape.panel) { shape.panel.hidden = true; shape.panel.innerHTML = ""; }
}

function onMapClick(event) {
  addVertex(event.latlng);
}

const waypointTool = {
  id: "waypoint",
  label: "Waypoint",
  key: "w",
  hasUnsavedWork: () => shape.points.length > 0,
  activate() {
    shape.layer = shape.layer || L.layerGroup({ pane: TOOL_PANE }).addTo(map);
    map.on("click", onMapClick);
  },
  deactivate() {
    map.off("click", onMapClick);
    reset();
  },
};

// ------------------------------------------------------------ measure tool

const measure = { points: [], layer: null };

/** Formats a distance the way an operator reads it on a map: metres under a
 * kilometre, kilometres above.
 * @param {number} metres - Distance in metres.
 * @returns {string} */
export function formatDistance(metres) {
  return metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(2)} km`;
}

/** Initial compass bearing from one point to another, in degrees.
 * @param {Array<number>} from - [lon, lat].
 * @param {Array<number>} to - [lon, lat].
 * @returns {number} Bearing 0-360. */
export function bearingBetween(from, to) {
  const toRad = (value) => (value * Math.PI) / 180;
  const [lon1, lat1] = from;
  const [lon2, lat2] = to;
  const dLon = toRad(lon2 - lon1);
  const y = Math.sin(dLon) * Math.cos(toRad(lat2));
  const x = Math.cos(toRad(lat1)) * Math.sin(toRad(lat2))
    - Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLon);
  return (((Math.atan2(y, x) * 180) / Math.PI) + 360) % 360;
}

function redrawMeasure() {
  measure.layer.clearLayers();
  if (measure.points.length < 1) return;
  const latlngs = measure.points.map(([lon, lat]) => [lat, lon]);
  if (latlngs.length > 1) {
    measure.layer.addLayer(L.polyline(latlngs, { color: "#fd7924", weight: 3, dashArray: "6 4" }));
  }
  latlngs.forEach((latlng) => measure.layer.addLayer(L.marker(latlng, {
    icon: L.divIcon({ className: "nf-vertex", html: "<span></span>",
                      iconSize: [14, 14], iconAnchor: [7, 7] }),
    interactive: false,
  })));
}

function renderMeasurePanel() {
  if (!shape.panel) return;
  if (measure.points.length < 2) {
    shape.panel.hidden = false;
    shape.panel.innerHTML = `<header><strong>Measure</strong><span>${measure.points.length} point${
      measure.points.length === 1 ? "" : "s"}</span></header>` +
      `<p class="nf-panel-hint">Click a second point for distance and bearing · Esc to clear</p>`;
    return;
  }
  // Leaflet's own LatLng.distanceTo is haversine, so leg lengths need no
  // projection maths of ours - the same reasoning app.js's area readout uses.
  let total = 0;
  const legs = [];
  for (let index = 1; index < measure.points.length; index += 1) {
    const from = measure.points[index - 1];
    const to = measure.points[index];
    const metres = L.latLng(from[1], from[0]).distanceTo(L.latLng(to[1], to[0]));
    total += metres;
    legs.push(`<dt>Leg ${index}</dt><dd>${formatDistance(metres)} · ${
      bearingBetween(from, to).toFixed(0)}°</dd>`);
  }
  const straight = L.latLng(measure.points[0][1], measure.points[0][0])
    .distanceTo(L.latLng(measure.points.at(-1)[1], measure.points.at(-1)[0]));
  shape.panel.hidden = false;
  shape.panel.innerHTML =
    `<header><strong>Measure</strong><span>${measure.points.length} points</span></header>` +
    `<dl>${legs.join("")}<dt>Total</dt><dd>${formatDistance(total)}</dd>` +
    (measure.points.length > 2 ? `<dt>Straight line</dt><dd>${formatDistance(straight)}</dd>` : "") +
    `</dl><p class="nf-panel-hint">Esc to clear</p>`;
}

function onMeasureClick(event) {
  measure.points.push([event.latlng.lng, event.latlng.lat]);
  redrawMeasure();
  renderMeasurePanel();
}

const measureTool = {
  id: "measure",
  label: "Measure",
  key: "m",
  hasUnsavedWork: () => false,  // nothing to lose: measuring writes nothing
  activate() {
    measure.layer = measure.layer || L.layerGroup({ pane: TOOL_PANE }).addTo(map);
    measure.points = [];
    map.on("click", onMeasureClick);
    renderMeasurePanel();
  },
  deactivate() {
    map.off("click", onMeasureClick);
    measure.points = [];
    measure.layer?.clearLayers();
    if (shape.panel) { shape.panel.hidden = true; shape.panel.innerHTML = ""; }
  },
};

const selectTool = {
  id: "select",
  label: "Select",
  key: "v",
  // Deliberately empty: select *is* the app's existing behaviour, so it adds
  // and removes nothing. Its whole job is to be the state in which no tool has
  // claimed the click.
  activate() {},
  deactivate() {},
};

// ----------------------------------------------------------------- wiring

whenMap((instance) => {
  map = instance;
  if (!map.getPane(TOOL_PANE)) {
    // Above the overlay/marker panes (400/600) so handles sit on top of the
    // shapes they belong to, below popups (700).
    map.createPane(TOOL_PANE).style.zIndex = 650;
  }

  const palette = document.getElementById("map-tools");
  if (!palette) return;
  container = palette;
  // Without this a click on a button also reaches the map underneath and drops
  // a vertex behind the palette.
  L.DomEvent.disableClickPropagation(palette);
  L.DomEvent.disableScrollPropagation(palette);

  shape.panel = document.getElementById("map-tool-panel");
  if (shape.panel) {
    L.DomEvent.disableClickPropagation(shape.panel);
    shape.panel.addEventListener("click", (event) => {
      const action = event.target.closest("[data-commit]")?.dataset.commit;
      if (action) commit(action);
    });
  }

  registerTool(selectTool);
  registerTool(waypointTool);
  registerTool(measureTool);
  loadSprites().then(renderPalette);

  palette.addEventListener("click", (event) => {
    const id = event.target.closest("[data-tool]")?.dataset.tool;
    if (id) setTool(id);
  });

  document.addEventListener("keydown", (event) => {
    // Never steal a keystroke from a form field - the incident panel is full of
    // text inputs, and "w" is a common letter.
    const tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || event.target.isContentEditable) return;
    if (event.key === "Escape") { setTool("select", { force: true }); return; }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const match = [...tools.values()].find((tool) => tool.key === event.key.toLowerCase());
    if (match) { event.preventDefault(); setTool(match.id); }
  });

  document.body.classList.add("nf-tool-select");
});
