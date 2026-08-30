/* SmokeWatch — interface de supervision
 *
 * Aucune dépendance : une page de supervision doit s'ouvrir même si le serveur
 * du site est coupé d'Internet.
 */

const MODEL_LABELS = {
  arc: "⚡ Arc électrique",
  conveyor: "🏗️ Convoyeur",
  epi: "🦺 EPI",
  fire_smoke: "🔥 Fumée / Feu",
  gloves_glasses: "🧤 Gants / lunettes / chute",
  load_control: "📦 Contrôle chargement",
  person_animal: "🚶 Personne / animal",
  vehicles: "🚗 Véhicules",
  systeme: "🩺 Incident technique",
};

const OPERATOR = "opérateur";
const PAGE_SIZE = 25;

let cameras = [];
let cameraNames = [];
let uploads = [];
let lastCriticalId = null;
let alertsPage = 0;
let timelineDay = null;
let viewerCamera = null;

const el = (id) => document.getElementById(id);
const modelName = (m) => MODEL_LABELS[m] || m;
const fmtTime = (iso) => new Date(iso).toLocaleString("fr-FR");
const esc = (s) => String(s ?? "").replace(/[<>&"]/g, (c) =>
  ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));

async function api(url, options) {
  const r = await fetch(url, options);
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `Erreur ${r.status}`);
  }
  return r.json();
}

/* ═══ Retours visuels ═══ */

function toast(titre, corps = "", type = "") {
  const box = document.createElement("div");
  box.className = `toast ${type}`;
  box.innerHTML = `
    <div>
      <div class="toast-title">${esc(titre)}</div>
      ${corps ? `<div class="toast-body">${esc(corps)}</div>` : ""}
    </div>
    <button class="toast-close" aria-label="Fermer">✕</button>`;
  box.querySelector(".toast-close").addEventListener("click", () => box.remove());
  el("toasts").appendChild(box);
  setTimeout(() => box.remove(), type === "critique" ? 20000 : 6000);
}

/* Remplace confirm() : une action destructrice mérite d'annoncer ses
   conséquences, ce qu'une boîte native ne permet pas. */
function confirmer(titre, texte, libelle = "Confirmer") {
  return new Promise((resolve) => {
    const modal = el("modal");
    el("modal-title").textContent = titre;
    el("modal-text").textContent = texte;
    el("modal-confirm").textContent = libelle;
    modal.hidden = false;

    const fermer = (reponse) => {
      modal.hidden = true;
      el("modal-confirm").onclick = null;
      el("modal-cancel").onclick = null;
      document.removeEventListener("keydown", surTouche);
      resolve(reponse);
    };
    const surTouche = (e) => { if (e.key === "Escape") fermer(false); };

    el("modal-confirm").onclick = () => fermer(true);
    el("modal-cancel").onclick = () => fermer(false);
    document.addEventListener("keydown", surTouche);
    el("modal-confirm").focus();
  });
}

function vide(cible, icone, titre, texte, action = "") {
  el(cible).innerHTML = `
    <div class="empty">
      <div class="empty-icon">${icone}</div>
      <div class="empty-title">${esc(titre)}</div>
      <p>${texte}</p>
      ${action}
    </div>`;
}

function squelette(cible, lignes = 3) {
  el(cible).innerHTML = '<div class="skeleton"></div>'.repeat(lignes);
}

/* ═══ Navigation ═══ */

function setupNav() {
  document.querySelectorAll(".nav-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-link").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      el(`page-${btn.dataset.page}`).classList.add("active");
      el("page-title").textContent = btn.dataset.title;
      document.querySelector(".content").scrollTop = 0;

      ({
        dashboard: loadDashboard,
        cameras: loadCameras,
        alerts: () => { loadAlertsTable(); loadTimeline(); },
        zones: loadZonesPage,
        sources: loadUploads,
        usecases: loadUseCases,
        reports: loadReports,
        system: () => { loadSystem(); loadHandoffs(); },
      })[btn.dataset.page]?.();
    });
  });

  // Raccourcis clavier : un poste de supervision se pilote sans quitter l'écran.
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea")) return;
    const raccourcis = { 1: "dashboard", 2: "cameras", 3: "alerts", 4: "zones" };
    if (raccourcis[e.key]) goToPage(raccourcis[e.key]);
    if (e.key === "Escape") fermerVisionneuse();
  });
}

function goToPage(name) {
  document.querySelector(`.nav-link[data-page="${name}"]`)?.click();
}

function tickHorloge() {
  el("clock").textContent = new Date().toLocaleTimeString("fr-FR");
}

/* ═══ Mur de caméras ═══ */

