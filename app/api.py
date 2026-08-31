from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.cameras import (
    active_cameras,
    camera_source,
    delete_camera,
    load_cameras,
    rename_camera,
    upsert_camera,
)
from app.capture import probe_source
from app.config import load_config
from app.health import read_health, system_metrics
from app.settings import PIPELINE_MODELS, load_settings, set_model_setting
from app.usecases import usecases_with_status
from app.zones import load_zones, save_zones
from app.storage import (
    acknowledge_alert,
    alerts_for_day,
    count_alerts,
    days_with_alerts,
    export_csv,
    mark_false_positive,
    quality_stats,
    read_alerts,
    stats_summary,
    stats_timeline,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
LIVE_DIR = PROJECT_ROOT / "data" / "live"
SNAPSHOTS_DIR = PROJECT_ROOT / "clips" / "snapshots"
VIDEOS_DIR = PROJECT_ROOT / "clips" / "videos"
UPLOADS_DIR = PROJECT_ROOT / "videos"

# Extensions acceptees a l'import. Liste blanche plutot que liste noire : un
# fichier depose sur le serveur ne doit jamais pouvoir etre autre chose qu'une
# video ou une image.
EXTENSIONS_VIDEO = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
EXTENSIONS_IMAGE = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# 1 Go : au-dela, un import par navigateur devient hasardeux, et le disque du
# serveur se remplit vite. Les gros volumes se deposent directement sur le
# serveur, dans videos/.
TAILLE_MAX_OCTETS = 1024 * 1024 * 1024

app = FastAPI(title="Ciment's Eye VMS")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


class SettingUpdate(BaseModel):
    value: bool


class AckBody(BaseModel):
    operator: str = "opérateur"


class CameraBody(BaseModel):
    source: str | int
    models: list[str] = []
    fps: float | None = None
    imgsz: int | None = None
    workers: int | None = None
    enabled: bool = True
    # Le suivi conditionne tout le reste : sans lui une alerte se répète au
    # lieu de désigner un objet, on ne peut ni compter ni connaître un sens de
    # passage. Il coûte peu au regard de l'inférence. Il est donc actif par
    # défaut, et se désactive à la main sur une caméra qu'on veut alléger.
    tracking: bool = True
    recording: bool = False
    plates: bool = False          # lecture des plaques (véhicules, suivi requis)
    collecte: bool = False        # photographier chaque franchissement de ligne
    bachage: bool = False         # déduire l'absence de bâche (camions, suivi requis)
    voisins: list[str] = []       # caméras pouvant recevoir un objet venu d'ici
    segment_minutes: int | None = None
    retention_days: int | None = None


class SourceBody(BaseModel):
    source: str | int


class RenameBody(BaseModel):
    nouveau_nom: str


class FalsePositiveBody(BaseModel):
    is_false: bool = True
    operator: str = "opérateur"


class Zone(BaseModel):
    name: str
    polygon: list[list[float]]   # sommets normalisés (0.0 à 1.0)
    models: list[str] = []       # vide = tous les modèles
    type: str = "surveillance"   # ou "exclusion" : masquer plutôt que surveiller
    schedule: dict | None = None  # {"start": "06:00", "end": "22:00", "days": [0-6]}
    conf: float | None = None     # seuil de confiance propre à la zone
    cooldown: float | None = None  # délai anti-répétition propre à la zone


class ZonesBody(BaseModel):
    zones: list[Zone]


# Une image live plus ancienne que ce délai signifie que le pipeline ne tourne
# plus, ou que la caméra ne répond pas : la marquer hors ligne évite d'afficher
# une vignette figée en la faisant passer pour du direct.
LIVE_MAX_AGE_SECONDS = 15


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = WEB_DIR / "templates" / "index.html"
    return html_path.read_text(encoding="utf-8")


# ── Alertes ──────────────────────────────────────────────────────────


@app.get("/api/alerts")
def api_alerts(
    limit: int = 50,
    offset: int = 0,
    model: str | None = None,
    camera: str | None = None,
    severity: str | None = None,
    zone: str | None = None,
    label: str | None = None,
    plaque: str | None = None,
    acknowledged: bool | None = None,
    false_positive: bool | None = None,
    since_hours: int | None = None,
    hour_from: int | None = None,
    hour_to: int | None = None,
):
    """Historique filtrable et paginé.

    Le total accompagne la page : après quelques mois d'exploitation, savoir
    qu'il existe 1 240 résultats change la façon de chercher.
    """
    filtres = {
        "model": model, "camera": camera, "severity": severity, "zone": zone,
        "acknowledged": acknowledged, "false_positive": false_positive,
        "since_hours": since_hours,
    }
    items = read_alerts(limit=limit, offset=offset, label=label, plaque=plaque,
                        hour_from=hour_from, hour_to=hour_to, **filtres)
    return {"items": items, "total": count_alerts(**filtres),
            "limit": limit, "offset": offset}


@app.post("/api/alerts/{alert_id}/false")
def api_false_positive(alert_id: int, body: FalsePositiveBody):
    """Retour de l'opérateur : le système s'est trompé.

    Ce clic sert deux fois — il alimente les indicateurs de qualité, et il
    étiquette une image que le ré-entraînement pourra utiliser.
    """
    if not mark_false_positive(alert_id, body.is_false, body.operator):
        raise HTTPException(status_code=404, detail="Alerte introuvable")
    return {"ok": True, "id": alert_id, "false_positive": body.is_false}


@app.post("/api/alerts/{alert_id}/ack")
def api_ack_alert(alert_id: int, body: AckBody):
    if not acknowledge_alert(alert_id, body.operator):
        raise HTTPException(status_code=404, detail="Alerte introuvable")
    return {"ok": True, "id": alert_id}


# ── Statistiques / rapports ──────────────────────────────────────────


@app.get("/api/stats/summary")
def api_stats_summary():
    return stats_summary()


@app.get("/api/timeline")
def api_timeline(day: str | None = None, camera: str | None = None):
    """Alertes d'une journée, positionnées sur 24 h.

    Après un incident, on remonte le temps : c'est le geste de base d'un
    opérateur de vidéosurveillance, qu'une liste paginée ne permet pas.
    """
    from datetime import date

    jours = days_with_alerts(camera)
    if day is None:
        day = jours[0] if jours else date.today().isoformat()
    return {"day": day, "camera": camera, "jours_disponibles": jours,
            "alertes": alerts_for_day(camera, day)}


@app.get("/api/stats/quality")
def api_stats_quality(days: int = 30):
    """Qualité vue par les opérateurs : taux de fausses alertes, délai de prise
    en charge. C'est ce qui permet de répondre par un chiffre à l'objectif du
    cahier des charges."""
    return quality_stats(days)


@app.get("/api/stats/timeline")
def api_stats_timeline(hours: int = 24):
    return stats_timeline(hours=min(hours, 168))


@app.get("/api/export/pdf")
def api_export_pdf(days: int = 7):
    """Rapport HSE en PDF.

    Le CSV sert a qui veut retravailler les donnees ; ce rapport sert a qui doit
    decider, presenter et classer.
    """
    from app.report import build_report

    pdf = build_report(days=days)
    from datetime import date
    nom = f"ciments_eye_rapport_{date.today():%Y%m%d}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={nom}"},
    )


