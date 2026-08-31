/* Ciment's Eye — poste de supervision
 *
 * Aucune dépendance : la page doit s'ouvrir même si le serveur du site est
 * coupé d'Internet.
 */

const MODELES = {
  arc: "Arc électrique",
  conveyor: "Convoyeur",
  epi: "EPI",
  fall: "Personne au sol",
  fire_smoke: "Fumée / feu",
  gloves_glasses: "Gants / lunettes",
  load_control: "Chargement camion",
  person_animal: "Personne / animal",
  vehicles: "Véhicules",
  systeme: "Incident technique",
};

const OPERATEUR = "opérateur";
const PAR_PAGE = 30;

let cameras = [];
let noms = [];
let fichiers = [];
let selection = null;
let derniereCritique = null;
let page = 0;
let jourFrise = null;
let visionnee = null;

const el = (id) => document.getElementById(id);
const nomModele = (m) => MODELES[m] || m;
const heure = (iso) => new Date(iso).toLocaleTimeString("fr-FR");
const dateHeure = (iso) => new Date(iso).toLocaleString("fr-FR");
const ech = (s) => String(s ?? "").replace(/[<>&"]/g, (c) =>
  ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));

async function api(url, options) {
  const r = await fetch(url, options);
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail || `Erreur ${r.status}`);
  }
  return r.json();
}

/* ═══ Retours ═══ */

function avis(titre, corps = "", type = "") {
  const n = document.createElement("div");
  n.className = `avis-item ${type}`;
  n.innerHTML = `<div><div class="avis-titre">${ech(titre)}</div>` +
    (corps ? `<div class="avis-corps">${ech(corps)}</div>` : "") +
    `</div><button class="avis-fermer" aria-label="Fermer">×</button>`;
  n.querySelector("button").addEventListener("click", () => n.remove());
  el("avis").appendChild(n);
  setTimeout(() => n.remove(), type === "critique" ? 20000 : 6000);
}

/* Remplace confirm() : une action destructrice doit annoncer ses conséquences,
   ce qu'une boîte native ne permet pas. */
function confirmer(titre, texte, libelle = "Confirmer") {
  return new Promise((resolve) => {
    const d = el("dialogue");
    el("dialogue-titre").textContent = titre;
    el("dialogue-texte").textContent = texte;
    el("dialogue-confirmer").textContent = libelle;
    d.hidden = false;

    const fin = (r) => {
      d.hidden = true;
      el("dialogue-confirmer").onclick = null;
      el("dialogue-annuler").onclick = null;
      document.removeEventListener("keydown", touche);
      resolve(r);
    };
    const touche = (e) => { if (e.key === "Escape") fin(false); };
    el("dialogue-confirmer").onclick = () => fin(true);
    el("dialogue-annuler").onclick = () => fin(false);
    document.addEventListener("keydown", touche);
    el("dialogue-confirmer").focus();
  });
}

function vide(cible, titre, texte) {
  el(cible).innerHTML =
    `<div class="vide"><div class="vide-titre">${ech(titre)}</div><p>${texte}</p></div>`;
}

/* ═══ Onglets ═══ */

/* Le thème suit d'abord le réglage du système d'exploitation, puis le choix
   explicite de l'agent s'il en fait un. Un poste de garde est sombre la nuit et
   éclairé le jour : imposer l'un des deux fatigue un cas sur deux. */
function theme() {
  // Noir par defaut : un poste de garde est rarement une piece eclairee, et
  // l'image video ressort mieux sur un fond noir.
  let courant = localStorage.getItem("ciments_eye-theme") || "noir";

  const appliquer = () => {
    document.documentElement.dataset.theme = courant;
    document.querySelectorAll("[data-theme-choix]").forEach((b) =>
      b.classList.toggle("actif", b.dataset.themeChoix === courant));
  };
  appliquer();

  document.querySelectorAll("[data-theme-choix]").forEach((b) =>
    b.addEventListener("click", () => {
      courant = b.dataset.themeChoix;
      localStorage.setItem("ciments_eye-theme", courant);
      appliquer();
    }));
}

const ECRANS = {
  direct: ["Direct", "mur d'images du site"],
  historique: ["Historique", "retrouver un événement"],
  analyse: ["Analyse", "indicateurs et fiabilité"],
  config: ["Paramètres", "caméras, zones, modèles"],
};

function onglets() {
  document.querySelectorAll(".rail-item").forEach((b) => {
    b.addEventListener("click", () => {
      const vue = b.dataset.vue;
      document.querySelectorAll(".rail-item").forEach((x) => x.classList.remove("actif"));
      document.querySelectorAll(".page").forEach((p) => p.classList.remove("actif"));
      b.classList.add("actif");
      el(`vue-${vue}`).classList.add("actif");

      const [titre, sous] = ECRANS[vue];
      el("entete-titre").textContent = titre;
      el("entete-sous").textContent = sous;

      ({
        direct: chargerCameras,
        historique: () => { chargerAlertes(); chargerFrise(); },
        analyse: chargerAnalyse,
        config: () => ouvrirSection(sectionCourante),
      })[vue]?.();
    });
  });

  document.querySelectorAll("#sous-nav [data-section]").forEach((n) =>
    n.addEventListener("click", () => ouvrirSection(n.dataset.section)));

  // Raccourcis : un poste de supervision se pilote sans quitter l'écran.
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea")) return;
    const vues = ["direct", "historique", "analyse", "config"];
    if (vues[Number(e.key) - 1]) allerA(vues[Number(e.key) - 1]);
    if (e.key === "Escape") { fermerVisionneuse(); el("aide").hidden = true; }
  });
}

let sectionCourante = "cameras";

function ouvrirSection(nom) {
  sectionCourante = nom;
  document.querySelectorAll("#sous-nav [data-section]").forEach((n) =>
    n.classList.toggle("actif", n.dataset.section === nom));
  document.querySelectorAll("#vue-config .section").forEach((n) =>
    n.classList.toggle("actif", n.id === "config-" + nom));

  ({
    cameras: chargerCameras,
    zones: chargerZones,
    fichiers: chargerFichiers,
    modeles: chargerReglages,
    systeme: chargerSysteme,
  })[nom]?.();
}

function allerA(vue) {
  document.querySelector(`.rail-item[data-vue="${vue}"]`)?.click();
}

function tick() {
  el("horloge").textContent = new Date().toLocaleTimeString("fr-FR");
}

/* ═══ Caméras ═══ */

async function chargerCameras() {
  const d = await api("/api/cameras");
  cameras = d.cameras;
  noms = cameras.map((c) => c.name);
  dessinerMur();
  dessinerTableCameras();
  majBarreEtat();
}

/* En configuration, les caméras se lisent en tableau : on y compare des
   réglages, alors que le mur sert à regarder des images. */
function dessinerTableCameras() {
  const t = el("table-cameras");
  if (!t) return;
  if (!cameras.length) {
    t.innerHTML = '<tbody><tr><td class="msg" style="padding:20px">'
      + 'Aucune caméra. Cliquez sur « Ajouter ».</td></tr></tbody>';
    return;
  }

  const lignes = cameras.map((c) => {
    const options = [c.tracking && "suivi", c.plates && "plaques",
      c.bachage && "bâchage", c.collecte && "collecte",
      c.recording && "enreg."].filter(Boolean).join(", ") || "—";
    const etat = c.enabled ? (c.online ? "en ligne" : "hors ligne") : "en pause";
    return "<tr data-cam=\"" + ech(c.name) + "\" class=\""
      + (selection === c.name ? "haute" : "") + "\" style=\"cursor:pointer\">"
      + "<td><span class=\"pastille " + etatCamera(c) + "\"></span></td>"
      + "<td>" + ech(c.name) + "</td>"
      + "<td class=\"num\">" + ech(String(c.source ?? "")) + "</td>"
      + "<td class=\"num\">" + (c.fps ?? "—") + "</td>"
      + "<td>" + (c.models.length ? c.models.map(nomModele).join(", ") : "aucun") + "</td>"
      + "<td>" + (c.zones?.length ? ech(c.zones.join(", ")) : "plein cadre") + "</td>"
      + "<td class=\"msg\">" + options + "</td>"
      + "<td>" + etat + "</td></tr>";
  }).join("");

  t.innerHTML = '<thead><tr><th style="width:26px"></th><th>Nom</th><th>Source</th>'
    + '<th style="width:70px">Img/s</th><th>Modèles</th><th>Zones</th>'
    + '<th style="width:160px">Options</th><th style="width:90px">État</th></tr></thead>'
    + "<tbody>" + lignes + "</tbody>";

  t.querySelectorAll("[data-cam]").forEach((r) =>
    r.addEventListener("click", () => {
      selection = selection === r.dataset.cam ? null : r.dataset.cam;
      dessinerTableCameras();
      dessinerMur();
      majActions();
    }));
}