async function loadCameras() {
  const data = await api("/api/cameras");
  cameras = data.cameras;
  cameraNames = cameras.map((c) => c.name);
  el("top-cams").textContent = `${cameras.filter((c) => c.online).length}/${cameras.length}`;
  el("sys-info").textContent =
    `${cameras.length} caméra${cameras.length > 1 ? "s" : ""} · ${Object.keys(MODEL_LABELS).length - 1} modèles`;

  const grid = el("camera-grid");
  if (!cameras.length) {
    vide("camera-grid", "▣", "Aucune caméra configurée",
      "Ajoutez une caméra, ou déposez une vidéo dans « Fichiers de test » pour éprouver le système sans matériel.",
      '<button class="btn" onclick="openCameraForm()">＋ Ajouter une caméra</button>');
    return;
  }

  grid.innerHTML = cameras.map((cam) => {
    const led = !cam.enabled
      ? '<span class="led pause">Pause</span>'
      : cam.online ? '<span class="led live">Direct</span>' : '<span class="led off">Hors ligne</span>';
    const modeles = cam.models.length
      ? cam.models.map((m) => `<span class="chip">${modelName(m)}</span>`).join("")
      : '<span class="chip">aucun modèle</span>';
    const zones = cam.zones?.length
      ? cam.zones.map((z) => `<span class="chip zone">◫ ${esc(z)}</span>`).join("")
      : '<span class="chip">plein cadre</span>';
    const debit = cam.cycle_ms ? `${cam.cycle_ms} ms` : (cam.state || "—");

    return `
      <article class="camera-tile" id="tile-${esc(cam.name)}">
        <div class="tile-video" data-open="${esc(cam.name)}">
          <img id="feed-${esc(cam.name)}" src="/video/${encodeURIComponent(cam.name)}.jpg" alt=""
               onerror="this.style.opacity=0.12" onload="this.style.opacity=1" />
          <div class="tile-top">
            <span class="tile-name">${esc(cam.name)}</span>
            <span class="tile-right">${led}</span>
          </div>
          <div class="tile-bottom">
            <span>${esc(String(cam.source ?? ""))}</span>
            <span class="tile-right">${debit}</span>
          </div>
        </div>
        <div class="tile-meta">${modeles}${zones}</div>
        <div class="tile-actions">
          <button class="btn ghost small" data-edit="${esc(cam.name)}">Modifier</button>
          <button class="btn ghost small" data-zones="${esc(cam.name)}">Zones</button>
          <button class="btn ghost small" data-delete="${esc(cam.name)}">Supprimer</button>
        </div>
      </article>`;
  }).join("");

  grid.querySelectorAll("[data-open]").forEach((n) =>
    n.addEventListener("click", () => ouvrirVisionneuse(n.dataset.open)));
  grid.querySelectorAll("[data-edit]").forEach((b) =>
    b.addEventListener("click", () => openCameraForm(b.dataset.edit)));
  grid.querySelectorAll("[data-zones]").forEach((b) =>
    b.addEventListener("click", () => { goToPage("zones"); selectZoneCamera(b.dataset.zones); }));
  grid.querySelectorAll("[data-delete]").forEach((b) =>
    b.addEventListener("click", () => deleteCamera(b.dataset.delete)));
}

function refreshVideos() {
  const t = Date.now();
  for (const name of cameraNames) {
    const img = el(`feed-${name}`);
    if (img) img.src = `/video/${encodeURIComponent(name)}.jpg?t=${t}`;
  }
  if (viewerCamera) {
    el("viewer-img").src = `/video/${encodeURIComponent(viewerCamera)}.jpg?t=${t}`;
  }
}

function ouvrirVisionneuse(nom) {
  viewerCamera = nom;
  const cam = cameras.find((c) => c.name === nom);
  el("viewer-name").textContent = nom;
  el("viewer-state").innerHTML = cam?.online
    ? '<span class="led live">Direct</span>'
    : '<span class="led off">Hors ligne</span>';
  el("viewer-img").src = `/video/${encodeURIComponent(nom)}.jpg?t=${Date.now()}`;
  el("viewer").hidden = false;
}

function fermerVisionneuse() {
  viewerCamera = null;
  el("viewer").hidden = true;
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
    if (document.fullscreenElement) document.exitFullscreen();
    else el("page-cameras").requestFullscreen?.();
  });

  el("viewer-close").addEventListener("click", fermerVisionneuse);
  el("viewer").addEventListener("click", (e) => {
    if (e.target === el("viewer")) fermerVisionneuse();
  });
}

/* ═══ Configuration d'une caméra ═══ */

function openCameraForm(name = null) {
  const form = el("camera-form");
  form.hidden = false;
  el("camera-form-title").textContent = name ? `Modifier « ${name} »` : "Nouvelle caméra";
  el("cam-status").textContent = "";
  el("cam-status").className = "form-status";

  const models = el("cam-models");
  if (!models.children.length) {
    models.innerHTML = Object.entries(MODEL_LABELS)
      .filter(([k]) => k !== "systeme")
      .map(([k, v]) => `<label><input type="checkbox" value="${k}" /> <span>${v}</span></label>`)
      .join("");
  }
  models.querySelectorAll("input").forEach((c) => (c.checked = false));

  const cam = cameras.find((c) => c.name === name);
  el("cam-name").value = cam?.name ?? "";
  el("cam-name").disabled = Boolean(name);
  el("cam-source").value = cam?.source ?? "";
  el("cam-fps").value = cam?.fps ?? "";
  el("cam-enabled").value = String(cam?.enabled ?? true);
  el("cam-tracking").value = String(cam?.tracking ?? false);
  el("cam-recording").value = String(cam?.recording ?? false);
  el("cam-plates").value = String(cam?.plates ?? false);
  el("cam-voisins").value = (cam?.voisins || []).join(", ");
  cam?.models.forEach((m) => {
    const box = models.querySelector(`input[value="${m}"]`);
    if (box) box.checked = true;
  });

  renderFilePicker();
  form.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* Les fichiers déposés se choisissent d'un clic : recopier un chemin à la main
   est la principale source d'erreur au moment de créer une caméra. */
function renderFilePicker() {
  const box = el("cam-file-picker");
  if (!uploads.length) {
    box.innerHTML = '<span class="muted">Aucun fichier déposé. Voir « Fichiers de test ».</span>';
    return;
  }
  box.innerHTML = uploads.slice(0, 8).map((f) =>
    `<button type="button" class="btn ghost small" data-src="${esc(f.source)}">
       ${f.type === "video" ? "🎞" : "🖼"} ${esc(f.nom)}
     </button>`).join("");
  box.querySelectorAll("[data-src]").forEach((b) =>
    b.addEventListener("click", () => { el("cam-source").value = b.dataset.src; }));
}

function cameraFormPayload() {
  const source = el("cam-source").value.trim();
  return {
    source: /^\d+$/.test(source) ? Number(source) : source,
    models: [...el("cam-models").querySelectorAll("input:checked")].map((c) => c.value),
    fps: el("cam-fps").value ? Number(el("cam-fps").value) : null,
    enabled: el("cam-enabled").value === "true",
    tracking: el("cam-tracking").value === "true",
    recording: el("cam-recording").value === "true",
    plates: el("cam-plates").value === "true",
    voisins: el("cam-voisins").value.split(",").map((v) => v.trim()).filter(Boolean),
  };
}

function setupCameraForm() {
  el("btn-add-camera").addEventListener("click", () => openCameraForm());

  // La lecture de plaques repose sur le vote entre plusieurs images du meme
  // vehicule : sans suivi, elle n'aurait rien sur quoi voter.
  el("cam-plates").addEventListener("change", (e) => {
    if (e.target.value === "true" && el("cam-tracking").value !== "true") {
      el("cam-tracking").value = "true";
      toast("Suivi activé", "La lecture de plaques repose sur le suivi des objets.");
    }
  });
  el("cam-cancel").addEventListener("click", () => (el("camera-form").hidden = true));

  el("cam-test").addEventListener("click", async () => {
    const status = el("cam-status");
    const source = cameraFormPayload().source;
    if (source === "") { status.className = "form-status error"; status.textContent = "Renseignez une source."; return; }

    status.className = "form-status";
    status.textContent = "Test en cours…";
    try {
      const res = await api("/api/cameras/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source }),
      });
      if (res.ok) {
        status.className = "form-status ok";
        status.textContent = `Connexion établie — ${res.kind}, ${res.width}×${res.height}`
          + (res.fps ? `, ${res.fps} img/s` : "");
      } else {
        status.className = "form-status error";
        status.textContent = res.error;
      }
    } catch (e) {
      status.className = "form-status error";
      status.textContent = e.message;
    }
  });

  el("cam-save").addEventListener("click", async () => {
    const status = el("cam-status");
    const name = el("cam-name").value.trim();
    if (!name) { status.className = "form-status error"; status.textContent = "Donnez un nom à la caméra."; return; }

    try {
      await api(`/api/cameras/${encodeURIComponent(name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cameraFormPayload()),
      });
      el("camera-form").hidden = true;
      toast("Caméra enregistrée", "Le pipeline la prend en compte dans quelques secondes.", "ok");
      await loadCameras();
    } catch (e) {
      status.className = "form-status error";
      status.textContent = e.message;
    }
  });
}

async function deleteCamera(name) {
  const ok = await confirmer(
    `Supprimer « ${name} » ?`,
    "La caméra ne sera plus traitée. Ses zones et son historique d'alertes sont conservés.",
    "Supprimer");
  if (!ok) return;
  try {
    await api(`/api/cameras/${encodeURIComponent(name)}`, { method: "DELETE" });
    toast("Caméra supprimée", name, "ok");
    await loadCameras();
  } catch (e) {
    toast("Suppression impossible", e.message, "error");
  }
}

/* ═══ Fichiers de test ═══ */

async function loadUploads() {
  try {
    uploads = (await api("/api/uploads")).fichiers;
  } catch {
    uploads = [];
  }

  if (!uploads.length) {
    vide("uploads-list", "🎞", "Aucun fichier déposé",
      "Déposez une vidéo de chantier, de départ de feu ou de quai de chargement : elle sera analysée comme une caméra réelle.");
    renderFilePicker();
    return;
  }

  el("uploads-list").innerHTML = uploads.map((f) => `
    <div class="file-row">
      <div class="file-icon">${f.type === "video" ? "🎞" : "🖼"}</div>
      <div class="file-main">
        <div class="file-name">${esc(f.nom)}</div>
        <div class="file-sub">${esc(f.source)} · ${f.taille_mo} Mo</div>
      </div>
      <button class="btn small" data-use="${esc(f.source)}">Créer une caméra</button>
      <button class="btn ghost small" data-del="${esc(f.nom)}">Supprimer</button>
    </div>`).join("");

  el("uploads-list").querySelectorAll("[data-use]").forEach((b) =>
    b.addEventListener("click", () => {
      goToPage("cameras");
      openCameraForm();
      el("cam-source").value = b.dataset.use;
      el("cam-name").value = b.dataset.use.split("/").pop().replace(/\.[^.]+$/, "").slice(0, 40);
    }));

  el("uploads-list").querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      const ok = await confirmer(`Supprimer « ${b.dataset.del} » ?`,
        "Le fichier est effacé du serveur. Une caméra qui l'utilise doit être supprimée d'abord.",
        "Supprimer");
      if (!ok) return;
      try {
        await api(`/api/uploads/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" });
        toast("Fichier supprimé", b.dataset.del, "ok");
        loadUploads();
      } catch (e) {
        toast("Suppression impossible", e.message, "error");
      }
    }));

  renderFilePicker();
}

function setupUpload() {
  const zone = el("dropzone");
  const input = el("file-input");

  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files.length) envoyerFichier(input.files[0]);
    input.value = "";
  });

  ["dragenter", "dragover"].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("over"); }));
  zone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) envoyerFichier(e.dataTransfer.files[0]);
  });
}

/* XMLHttpRequest plutôt que fetch : c'est le seul moyen d'afficher une
   progression, et un fichier vidéo peut prendre une minute à monter. */
function envoyerFichier(fichier) {
  const status = el("upload-status");
  const barre = el("upload-progress");
  const jauge = barre.querySelector("span");

  status.className = "form-status";
  status.textContent = `Envoi de ${fichier.name}…`;
  barre.hidden = false;
  jauge.style.width = "0%";

  const form = new FormData();
  form.append("file", fichier);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/uploads");
  xhr.upload.addEventListener("progress", (e) => {
    if (e.lengthComputable) jauge.style.width = `${(e.loaded / e.total) * 100}%`;
  });
  xhr.addEventListener("load", () => {
    barre.hidden = true;
    let reponse = {};
    try { reponse = JSON.parse(xhr.responseText); } catch { /* réponse illisible */ }

    if (xhr.status === 200) {
      const a = reponse.apercu || {};
      status.className = "form-status ok";
      status.textContent = `${reponse.nom} — ${reponse.taille_mo} Mo`
        + (a.width ? `, ${a.width}×${a.height}` : "");
      toast("Fichier importé", "Il est utilisable comme source de caméra.", "ok");
      loadUploads();
    } else {
      status.className = "form-status error";
      status.textContent = reponse.detail || `Échec (${xhr.status})`;
      toast("Import refusé", status.textContent, "error");
    }
  });
  xhr.addEventListener("error", () => {
    barre.hidden = true;
    status.className = "form-status error";
    status.textContent = "Connexion interrompue pendant l'envoi.";
  });
  xhr.send(form);
}

/* ═══ Tableau de bord ═══ */

async function loadDashboard() {
  const [summary, timeline, alerts, quality] = await Promise.all([
    api("/api/stats/summary"), api("/api/stats/timeline?hours=24"),
    api("/api/alerts?limit=6"), api("/api/stats/quality?days=30"),
  ]);

  el("kpi-24h").textContent = summary.total_24h;
  el("kpi-crit").textContent = summary.critiques_24h;
  el("kpi-unack").textContent = summary.non_acquittees;
  el("top-unack").textContent = summary.non_acquittees;
  el("unack-badge").textContent = summary.non_acquittees || "";

  const mtta = quality.delai_prise_en_charge_s;
  el("kpi-mtta").textContent = mtta == null ? "–" : mtta < 90 ? `${mtta} s` : `${Math.round(mtta / 60)} min`;

  const parCam = Object.values(quality.par_camera || {});
  const pire = parCam.length ? Math.max(...parCam.map((c) => c.fausses_par_jour)) : 0;
  el("kpi-false").textContent = parCam.length ? pire.toFixed(1) : "–";
  el("kpi-false").parentElement.classList.toggle("kpi-crit", pire > 2);

  renderTimelineChart(timeline);
  renderBars("model-bars", summary.par_modele_7j, modelName);
  renderBars("zone-bars", summary.par_zone_7j, (k) => k || "plein cadre");
  renderQuality(quality);

  const box = el("dash-alerts");
  if (!alerts.items.length) {
    vide("dash-alerts", "✓", "Aucune alerte", "Rien à signaler sur la période.");
  } else {
    box.innerHTML = "";
    box.className = "alerts-table";
    alerts.items.forEach((a) => box.appendChild(alertRow(a, false)));
  }
}

function renderTimelineChart(timeline) {
  const max = Math.max(1, ...timeline.map((t) => t.total));
  el("timeline-chart").innerHTML = timeline.map((t) => `
    <div class="bar-col" title="${t.heure} — ${t.total} alerte(s), ${t.critique} critique(s)">
      <div class="bar ${t.critique ? "crit" : ""}" style="height:${(t.total / max) * 100}%"></div>
      <div class="bar-label">${t.heure}</div>
    </div>`).join("");
}

function renderBars(cible, data, libelle) {
  const entrees = Object.entries(data || {}).sort((a, b) => b[1] - a[1]);
  if (!entrees.length) { el(cible).innerHTML = '<p class="muted">Aucune donnée.</p>'; return; }
  const max = Math.max(...entrees.map(([, v]) => v));
  el(cible).innerHTML = entrees.map(([k, v]) => `
    <div class="hbar-row">
      <div class="hbar-name">${esc(libelle(k))}</div>
      <div class="hbar-track"><div class="hbar-fill" style="width:${(v / max) * 100}%"></div></div>
      <div class="hbar-val">${v}</div>
    </div>`).join("");
}

function renderQuality(quality) {
  const entrees = Object.entries(quality.par_modele || {});
  if (!entrees.length) {
    el("quality-bars").innerHTML =
      `<p class="muted">Aucune alerte marquée pour l'instant. Le bouton « Fausse alerte »
       alimente cet indicateur — c'est ce qui permet de savoir quel modèle corriger.</p>`;
    return;
  }
  el("quality-bars").innerHTML = entrees
    .sort((a, b) => b[1].taux_faux - a[1].taux_faux)
    .map(([modele, s]) => {
      const pct = Math.round(s.taux_faux * 100);
      const cls = pct >= 40 ? "bad" : pct >= 15 ? "warn" : "good";
      return `
        <div class="hbar-row">
          <div class="hbar-name">${modelName(modele)}</div>
          <div class="hbar-track"><div class="hbar-fill ${cls}" style="width:${pct}%"></div></div>
          <div class="hbar-val">${pct}% <span class="muted">${s.fausses}/${s.alertes}</span></div>
        </div>`;
    }).join("");
}

/* ═══ Alertes ═══ */

function alertFilters() {
  const params = new URLSearchParams();
  const champs = {
    model: "filter-model", camera: "filter-camera", zone: "filter-zone",
    severity: "filter-severity", acknowledged: "filter-ack",
    false_positive: "filter-false", since_hours: "filter-period",
  };
  for (const [cle, id] of Object.entries(champs)) {
    const v = el(id)?.value;
    if (v) params.set(cle, v);
  }
  const label = el("filter-label")?.value.trim();
  if (label) params.set("label", label);
  const plaque = el("filter-plaque")?.value.trim();
  if (plaque) params.set("plaque", plaque);
  const poste = el("filter-hours")?.value;
  if (poste) {
    const [de, a] = poste.split("-");
    params.set("hour_from", de);
    params.set("hour_to", a);
  }
  return params;
}

async function loadAlertsTable() {
  squelette("alerts-table", 4);
  const params = alertFilters();
  params.set("limit", PAGE_SIZE);
  params.set("offset", alertsPage * PAGE_SIZE);

  const data = await api(`/api/alerts?${params}`);
  const table = el("alerts-table");

  if (!data.items.length) {
    vide("alerts-table", "🔍", "Aucune alerte",
      "Aucun événement ne correspond à ces critères. Élargissez la période ou retirez un filtre.");
  } else {
    table.innerHTML = "";
    data.items.forEach((a) => table.appendChild(alertRow(a, true)));
  }
  renderPager(data);
}

function renderPager(data) {
  const pages = Math.ceil(data.total / PAGE_SIZE);
  const pager = el("alerts-pager");
  if (pages <= 1) {
    pager.innerHTML = `<span>${data.total} alerte(s)</span>`;
    return;
  }
  pager.innerHTML = `
    <button class="btn ghost small" ${alertsPage === 0 ? "disabled" : ""} id="prev-page">← Précédent</button>
    <span>Page ${alertsPage + 1} sur ${pages} — ${data.total} alerte(s)</span>
    <button class="btn ghost small" ${alertsPage + 1 >= pages ? "disabled" : ""} id="next-page">Suivant →</button>`;
  el("prev-page")?.addEventListener("click", () => { alertsPage--; loadAlertsTable(); });
  el("next-page")?.addEventListener("click", () => { alertsPage++; loadAlertsTable(); });
}

function alertRow(a, avecActions) {
  const row = document.createElement("div");
  row.className = `alert-row sev-${a.severity}${a.false_positive ? " is-false" : ""}`;

  const vignette = a.snapshot
    ? `<img class="alert-thumb" src="/api/snapshot?path=${encodeURIComponent(a.snapshot)}"
            alt="" onerror="this.style.opacity=0.15" />`
    : '<div class="alert-thumb"></div>';

  const clip = a.clip
    ? `<a class="clip-link" href="/api/clip?path=${encodeURIComponent(a.clip)}" target="_blank">🎬 clip</a>`
    : "";
  const zone = a.zone ? `<span class="chip zone">◫ ${esc(a.zone)}</span>` : "";
  const plaque = a.plaque ? `<span class="chip accent">▭ ${esc(a.plaque)}</span>` : "";
  const faux = a.false_positive ? '<span class="tag-false">fausse</span>' : "";

  let actions = "";
  if (avecActions) {
    actions = a.acknowledged
      ? `<button class="btn ghost small" disabled>✓ ${esc(a.ack_by || "traitée")}</button>`
      : `<button class="btn small" data-ack="${a.id}">Prendre en charge</button>`;
    actions += a.false_positive
      ? `<button class="btn ghost small" data-true="${a.id}" title="Revenir sur ce jugement">↩</button>`
      : `<button class="btn ghost small" data-false="${a.id}"
                 title="Le système s'est trompé — sert aussi à corriger le modèle">✗ Fausse</button>`;
  }

  row.innerHTML = `
    ${vignette}
    <div class="alert-main">
      <div class="alert-l1">${esc(a.label)} ${faux}</div>
      <div class="alert-l2">
        <span>${esc(a.camera)}</span>${zone}${plaque}
        <span>${modelName(a.model)}</span>${clip}
      </div>
    </div>
    <div><span class="sev ${a.severity}">${a.severity}</span></div>
    <div class="alert-conf">${a.confidence.toFixed(2)}</div>
    <div class="alert-time">${fmtTime(a.timestamp)}</div>
    <div class="alert-actions">${actions}</div>`;

  row.querySelector(".alert-thumb")?.addEventListener("click", () => {
    if (a.snapshot) window.open(`/api/snapshot?path=${encodeURIComponent(a.snapshot)}`, "_blank");
  });
  row.querySelector("[data-ack]")?.addEventListener("click", (e) => ackAlert(a.id, e.target));
  row.querySelector("[data-false]")?.addEventListener("click", () => flagAlert(a.id, true));
  row.querySelector("[data-true]")?.addEventListener("click", () => flagAlert(a.id, false));
  return row;
}

async function ackAlert(id, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  await api(`/api/alerts/${id}/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operator: OPERATOR }),
  });
  loadAlertsTable();
}

async function flagAlert(id, estFausse) {
  await api(`/api/alerts/${id}/false`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_false: estFausse, operator: OPERATOR }),
  });
  if (estFausse) {
    toast("Marquée comme fausse alerte",
      "L'image rejoint le jeu de données qui servira à corriger le modèle.", "ok");
  }
  loadAlertsTable();
}

/* ═══ Frise chronologique ═══ */

async function loadTimeline() {
  const camera = el("timeline-camera")?.value || "";
  const params = new URLSearchParams();
  if (camera) params.set("camera", camera);
  if (timelineDay) params.set("day", timelineDay);

  const data = await api(`/api/timeline?${params}`);
  timelineDay = data.day;

  // N'afficher que les journées ayant produit des alertes : un calendrier
  // majoritairement vide ne rend service à personne.
  const jours = data.jours_disponibles.length ? data.jours_disponibles : [data.day];
  el("timeline-day").innerHTML = jours
    .map((j) => `<option value="${j}" ${j === data.day ? "selected" : ""}>${formatJour(j)}</option>`)
    .join("");

  renderTimelineTrack(data.alertes);
}

function formatJour(iso) {
  return new Date(`${iso}T12:00:00`)
    .toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "long" });
}

function renderTimelineTrack(alertes) {
  el("timeline-hours").innerHTML = [0, 3, 6, 9, 12, 15, 18, 21, 24]
    .map((h) => `<span style="left:${(h / 24) * 100}%">${String(h).padStart(2, "0")}h</span>`).join("");

  const track = el("timeline-track");
  if (!alertes.length) {
    track.innerHTML = '<div class="timeline-empty">Aucune alerte ce jour-là.</div>';
    el("timeline-detail").innerHTML = "";
    return;
  }

  track.innerHTML = alertes.map((a) => `
    <button class="timeline-mark ${a.severity}${a.false_positive ? " is-false" : ""}"
            style="left:${a.position * 100}%" data-id="${a.id}"
            title="${new Date(a.timestamp).toLocaleTimeString("fr-FR")} — ${esc(a.label)} (${esc(a.camera)})"></button>
  `).join("");

  track.querySelectorAll(".timeline-mark").forEach((mark) => {
    mark.addEventListener("click", () => {
      track.querySelectorAll(".timeline-mark").forEach((m) => m.classList.remove("selected"));
      mark.classList.add("selected");
      showTimelineDetail(alertes.find((a) => a.id === Number(mark.dataset.id)));
    });
  });
}

function showTimelineDetail(a) {
  if (!a) return;
  const heure = new Date(a.timestamp).toLocaleTimeString("fr-FR");
  const image = a.snapshot
    ? `<img src="/api/snapshot?path=${encodeURIComponent(a.snapshot)}" alt="" onerror="this.remove()" />`
    : "";
  const clip = a.clip
    ? `<a class="clip-link" href="/api/clip?path=${encodeURIComponent(a.clip)}" target="_blank">🎬 clip vidéo</a>`
    : '<span class="muted">pas de clip</span>';
  const etat = a.false_positive ? '<span class="tag-false">fausse alerte</span>'
    : a.acknowledged ? `<span class="muted">traitée par ${esc(a.ack_by || "—")}</span>`
      : '<span class="muted">à traiter</span>';

  el("timeline-detail").innerHTML = `
    ${image}
    <div>
      <div class="alert-l1">${heure} — ${esc(a.label)} <span class="sev ${a.severity}">${a.severity}</span></div>
      <div class="alert-l2"><span>${esc(a.camera)}</span>
        ${a.zone ? `<span class="chip zone">◫ ${esc(a.zone)}</span>` : ""}
        <span>${modelName(a.model)}</span></div>
      <div class="alert-l2">${clip} · ${etat}</div>
    </div>`;
}

function setupTimeline() {
  el("timeline-day").addEventListener("change", (e) => { timelineDay = e.target.value; loadTimeline(); });
  el("timeline-camera").addEventListener("change", () => { timelineDay = null; loadTimeline(); });
}

/* ═══ Cas d'usage ═══ */

async function loadUseCases() {
  const data = await api("/api/usecases");
  const etats = { operationnel: "Opérationnel", partiel: "Partiel", a_entrainer: "À entraîner" };
  el("usecases-table").innerHTML = data.usecases.map((uc) => `
    <div class="uc-row">
      <div class="uc-num">${String(uc.num).padStart(2, "0")}</div>
      <div>
        <div class="uc-title"><span class="uc-dot ${uc.etat}"></span>${esc(uc.titre)}</div>
        <div class="muted" style="font-size:12px">
          ${uc.model ? modelName(uc.model) : "aucun modèle"} · ${esc(uc.classes.join(", "))}
        </div>
        ${uc.note ? `<div class="uc-note">${esc(uc.note)}</div>` : ""}
      </div>
      <div class="uc-state">${etats[uc.etat]}</div>
      <div class="uc-live">${uc.detect ? "détection active" : "détection inactive"}</div>
    </div>`).join("");
}

/* ═══ Rapports ═══ */

async function loadReports() {
  const [summary, quality] = await Promise.all([
    api("/api/stats/summary"), api("/api/stats/quality?days=30"),
  ]);
  renderBars("severity-bars", summary.par_severite_7j, (k) => k);

  const entrees = Object.entries(quality.par_camera || {});
  if (!entrees.length) {
    el("false-by-camera").innerHTML = '<p class="muted">Aucune donnée.</p>';
    return;
  }
  el("false-by-camera").innerHTML = entrees.map(([cam, s]) => {
    const depasse = s.fausses_par_jour > 2;
    return `
      <div class="hbar-row">
        <div class="hbar-name">${esc(cam)}</div>
        <div class="hbar-track">
          <div class="hbar-fill ${depasse ? "bad" : "good"}"
               style="width:${Math.min(100, (s.fausses_par_jour / 4) * 100)}%"></div>
        </div>
        <div class="hbar-val">${s.fausses_par_jour}/j</div>
      </div>`;
  }).join("");
}

/* ═══ Système ═══ */

async function loadSystem() {
  const h = await api("/api/health");
  const m = h.machine || {};
  const kpi = (valeur, libelle, alerte = false) =>
    `<div class="kpi ${alerte ? "kpi-crit" : ""}">
       <div class="kpi-value">${valeur}</div><div class="kpi-label">${libelle}</div></div>`;

  el("system-kpis").innerHTML = [
    kpi(h.pipeline.running ? "Actif" : "Arrêté", "Pipeline de détection", !h.pipeline.running),
    kpi(`${h.cameras_actives}/${h.cameras_configurees}`, "Caméras actives"),
    kpi(m.cpu_percent != null ? `${m.cpu_percent}%` : "–", "Processeur", (m.cpu_percent ?? 0) > 90),
    kpi(m.memory_percent != null ? `${m.memory_percent}%` : "–", "Mémoire", (m.memory_percent ?? 0) > 90),
    kpi(m.disk_free_gb != null ? `${m.disk_free_gb} Go` : "–", "Disque libre", (m.disk_free_gb ?? 99) < 5),
  ].join("");

  const lignes = Object.entries(h.cameras || {});
  if (!lignes.length) {
    vide("system-cameras", "◉", "Aucun état publié",
      "Le pipeline n'a encore rien signalé. S'il devrait tourner, vérifiez le service de détection.");
    return;
  }
  el("system-cameras").innerHTML = lignes.map(([nom, c]) => `
    <div class="sys-row">
      <div class="sys-name">${esc(nom)}</div>
      <div><span class="state-tag ${(c.state || "").replace(/\s/g, "-")}">${esc(c.state || "inconnu")}</span></div>
      <div class="muted">
        ${c.cycle_ms ? `${c.cycle_ms} ms/cycle` : ""}
        ${c.modeles_actifs != null ? ` · ${c.modeles_actifs} modèles` : ""}
        ${c.objets_suivis != null ? ` · ${c.objets_suivis} objet(s) suivi(s)` : ""}
      </div>
      <div class="muted">${esc(c.error || "")}</div>
    </div>`).join("");
}

async function loadHandoffs() {
  let data;
  try {
    data = await api("/api/handoffs");
  } catch {
    return;
  }

  if (!data.correspondances.length) {
    vide("handoffs-list", "⇄", "Aucun rapprochement",
      "Aucun objet n'a encore été retrouvé d'une caméra à l'autre. Cela demande au moins deux caméras avec le suivi activé.");
    return;
  }

  el("handoffs-list").innerHTML = data.correspondances.map((c) => {
    const heure = new Date(c.horodatage * 1000).toLocaleTimeString("fr-FR");
    const certitude = c.certain
      ? '<span class="chip accent">plaque — certain</span>'
      : `<span class="chip">apparence — probable (${c.score})</span>`;
    return `
      <div class="file-row">
        <div class="file-icon">⇄</div>
        <div class="file-main">
          <div class="file-name">${esc(c.de)} → ${esc(c.vers)}</div>
          <div class="file-sub">${heure} · ${esc(c.classe)}${c.plaque ? ` · ${esc(c.plaque)}` : ""}</div>
        </div>
        ${certitude}
      </div>`;
  }).join("");
}

async function refreshPipelineState() {
  try {
    const h = await api("/api/health");
    const actif = h.pipeline.running;
    el("pipeline-dot").className = `status-dot ${actif ? "" : "down"}`;
    el("pipeline-state").textContent = actif ? "Détection active" : "Détection arrêtée";
    el("system-badge").textContent = actif ? "" : "!";
  } catch {
    el("pipeline-state").textContent = "Interface injoignable";
    el("pipeline-dot").className = "status-dot down";
  }
}

/* ═══ Alertes critiques en direct ═══ */

async function watchCriticalAlerts() {
  let data;
  try {
    data = await api("/api/alerts?limit=1&severity=critique&since_hours=1");
  } catch { return; }

  const derniere = data.items[0];
  if (!derniere) return;
  if (lastCriticalId === null) { lastCriticalId = derniere.id; return; }
  if (derniere.id === lastCriticalId) return;
  lastCriticalId = derniere.id;

  toast(`${derniere.label} — ${derniere.camera}`,
    derniere.zone ? `Zone ${derniere.zone}` : "", "critique");

  if (el("auto-focus")?.checked) {
    document.querySelectorAll(".camera-tile").forEach((t) => t.classList.remove("alerting"));
    const tuile = el(`tile-${derniere.camera}`);
    tuile?.classList.add("alerting");
    tuile?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

/* ═══ Paramètres ═══ */

async function loadSettings() {
  const settings = await api("/api/settings");
  el("controls-list").innerHTML = "";
  for (const [modele, valeurs] of Object.entries(settings)) {
    const row = document.createElement("div");
    row.className = "control-row";
    row.innerHTML = `<div class="control-name">${modelName(modele)}</div>`;
    row.appendChild(makeToggle(modele, "detect", valeurs.detect, "Détection"));
    row.appendChild(makeToggle(modele, "alert", valeurs.alert, "Alertes"));
    el("controls-list").appendChild(row);
  }
}

function makeToggle(modele, cle, valeur, texte) {
  const label = document.createElement("label");
  label.className = "switch";
  label.innerHTML = `<input type="checkbox" ${valeur ? "checked" : ""} />
                     <span class="slider"></span><span class="switch-text">${texte}</span>`;
  label.querySelector("input").addEventListener("change", async (e) => {
    try {
      await api(`/api/settings/${modele}/${cle}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: e.target.checked }),
      });
    } catch (err) {
      e.target.checked = !e.target.checked;
      toast("Réglage non appliqué", err.message, "error");
    }
  });
  return label;
}

