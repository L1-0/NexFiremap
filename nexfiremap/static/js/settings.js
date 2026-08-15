/* The global settings pane.

   Reached from the tool palette, but registered as a one-shot *action* rather
   than a mode (see `registerTool`): settings has no business owning the map's
   left click, and a "settings tool" that opened a dialog and handed the click
   back would leave the map in a state whose behaviour depends on a dialog that
   is no longer visible.

   Three groups, in the order an operator needs them:

   1. **API keys.** The pane never receives a key - the server returns only
      whether one is configured and a four-character tail (`settings_store
      .public_view`). So the input is always empty and always means "replace
      it"; leaving it blank changes nothing. That is why each key shows its
      current state as text beside the field instead of as a value inside it.
   2. **Coordinate framework.** The one setting that is genuinely *shared*: a
      room where each screen formats coordinates differently is a readback
      error waiting to happen. It arrives via `/api/config` for every client,
      not via this administrator-only endpoint.
   3. **Everything else** - symbology profile, CAP feeds, cache and staleness
      windows.

   The pane is honest about two things the server tells it. Settings that only
   take effect on restart say so *after* a save, naming exactly which ones; and
   a non-administrator gets a plain explanation rather than an empty form that
   fails on submit. */

import { registerTool } from "./tools.js";
import { SYSTEMS, currentSystem, setSystem } from "./coords.js";

/** Human-readable labels and help for each server-side setting. Kept here
 * rather than served from the API because it is presentation, not
 * configuration - and because a settings pane that cannot label its own fields
 * without a round trip is worse offline than one that can. */
const FIELD_LABELS = {
  map_key: ["NASA FIRMS map key", "Free from firms.modaps.eosdis.nasa.gov. Without it the map works, but shows no fire detections."],
  eumetsat_consumer_key: ["EUMETSAT consumer key", "For Meteosat imagery. Optional."],
  eumetsat_consumer_secret: ["EUMETSAT consumer secret", "The paired secret for the key above."],
  symbology_profile: ["Tactical symbol set", "Which symbol standard the whole room reads."],
  cap_feeds: ["CAP warning feeds", "One URL per line. Official public warnings (MoWaS/NINA, DWD, IPAWS)."],
  cache_days: ["Detection cache (days)", "How much detection history to keep on disk."],
  refresh_interval_minutes: ["Refresh interval (minutes)", "How often to poll FIRMS for new detections."],
  observation_stale_hours: ["Observation stale after (hours)", "When a field observation stops counting as current."],
  position_stale_seconds: ["Position stale after (seconds)", "When a unit's last position stops being trusted."],
  tile_contact: ["Tile contact address", "Sent as the User-Agent contact to tile providers, as their usage policies ask."],
};

/** Fields rendered as a multi-line list rather than a single input. */
const MULTILINE = new Set(["cap_feeds"]);

let overlay = null;
let dialog = null;
/** The payload the dialog currently shows. Read by `collect` to decide what
 * actually changed, so it has to track re-renders: comparing a second save
 * against the values from *before* the first one would resend fields the
 * operator never touched again. @type {?object} */
let currentView = null;

/** @param {string} value @returns {string} HTML-escaped text. */
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
}

/** GET/PUT helper carrying the CSRF token, mirroring tools.js's writeJson.
 * @param {string} url @param {?object} body @param {string} method
 * @returns {Promise<object>} */