function etatCamera(c) {
  if (!c.enabled) return "pause";
  return c.online ? "en-ligne" : "hors-ligne";
}

function majActions() {
  const actif = Boolean(selection);
  const modifier = el("btn-modifier");
  if (!modifier) return;
  modifier.disabled = !actif;
  el("btn-supprimer").disabled = !actif;
  el("config-selection").textContent = actif
    ? "Sélection : " + selection
    : "Sélectionnez une caméra dans la liste pour la modifier.";
}

let vuePage = 0;
let colonnes = 2;
let infosTech = false;

const ETIQ_ETAT = { "en-ligne": "DIRECT", pause: "PAUSE", "hors-ligne": "HORS LIGNE" };

/* Les commandes d'une vignette restent cachées jusqu'au survol : un mur de
   supervision doit rester nu. C'est ce qui le distingue d'un tableau de bord. */
function tuile(c, principal) {
  const e = etatCamera(c);
  const mods = c.models.length ? c.models.map(nomModele).join(", ") : "aucun modèle";
  const tech = infosTech
    ? (e === "en-ligne" ? `${c.models.length} mod · ${c.cycle_ms ? c.cycle_ms + " ms" : "—"}` : "—")
    : "";
  return `
    <div class="tuile${principal ? " principal" : ""}${e === "hors-ligne" ? " hs" : ""}"
         id="tuile-${ech(c.name)}" data-cam="${ech(c.name)}">
      <img data-flux="${ech(c.name)}" src="/video/${encodeURIComponent(c.name)}.jpg" alt=""
           onerror="this.style.opacity=0.08" onload="this.style.opacity=1" />
      <div class="tuile-haut">
        <span class="tuile-modeles">${ech(mods)}</span>
        <span class="tuile-actions">
          <button data-pause="${ech(c.name)}" title="${c.enabled ? "Mettre en pause" : "Reprendre"}">${c.enabled ? "Pause" : "Reprendre"}</button>
          <button data-zones="${ech(c.name)}" title="Zones de cette caméra">Zones</button>
          <button data-plein="${ech(c.name)}" title="Plein écran">Plein écran</button>
        </span>
      </div>
      <div class="tuile-bas">
        <span class="pastille ${e}"></span>
        <span class="tuile-nom">${ech(c.name)}</span>
        <span class="tuile-tech">${ech(tech)}</span>
      </div>
    </div>`;
}

function dessinerMur() {
  const plan = el("direct-plan");
  const mur = el("mur");
  const bande = el("bande");

  if (!cameras.length) {
    plan.classList.remove("focus");
    mur.className = "mur c1";
    mur.innerHTML = `<div class="vide mur-vide"><div>
      <div class="vide-titre">Aucune caméra configurée</div>
      <p>Ajoutez-en une depuis Paramètres → Caméras, ou déposez une vidéo
         dans Paramètres → Fichiers de test pour essayer sans caméra.</p>
    </div></div>`;
    bande.innerHTML = "";
    el("mur-page").textContent = "";
    el("prec-vue").disabled = el("suiv-vue").disabled = true;
    return;
  }

  // Une caméra choisie s'agrandit ; les autres se rangent en colonne et
  // restent visibles. L'agent garde le site entier sous les yeux.
  const principale = cameras.find((c) => c.name === selection);
  plan.classList.toggle("focus", Boolean(principale));

  if (principale) {
    mur.className = "mur";
    mur.innerHTML = tuile(principale, true);
    bande.innerHTML = cameras.filter((c) => c !== principale).map((c) => tuile(c, false)).join("");
    el("mur-page").textContent = `${principale.name} — clic pour revenir à la mosaïque`;
    el("prec-vue").disabled = el("suiv-vue").disabled = true;
  } else {
    const parPage = colonnes * colonnes;
    const pages = Math.max(1, Math.ceil(cameras.length / parPage));
    vuePage = Math.min(Math.max(0, vuePage), pages - 1);
    const debut = vuePage * parPage;
    const lot = cameras.slice(debut, debut + parPage);

    mur.className = `mur c${colonnes}`;
    mur.innerHTML = lot.map((c) => tuile(c, false)).join("");
    bande.innerHTML = "";
    el("mur-page").textContent = pages > 1
      ? `Vue ${vuePage + 1}/${pages} · caméras ${debut + 1}–${debut + lot.length} sur ${cameras.length}`
      : `${cameras.length} caméra${cameras.length > 1 ? "s" : ""}`;
    el("prec-vue").disabled = vuePage === 0;
    el("suiv-vue").disabled = vuePage + 1 >= pages;
  }

  brancherTuiles();
}

function brancherTuiles() {
  document.querySelectorAll("#direct-plan [data-cam]").forEach((n) =>
    n.addEventListener("click", () => {
      selection = selection === n.dataset.cam ? null : n.dataset.cam;
      dessinerMur();
      majActions();
    }));

  const sans = (n, fn) => n.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
  document.querySelectorAll("[data-plein]").forEach((n) => sans(n, () => ouvrirVisionneuse(n.dataset.plein)));
  document.querySelectorAll("[data-zones]").forEach((n) => sans(n, () => {
    allerA("config");
    ouvrirSection("zones");
    choisirCameraZone(n.dataset.zones);
    el("zone-camera").value = n.dataset.zones;
  }));
  document.querySelectorAll("[data-pause]").forEach((n) => sans(n, () => basculerPause(n.dataset.pause)));
}

/* Mettre une caméra en pause libère du calcul sans perdre sa configuration.
   On renvoie l'objet complet : l'API remplit les champs absents par leurs
   valeurs par défaut, ce qui effacerait les options en cours. */
