/* Where the analytical map meets the operational record.

   app.js knows about detections, events and models. operations.js knows about
   incidents, scenarios and resources. Neither imports the other, and that
   separation is worth keeping - so the actions that *join* them live here, in
   a third module that depends on both only through context.js's registries.

   Everything this module adds is a right-click item:

     Create incident here      - turns an observation into a fully-formed
                                 incident (record, first period, first
                                 scenario, AOI, snapshot link) in one request
     Attach to incident ⟨name⟩ - links what was right-clicked to the incident
                                 already being worked
     Situation here            - asks every module what it knows about a point

   The first of those replaces roughly five minutes of an incident commander's
   first ten, at the moment they have least attention to spare. */

import {
  onMapContextMenu, getActiveIncident, whenMap, fetchJson, emitIncidentCreated,
} from "./context.js";

/** Escapes a value for interpolation into innerHTML. Place names and incident
 * names are operator- and geocoder-supplied, so they are untrusted at the
 * point they become markup.
 * @param {*} value - Value to escape; nullish becomes an empty string.
 * @returns {string} Escaped text. */
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** POSTs JSON and throws with the server's own detail on failure.
 * Deliberately not context.js's fetchJson, which is GET-only: this is the
 * write half, and it needs the CSRF header a LAN-mode session requires.
 * @param {string} url - Request URL.
 * @param {object} body - JSON body.
 * @returns {Promise<object>} Parsed JSON payload. */
async function postJson(url, body) {
  const headers = { "Content-Type": "application/json" };
  // In LAN mode a state-changing request needs the CSRF token from the current
  // session. Read it from /api/auth/session rather than duplicating
  // operations.js's login flow or reaching into its module state: this module
  // deliberately depends on the other two only through context.js.
  // On loopback (auth disabled) the endpoint reports enabled:false and there is
  // no token to send, which is the normal single-user case.
  try {
    const session = await fetchJson("/api/auth/session");
    if (session.csrf) headers["X-CSRF-Token"] = session.csrf;
  } catch {
    /* no session yet - let the request itself surface the 401 */
  }
  const response = await fetch(url, { method: "POST", headers, body: JSON.stringify(body) });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
  return payload;
}

/** Shows a transient confirmation on the map. Deliberately a Leaflet popup
 * rather than a toast: the operator's attention is already at the point they
 * right-clicked, and that is where the answer should appear.
 * @param {L.Map} map - The Leaflet map.
 * @param {L.LatLng|Array<number>} at - Where to anchor.
 * @param {string} html - Popup markup. */
function flash(map, at, html) {
  L.popup({ closeButton: true, autoClose: true })
    .setLatLng(at)
    .setContent(html)
    .openOn(map);
}

/** Creates an incident from whatever was right-clicked and reports the result.
 * @param {L.Map} map - The Leaflet map.
 * @param {object} body - from_context request body.
 * @param {L.LatLng|Array<number>} at - Where to anchor the confirmation.
 * @returns {Promise<void>} */
async function createIncident(map, body, at) {
  try {
    const created = await postJson("/api/operations/incidents/from_context", body);
    const name = created.incident?.name || "incident";
    // Tell the workspace an incident now exists. Without this the record was
    // real server-side but absent from the operations panel's list until a full
    // page reload - the operator's next click after "created!" found nothing.
    if (created.incident) emitIncidentCreated(created.incident);
    flash(map, at,
      `<strong>${escapeHtml(name)}</strong><br>` +
      `Incident created with an operational period, a first scenario` +
      `${created.aoi ? ", an area of interest" : ""}` +
      `${created.links?.length ? " and a link to what you selected" : ""}.<br>` +
      `<em>Open NexIncidentCommand to continue.</em>`);
  } catch (error) {
    flash(map, at, `<strong>Could not create incident</strong><br>${escapeHtml(error.message)}`);
  }
}

/** Renders the aggregated situation report for a point.
 * @param {L.Map} map - The Leaflet map.
 * @param {L.LatLng} latlng - The point asked about.
 * @returns {Promise<void>} */
async function showSituation(map, latlng) {
  flash(map, latlng, "<em>Gathering situation…</em>");
  let report;
  try {
    report = await fetchJson(
      `/api/situation?lat=${latlng.lat.toFixed(5)}&lon=${latlng.lng.toFixed(5)}`);
  } catch (error) {
    flash(map, latlng, `<strong>Situation unavailable</strong><br>${escapeHtml(error.message)}`);
    return;
  }
  const rows = (report.sections || [])
    .filter((section) => section.summary)
    .map((section) =>
      `<dt>${escapeHtml(section.label)}</dt><dd>${escapeHtml(section.summary)}</dd>`)
    .join("");
  // A provider that is unavailable is named rather than silently omitted: "we
  // did not ask" and "there is nothing there" are very different answers when
  // someone is deciding whether to send a crew.
  const missing = (report.unavailable || []).length
    ? `<p class="situation-missing">Not consulted: ${escapeHtml(report.unavailable.join(", "))}</p>`
    : "";
  flash(map, latlng,
    `<div class="situation-popup"><strong>Situation at ` +
    `${latlng.lat.toFixed(4)}, ${latlng.lng.toFixed(4)}</strong>` +
    (rows ? `<dl>${rows}</dl>` : "<p>Nothing known at this location.</p>") +
    `${missing}</div>`);
}

whenMap((map) => {
  onMapContextMenu((detail) => {
    const incident = getActiveIncident();

    if (detail.kind === "point" && detail.latlng) {
      detail.addItem({
        label: "Situation here",
        action: () => showSituation(map, detail.latlng),
      });
      detail.addItem({
        label: "Create incident here",
        action: () => createIncident(map,
          { lat: detail.latlng.lat, lon: detail.latlng.lng, radius_km: 5 }, detail.latlng),
      });
    }

    if (detail.kind === "area" && detail.bounds) {
      const bounds = detail.bounds;
      const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()]
        .map((value) => value.toFixed(5)).join(",");
      detail.addItem({
        label: "Create incident for this area",
        action: () => createIncident(map, { bbox }, bounds.getCenter()),
      });
    }

    // Attaching only makes sense once something is being worked, so this item
    // appears exactly when it is actionable rather than always being present
    // and sometimes failing.
    if (incident && detail.kind === "event" && detail.eventId != null) {
      detail.addItem({
        label: `Attach event to ${incident.name}`,
        action: async () => {
          try {
            await postJson(`/api/operations/incidents/${incident.id}/links`, {
              kind: "event",
              ref_id: String(detail.eventId),
              snapshot: detail.snapshot || { event_id: detail.eventId },
              note: "attached from map",
            });
            flash(map, detail.latlng, `<strong>Attached</strong><br>Linked to ${escapeHtml(incident.name)}.`);
          } catch (error) {
            flash(map, detail.latlng, `<strong>Could not attach</strong><br>${escapeHtml(error.message)}`);
          }
        },
      });
    }

    if (detail.kind === "event" && detail.eventId != null) {
      detail.addItem({
        label: "Create incident from this event",
        action: () => createIncident(map, { event_id: detail.eventId }, detail.latlng),
      });
    }
  });
});