/* ═══ Éditeur de zones ═══ */

const zoneEditor = { camera: null, zones: [], draft: [] };

async function loadZonesPage() {
  const select = el("zone-camera");
  select.innerHTML = cameraNames.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  if (!select.dataset.bound) {
    select.addEventListener("change", () => selectZoneCamera(select.value));
    select.dataset.bound = "1";
  }

  const models = el("zone-models");
  if (!models.children.length) {
    models.innerHTML = Object.entries(MODEL_LABELS)
      .filter(([k]) => k !== "systeme")
      .map(([k, v]) => `<label><input type="checkbox" value="${k}" /> <span>${v}</span></label>`)
      .join("");
  }
  await selectZoneCamera(zoneEditor.camera || select.value || cameraNames[0]);
}

async function selectZoneCamera(camera) {
  if (!camera) return;
  zoneEditor.camera = camera;
  zoneEditor.draft = [];
  el("zone-camera").value = camera;
  el("zone-frame").src = `/video/${encodeURIComponent(camera)}.jpg?t=${Date.now()}`;
  const data = await api(`/api/zones/${encodeURIComponent(camera)}`);
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
      exclusion ? "rgba(240,68,56,0.95)" : "rgba(247,144,9,0.95)",
      exclusion ? "rgba(240,68,56,0.18)" : "rgba(247,144,9,0.15)", zone.name);
  }
  if (zoneEditor.draft.length) {
    drawPolygon(ctx, zoneEditor.draft, w, h, "rgba(18,183,106,0.95)", "rgba(18,183,106,0.16)", null, true);
  }
}

function drawPolygon(ctx, polygon, w, h, trait, fond, libelle, points) {
  if (!polygon.length) return;
  ctx.beginPath();
  polygon.forEach(([x, y], i) => {
    const px = x * w, py = y * h;
    i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
  });
  if (polygon.length >= 3) {
    ctx.closePath();
    ctx.fillStyle = fond;
    ctx.fill();
  }
  ctx.strokeStyle = trait;
  ctx.lineWidth = 2;
  ctx.stroke();

  if (points) {
    for (const [x, y] of polygon) {
      ctx.beginPath();
      ctx.arc(x * w, y * h, 4, 0, Math.PI * 2);
      ctx.fillStyle = trait;
      ctx.fill();
    }
  }
  if (libelle) {
    const [x, y] = polygon[0];
    ctx.fillStyle = trait;
    ctx.font = "12px system-ui, sans-serif";
    ctx.fillText(libelle, x * w + 5, y * h - 6);
  }
}

function decrireZone(zone) {
  const parts = [`${zone.polygon.length} sommets`];
  parts.push(zone.models?.length ? zone.models.map(modelName).join(" · ") : "tous les modèles");
  if (zone.schedule?.start && zone.schedule?.end) parts.push(`${zone.schedule.start} → ${zone.schedule.end}`);
  if (zone.conf) parts.push(`seuil ${zone.conf}`);
  if (zone.cooldown) parts.push(`délai ${zone.cooldown} s`);
  return parts.join(" · ");
}

