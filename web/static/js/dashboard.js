const MODEL_LABELS = {
  arc: "⚡ Arc électrique",
  conveyor: "🏗️ Convoyeur (déchirure)",
  epi: "🦺 EPI (casque, masque, gilet)",
  fire_smoke: "🔥 Fumée / Feu",
  gloves_glasses: "🧤 Gants / lunettes / chute",
  load_control: "📦 Contrôle chargement",
  person_animal: "🚶 Personne / animal",
  vehicles: "🚗 Véhicules",
};

let cameraNames = [];

/* ── Navigation ── */
function setupNav() {
  document.querySelectorAll(".nav-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-link").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`page-${btn.dataset.page}`).classList.add("active");
      if (btn.dataset.page === "alerts") loadAlertsTable();
      if (btn.dataset.page === "dashboard") loadDashboard();
      if (btn.dataset.page === "reports") loadReports();
      if (btn.dataset.page === "usecases") loadUseCases();
    });
  });
}

/* ── Caméras ── */
async function loadCameras() {
  const res = await fetch("/api/cameras");
  const data = await res.json();
  cameraNames = data.cameras.map((c) => c.name);
  const grid = document.getElementById("camera-grid");
  grid.innerHTML = "";
  for (const cam of data.cameras) {
    const tile = document.createElement("div");
    tile.className = "camera-tile";
    const models = cam.models.map((m) => MODEL_LABELS[m] || m).join(" · ");
    tile.innerHTML = `
      <div class="camera-tile-header">
        <span>${cam.name}</span>
        ${cam.online ? '<span class="live-badge">LIVE</span>' : '<span class="offline-badge">HORS LIGNE</span>'}
      </div>
      <img id="feed-${cam.name}" src="/video/${cam.name}.jpg" alt="${cam.name}"
           onerror="this.style.opacity=0.25" onload="this.style.opacity=1" />
      <div class="camera-models">${models}</div>
    `;
    grid.appendChild(tile);
  }
  document.getElementById("sys-info").textContent =
    `${data.cameras.length} caméra${data.cameras.length > 1 ? "s" : ""} · 7 modèles IA`;
}

function refreshVideos() {
  for (const name of cameraNames) {
    const img = document.getElementById(`feed-${name}`);
    if (img) img.src = `/video/${name}.jpg?t=${Date.now()}`;
  }
}

/* ── Tableau de bord ── */
async function loadDashboard() {
  const [summaryRes, timelineRes, alertsRes] = await Promise.all([
    fetch("/api/stats/summary"),
    fetch("/api/stats/timeline?hours=24"),
    fetch("/api/alerts?limit=8"),
  ]);
  const summary = await summaryRes.json();
  const timeline = await timelineRes.json();
  const alerts = await alertsRes.json();

  document.getElementById("kpi-24h").textContent = summary.total_24h;
  document.getElementById("kpi-crit").textContent = summary.critiques_24h;
  document.getElementById("kpi-unack").textContent = summary.non_acquittees;
  document.getElementById("kpi-7d").textContent = summary.total_7d;

  const badge = document.getElementById("unack-badge");
  badge.textContent = summary.non_acquittees > 0 ? summary.non_acquittees : "";

  renderTimeline(timeline);
  renderModelBars(summary.par_modele_7j);
  renderDashAlerts(alerts);
}

function renderTimeline(timeline) {
  const chart = document.getElementById("timeline-chart");
  chart.innerHTML = "";
  const max = Math.max(1, ...timeline.map((t) => t.total));
  // n'afficher qu'une étiquette sur 4 pour la lisibilité
  timeline.forEach((t, i) => {
    const col = document.createElement("div");
    col.className = "bar-col";
    const critH = Math.round((t.critique / max) * 100);
    const normH = Math.round(((t.total - t.critique) / max) * 100);
    col.innerHTML = `
      <div class="bar crit" style="height:${critH}%" title="${t.heure} : ${t.critique} critique(s)"></div>
      <div class="bar" style="height:${normH}%" title="${t.heure} : ${t.total} alerte(s)"></div>
      <div class="bar-label">${i % 4 === 0 ? t.heure : ""}</div>
    `;
    chart.appendChild(col);
  });
}

