/* NexIngest's live-feed console.

   Two jobs, both scoped to the "ingest" app mode: (1) show the status of
   every inbound live-data channel (CoT/TAK gateway, MQTT bridge, CAP warning
   feeds, CAD webhooks) so an operator can tell "no data" from "channel dead";
   (2) administer per-incident position feeds - create, enable/disable,
   rotate tokens - which until now existed only as curl-able endpoints.

   Deliberately a standalone ES module like alerts.js: it touches shared
   state only through context.js (the active incident that operations.js
   publishes) and its own fetches. It never renders the incident-command
   records - NexIngest is a feed console, not a command workspace; the tools
   here act on whatever incident is active and say so in #ingest-incident-line.

   Security note: feed administration is administrator-only server-side; this
   module reads /api/auth/session once for the CSRF token and role, mirrors
   operations.js's header convention, and surfaces 403s as plain text rather
   than hiding the controls (an operator should learn "ask an administrator",
   not "the feature does not exist"). */

import { onActiveIncident, getActiveIncident } from "./context.js";

const $ = (sel) => document.querySelector(sel);

let auth = { enabled: false, role: "administrator", csrf: "" };
let incident = null;
let feeds = [];

/* Same convention as operations.js's api(): CSRF header on writes, JSON
   body, X-Operator so the audit trail names who acted (the server ignores
   the header whenever a real authenticated identity exists - see
   routes/common._operator). */