@app.get("/api/export/csv")
def api_export_csv():
    csv_data = export_csv()
    return Response(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=ciments_eye_alertes.csv"},
    )


# ── Configuration modèles ────────────────────────────────────────────


@app.get("/api/settings")
def api_settings():
    return load_settings()


@app.post("/api/settings/{model}/{key}")
def api_update_setting(model: str, key: str, body: SettingUpdate):
    if model not in PIPELINE_MODELS:
        raise HTTPException(status_code=404, detail="Modèle inconnu")
    if key not in ("detect", "alert"):
        raise HTTPException(status_code=400, detail="Clé invalide")
    set_model_setting(model, key, body.value)
    return {"ok": True, "model": model, "key": key, "value": body.value}


@app.get("/api/models")
def api_models():
    return {"models": PIPELINE_MODELS}


@app.get("/api/usecases")
def api_usecases():
    return {"usecases": usecases_with_status()}


@app.get("/api/clip")
def api_clip(path: str):
    candidate = Path(path).resolve()
    try:
        candidate.relative_to(VIDEOS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Chemin invalide")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Clip introuvable")
    return FileResponse(str(candidate), media_type="video/mp4")


@app.get("/api/cameras")
def api_cameras():
    import time

    all_zones = load_zones()
    health = read_health().get("cameras", {})
    cameras = []
    for name, cfg in load_cameras().items():
        frame_path = LIVE_DIR / f"{name}.jpg"
        online = False
        age = None
        if frame_path.exists():
            age = time.time() - frame_path.stat().st_mtime
            online = age < LIVE_MAX_AGE_SECONDS
        etat = health.get(name, {})
        cameras.append({
            "name": name,
            "source": str(camera_source(cfg)),
            "models": cfg.get("models", []),
            "enabled": cfg.get("enabled", True),
            "fps": cfg.get("fps"),
            "tracking": cfg.get("tracking", False),
            "recording": cfg.get("recording", False),
            "plates": cfg.get("plates", False),
            "collecte": cfg.get("collecte", False),
            "bachage": cfg.get("bachage", False),
            "voisins": cfg.get("voisins", []),
            "imgsz": cfg.get("imgsz"),
            "workers": cfg.get("workers"),
            "segment_minutes": cfg.get("segment_minutes"),
            "retention_days": cfg.get("retention_days"),
            "online": online,
            "age_seconds": round(age, 1) if age is not None else None,
            "zones": [z.get("name") for z in all_zones.get(name, [])],
            "state": etat.get("state"),
            "cycle_ms": etat.get("cycle_ms"),
            "error": etat.get("error"),
        })
    return {"cameras": cameras}