async function request(url, body = null, method = "GET") {
  const headers = {};
  if (body) {
    headers["Content-Type"] = "application/json";
    try {
      const session = await fetch("/api/auth/session").then((r) => r.json());
      if (session.csrf) headers["X-CSRF-Token"] = session.csrf;
    } catch {
      /* loopback single-user mode has no session; the request 401s if it matters */
    }
  }
  const response = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

/** Renders one server-side setting.
 * @param {string} name @param {object} entry @param {object} view
 * @returns {string} HTML for one field row. */
function renderField(name, entry, view) {
  const [label, help] = FIELD_LABELS[name] || [name, ""];
  const id = `nf-set-${name}`;
  let control;

  if (entry.configured !== undefined) {
    // A credential. The box is always empty - the server never sends the value
    // - so its placeholder has to carry the meaning instead: type here to
    // replace, leave blank to keep. `autocomplete="off"` and type=password stop
    // a browser password manager from offering to save an API key as a login.
    const state = entry.configured
      ? `Configured (${escapeHtml(entry.hint)}) from ${escapeHtml(entry.source)}`
      : "Not configured";
    control =
      `<input type="password" id="${id}" data-setting="${name}" autocomplete="off"
         placeholder="${entry.configured ? "Leave blank to keep the current key" : "Paste the key"}">
       <span class="nf-set-state${entry.configured ? " is-set" : ""}">${state}</span>` +
      (entry.configured && entry.source === "database"
        ? `<button type="button" class="nf-panel-btn ghost" data-clear="${name}">Remove</button>` : "");
  } else if (name === "symbology_profile") {
    const options = (view.symbology_profiles || []).map((profile) =>
      `<option value="${escapeHtml(profile.id)}"${profile.id === entry.value ? " selected" : ""}>${
        escapeHtml(profile.label)}</option>`).join("");
    control = `<select id="${id}" data-setting="${name}">${options}</select>`;
  } else if (MULTILINE.has(name)) {
    const text = Array.isArray(entry.value) ? entry.value.join("\n") : String(entry.value ?? "");
    control = `<textarea id="${id}" data-setting="${name}" rows="3">${escapeHtml(text)}</textarea>`;
  } else {
    const numeric = typeof entry.value === "number";
    control = `<input type="${numeric ? "number" : "text"}" id="${id}" data-setting="${name}"
      value="${escapeHtml(entry.value)}">`;
  }

  return `<div class="nf-set-field">
    <label for="${id}">${escapeHtml(label)}${
      entry.restart_required ? '<span class="nf-set-restart" title="Takes effect after a restart">restart</span>' : ""}</label>
    ${control}
    ${help ? `<p class="nf-set-help">${escapeHtml(help)}</p>` : ""}
  </div>`;
}

/** Rebuilds the dialog body from a settings payload. @param {object} view */
function render(view) {
  currentView = view;
  const group = (names) => names
    .filter((name) => view.fields[name])
    .map((name) => renderField(name, view.fields[name], view)).join("");

  // This control edits the *shared* setting, so it shows the shared value when
  // there is one - not this browser's local choice. Otherwise an operator who
  // had locally deviated from the shared format would silently push their own
  // preference onto everyone the next time they saved anything on this page.
  const shared = view.client?.coordinate_system || currentSystem();
  const systems = SYSTEMS.map((system) =>
    `<option value="${escapeHtml(system.id)}"${system.id === shared ? " selected" : ""}>${
      escapeHtml(system.label)}</option>`).join("");

  dialog.innerHTML = `
    <header class="nf-set-header">
      <h2>Settings</h2>
      <button type="button" class="nf-set-close" data-close aria-label="Close settings">×</button>
    </header>
    <div class="nf-set-body">
      <section>
        <h3>API keys</h3>
        <p class="nf-set-note">Keys are stored on the server and never sent back to this page -
          only whether one is set, and its last four characters.</p>
        ${group(["map_key", "eumetsat_consumer_key", "eumetsat_consumer_secret"])}
      </section>
      <section>
        <h3>Coordinate framework</h3>
        <p class="nf-set-note">Applies to every screen in this incident. Changing it here moves
          everyone onto the same format.</p>
        <div class="nf-set-field">
          <label for="nf-set-coords">Coordinate system</label>
          <select id="nf-set-coords" data-client="coordinate_system">${systems}</select>
          <p class="nf-set-help">How coordinates are shown and what the search box assumes first.</p>
        </div>
        <div class="nf-set-field">
          <label for="nf-set-proj4">Custom projection</label>
          <input type="text" id="nf-set-proj4" data-client="custom_proj4"
            value="${escapeHtml(view.client?.custom_proj4 || "")}"
            placeholder="EPSG:25832 or a full proj4 string">
          <p class="nf-set-help">Only used when the system above is set to Custom - without a
            definition here, that option cannot format anything.</p>
        </div>
        ${group(["symbology_profile"])}
      </section>
      <section>
        <h3>Data sources</h3>
        ${group(["cap_feeds", "refresh_interval_minutes", "cache_days",
                 "observation_stale_hours", "position_stale_seconds", "tile_contact"])}
      </section>
    </div>
    <footer class="nf-set-footer">
      <p class="nf-set-status" data-status></p>
      <button type="button" class="nf-panel-btn ghost" data-close>Close</button>
      <button type="button" class="nf-panel-btn" data-save>Save</button>
    </footer>`;
}

/** Collects only the fields the operator actually changed.
 *
 * "Only changed" matters for more than tidiness: an empty credential box means
 * "keep the current key", and submitting it as an empty string would clear a
 * working key every time anyone saved anything else on the page.
 * @returns {object} Name/value pairs to send. */
function collect() {
  const view = currentView;
  const values = {};
  dialog.querySelectorAll("[data-setting]").forEach((input) => {
    const name = input.dataset.setting;
    const entry = view.fields[name];
    const raw = input.value;
    if (entry.configured !== undefined) {
      if (raw.trim()) values[name] = raw.trim();
      return;
    }
    if (MULTILINE.has(name)) {
      const list = raw.split("\n").map((line) => line.trim()).filter(Boolean);
      if (JSON.stringify(list) !== JSON.stringify(entry.value || [])) values[name] = list;
      return;
    }
    if (typeof entry.value === "number") {
      if (Number(raw) !== entry.value) values[name] = Number(raw);
    } else if (raw !== String(entry.value ?? "")) {
      values[name] = raw;
    }
  });

  // Client preferences are collected generically rather than field by field,
  // so adding one to the markup cannot leave it silently unsaved.
  dialog.querySelectorAll("[data-client]").forEach((input) => {
    const name = input.dataset.client;
    // The coordinate select is rendered against the shared value where there
    // is one, falling back to this browser's - so compare against the same
    // thing render() used, or an unchanged control would look changed.
    const shown = view.client?.[name]
      || (name === "coordinate_system" ? currentSystem() : "");
    if (input.value !== shown) values[name] = input.value;
  });
  return values;
}

/** @param {string} message @param {boolean} [isError] */
function status(message, isError = false) {
  const element = dialog?.querySelector("[data-status]");
  if (!element) return;
  element.textContent = message;
  element.classList.toggle("is-error", isError);
}

async function save() {
  if (!currentView) return;  // the pane failed to load; there is nothing to save
  const values = collect();
  if (!Object.keys(values).length) { status("Nothing changed."); return; }

  // The coordinate system is both a stored shared preference and this browser's
  // own immediate setting, so apply it locally rather than waiting for a reload.
  if (values.coordinate_system) setSystem(values.coordinate_system);

  status("Saving…");
  try {
    const saved = await request("/api/settings", { values }, "PUT");
    // Re-render *before* writing the status line: render() replaces the dialog's
    // whole innerHTML, including the element status() writes into, so the other
    // order silently discards the confirmation - and a save that reports nothing
    // is indistinguishable from one that did not happen.
    render(saved);
    const restart = saved.restart_needed_for || [];
    status(restart.length
      ? `Saved. ${restart.map((name) => (FIELD_LABELS[name] || [name])[0]).join(", ")} ` +
        `${restart.length === 1 ? "takes" : "take"} effect after restarting the server.`
      : "Saved.");
  } catch (error) {
    status(error.message, true);
  }
}

/** Opens the pane, fetching current settings each time so two operators editing
 * in parallel do not work from a stale copy. */
export async function openSettings() {
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "nf-set-overlay";
    overlay.hidden = true;
    dialog = document.createElement("div");
    dialog.className = "nf-set-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-label", "Settings");
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    // A click on the backdrop closes; a click inside must not bubble out to it.
    overlay.addEventListener("click", (event) => { if (event.target === overlay) close(); });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !overlay.hidden) { event.stopPropagation(); close(); }
    }, true);
    // Delegated, and bound exactly once for the dialog's lifetime. Binding it
    // per open would stack a fresh handler on every visit to the pane, so the
    // third open would fire three saves - and the dialog element itself
    // survives every close, so nothing would ever clean them up.
    dialog.addEventListener("click", async (event) => {
      if (event.target.closest("[data-close]")) { close(); return; }
      if (event.target.closest("[data-save]")) { await save(); return; }
      const clear = event.target.closest("[data-clear]")?.dataset.clear;
      if (clear) {
        status("Removing…");
        try {
          render(await request(`/api/settings/${encodeURIComponent(clear)}`, null, "DELETE"));
          status("Removed. Restart the server for it to take effect.");
        } catch (error) {
          status(error.message, true);
        }
      }
    });
  }

  overlay.hidden = false;
  dialog.innerHTML = '<p class="nf-set-note">Loading settings…</p>';
  let view;
  try {
    view = await request("/api/settings");
  } catch (error) {
    // 401/403 is the ordinary case for a non-administrator, not a fault, so it
    // gets an explanation rather than an error style.
    const denied = error.status === 401 || error.status === 403;
    dialog.innerHTML = `<header class="nf-set-header"><h2>Settings</h2>
        <button type="button" class="nf-set-close" data-close aria-label="Close settings">×</button></header>
      <div class="nf-set-body"><p class="nf-set-note${denied ? "" : " is-error"}">${
        denied ? "Settings are administrator-only. Sign in as an administrator to change API keys or shared preferences."
               : escapeHtml(error.message)}</p></div>`;
    // The delegated handler bound at creation already closes on [data-close],
    // so this branch needs no listener of its own.
    currentView = null;
    return;
  }

  render(view);
}

function close() {
  if (overlay) overlay.hidden = true;
}

registerTool({
  id: "settings",
  label: "Settings",
  key: ",",
  action: openSettings,
});
