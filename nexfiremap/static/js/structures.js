/* Temporal building exposure for completed spread/ensemble analyses. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const state = { map: null, analysis: null, exposure: null, layer: null, token: 0 };

  const ramp = [
    { max: 0, color: "#b70f2a", label: "reached" },
    { max: 2, color: "#e3342f", label: "≤2 h" },
    { max: 6, color: "#f06b24", label: "≤6 h" },
    { max: 12, color: "#f2b134", label: "≤12 h" },
    { max: 24, color: "#d4c84b", label: "≤24 h" },
    { max: 48, color: "#9abf67", label: "≤48 h" },
  ];

  function colorFor(hours) {
    if (hours == null) return "#7d8490";
    for (const step of ramp) if (hours <= step.max) return step.color;
    return "#7d8490";
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  async function fetchJson(url) {
    const response = await fetch(url);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  }

  async function waitForJob(jobId) {
    const deadline = Date.now() + 120000;
    // A structure scan can run for a while (Overpass can be slow), so this
    // polls over a real span of time on real network links - a single
    // dropped packet or momentary 5xx shouldn't blow up a scan that's still
    // happily running server-side. Mirrors app.js's waitForJob: fail fast on
    // 404 (permanent), tolerate a handful of consecutive transient failures
    // (HTTP or network-level) otherwise.
    let consecutiveFailures = 0;
    while (Date.now() < deadline) {
      let res;
      try {
        res = await fetch(`/api/jobs/${jobId}`);
      } catch (networkError) {
        consecutiveFailures += 1;
        if (consecutiveFailures >= 5) throw networkError;
        await new Promise((resolve) => setTimeout(resolve, 1000));
        continue;
      }
      if (res.status === 404) throw new Error(`job ${jobId} not found (it may have been purged)`);
      if (!res.ok) {
        consecutiveFailures += 1;
        if (consecutiveFailures >= 5) throw new Error(`server returned HTTP ${res.status} while checking job ${jobId}`);
        await new Promise((resolve) => setTimeout(resolve, 1000));
        continue;
      }
      consecutiveFailures = 0;
      const job = await res.json();
      if (job.status === "done") return;
      if (job.status === "error") throw new Error(job.error || "structure scan failed");
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    throw new Error("timed out while caching structures");
  }

  function popup(feature) {
    const p = feature.properties;
    const timing = p.impact_hours == null ? "Not reached in the model surface" :
      (p.impact_hours <= 0 ? "Reached at model reference time" : `Median impact in ${p.impact_hours.toFixed(1)} h`);
    const uncertainty = p.earliest_hours != null && p.latest_hours != null && p.earliest_hours !== p.latest_hours
      ? `<br>Ensemble range: ${p.earliest_hours.toFixed(1)}-${p.latest_hours.toFixed(1)} h` : "";
    return `<div class="structure-popup"><strong>${escapeHtml(p.name)}</strong>` +
      `${p.address ? `<br>${escapeHtml(p.address)}` : ""}<br>${escapeHtml(p.category)} · ${escapeHtml(p.building || "building")}` +
      `<hr><strong>${escapeHtml(timing)}</strong>${uncertainty}` +
      `<br><small>Modelled scenario exposure only; not confirmed damage or evacuation status.</small></div>`;
  }

  function render() {
    state.layer.clearLayers();
    if (!state.exposure || !$("#structures-toggle").checked) return;
    const horizon = Number($("#structure-horizon").value);
    const affected = state.exposure.features.filter((f) =>
      f.properties.impact_hours != null && f.properties.impact_hours <= horizon
    );
    affected.forEach((feature) => {
      const p = feature.properties, color = colorFor(p.impact_hours);
      const item = L.geoJSON(feature, {
        style: { color: p.category === "critical" ? "#ffffff" : color,
          weight: p.category === "critical" ? 3 : 1.5, fillColor: color, fillOpacity: 0.72 },
        pointToLayer: (_f, latlng) => L.circleMarker(latlng, {
          radius: p.category === "critical" ? 8 : 5, color: "#fff", weight: 2,
          fillColor: color, fillOpacity: 0.9,
        }),
      }).bindPopup(popup(feature));
      state.layer.addLayer(item);
    });

    const houses = affected.filter((f) => f.properties.category === "residential").length;
    const critical = affected.filter((f) => f.properties.category === "critical").length;
    const allFinite = state.exposure.features.filter((f) => f.properties.impact_hours != null).length;
    $("#structure-summary").innerHTML =
      `<strong>${affected.length.toLocaleString()}</strong><span>structures by +${horizon} h</span>` +
      `<strong>${houses.toLocaleString()}</strong><span>residential / houses</span>` +
      `<strong>${critical.toLocaleString()}</strong><span>critical facilities</span>` +
      `<small>${allFinite.toLocaleString()} structures reached across the complete model horizon.</small>`;
  }

  async function loadExposure({ scan = false } = {}) {
    if (!state.analysis) return;
    const token = ++state.token;
    const status = $("#structure-status");
    status.textContent = scan ? "Caching building footprints from OpenStreetMap…" : "Reading cached building exposure…";
    $("#btn-structures-refresh").disabled = true;
    try {
      let payload = await fetchJson(`/api/structures/exposure?job_id=${state.analysis.jobId}&autofetch=${scan}`);
      if (token !== state.token) return;
      if (payload.meta.scan_job_id) {
        await waitForJob(payload.meta.scan_job_id);
        if (token !== state.token) return;
        payload = await fetchJson(`/api/structures/exposure?job_id=${state.analysis.jobId}`);
      }
      if (token !== state.token) return;
      state.exposure = payload;
      $("#structures-toggle").checked = true;
      render();
      status.textContent = payload.features.length
        ? `${payload.features.length.toLocaleString()} cached structures assessed · model reference ${new Date(payload.meta.reference_ts * 1000).toLocaleString()} · scenario timing, not observed damage.`
        : (payload.meta.structure_cache_fresh
          ? "No mapped building footprints were found in this model area."
          : "No local structures cached for this area. Use “cache / refresh buildings” while connectivity is available; later assessments work offline.");
    } catch (error) {
      if (token === state.token) status.textContent = `Structure exposure unavailable: ${error.message}`;
    } finally {
      if (token === state.token) $("#btn-structures-refresh").disabled = false;
    }
  }

  function setAnalysis(detail) {
    state.token += 1;
    state.analysis = detail;
    state.exposure = null;
    state.layer?.clearLayers();
    const section = $("#structure-exposure");
    section.hidden = !detail;
    if (detail) loadExposure();
  }

  function init(map) {
    if (state.map) return;
    state.map = map;
    document.documentElement.dataset.structuresReady = "true";
    state.layer = L.featureGroup().addTo(map);
    $("#structures-toggle").addEventListener("change", render);
    $("#structure-horizon").addEventListener("input", (event) => {
      $("#structure-horizon-out").textContent = `${event.target.value} h`;
      render();
    });
    $("#btn-structures-refresh").addEventListener("click", () => loadExposure({ scan: true }));
    if (window.NexFiremapSpreadAnalysis) setAnalysis(window.NexFiremapSpreadAnalysis);
  }

  window.addEventListener("nexfiremap:spread-analysis", (event) => setAnalysis(event.detail));
  if (window.NexFiremapMap) init(window.NexFiremapMap);
  else window.addEventListener("nexfiremap:ready", (event) => init(event.detail.map), { once: true });
})();