async function basculerPause(nom) {
  const c = cameras.find((x) => x.name === nom);
  if (!c) return;
  try {
    await api(`/api/cameras/${encodeURIComponent(nom)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: c.source, models: c.models, fps: c.fps, imgsz: c.imgsz,
        workers: c.workers, enabled: !c.enabled, tracking: c.tracking,
        recording: c.recording, plates: c.plates, collecte: c.collecte,
        bachage: c.bachage, voisins: c.voisins || [],
        segment_minutes: c.segment_minutes, retention_days: c.retention_days,
      }),
    });
    avis(nom, c.enabled ? "Caméra mise en pause." : "Caméra reprise.", "ok");
    await chargerCameras();
  } catch (err) {
    avis("Changement refusé", err.message, "err");
  }
}

function rafraichirFlux() {
  const t = Date.now();
  document.querySelectorAll("[data-flux]").forEach((img) => {
    img.src = `/video/${encodeURIComponent(img.dataset.flux)}.jpg?t=${t}`;
  });
}

function ouvrirClip(url) {
  el("visionneuse-nom").textContent = "Clip de l'alerte";
  el("visionneuse-etat").textContent = "";
  el("visionneuse-image").hidden = true;
  const v = el("visionneuse-video");
  v.hidden = false;
  v.src = url;
  v.play().catch(() => { /* lecture différée par le navigateur */ });
  el("visionneuse").hidden = false;
}

function ouvrirVisionneuse(nom) {
  el("visionneuse-video").hidden = true;
  el("visionneuse-video").pause?.();
  el("visionneuse-image").hidden = false;
  visionnee = nom;
  const c = cameras.find((x) => x.name === nom);
  el("visionneuse-nom").textContent = nom;
  el("visionneuse-etat").innerHTML = c?.online
    ? '<span class="rec direct">DIRECT</span>' : '<span class="rec perdu">HORS LIGNE</span>';
  el("visionneuse-image").src = `/video/${encodeURIComponent(nom)}.jpg?t=${Date.now()}`;
  el("visionneuse").hidden = false;
}

function fermerVisionneuse() {
  const v = el("visionneuse-video");
  if (v) { v.pause?.(); v.removeAttribute("src"); v.hidden = true; }
  visionnee = null;
  el("visionneuse").hidden = true;
}

function outilsDirect() {
  el("disposition").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    document.querySelectorAll("#disposition .bouton").forEach((x) => x.classList.remove("actif"));
    b.classList.add("actif");
    colonnes = Number(b.dataset.cols);
    vuePage = 0;
    dessinerMur();
  });

  el("prec-vue").addEventListener("click", () => { vuePage--; dessinerMur(); });
  el("suiv-vue").addEventListener("click", () => { vuePage++; dessinerMur(); });

  el("btn-tech").addEventListener("click", (e) => {
    infosTech = !infosTech;
    e.currentTarget.classList.toggle("actif", infosTech);
    dessinerMur();
  });

  el("btn-plein-ecran").addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else el("vue-direct").requestFullscreen?.();
  });

  el("vers-historique").addEventListener("click", () => allerA("historique"));

  el("btn-recharger").addEventListener("click", chargerCameras);
  el("btn-modifier").addEventListener("click", () => selection && ouvrirFormCamera(selection));
  el("btn-supprimer").addEventListener("click", () => selection && supprimerCamera(selection));
  el("visionneuse-fermer").addEventListener("click", fermerVisionneuse);

  el("btn-aide").addEventListener("click", () => { el("aide").hidden = false; });
  el("aide-fermer").addEventListener("click", () => { el("aide").hidden = true; });
}

/* ═══ Configuration d'une caméra ═══ */

function ouvrirFormCamera(nom = null) {
  const f = el("form-camera");
  f.hidden = false;
  el("form-camera-titre").textContent = nom ? `Modifier « ${nom} »` : "Nouvelle caméra";
  el("cam-msg").textContent = "";
  el("cam-msg").className = "msg";

  const cases = el("cam-modeles");
  if (!cases.children.length) {
    cases.innerHTML = Object.entries(MODELES).filter(([k]) => k !== "systeme")
      .map(([k, v]) => `<label><input type="checkbox" value="${k}" /> ${v}</label>`).join("");
  }
  cases.querySelectorAll("input").forEach((c) => (c.checked = false));

  const c = cameras.find((x) => x.name === nom);
  el("cam-nom").value = c?.name ?? "";
  el("cam-nom").disabled = Boolean(nom);
  el("cam-source").value = c?.source ?? "";
  el("cam-fps").value = c?.fps ?? "";
  el("cam-actif").value = String(c?.enabled ?? true);
  el("cam-suivi").value = String(c?.tracking ?? false);
  el("cam-plaques").value = String(c?.plates ?? false);
  el("cam-bachage").value = String(c?.bachage ?? false);
  el("cam-collecte").value = String(c?.collecte ?? false);
  el("cam-enregistrement").value = String(c?.recording ?? false);
  el("cam-voisins").value = (c?.voisins || []).join(", ");
  c?.models.forEach((m) => {
    const b = cases.querySelector(`input[value="${m}"]`);
    if (b) b.checked = true;
  });

  choixFichiers();
  f.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* Choisir un fichier déposé d'un clic : recopier un chemin à la main est la
   première source d'erreur au moment de créer une caméra. */
function choixFichiers() {
  const box = el("choix-fichiers");
  if (!fichiers.length) {
    box.innerHTML = '<span class="msg">Aucun fichier déposé — voir l\'onglet Fichiers.</span>';
    return;
  }
  box.innerHTML = fichiers.slice(0, 8).map((f) =>
    `<button type="button" class="bouton" data-src="${ech(f.source)}">${ech(f.nom)}</button>`).join("");
  box.querySelectorAll("[data-src]").forEach((b) =>
    b.addEventListener("click", () => { el("cam-source").value = b.dataset.src; }));
}

function donneesCamera() {
  const s = el("cam-source").value.trim();
  return {
    source: /^\d+$/.test(s) ? Number(s) : s,
    models: [...el("cam-modeles").querySelectorAll("input:checked")].map((c) => c.value),
    fps: el("cam-fps").value ? Number(el("cam-fps").value) : null,
    enabled: el("cam-actif").value === "true",
    tracking: el("cam-suivi").value === "true",
    plates: el("cam-plaques").value === "true",
    bachage: el("cam-bachage").value === "true",
    collecte: el("cam-collecte").value === "true",
    recording: el("cam-enregistrement").value === "true",
    voisins: el("cam-voisins").value.split(",").map((v) => v.trim()).filter(Boolean),
  };
}

function formCamera() {
  el("btn-ajout-camera").addEventListener("click", () => ouvrirFormCamera());
  el("cam-annuler").addEventListener("click", () => (el("form-camera").hidden = true));

  // Ces deux options reposent sur le vote entre plusieurs images du même objet :
  // sans suivi, elles n'ont rien sur quoi voter.
  for (const id of ["cam-plaques", "cam-bachage"]) {
    el(id).addEventListener("change", (e) => {
      if (e.target.value === "true" && el("cam-suivi").value !== "true") {
        el("cam-suivi").value = "true";
        avis("Suivi activé", "Cette option repose sur le suivi des objets.");
      }
    });
  }

  el("cam-tester").addEventListener("click", async () => {
    const m = el("cam-msg");
    const source = donneesCamera().source;
    if (source === "") { m.className = "msg err"; m.textContent = "Renseignez une source."; return; }
    m.className = "msg"; m.textContent = "Test…";
    try {
      const r = await api("/api/cameras/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source }),
      });
      if (r.ok) {
        m.className = "msg ok";
        m.textContent = `${r.kind} — ${r.width}×${r.height}` + (r.fps ? ` — ${r.fps} img/s` : "");
      } else { m.className = "msg err"; m.textContent = r.error; }
    } catch (e) { m.className = "msg err"; m.textContent = e.message; }
  });

  el("cam-valider").addEventListener("click", async () => {
    const m = el("cam-msg");
    const nom = el("cam-nom").value.trim();
    if (!nom) { m.className = "msg err"; m.textContent = "Donnez un nom."; return; }
    try {
      await api(`/api/cameras/${encodeURIComponent(nom)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(donneesCamera()),
      });
      el("form-camera").hidden = true;
      avis("Caméra enregistrée", "Prise en compte dans quelques secondes.", "ok");
      chargerCameras();
    } catch (e) { m.className = "msg err"; m.textContent = e.message; }
  });
}

async function supprimerCamera(nom) {
  const ok = await confirmer(`Supprimer « ${nom} » ?`,
    "La caméra ne sera plus traitée. Ses zones et son historique sont conservés.", "Supprimer");
  if (!ok) return;
  try {
    await api(`/api/cameras/${encodeURIComponent(nom)}`, { method: "DELETE" });
    avis("Caméra supprimée", nom, "ok");
    chargerCameras();
  } catch (e) { avis("Suppression impossible", e.message, "err"); }
}

