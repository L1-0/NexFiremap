/* Shared runtime context for the NexFiremap frontend modules.

   This file is the single place that describes how app.js, operations.js and
   structures.js reach each other. Everything here used to be a `window.NexXxx`
   global plus a `nexfiremap:*` CustomEvent on `window`, which made the
   <script> order in index.html load-bearing: operations.js could only see
   app.js's map because app.js's tag came first, and each consumer had to
   hand-write the same "already set? read the global : otherwise wait for the
   event" dance. Those are now real ES module imports, so the dependency is
   declared rather than implied, and the browser resolves the order itself.

   Two things deliberately survive as *registries* rather than becoming direct
   imports, because the underlying relationship is genuinely late-bound and a
   static import would misrepresent it:

   - the map, which does not exist at module-evaluation time (app.js has to
     await /api/config before it can build one), and
   - the print view and context-menu contributions, which are optional
     capabilities operations.js registers during its own async init() and
     which app.js must keep working without.

   Every subscribe function below is safe to call before *or* after the value
   it waits on has been published; that is the property the old duplicated
   "if (global) ... else addEventListener(..., {once:true})" idiom was
   hand-rolling at each call site, and getting subtly different each time. */

// ------------------------------------------------------------------ the map

/** @type {?L.Map} */
let mapInstance = null;
/** @type {Array<function(L.Map): void>} */
let mapWaiters = [];

/** Publishes the Leaflet map once app.js has built it, running every
 * whenMap() callback registered so far. Called exactly once, from app.js's
 * init().
 * @param {L.Map} map - The Leaflet map instance. */
export function setMap(map) {
  mapInstance = map;
  // Snapshot and clear first: a waiter is allowed to register another
  // waiter, and each must still run exactly once.
  const waiting = mapWaiters;
  mapWaiters = [];
  waiting.forEach((callback) => callback(map));
}

/** The Leaflet map, or null before app.js's init() has built it.
 * @returns {?L.Map} */
export function getMap() {
  return mapInstance;
}

/** Runs `callback` with the Leaflet map, immediately if it already exists and
 * otherwise as soon as app.js publishes it. Fires at most once per call -
 * this replaces the `window.NexFiremapMap ? init(...) : addEventListener(
 * "nexfiremap:ready", ..., {once:true})` pattern that operations.js and
 * structures.js each carried their own copy of.
 * @param {function(L.Map): void} callback */
export function whenMap(callback) {
  if (mapInstance) callback(mapInstance);
  else mapWaiters.push(callback);
}

// ------------------------------------------------- current spread analysis

/** @type {?object} */
let spreadAnalysis = null;
/** @type {Array<function(?object): void>} */
const spreadSubscribers = [];

/** Publishes the currently-rendered spread/ensemble analysis (or null when
 * cleared) to every module that shares the map. Replaces the
 * window.NexFiremapSpreadAnalysis global + "nexfiremap:spread-analysis"
 * CustomEvent pair, which had to be kept in sync by hand.
 * @param {?object} detail - Analysis detail (with jobId), or null to clear. */
export function setSpreadAnalysis(detail) {
  spreadAnalysis = detail;
  spreadSubscribers.forEach((callback) => callback(detail));
}

/** The analysis currently on the map, or null. Lets a module that starts up
 * after an analysis was already published catch up on the current value
 * instead of waiting for the next change.
 * @returns {?object} */
export function getSpreadAnalysis() {
  return spreadAnalysis;
}

/** Subscribes to spread/ensemble analysis changes. Does not replay the
 * current value - pair it with getSpreadAnalysis() when the subscriber needs
 * to catch up, exactly as structures.js does.
 * @param {function(?object): void} callback */
export function onSpreadAnalysis(callback) {
  spreadSubscribers.push(callback);
}

// ------------------------------------------------------------- print view

/** @type {?function(): void} */
let printView = null;

/** Registers the incident-aware print view. operations.js calls this from its
 * init(); until then app.js's topbar print button falls back to a bare
 * window.print(), so the button never silently does nothing.
 * @param {function(): void} render */
