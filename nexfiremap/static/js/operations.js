/* Offline incident-command workspace layered onto the existing Leaflet map.
   It only talks to NexFiremap's local server; no third-party service is used.

   An ES module: the map arrives through context.js's whenMap() instead of the
   old window.NexFiremapMap / "nexfiremap:ready" handshake, and app.js's
   context-menu renderer is a direct import rather than a global that only
   existed because app.js's <script> tag came first. */

import { showContextMenu } from "./app.js";
import { whenMap, setPrintView, onMapContextMenu } from "./context.js";

  const $ = (s) => document.querySelector(s);
  // Almost every innerHTML template below interpolates operator-supplied
  // free text (titles, callsigns, incident/account names, ...), and this is
  // a shared LAN workspace - an unescaped value from one operator's session
  // would run as script in every other operator's browser the moment they
  // view the same list. Escape at render time, not at input time, so it
  // still works no matter where the text came from (this UI, a field
  // import, a merged package from another installation).
  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const state = {
    map: null, meta: null, incidents: [], workspace: null,
    incidentId: "", periodId: "", scenarioId: "",
    featureLayer: null, resourceLayer: null, vehicleLayer: null, vehicleTrackLayer: null,
    windLayer: null, windObservationLayer: null,
    sketchLayer: null, progressionLayer: null,
    // Mutually exclusive: at most one of these is ever non-null at a time.
    // `drawing` means "collecting vertices for a brand-new geometry" (see
    // startDraw()); `editingFeatureId` means "editing an existing saved
    // feature's record fields" (see editFeature()). Both startDraw() and
    // editFeature() call cancelDraw()/cancelEdit() on entry precisely to
    // preserve that exclusivity - see the drawing/sketch state machine
    // section below, and persistSketchDraft()'s crash-recovery use of it.
    drawing: null, editingFeatureId: null,
    latestPackId: null, latestBackupName: null, pendingFieldImport: null, pendingMerge: null,
    droneMissions: [], droneAssets: [],
    auth: { enabled: false, username: "local operator", role: "administrator", csrf: "" },
  };
  // localStorage key for the in-progress-sketch/edit crash-recovery draft
  // (see persistSketchDraft()/restoreSketchDraft() below). Contract: the key
  // holds at most one draft, written on every edit and removed the moment
  // that edit either lands successfully or is deliberately cancelled - so
  // its mere presence at startup means the browser closed (crash, power
  // loss, accidental tab close) while something was genuinely unsaved.
  const SKETCH_DRAFT_KEY = "nexfiremap.unsavedSketch.v1";

  const PLAN_TYPES = new Set([
    "tactical_line", "escape_route", "division_boundary", "branch_boundary",
    "forecast_perimeter", "uncertainty_area", "evacuation_area",
    "structure_protection_area", "arrival_time_line", "spread_arrow",
  ]);
  const OBSERVATION_TYPES = new Set(["confirmed_perimeter", "fire_perimeter", "active_edge", "inactive_edge",
    "spot_fire", "smoke_report", "wind_observation", "burn_area"]);

  const TYPE_LABELS = {
    confirmed_perimeter: "Confirmed fire perimeter", fire_perimeter: "Fire perimeter line",
    active_edge: "Active fire edge", inactive_edge: "Inactive / cold edge",
    spot_fire: "Spot fire", smoke_report: "Smoke report", wind_observation: "Wind observation",
    tactical_line: "Control / tactical line", escape_route: "Escape route",
    anchor_point: "Anchor point", lookout: "Lookout", safety_zone: "Safety zone point",
    safety_zone_area: "Safety zone area", trigger_point: "Trigger / decision point",
    hazard: "Hazard", staging_area: "Staging area", command_post: "Command post",
    drop_point: "Drop point", water_source: "Water source",
    restricted_water_source: "Restricted water source", helibase: "Helibase",
    helispot: "Helispot", dip_site: "Dip site", weather_station: "Weather station",
    communications_repeater: "Communications repeater", road_closure: "Road closure",
    critical_value: "Critical value at risk", division_boundary: "Division / sector boundary",
    branch_boundary: "Branch boundary", forecast_perimeter: "Forecast perimeter (scenario)",
    uncertainty_area: "Forecast uncertainty area", evacuation_area: "Evacuation area",
    structure_protection_area: "Structure protection area", spread_arrow: "Spread arrow",
    arrival_time_line: "Estimated arrival-time line", road_restriction: "Road restriction",
    burn_area: "Observed burn area",
    resource_position: "Resource position report",
  };

  // ------------------------------------------------------ request helpers

  function operator() {
    return $("#ops-operator").value.trim() || "local operator";
  }

  /** Thin fetch() wrapper shared by every API call in this file: attaches
   * the operator identity + CSRF header, JSON-encodes a non-string body,
   * and turns a non-2xx response into a thrown Error carrying `.status`/
   * `.payload` (so callers can e.g. special-case 409 revision conflicts -
   * see handleRecordError() below). */
  async function api(url, options = {}) {
    const headers = { "X-Operator": operator(), ...(options.headers || {}) };
    if (state.auth.csrf && !["GET", "HEAD", "OPTIONS"].includes((options.method || "GET").toUpperCase())) {
      headers["X-CSRF-Token"] = state.auth.csrf;
    }
    if (options.body && typeof options.body !== "string") {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(options.body);
    }
    const response = await fetch(url, { ...options, headers });
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* download/no body */ }
    if (!response.ok) {
      const error = new Error(payload?.detail || `HTTP ${response.status}`);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  // -------------------------------------------------- authentication & accounts

  function applyIdentity(payload) {
    state.auth = payload;
    $("#auth-identity").hidden = !payload.enabled;
    $("#btn-auth-logout").hidden = !payload.enabled;
    $("#auth-identity").textContent = payload.enabled ? `${payload.username} · ${payload.role}` : "";
    $("#ops-operator").value = payload.username;
    $("#ops-operator").disabled = payload.enabled;
    document.body.dataset.incidentRole = payload.role;
    $("#ops-account-admin").hidden = !payload.enabled || payload.role !== "administrator";
  }

  /** Establishes the operator's session before anything else in the app can
   * safely run, blocking on a real login if there isn't one yet.
   *
   * A plain session check (GET /api/auth/session) either succeeds - nothing
   * more to do - or, on a 401, opens the login `<dialog>` and *awaits a
   * Promise that only resolves once the dialog's submit handler has
   * actually logged the operator in*. That Promise never rejects on a bad
   * password: the submit handler catches the login failure itself, shows
   * `#auth-error`, and leaves the Promise pending so the operator can just
   * retry in place, as many times as it takes - only a *successful* login
   * calls resolve() and closes the dialog. init() below `await`s this
   * function before touching any incident data specifically so that no
   * authenticated-only API call can ever fire with no session behind it;
   * the whole app is otherwise inert until a real identity exists. */
  async function ensureAuth() {
    const response = await fetch("/api/auth/session", { cache: "no-store" });
    if (response.ok) {
      applyIdentity(await response.json());
      return;
    }
    if (response.status !== 401) throw new Error(`Authentication check failed (HTTP ${response.status})`);
    const dialog = $("#auth-dialog");
    if (!dialog.open) dialog.showModal();
    $("#auth-username").focus();
    await new Promise((resolve) => {
      const submit = async (event) => {
        event.preventDefault();
        $("#auth-error").textContent = "";
        try {
          const login = await fetch("/api/auth/login", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: $("#auth-username").value, password: $("#auth-password").value }),
          });
          const payload = await login.json();
          if (!login.ok) throw new Error(payload.detail || "Sign in failed");
          applyIdentity(payload); $("#auth-password").value = ""; dialog.close();
          $("#auth-form").removeEventListener("submit", submit); resolve();
        } catch (error) {
          $("#auth-error").textContent = error.message; $("#auth-password").select();
        }
      };
      $("#auth-form").addEventListener("submit", submit);
    });
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST" });
    window.location.reload();
  }

  async function loadAccounts() {
    const list = $("#ops-account-list"); list.replaceChildren();
    if (!state.auth.enabled || state.auth.role !== "administrator") return;
    const accounts = await api("/api/auth/accounts");
    accounts.forEach((account) => {
      const row = document.createElement("div"); row.className = "ops-resource-row";
      row.innerHTML = `<strong>${escapeHtml(account.username)}</strong><span>${escapeHtml(account.role)}</span><small>${account.active ? "active" : "disabled"}</small>`;
      list.append(row);
    });
  }

  async function createAccount() {
    const username = $("#ops-account-username").value.trim();
    const password = $("#ops-account-password").value; const role = $("#ops-account-role").value;
    if (!username || password.length < 12) throw new Error("Username and a password of at least 12 characters are required.");
    await api("/api/auth/accounts", { method: "POST", body: { username, password, role } });
    $("#ops-account-password").value = ""; $("#ops-account-username").value = "";
    await loadAccounts(); $("#ops-account-status").textContent = `${username} created with ${role} role.`;
  }

  // ------------------------------------------------------- public session

  async function showPublicProducts() {
    $(".operations-block").hidden = true; $("#public-products-view").hidden = false;
    // The app switcher (app.js) offers NexIncidentCommand/NexIngest/
    // NexEventView, every one of which either shows an empty panel for a
    // public session (.operations-block is hidden regardless of app mode,
    // above) or, for the event-analysis endpoints, a 403 dead end - the
    // switcher itself has no reason to be offered here.
    const switcher = $("#app-switcher"); if (switcher) switcher.hidden = true;
    const list = $("#public-product-list"); list.replaceChildren();
    const products = await api("/api/public/products");
    products.forEach((product) => {
      const row = document.createElement("a"); row.className = "ops-resource-row";
      row.href = `/api/public/products/${product.id}/download`;
      row.innerHTML = `<strong>${escapeHtml(product.incident_name)}</strong><span>${escapeHtml(product.product_type)}</span>` +
        `<small>${escapeHtml(product.filename)} · ${(product.size_bytes / 1024).toFixed(1)} KB · ${new Date(product.created_at).toLocaleString()}</small>`;
      list.append(row);
    });
    if (!products.length) list.textContent = "No public-information product has been released.";
  }

  // ------------------------------- incident / period / scenario selection

  function setStatus(text, error = false) {
    const el = $("#ops-status");
    el.textContent = text || "";
    el.classList.toggle("error", error);
  }

  function currentPeriod() {
    return state.workspace?.operational_periods.find((p) => p.id === state.periodId);
  }

  function currentScenario() {
    return state.workspace?.scenarios.find((s) => s.id === state.scenarioId);
  }

  function option(value, text, selected = false) {
    const el = document.createElement("option");
    el.value = value; el.textContent = text; el.selected = selected;
    return el;
  }

  async function loadIncidents(preferId = "") {
    state.incidents = await api("/api/operations/incidents");
    const select = $("#ops-incident");
    select.replaceChildren(option("", "No incident selected"));
    state.incidents.forEach((incident) =>
      select.append(option(incident.id,
        `${incident.name}${incident.incident_number ? ` · ${incident.incident_number}` : ""}`)));
    const saved = preferId || localStorage.getItem("nexfiremap.activeIncident") || "";
    if (state.incidents.some((i) => i.id === saved)) select.value = saved;
    if (select.value) await selectIncident(select.value);
    else await selectIncident("");
  }

  /** Switches the active incident (or clears it for id=""), tearing down
   * every per-incident map layer/draft/telemetry state first so nothing
   * from the previous incident bleeds into the new one, then reloads the
   * whole workspace and its dependent panels from the server. `discardDraft:
   * false` on cancelDraw() here is deliberate: switching incidents is not a
   * deliberate cancel of an in-progress sketch, so any localStorage draft
   * for a *different* incident is left alone rather than deleted. */
  async function selectIncident(id) {
    cancelDraw({ discardDraft: false });
    state.progressionLayer?.clearLayers(); state.vehicleLayer?.clearLayers();
    state.vehicleTrackLayer?.clearLayers(); state.pendingFieldImport = null;
    clearWindMap();
    if ($("#btn-apply-field-import")) $("#btn-apply-field-import").hidden = true;
    state.incidentId = id;
    localStorage.setItem("nexfiremap.activeIncident", id);
    $("#ops-workspace").hidden = !id;
    if (!id) return;
    state.workspace = await api(`/api/operations/incidents/${id}`);
    const periods = state.workspace.operational_periods;
    const periodSelect = $("#ops-period");
    periodSelect.replaceChildren();
    periods.forEach((p) => periodSelect.append(option(p.id, `${p.name} · ${p.status}`)));
    state.periodId = periods.some((p) => p.id === state.periodId) ? state.periodId : (periods[0]?.id || "");
    periodSelect.value = state.periodId;
    rebuildScenarioSelect();
    renderCommandForms();
    renderFeatures();
    renderResources();
    await refreshVehicleTelemetry();
    await loadDroneMissions();
    await loadSafety();
    await loadSnapshots();
    await loadModelRuns();
    await loadProducts();
    const incident = state.workspace.incident;
    if (incident.center_lat != null && incident.center_lon != null && !sessionStorage.getItem(`nexfiremap.centered.${id}`)) {
      state.map.setView([incident.center_lat, incident.center_lon], Math.max(state.map.getZoom(), 11));
      sessionStorage.setItem(`nexfiremap.centered.${id}`, "1");
    }
    setStatus(`${state.workspace.features.features.length} operational objects · last saved ${new Date(incident.updated_at).toLocaleString()}`);
  }

  function rebuildScenarioSelect() {
    const select = $("#ops-scenario");
    const scenarios = state.workspace.scenarios.filter((s) => s.period_id === state.periodId);
    select.replaceChildren();
    scenarios.forEach((s) => select.append(option(s.id, `${s.name} · ${s.kind} · ${s.status}`)));
    state.scenarioId = scenarios.some((s) => s.id === state.scenarioId) ? state.scenarioId : (scenarios[0]?.id || "");
    select.value = state.scenarioId;
    renderCommandForms();
  }

  function renderCommandForms() {
    if (!state.workspace) return;
    const incident = state.workspace.incident, period = currentPeriod(), scenario = currentScenario();
    $("#ops-incident-name").value = incident.name || "";
    $("#ops-incident-number").value = incident.incident_number || "";
    $("#ops-incident-status").value = incident.status;
    $("#ops-incident-timezone").value = incident.timezone || "UTC";
    $("#ops-incident-notes").value = incident.notes || "";
    if (period) {
      $("#ops-period-name").value = period.name || ""; $("#ops-period-status").value = period.status;
      $("#ops-period-start").value = localDateTime(period.starts_at); $("#ops-period-end").value = localDateTime(period.ends_at);
      $("#ops-period-objectives").value = period.objectives || "";
    }
    if (scenario) {
      $("#ops-scenario-name").value = scenario.name || ""; $("#ops-scenario-kind").value = scenario.kind;
      $("#ops-scenario-status").value = scenario.status;
      $("#ops-scenario-description").value = scenario.description || ""; $("#ops-scenario-assumptions").value = scenario.assumptions || "";
    }
    const copyTarget = $("#ops-copy-target-period"); copyTarget.replaceChildren(option("", "select another period"));
    state.workspace.operational_periods.filter((item) => item.id !== state.periodId).forEach((item) => copyTarget.append(option(item.id, item.name)));
    $("#ops-copy-scenario-name").value = `${scenario?.name || "Plan"} carry-forward`;
  }

  // -------------------------------------------- feature styling & records

  // Colour-codes a map feature by its worst/most-notable status or type -
  // status (breached/completed/proposed) takes priority over the feature's
  // base type, since a stale or broken plan element needs to read
  // differently from a healthy one regardless of what kind of object it is.
  function featureColour(properties) {
    const type = properties.feature_type;
    const status = properties.status;
    if (status === "breached") return "#ff2d55";
    if (status === "completed" || status === "held") return "#33d17a";
    if (status === "proposed") return "#9aa0aa";
    if (type === "active_edge" || type === "spot_fire") return "#ff493d";
    if (type === "inactive_edge" || status === "inactive") return "#7e8796";
    if (type.includes("escape") || type.includes("safety")) return "#42d392";
    if (type.includes("water") || type === "dip_site") return "#4db8ff";
    if (type.includes("forecast") || type.includes("uncertainty")) return "#cc7dff";
    if (type === "hazard" || type === "road_closure") return "#ffb020";
    return "#4f9cff";
  }

  function isStaleObservation(properties) {
    if (!OBSERVATION_TYPES.has(properties.feature_type) || !properties.observed_at) return false;
    const age = Date.now() - Date.parse(properties.observed_at);
    return Number.isFinite(age) && age > (state.meta.observation_stale_hours || 6) * 3600000;
  }

  function popupHtml(feature) {
    const p = feature.properties;
    const esc = escapeHtml;
    const objective = p.objective ? `<p>${esc(p.objective)}</p>` : "";
    const stale = isStaleObservation(p) ? `<p class="ops-stale-warning">STALE observation: verify before use</p>` : "";
    return `<div class="ops-popup"><strong>${esc(p.title || TYPE_LABELS[p.feature_type] || p.feature_type)}</strong>${stale}${objective}` +
      `<dl><dt>Type</dt><dd>${esc(TYPE_LABELS[p.feature_type] || p.feature_type)}</dd>` +
      `<dt>Status</dt><dd>${esc(p.status)}</dd>` +
      `${p.responsible_unit ? `<dt>Responsible</dt><dd>${esc(p.responsible_unit)}</dd>` : ""}` +
      `${p.priority ? `<dt>Priority</dt><dd>${esc(p.priority)}</dd>` : ""}` +
      `<dt>Updated</dt><dd>${esc(new Date(p.updated_at).toLocaleString())}</dd></dl>` +
      `<button data-ops-edit="${esc(p.id)}">edit</button> <button data-ops-delete="${esc(p.id)}">remove</button></div>`;
  }

  const RECORD_FIELDS = {
    objective: "#ops-objective", responsible_unit: "#ops-responsible-unit",
    priority: "#ops-priority", assigned_resources: "#ops-assigned-resources",
    method: "#ops-method", required_equipment: "#ops-equipment",
    water_requirement: "#ops-water", prerequisites: "#ops-prerequisites",
    hazards: "#ops-hazards", escape_route: "#ops-escape-route",
    safety_zone: "#ops-safety-zone", communications_channel: "#ops-communications",
    notes: "#ops-notes",
    horizontal_accuracy_m: "#ops-horizontal-accuracy", fire_activity: "#ops-fire-activity",
    intensity: "#ops-intensity", wind_observation: "#ops-wind",
    wind_speed_ms: "#ops-wind-speed-ms", wind_from_deg: "#ops-wind-from-deg",
    wind_gust_ms: "#ops-wind-gust-ms", wind_height_m: "#ops-wind-height-m",
  };

  function recordProperties(base = {}) {
    const properties = { symbology_profile: base.symbology_profile || "simplified_multinational" };
    Object.entries(RECORD_FIELDS).forEach(([key, selector]) => { properties[key] = $(selector).value.trim(); });
    return properties;
  }

  function localDateTime(iso) {
    if (!iso) return "";
    const d = new Date(iso); if (Number.isNaN(d.getTime())) return "";
    const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }

  function isoDateTime(selector) {
    const value = $(selector).value;
    return value ? new Date(value).toISOString() : null;
  }

  function clearRecordForm({ keepTitle = false } = {}) {
    Object.values(RECORD_FIELDS).forEach((selector) => { $(selector).value = selector === "#ops-priority" ? "normal" : ""; });
    $("#ops-source").value = ""; $("#ops-confidence").value = ""; $("#ops-observed-at").value = ""; $("#ops-observer").value = "";
    $("#ops-valid-from").value = ""; $("#ops-valid-to").value = "";
    if (!keepTitle) $("#ops-feature-title").value = "";
  }

  // --------------------------------------------- sketch draft persistence
  //
  // Crash-recovery for an in-progress sketch or record edit: every
  // keystroke/vertex is mirrored into localStorage (persistSketchDraft())
  // so a browser crash, power loss, or accidental tab close mid-edit
  // doesn't silently lose field-collected work. The contract is a single
  // slot (SKETCH_DRAFT_KEY, declared near the top of this file) that is
  // removed the instant the in-progress work is resolved one way or the
  // other - a successful save (saveGeometry()/saveFeatureRecord()) or a
  // deliberate cancel (cancelDraw()/cancelEdit()) - so anything still in
  // that slot at the next startup (restoreSketchDraft(), called from
  // init()) really was abandoned mid-edit and is worth offering to recover.

  function sketchFields() {
    const selectors = ["#ops-feature-type", "#ops-feature-title", "#ops-feature-status", "#ops-source",
      "#ops-confidence", "#ops-observed-at", "#ops-observer", "#ops-valid-from", "#ops-valid-to", ...Object.values(RECORD_FIELDS)];
    return Object.fromEntries(selectors.map((selector) => [selector, $(selector).value]));
  }

  function persistSketchDraft() {
    // Originally gated on state.drawing alone, so this silently did nothing
    // while editing an *existing* feature's record (state.editingFeatureId,
    // no geometry being drawn) - every keystroke in the hazards/escape-route/
    // notes form was already wired to call this, it just always no-opped.
    // A crash/power-loss mid-edit of an already-saved tactical record had no
    // recovery at all, unlike a brand-new sketch. startDraw()/editFeature()
    // keep state.drawing and state.editingFeatureId mutually exclusive, so
    // recording whichever is active is unambiguous.
    if (!state.incidentId || (!state.drawing && !state.editingFeatureId)) return;
    try {
      localStorage.setItem(SKETCH_DRAFT_KEY, JSON.stringify({
        version: 1, savedAt: new Date().toISOString(), incidentId: state.incidentId,
        periodId: state.periodId, scenarioId: state.scenarioId,
        drawing: state.drawing || null,
        editingFeatureId: state.drawing ? null : (state.editingFeatureId || null),
        fields: sketchFields(),
      }));
    } catch (_) { /* private mode/storage pressure must not block drawing */ }
  }

  function clearSketchDraft() {
    try { localStorage.removeItem(SKETCH_DRAFT_KEY); } catch (_) { /* unavailable storage */ }
  }

  async function restoreSketchDraft() {
    let draft;
    try { draft = JSON.parse(localStorage.getItem(SKETCH_DRAFT_KEY) || "null"); } catch (_) { clearSketchDraft(); return; }
    const savedAt = Date.parse(draft?.savedAt);
    if (!draft || draft.version !== 1 || !Number.isFinite(savedAt) || Date.now() - savedAt > 7 * 86400000) { clearSketchDraft(); return; }
    const knownIncident = state.incidents.some((item) => item.id === draft.incidentId);

    if (draft.editingFeatureId) {
      // Recovery path for an in-progress edit of an already-saved record's
      // fields (objective, hazards, escape route, etc.), as opposed to a
      // brand-new sketch below - no geometry to restore, just re-open the
      // same feature for editing and reapply the drafted field values.
      if (!knownIncident) { clearSketchDraft(); return; }
      if (!confirm("An unsaved edit to an operational record was recovered from this browser. Restore it?")) { clearSketchDraft(); return; }
      if (state.incidentId !== draft.incidentId) await selectIncident(draft.incidentId);
      if (!state.workspace.operational_periods.some((item) => item.id === draft.periodId)) { clearSketchDraft(); return; }
      const feature = state.workspace.features.features.find((f) => f.properties.id === draft.editingFeatureId);
      if (!feature) { clearSketchDraft(); return; }
      state.periodId = draft.periodId;
      state.scenarioId = state.workspace.scenarios.some((item) => item.id === draft.scenarioId) ? draft.scenarioId : "";
      $("#ops-period").value = state.periodId; rebuildScenarioSelect(); $("#ops-scenario").value = state.scenarioId; renderFeatures();
      await editFeature(draft.editingFeatureId);
      Object.entries(draft.fields || {}).forEach(([selector, value]) => { const input = $(selector); if (input) input.value = String(value ?? ""); });
      setStatus("Recovered unsaved edits to an operational record. Review and save, or cancel explicitly.", true);
      return;
    }

    const d = draft.drawing;
    const validPoints = Array.isArray(d?.points) && d.points.every((point) => Array.isArray(point) && point.length === 2 &&
      point.every(Number.isFinite) && point[0] >= -180 && point[0] <= 180 && point[1] >= -90 && point[1] <= 90);
    if (!knownIncident || !state.meta.feature_types.includes(d?.featureType) || geometryType(d.featureType) !== d.geometry || !validPoints) {
      clearSketchDraft(); return;
    }
    if (!confirm("An unsaved tactical sketch was recovered from this browser. Restore it?")) { clearSketchDraft(); return; }
    if (state.incidentId !== draft.incidentId) await selectIncident(draft.incidentId);
    if (!state.workspace.operational_periods.some((item) => item.id === draft.periodId)) { clearSketchDraft(); return; }
    state.periodId = draft.periodId;
    state.scenarioId = state.workspace.scenarios.some((item) => item.id === draft.scenarioId) ? draft.scenarioId : "";
    $("#ops-period").value = state.periodId; rebuildScenarioSelect(); $("#ops-scenario").value = state.scenarioId;
    Object.entries(draft.fields || {}).forEach(([selector, value]) => { const input = $(selector); if (input) input.value = String(value ?? ""); });
    state.drawing = { featureType: d.featureType, geometry: d.geometry, points: d.points.map((point) => [...point]) };
    state.map.getContainer().classList.add("ops-drawing"); state.map.on("click", drawClick); redrawSketch();
    $("#btn-start-draw").hidden = true; $("#btn-cancel-draw").hidden = false;
    $("#btn-finish-draw").hidden = d.geometry === "Point"; $("#btn-undo-draw").hidden = d.geometry === "Point";
    setStatus(`Recovered unsaved sketch · ${d.points.length} vertex/vertices. Finish or cancel explicitly.`, true);
  }

  // --------------------------------------- drawing / sketch state machine
  //
  // startDraw() begins collecting vertices for a brand-new feature into
  // state.drawing; editFeature() opens an existing feature's record fields
  // for editing via state.editingFeatureId. The two are mutually exclusive
  // (see the state object's own comment near the top of this file) -
  // startDraw() calls cancelEdit() first and editFeature() calls
  // cancelDraw() first, so entering one path always cleanly exits the
  // other rather than letting both be active at once.

  /** Injects the vendored tactical sprite sheet into the document once.
   *
   * `<use href="...">` cannot reference an external file - Chrome dropped
   * cross-document SVG references years ago on security grounds - so the sheet
   * has to live in this document for the markers to resolve. Fetched rather
   * than inlined into index.html so the sprites stay one vendored asset
   * (`static/vendor/symbols/tactical.svg`) rather than being duplicated into
   * the page template.
   *
   * A failure here is non-fatal on purpose: `pointMarker` falls back to the
   * initials it always drew, so a missing sprite sheet degrades the map's
   * appearance and never its function.
   * @returns {Promise<void>} */
  async function loadSprites() {
    const url = state.meta?.symbology?.sprite_url;
    if (!url || document.getElementById("nf-symbol-sprites")) return;
    try {
      const response = await fetch(url);
      if (!response.ok) return;
      const holder = document.createElement("div");
      holder.id = "nf-symbol-sprites";
      holder.hidden = true;
      holder.innerHTML = await response.text();
      document.body.appendChild(holder);
    } catch {
      /* offline or missing: markers fall back to initials */
    }
  }

  function pointMarker(feature, latlng) {
    const p = feature.properties;
    // The *glyph* comes from the server, which resolves it for this install's
    // configured symbology profile (DV 102 / ICS / neutral - see
    // symbology.py). The frontend keeps no second copy of that table, so
    // switching profile is a server setting rather than a code change on both
    // sides.
    //
    // The *colour* deliberately does not: featureColour() below encodes this
    // feature's current status (breached, completed, proposed...), which is
    // what an operator needs to see first and is a property of the feature
    // right now rather than of its symbol category. The symbology table's own
    // `color` is used for the product legends instead, where there is no live
    // status to convey.
    const symbol = state.meta?.symbology?.symbols?.[p.feature_type];
    const color = featureColour(p);
    const sprites = document.getElementById("nf-symbol-sprites");
    const html = symbol && sprites
      ? `<span style="--ops-color:${color}"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#nf-sym-${symbol.glyph}"/></svg></span>`
      : `<span style="--ops-color:${color}">${(TYPE_LABELS[p.feature_type] || p.feature_type).split(/\s+/).map((x) => x[0]).join("").slice(0, 2).toUpperCase()}</span>`;
    return L.marker(latlng, { icon: L.divIcon({
      className: "ops-map-icon", html,
      iconSize: [30, 30], iconAnchor: [15, 15],
    }) });
  }

  function renderFeatures() {
    state.featureLayer.clearLayers();
    const visible = state.workspace.features.features.filter((f) => {
      const p = f.properties;
      return !p.deleted_at && p.period_id === state.periodId && (!p.scenario_id || p.scenario_id === state.scenarioId);
    });
    visible.forEach((feature) => {
      const p = feature.properties, color = featureColour(p), stale = isStaleObservation(p);
      const layer = L.geoJSON(feature, {
        pointToLayer: (_f, latlng) => pointMarker(feature, latlng),
        style: { color, weight: p.status === "proposed" ? 3 : 4, opacity: stale ? 0.55 : 1,
          dashArray: stale ? "3 8" : (["proposed", "unconfirmed"].includes(p.status) ? "8 7" : null),
          fillColor: color, fillOpacity: stale ? 0.07 : 0.16 },
      });
      layer.bindPopup(popupHtml(feature));
      // Right-click directly on a placed feature: Edit/Remove as a first-
      // class, discoverable action - the "couldn't remove a set point"
      // report turned out to be this (removeFeature() below already
      // worked, just tucked inside a popup's small text button, easy to
      // miss - see this file's other contextmenu wiring in wireEvents()
      // for the full story). stopPropagation so the map's own generic
      // point-menu (app.js) doesn't *also* fire underneath this one -
      // L.FeatureGroup (what L.geoJSON returns) forwards a child layer's
      // contextmenu to a listener on the group itself, same as any other
      // Leaflet event.
      layer.on("contextmenu", (e) => {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        showFeatureContextMenu(feature, e);
      });
      state.featureLayer.addLayer(layer);
    });
    const list = $("#ops-feature-list");
    list.replaceChildren();
    visible.slice(0, 50).forEach((feature) => {
      const p = feature.properties, row = document.createElement("button");
      row.type = "button"; row.className = "ops-feature-row";
      row.innerHTML = `<span class="ops-swatch" style="background:${featureColour(p)}"></span><span>${escapeHtml(p.title || TYPE_LABELS[p.feature_type] || p.feature_type)}</span><small>${escapeHtml(p.status)}${isStaleObservation(p) ? " · STALE" : ""}</small>`;
      row.addEventListener("click", () => {
        const layer = L.geoJSON(feature); state.map.fitBounds(layer.getBounds().pad(0.5), { maxZoom: 16 });
      });
      list.append(row);
    });
  }

  /** Right-click-on-a-feature menu: Edit/Zoom to/Remove, via app.js's
   * exported showContextMenu() - reuses the exact same
   * editFeature()/removeFeature() this file's popup buttons already call,
   * just surfaced as a direct, discoverable right-click action instead of
   * "open the popup, notice the small text button". */
  function showFeatureContextMenu(feature, e) {
    const p = feature.properties;
    const items = [
      { label: "Edit", action: () => editFeature(p.id) },
      {
        label: "Zoom to",
        action: () => {
          const layer = L.geoJSON(feature);
          state.map.fitBounds(layer.getBounds().pad(0.5), { maxZoom: 16 });
        },
      },
      { label: "Remove", danger: true, action: () => removeFeature(p.id).catch(showError) },
    ];
    showContextMenu(items, { x: e.originalEvent.clientX, y: e.originalEvent.clientY });
  }

  function geometryType(featureType) {
    for (const [geometry, types] of Object.entries(state.meta.geometry)) {
      if (types.includes(featureType)) return geometry;
    }
    return null;
  }

  // Begins a new geometry sketch. Enforces the drawing/editing exclusivity
  // invariant up front (bails out of any in-progress record edit before
  // starting), then resets any leftover drawing state before beginning.
  function startDraw() {
    if (!state.incidentId || !state.periodId) return setStatus("Select an incident and operational period first.", true);
    if (state.editingFeatureId) cancelEdit();
    cancelDraw();
    const featureType = $("#ops-feature-type").value;
    const geometry = geometryType(featureType);
    state.drawing = { featureType, geometry, points: [] };
    state.map.getContainer().classList.add("ops-drawing");
    state.map.on("click", drawClick);
    $("#btn-start-draw").hidden = true;
    $("#btn-cancel-draw").hidden = false;
    $("#btn-finish-draw").hidden = geometry === "Point";
    $("#btn-undo-draw").hidden = geometry === "Point";
    persistSketchDraft();
    setStatus(geometry === "Point" ? "Click the object location." : "Click vertices; press Enter or Finish when done.");
  }

  // Map click handler while a sketch is active (wired/unwired by
  // startDraw()/cancelDraw()). A Point geometry saves itself immediately on
  // the first click - there's nothing to "finish" - everything else just
  // appends a vertex and redraws the in-progress outline.
  function drawClick(event) {
    const d = state.drawing;
    if (!d) return;
    d.points.push([event.latlng.lng, event.latlng.lat]);
    persistSketchDraft();
    if (d.geometry === "Point") return saveGeometry({ type: "Point", coordinates: d.points[0] });
    redrawSketch();
  }

  // Re-renders the dashed white in-progress outline from state.drawing's
  // current vertex list - called after every vertex add/undo/restore so the
  // on-map preview always matches state.
  function redrawSketch() {
    const d = state.drawing;
    state.sketchLayer.clearLayers();
    if (!d || !d.points.length) return;
    const latlngs = d.points.map(([lon, lat]) => [lat, lon]);
    state.sketchLayer.addLayer(d.geometry === "Polygon" ? L.polygon(latlngs, { color: "#fff", dashArray: "5 5" }) : L.polyline(latlngs, { color: "#fff", dashArray: "5 5" }));
  }

  function undoDraw() {
    if (!state.drawing || state.drawing.geometry === "Point" || !state.drawing.points.length) return;
    state.drawing.points.pop();
    redrawSketch();
    persistSketchDraft();
    setStatus(`${state.drawing.points.length} vert${state.drawing.points.length === 1 ? "ex" : "ices"} in current sketch.`);
  }

  // Ends vertex collection for a Polygon/LineString sketch and hands the
  // finished geometry to saveGeometry(). A polygon ring must be closed
  // (first point repeated as the last) for valid GeoJSON, so that's added
  // here rather than requiring the operator to click the start point again.
  function finishDraw() {
    const d = state.drawing;
    if (!d) return;
    const needed = d.geometry === "Polygon" ? 3 : 2;
    if (d.points.length < needed) return setStatus(`Add at least ${needed} vertices.`, true);
    if (d.geometry === "Polygon") {
      const ring = [...d.points, d.points[0]];
      saveGeometry({ type: "Polygon", coordinates: [ring] });
    } else saveGeometry({ type: "LineString", coordinates: d.points });
  }

  // Persists the finished geometry as a new operational feature. Plan-type
  // features (PLAN_TYPES, or a proposed/planned/under_construction status)
  // are treated as forecast/proposed work rather than an observation: no
  // observed_at/confidence defaulting, and they're tied to the current
  // scenario so switching scenarios shows only that plan's own objects.
  async function saveGeometry(geometry) {
    const d = state.drawing;
    if (!d) return;
    const status = $("#ops-feature-status").value;
    const isPlan = PLAN_TYPES.has(d.featureType) || ["proposed", "planned", "under_construction"].includes(status);
    const payload = {
      period_id: state.periodId, scenario_id: isPlan ? (state.scenarioId || null) : null,
      feature_type: d.featureType, title: $("#ops-feature-title").value.trim(), status, geometry,
      observed_at: isPlan ? null : (isoDateTime("#ops-observed-at") || new Date().toISOString()),
      source: isPlan ? "incident plan" : "field observation", observer: $("#ops-observer").value.trim() || operator(),
      confidence: status === "confirmed" ? "confirmed" : (status === "unconfirmed" ? "unconfirmed" : null),
      valid_from: isoDateTime("#ops-valid-from"), valid_to: isoDateTime("#ops-valid-to"),
      properties: recordProperties(),
    };
    payload.source = $("#ops-source").value.trim() || payload.source;
    payload.confidence = $("#ops-confidence").value || payload.confidence;
    cancelDraw({ discardDraft: false });
    try {
      await api(`/api/operations/incidents/${state.incidentId}/features`, { method: "POST", body: payload });
      clearSketchDraft();
      clearRecordForm();
      await selectIncident(state.incidentId);
      setStatus("Operational object saved locally with revision history.");
    } catch (error) { setStatus(error.message, true); }
  }

  // Tears down any in-progress sketch (unhook the map click handler, clear
  // the preview layer, hide/show the relevant buttons). `discardDraft:
  // false` is used by callers (e.g. selectIncident()) that need this reset
  // for bookkeeping reasons but are not the operator deliberately
  // abandoning their sketch - the localStorage crash-recovery draft stays
  // in that case; a true deliberate cancel (the default) clears it.
  function cancelDraw({ discardDraft = true } = {}) {
    if (!state.map) return;
    state.map.off("click", drawClick);
    state.map.getContainer().classList.remove("ops-drawing");
    state.sketchLayer?.clearLayers();
    state.drawing = null;
    $("#btn-start-draw").hidden = false;
    $("#btn-finish-draw").hidden = true;
    $("#btn-undo-draw").hidden = true;
    $("#btn-cancel-draw").hidden = true;
    if (discardDraft) clearSketchDraft();
  }

  // Opens an existing feature's record fields for editing. Enforces the
  // drawing/editing exclusivity invariant up front (cancels any in-progress
  // sketch before switching into edit mode) - the geometry itself is not
  // editable here, only the record fields (title, status, hazards, etc).
  async function editFeature(id) {
    const feature = state.workspace.features.features.find((f) => f.properties.id === id);
    if (!feature) return;
    cancelDraw();
    const p = feature.properties;
    state.editingFeatureId = id;
    $("#ops-feature-type").value = p.feature_type;
    $("#ops-feature-title").value = p.title || "";
    $("#ops-feature-status").value = p.status;
    Object.entries(RECORD_FIELDS).forEach(([key, selector]) => { $(selector).value = p[key] || (key === "priority" ? "normal" : ""); });
    $("#ops-source").value = p.source || ""; $("#ops-confidence").value = p.confidence || "";
    $("#ops-observed-at").value = localDateTime(p.observed_at); $("#ops-observer").value = p.observer || "";
    $("#ops-valid-from").value = localDateTime(p.valid_from);
    $("#ops-valid-to").value = localDateTime(p.valid_to);
    $("#ops-record-fields").open = true;
    $("#btn-start-draw").hidden = true; $("#btn-save-record").hidden = false; $("#btn-cancel-edit").hidden = false;
    setStatus(`Editing ${p.title || TYPE_LABELS[p.feature_type] || p.feature_type} · revision ${p.revision}.`);
    $("#ops-record-fields").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function saveFeatureRecord() {
    const id = state.editingFeatureId;
    const feature = state.workspace.features.features.find((f) => f.properties.id === id);
    if (!feature) return;
    const p = feature.properties;
    try {
      await api(`/api/operations/incidents/${state.incidentId}/features/${id}`, {
        method: "PATCH", body: {
          expected_revision: p.revision,
          title: $("#ops-feature-title").value.trim(), status: $("#ops-feature-status").value,
          source: $("#ops-source").value.trim() || null,
          observed_at: isoDateTime("#ops-observed-at"), observer: $("#ops-observer").value.trim() || null,
          confidence: $("#ops-confidence").value || null,
          valid_from: isoDateTime("#ops-valid-from"), valid_to: isoDateTime("#ops-valid-to"),
          properties: recordProperties(p),
        },
      });
      cancelEdit();
      await selectIncident(state.incidentId);
      setStatus("Operational record updated with a new revision.");
    } catch (error) {
      setStatus(error.status === 409 ? "Edit conflict: the latest saved version was kept. Reload and retry." : error.message, true);
      if (error.status === 409) { cancelEdit(); await selectIncident(state.incidentId); }
    }
  }

  function cancelEdit() {
    state.editingFeatureId = null;
    $("#btn-save-record").hidden = true; $("#btn-cancel-edit").hidden = true;
    $("#btn-start-draw").hidden = false;
    clearRecordForm();
    // Deliberate cancel (or a successful/conflicted save, which both route
    // through here) discards any recovery draft for this edit - matches the
    // sketch-draft contract: "successful save or deliberate cancel removes it."
    clearSketchDraft();
  }

  async function removeFeature(id) {
    const feature = state.workspace.features.features.find((f) => f.properties.id === id);
    if (!feature || !confirm("Remove this object from the active operational map? Its audit history remains.")) return;
    await api(`/api/operations/incidents/${state.incidentId}/features/${id}?expected_revision=${feature.properties.revision}`, { method: "DELETE" });
    await selectIncident(state.incidentId);
  }

  // ------------------------------------- safety checklist & plan approval

  async function loadSafety() {
    const container = $("#ops-safety"); container.replaceChildren();
    if (!state.periodId) return;
    const checks = await api(`/api/operations/incidents/${state.incidentId}/periods/${state.periodId}/safety?scenario_id=${encodeURIComponent(state.scenarioId || "")}`);
    checks.forEach((check) => {
      const label = document.createElement("label");
      label.innerHTML = `<input type="checkbox" data-safety-key="${check.key}" ${check.checked ? "checked" : ""}> <span>${check.label}</span>`;
      label.querySelector("input").addEventListener("change", saveSafety);
      container.append(label);
    });
  }

  async function saveSafety(event) {
    // Wired directly as a checkbox "change" handler with no .catch() at the
    // call site, unlike every other mutation in this file - a failed save
    // (network blip, server error) previously became an unhandled promise
    // rejection: no status message, and the checkbox stayed visually
    // checked/unchecked exactly as clicked even though nothing persisted.
    // On a safety review checklist that's the single worst place for a
    // silent failure to live, so this now surfaces the error and reloads
    // the checklist from the server rather than trusting the optimistic DOM.
    //
    // It also used to resend every currently-displayed checkbox's state
    // (a full snapshot from this browser's own DOM) on every single toggle.
    // The backend upserts by check_key and never touches keys it isn't
    // given, so that full snapshot bought nothing - it only meant two
    // operators reviewing the same checklist could silently stomp each
    // other: if operator A's page still shows a stale state for the item
    // operator B just checked, A ticking an unrelated box resends A's
    // stale value for B's item and quietly reverts it, with neither
    // operator seeing any conflict. Sending only the single toggled key
    // removes that race entirely.
    const target = event?.target;
    const checks = target
      ? [{ key: target.dataset.safetyKey, checked: target.checked }]
      : Array.from(document.querySelectorAll("[data-safety-key]")).map((el) => ({ key: el.dataset.safetyKey, checked: el.checked }));
    try {
      await api(`/api/operations/incidents/${state.incidentId}/periods/${state.periodId}/safety`, {
        method: "PUT", body: { scenario_id: state.scenarioId || null, checks },
      });
      setStatus("Safety review saved locally.");
    } catch (error) {
      setStatus(`Safety review NOT saved: ${error.message}`, true);
      await loadSafety();
    }
  }

  async function approveScenario(acknowledge = false) {
    if (!state.scenarioId) return setStatus("Create or select a plan scenario first.", true);
    try {
      const result = await api(`/api/operations/incidents/${state.incidentId}/scenarios/${state.scenarioId}/approve`, {
        method: "POST", body: { approver: operator(), acknowledge_warnings: acknowledge,
          expected_revision: currentScenario()?.revision },
      });
      await selectIncident(state.incidentId);
      setStatus(result.safety_warnings?.length ? "Plan approved with explicitly acknowledged safety warnings." : "Plan approved after safety review.");
    } catch (error) {
      if (!acknowledge && confirm(`${error.message}\n\nApprove with the remaining warnings explicitly acknowledged?`)) return approveScenario(true);
      setStatus(error.message, true);
    }
  }

  // ---------------------------------------- resources & vehicle telemetry

  function renderResources() {
    state.resourceLayer.clearLayers();
    const list = $("#ops-resource-list"); list.replaceChildren();
    const positionSelect = $("#ops-position-resource"); positionSelect.replaceChildren(option("", "unlisted resource"));
    state.workspace.resources.forEach((resource) => {
      positionSelect.append(option(resource.id, resource.callsign));
      const row = document.createElement("button"); row.type = "button"; row.className = "ops-resource-row";
      row.innerHTML = `<strong>${escapeHtml(resource.callsign)}</strong><span>${escapeHtml(resource.unit_type)}</span><small>${escapeHtml(resource.status)}${resource.assignment ? ` · ${escapeHtml(resource.assignment)}` : ""}</small>`;
      row.addEventListener("click", () => editResource(resource.id));
      list.append(row);
      if (resource.latitude != null && resource.longitude != null) {
        L.circleMarker([resource.latitude, resource.longitude], { radius: 7, color: "#fff", fillColor: "#29a3ff", fillOpacity: 0.9 })
          .bindTooltip(`${resource.callsign} · ${resource.status}`).addTo(state.resourceLayer);
      }
    });
  }

  /** Pulls the latest known position for every vehicle and redraws the
   * vehicle layer, colour-coding each marker by how much to trust it.
   *
   * The server attaches a `quality` object per position with three
   * independent red flags, any one of which marks the fix as a warning
   * (drawn red, ahead of the normal fresh/stale colouring below):
   *  - `implausible_speed`: the implied speed since the previous fix for
   *    this vehicle exceeds a sane ground-vehicle/aircraft bound - GPS
   *    jump, clock skew, or a swapped/relayed identity.
   *  - `poor_accuracy`: the report's own claimed accuracy radius is too
   *    coarse to trust for tactical positioning.
   *  - `out_of_order`: this fix's timestamp is older than one already
   *    recorded for the same vehicle - a delayed/replayed report arriving
   *    after a newer one, so "latest" can't be taken at face value.
   * A quality warning is reported to the operator (status line + red
   * marker) but never silently dropped - the position still renders, since
   * a suspicious-but-real fix is still better tactical information than no
   * fix at all. */
  async function refreshVehicleTelemetry() {
    state.vehicleLayer.clearLayers(); state.vehicleTrackLayer.clearLayers();
    const status = $("#ops-vehicle-status");
    if (!state.incidentId) { status.textContent = "No incident selected."; return; }
    const latest = await api(`/api/operations/incidents/${state.incidentId}/vehicle-positions/latest`);
    let fresh = 0, stale = 0, warnings = 0;
    latest.features.forEach((feature) => {
      const properties = feature.properties, quality = properties.quality || {};
      const warning = quality.implausible_speed || quality.poor_accuracy || quality.out_of_order;
      const colour = warning ? "#ff493d" : (properties.stale ? "#7f8794" : "#19d3c5");
      if (warning) warnings += 1;
      if (properties.stale) stale += 1; else fresh += 1;
      const label = document.createElement("span");
      label.textContent = `${properties.callsign} · ${Math.round(properties.age_seconds)} s old` +
        `${warning ? " · quality warning" : ""}`;
      L.circleMarker([feature.geometry.coordinates[1], feature.geometry.coordinates[0]], {
        radius: 8, color: "#fff", weight: 2, fillColor: colour, fillOpacity: 0.95,
      }).bindTooltip(label).addTo(state.vehicleLayer);
    });
    if ($("#ops-show-vehicle-tracks").checked) {
      const start = new Date(Date.now() - 2 * 3600000).toISOString();
      const tracks = await api(`/api/operations/incidents/${state.incidentId}/vehicle-tracks?start=${encodeURIComponent(start)}`);
      L.geoJSON(tracks, {
        style: { color: "#19d3c5", weight: 3, opacity: 0.8, dashArray: "6 5" },
        pointToLayer: (_feature, latlng) => L.circleMarker(latlng, { radius: 3, color: "#19d3c5" }),
      }).addTo(state.vehicleTrackLayer);
    }
    status.textContent = `${fresh} fresh · ${stale} stale · ${warnings} quality warning(s) · threshold ${latest.freshness_threshold_seconds} s`;
  }

  // ----------------------------------------------------------- wind field

  function windColour(speed) {
    return speed >= 10 ? "#ff493d" : (speed >= 5 ? "#ffb547" : "#19d3c5");
  }

  /** Fetches an interpolated wind field for the current viewport and draws
   * it as arrow markers, plus the raw measurements that fed it.
   *
   * The server reports `wind_to_deg` - the compass bearing the wind is
   * blowing *towards* (0deg = north, measured clockwise), which is the
   * natural quantity for "which way will the fire run". The CSS rotation
   * applied to the arrow glyph is `wind_to_deg - 90`: CSS/SVG rotation angle
   * 0 points along +x (east, i.e. compass 90deg), so converting a compass
   * bearing to a screen rotation is a fixed -90deg offset - without it every
   * arrow would point 90deg away (e.g. a wind blowing towards north would
   * draw pointing east). The tooltip separately reports `wind_from_deg`
   * (the traditional meteorological "wind FROM" bearing meteorologists
   * and radio traffic actually say aloud), which is not the angle drawn. */
  async function showWindMap() {
    if (!state.incidentId) throw new Error("Select an incident first.");
    const bounds = state.map.getBounds(), at = isoDateTime("#ops-wind-time") || new Date().toISOString();
    const windowHours = Number($("#ops-wind-window").value), grid = Number($("#ops-wind-grid").value);
    const query = new URLSearchParams({
      bbox: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].join(","),
      at, window_hours: String(windowHours), grid: String(grid),
    });
    if (state.scenarioId) query.set("scenario_id", state.scenarioId);
    const result = await api(`/api/operations/incidents/${state.incidentId}/wind-field?${query}`);
    state.windLayer.clearLayers(); state.windObservationLayer.clearLayers();
    result.vectors.features.forEach((feature) => {
      const p = feature.properties, colour = windColour(p.speed_ms);
      const icon = L.divIcon({ className: "wind-vector-icon",
        html: `<span style="--wind-angle:${p.wind_to_deg - 90}deg;--wind-colour:${colour}">➤</span>`,
        iconSize: [26, 26], iconAnchor: [13, 13] });
      L.marker([feature.geometry.coordinates[1], feature.geometry.coordinates[0]], { icon, interactive: true })
        .bindTooltip(`${p.speed_ms.toFixed(1)} m/s from ${Math.round(p.wind_from_deg)}° · ${p.method}` +
          `${p.nearest_support_km == null ? "" : ` · nearest ${p.nearest_support_km.toFixed(1)} km`}` +
          `${p.vector_disagreement_ms == null ? "" : ` · disagreement ${p.vector_disagreement_ms.toFixed(1)} m/s`}`)
        .addTo(state.windLayer);
    });
    result.observations.features.forEach((feature) => {
      const p = feature.properties;
      L.circleMarker([feature.geometry.coordinates[1], feature.geometry.coordinates[0]], {
        radius: 8, color: windColour(p.speed_ms), weight: 3, fillOpacity: 0, dashArray: "3 3",
      }).bindTooltip(`MEASURED · ${p.title || "wind observation"} · ${p.speed_ms.toFixed(1)} m/s from ${Math.round(p.wind_from_deg)}° · ${new Date(p.observed_at).toLocaleString()}`)
        .addTo(state.windObservationLayer);
    });
    const warnings = result.warnings.length ? ` · WARNING: ${result.warnings.join("; ")}` : "";
    $("#ops-wind-status").textContent = `${result.method} · ${result.observations.features.length} retained measurement(s) · ${result.vectors.features.length} derived arrow(s) · ${new Date(result.at).toLocaleString()}${warnings}`;
  }

  function clearWindMap() {
    state.windLayer?.clearLayers(); state.windObservationLayer?.clearLayers();
    if ($("#ops-wind-status")) $("#ops-wind-status").textContent = "No wind field displayed.";
  }

  // --------------------------------------------- drone missions & mosaics

  async function loadDroneMissions(preferId = "") {
    const select = $("#ops-drone-mission");
    state.droneMissions = state.incidentId ? await api(`/api/operations/incidents/${state.incidentId}/drone-missions`) : [];
    const previous = preferId || select.value;
    select.replaceChildren(option("", "No mission"));
    state.droneMissions.forEach((mission) => select.append(option(mission.id, mission.name)));
    select.value = state.droneMissions.some((mission) => mission.id === previous) ? previous : (state.droneMissions[0]?.id || "");
    await loadDroneAssets();
  }

  async function createDroneMission() {
    if (!state.incidentId) throw new Error("Select an incident first.");
    const name = $("#ops-drone-mission-name").value.trim();
    if (!name) throw new Error("Mission name is required.");
    const mission = await api(`/api/operations/incidents/${state.incidentId}/drone-missions`, { method: "POST", body: {
      name, aircraft: $("#ops-drone-aircraft").value.trim(),
      operator: $("#ops-drone-operator").value.trim() || operator(), started_at: new Date().toISOString(),
    }});
    $("#ops-drone-mission-name").value = "";
    await loadDroneMissions(mission.id); $("#ops-drone-status").textContent = `Mission ${mission.name} created.`;
  }

  async function loadDroneAssets() {
    const missionId = $("#ops-drone-mission").value, list = $("#ops-drone-assets"); list.replaceChildren();
    state.droneAssets = missionId ? await api(`/api/operations/incidents/${state.incidentId}/drone-missions/${missionId}/assets`) : [];
    state.droneAssets.forEach((asset) => {
      const row = document.createElement("div"); row.className = "ops-resource-row";
      const choice = document.createElement("input"); choice.type = "checkbox";
      choice.dataset.droneAsset = asset.id; choice.disabled = !asset.offline_source_id;
      const preview = document.createElement("a"); preview.textContent = asset.filename;
      preview.href = `/api/operations/incidents/${state.incidentId}/drone-missions/${missionId}/assets/${asset.id}/thumbnail`;
      preview.target = "_blank"; preview.rel = "noopener";
      const detail = document.createElement("small");
      detail.textContent = `${asset.georef_status} · ${asset.width}×${asset.height} · ${new Date(asset.captured_at || asset.created_at).toLocaleString()}`;
      row.append(choice, preview, detail); list.append(row);
    });
    if (!state.droneAssets.length) list.textContent = missionId ? "No retained frames." : "Create or select a mission.";
  }

  // Ground corners (a manual georeference) and the georeference-type
  // selector are two halves of one optional input: either both are given
  // (an operator manually pinned the image to the ground) or neither is -
  // one without the other is a half-finished georeference the server
  // couldn't act on, so it's rejected client-side rather than uploaded.
  async function uploadDroneImage() {
    const missionId = $("#ops-drone-mission").value, file = $("#ops-drone-file").files?.[0];
    if (!missionId || !file) throw new Error("Select a mission and image.");
    const corners = $("#ops-drone-corners").value.trim(), georefKind = $("#ops-drone-georef-kind").value;
    if ((corners && !georefKind) || (!corners && georefKind)) throw new Error("Ground corners and georeference type must be supplied together.");
    if (corners) JSON.parse(corners);
    const query = new URLSearchParams({ filename: file.name, classification: "operational", georef_kind: georefKind });
    const captured = isoDateTime("#ops-drone-captured"); if (captured) query.set("captured_at", captured);
    if (corners) query.set("corners", corners);
    const headers = { "Content-Type": file.type || "application/octet-stream", "X-Operator": operator() };
    if (state.auth.csrf) headers["X-CSRF-Token"] = state.auth.csrf;
    const response = await fetch(`/api/operations/incidents/${state.incidentId}/drone-missions/${missionId}/assets?${query}`, {
      method: "POST", headers, body: file,
    });
    const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    $("#ops-drone-file").value = ""; await loadDroneAssets();
    $("#ops-drone-status").textContent = payload.offline_source_id ? "Image retained and registered as a local map layer; reload layer controls to display it." : "Image retained as unreferenced evidence.";
  }

  async function createDroneMosaic() {
    const missionId = $("#ops-drone-mission").value;
    const assetIds = [...document.querySelectorAll("[data-drone-asset]:checked")].map((item) => item.dataset.droneAsset);
    if (!missionId || !assetIds.length) throw new Error("Select at least one georeferenced frame.");
    const product = await api(`/api/operations/incidents/${state.incidentId}/drone-missions/${missionId}/mosaics`, {
      method: "POST", body: { name: $("#ops-drone-mosaic-name").value.trim(), asset_ids: assetIds },
    });
    $("#ops-drone-status").textContent = `Mosaic created with ${assetIds.length} frame(s), hash ${product.sha256.slice(0, 12)}…; reload layer controls to display it.`;
  }

  // ----------------------------------------------------- position reports

  async function savePositionReport() {
    const resourceId = $("#ops-position-resource").value || null;
    const resource = state.workspace.resources.find((item) => item.id === resourceId);
    const callsign = $("#ops-position-callsign").value.trim() || resource?.callsign || "";
    const latitude = Number($("#ops-position-lat").value), longitude = Number($("#ops-position-lon").value);
    if (!callsign || !Number.isFinite(latitude) || !Number.isFinite(longitude)) throw new Error("Callsign and valid coordinates are required.");
    await api(`/api/operations/incidents/${state.incidentId}/position-reports`, { method: "POST", body: {
      resource_id: resourceId, callsign, latitude, longitude, observed_at: new Date().toISOString(),
      report_source: $("#ops-position-source").value,
      accuracy_m: $("#ops-position-accuracy").value === "" ? null : Number($("#ops-position-accuracy").value),
      period_id: state.periodId || null, scenario_id: state.scenarioId || null,
    }});
    await selectIncident(state.incidentId); $("#ops-position-status").textContent = `${callsign} position recorded and audited.`;
  }

  // ------------------------------------------------------- record editing

  async function saveIncidentRecord() {
    const incident = state.workspace.incident;
    try {
      await api(`/api/operations/incidents/${state.incidentId}`, { method: "PATCH", body: {
        expected_revision: incident.revision, name: $("#ops-incident-name").value.trim(),
        incident_number: $("#ops-incident-number").value.trim() || null,
        status: $("#ops-incident-status").value, timezone: $("#ops-incident-timezone").value.trim(),
        notes: $("#ops-incident-notes").value.trim(),
      }});
      if ($("#ops-incident-status").value === "closed") await loadIncidents();
      else await selectIncident(state.incidentId);
      setStatus("Incident record updated with a new revision.");
    } catch (error) { await handleRecordError(error, "incident"); }
  }

  async function savePeriodRecord() {
    const period = currentPeriod(); if (!period) return;
    try {
      await api(`/api/operations/incidents/${state.incidentId}/periods/${period.id}`, { method: "PATCH", body: {
        expected_revision: period.revision, name: $("#ops-period-name").value.trim(),
        status: $("#ops-period-status").value, starts_at: isoDateTime("#ops-period-start"),
        ends_at: isoDateTime("#ops-period-end"), objectives: $("#ops-period-objectives").value.trim(),
      }});
      await selectIncident(state.incidentId); setStatus("Operational period updated with a new revision.");
    } catch (error) { await handleRecordError(error, "operational period"); }
  }

  async function saveScenarioRecord() {
    const scenario = currentScenario(); if (!scenario) return;
    const status = $("#ops-scenario-status").value === "approved" ? undefined : $("#ops-scenario-status").value;
    try {
      await api(`/api/operations/incidents/${state.incidentId}/scenarios/${scenario.id}`, { method: "PATCH", body: {
        expected_revision: scenario.revision, name: $("#ops-scenario-name").value.trim(),
        kind: $("#ops-scenario-kind").value, ...(status ? { status } : {}),
        description: $("#ops-scenario-description").value.trim(), assumptions: $("#ops-scenario-assumptions").value.trim(),
      }});
      await selectIncident(state.incidentId); setStatus("Scenario updated; safety approval is required after material changes.");
    } catch (error) { await handleRecordError(error, "scenario"); }
  }

  // Shared 409-conflict handler for the optimistic-concurrency pattern used
  // throughout this file: every PATCH sends `expected_revision`, and the
  // server rejects with 409 if another operator saved a newer revision
  // first. Rather than silently overwriting that newer save, this reloads
  // the workspace (discarding this operator's conflicting edit) so the
  // operator sees the current state and can redo their change against it.
  async function handleRecordError(error, label) {
    if (error.status === 409) {
      setStatus(`${label} conflict: the newer saved revision was kept. Review it and retry.`, true);
      await selectIncident(state.incidentId);
    } else setStatus(error.message, true);
  }

  // -------------------------------------------------------- resource CRUD

  function clearResourceForm() {
    $("#ops-resource-id").value = ""; $("#ops-resource-callsign").value = ""; $("#ops-resource-type").value = "";
    $("#ops-resource-status").value = "available"; $("#ops-resource-crew").value = ""; $("#ops-resource-water").value = "";
    $("#ops-resource-channel").value = ""; $("#ops-resource-capabilities").value = ""; $("#ops-resource-assignment").value = "";
    $("#btn-save-resource").textContent = "add resource"; $("#btn-cancel-resource").hidden = true;
  }

  function editResource(id) {
    const resource = state.workspace.resources.find((item) => item.id === id); if (!resource) return;
    $("#ops-resource-id").value = id; $("#ops-resource-callsign").value = resource.callsign;
    $("#ops-resource-type").value = resource.unit_type; $("#ops-resource-status").value = resource.status;
    $("#ops-resource-crew").value = resource.crew_size ?? ""; $("#ops-resource-water").value = resource.water_capacity_l ?? "";
    $("#ops-resource-channel").value = resource.contact_channel || ""; $("#ops-resource-capabilities").value = resource.capabilities || "";
    $("#ops-resource-assignment").value = resource.assignment || "";
    $("#btn-save-resource").textContent = "save resource"; $("#btn-cancel-resource").hidden = false;
  }

  async function saveResource() {
    const id = $("#ops-resource-id").value;
    const current = state.workspace.resources.find((item) => item.id === id);
    const payload = {
      callsign: $("#ops-resource-callsign").value.trim(), unit_type: $("#ops-resource-type").value.trim(),
      status: $("#ops-resource-status").value,
      crew_size: $("#ops-resource-crew").value === "" ? null : Number($("#ops-resource-crew").value),
      water_capacity_l: $("#ops-resource-water").value === "" ? null : Number($("#ops-resource-water").value),
      contact_channel: $("#ops-resource-channel").value.trim(), capabilities: $("#ops-resource-capabilities").value.trim(),
      assignment: $("#ops-resource-assignment").value.trim(),
    };
    if (!payload.callsign || !payload.unit_type) return setStatus("Resource callsign and unit type are required.", true);
    try {
      if (current) await api(`/api/operations/incidents/${state.incidentId}/resources/${id}`, {
        method: "PATCH", body: { ...payload, expected_revision: current.revision },
      });
      else await api(`/api/operations/incidents/${state.incidentId}/resources`, { method: "POST", body: payload });
      clearResourceForm(); await selectIncident(state.incidentId); setStatus(current ? "Resource updated with a new revision." : "Resource added.");
    } catch (error) { await handleRecordError(error, "resource"); }
  }

  // --------------------------------- creating incidents/periods/scenarios

  async function createIncident() {
    const name = prompt("Incident name"); if (!name) return;
    const number = prompt("Incident identifier / number (optional)", "") || "";
    const center = state.map.getCenter();
    const result = await api("/api/operations/incidents", { method: "POST", body: {
      name, incident_number: number, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      center_lat: center.lat, center_lon: center.lng,
    }});
    await loadIncidents(result.incident.id);
    setStatus("Incident created with a 12-hour operational period and primary Plan A.");
  }

  async function createNextPeriod() {
    const current = currentPeriod();
    const starts = current ? new Date(current.ends_at) : new Date();
    const ends = new Date(starts.getTime() + 12 * 3600 * 1000);
    const name = prompt("Operational period name", `Operational period ${starts.toLocaleString()}`);
    if (!name) return;
    const period = await api(`/api/operations/incidents/${state.incidentId}/periods`, {
      method: "POST", body: { name, starts_at: starts.toISOString(), ends_at: ends.toISOString(), objectives: "" },
    });
    await api(`/api/operations/incidents/${state.incidentId}/periods/${period.id}/scenarios`, {
      method: "POST", body: { name: "Plan A", kind: "primary", description: "Primary operational plan" },
    });
    state.periodId = period.id; state.scenarioId = "";
    await selectIncident(state.incidentId);
  }

  async function createScenario() {
    const name = prompt("Scenario name", "Plan B"); if (!name) return;
    const kind = prompt("Kind: primary, contingency, alternative, or worst_case", "contingency"); if (!kind) return;
    const scenario = await api(`/api/operations/incidents/${state.incidentId}/periods/${state.periodId}/scenarios`, {
      method: "POST", body: { name, kind, description: "", assumptions: "" },
    });
    state.scenarioId = scenario.id; await selectIncident(state.incidentId);
  }

  // ------------------------------------- snapshots, products & model runs

  async function createSnapshot() {
    const name = prompt("Snapshot name", `${currentPeriod()?.name || "Incident"} handover`); if (name === null) return;
    await api(`/api/operations/incidents/${state.incidentId}/snapshots`, {
      method: "POST", body: { name, period_id: state.periodId || null, classification: "operational" },
    });
    await loadSnapshots();
    setStatus("Immutable incident snapshot created.");
  }

  async function loadSnapshots() {
    const left = $("#ops-snapshot-left"), right = $("#ops-snapshot-right"), product = $("#ops-product-snapshot");
    left.replaceChildren(); right.replaceChildren(option("", "current workspace")); product.replaceChildren(option("", "current workspace"));
    if (!state.incidentId) return;
    const snapshots = await api(`/api/operations/incidents/${state.incidentId}/snapshots`);
    snapshots.forEach((snapshot) => {
      const label = `${snapshot.name} · ${new Date(snapshot.created_at).toLocaleString()}`;
      left.append(option(snapshot.id, label)); right.append(option(snapshot.id, label));
      product.append(option(snapshot.id, label));
    });
    $("#btn-compare-snapshots").disabled = snapshots.length === 0;
    if (!snapshots.length) $("#ops-comparison-status").textContent = "Create a snapshot before comparing changes.";
  }

  async function loadProducts() {
    const list = $("#ops-product-list"); list.replaceChildren();
    if (!state.incidentId) return;
    const products = await api(`/api/operations/incidents/${state.incidentId}/products`);
    products.slice(0, 20).forEach((product) => {
      const row = document.createElement("a"); row.className = "ops-resource-row";
      row.href = `/api/operations/incidents/${state.incidentId}/products/${product.id}/download`;
      row.innerHTML = `<strong>${product.filename}</strong><span>${product.classification}</span><small>${product.product_type} · ${(product.size_bytes / 1024).toFixed(1)} KB · SHA-256 ${product.sha256.slice(0, 12)}…</small>`;
      list.append(row);
    });
  }

  // Attached model runs carry server-computed provenance (reference time,
  // staleness, warnings) rather than raw model output - this panel is an
  // audit trail of *which* model results a scenario's plan actually relied
  // on, not a live re-run of the model itself.
  async function loadModelRuns() {
    const list = $("#ops-model-run-list"); list.replaceChildren();
    if (!state.incidentId || !state.scenarioId) {
      $("#ops-model-status").textContent = "Select a plan scenario to view attached model records."; return;
    }
    const rows = await api(`/api/operations/incidents/${state.incidentId}/model-runs?scenario_id=${encodeURIComponent(state.scenarioId)}`);
    rows.forEach((row) => {
      const item = document.createElement("div"); item.className = "ops-resource-row";
      const warnings = row.provenance.warnings || [];
      item.innerHTML = `<strong>${row.model_kind}</strong><span>${row.provenance.is_stale ? "STALE" : "within validity window"}</span>` +
        `<small>Job ${row.job_id ?? "imported"} · reference ${new Date(row.provenance.reference_at).toLocaleString()}${warnings.length ? `<br>${warnings.join("; ")}` : ""}</small>`;
      if (row.provenance.is_stale || warnings.length) item.classList.add("ops-stale-warning");
      list.append(item);
    });
    $("#ops-model-status").textContent = rows.length ? `${rows.length} auditable model record(s) attached.` : "No model output is attached to this scenario.";
  }

  async function attachModelRun() {
    if (!state.incidentId || !state.scenarioId) throw new Error("Select a plan scenario first.");
    const jobId = Number($("#ops-model-job-id").value);
    if (!Number.isInteger(jobId) || jobId < 1) throw new Error("Enter a completed analysis job ID.");
    const row = await api(`/api/operations/incidents/${state.incidentId}/scenarios/${state.scenarioId}/model-runs`, {
      method: "POST", body: { job_id: jobId },
    });
    await loadModelRuns();
    $("#ops-model-status").textContent = row.provenance.is_stale ? "Attached, but the model reference time is stale. Review warnings." : "Model provenance attached to the scenario.";
  }

  async function createProduct() {
    const productType = $("#ops-product-type").value, classification = $("#ops-product-classification").value;
    if ((classification === "public") !== (productType === "public_information")) {
      return setStatus("Public classification and the public-information template must be selected together.", true);
    }
    const result = await api(`/api/operations/incidents/${state.incidentId}/products`, { method: "POST", body: {
      format: $("#ops-product-format").value, classification, product_type: productType,
      snapshot_id: $("#ops-product-snapshot").value || null, title: $("#ops-product-title").value.trim(),
    }});
    await loadProducts();
    $("#ops-product-status").textContent = `${result.filename} created · ${(result.size_bytes / 1024).toFixed(1)} KB · SHA-256 ${result.sha256}.`;
  }

  async function compareSnapshots() {
    const left = $("#ops-snapshot-left").value; if (!left) return;
    const right = $("#ops-snapshot-right").value;
    const query = right ? `?right_snapshot_id=${encodeURIComponent(right)}` : "";
    const result = await api(`/api/operations/incidents/${state.incidentId}/snapshots/${left}/compare${query}`);
    const c = result.counts;
    const examples = result.changes.slice(0, 5).map((item) => `${item.classification} ${item.entity_type} ${item.id}`).join("; ");
    $("#ops-comparison-status").textContent = `${c.added} added · ${c.removed} removed · ${c.changed} changed · ${c.unchanged} unchanged.` +
      (examples ? ` ${examples}${result.changes.length > 5 ? "…" : ""}` : " No operational differences.");
  }

  // ----------------------------------------------------------- print view

  // "Print this view" now has a trigger in every app (see app.js's topbar
  // button, wired via context.js's setPrintView below), not just the
  // incident-command output row this originally lived in - so a missing
  // incident builds a simpler header instead of refusing to print. The old
  // behaviour (silently do nothing beyond a status-bar message) would be
  // outright invisible from NexFiremap: #ops-status is itself tagged
  // data-app="incident-command ingest eventview", hidden under NexFiremap,
  // so that refusal wouldn't even have been seen from the app this button
  // is now reachable from.
  function printOperationsMap() {
    const incident = state.workspace?.incident;
    const center = state.map.getCenter();
    const produced = `<span>Produced ${new Date().toLocaleString()} by ${escapeHtml(operator())} · OPERATIONAL</span>`;
    const mapLine = `<span>Map centre ${center.lat.toFixed(5)}, ${center.lng.toFixed(5)} · zoom ${state.map.getZoom()}</span>`;
    const title = $("#ops-print-title");
    if (incident) {
      const period = currentPeriod(), scenario = currentScenario();
      title.innerHTML = `<strong>${escapeHtml(incident.name)}</strong>` +
        `<span>${escapeHtml(incident.incident_number || incident.id)}</span>` +
        `<span>${escapeHtml(period?.name || "No operational period")}</span>` +
        `<span>${scenario ? `${escapeHtml(scenario.name)} · ${escapeHtml(scenario.kind)} · ${escapeHtml(scenario.status)}` : "Observed situation"}</span>` +
        produced + mapLine +
        `<span>Local incident record current at ${new Date(incident.updated_at).toLocaleString()}; external layers retain their displayed source/freshness limitations.</span>`;
    } else {
      title.innerHTML = `<strong>NexFiremap</strong>` +
        `<span>No active incident</span>` +
        produced + mapLine +
        `<span>Satellite and analysis layers retain their own displayed source and freshness limitations.</span>`;
    }
    title.setAttribute("aria-hidden", "false");
    state.map.invalidateSize();
    setTimeout(() => window.print(), 150);
  }

  // --------------------------------- backups, recovery & offline packages

  async function loadBackupStatus() {
    const payload = await api("/api/operations/backups");
    const status = payload.status, el = $("#ops-backup-status");
    if (!status.latest) {
      state.latestBackupName = null;
      el.textContent = `No verified backup yet · scheduled every ${status.interval_minutes || "-"} min.`;
      return;
    }
    state.latestBackupName = status.latest.name;
    const age = new Date(status.latest.created_at).toLocaleString();
    el.innerHTML = `Latest verified backup ${age} · ${(status.latest.size_bytes / 1048576).toFixed(1)} MB · ` +
      `<a href="/api/operations/backups/${encodeURIComponent(status.latest.name)}/download">download</a>`;
  }

  async function createBackup() {
    const button = $("#btn-backup-now"); button.disabled = true;
    $("#ops-backup-status").textContent = "Creating and integrity-checking backup…";
    try {
      const result = await api("/api/operations/backups", { method: "POST" });
      await loadBackupStatus();
      setStatus(`Verified database backup created (${(result.size_bytes / 1048576).toFixed(1)} MB).`);
    } finally { button.disabled = false; }
  }

  // A recovery copy is a separate, independently integrity-checked database
  // file made *from* the latest backup - not the live database and not the
  // backup itself - so it can be handed off/downloaded and restored without
  // any risk of touching the running system's own data.
  async function createRecovery() {
    if (!state.latestBackupName) return setStatus("Create a verified backup before making a recovery copy.", true);
    $("#ops-recovery-status").textContent = "Creating a separate verified recovery database…";
    const result = await api("/api/operations/recoveries", {
      method: "POST", body: { backup_name: state.latestBackupName },
    });
    $("#ops-recovery-status").innerHTML = `Recovery copy verified · ${(result.size_bytes / 1048576).toFixed(1)} MB · ` +
      `<a href="/api/operations/recoveries/${encodeURIComponent(result.name)}/download">download recovery database</a>`;
  }

  async function importMbtiles() {
    const file = $("#ops-mbtiles-file").files?.[0];
    const fields = {
      name: $("#ops-mbtiles-name").value.trim(), source: $("#ops-mbtiles-source").value.trim(),
      attribution: $("#ops-mbtiles-attribution").value.trim(), acquired_at: $("#ops-mbtiles-acquired").value,
      licence: $("#ops-mbtiles-licence").value.trim(), limitations: $("#ops-mbtiles-limitations").value.trim(),
    };
    if (!file || Object.entries(fields).some(([key, value]) => key !== "limitations" && !value)) {
      return setStatus("Choose an MBTiles file and complete its source, attribution, date and licence metadata.", true);
    }
    if (file.size > 4 * 1024 * 1024 * 1024) return setStatus("MBTiles file exceeds the 4 GB import limit.", true);
    const button = $("#btn-import-mbtiles"); button.disabled = true;
    $("#ops-mbtiles-status").textContent = `Uploading and validating ${file.name}…`;
    try {
      const extension = file.name.toLowerCase().split(".").pop();
      fields.source_format = extension === "tif" || extension === "tiff" ? "geotiff" : extension === "gpkg" ? "gpkg" : "mbtiles";
      const query = new URLSearchParams(fields);
      const response = await fetch(`/api/operations/offline-sources?${query}`, {
        method: "POST", headers: { "Content-Type": "application/x-sqlite3", "X-Operator": operator() }, body: file,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      $("#ops-mbtiles-status").textContent = `${result.name} imported · ${result.tile_count} tiles · zoom ${result.min_zoom}-${result.max_zoom} · ` +
        `${(result.size_bytes / 1048576).toFixed(1)} MB. Reload this page to select it as a basemap.`;
      $("#ops-mbtiles-file").value = "";
    } finally { button.disabled = false; }
  }

  async function createTerrainPackage() {
    const sourceId = $("#ops-terrain-source").value, interval = Number($("#ops-terrain-interval").value);
    if (!sourceId) throw new Error("Select an imported geospatial DEM raster.");
    $("#ops-terrain-status").textContent = "Deriving local slope, aspect and contours…";
    const headers = state.auth.csrf ? { "X-CSRF-Token": state.auth.csrf } : {};
    const response = await fetch(`/api/operations/offline-sources/${sourceId}/terrain-package?interval_m=${encodeURIComponent(interval)}`, { method: "POST", headers });
    if (!response.ok) { const payload = await response.json(); throw new Error(payload.detail || `HTTP ${response.status}`); }
    const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement("a");
    link.href = url; link.download = response.headers.get("Content-Disposition")?.match(/filename="?([^";]+)"?/)?.[1] || "terrain-package.zip";
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    $("#ops-terrain-status").textContent = `Terrain package created · ${(blob.size / 1048576).toFixed(1)} MB · SHA-256 ${response.headers.get("X-Content-SHA256") || "see manifest"}`;
  }

  // --------------------------------------------------------- field import
  //
  // Two-step preview-then-apply flow for importing field-collected data
  // (GeoJSON/GPX/KML/KMZ/GeoPackage/CSV) gathered offline: previewFieldImport()
  // validates format/geometry/AOI/confirmation requirements against the
  // server without changing anything, then applyFieldImport() commits using
  // the exact same payload plus any operator acknowledgements the preview
  // flagged as required (features outside the reviewed map view, or
  // confirmed-status observations needing a named reason).

  function fieldImportPayload(file, content, contentBase64 = "") {
    const bounds = state.map.getBounds();
    let mapping = {};
    const mappingText = $("#ops-field-import-mapping")?.value.trim();
    if (mappingText) {
      try { mapping = JSON.parse(mappingText); }
      catch (_) { throw new Error("Field mapping must be a valid JSON object."); }
      if (!mapping || Array.isArray(mapping) || typeof mapping !== "object") throw new Error("Field mapping must be a JSON object.");
    }
    return {
      filename: file.name, content, content_base64: contentBase64, format: "", period_id: state.periodId || null,
      source: $("#ops-field-import-source").value.trim(), observer: operator(),
      default_feature_type: $("#ops-field-import-type").value, mapping,
      aoi_bbox: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()],
      confirmation_reason: $("#ops-field-import-confirmation").value.trim(),
    };
  }

  async function previewFieldImport() {
    const file = $("#ops-field-import-file").files?.[0];
    if (!file) return setStatus("Choose a GeoJSON, GPX, KML, KMZ, vector GeoPackage or CSV source file.", true);
    if (file.size > 20 * 1024 * 1024) return setStatus("Field source file exceeds the 20 MB import limit.", true);
    let payload;
    if (file.name.toLowerCase().endsWith(".kmz") || file.name.toLowerCase().endsWith(".gpkg")) {
      const bytes = new Uint8Array(await file.arrayBuffer()); let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 32768) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
      }
      payload = fieldImportPayload(file, "", btoa(binary));
    } else payload = fieldImportPayload(file, await file.text());
    $("#ops-field-import-status").textContent = "Validating geometry, coordinates, AOI and confirmation state…";
    const report = await api(`/api/operations/incidents/${state.incidentId}/field-imports/preview`, {
      method: "POST", body: payload,
    });
    state.pendingFieldImport = { payload, report };
    $("#btn-apply-field-import").hidden = false;
    $("#ops-field-import-status").textContent = `${report.feature_count} feature(s) · ${report.format} · ` +
      `${report.geometry_counts.Point} point, ${report.geometry_counts.LineString} line, ${report.geometry_counts.Polygon} area · ` +
      `${report.outside_aoi} outside current view${report.requires_confirmation ? " · CONFIRMATION REASON REQUIRED" : ""}. No records changed.`;
  }

  async function applyFieldImport() {
    const pending = state.pendingFieldImport; if (!pending) return;
    const payload = { ...pending.payload };
    if (pending.report.requires_aoi_acknowledgement) {
      if (!confirm(`${pending.report.outside_aoi} imported feature(s) extend outside the reviewed current map view. Apply with this explicitly acknowledged?`)) return;
      payload.acknowledge_outside_aoi = true;
    }
    if (pending.report.requires_confirmation && !payload.confirmation_reason) {
      return setStatus("A named confirmation reason is required before confirmed observations can be imported.", true);
    }
    const result = await api(`/api/operations/incidents/${state.incidentId}/field-imports/apply`, {
      method: "POST", body: payload,
    });
    state.pendingFieldImport = null; $("#btn-apply-field-import").hidden = true; $("#ops-field-import-file").value = "";
    await selectIncident(state.incidentId);
    setStatus(`Imported ${result.feature_ids.length} field observation(s); original bytes and SHA-256 retained.`);
  }

  // ------------------------------------ progression & tactical assessment

  async function showProgression() {
    const from = isoDateTime("#ops-progression-from"), to = isoDateTime("#ops-progression-to");
    if (!from || !to) return setStatus("Choose both progression times.", true);
    const result = await api(`/api/operations/incidents/${state.incidentId}/progression?from_time=${encodeURIComponent(from)}&to_time=${encodeURIComponent(to)}`);
    state.progressionLayer.clearLayers();
    L.geoJSON(result.from, { style: { color: "#7f8794", weight: 3, dashArray: "7 6", fillOpacity: 0.05 },
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, { radius: 6, color: "#7f8794" }) }).addTo(state.progressionLayer);
    L.geoJSON(result.new_since, { style: { color: "#ff493d", weight: 5, fillColor: "#ff493d", fillOpacity: 0.18 },
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, { radius: 8, color: "#ff493d", fillOpacity: 0.8 }) }).addTo(state.progressionLayer);
    $("#ops-progression-status").textContent = `${result.counts.from} observation(s) at start · ${result.counts.new_since} new by end · ${result.counts.to} total. Gray is earlier; red entered during the interval.`;
  }

  function clearProgression() {
    state.progressionLayer.clearLayers(); $("#ops-progression-status").textContent = "";
  }

  // Runs the server's tactical screening rules (line/area measurements plus
  // doctrine-based warnings, e.g. missing escape routes) over the current
  // period/scenario's objects. Warnings persist as active until explicitly
  // acknowledged with a reason (acknowledgeTacticalWarning()) - they are
  // never auto-dismissed just because the underlying condition might have
  // changed, since only a human reviewing the specific warning can know
  // whether the mitigation actually addressed it.
  async function assessTactics() {
    if (!state.periodId) return;
    const result = await api(`/api/operations/incidents/${state.incidentId}/tactical-assessment?period_id=${encodeURIComponent(state.periodId)}&scenario_id=${encodeURIComponent(state.scenarioId || "")}`);
    const totalLength = result.measurements.reduce((sum, item) => sum + item.length_m, 0);
    const totalArea = result.measurements.reduce((sum, item) => sum + item.area_m2, 0);
    const examples = result.warnings.slice(0, 5).map((item) => item.message).join("; ");
    $("#ops-tactical-status").textContent = `${result.measurements.length} object(s) · ${(totalLength / 1000).toFixed(2)} km total line/perimeter · ` +
      `${(totalArea / 10000).toFixed(2)} ha · ${result.warning_count} screening warning(s). ${examples}`;
    const list = $("#ops-tactical-warnings"); list.replaceChildren();
    result.warnings.forEach((warning) => {
      const row = document.createElement("div"); row.className = "ops-resource-row";
      const title = document.createElement("strong"); title.textContent = warning.code.replaceAll("_", " ");
      const stateLabel = document.createElement("span"); stateLabel.textContent = warning.acknowledgement ? "acknowledged" : "active";
      const detail = document.createElement("small"); detail.textContent = warning.acknowledgement ?
        `${warning.message}: ${warning.acknowledgement.reason} (${warning.acknowledgement.acknowledged_by})` : warning.message;
      row.append(title, stateLabel, detail);
      if (!warning.acknowledgement) {
        const button = document.createElement("button"); button.type = "button"; button.className = "btn ghost";
        button.dataset.warningAck = warning.warning_id; button.textContent = "acknowledge with reason"; row.append(button);
      }
      list.append(row);
    });
  }

  async function acknowledgeTacticalWarning(warningId) {
    const reason = prompt("Reason or mitigation for acknowledging this screening warning");
    if (!reason?.trim()) return;
    await api(`/api/operations/incidents/${state.incidentId}/tactical-warning-acknowledgements`, {
      method: "POST", body: { period_id: state.periodId, scenario_id: state.scenarioId || null,
        warning_id: warningId, reason: reason.trim() },
    });
    await assessTactics();
  }

  async function runTacticalCalculator() {
    let inputs;
    try { inputs = JSON.parse($("#ops-calculator-inputs").value); }
    catch (_) { return setStatus("Calculator inputs must be a valid JSON object.", true); }
    inputs.profile = $("#ops-calculator-profile").value.trim(); inputs.assumptions = $("#ops-calculator-assumptions").value.trim();
    const result = await api("/api/operations/tactical-calculator", {
      method: "POST", body: { kind: $("#ops-calculator-kind").value, inputs },
    });
    $("#ops-calculator-status").textContent = `${Object.entries(result.output).map(([key, value]) => `${key}: ${value}`).join(" · ")} · ${result.formula} · ${result.warning}`;
  }

  // ---------------------------------------------- copy scenario to period

  async function copyScenarioToPeriod() {
    const targetId = $("#ops-copy-target-period").value;
    const target = state.workspace.operational_periods.find((period) => period.id === targetId);
    const name = $("#ops-copy-scenario-name").value.trim();
    if (!state.scenarioId || !target || !name) return setStatus("Select another period and enter a copied-plan name.", true);
    const result = await api(`/api/operations/incidents/${state.incidentId}/scenarios/${state.scenarioId}/copy`, {
      method: "POST", body: { target_period_id: target.id, name },
    });
    state.periodId = target.id; state.scenarioId = result.scenario.id; await selectIncident(state.incidentId);
    setStatus(`Copied ${result.feature_ids.length} tactical object(s) into ${target.name}; source period unchanged.`);
  }

  // ----------------------------------- incident import & merge resolution
  //
  // Importing an incident package (typically produced by another
  // NexFiremap installation for offline field data exchange) has three
  // possible outcomes, decided server-side by /api/operations/import/preview
  // and acted on here without ever mutating anything until the operator
  // explicitly confirms:
  //   1. Invalid package (`!report.valid`) - rejected outright, nothing to
  //      resolve.
  //   2. New incident (`report.can_apply`) - no existing local record
  //      conflicts, so a plain confirm() is enough before applying atomically.
  //   3. Conflicts with an existing local incident (`!report.can_apply`,
  //      mode "existing_incident") - the interesting case: the package is
  //      staged server-side (both the local and incoming versions preserved
  //      untouched) and renderMerge()/resolveMerge() below drive a
  //      per-conflict decision UI rather than picking a side automatically.
  //      Only conflicts the server can't resolve unambiguously
  //      (classification "divergent"/"incoming_newer"/"local_newer") need an
  //      operator choice; identical or genuinely-new records apply on their
  //      own once resolved.
  async function importIncidentFile(file) {
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) throw new Error("Incident package exceeds the 50 MB browser import limit.");
    let bundle;
    try { bundle = JSON.parse(await file.text()); }
    catch (_) { throw new Error("The selected file is not valid JSON."); }
    setStatus("Validating incident package without making changes…");
    const report = await api("/api/operations/import/preview", { method: "POST", body: { bundle } });
    if (!report.valid) {
      setStatus(`Package rejected: ${(report.errors || []).join("; ")}`, true);
      return;
    }
    if (!report.can_apply) {
      const classes = report.classifications
        ? Object.entries(report.classifications).map(([key, count]) => `${key} ${count}`).join(" · ")
        : `${(report.conflicts || []).length} conflict(s)`;
      if (report.mode === "existing_incident" && confirm(`This package targets an existing incident. Stage both versions for side-by-side resolution?\n\n${classes}\n\nNo record will change until every conflict is resolved.`)) {
        const staged = await api("/api/operations/merge/stage", { method: "POST", body: { bundle } });
        renderMerge(staged);
        setStatus(`Package staged with SHA-256 ${staged.sha256}. Both inputs are preserved; resolve below.`, true);
      } else setStatus(`Import preview only: ${report.reason || "conflict resolution required"}. ${classes}. No records changed.`, true);
      return;
    }
    const c = report.counts;
    const summary = `${c.periods} period(s), ${c.scenarios} scenario(s), ${c.features} feature(s), ${c.resources} resource(s)`;
    if (!confirm(`Import new incident “${report.incident_name}”?\n\n${summary}\n\nThis was validated as a new incident and will be applied atomically.`)) return;
    const result = await api("/api/operations/import/apply", { method: "POST", body: { bundle } });
    await loadIncidents(result.report.incident_id);
    setStatus(`Imported ${report.incident_name}: ${summary}.`);
  }

  // Renders one "local" vs "incoming" choice per unresolved conflict from a
  // staged merge. Only the three ambiguous classifications are shown here -
  // conflicts the server already classified as identical or unambiguously
  // new don't need an operator decision and aren't listed, keeping this
  // form scoped to exactly the decisions that matter.
  function renderMerge(staged) {
    state.pendingMerge = staged;
    const container = $("#ops-merge-conflicts"); container.replaceChildren();
    const conflicts = staged.report.conflicts.filter((item) => ["divergent", "incoming_newer", "local_newer"].includes(item.classification));
    conflicts.forEach((item) => {
      const label = document.createElement("label"); label.className = "field";
      const text = document.createElement("span"); text.textContent = `${item.entity} ${item.id} · ${item.classification}`;
      const select = document.createElement("select"); select.dataset.mergeKey = `${item.entity}:${item.id}`;
      select.append(option("", "choose version"), option("local", "keep local"), option("incoming", "use incoming"));
      label.append(text, select); container.append(label);
    });
    $("#ops-merge-resolver").open = true; $("#btn-resolve-merge").hidden = false;
    $("#ops-merge-status").textContent = `${conflicts.length} decision(s) required. New records apply automatically; identical records remain unchanged.`;
  }

  // Submits the operator's local-vs-incoming choice for every staged
  // conflict in one atomic call. Requires every listed conflict to have a
  // choice (no partial resolution) so the merge either fully applies with
  // a complete, auditable decision for each divergence, or not at all.
  async function resolveMerge() {
    if (!state.pendingMerge) return;
    const choices = {};
    for (const select of document.querySelectorAll("[data-merge-key]")) {
      if (!select.value) return setStatus("Choose local or incoming for every staged conflict.", true);
      choices[select.dataset.mergeKey] = select.value;
    }
    const result = await api(`/api/operations/merge/${state.pendingMerge.id}/resolve`, { method: "POST", body: { choices } });
    state.pendingMerge = null; $("#btn-resolve-merge").hidden = true; $("#ops-merge-conflicts").replaceChildren();
    await selectIncident(result.incident_id);
    setStatus(`Package resolved by ${result.resolved_by}; selected changes and provenance are in the audit trail.`);
  }

  // ------------------------------------------------------ map-pack export

  // Renders a map-pack coverage report (tile counts, completeness percent,
  // per-zoom breakdown) shared by both createMapPackManifest() (build a new
  // manifest and check coverage) and verifyMapPackManifest() (re-check an
  // existing one against what's actually cached) - `verb` only changes the
  // wording, the coverage-summary rendering itself is identical either way.
  function showPackResult(result, verb = "checked") {
    const summary = result.summary;
    $("#ops-pack-status").textContent = `${result.name || "AOI"} ${verb}: ${summary.present}/${summary.expected} tiles present ` +
      `(${summary.completeness_percent}%) · ${summary.missing} missing · ${summary.modified || 0} modified · ` +
      `${(summary.bytes / 1048576).toFixed(1)} MB. ${summary.complete ? "READY FOR OFFLINE USE." : "Coverage is incomplete."}`;
    $("#ops-pack-status").classList.toggle("error", !summary.complete);
    const levels = $("#ops-pack-zooms"); levels.replaceChildren();
    (result.zoom_levels || []).forEach((level) => {
      const row = document.createElement("div"); row.className = "ops-resource-row";
      row.innerHTML = `<strong>Zoom ${level.zoom}</strong><span>${level.layer}</span>` +
        `<small>${level.present}/${level.expected} · ${level.missing} missing · ${level.modified || 0} modified · ${level.complete ? "complete" : "INCOMPLETE"}</small>`;
      levels.append(row);
    });
  }

  async function createMapPackManifest() {
    const bounds = state.map.getBounds();
    const minZoom = Number($("#ops-pack-min-zoom").value), maxZoom = Number($("#ops-pack-max-zoom").value);
    $("#ops-pack-status").textContent = "Hashing cached tiles and checking coverage…";
    const result = await api("/api/operations/map-packs", { method: "POST", body: {
      name: `${state.workspace?.incident?.name || "Current view"} offline coverage`,
      bbox: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()],
      layers: [$("#ops-pack-layer").value], min_zoom: minZoom, max_zoom: maxZoom,
    }});
    state.latestPackId = result.id; $("#btn-verify-pack").hidden = false; showPackResult(result);
  }

  async function verifyMapPackManifest() {
    if (!state.latestPackId) return;
    const result = await api(`/api/operations/map-packs/${state.latestPackId}/verify`, { method: "POST" });
    showPackResult(result, "verified");
  }

  // ------------------------------------------------------- control wiring
  //
  // Everything below just registers listeners - no state lives here. Each
  // handler either calls straight into a named function above, or wraps
  // one in `.catch(showError)` because addEventListener never awaits its
  // callback: an unhandled rejection from a bare `async` listener fails
  // silently (see the #ops-period/#ops-scenario comment just below for the
  // bug that pattern caused historically), so every async action taken
  // from a listener here is deliberately routed through showError.
  function wireControls() {
    // ---- incident / period / scenario selection ----
    $("#ops-incident").addEventListener("change", (e) => selectIncident(e.target.value).catch(showError));
    // Unlike the incident-switch handler right above, these two used to be
    // `async` listeners with no .catch() anywhere - addEventListener never
    // awaits its callback, so a failed loadSafety()/loadModelRuns() (network
    // blip while switching period/scenario) became an unhandled rejection:
    // no status message, safety checklist and model-provenance panel just
    // silently kept showing whatever they last had for the previous
    // period/scenario. Wrapping in an async IIFE + .catch(showError) matches
    // the pattern already used everywhere else in this file.
    $("#ops-period").addEventListener("change", (e) => {
      state.periodId = e.target.value; state.scenarioId = ""; rebuildScenarioSelect(); renderCommandForms(); renderFeatures();
      (async () => { await loadSafety(); await loadModelRuns(); })().catch(showError);
    });
    $("#ops-scenario").addEventListener("change", (e) => {
      state.scenarioId = e.target.value; renderCommandForms(); renderFeatures();
      (async () => { await loadSafety(); await loadModelRuns(); })().catch(showError);
    });
    $("#ops-operator").addEventListener("change", (e) => localStorage.setItem("nexfiremap.operator", e.target.value));

    // ---- authentication & account management ----
    $("#btn-auth-logout").addEventListener("click", () => logout().catch(showError));
    $("#btn-create-account").addEventListener("click", () => createAccount().catch(showError));

    // ---- creating / saving incidents, periods & scenarios ----
    $("#btn-new-incident").addEventListener("click", () => createIncident().catch(showError));
    $("#btn-next-period").addEventListener("click", () => createNextPeriod().catch(showError));
    $("#btn-new-scenario").addEventListener("click", () => createScenario().catch(showError));
    $("#btn-save-incident").addEventListener("click", () => saveIncidentRecord().catch(showError));
    $("#btn-save-period").addEventListener("click", () => savePeriodRecord().catch(showError));
    $("#btn-save-scenario").addEventListener("click", () => saveScenarioRecord().catch(showError));

    // ---- draw / sketch controls ----
    // See the drawing/sketch state machine section above: these map
    // directly onto startDraw()/finishDraw()/undoDraw()/cancelDraw()/
    // saveFeatureRecord()/cancelEdit(), which themselves enforce the
    // state.drawing / state.editingFeatureId exclusivity invariant.
    $("#btn-start-draw").addEventListener("click", startDraw);
    $("#btn-save-record").addEventListener("click", () => saveFeatureRecord().catch(showError));
    $("#btn-cancel-edit").addEventListener("click", cancelEdit);
    $("#btn-finish-draw").addEventListener("click", finishDraw);
    $("#btn-undo-draw").addEventListener("click", undoDraw);
    $("#btn-cancel-draw").addEventListener("click", cancelDraw);
    $("#btn-approve-scenario").addEventListener("click", () => approveScenario(false));

    // ---- resource roster ----
    $("#btn-save-resource").addEventListener("click", () => saveResource().catch(showError));
    $("#btn-cancel-resource").addEventListener("click", clearResourceForm);

    // ---- snapshots & printing ----
    $("#btn-snapshot").addEventListener("click", () => createSnapshot().catch(showError));
    $("#btn-compare-snapshots").addEventListener("click", () => compareSnapshots().catch(showError));
    $("#btn-print-operations").addEventListener("click", printOperationsMap);

    // ---- backups, recovery copies & offline data sources ----
    $("#btn-backup-now").addEventListener("click", () => createBackup().catch(showError));
    $("#btn-create-recovery").addEventListener("click", () => createRecovery().catch(showError));
    $("#btn-import-mbtiles").addEventListener("click", () => importMbtiles().catch(showError));
    $("#btn-terrain-package").addEventListener("click", () => createTerrainPackage().catch(showError));

    // ---- field import (offline-collected data) ----
    $("#btn-preview-field-import").addEventListener("click", () => previewFieldImport().catch(showError));
    $("#btn-apply-field-import").addEventListener("click", () => applyFieldImport().catch(showError));

    // ---- resource positions & vehicle telemetry ----
    $("#btn-save-position").addEventListener("click", () => savePositionReport().catch(showError));
    $("#btn-refresh-vehicles").addEventListener("click", () => refreshVehicleTelemetry().catch(showError));
    $("#ops-show-vehicle-tracks").addEventListener("change", () => refreshVehicleTelemetry().catch(showError));

    // ---- drone missions, imagery & mosaics ----
    $("#btn-create-drone-mission").addEventListener("click", () => createDroneMission().catch(showError));
    $("#ops-drone-mission").addEventListener("change", () => loadDroneAssets().catch(showError));
    $("#btn-upload-drone-image").addEventListener("click", () => uploadDroneImage().catch(showError));
    $("#btn-create-drone-mosaic").addEventListener("click", () => createDroneMosaic().catch(showError));

    // ---- wind field ----
    $("#btn-show-wind").addEventListener("click", () => showWindMap().catch(showError));
    $("#btn-clear-wind").addEventListener("click", clearWindMap);

    // Autofills the position-report form from a selected roster resource's
    // last-known callsign/coordinates - a convenience default, not a lock;
    // the fields stay freely editable for an ad hoc/unlisted report.
    $("#ops-position-resource").addEventListener("change", (event) => {
      const resource = state.workspace?.resources.find((item) => item.id === event.target.value);
      if (resource) {
        $("#ops-position-callsign").value = resource.callsign;
        if (resource.latitude != null) $("#ops-position-lat").value = resource.latitude;
        if (resource.longitude != null) $("#ops-position-lon").value = resource.longitude;
      }
    });

    // ---- progression, tactical assessment & calculator ----
    $("#btn-show-progression").addEventListener("click", () => showProgression().catch(showError));
    $("#btn-clear-progression").addEventListener("click", clearProgression);
    $("#btn-assess-tactics").addEventListener("click", () => assessTactics().catch(showError));
    $("#btn-run-calculator").addEventListener("click", () => runTacticalCalculator().catch(showError));

    // ---- plan copy, model provenance, products & merge resolution ----
    $("#btn-copy-scenario").addEventListener("click", () => copyScenarioToPeriod().catch(showError));
    $("#btn-attach-model").addEventListener("click", () => attachModelRun().catch(showError));
    $("#btn-create-product").addEventListener("click", () => createProduct().catch(showError));
    $("#btn-resolve-merge").addEventListener("click", () => resolveMerge().catch(showError));
    $("#ops-product-type").addEventListener("change", (event) => {
      if (event.target.value === "public_information") $("#ops-product-classification").value = "public";
    });

    // ---- incident import/export & offline map-pack controls ----
    $("#btn-import-incident").addEventListener("click", () => $("#ops-import-file").click());
    $("#btn-create-pack").addEventListener("click", () => createMapPackManifest().catch(showError));
    $("#btn-verify-pack").addEventListener("click", () => verifyMapPackManifest().catch(showError));
    $("#ops-import-file").addEventListener("change", (event) => {
      importIncidentFile(event.target.files?.[0]).catch(showError).finally(() => { event.target.value = ""; });
    });
    $("#btn-export-incident").addEventListener("click", () => {
      if (state.incidentId) window.location.href = `/api/operations/incidents/${state.incidentId}/export`;
    });

    // ---- keyboard shortcuts ----
    // Escape backs out of whichever of drawing/editing is active (the two
    // are mutually exclusive - see the state object's own comment); Enter
    // finishes a non-Point sketch in progress; Ctrl/Cmd+Z undoes the last
    // vertex of an in-progress sketch (not a general undo).
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { if (state.editingFeatureId) cancelEdit(); else cancelDraw(); }
      if (event.key === "Enter" && state.drawing && state.drawing.geometry !== "Point") finishDraw();
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && state.drawing) {
        event.preventDefault(); undoDraw();
      }
    });

    // Delegated clicks for buttons rendered dynamically inside popup/list
    // HTML (popupHtml(), the tactical-warnings list) - those buttons don't
    // exist yet when wireControls() runs, so they're matched by data
    // attribute at document level instead of getting their own listener.
    document.addEventListener("click", (event) => {
      const edit = event.target.closest("[data-ops-edit]"); if (edit) editFeature(edit.dataset.opsEdit).catch(showError);
      const del = event.target.closest("[data-ops-delete]"); if (del) removeFeature(del.dataset.opsDelete).catch(showError);
      const warning = event.target.closest("[data-warning-ack]"); if (warning) acknowledgeTacticalWarning(warning.dataset.warningAck).catch(showError);
    });

    // Every record-form field feeds the sketch-draft crash-recovery
    // snapshot (see persistSketchDraft() above) on each keystroke, not just
    // on save - so a crash mid-edit loses at most the last keystroke.
    ["#ops-feature-type", "#ops-feature-title", "#ops-feature-status", "#ops-source", "#ops-confidence", "#ops-observed-at", "#ops-observer",
      "#ops-valid-from", "#ops-valid-to", ...Object.values(RECORD_FIELDS)].forEach((selector) =>
      $(selector).addEventListener("input", persistSketchDraft));

    window.addEventListener("afterprint", () => {
      $("#ops-print-title").setAttribute("aria-hidden", "true");
      setTimeout(() => state.map.invalidateSize(), 50);
    });

    // Contributes "Add <type> here" to the map's generic right-click point
    // menu (app.js's showPointContextMenu/wireMapContextMenu) - skips the
    // sketch tool's own "click to place" step entirely by handing
    // saveGeometry() the right-clicked point directly, the exact path
    // drawClick() already uses for a Point geometry. Only offered with an
    // incident+period actually selected (nothing sensible to attach a
    // tactical marker to otherwise) and no sketch/edit already in progress
    // (draw and edit are mutually exclusive elsewhere in this file -
    // overwriting state.drawing here would silently discard an in-progress
    // multi-vertex sketch instead of respecting that invariant).
    onMapContextMenu(({ kind, latlng, addItem }) => {
      if (kind !== "point" || !state.incidentId || !state.periodId || state.drawing || state.editingFeatureId) return;
      const featureType = $("#ops-feature-type").value;
      const label = TYPE_LABELS[featureType] || featureType;
      addItem({
        label: `Add ${label} here`,
        action: () => {
          state.drawing = { featureType, geometry: "Point", points: [[latlng.lng, latlng.lat]] };
          saveGeometry({ type: "Point", coordinates: [latlng.lng, latlng.lat] }).catch(showError);
        },
      });
    });
  }

  // ------------------------------------------------------ errors & connectivity

  function showError(error) { console.error(error); setStatus(error.message || String(error), true); }

  async function checkConnectivity() {
    const pill = $("#ops-connectivity");
    try {
      const controller = new AbortController(); setTimeout(() => controller.abort(), 4000);
      const response = await fetch("/health", { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error();
      pill.textContent = navigator.onLine ? "local server connected" : "offline · local server connected";
      pill.className = "pill ok";
    } catch (_) {
      pill.textContent = "local server disconnected"; pill.className = "pill warn";
    }
  }

  // ----------------------------------------------------------------- init

  async function init(map) {
    state.map = map;
    // Registered with context.js so app.js's always-visible topbar print
    // button can call it without reaching into this module's own scope -
    // set synchronously here (before any await below) so it's guaranteed
    // ready by the time any button wired later in this same synchronous
    // pass could possibly be clicked.
    setPrintView(printOperationsMap);
    state.featureLayer = L.featureGroup().addTo(map);
    state.resourceLayer = L.featureGroup().addTo(map);
    state.vehicleTrackLayer = L.featureGroup().addTo(map);
    state.vehicleLayer = L.featureGroup().addTo(map);
    state.windLayer = L.featureGroup().addTo(map);
    state.windObservationLayer = L.featureGroup().addTo(map);
    state.sketchLayer = L.featureGroup().addTo(map);
    state.progressionLayer = L.featureGroup().addTo(map);
    // Wired before any await below, not after: every handler here only
    // touches state/DOM lazily when it actually runs, never at attachment
    // time, so there's no ordering requirement forcing this later - but
    // leaving it after ensureAuth()/meta/config used to mean every button
    // on the page existed and looked clickable for the full round-trip
    // time of those calls while doing precisely nothing when clicked, no
    // error, no feedback. On a fast localhost that's tens of milliseconds -
    // on a loaded LAN or a slow field laptop/tablet cold-starting the
    // server it's a real, reachable window - confirmed live via the
    // browser-driven test suite, which reproduced it deterministically.
    wireControls();
    await ensureAuth();
    if (state.auth.role === "public") { await showPublicProducts(); return; }
    state.meta = await api("/api/operations/meta");
    // Must come after meta (it reads the sprite URL from it) and before the
    // first renderFeatures(), or the initial markers draw as initials and only
    // pick up their symbols on the next redraw.
    await loadSprites();
    const config = await api("/api/config");
    config.basemaps.forEach((layer) => $("#ops-pack-layer").append(option(layer.id, layer.name)));
    config.basemaps.filter((layer) => layer.source_kind === "raster").forEach((layer) => $("#ops-terrain-source").append(option(layer.source_id, layer.name)));
    const typeSelect = $("#ops-feature-type");
    const ordered = [...state.meta.geometry.Point, ...state.meta.geometry.LineString, ...state.meta.geometry.Polygon];
    ordered.sort((a, b) => (TYPE_LABELS[a] || a).localeCompare(TYPE_LABELS[b] || b));
    ordered.forEach((type) => typeSelect.append(option(type, TYPE_LABELS[type] || type.replaceAll("_", " "))));
    ordered.forEach((type) => $("#ops-field-import-type").append(option(type, TYPE_LABELS[type] || type.replaceAll("_", " "))));
    typeSelect.value = "tactical_line";
    typeSelect.addEventListener("change", () => {
      const type = typeSelect.value;
      $("#ops-feature-status").value = PLAN_TYPES.has(type) ? "proposed" : (type === "confirmed_perimeter" ? "confirmed" : "observed");
    });
    if (!state.auth.enabled) $("#ops-operator").value = localStorage.getItem("nexfiremap.operator") || "local operator";
    $("#ops-mbtiles-acquired").value = new Date().toISOString().slice(0, 10);
    $("#ops-drone-captured").value = localDateTime(new Date().toISOString());
    $("#ops-wind-time").value = localDateTime(new Date().toISOString());
    const progressionEnd = new Date(), progressionStart = new Date(progressionEnd.getTime() - 24 * 3600000);
    $("#ops-progression-from").value = localDateTime(progressionStart.toISOString());
    $("#ops-progression-to").value = localDateTime(progressionEnd.toISOString());
    // wireControls() already ran once above, before ensureAuth() - it must
    // not run again here. Every handler it registers only touches
    // state/DOM lazily when it fires, never at attachment time, so a
    // second call here was never load-bearing for anything created since
    // the first call - it only doubled every listener in the operations
    // panel (draw/incident/period/scenario/resource/backup/drone/wind/
    // field-import/merge/map-pack controls, plus the document-level
    // delegated click handler), making every click or keydown fire its
    // handler twice: duplicate saves/PATCHes, doubled revision bumps,
    // double undo/redo on Ctrl+Z. Confirmed pre-existing in the baseline
    // (predates this file's comment pass) via `git show` on the initial
    // commit - not something introduced here.
    await loadAccounts();
    await loadIncidents();
    await restoreSketchDraft();
    await loadBackupStatus();
    setInterval(() => loadBackupStatus().catch(console.warn), 60000);
    setInterval(() => { if (state.incidentId) refreshVehicleTelemetry().catch(console.warn); }, 15000);
    await checkConnectivity(); setInterval(checkConnectivity, 15000);
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(console.warn);
  }

  whenMap((map) => init(map).catch(showError));