@app.post("/api/cameras/test")
def api_test_camera(body: SourceBody):
    """Teste une source avant de l'enregistrer.

    Le jour du raccordement des caméras du site, la fenêtre d'intervention est
    courte : il faut savoir en deux secondes si l'adresse et les identifiants
    sont bons, pas chercher dans les journaux dix minutes plus tard.
    """
    return probe_source(body.source)


@app.post("/api/cameras/{name}")
def api_upsert_camera(name: str, body: CameraBody):
    if not name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Nom de caméra invalide")

    known = set(load_config().get("models", {}))
    inconnus = [m for m in body.models if m not in known]
    if inconnus:
        raise HTTPException(status_code=400, detail=f"Modèles inconnus : {', '.join(inconnus)}")

    cfg = {k: v for k, v in body.model_dump().items() if v is not None}
    if not cfg.get("voisins"):
        cfg.pop("voisins", None)   # ne pas écraser une topologie déjà déclarée
    return {"ok": True, "name": name, "camera": upsert_camera(name, cfg)}


@app.post("/api/cameras/{name}/rename")
def api_rename_camera(name: str, body: RenameBody):
    if not rename_camera(name, body.nouveau_nom):
        raise HTTPException(status_code=400, detail="Renommage impossible (nom inconnu ou déjà pris)")
    return {"ok": True, "name": body.nouveau_nom}


@app.delete("/api/cameras/{name}")
def api_delete_camera(name: str):
    if not delete_camera(name):
        raise HTTPException(status_code=404, detail="Caméra inconnue")
    return {"ok": True, "name": name}


# ── Santé du système ─────────────────────────────────────────────────


@app.get("/api/handoffs")
def api_handoffs():
    """Objets rapprochés d'une caméra à l'autre.

    Le champ `certain` distingue ce qui est établi — une plaque identique — de
    ce qui n'est qu'une ressemblance d'apparence. Cette nuance doit rester
    visible : un rapprochement présenté comme certain raconte une histoire que
    les données ne soutiennent pas.
    """
    sante = read_health()
    return {
        "correspondances": sante.get("correspondances", []),
        "objets_suivis": sante.get("objets_inter_cameras", 0),
        "pipeline_actif": sante.get("running", False),
    }


@app.get("/api/health")
def api_health():
    """État du pipeline, des caméras et de la machine.

    Un système de surveillance qui s'arrête devient silencieux, et le silence
    ressemble au calme : cette page est là pour que l'arrêt se voie.
    """
    health = read_health()
    return {
        "pipeline": {
            "running": health.get("running", False),
            "updated_at": health.get("updated_at"),
            "age_seconds": health.get("age_seconds"),
        },
        "cameras": health.get("cameras", {}),
        "cameras_configurees": len(load_cameras()),
        "cameras_actives": len(active_cameras()),
        "machine": system_metrics(),
    }


# ── Zones d'intérêt (ROI) ────────────────────────────────────────────


@app.get("/api/zones")
def api_zones():
    return load_zones()


@app.get("/api/zones/{camera}")
def api_zones_camera(camera: str):
    return {"camera": camera, "zones": load_zones().get(camera, [])}


@app.post("/api/zones/{camera}")
def api_save_zones(camera: str, body: ZonesBody):
    """Remplace les zones d'une caméra. Prise en compte au cycle suivant."""
    if camera not in load_cameras():
        raise HTTPException(status_code=404, detail="Caméra inconnue")

    for zone in body.zones:
        if len(zone.polygon) < 3:
            raise HTTPException(status_code=400, detail=f"Zone « {zone.name} » : 3 sommets minimum")
        for point in zone.polygon:
            if len(point) != 2 or not all(0.0 <= c <= 1.0 for c in point):
                raise HTTPException(
                    status_code=400,
                    detail=f"Zone « {zone.name} » : coordonnées attendues normalisées entre 0 et 1",
                )

    data = load_zones()
    data[camera] = [z.model_dump() for z in body.zones]
    save_zones(data)
    return {"ok": True, "camera": camera, "zones": len(body.zones)}


