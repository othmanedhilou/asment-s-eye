from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import load_config
from app.settings import PIPELINE_MODELS, load_settings, set_model_setting
from app.usecases import usecases_with_status
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
    acknowledged: bool | None = None,
    since_hours: int | None = None,
):
    return read_alerts(
        limit=limit,
        model=model,
        camera=camera,
        severity=severity,
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
    config = load_config()
    cameras = []
    for name, cfg in config.get("cameras", {}).items():
        live = (LIVE_DIR / f"{name}.jpg").exists()
        cameras.append({"name": name, "models": cfg.get("models", []), "online": live})
    return {"cameras": cameras}


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
