/* Offline app-shell and last-read fallback for clients on the incident LAN.
   Writes are never queued or fabricated; the command server is authoritative. */
const SHELL_CACHE = "nexfiremap-shell-v13";
const READ_CACHE = "nexfiremap-reads-v13";
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
    try {
      const response = await fetch(request);
      if (response.ok && (url.pathname.startsWith("/api/") || SHELL.includes(url.pathname))) {
        const cache = await caches.open(url.pathname.startsWith("/api/") ? READ_CACHE : SHELL_CACHE);
        cache.put(request, response.clone());
      }
      return response;
    } catch (_) {
      const cached = await caches.match(request);
      if (cached) return cached;
      return new Response(JSON.stringify({detail: "Local command server unavailable and no cached response exists."}), {
        status: 503, headers: {"Content-Type": "application/json"}
      });
    }
  })());
});