/* ═══ Événements du panneau ═══ */

async function chargerEvenements() {
  let d;
  try { d = await api("/api/alerts?limit=12&acknowledged=false"); } catch { return; }

  el("vivantes-compte").textContent = d.total ?? d.items.length;
  const box = el("evenements");
  if (!d.items.length) {
    box.innerHTML = '<div class="vide" style="padding:36px 18px"><p>Aucune alerte à traiter.</p></div>';
    return;
  }
  box.innerHTML = d.items.map((a) => `
    <button class="carte ${a.severity}" data-cam="${ech(a.camera)}">
      <span class="carte-tete">
        <span class="sev ${a.severity}">${a.severity}</span>
        <span class="carte-heure">${heure(a.timestamp)}</span>
      </span>
      <span class="carte-titre">${ech(a.label)}</span>
      <span class="carte-pied">
        <span class="carte-cam">${ech(a.camera)}${a.zone ? " — " + ech(a.zone) : ""}</span>
        <span class="carte-mod">${ech(a.model)}</span>
      </span>
    </button>`).join("");

  box.querySelectorAll("[data-cam]").forEach((n) =>
    n.addEventListener("click", () => {
      allerA("direct");
      selection = n.dataset.cam;
      dessinerMur();
      majActions();
    }));
}

/* ═══ Historique ═══ */

function filtres() {
  const p = new URLSearchParams();
  const champs = {
    model: "f-modele", camera: "f-camera", zone: "f-zone", severity: "f-gravite",
    acknowledged: "f-traite", false_positive: "f-fausse", since_hours: "f-periode",
  };
  for (const [k, id] of Object.entries(champs)) {
    const v = el(id)?.value;
    if (v) p.set(k, v);
  }
  const cl = el("f-classe")?.value.trim();
  if (cl) p.set("label", cl);
  const pl = el("f-plaque")?.value.trim();
  if (pl) p.set("plaque", pl);
  const poste = el("f-poste")?.value;
  if (poste) { const [a, b] = poste.split("-"); p.set("hour_from", a); p.set("hour_to", b); }
  return p;
}

function resumerFiltres() {
  const lu = (id, defaut) => {
    const n = el(id);
    if (!n || !n.value) return defaut;
    return n.selectedOptions ? n.selectedOptions[0].textContent.trim() : n.value;
  };
  el("filtres-resume").textContent = [
    lu("f-periode", "tout l'historique"),
    lu("f-camera", "toutes caméras"),
    lu("f-gravite", "toutes gravités"),
  ].join(" · ");
}

async function chargerAlertes() {
  resumerFiltres();
  const p = filtres();
  p.set("limit", PAR_PAGE);
  p.set("offset", page * PAR_PAGE);
  const d = await api(`/api/alerts?${p}`);

  if (!d.items.length) {
    vide("tableau-alertes", "Aucune alerte",
      "Aucun événement ne correspond à ces critères. Élargissez la période ou retirez un filtre.");
    el("pagination").innerHTML = "";
    return;
  }

  el("tableau-alertes").innerHTML = `
    <table class="liste">
      <thead><tr>
        <th style="width:64px"></th><th>Détection</th><th>Caméra</th><th>Modèle</th>
        <th style="width:70px">Gravité</th><th style="width:54px">Conf.</th>
        <th style="width:130px">Horodatage</th><th style="width:200px">Traitement</th>
      </tr></thead>
      <tbody>${d.items.map(ligneAlerte).join("")}</tbody>
    </table>`;

  el("tableau-alertes").querySelectorAll("[data-ack]").forEach((b) =>
    b.addEventListener("click", () => prendreEnCharge(b.dataset.ack, b)));
  el("tableau-alertes").querySelectorAll("[data-faux]").forEach((b) =>
    b.addEventListener("click", () => marquer(b.dataset.faux, true)));
  el("tableau-alertes").querySelectorAll("[data-vrai]").forEach((b) =>
    b.addEventListener("click", () => marquer(b.dataset.vrai, false)));
  el("tableau-alertes").querySelectorAll("[data-suppr]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await api(`/api/alerts/${b.dataset.suppr}`, { method: "DELETE" });
        chargerAlertes();
        chargerFrise();
      } catch (e) { avis("Suppression impossible", e.message, "err"); }
    }));

  el("tableau-alertes").querySelectorAll("[data-clip]").forEach((n) =>
    n.addEventListener("click", () => ouvrirClip(n.dataset.clip)));

  el("tableau-alertes").querySelectorAll("[data-img]").forEach((n) =>
    n.addEventListener("click", () => window.open(n.dataset.img, "_blank")));

  pagination(d);
}

function ligneAlerte(a) {
  const clipUrl = a.clip ? `/api/clip?path=${encodeURIComponent(a.clip)}` : null;
  const image = a.snapshot
    ? `<img class="vignette" src="/api/snapshot?path=${encodeURIComponent(a.snapshot)}"
            ${clipUrl ? `data-clip="${clipUrl}" title="Voir le clip"`
    : `data-img="/api/snapshot?path=${encodeURIComponent(a.snapshot)}"`} alt="" />`
    : "";
  const vign = clipUrl ? `<span class="avec-clip">${image}</span>` : image;
  // Un clip vaut mieux qu'une image : il montre ce qui s'est passé avant et
  // après, ce qu'une capture ne dira jamais.
  const clip = clipUrl
    ? ` <button class="bouton" data-clip="${clipUrl}" style="padding:3px 9px">▶ clip</button>`
    : ' <span class="msg">clip en cours…</span>';
  const zone = a.zone ? ` <span class="etiq zone">${ech(a.zone)}</span>` : "";
  const plaque = a.plaque ? ` <span class="etiq plaque">${ech(a.plaque)}</span>` : "";
  const faux = a.false_positive ? ' <span class="etiq">fausse</span>' : "";

  const traitement = a.acknowledged
    ? `<span class="msg">${ech(a.ack_by || "traitée")}</span>`
    : `<button class="bouton" data-ack="${a.id}">Prendre en charge</button>`;
  const jugement = a.false_positive
    ? `<button class="bouton" data-vrai="${a.id}">Vraie</button>`
    : `<button class="bouton danger" data-faux="${a.id}"
               title="Le système s'est trompé — sert aussi à corriger le modèle">Fausse</button>`;

  return `<tr class="${a.severity}${a.false_positive ? " fausse" : ""}">
    <td>${vign}</td>
    <td>${ech(a.label)}${faux}${zone}${plaque}</td>
    <td>${ech(a.camera)}<br>${clip}</td>
    <td>${nomModele(a.model)}</td>
    <td><span class="sev ${a.severity}">${a.severity}</span></td>
    <td class="num">${a.confidence.toFixed(2)}</td>
    <td class="num">${dateHeure(a.timestamp)}</td>
    <td><div class="enligne">${traitement}${jugement}
      <button class="bouton danger" data-suppr="${a.id}" title="Effacer cette alerte"
              style="padding:3px 9px">✕</button></div></td>
  </tr>`;
}

function pagination(d) {
  const pages = Math.ceil(d.total / PAR_PAGE);
  const p = el("pagination");
  if (pages <= 1) { p.innerHTML = `<span>${d.total} alerte(s)</span>`; return; }
  p.innerHTML = `
    <button class="bouton" ${page === 0 ? "disabled" : ""} id="page-prec">Précédent</button>
    <span>Page ${page + 1} / ${pages} — ${d.total} alerte(s)</span>
    <button class="bouton" ${page + 1 >= pages ? "disabled" : ""} id="page-suiv">Suivant</button>`;
  el("page-prec")?.addEventListener("click", () => { page--; chargerAlertes(); });
  el("page-suiv")?.addEventListener("click", () => { page++; chargerAlertes(); });
}