function renderZoneList() {
  const list = el("zone-list");
  if (!zoneEditor.zones.length) {
    list.innerHTML = '<p class="muted">Aucune zone : la caméra est analysée en entier.</p>';
    return;
  }
  list.innerHTML = "";
  zoneEditor.zones.forEach((zone, index) => {
    const row = document.createElement("div");
    row.className = "zone-item";
    row.innerHTML = `
      <div>
        <div class="zone-item-name">${esc(zone.name)}
          <span class="zone-kind ${zone.type === "exclusion" ? "excl" : ""}">
            ${zone.type === "exclusion" ? "masque" : "surveillée"}</span>
        </div>
        <div class="muted" style="font-size:12px">${esc(decrireZone(zone))}</div>
      </div>
      <button class="btn ghost small">Supprimer</button>`;
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
    // Coordonnées normalisées : indépendantes de la taille d'affichage et de la
    // résolution de la caméra.
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    zoneEditor.draft.push([+x.toFixed(4), +y.toFixed(4)]);
    el("zone-hint").textContent = `${zoneEditor.draft.length} sommet(s) posé(s)`
      + (zoneEditor.draft.length >= 3 ? " — nommez la zone puis « Ajouter »." : " — 3 minimum.");
    drawZones();
  });

  el("zone-undo").addEventListener("click", () => { zoneEditor.draft.pop(); drawZones(); });
  el("zone-clear").addEventListener("click", () => { zoneEditor.draft = []; drawZones(); });

  el("zone-add").addEventListener("click", () => {
    const status = el("zone-status");
    const nom = el("zone-name").value.trim();
    if (zoneEditor.draft.length < 3) {
      status.className = "form-status error";
      status.textContent = "Tracez au moins 3 sommets sur l'image.";
      return;
    }
    if (!nom) {
      status.className = "form-status error";
      status.textContent = "Donnez un nom à la zone.";
      return;
    }

    const zone = {
      name: nom,
      polygon: zoneEditor.draft,
      type: el("zone-type").value,
      models: [...el("zone-models").querySelectorAll("input:checked")].map((c) => c.value),
    };
    const debut = el("zone-start").value, fin = el("zone-end").value;
    if (debut && fin) zone.schedule = { start: debut, end: fin };
    if (el("zone-conf").value) zone.conf = Number(el("zone-conf").value);
    if (el("zone-cooldown").value) zone.cooldown = Number(el("zone-cooldown").value);

    zoneEditor.zones.push(zone);
    zoneEditor.draft = [];
    el("zone-name").value = "";
    el("zone-conf").value = "";
    el("zone-cooldown").value = "";
    el("zone-models").querySelectorAll("input:checked").forEach((c) => (c.checked = false));
    status.className = "form-status";
    status.textContent = "Zone ajoutée — pensez à enregistrer.";
    renderZoneList();
    drawZones();
  });

  el("zone-save").addEventListener("click", async () => {
    const status = el("zone-status");
    try {
      const data = await api(`/api/zones/${encodeURIComponent(zoneEditor.camera)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zones: zoneEditor.zones }),
      });
      status.className = "form-status ok";
      status.textContent = `${data.zones} zone(s) enregistrée(s), appliquées au prochain cycle.`;
      toast("Zones enregistrées", "Prises en compte sans redémarrage.", "ok");
      loadCameras();
    } catch (e) {
      status.className = "form-status error";
      status.textContent = e.message;
    }
  });

  el("zone-frame").addEventListener("load", drawZones);
  window.addEventListener("resize", drawZones);
}

/* ═══ Filtres ═══ */

function setupFilters() {
  const ids = ["filter-model", "filter-camera", "filter-zone", "filter-severity",
    "filter-ack", "filter-false", "filter-period", "filter-hours"];
  ids.forEach((id) => el(id)?.addEventListener("change", () => { alertsPage = 0; loadAlertsTable(); }));
  const recherche = debounce(() => { alertsPage = 0; loadAlertsTable(); }, 350);
  el("filter-label")?.addEventListener("input", recherche);
  el("filter-plaque")?.addEventListener("input", recherche);
}

function debounce(fn, delai) {
  let minuteur;
  return (...args) => { clearTimeout(minuteur); minuteur = setTimeout(() => fn(...args), delai); };
}

function populateFilters() {
  el("filter-model").innerHTML = '<option value="">Tous les modèles</option>'
    + Object.entries(MODEL_LABELS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");

  const zones = new Set();
  cameras.forEach((c) => c.zones?.forEach((z) => zones.add(z)));

  el("filter-camera").innerHTML = '<option value="">Toutes les caméras</option>'
    + cameras.map((c) => `<option value="${esc(c.name)}">${esc(c.name)}</option>`).join("");
  el("timeline-camera").innerHTML = '<option value="">Toutes les caméras</option>'
    + cameras.map((c) => `<option value="${esc(c.name)}">${esc(c.name)}</option>`).join("");
  el("filter-zone").innerHTML = '<option value="">Toutes les zones</option>'
    + [...zones].map((z) => `<option value="${esc(z)}">${esc(z)}</option>`).join("");
}

/* ═══ Démarrage ═══ */

async function init() {
  setupNav();
  setupFilters();
  setupZoneEditor();
  setupTimeline();
  setupCameraForm();
  setupCameraWall();
  setupUpload();

  tickHorloge();
  await loadCameras();
  await loadUploads();
  populateFilters();
  await loadSettings();
  await loadDashboard();
  await refreshPipelineState();

  setInterval(tickHorloge, 1000);
  setInterval(refreshVideos, 700);
  setInterval(loadDashboard, 15000);
  setInterval(refreshPipelineState, 10000);
  setInterval(watchCriticalAlerts, 5000);
  setInterval(loadCameras, 30000);
}

init();
