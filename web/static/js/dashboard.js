const MODEL_LABELS = {
  arc: "⚡ Arc électrique",
  conveyor: "🏗️ Convoyeur (déchirure)",
  epi: "🦺 EPI (casque, masque, gilet)",
  fire_smoke: "🔥 Fumée / Feu",
  gloves_glasses: "🧤 Gants / lunettes / chute",
  load_control: "📦 Contrôle chargement",
  person_animal: "🚶 Personne / animal",
  vehicles: "🚗 Véhicules",
  systeme: "🩺 Incident technique",
};

const OPERATOR = "opérateur";

let cameras = [];
let cameraNames = [];
let lastCriticalId = null;
let alertsPage = 0;
const PAGE_SIZE = 25;

const el = (id) => document.getElementById(id);
const fmtTime = (iso) => new Date(iso).toLocaleString("fr-FR");

/* ── Navigation ── */

function setupNav() {
  document.querySelectorAll(".nav-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-link").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      el(`page-${btn.dataset.page}`).classList.add("active");
      const loaders = {
        dashboard: loadDashboard,
        alerts: loadAlertsTable,
        reports: loadReports,
        usecases: loadUseCases,
        system: loadSystem,
        zones: loadZonesPage,
        cameras: loadCameras,
      };
      loaders[btn.dataset.page]?.();
    });
  });
}

function goToPage(name) {
  document.querySelector(`.nav-link[data-page="${name}"]`)?.click();
}

/* ── Mur de caméras ── */

async function loadCameras() {
  const data = await (await fetch("/api/cameras")).json();
  cameras = data.cameras;
  cameraNames = cameras.map((c) => c.name);

  const grid = el("camera-grid");
  grid.innerHTML = "";

  if (!cameras.length) {
    grid.innerHTML = `<p class="muted">Aucune caméra configurée. Utilisez « Ajouter une caméra ».</p>`;
  }

  for (const cam of cameras) {
    const models = cam.models.map((m) => MODEL_LABELS[m] || m).join(" · ") || "aucun modèle";
    const zones = cam.zones?.length ? `📐 ${cam.zones.join(" · ")}` : "📐 plein cadre";
    const badge = !cam.enabled
      ? '<span class="offline-badge">EN PAUSE</span>'
      : cam.online
        ? '<span class="live-badge">LIVE</span>'
        : '<span class="offline-badge">HORS LIGNE</span>';
    const detail = cam.cycle_ms ? `${cam.cycle_ms} ms/cycle` : (cam.state || "");

    const tile = document.createElement("div");
    tile.className = "camera-tile";
    tile.id = `tile-${cam.name}`;
    tile.innerHTML = `
      <div class="camera-tile-header">
        <span>${cam.name}</span>
        ${badge}
      </div>
      <img id="feed-${cam.name}" src="/video/${cam.name}.jpg" alt="${cam.name}"
           onerror="this.style.opacity=0.2" onload="this.style.opacity=1" />
      <div class="camera-models">${models}</div>
      <div class="camera-zones muted">${zones} <span class="cycle">${detail}</span></div>
      <div class="camera-tile-actions">
        <button class="btn small ghost" data-edit="${cam.name}">Modifier</button>
        <button class="btn small ghost" data-zones="${cam.name}">Zones</button>
        <button class="btn small ghost danger" data-delete="${cam.name}">Supprimer</button>
      </div>
    `;
    grid.appendChild(tile);
  }

  grid.querySelectorAll("[data-edit]").forEach((b) =>
    b.addEventListener("click", () => openCameraForm(b.dataset.edit)));
  grid.querySelectorAll("[data-zones]").forEach((b) =>
    b.addEventListener("click", () => { goToPage("zones"); selectZoneCamera(b.dataset.zones); }));
  grid.querySelectorAll("[data-delete]").forEach((b) =>
    b.addEventListener("click", () => deleteCamera(b.dataset.delete)));

  el("sys-info").textContent =
    `${cameras.length} caméra${cameras.length > 1 ? "s" : ""} · ${Object.keys(MODEL_LABELS).length - 1} modèles`;
}