async function prendreEnCharge(id, bouton) {
  bouton.disabled = true;
  await api(`/api/alerts/${id}/ack`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operator: OPERATEUR }),
  });
  chargerAlertes();
}

async function marquer(id, fausse) {
  await api(`/api/alerts/${id}/false`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_false: fausse, operator: OPERATEUR }),
  });
  if (fausse) avis("Marquée fausse", "L'image rejoint le jeu de données du ré-entraînement.", "ok");
  chargerAlertes();
}

function brancherFiltres() {
  ["f-modele", "f-camera", "f-zone", "f-gravite", "f-traite", "f-fausse", "f-periode", "f-poste"]
    .forEach((id) => el(id)?.addEventListener("change", () => { page = 0; chargerAlertes(); }));
  const rech = attendre(() => { page = 0; chargerAlertes(); }, 350);
  el("f-classe")?.addEventListener("input", rech);
  el("f-plaque")?.addEventListener("input", rech);
}

function attendre(fn, delai) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), delai); };
}

function remplirFiltres() {
  el("f-modele").innerHTML = '<option value="">Tous modèles</option>'
    + Object.entries(MODELES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  const opts = cameras.map((c) => `<option value="${ech(c.name)}">${ech(c.name)}</option>`).join("");
  el("f-camera").innerHTML = '<option value="">Toutes caméras</option>' + opts;
  el("frise-camera").innerHTML = '<option value="">Toutes les caméras</option>' + opts;
  const zonesVues = new Set();
  cameras.forEach((c) => c.zones?.forEach((z) => zonesVues.add(z)));
  el("f-zone").innerHTML = '<option value="">Toutes zones</option>'
    + [...zonesVues].map((z) => `<option value="${ech(z)}">${ech(z)}</option>`).join("");
}

/* ═══ Frise ═══ */

async function chargerFrise() {
  const cam = el("frise-camera")?.value || "";
  const p = new URLSearchParams();
  if (cam) p.set("camera", cam);
  if (jourFrise) p.set("day", jourFrise);

  const d = await api(`/api/timeline?${p}`);
  jourFrise = d.day;

  // N'afficher que les journées ayant produit des alertes : un calendrier
  // majoritairement vide ne rend service à personne.
  const jours = d.jours_disponibles.length ? d.jours_disponibles : [d.day];
  el("frise-jour").innerHTML = jours.map((j) =>
    `<option value="${j}" ${j === d.day ? "selected" : ""}>${formatJour(j)}</option>`).join("");

  el("frise-heures").innerHTML = [0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) =>
    `<span style="left:${(h / 24) * 100}%">${String(h).padStart(2, "0")}h</span>`).join("");

  const f = el("frise");
  if (!d.alertes.length) {
    f.innerHTML = '<div class="frise-vide">Aucune alerte ce jour-là.</div>';
    el("frise-detail").textContent = "";
    return;
  }
  f.innerHTML = d.alertes.map((a) => `
    <button class="marque ${a.severity}${a.false_positive ? " fausse" : ""}"
            style="left:${a.position * 100}%" data-id="${a.id}"
            title="${heure(a.timestamp)} — ${ech(a.label)} (${ech(a.camera)})"></button>`).join("");

  f.querySelectorAll(".marque").forEach((m) =>
    m.addEventListener("click", () => {
      f.querySelectorAll(".marque").forEach((x) => x.classList.remove("selection"));
      m.classList.add("selection");
      const a = d.alertes.find((x) => x.id === Number(m.dataset.id));
      el("frise-detail").textContent =
        `${heure(a.timestamp)} — ${a.label} · ${a.camera}${a.zone ? " · " + a.zone : ""}`;
    }));
}

function formatJour(iso) {
  return new Date(`${iso}T12:00:00`)
    .toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
}

function brancherFrise() {
  el("frise-jour").addEventListener("change", (e) => { jourFrise = e.target.value; chargerFrise(); });
  el("frise-camera").addEventListener("change", () => { jourFrise = null; chargerFrise(); });
}

/* ═══ Fichiers ═══ */

async function chargerFichiers() {
  try { fichiers = (await api("/api/uploads")).fichiers; } catch { fichiers = []; }

  if (!fichiers.length) {
    vide("liste-fichiers", "Aucun fichier déposé",
      "Déposez une vidéo de chantier, de départ de feu ou de quai de chargement : elle sera analysée comme une caméra réelle.");
    choixFichiers();
    return;
  }

  el("liste-fichiers").innerHTML = `
    <table class="liste">
      <thead><tr><th>Fichier</th><th style="width:80px">Type</th><th style="width:80px">Taille</th>
      <th style="width:220px"></th></tr></thead>
      <tbody>${fichiers.map((f) => `
        <tr>
          <td>${ech(f.nom)}<br><span class="msg">${ech(f.source)}</span></td>
          <td>${f.type}</td>
          <td class="num">${f.taille_mo} Mo</td>
          <td><div class="enligne">
            <button class="bouton primaire" data-essai="${ech(f.source)}">Analyser</button>
            <button class="bouton" data-use="${ech(f.source)}">Créer une caméra</button>
            <button class="bouton danger" data-del="${ech(f.nom)}">Supprimer</button>
          </div></td>
        </tr>`).join("")}</tbody>
    </table>`;

  el("liste-fichiers").querySelectorAll("[data-essai]").forEach((b) =>
    b.addEventListener("click", () => analyser(b.dataset.essai)));

  el("liste-fichiers").querySelectorAll("[data-use]").forEach((b) =>
    b.addEventListener("click", () => {
      allerA("config");
      ouvrirSection("cameras");
      ouvrirFormCamera();
      el("cam-source").value = b.dataset.use;
      el("cam-nom").value = b.dataset.use.split("/").pop().replace(/\.[^.]+$/, "").slice(0, 40);
    }));

  el("liste-fichiers").querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", async () => {
      const ok = await confirmer(`Supprimer « ${b.dataset.del} » ?`,
        "Le fichier est effacé du serveur. Une caméra qui l'utilise doit être supprimée d'abord.",
        "Supprimer");
      if (!ok) return;
      try {
        await api(`/api/uploads/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" });
        avis("Fichier supprimé", b.dataset.del, "ok");
        chargerFichiers();
      } catch (e) { avis("Suppression impossible", e.message, "err"); }
    }));

  choixFichiers();
}

/* Éprouver un modèle ne doit pas demander de créer une caméra : on braque une
   source d'essai sur le fichier et on regarde. La caméra « essai » est réservée
   à cet usage et réutilisée à chaque fois — sinon la liste se remplirait d'une
   caméra par vidéo testée, ce qui est arrivé. */
const CAMERA_ESSAI = "essai";

async function analyser(source) {
  let modeles = [];
  try {
    const s = await api("/api/settings");
    modeles = Object.entries(s).filter(([, v]) => v.detect).map(([m]) => m);
  } catch { /* on tentera sans liste */ }

  try {
    await api(`/api/cameras/${CAMERA_ESSAI}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source, models: modeles, enabled: true, tracking: true, fps: 2,
      }),
    });
  } catch (e) {
    avis("Analyse impossible", e.message, "err");
    return;
  }

  avis("Analyse lancée", `${modeles.length} modèle(s) — quelques secondes de chargement.`, "ok");
  allerA("direct");
  selection = CAMERA_ESSAI;
  await chargerCameras();
}

function brancherDepot() {
  const z = el("depot");
  const entree = el("fichier-entree");

  z.addEventListener("click", () => entree.click());
  entree.addEventListener("change", () => {
    if (entree.files.length) envoyer(entree.files[0]);
    entree.value = "";
  });
  ["dragenter", "dragover"].forEach((e) =>
    z.addEventListener(e, (ev) => { ev.preventDefault(); z.classList.add("survol"); }));
  ["dragleave", "drop"].forEach((e) =>
    z.addEventListener(e, (ev) => { ev.preventDefault(); z.classList.remove("survol"); }));
  z.addEventListener("drop", (ev) => {
    if (ev.dataTransfer.files.length) envoyer(ev.dataTransfer.files[0]);
  });
}