async function api(url, options = {}) {
  const operator = $("#ops-operator")?.value?.trim() || "local operator";
  const headers = { "X-Operator": operator, ...(options.headers || {}) };
  const method = (options.method || "GET").toUpperCase();
  if (auth.csrf && !["GET", "HEAD", "OPTIONS"].includes(method)) headers["X-CSRF-Token"] = auth.csrf;
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options = { ...options, body: JSON.stringify(options.body) };
  }
  const response = await fetch(url, { ...options, headers });
  let payload = null;
  try { payload = await response.json(); } catch (_) { /* no body */ }
  if (!response.ok) {
    const error = new Error(payload?.detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

/* App-wide time convention (see app.js): labeled UTC, minute precision. */
const utc = (iso) => (iso ? new Date(iso).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "never");

const esc = (text) => {
  const div = document.createElement("div");
  div.textContent = String(text ?? "");
  return div.innerHTML;
};

/* One channel card: a name, an at-a-glance state pill, and the facts that
   matter for "is data flowing". `state` is "ok" | "off" | "err". */
function channelCard(name, state, stateLabel, rows, actionsHtml = "") {
  const facts = rows
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([label, value]) => `<div class="feed-fact"><span>${esc(label)}</span><span>${esc(value)}</span></div>`)
    .join("");
  return `<div class="feed-card">
    <div class="feed-card-head">
      <strong>${esc(name)}</strong>
      <span class="feed-state feed-state-${state}">${esc(stateLabel)}</span>
    </div>
    ${facts}${actionsHtml}
  </div>`;
}

/* ------------------------------------------------------------ channel status */

async function renderChannelStatus() {
  const grid = $("#feed-status-grid");
  if (!grid) return;
  grid.innerHTML = `<p class="hint">Checking channels&hellip;</p>`;

  /* Independent fetches, independently allowed to fail: one dead endpoint
     must not blank the whole console - the failed channel renders its own
     error card instead ("we could not ask" is itself status information). */
  const [cot, alerts, server, hooks] = await Promise.allSettled([
    api("/api/feeds/cot/status"),
    api("/api/alerts/status"),
    api("/api/status"),
    api("/api/webhooks"),
  ]);

  const cards = [];

  if (cot.status === "fulfilled") {
    const s = cot.value;
    cards.push(channelCard("CoT / TAK gateway",
      s.last_error ? "err" : s.listening ? "ok" : "off",
      s.last_error ? "error" : s.listening ? "listening" : "disabled",
      [["Ports", s.enabled ? `TCP ${s.tcp_port} / UDP ${s.udp_port} on ${s.host}` : null],
       ["Positions / features", s.enabled ? `${s.positions} / ${s.features}` : null],
       ["Received / rejected", s.enabled ? `${s.received} / ${s.rejected}` : null],
       ["Last received", s.enabled ? utc(s.last_received_at) : null],
       ["Last error", s.last_error]]));
  } else {
    cards.push(channelCard("CoT / TAK gateway", "err", "status unavailable", [["Error", cot.reason.message]]));
  }

  if (server.status === "fulfilled") {
    const m = server.value.mqtt || {};
    cards.push(channelCard("MQTT bridge",
      m.last_error ? "err" : m.connected ? "ok" : "off",
      m.last_error ? "error" : m.connected ? "connected" : m.enabled ? "connecting" : "disabled",
      [["Broker", m.enabled ? m.broker : null],
       ["Topics", m.enabled && m.topics?.length ? m.topics.join(", ") : null],
       ["Accepted / rejected", m.enabled ? `${m.accepted} / ${m.rejected}` : null],
       ["Unavailable", m.unavailable_reason],
       ["Last error", m.last_error]]));
  } else {
    cards.push(channelCard("MQTT bridge", "err", "status unavailable", [["Error", server.reason.message]]));
  }

  if (alerts.status === "fulfilled") {
    const a = alerts.value;
    cards.push(channelCard("CAP warning feeds",
      a.last_error ? "err" : a.enabled ? "ok" : "off",
      a.last_error ? "error" : a.enabled ? "polling" : "disabled",
      [["Feeds", a.feeds?.length ? a.feeds.join(", ") : null],
       ["Warnings stored", a.enabled ? a.stored : null],
       ["Last poll", a.enabled ? utc(a.last_poll_at) : null],
       ["Last error", a.last_error]],
      a.enabled ? `<div class="button-row"><button id="btn-alerts-poll" class="btn ghost" type="button">poll feeds now</button></div>` : ""));
  } else {
    cards.push(channelCard("CAP warning feeds", "err", "status unavailable", [["Error", alerts.reason.message]]));
  }

  if (hooks.status === "fulfilled") {
    const list = Array.isArray(hooks.value) ? hooks.value : hooks.value?.webhooks || [];
    cards.push(channelCard("CAD dispatch webhooks",
      list.length ? "ok" : "off",
      list.length ? `${list.length} registered` : "none registered",
      list.slice(0, 6).map((h) => [h.name || h.id, h.active === false ? "disabled" : "active"])));
  } else if (hooks.reason?.status === 403) {
    cards.push(channelCard("CAD dispatch webhooks", "off", "administrator only",
      [["Note", "Sign in as an administrator to view or manage webhooks."]]));
  } else {
    cards.push(channelCard("CAD dispatch webhooks", "err", "status unavailable", [["Error", hooks.reason.message]]));
  }

  grid.innerHTML = cards.join("");
  $("#btn-alerts-poll")?.addEventListener("click", async (event) => {
    event.target.disabled = true;
    try { await api("/api/alerts/refresh", { method: "POST" }); await renderChannelStatus(); }
    catch (error) { setAdminStatus(`Poll failed: ${error.message}`); event.target.disabled = false; }
  });
}

/* ---------------------------------------------------------- position feeds */

function setAdminStatus(text) {
  const el = $("#feed-admin-status");
  if (el) el.textContent = text;
}

/* The one place a token is ever shown - immediately after create/rotate,
   with the two URLs a device integrator actually needs. Deliberately left
   on screen until the next create/rotate: the operator is mid-setup. */
function revealToken(feed) {
  const reveal = $("#feed-token-reveal");
  if (!reveal) return;
  const base = `${window.location.origin}/api/feeds/positions/${feed.id}`;
  reveal.hidden = false;
  reveal.innerHTML = `
    <p class="hint"><strong>Token for &ldquo;${esc(feed.name)}&rdquo; &mdash; shown once, copy it now.</strong></p>
    <div class="feed-fact"><span>Token</span><span><code>${esc(feed.ingest_token)}</code></span></div>
    <div class="feed-fact"><span>POST endpoint</span><span><code>${esc(base)}</code> with header <code>X-Feed-Token</code></span></div>
    <div class="feed-fact"><span>OsmAnd URL</span><span><code>${esc(`${base}/osmand?token=${feed.ingest_token}&lat={0}&lon={1}&timestamp={2}&speed={5}&bearing={6}`)}</code></span></div>
    <div class="button-row"><button id="btn-token-copy" class="btn ghost" type="button">copy token</button></div>`;
  $("#btn-token-copy")?.addEventListener("click", () =>
    navigator.clipboard?.writeText(feed.ingest_token).then(
      () => setAdminStatus("Token copied to clipboard."),
      () => setAdminStatus("Copy failed - select the token text manually.")));
}

async function renderFeeds() {
  const list = $("#position-feed-list");
  if (!list) return;
  if (!incident) {
    list.innerHTML = `<p class="hint">Select an incident in NexIncidentCommand first.</p>`;
    return;
  }
  try {
    feeds = await api(`/api/operations/incidents/${incident.id}/position-feeds`);
  } catch (error) {
    list.innerHTML = `<p class="hint">Could not load feeds: ${esc(error.message)}</p>`;
    return;
  }
  if (!feeds.length) {
    list.innerHTML = `<p class="hint">No position feeds for this incident yet.</p>`;
    return;
  }
  list.innerHTML = feeds.map((f) => `<div class="feed-card" data-feed-id="${esc(f.id)}">
      <div class="feed-card-head">
        <strong>${esc(f.name)}</strong>
        <span class="feed-state feed-state-${f.active === false ? "off" : "ok"}">${f.active === false ? "disabled" : "active"}</span>
      </div>
      <div class="feed-fact"><span>Kind</span><span>${esc(f.device_kind)}${f.provider ? " &middot; " + esc(f.provider) : ""}</span></div>
      <div class="feed-fact"><span>Updated</span><span>${esc(utc(f.updated_at))}</span></div>
      <div class="button-row">
        <button class="btn ghost" type="button" data-feed-toggle>${f.active === false ? "enable" : "disable"}</button>
        <button class="btn ghost" type="button" data-feed-rotate>rotate token</button>
      </div>
    </div>`).join("");

  list.querySelectorAll("[data-feed-toggle]").forEach((btn) => btn.addEventListener("click", () => toggleFeed(btn)));
  list.querySelectorAll("[data-feed-rotate]").forEach((btn) => btn.addEventListener("click", () => rotateFeed(btn)));
}

function feedFor(btn) {
  const id = btn.closest("[data-feed-id]")?.dataset.feedId;
  return feeds.find((f) => f.id === id);
}

async function toggleFeed(btn) {
  const feed = feedFor(btn);
  if (!feed) return;
  try {
    await api(`/api/operations/incidents/${incident.id}/position-feeds/${feed.id}`, {
      method: "PATCH",
      body: { expected_revision: feed.revision, active: feed.active === false },
    });
    setAdminStatus(`Feed "${feed.name}" ${feed.active === false ? "enabled" : "disabled"}.`);
    await renderFeeds();
  } catch (error) { setAdminStatus(`Update failed: ${error.message}`); }
}

async function rotateFeed(btn) {
  const feed = feedFor(btn);
  if (!feed) return;
  try {
    const result = await api(`/api/operations/incidents/${incident.id}/position-feeds/${feed.id}/rotate-token`, { method: "POST" });
    setAdminStatus(`Token rotated for "${feed.name}" - the old token is now invalid.`);
    revealToken(result);
    await renderFeeds();
  } catch (error) { setAdminStatus(`Rotate failed: ${error.message}`); }
}

async function createFeed() {
  const name = $("#feed-new-name")?.value?.trim();
  if (!name) { setAdminStatus("A feed name is required."); return; }
  if (!incident) { setAdminStatus("Select an incident in NexIncidentCommand first."); return; }
  try {
    const result = await api(`/api/operations/incidents/${incident.id}/position-feeds`, {
      method: "POST",
      body: { name, device_kind: $("#feed-new-kind")?.value || "vehicle_gps" },
    });
    $("#feed-new-name").value = "";
    setAdminStatus(`Feed "${result.name}" created.`);
    revealToken(result);
    await renderFeeds();
  } catch (error) { setAdminStatus(`Create failed: ${error.message}`); }
}

/* ------------------------------------------------------------------ wiring */

function updateIncidentLine() {
  const line = $("#ingest-incident-line");
  if (!line) return;
  line.textContent = incident
    ? `Active incident: ${incident.name} - feeds, drone and field imports below attach to it.`
    : "No active incident - position feeds, drone and field imports attach to the incident selected in NexIncidentCommand.";
}

async function init() {
  try { auth = await api("/api/auth/session"); } catch (_) { /* local mode defaults hold */ }

  onActiveIncident((active) => {
    incident = active;
    updateIncidentLine();
    renderFeeds();
  });
  incident = getActiveIncident();
  updateIncidentLine();

  $("#btn-feeds-refresh")?.addEventListener("click", () => { renderChannelStatus(); renderFeeds(); });
  $("#btn-feed-create")?.addEventListener("click", () => createFeed());
  /* Lazy-load the admin list the first time the details is opened, so the
     console costs nothing for operators who never touch feed admin. */
  $("#ops-position-feeds")?.addEventListener("toggle", (event) => { if (event.target.open) renderFeeds(); });

  renderChannelStatus();
}

init();