function refreshVideos() {
  for (const name of cameraNames) {
    const img = el(`feed-${name}`);
    if (img) img.src = `/video/${name}.jpg?t=${Date.now()}`;
  }
}

function setupCameraWall() {
  el("layout-switch").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    document.querySelectorAll("#layout-switch button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    el("camera-grid").className = `camera-grid cols-${btn.dataset.layout}`;
  });

  el("btn-fullscreen").addEventListener("click", () => {
    const wall = el("page-cameras");
    if (document.fullscreenElement) document.exitFullscreen();
    else wall.requestFullscreen?.();
  });
}

/* ── Gestion des caméras ── */

let editingCamera = null;

function openCameraForm(name = null) {
  editingCamera = name;
  const form = el("camera-form");
  form.hidden = false;
  el("camera-form-title").textContent = name ? `Modifier « ${name} »` : "Nouvelle caméra";
  el("cam-status").textContent = "";
  el("cam-preview").innerHTML = "";

  const models = el("cam-models");
  if (!models.children.length) {
    for (const [key, label] of Object.entries(MODEL_LABELS)) {
      if (key === "systeme") continue;
      const item = document.createElement("label");
      item.className = "zone-model";
      item.innerHTML = `<input type="checkbox" value="${key}" /> <span>${label}</span>`;
      models.appendChild(item);
    }
  }
  models.querySelectorAll("input").forEach((c) => (c.checked = false));

  const cam = cameras.find((c) => c.name === name);
  el("cam-name").value = cam?.name ?? "";
  el("cam-name").disabled = Boolean(name);
  el("cam-source").value = cam?.source ?? "";
  el("cam-fps").value = cam?.fps ?? "";
  el("cam-enabled").value = String(cam?.enabled ?? true);
  cam?.models.forEach((m) => {
    const box = models.querySelector(`input[value="${m}"]`);
    if (box) box.checked = true;
  });
  form.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function cameraFormPayload() {
  const source = el("cam-source").value.trim();
  return {
    source: /^\d+$/.test(source) ? Number(source) : source,
    models: [...el("cam-models").querySelectorAll("input:checked")].map((c) => c.value),
    fps: el("cam-fps").value ? Number(el("cam-fps").value) : null,
    enabled: el("cam-enabled").value === "true",
  };
}

function setupCameraForm() {
  el("btn-add-camera").addEventListener("click", () => openCameraForm());
  el("cam-cancel").addEventListener("click", () => (el("camera-form").hidden = true));

  el("cam-test").addEventListener("click", async () => {
    const status = el("cam-status");
    const source = cameraFormPayload().source;
    if (source === "") { status.textContent = "Renseignez une source."; return; }
    status.textContent = "Test en cours…";
    const r = await fetch("/api/cameras/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
    const res = await r.json();
    if (res.ok) {
      status.className = "form-status ok";
      status.textContent = `Connexion établie — ${res.kind}, ${res.width}×${res.height}`;
      el("cam-preview").innerHTML = `<p class="muted">Aperçu disponible une fois la caméra enregistrée et le pipeline lancé.</p>`;
    } else {
      status.className = "form-status error";
      status.textContent = res.error;
    }
  });

  el("cam-save").addEventListener("click", async () => {
    const status = el("cam-status");
    const name = el("cam-name").value.trim();
    if (!name) { status.textContent = "Donnez un nom à la caméra."; return; }

    const r = await fetch(`/api/cameras/${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cameraFormPayload()),
    });
    if (r.ok) {
      status.className = "form-status ok";
      status.textContent = "Enregistrée. Le pipeline la prend en compte dans quelques secondes.";
      el("camera-form").hidden = true;
      await loadCameras();
    } else {
      const err = await r.json().catch(() => ({}));
      status.className = "form-status error";
      status.textContent = err.detail || `Échec (${r.status})`;
    }
  });
}

async function deleteCamera(name) {
  if (!confirm(`Supprimer la caméra « ${name} » ? Ses zones et son historique sont conservés.`)) return;
  await fetch(`/api/cameras/${encodeURIComponent(name)}`, { method: "DELETE" });
  await loadCameras();
}

/* ── Tableau de bord ── */

async function loadDashboard() {
  const [summary, timeline, alerts, quality] = await Promise.all([
    (await fetch("/api/stats/summary")).json(),
    (await fetch("/api/stats/timeline?hours=24")).json(),
    (await fetch("/api/alerts?limit=8")).json(),
    (await fetch("/api/stats/quality?days=30")).json(),
  ]);

  el("kpi-24h").textContent = summary.total_24h;
  el("kpi-crit").textContent = summary.critiques_24h;
  el("kpi-unack").textContent = summary.non_acquittees;

  const mtta = quality.delai_prise_en_charge_s;
  el("kpi-mtta").textContent = mtta == null ? "–" : mtta < 90 ? `${mtta} s` : `${Math.round(mtta / 60)} min`;

  const parCam = Object.values(quality.par_camera || {});
  const pire = parCam.length ? Math.max(...parCam.map((c) => c.fausses_par_jour)) : 0;
  const kpiFalse = el("kpi-false");
  kpiFalse.textContent = parCam.length ? pire.toFixed(1) : "–";
  kpiFalse.parentElement.classList.toggle("kpi-crit", pire > 2);

  const badge = el("unack-badge");
  badge.textContent = summary.non_acquittees > 0 ? summary.non_acquittees : "";

  renderTimeline(timeline);
  renderBars("model-bars", summary.par_modele_7j, (k) => MODEL_LABELS[k] || k);
  renderBars("zone-bars", summary.par_zone_7j, (k) => k || "plein cadre");
  renderQuality(quality);
  renderDashAlerts(alerts.items);
}

function renderTimeline(timeline) {
  const max = Math.max(1, ...timeline.map((t) => t.total));
  el("timeline-chart").innerHTML = timeline.map((t) => `
    <div class="bar-col" title="${t.heure} — ${t.total} alerte(s), ${t.critique} critique(s)">
      <div class="bar ${t.critique ? "crit" : ""}" style="height:${(t.total / max) * 100}%"></div>
      <div class="bar-label">${t.heure}</div>
    </div>`).join("");
}

function renderBars(target, data, labeller) {
  const entries = Object.entries(data || {}).sort((a, b) => b[1] - a[1]);
  if (!entries.length) { el(target).innerHTML = '<p class="muted">Aucune donnée.</p>'; return; }
  const max = Math.max(...entries.map(([, v]) => v));
  el(target).innerHTML = entries.map(([k, v]) => `
    <div class="hbar-row">
      <div class="hbar-name">${labeller(k)}</div>
      <div class="hbar-track"><div class="hbar-fill" style="width:${(v / max) * 100}%"></div></div>
      <div class="hbar-val">${v}</div>
    </div>`).join("");
}

function renderQuality(quality) {
  const entries = Object.entries(quality.par_modele || {});
  if (!entries.length) {
    el("quality-bars").innerHTML =
      '<p class="muted">Aucune alerte marquée pour l\'instant. Le bouton « Fausse alerte » alimente cet indicateur.</p>';
    return;
  }
  el("quality-bars").innerHTML = entries
    .sort((a, b) => b[1].taux_faux - a[1].taux_faux)
    .map(([model, s]) => {
      const pct = Math.round(s.taux_faux * 100);
      const cls = pct >= 40 ? "bad" : pct >= 15 ? "warn" : "good";
      return `
        <div class="hbar-row">
          <div class="hbar-name">${MODEL_LABELS[model] || model}</div>
          <div class="hbar-track"><div class="hbar-fill ${cls}" style="width:${pct}%"></div></div>
          <div class="hbar-val">${pct}% <span class="muted">(${s.fausses}/${s.alertes})</span></div>
        </div>`;
    }).join("");
}

function renderDashAlerts(alerts) {
  const box = el("dash-alerts");
  box.innerHTML = "";
  if (!alerts.length) { box.innerHTML = '<p class="muted">Aucune alerte.</p>'; return; }
  alerts.forEach((a) => box.appendChild(alertRow(a, false)));
}

/* ── Alertes ── */

function alertFilters() {
  const params = new URLSearchParams();
  const map = {
    model: "filter-model", camera: "filter-camera", zone: "filter-zone",
    severity: "filter-severity", acknowledged: "filter-ack",
    false_positive: "filter-false", since_hours: "filter-period",
  };
  for (const [key, id] of Object.entries(map)) {
    const value = el(id)?.value;
    if (value) params.set(key, value);
  }
  const label = el("filter-label")?.value.trim();
  if (label) params.set("label", label);
  const shift = el("filter-hours")?.value;
  if (shift) {
    const [from, to] = shift.split("-");
    params.set("hour_from", from);
    params.set("hour_to", to);
  }
  return params;
}

async function loadAlertsTable() {
  const params = alertFilters();
  params.set("limit", PAGE_SIZE);
  params.set("offset", alertsPage * PAGE_SIZE);

  const data = await (await fetch(`/api/alerts?${params}`)).json();
  const table = el("alerts-table");
  table.innerHTML = "";

  if (!data.items.length) {
    table.innerHTML = '<p class="muted">Aucune alerte ne correspond à ces critères.</p>';
  } else {
    data.items.forEach((a) => table.appendChild(alertRow(a, true)));
  }
  renderPager(data);
}

function renderPager(data) {
  const pages = Math.ceil(data.total / PAGE_SIZE);
  const pager = el("alerts-pager");
  if (pages <= 1) {
    pager.innerHTML = `<span class="muted">${data.total} alerte(s)</span>`;
    return;
  }
  pager.innerHTML = `
    <button class="btn small ghost" ${alertsPage === 0 ? "disabled" : ""} id="prev-page">← Précédent</button>
    <span class="muted">Page ${alertsPage + 1} sur ${pages} — ${data.total} alerte(s)</span>
    <button class="btn small ghost" ${alertsPage + 1 >= pages ? "disabled" : ""} id="next-page">Suivant →</button>`;
  el("prev-page")?.addEventListener("click", () => { alertsPage--; loadAlertsTable(); });
  el("next-page")?.addEventListener("click", () => { alertsPage++; loadAlertsTable(); });
}

function alertRow(a, withAction) {
  const row = document.createElement("div");
  row.className = "alert-row" + (a.false_positive ? " is-false" : "");
  const thumb = a.snapshot
    ? `<img class="alert-thumb" src="/api/snapshot?path=${encodeURIComponent(a.snapshot)}" onerror="this.style.opacity=0.2" />`
    : '<div class="alert-thumb"></div>';

  let action = "";
  if (withAction) {
    const ack = a.acknowledged
      ? `<button class="btn small ghost" disabled>✓ ${a.ack_by || "traitée"}</button>`
      : `<button class="btn small" data-ack="${a.id}">Prendre en charge</button>`;
    const flag = a.false_positive
      ? `<button class="btn small ghost" data-true="${a.id}" title="Revenir sur ce jugement">↩ Vraie alerte</button>`
      : `<button class="btn small ghost" data-false="${a.id}" title="Le système s'est trompé — sert aussi à améliorer le modèle">✗ Fausse alerte</button>`;
    action = ack + flag;
  }

  const clip = a.clip
    ? ` · <a class="clip-link" href="/api/clip?path=${encodeURIComponent(a.clip)}" target="_blank">🎬 clip vidéo</a>`
    : "";
  const zone = a.zone ? ` · <span class="zone-tag">${a.zone}</span>` : "";
  const faux = a.false_positive ? ' <span class="false-tag">fausse</span>' : "";

  row.innerHTML = `
    ${thumb}
    <div class="alert-main">
      <div class="l1">${a.label}${faux} <span class="muted">(${MODEL_LABELS[a.model] || a.model})</span></div>
      <div class="l2">${a.camera}${zone}${clip}</div>
    </div>
    <div><span class="sev ${a.severity}">${a.severity}</span></div>
    <div>${a.confidence.toFixed(2)}</div>
    <div class="muted" style="font-size:12px">${fmtTime(a.timestamp)}</div>
    <div class="alert-actions">${action}</div>
  `;

  row.querySelector("[data-ack]")?.addEventListener("click", (e) => ackAlert(a.id, e.target));
  row.querySelector("[data-false]")?.addEventListener("click", () => flagAlert(a.id, true));
  row.querySelector("[data-true]")?.addEventListener("click", () => flagAlert(a.id, false));
  return row;
}

async function ackAlert(id, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  await fetch(`/api/alerts/${id}/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operator: OPERATOR }),
  });
  loadAlertsTable();
}

async function flagAlert(id, isFalse) {
  await fetch(`/api/alerts/${id}/false`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_false: isFalse, operator: OPERATOR }),
  });
  loadAlertsTable();
}