/* XMLHttpRequest plutôt que fetch : c'est le seul moyen d'afficher une
   progression, et un fichier vidéo peut prendre une minute à monter. */
function envoyer(fichier) {
  const msg = el("depot-msg");
  const jauge = el("jauge");
  const barre = jauge.querySelector("span");

  msg.className = "msg";
  msg.textContent = `Envoi de ${fichier.name}…`;
  jauge.hidden = false;
  barre.style.width = "0%";

  const form = new FormData();
  form.append("file", fichier);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/uploads");
  xhr.upload.addEventListener("progress", (e) => {
    if (e.lengthComputable) barre.style.width = `${(e.loaded / e.total) * 100}%`;
  });
  xhr.addEventListener("load", () => {
    jauge.hidden = true;
    let r = {};
    try { r = JSON.parse(xhr.responseText); } catch { /* réponse illisible */ }
    if (xhr.status === 200) {
      const a = r.apercu || {};
      msg.className = "msg ok";
      msg.textContent = `${r.nom} — ${r.taille_mo} Mo` + (a.width ? `, ${a.width}×${a.height}` : "");
      chargerFichiers();
      if (r.source) analyser(r.source);
      else avis("Fichier importé", "Utilisable comme source de caméra.", "ok");
    } else {
      msg.className = "msg err";
      msg.textContent = r.detail || `Échec (${xhr.status})`;
      avis("Import refusé", msg.textContent, "err");
    }
  });
  xhr.addEventListener("error", () => {
    jauge.hidden = true;
    msg.className = "msg err";
    msg.textContent = "Connexion interrompue pendant l'envoi.";
  });
  xhr.send(form);
}

/* ═══ Analyse ═══ */

async function chargerAnalyse() {
  const [resume, frise, qualite] = await Promise.all([
    api("/api/stats/summary"), api("/api/stats/timeline?hours=24"),
    api("/api/stats/quality?days=30"),
  ]);

  const mtta = qualite.delai_prise_en_charge_s;
  const parCam = Object.values(qualite.par_camera || {});
  const pire = parCam.length ? Math.max(...parCam.map((c) => c.fausses_par_jour)) : null;

  el("indicateurs").innerHTML = `
    ${indic("Alertes 24 h", resume.total_24h)}
    ${indic("Critiques 24 h", resume.critiques_24h, resume.critiques_24h > 0)}
    ${indic("À traiter", resume.non_acquittees, resume.non_acquittees > 0)}
    ${indic("Alertes 7 jours", resume.total_7d)}
    ${indic("Délai moyen de prise en charge",
    mtta == null ? "–" : mtta < 90 ? `${mtta} s` : `${Math.round(mtta / 60)} min`)}
    ${indic("Fausses alertes / jour / caméra (pire)",
      pire == null ? "–" : pire.toFixed(1), pire > 2)}`;

  const max = Math.max(1, ...frise.map((t) => t.total));
  el("histo-24h").innerHTML = frise.map((t) => `
    <div class="colonne" title="${t.heure} — ${t.total} alerte(s), ${t.critique} critique(s)">
      <div class="barre ${t.critique ? "crit" : ""}" style="height:${(t.total / max) * 100}%"></div>
      <div class="colonne-lab">${t.heure}</div>
    </div>`).join("");

  rangs("rangs-modele", resume.par_modele_7j, nomModele);
  rangs("rangs-zone", resume.par_zone_7j, (k) => k || "plein cadre");
  fiabilite(qualite);
  faussesParCamera(qualite);

}

function indic(nom, valeur, alarme = false) {
  return `<div class="indic">
    <div class="indic-lab">${ech(String(nom)).toUpperCase()}</div>
    <div class="indic-val ${alarme ? "crit" : ""}">${valeur}</div>
  </div>`;
}

function rangs(cible, data, libelle) {
  const e = Object.entries(data || {}).sort((a, b) => b[1] - a[1]);
  if (!e.length) { el(cible).innerHTML = '<p class="msg">Aucune donnée.</p>'; return; }
  const max = Math.max(...e.map(([, v]) => v));
  el(cible).innerHTML = e.map(([k, v]) => `
    <div class="rang">
      <div class="rang-nom">${ech(libelle(k))}</div>
      <div class="rang-piste"><div class="rang-part" style="width:${(v / max) * 100}%"></div></div>
      <div class="rang-val">${v}</div>
    </div>`).join("");
}

function fiabilite(q) {
  const e = Object.entries(q.par_modele || {});
  if (!e.length) {
    el("rangs-fiabilite").innerHTML =
      `<p class="msg">Aucune alerte marquée. Le bouton « Fausse » alimente cet indicateur —
       c'est lui qui désigne le modèle à corriger.</p>`;
    return;
  }
  el("rangs-fiabilite").innerHTML = e.sort((a, b) => b[1].taux_faux - a[1].taux_faux)
    .map(([m, s]) => {
      const pct = Math.round(s.taux_faux * 100);
      const cls = pct >= 40 ? "mauvais" : pct >= 15 ? "moyen" : "bon";
      return `<div class="rang">
        <div class="rang-nom">${nomModele(m)}</div>
        <div class="rang-piste"><div class="rang-part ${cls}" style="width:${pct}%"></div></div>
        <div class="rang-val">${pct}% · ${s.fausses}/${s.alertes}</div>
      </div>`;
    }).join("");
}

function faussesParCamera(q) {
  const e = Object.entries(q.par_camera || {});
  if (!e.length) { el("rangs-fausses").innerHTML = '<p class="msg">Aucune donnée.</p>'; return; }
  el("rangs-fausses").innerHTML = e.map(([c, s]) => {
    const trop = s.fausses_par_jour > 2;
    return `<div class="rang">
      <div class="rang-nom">${ech(c)}</div>
      <div class="rang-piste"><div class="rang-part ${trop ? "mauvais" : "bon"}"
           style="width:${Math.min(100, (s.fausses_par_jour / 4) * 100)}%"></div></div>
      <div class="rang-val">${s.fausses_par_jour}/j</div>
    </div>`;
  }).join("");
}

/* ═══ Système ═══ */

async function chargerSysteme() {
  const [h, rapp] = await Promise.all([
    api("/api/health"),
    api("/api/handoffs").catch(() => ({ correspondances: [] })),
  ]);
  const m = h.machine || {};

  el("systeme-machine").innerHTML = `
    ${indic("Pipeline de détection", h.pipeline.running ? "actif" : "arrêté", !h.pipeline.running)}
    ${indic("Caméras actives", `${h.cameras_actives} / ${h.cameras_configurees}`)}
    ${indic("Processeur", m.cpu_percent != null ? `${m.cpu_percent} %` : "–", (m.cpu_percent ?? 0) > 90)}
    ${indic("Mémoire", m.memory_percent != null ? `${m.memory_percent} %` : "–", (m.memory_percent ?? 0) > 90)}
    ${indic("Disque libre", m.disk_free_gb != null ? `${m.disk_free_gb} Go` : "–", (m.disk_free_gb ?? 99) < 5)}`;

  const lignes = Object.entries(h.cameras || {});
  el("systeme-cameras").innerHTML = lignes.length ? `
    <thead><tr><th>Caméra</th><th style="width:110px">État</th><th style="width:90px">Cycle</th>
    <th style="width:90px">Modèles</th><th style="width:90px">Objets</th><th>Erreur</th></tr></thead>
    <tbody>${lignes.map(([n, c]) => `
      <tr>
        <td>${ech(n)}</td>
        <td><span class="sev ${c.state === "en ligne" ? "moyenne" : "critique"}">${ech(c.state || "inconnu")}</span></td>
        <td class="num">${c.cycle_ms ? c.cycle_ms + " ms" : "–"}</td>
        <td class="num">${c.modeles_actifs ?? "–"}</td>
        <td class="num">${c.objets_suivis ?? "–"}</td>
        <td class="msg">${ech(c.error || "")}</td>
      </tr>`).join("")}</tbody>`
    : '<tbody><tr><td class="msg">Le pipeline n\'a publié aucun état.</td></tr></tbody>';

  const corr = rapp.correspondances || [];
  el("systeme-rapprochements").innerHTML = corr.length ? `
    <thead><tr><th style="width:80px">Heure</th><th>Trajet</th><th style="width:110px">Classe</th>
    <th style="width:100px">Plaque</th><th style="width:170px">Fiabilité</th></tr></thead>
    <tbody>${corr.map((c) => `
      <tr>
        <td class="num">${new Date(c.horodatage * 1000).toLocaleTimeString("fr-FR")}</td>
        <td>${ech(c.de)} → ${ech(c.vers)}</td>
        <td>${ech(c.classe)}</td>
        <td class="num">${ech(c.plaque || "–")}</td>
        <td>${c.certain ? '<span class="etiq plaque">plaque — certain</span>'
      : `<span class="etiq">apparence — ${c.score}</span>`}</td>
      </tr>`).join("")}</tbody>`
    : '<tbody><tr><td class="msg">Aucun rapprochement. Il faut au moins deux caméras avec suivi.</td></tr></tbody>';
}

