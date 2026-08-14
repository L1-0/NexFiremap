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