export function setPrintView(render) {
  printView = render;
}

/** The registered incident-aware print view, or null if operations.js has not
 * initialised (or never will, e.g. a public-role session).
 * @returns {?function(): void} */
export function getPrintView() {
  return printView;
}

// ------------------------------------------------- map context-menu items

/** @type {Array<function(object): void>} */
const contextMenuContributors = [];

/** Subscribes to map right-click menus so a module can contribute its own
 * items (operations.js adds "Add <tactical type> here") without app.js
 * needing any incident-command knowledge.
 * @param {function({kind: string, latlng?: L.LatLng, bounds?: L.LatLngBounds,
 *   addItem: function(object): void}): void} callback */
export function onMapContextMenu(callback) {
  contextMenuContributors.push(callback);
}

/** Invokes every contributor synchronously, before app.js renders the menu -
 * `detail.addItem` pushes into the item array in place, so contributions must
 * be made during this call and not asynchronously afterwards.
 * @param {object} detail - {kind, latlng|bounds, addItem}. */
export function emitMapContextMenu(detail) {
  contextMenuContributors.forEach((callback) => callback(detail));
}

// ------------------------------------------------- active incident context

/* The incident the operator is currently working, published by operations.js
   and read by anything analytical that wants to offer "…to incident ⟨name⟩".

   This is the piece that lets the two halves of the application talk without
   depending on each other: app.js knows about detections and events and
   nothing about incidents; operations.js is the reverse. Routing the active
   incident through here means a right-click on a fire event can offer "attach
   to Waldbrand Nord" without either module importing the other.

   Same publish/subscribe/late-subscriber shape as setSpreadAnalysis above,
   deliberately - one idiom for shared state in this app, not three. */

/** @type {?{id: string, name: string}} */
let activeIncident = null;
/** @type {Array<function(?object): void>} */
const incidentSubscribers = [];

/** Publishes the incident the operator is working on (or null when cleared).
 * @param {?{id: string, name: string}} incident */
export function setActiveIncident(incident) {
  activeIncident = incident;
  incidentSubscribers.forEach((callback) => callback(incident));
}

/** The active incident, or null if none is selected.
 * @returns {?{id: string, name: string}} */
export function getActiveIncident() {
  return activeIncident;
}

/** Subscribes to active-incident changes, firing immediately with the current
 * value so a late subscriber is never left waiting for a change that already
 * happened - the property every subscribe function in this file guarantees.
 * @param {function(?object): void} callback */
export function onActiveIncident(callback) {
  incidentSubscribers.push(callback);
  callback(activeIncident);
}

/* Incidents created outside the operations workspace.

   `setActiveIncident` answers "which incident is being worked", which is not
   the same question as "the set of incidents changed". Creating one from the
   map's right-click menu wrote a real incident server-side that the operations
   panel's own list knew nothing about, so it stayed invisible until a full page
   reload - the two halves shared a selection but not a creation.

   A separate channel rather than overloading the active-incident one, because
   the subscriber's job is different: reload the list, then select. This is
   notification, not state, so there is no getter and no late-subscriber replay -
   an incident created before a subscriber existed is already in the list that
   subscriber loads on startup. */

/** @type {Array<function(object): void>} */
const incidentCreatedSubscribers = [];

/** Announces an incident created outside the workspace that owns the list.
 * @param {{id: string, name: string}} incident */
export function emitIncidentCreated(incident) {
  if (!incident) return;
  incidentCreatedSubscribers.forEach((callback) => {
    try {
      callback(incident);
    } catch (error) {
      // One bad subscriber must not stop the others from refreshing.
      console.warn("incident-created subscriber failed", error);
    }
  });
}

/** Subscribes to incidents created elsewhere.
 * @param {function(object): void} callback */
export function onIncidentCreated(callback) {
  incidentCreatedSubscribers.push(callback);
}

// ----------------------------------------------------------- active tool