# ── Import de fichiers de test ───────────────────────────────────────


def _nom_sur(nom: str) -> str:
    """Nettoie un nom de fichier venu du navigateur.

    Un nom peut contenir des separateurs de chemin ou des `..` : sans ce
    nettoyage, un import pourrait ecrire n'importe ou sur le serveur.
    """
    base = Path(nom).name
    autorises = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- "
    propre = "".join(c if c in autorises else "_" for c in base).strip()
    return propre or "fichier"


@app.get("/api/uploads")
def api_list_uploads():
    """Fichiers deposes, utilisables comme source de camera."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    fichiers = []
    for chemin in sorted(UPLOADS_DIR.iterdir()):
        if not chemin.is_file():
            continue
        suffixe = chemin.suffix.lower()
        if suffixe not in EXTENSIONS_VIDEO | EXTENSIONS_IMAGE:
            continue
        fichiers.append({
            "nom": chemin.name,
            "source": f"videos/{chemin.name}",
            "type": "video" if suffixe in EXTENSIONS_VIDEO else "image",
            "taille_mo": round(chemin.stat().st_size / 1024 / 1024, 1),
        })
    return {"fichiers": fichiers}


@app.post("/api/uploads")
async def api_upload(file: UploadFile = File(...)):
    suffixe = Path(file.filename or "").suffix.lower()
    if suffixe not in EXTENSIONS_VIDEO | EXTENSIONS_IMAGE:
        raise HTTPException(
            status_code=400,
            detail=f"Format non accepte ({suffixe or 'sans extension'}). "
                   "Videos : mp4, avi, mov, mkv, webm. Images : jpg, png, bmp, webp.")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    nom = _nom_sur(file.filename or "fichier")
    destination = UPLOADS_DIR / nom

    # Ne jamais ecraser un fichier existant : une camera peut deja s'en servir.
    tige, index = destination.stem, 1
    while destination.exists():
        destination = UPLOADS_DIR / f"{tige}_{index}{suffixe}"
        index += 1

    ecrits = 0
    try:
        with open(destination, "wb") as sortie:
            while morceau := await file.read(1024 * 1024):
                ecrits += len(morceau)
                if ecrits > TAILLE_MAX_OCTETS:
                    raise HTTPException(status_code=413,
                                        detail="Fichier trop volumineux (1 Go maximum)")
                sortie.write(morceau)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except Exception as e:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Echec de l'import : {e}")

    # Verifier que le fichier est reellement lisible : un envoi tronque ou un
    # fichier renomme se decouvrirait sinon au demarrage de la camera.
    # On sonde le chemin reellement ecrit, pas une forme relative : c'est le
    # fichier qu'on vient de recevoir qui doit etre lisible.
    sonde = probe_source(str(destination))
    if not sonde.get("ok"):
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400,
                            detail=f"Fichier illisible : {sonde.get('error', 'format non reconnu')}")

    return {"ok": True, "nom": destination.name, "source": f"videos/{destination.name}",
            "taille_mo": round(ecrits / 1024 / 1024, 1), "apercu": sonde}


@app.delete("/api/uploads/{nom}")
def api_delete_upload(nom: str):
    chemin = UPLOADS_DIR / _nom_sur(nom)
    if not chemin.exists() or not chemin.is_file():
        raise HTTPException(status_code=404, detail="Fichier introuvable")

    utilise = [n for n, cfg in load_cameras().items()
               if str(camera_source(cfg)).endswith(chemin.name)]
    if utilise:
        raise HTTPException(status_code=409,
                            detail=f"Utilise par la camera : {', '.join(utilise)}")

    chemin.unlink()
    return {"ok": True, "nom": chemin.name}


# ── Médias ───────────────────────────────────────────────────────────


@app.get("/api/snapshot")
def api_snapshot(path: str):
    candidate = Path(path).resolve()
    try:
        candidate.relative_to(SNAPSHOTS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Chemin invalide")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Snapshot introuvable")
    return FileResponse(str(candidate), media_type="image/jpeg")


@app.get("/video/{camera}.jpg")
def video_frame(camera: str):
    path = LIVE_DIR / f"{camera}.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Pas encore de frame disponible")
    return FileResponse(
        str(path),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )
