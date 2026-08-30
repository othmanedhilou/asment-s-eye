from pathlib import Path

from fastapi import FastAPI, HTTPException
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

app = FastAPI(title="SmokeWatch VMS")

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
    items = read_alerts(limit=limit, offset=offset, label=label,
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
    nom = f"smokewatch_rapport_{date.today():%Y%m%d}.pdf"
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
        headers={"Content-Disposition": "attachment; filename=smokewatch_alertes.csv"},
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