/* Which map tool currently owns the left click.

   Left-click in this app was subscribed by three modules at once: operations.js
   binds `map.on("click", drawClick)` while sketching, markers and features have
   their own popups, and app.js has handlers of its own. Nothing made that
   visible or mutually exclusive, so "what does clicking do right now?" was an
   implicit, unanswerable question.

   The tool palette (tools.js) makes it explicit: exactly one tool is active,
   and it owns the click. `"select"` is the default and reproduces the previous
   behaviour exactly, so anyone who ignores the toolbar notices no change.

   Same publish/subscribe/late-subscriber shape as the registries above - one
   idiom for shared state in this app, not four. */

/** @type {string} */
let activeTool = "select";
/** @type {Array<function(string): void>} */
const toolSubscribers = [];

/** Publishes the active tool. Called only by tools.js, which is also the only
 * place a tool's deactivate() runs - centralising that is what stops a tool
 * failing to release the click and leaving the map feeling broken.
 * @param {string} tool - Tool id, e.g. "select" / "waypoint". */
export function setActiveTool(tool) {
  activeTool = tool || "select";
  toolSubscribers.forEach((callback) => callback(activeTool));
}

/** The active tool id; "select" when the palette has not loaded at all, so a
 * consumer never has to special-case its absence.
 * @returns {string} */
export function getActiveTool() {
  return activeTool;
}

/** Subscribes to tool changes, firing immediately with the current value.
 * @param {function(string): void} callback */
export function onActiveTool(callback) {
  toolSubscribers.push(callback);
  callback(activeTool);
}

// ------------------------------------------------------------ fetch helper

/** Fetches JSON from the local command server and throws with the server's
 * own error detail (falling back to the HTTP status) on a non-OK response.
 *
 * This is the read-only helper, moved here from structures.js so any module
 * can use it. It is deliberately *not* operations.js's api(): that one also
 * attaches the operator identity and CSRF header from incident-command's
 * authenticated session state, which the plain GET endpoints app.js and
 * structures.js read do not send today.
 * @param {string} url - Request URL.
 * @returns {Promise<object>} Parsed JSON payload. */
export async function fetchJson(url) {
  const response = await fetch(url);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
  return payload;
}

// ------------------------------------------------- stale-replay surfacing

/** Header the service worker sets on a response it replayed from cache. */
const STALE_HEADER = "x-nexfiremap-stale";
const CACHED_AT_HEADER = "x-nexfiremap-cached-at";

/** Shows or clears the "showing cached data" banner.
 *
 * The service worker serves a cached API response when the command server is
 * unreachable. That is the right behaviour - a field client keeping the last
 * known picture beats a blank screen - but the replay used to be
 * indistinguishable from a live 200, so days-old detections and unit positions
 * rendered as current and every error path in the app was unreachable. The
 * banner is the difference between an offline-tolerant tool and one that lies.
 * @param {?string} cachedAt - ISO time the cached copy was fetched, if known. */
function showStale(cachedAt) {
  const banner = document.getElementById("stale-banner");
  if (!banner) return;
  if (cachedAt === null) {
    banner.hidden = true;
    return;
  }
  const when = cachedAt ? new Date(cachedAt) : null;
  const time = when && !Number.isNaN(when.getTime())
    ? when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;
  banner.textContent = time
    ? `Command server unreachable - showing cached data from ${time}`
    : "Command server unreachable - showing cached data";
  banner.hidden = false;
}

// Installed once, at module scope, wrapping fetch itself rather than only
// fetchJson: most call sites in app.js use fetch directly, and a marker that
// only some of them honoured would be worse than none - it would make the
// banner's absence meaningless.
if (typeof window !== "undefined" && !window.__nexStaleWatch) {
  window.__nexStaleWatch = true;
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    try {
      const url = new URL(response.url || String(args[0]), window.location.origin);
      if (url.pathname.startsWith("/api/")) {
        showStale(response.headers.get(STALE_HEADER)
          ? response.headers.get(CACHED_AT_HEADER) || ""
          : null);
      }
    } catch (_) {
      /* never let the banner's bookkeeping break the request it observed */
    }
    return response;
  };
}