/* ── Cas d'usage ── */

async function loadUseCases() {
  const data = await (await fetch("/api/usecases")).json();
  const labels = { operationnel: "Opérationnel", partiel: "Partiel", a_entrainer: "À entraîner" };
  el("usecases-table").innerHTML = data.usecases.map((uc) => `
    <div class="uc-row">
      <div class="uc-num">${uc.num}</div>
      <div>
        <div class="uc-title"><span class="uc-dot ${uc.etat}"></span> ${uc.titre}</div>
        <div class="muted">${uc.model ? (MODEL_LABELS[uc.model] || uc.model) : "aucun modèle"} · ${uc.classes.join(", ")}</div>
        ${uc.note ? `<div class="uc-note">${uc.note}</div>` : ""}
      </div>
      <div class="uc-state">${labels[uc.etat]}</div>
      <div class="uc-live">${uc.detect ? "détection active" : "détection inactive"}</div>
    </div>`).join("");
}

/* ── Rapports ── */

async function loadReports() {
  const [summary, quality] = await Promise.all([
    (await fetch("/api/stats/summary")).json(),
    (await fetch("/api/stats/quality?days=30")).json(),
  ]);
  renderBars("severity-bars", summary.par_severite_7j, (k) => k);

  const entries = Object.entries(quality.par_camera || {});
  if (!entries.length) {
    el("false-by-camera").innerHTML = '<p class="muted">Aucune donnée.</p>';
    return;
  }
  el("false-by-camera").innerHTML = entries.map(([cam, s]) => {
    const depasse = s.fausses_par_jour > 2;
    return `
      <div class="hbar-row">
        <div class="hbar-name">${cam}</div>
        <div class="hbar-track"><div class="hbar-fill ${depasse ? "bad" : "good"}"
             style="width:${Math.min(100, (s.fausses_par_jour / 4) * 100)}%"></div></div>
        <div class="hbar-val">${s.fausses_par_jour}/j</div>
      </div>`;
  }).join("");
}

