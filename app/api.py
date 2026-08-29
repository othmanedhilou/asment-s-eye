from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import load_config
from app.settings import PIPELINE_MODELS, load_settings, set_model_setting
from app.usecases import usecases_with_status
from app.zones import load_zones, save_zones
from app.storage import (
    acknowledge_alert,
    export_csv,
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


class Zone(BaseModel):
    name: str
    polygon: list[list[float]]   # sommets normalisés (0.0 à 1.0)
    models: list[str] = []       # vide = tous les modèles


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
    model: str | None = None,
    camera: str | None = None,
    severity: str | None = None,
    zone: str | None = None,
    acknowledged: bool | None = None,
    since_hours: int | None = None,
):
    return read_alerts(
        limit=limit,
        model=model,
        camera=camera,
        severity=severity,
        zone=zone,
        acknowledged=acknowledged,
        since_hours=since_hours,
    )


@app.post("/api/alerts/{alert_id}/ack")
def api_ack_alert(alert_id: int, body: AckBody):
    if not acknowledge_alert(alert_id, body.operator):
        raise HTTPException(status_code=404, detail="Alerte introuvable")
    return {"ok": True, "id": alert_id}


# ── Statistiques / rapports ──────────────────────────────────────────


@app.get("/api/stats/summary")
def api_stats_summary():
    return stats_summary()


@app.get("/api/stats/timeline")
def api_stats_timeline(hours: int = 24):
    return stats_timeline(hours=min(hours, 168))


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

    config = load_config()
    all_zones = load_zones()
    cameras = []
    for name, cfg in config.get("cameras", {}).items():
        frame_path = LIVE_DIR / f"{name}.jpg"
        online = False
        age = None
        if frame_path.exists():
            age = time.time() - frame_path.stat().st_mtime
            online = age < LIVE_MAX_AGE_SECONDS
        cameras.append({
            "name": name,
            "models": cfg.get("models", []),
            "online": online,
            "age_seconds": round(age, 1) if age is not None else None,
            "zones": [z.get("name") for z in all_zones.get(name, [])],
        })
    return {"cameras": cameras}


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
    config = load_config()
    if camera not in config.get("cameras", {}):
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