/* ═══ Barre d'état ═══ */

async function majBarreEtat() {
  try {
    const [h, resume] = await Promise.all([api("/api/health"), api("/api/stats/summary")]);
    const actif = h.pipeline.running;
    const enLigne = cameras.filter((c) => c.online).length;

    el("puce-pastille").className = `pastille ${actif ? "" : "hors-ligne"}`;
    el("puce-pastille").style.background = actif ? "var(--ok)" : "var(--crit)";
    el("puce-cameras").textContent = actif
      ? `${cameras.length} caméra${cameras.length > 1 ? "s" : ""} · ${enLigne} en ligne`
      : "détection arrêtée";

    const crit = resume.critiques_24h || 0;
    const puce = el("puce-crit");
    puce.hidden = crit === 0;
    puce.textContent = `${crit} alerte${crit > 1 ? "s" : ""} critique${crit > 1 ? "s" : ""}`;

    el("badge-alertes").textContent = resume.non_acquittees || "";
  } catch {
    el("puce-cameras").textContent = "interface injoignable";
    el("puce-pastille").style.background = "var(--crit)";
  }
}

/* Le son : un agent ne regarde pas l'écran en permanence. Une alerte critique
   doit pouvoir l'appeler. Synthétisé sur place — aucun fichier à charger, donc
   rien à télécharger sur un site coupé d'Internet. */
let sonActif = localStorage.getItem("ciments_eye-son") !== "non";

function brancherSon() {
  const b = el("btn-son");
  const rendre = () => {
    b.classList.toggle("actif", sonActif);
    b.textContent = sonActif ? "Son activé" : "Son coupé";
    b.title = sonActif ? "Couper le son des alertes" : "Rétablir le son des alertes";
  };
  rendre();
  b.addEventListener("click", () => {
    sonActif = !sonActif;
    localStorage.setItem("ciments_eye-son", sonActif ? "oui" : "non");
    rendre();
    if (sonActif) sonner();
  });
}

function sonner() {
  if (!sonActif) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    // Deux notes brèves : un bip continu se confond avec un bruit d'atelier.
    [0, 0.22].forEach((retard, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "square";
      o.frequency.value = i === 0 ? 880 : 660;
      g.gain.setValueAtTime(0.0001, ctx.currentTime + retard);
      g.gain.exponentialRampToValueAtTime(0.22, ctx.currentTime + retard + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + retard + 0.18);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + retard);
      o.stop(ctx.currentTime + retard + 0.2);
    });
    setTimeout(() => ctx.close(), 1200);
  } catch { /* le navigateur refuse le son tant que rien n'a été cliqué */ }
}

/* ═══ Alertes critiques en direct ═══ */