/* ── Système ── */

async function loadSystem() {
  const h = await (await fetch("/api/health")).json();
  const m = h.machine || {};

  const kpi = (value, label, crit = false) =>
    `<div class="kpi ${crit ? "kpi-crit" : ""}"><div class="kpi-value">${value}</div><div class="kpi-label">${label}</div></div>`;

  el("system-kpis").innerHTML = [
    kpi(h.pipeline.running ? "Actif" : "Arrêté", "Pipeline de détection", !h.pipeline.running),
    kpi(`${h.cameras_actives}/${h.cameras_configurees}`, "Caméras actives"),
    kpi(m.cpu_percent != null ? `${m.cpu_percent}%` : "–", "Processeur", (m.cpu_percent ?? 0) > 90),
    kpi(m.memory_percent != null ? `${m.memory_percent}%` : "–", "Mémoire", (m.memory_percent ?? 0) > 90),
    kpi(m.disk_free_gb != null ? `${m.disk_free_gb} Go` : "–", "Disque libre", (m.disk_free_gb ?? 99) < 5),
  ].join("");

  const rows = Object.entries(h.cameras || {});
  el("system-cameras").innerHTML = rows.length
    ? rows.map(([name, c]) => `
      <div class="sys-row">
        <div class="sys-name">${name}</div>
        <div><span class="state-tag ${(c.state || "").replace(/\s/g, "-")}">${c.state || "inconnu"}</span></div>
        <div class="muted">${c.cycle_ms ? `${c.cycle_ms} ms/cycle` : ""} ${c.modeles_actifs != null ? `· ${c.modeles_actifs} modèles` : ""}</div>
        <div class="muted">${c.error || ""}</div>
      </div>`).join("")
    : '<p class="muted">Le pipeline n\'a encore publié aucun état.</p>';
}