function renderModelBars(byModel) {
  const container = document.getElementById("model-bars");
  container.innerHTML = "";
  const entries = Object.entries(byModel).sort((a, b) => b[1] - a[1]);
  if (!entries.length) {
    container.innerHTML = '<div class="empty-state">Aucune donnée sur 7 jours.</div>';
    return;
  }
  const max = Math.max(...entries.map(([, c]) => c));
  for (const [model, count] of entries) {
    const row = document.createElement("div");
    row.className = "hbar-row";
    row.innerHTML = `
      <div class="hbar-name">${MODEL_LABELS[model] || model}</div>
      <div class="hbar-track"><div class="hbar-fill" style="width:${(count / max) * 100}%"></div></div>
      <div class="hbar-count">${count}</div>
    `;
    container.appendChild(row);
  }
}

function renderDashAlerts(alerts) {
  const container = document.getElementById("dash-alerts");
  if (!alerts.length) {
    container.innerHTML = '<div class="empty-state">Aucune alerte récente.</div>';
    return;
  }
  container.innerHTML = "";
  for (const a of alerts.slice(0, 6)) {
    container.appendChild(alertRow(a, false));
  }
}

/* ── Page Alertes ── */
async function loadAlertsTable() {
  const model = document.getElementById("filter-model").value;
  const severity = document.getElementById("filter-severity").value;
  const ack = document.getElementById("filter-ack").value;
  const period = document.getElementById("filter-period").value;

  const params = new URLSearchParams({ limit: "100" });
  if (model) params.set("model", model);
  if (severity) params.set("severity", severity);
  if (ack) params.set("acknowledged", ack);
  if (period) params.set("since_hours", period);

  const res = await fetch(`/api/alerts?${params}`);
  const alerts = await res.json();

  const table = document.getElementById("alerts-table");
  table.innerHTML = "";

  const header = document.createElement("div");
  header.className = "alert-row header";
  header.innerHTML = "<div>Image</div><div>Détection</div><div>Sévérité</div><div>Conf.</div><div>Horodatage</div><div>Action</div>";
  table.appendChild(header);

  if (!alerts.length) {
    table.innerHTML += '<div class="empty-state">Aucune alerte pour ces filtres.</div>';
    return;
  }
  for (const a of alerts) table.appendChild(alertRow(a, true));
}

function alertRow(a, withAction) {
  const row = document.createElement("div");
  row.className = "alert-row";
  const time = new Date(a.timestamp).toLocaleString("fr-FR");
  const thumb = a.snapshot
    ? `<img class="alert-thumb" src="/api/snapshot?path=${encodeURIComponent(a.snapshot)}" onerror="this.style.opacity=0.2" />`
    : '<div class="alert-thumb"></div>';
  const action = withAction
    ? a.acknowledged
      ? `<button class="btn small ghost">✓ ${a.ack_by || "traitée"}</button>`
      : `<button class="btn small" onclick="ackAlert(${a.id}, this)">Prendre en charge</button>`
    : "";
  const clip = a.clip
    ? ` · <a class="clip-link" href="/api/clip?path=${encodeURIComponent(a.clip)}" target="_blank">🎬 clip vidéo</a>`
    : "";
  row.innerHTML = `
    ${thumb}
    <div class="alert-main">
      <div class="l1">${a.label} <span class="muted">(${MODEL_LABELS[a.model] || a.model})</span></div>
      <div class="l2">${a.camera}${clip}</div>
    </div>
    <div><span class="sev ${a.severity}">${a.severity}</span></div>
    <div>${a.confidence.toFixed(2)}</div>
    <div class="muted" style="font-size:12px">${time}</div>
    <div>${action}</div>
  `;
  return row;
}

