/* Offline app-shell and last-read fallback for clients on the incident LAN.
   Writes are never queued or fabricated; the command server is authoritative. */
const SHELL_CACHE = "nexfiremap-shell-v14";
const READ_CACHE = "nexfiremap-reads-v14";

/** Set on a response replayed from cache because the server was unreachable.
 *  The frontend reads it to say so on screen rather than presenting days-old
 *  detections as the current picture. */
const STALE = "x-nexfiremap-stale";
/** When the cached copy was actually fetched, so the banner can name a time. */
const CACHED_AT = "x-nexfiremap-cached-at";
/** Upper bound on cached API reads - see `trimReadCache`. */
const MAX_READ_ENTRIES = 300;
const SHELL = [
  "/", "/static/css/app.css", "/static/js/app.js", "/static/js/operations.js",
  "/static/js/structures.js", "/static/js/coords.js", "/static/js/context.js",
  "/static/vendor/leaflet/leaflet.css", "/static/vendor/leaflet/leaflet.js",
  "/static/vendor/markercluster/MarkerCluster.css",
  "/static/vendor/markercluster/MarkerCluster.Default.css",
  "/static/vendor/markercluster/leaflet.markercluster.js",
  "/static/vendor/heat/leaflet-heat.js", "/static/vendor/maplibre/maplibre-gl.css",
  "/static/vendor/maplibre/maplibre-gl.js", "/static/vendor/proj4/proj4.js",
  "/static/vendor/mgrs/mgrs.min.js", "/static/img/nexfiremap-64.png",
  "/static/img/nexfiremap-256.png", "/static/manifest.webmanifest"
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((key) => ![SHELL_CACHE, READ_CACHE].includes(key)).map((key) => caches.delete(key))
  )).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/tiles/")) return;
  event.respondWith((async () => {
    const isApi = url.pathname.startsWith("/api/");
    try {
      const response = await fetch(request);
      if (response.ok && (isApi || SHELL.includes(url.pathname))) {
        const cache = await caches.open(isApi ? READ_CACHE : SHELL_CACHE);
        // Stamp when this was actually fetched. Without it a replay can only
        // say "this is old", not "this is from 09:14" - and the second is what
        // lets an operator judge whether it still means anything.
        await cache.put(request, await stamp(response.clone(), CACHED_AT, new Date().toISOString()));
        if (isApi) trimReadCache();
      }
      return response;
    } catch (_) {
      const cached = await caches.match(request);
      if (cached) {
        // Mark the replay. It used to be returned byte-identical, status and
        // all, so a field client whose command server had died kept rendering
        // days-old detections and unit positions as current - and because the
        // fetch "succeeded", every error banner in the frontend was
        // unreachable. Silently serving stale data as live is the worst thing
        // an offline-first tool can do; serving it clearly labelled is the
        // whole point of one.
        return stamp(cached, STALE, "1");
      }
      return new Response(JSON.stringify({detail: "Local command server unavailable and no cached response exists."}), {
        status: 503, headers: {"Content-Type": "application/json"}
      });
    }
  })());
});

/** Returns a copy of `response` carrying one extra header.
 *
 * A Response's headers are immutable once constructed, so this rebuilds it
 * rather than mutating in place.
 * @param {Response} response @param {string} name @param {string} value
 * @returns {Promise<Response>} */
async function stamp(response, name, value) {
  const headers = new Headers(response.headers);
  headers.set(name, value);
  return new Response(await response.blob(), {
    status: response.status, statusText: response.statusText, headers,
  });
}

/** Bounds the read cache.
 *
 * Every distinct bbox querystring is its own cache entry, so a few minutes of
 * panning creates hundreds and nothing ever removed them - unbounded growth in
 * the browser's storage on a field device with little of it. Oldest-first,
 * which for this cache is insertion order.
 * @returns {Promise<void>} */
async function trimReadCache() {
  const cache = await caches.open(READ_CACHE);
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - MAX_READ_ENTRIES; i += 1) {
    await cache.delete(keys[i]);
  }
}