async function refreshPipelineState() {
  try {
    const h = await (await fetch("/api/health")).json();
    const running = h.pipeline.running;
    el("pipeline-dot").className = `status-dot ${running ? "" : "down"}`;
    el("pipeline-state").textContent = running ? "Détection active" : "Détection arrêtée";
    el("system-badge").textContent = running ? "" : "!";
  } catch {
    el("pipeline-state").textContent = "Interface injoignable";
  }
}

/* ── Alerte critique : bandeau + mise en avant de la caméra ── */

async function watchCriticalAlerts() {
  const data = await (await fetch("/api/alerts?limit=1&severity=critique&since_hours=1")).json();
  const latest = data.items[0];
  if (!latest) return;

  if (lastCriticalId === null) { lastCriticalId = latest.id; return; }
  if (latest.id === lastCriticalId) return;
  lastCriticalId = latest.id;

  showToast(`${latest.label} — ${latest.camera}${latest.zone ? ` (${latest.zone})` : ""}`);

  if (el("auto-focus")?.checked) {
    document.querySelectorAll(".camera-tile").forEach((t) => t.classList.remove("alerting"));
    el(`tile-${latest.camera}`)?.classList.add("alerting");
    el(`tile-${latest.camera}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function showToast(message) {
  const toast = el("toast");
  toast.textContent = `🚨 ${message}`;
  toast.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => (toast.hidden = true), 12000);
}

/* ── Paramètres ── */

async function loadSettings() {
  const settings = await (await fetch("/api/settings")).json();
  const list = el("controls-list");
  list.innerHTML = "";
  for (const [model, values] of Object.entries(settings)) {
    const row = document.createElement("div");
    row.className = "control-row";
    row.innerHTML = `<div class="control-name">${MODEL_LABELS[model] || model}</div>`;
    row.appendChild(makeToggle(model, "detect", values.detect, "Détection"));
    row.appendChild(makeToggle(model, "alert", values.alert, "Alertes"));
    list.appendChild(row);
  }
}

function makeToggle(model, key, value, text) {
  const label = document.createElement("label");
  label.className = "switch";
  label.innerHTML = `<input type="checkbox" ${value ? "checked" : ""} /><span class="slider"></span><span class="switch-text">${text}</span>`;
  label.querySelector("input").addEventListener("change", async (e) => {
    await fetch(`/api/settings/${model}/${key}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: e.target.checked }),
    });
  });
  return label;
}