async function ackAlert(id, btn) {
  btn.disabled = true;
  await fetch(`/api/alerts/${id}/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operator: "opérateur" }),
  });
  loadAlertsTable();
  loadDashboard();
}

/* ── Cas d'usage (cahier des charges) ── */
const ETAT_LABEL = { operationnel: "Opérationnel", partiel: "Partiel", a_entrainer: "À entraîner" };

async function loadUseCases() {
  const res = await fetch("/api/usecases");
  const { usecases } = await res.json();
  const table = document.getElementById("usecases-table");
  table.innerHTML = "";

  const header = document.createElement("div");
  header.className = "uc-row header";
  header.innerHTML = "<div>#</div><div>Cas d'usage</div><div>Classes détectées</div><div>État</div><div>En service</div>";
  table.appendChild(header);

  for (const uc of usecases) {
    const row = document.createElement("div");
    row.className = "uc-row";
    const classes = uc.classes.length ? uc.classes.join(" / ") : "—";
    const live = uc.detect
      ? '<span class="uc-live on">● Actif</span>'
      : '<span class="uc-live off">○ Inactif</span>';
    row.innerHTML = `
      <div class="uc-num">${uc.num}</div>
      <div>
        <div class="uc-title">${uc.titre}</div>
        ${uc.note ? `<div class="uc-note">${uc.note}</div>` : ""}
      </div>
      <div class="uc-classes">${classes}</div>
      <div class="uc-state"><span class="uc-dot ${uc.etat}"></span>${ETAT_LABEL[uc.etat]}</div>
      <div>${live}</div>
    `;
    table.appendChild(row);
  }
}

/* ── Rapports ── */
async function loadReports() {
  const res = await fetch("/api/stats/summary");
  const summary = await res.json();
  const container = document.getElementById("severity-bars");
  container.innerHTML = "";
  const entries = Object.entries(summary.par_severite_7j);
  if (!entries.length) {
    container.innerHTML = '<div class="empty-state">Aucune donnée sur 7 jours.</div>';
    return;
  }
  const max = Math.max(...entries.map(([, c]) => c));
  for (const [sev, count] of entries) {
    const row = document.createElement("div");
    row.className = "hbar-row";
    row.innerHTML = `
      <div class="hbar-name">${sev}</div>
      <div class="hbar-track"><div class="hbar-fill ${sev}" style="width:${(count / max) * 100}%"></div></div>
      <div class="hbar-count">${count}</div>
    `;
    container.appendChild(row);
  }
}

/* ── Paramètres ── */
async function loadSettings() {
  const res = await fetch("/api/settings");
  const settings = await res.json();
  const container = document.getElementById("controls-list");
  container.innerHTML = "";
  for (const [model, values] of Object.entries(settings)) {
    const row = document.createElement("div");
    row.className = "model-row";
    const name = document.createElement("div");
    name.className = "model-name";
    name.textContent = MODEL_LABELS[model] || model;
    const toggles = document.createElement("div");
    toggles.className = "toggles";
    toggles.appendChild(makeToggle(model, "detect", values.detect, "Détection"));
    toggles.appendChild(makeToggle(model, "alert", values.alert, "Alerte"));
    row.appendChild(name);
    row.appendChild(toggles);
    container.appendChild(row);
  }
}

function makeToggle(model, key, value, text) {
  const label = document.createElement("label");
  label.className = "toggle-label";
  const sw = document.createElement("div");
  sw.className = "switch" + (value ? " on" : "");
  sw.onclick = async () => {
    const newValue = !sw.classList.contains("on");
    sw.classList.toggle("on", newValue);
    await fetch(`/api/settings/${model}/${key}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: newValue }),
    });
  };
  label.appendChild(sw);
  label.append(text);
  return label;
}

/* ── Filtres alertes ── */
function setupFilters() {
  const modelSelect = document.getElementById("filter-model");
  for (const [key, label] of Object.entries(MODEL_LABELS)) {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = label;
    modelSelect.appendChild(opt);
  }
  ["filter-model", "filter-severity", "filter-ack", "filter-period"].forEach((id) => {
    document.getElementById(id).addEventListener("change", loadAlertsTable);
  });
}

/* ── Init ── */
async function init() {
  setupNav();
  setupFilters();
  await loadCameras();
  await loadSettings();
  await loadDashboard();
  setInterval(refreshVideos, 700);
  setInterval(loadDashboard, 10000);
}

init();