async function guetter() {
  let d;
  try { d = await api("/api/alerts?limit=1&severity=critique&since_hours=1"); } catch { return; }
  const a = d.items[0];
  if (!a) return;
  if (derniereCritique === null) { derniereCritique = a.id; return; }
  if (a.id === derniereCritique) return;
  derniereCritique = a.id;

  avis(`${a.label} — ${a.camera}`, a.zone ? `Zone ${a.zone}` : "", "critique");
  sonner();

  if (el("suivi-alertes")?.checked) {
    // La caméra concernée n'est peut-être pas sur la vue affichée : on y va.
    const i = cameras.findIndex((c) => c.name === a.camera);
    if (i >= 0 && !selection) {
      const page = Math.floor(i / (colonnes * colonnes));
      if (page !== vuePage) { vuePage = page; dessinerMur(); }
    }
    document.querySelectorAll(".tuile").forEach((t) => t.classList.remove("alerte"));
    const t = el(`tuile-${a.camera}`);
    t?.classList.add("alerte");
    t?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

/* ═══ Réglages ═══ */

async function chargerReglages() {
  const s = await api("/api/settings");
  el("liste-reglages").innerHTML = "";
  const entrees = Object.entries(s);
  el("modeles-compte").textContent =
    `${entrees.filter(([, v]) => v.detect).length} actifs sur ${entrees.length}`;
  for (const [m, v] of entrees) {
    const l = document.createElement("div");
    l.className = "ligne-reglage";
    l.innerHTML = `<div>${nomModele(m)}</div>`;
    l.appendChild(interrupteur(m, "detect", v.detect, "Détection"));
    l.appendChild(interrupteur(m, "alert", v.alert, "Alertes"));
    el("liste-reglages").appendChild(l);
  }
}

function interrupteur(modele, cle, valeur, texte) {
  const l = document.createElement("label");
  l.className = "inter";
  l.innerHTML = `<input type="checkbox" ${valeur ? "checked" : ""} /><span class="rail-inter"></span><span>${texte}</span>`;
  l.querySelector("input").addEventListener("change", async (e) => {
    try {
      await api(`/api/settings/${modele}/${cle}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: e.target.checked }),
      });
    } catch (err) {
      e.target.checked = !e.target.checked;
      avis("Réglage non appliqué", err.message, "err");
    }
  });
  return l;
}

/* ═══ Zones ═══ */

const zones = { camera: null, liste: [], trace: [] };

async function chargerZones() {
  const s = el("zone-camera");
  s.innerHTML = noms.map((n) => `<option value="${ech(n)}">${ech(n)}</option>`).join("");
  if (!s.dataset.pret) {
    s.addEventListener("change", () => choisirCameraZone(s.value));
    s.dataset.pret = "1";
  }
  const cases = el("zone-modeles");
  if (!cases.children.length) {
    cases.innerHTML = Object.entries(MODELES).filter(([k]) => k !== "systeme")
      .map(([k, v]) => `<label><input type="checkbox" value="${k}" /> ${v}</label>`).join("");
  }
  await choisirCameraZone(zones.camera || s.value || noms[0]);
}

async function choisirCameraZone(cam) {
  if (!cam) return;
  zones.camera = cam;
  zones.trace = [];
  el("zone-camera").value = cam;
  el("zone-image").src = `/video/${encodeURIComponent(cam)}.jpg?t=${Date.now()}`;
  zones.liste = (await api(`/api/zones/${encodeURIComponent(cam)}`)).zones || [];
  listerZones();
  dessinerZones();
}

function tailleToile() {
  const img = el("zone-image");
  const c = el("zone-canvas");
  if (!img.clientWidth) return false;
  c.width = img.clientWidth;
  c.height = img.clientHeight;
  return true;
}

function dessinerZones() {
  const c = el("zone-canvas");
  if (!tailleToile()) return;
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);

  for (const z of zones.liste) {
    const excl = z.type === "exclusion";
    polygone(ctx, z.polygon, c.width, c.height,
      excl ? "rgba(229,72,77,0.95)" : "rgba(224,140,26,0.95)",
      excl ? "rgba(229,72,77,0.16)" : "rgba(224,140,26,0.14)", z.name);
  }
  if (zones.trace.length) {
    polygone(ctx, zones.trace, c.width, c.height,
      "rgba(48,164,108,0.95)", "rgba(48,164,108,0.16)", null, true);
  }
}

function polygone(ctx, poly, w, h, trait, fond, nom, points) {
  if (!poly.length) return;
  ctx.beginPath();
  poly.forEach(([x, y], i) => {
    const px = x * w, py = y * h;
    i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
  });
  if (poly.length >= 3) { ctx.closePath(); ctx.fillStyle = fond; ctx.fill(); }
  ctx.strokeStyle = trait;
  ctx.lineWidth = 1.5;
  ctx.stroke();

  if (points) {
    for (const [x, y] of poly) {
      ctx.beginPath();
      ctx.arc(x * w, y * h, 3, 0, Math.PI * 2);
      ctx.fillStyle = trait;
      ctx.fill();
    }
  }
  if (nom) {
    const [x, y] = poly[0];
    ctx.fillStyle = trait;
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillText(nom, x * w + 4, y * h - 5);
  }
}

function decrire(z) {
  const p = [`${z.polygon.length} sommets`];
  p.push(z.models?.length ? z.models.map(nomModele).join(", ") : "tous modèles");
  if (z.schedule?.start && z.schedule?.end) p.push(`${z.schedule.start}→${z.schedule.end}`);
  if (z.conf) p.push(`seuil ${z.conf}`);
  if (z.cooldown) p.push(`délai ${z.cooldown}s`);
  return p.join(" · ");
}

function listerZones() {
  const l = el("zone-liste");
  if (!zones.liste.length) {
    l.innerHTML = '<p class="msg">Aucune zone : la caméra est analysée en entier.</p>';
    return;
  }
  l.innerHTML = "";
  zones.liste.forEach((z, i) => {
    const d = document.createElement("div");
    d.className = "zone-ligne";
    d.innerHTML = `<div>
        <div class="zone-nom">${ech(z.name)}
          <span class="zone-type ${z.type === "exclusion" ? "excl" : ""}">${z.type === "exclusion" ? "masque" : "surveillée"}</span>
        </div>
        <div class="msg">${ech(decrire(z))}</div>
      </div>
      <button class="bouton danger">Supprimer</button>`;
    d.querySelector("button").addEventListener("click", () => {
      zones.liste.splice(i, 1);
      listerZones();
      dessinerZones();
    });
    l.appendChild(d);
  });
}

function brancherZones() {
  const c = el("zone-canvas");

  c.addEventListener("click", (e) => {
    const r = c.getBoundingClientRect();
    // Coordonnées normalisées : indépendantes de la taille d'affichage et de la
    // résolution de la caméra.
    zones.trace.push([
      +((e.clientX - r.left) / r.width).toFixed(4),
      +((e.clientY - r.top) / r.height).toFixed(4),
    ]);
    el("zone-aide").textContent = `${zones.trace.length} sommet(s)`
      + (zones.trace.length >= 3 ? " — nommez la zone puis « Ajouter »." : " — 3 minimum.");
    dessinerZones();
  });

  el("zone-annuler-point").addEventListener("click", () => { zones.trace.pop(); dessinerZones(); });
  el("zone-effacer").addEventListener("click", () => { zones.trace = []; dessinerZones(); });

  el("zone-ajouter").addEventListener("click", () => {
    const m = el("zone-msg");
    const nom = el("zone-nom").value.trim();
    if (zones.trace.length < 3) { m.className = "msg err"; m.textContent = "Tracez au moins 3 sommets."; return; }
    if (!nom) { m.className = "msg err"; m.textContent = "Donnez un nom à la zone."; return; }

    const z = {
      name: nom, polygon: zones.trace, type: el("zone-type").value,
      models: [...el("zone-modeles").querySelectorAll("input:checked")].map((x) => x.value),
    };
    const d = el("zone-debut").value, f = el("zone-fin").value;
    if (d && f) z.schedule = { start: d, end: f };
    if (el("zone-seuil").value) z.conf = Number(el("zone-seuil").value);
    if (el("zone-delai").value) z.cooldown = Number(el("zone-delai").value);

    zones.liste.push(z);
    zones.trace = [];
    el("zone-nom").value = "";
    el("zone-seuil").value = "";
    el("zone-delai").value = "";
    el("zone-modeles").querySelectorAll("input:checked").forEach((x) => (x.checked = false));
    m.className = "msg";
    m.textContent = "Zone ajoutée — pensez à enregistrer.";
    listerZones();
    dessinerZones();
  });

  el("zone-enregistrer").addEventListener("click", async () => {
    const m = el("zone-msg");
    try {
      const d = await api(`/api/zones/${encodeURIComponent(zones.camera)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zones: zones.liste }),
      });
      m.className = "msg ok";
      m.textContent = `${d.zones} zone(s) enregistrée(s), appliquées au prochain cycle.`;
      chargerCameras();
    } catch (e) { m.className = "msg err"; m.textContent = e.message; }
  });

  el("zone-image").addEventListener("load", dessinerZones);
  window.addEventListener("resize", dessinerZones);
}

/* ═══ Démarrage ═══ */

async function init() {
  theme();
  onglets();

  el("btn-effacer").addEventListener("click", async () => {
    const p = filtres();
    const combien = await api(`/api/alerts?limit=1&${p}`).then((d) => d.total).catch(() => null);
    if (combien === 0) { avis("Rien à effacer", "Aucune alerte ne correspond.", ""); return; }

    // On annonce le nombre exact : « effacer l'historique » ne dit pas si l'on
    // perd trois lignes ou trois mille.
    const sansFiltre = [...p.keys()].length === 0;
    const ok = await confirmer(
      `Effacer ${combien ?? "ces"} alerte(s) ?`,
      sansFiltre
        ? "Aucun filtre n'est actif : c'est TOUT l'historique qui part, avec ses captures et ses clips. Cette action est définitive."
        : "Seules les alertes correspondant aux filtres affichés sont effacées, avec leurs captures et leurs clips. Cette action est définitive.",
      "Effacer");
    if (!ok) return;

    try {
      const r = await api(`/api/alerts?${p}`, { method: "DELETE" });
      avis("Historique effacé", `${r.supprimees} alerte(s), ${r.fichiers} fichier(s).`, "ok");
      page = 0;
      chargerAlertes();
      chargerFrise();
      chargerEvenements();
    } catch (e) { avis("Suppression impossible", e.message, "err"); }
  });

  el("btn-filtres").addEventListener("click", (e) => {
    const corps = el("filtres-corps");
    corps.hidden = !corps.hidden;
    e.currentTarget.classList.toggle("actif", !corps.hidden);
    e.currentTarget.textContent = corps.hidden ? "Déplier" : "Replier";
  });
  outilsDirect();
  brancherSon();
  formCamera();
  brancherFiltres();
  brancherFrise();
  brancherDepot();
  brancherZones();

  tick();
  await chargerCameras();
  await chargerFichiers();
  remplirFiltres();
  await chargerReglages();
  await chargerEvenements();
  await majBarreEtat();

  setInterval(tick, 1000);
  setInterval(rafraichirFlux, 700);
  setInterval(majBarreEtat, 5000);
  setInterval(chargerEvenements, 8000);
  setInterval(guetter, 5000);
  setInterval(chargerCameras, 20000);
}

init();