/* ── Éditeur de zones ── */

const zoneEditor = { camera: null, zones: [], draft: [] };

async function loadZonesPage() {
  const select = el("zone-camera");
  select.innerHTML = "";
  for (const name of cameraNames) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  }
  if (!select.dataset.bound) {
    select.addEventListener("change", () => selectZoneCamera(select.value));
    select.dataset.bound = "1";
  }

  const models = el("zone-models");
  if (!models.children.length) {
    for (const [key, label] of Object.entries(MODEL_LABELS)) {
      if (key === "systeme") continue;
      const item = document.createElement("label");
      item.className = "zone-model";
      item.innerHTML = `<input type="checkbox" value="${key}" /> <span>${label}</span>`;
      models.appendChild(item);
    }
  }
  await selectZoneCamera(zoneEditor.camera || select.value || cameraNames[0]);
}

async function selectZoneCamera(camera) {
  if (!camera) return;
  zoneEditor.camera = camera;
  zoneEditor.draft = [];
  el("zone-camera").value = camera;
  el("zone-frame").src = `/video/${camera}.jpg?t=${Date.now()}`;
  const data = await (await fetch(`/api/zones/${encodeURIComponent(camera)}`)).json();
  zoneEditor.zones = data.zones || [];
  renderZoneList();
  drawZones();
}

function syncCanvasSize() {
  const img = el("zone-frame");
  const canvas = el("zone-canvas");
  if (!img.clientWidth) return false;
  canvas.width = img.clientWidth;
  canvas.height = img.clientHeight;
  return true;
}

function drawZones() {
  const canvas = el("zone-canvas");
  if (!syncCanvasSize()) return;
  const ctx = canvas.getContext("2d");
  const { width: w, height: h } = canvas;
  ctx.clearRect(0, 0, w, h);

  for (const zone of zoneEditor.zones) {
    const exclusion = zone.type === "exclusion";
    drawPolygon(ctx, zone.polygon, w, h,
      exclusion ? "rgba(239,68,68,0.95)" : "rgba(255,170,0,0.9)",
      exclusion ? "rgba(239,68,68,0.18)" : "rgba(255,170,0,0.15)",
      zone.name);
  }
  if (zoneEditor.draft.length) {
    drawPolygon(ctx, zoneEditor.draft, w, h, "rgba(34,197,94,0.95)", "rgba(34,197,94,0.15)", null, true);
  }
}

function drawPolygon(ctx, polygon, w, h, stroke, fill, label, showPoints) {
  if (!polygon.length) return;
  ctx.beginPath();
  polygon.forEach(([x, y], i) => {
    const px = x * w, py = y * h;
    i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
  });
  if (polygon.length >= 3) {
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
  }
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 2;
  ctx.stroke();

  if (showPoints) {
    for (const [x, y] of polygon) {
      ctx.beginPath();
      ctx.arc(x * w, y * h, 4, 0, Math.PI * 2);
      ctx.fillStyle = stroke;
      ctx.fill();
    }
  }
  if (label) {
    const [x, y] = polygon[0];
    ctx.fillStyle = stroke;
    ctx.font = "13px system-ui, sans-serif";
    ctx.fillText(label, x * w + 4, y * h - 6);
  }
}

function describeZone(zone) {
  const parts = [`${zone.polygon.length} sommets`];
  parts.push(zone.models?.length ? zone.models.map((m) => MODEL_LABELS[m] || m).join(" · ") : "tous les modèles");
  if (zone.schedule?.start && zone.schedule?.end) parts.push(`${zone.schedule.start} → ${zone.schedule.end}`);
  if (zone.conf) parts.push(`seuil ${zone.conf}`);
  if (zone.cooldown) parts.push(`délai ${zone.cooldown}s`);
  return parts.join(" · ");
}

function renderZoneList() {
  const list = el("zone-list");
  list.innerHTML = "";
  if (!zoneEditor.zones.length) {
    list.innerHTML = '<p class="muted">Aucune zone : la caméra est analysée en entier.</p>';
    return;
  }
  zoneEditor.zones.forEach((zone, index) => {
    const row = document.createElement("div");
    row.className = "zone-item";
    row.innerHTML = `
      <div>
        <div class="zone-item-name">
          ${zone.name}
          <span class="zone-kind ${zone.type === "exclusion" ? "excl" : ""}">
            ${zone.type === "exclusion" ? "masque" : "surveillée"}
          </span>
        </div>
        <div class="muted" style="font-size:12px">${describeZone(zone)}</div>
      </div>
      <button class="btn small ghost">Supprimer</button>`;
    row.querySelector("button").addEventListener("click", () => {
      zoneEditor.zones.splice(index, 1);
      renderZoneList();
      drawZones();
    });
    list.appendChild(row);
  });
}

function setupZoneEditor() {
  const canvas = el("zone-canvas");

  canvas.addEventListener("click", (e) => {
    const rect = canvas.getBoundingClientRect();
    // Coordonnées normalisées : indépendantes de la taille d'affichage et de
    // la résolution de la caméra.
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    zoneEditor.draft.push([+x.toFixed(4), +y.toFixed(4)]);
    el("zone-hint").textContent =
      `${zoneEditor.draft.length} sommet(s) posé(s)` +
      (zoneEditor.draft.length >= 3 ? " — nommez la zone puis « Ajouter »." : " — 3 minimum.");
    drawZones();
  });

  el("zone-undo").addEventListener("click", () => { zoneEditor.draft.pop(); drawZones(); });
  el("zone-clear").addEventListener("click", () => { zoneEditor.draft = []; drawZones(); });

  el("zone-add").addEventListener("click", () => {
    const status = el("zone-status");
    const name = el("zone-name").value.trim();
    if (zoneEditor.draft.length < 3) { status.textContent = "Tracez au moins 3 sommets sur l'image."; return; }
    if (!name) { status.textContent = "Donnez un nom à la zone."; return; }

    const start = el("zone-start").value;
    const end = el("zone-end").value;
    const zone = {
      name,
      polygon: zoneEditor.draft,
      type: el("zone-type").value,
      models: [...el("zone-models").querySelectorAll("input:checked")].map((c) => c.value),
    };
    if (start && end) zone.schedule = { start, end };
    if (el("zone-conf").value) zone.conf = Number(el("zone-conf").value);
    if (el("zone-cooldown").value) zone.cooldown = Number(el("zone-cooldown").value);

    zoneEditor.zones.push(zone);
    zoneEditor.draft = [];
    el("zone-name").value = "";
    el("zone-conf").value = "";
    el("zone-cooldown").value = "";
    el("zone-models").querySelectorAll("input:checked").forEach((c) => (c.checked = false));
    status.textContent = "Zone ajoutée — pensez à enregistrer.";
    renderZoneList();
    drawZones();
  });

  el("zone-save").addEventListener("click", async () => {
    const status = el("zone-status");
    const r = await fetch(`/api/zones/${encodeURIComponent(zoneEditor.camera)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zones: zoneEditor.zones }),
    });
    if (r.ok) {
      const data = await r.json();
      status.textContent = `Enregistré : ${data.zones} zone(s). Appliqué au prochain cycle du pipeline.`;
    } else {
      const err = await r.json().catch(() => ({}));
      status.textContent = `Échec : ${err.detail || r.status}`;
    }
  });

  el("zone-frame").addEventListener("load", drawZones);
  window.addEventListener("resize", drawZones);
}

/* ── Filtres ── */

function setupFilters() {
  const ids = ["filter-model", "filter-camera", "filter-zone", "filter-severity",
               "filter-ack", "filter-false", "filter-period", "filter-hours", "filter-label"];
  for (const id of ids) {
    el(id)?.addEventListener("change", () => { alertsPage = 0; loadAlertsTable(); });
  }
  el("filter-label")?.addEventListener("input", debounce(() => { alertsPage = 0; loadAlertsTable(); }, 350));
}

function debounce(fn, delay) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); };
}

async function populateFilters() {
  const models = el("filter-model");
  for (const [key, label] of Object.entries(MODEL_LABELS)) {
    models.insertAdjacentHTML("beforeend", `<option value="${key}">${label}</option>`);
  }
  const cams = el("filter-camera");
  const zonesSelect = el("filter-zone");
  const zones = new Set();
  for (const cam of cameras) {
    cams.insertAdjacentHTML("beforeend", `<option value="${cam.name}">${cam.name}</option>`);
    cam.zones?.forEach((z) => zones.add(z));
  }
  for (const z of zones) {
    zonesSelect.insertAdjacentHTML("beforeend", `<option value="${z}">${z}</option>`);
  }
}

/* ── Démarrage ── */

async function init() {
  setupNav();
  setupFilters();
  setupZoneEditor();
  setupCameraForm();
  setupCameraWall();

  await loadCameras();
  await populateFilters();
  await loadSettings();
  await loadDashboard();
  await refreshPipelineState();

  setInterval(refreshVideos, 700);
  setInterval(loadDashboard, 15000);
  setInterval(refreshPipelineState, 10000);
  setInterval(watchCriticalAlerts, 5000);
  setInterval(loadCameras, 30000);
}

init();
