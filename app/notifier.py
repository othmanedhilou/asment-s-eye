import os
import sys
from datetime import datetime
from pathlib import Path

import cv2
import requests
from dotenv import load_dotenv

from app.logging_setup import setup_logging
from app.models import Alert
from app.storage import log_alert, severity_for

load_dotenv()

log = setup_logging()

SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent / "clips" / "snapshots"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Plusieurs destinataires possibles, séparés par des virgules dans .env :
#   TELEGRAM_CHAT_IDS=111111,222222,333333
# Rétro-compatible avec TELEGRAM_CHAT_ID (un seul).
_raw_ids = os.getenv("TELEGRAM_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or ""
TELEGRAM_CHAT_IDS = [c.strip() for c in _raw_ids.split(",") if c.strip()]

# Routage par sévérité : critique -> tous ; haute/moyenne -> premier destinataire
# (superviseur). Adapter la liste dans .env selon l'organisation HSE.


def _recipients_for(severity: str) -> list[str]:
    if not TELEGRAM_CHAT_IDS:
        return []
    if severity == "critique":
        return TELEGRAM_CHAT_IDS
    return TELEGRAM_CHAT_IDS[:1]


def _beep():
    if sys.platform == "win32":
        import winsound

        winsound.Beep(1000, 400)
    else:
        print("\a", end="", flush=True)


SEVERITY_ICON = {"critique": "🟥", "haute": "🟧", "moyenne": "🟦"}


def _telegram_alert(alert: Alert, snapshot_path: str | None, severity: str):
    if not TELEGRAM_BOT_TOKEN:
        return

    icon = SEVERITY_ICON.get(severity, "🚨")
    caption = f"{icon} [{severity.upper()}] {alert.message}"

    for chat_id in _recipients_for(severity):
        try:
            if snapshot_path and Path(snapshot_path).exists():
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                with open(snapshot_path, "rb") as photo:
                    requests.post(
                        url,
                        data={"chat_id": chat_id, "caption": caption},
                        files={"photo": photo},
                        timeout=10,
                    )
            else:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                requests.post(url, data={"chat_id": chat_id, "text": caption}, timeout=10)
        except requests.RequestException as e:
            log.error(f"échec envoi Telegram ({chat_id}) : {e}")


def system_alert(camera: str, label: str, message: str) -> Alert:
    """Incident technique : caméra hors ligne, disque plein, pipeline en échec.

    Passe par le même circuit que les alertes métier — journal, base, Telegram —
    parce qu'une caméra qui ne filme plus doit réveiller quelqu'un au même titre
    qu'un départ de feu.
    """
    alert = Alert(
        camera=camera,
        model="systeme",
        label=label,
        confidence=1.0,
        message=message,
    )
    return local_alert(alert, frame=None)


def local_alert(alert: Alert, frame=None) -> Alert:
    severity = severity_for(alert.model, alert.label)
    log.warning(f"ALERTE [{severity}] — {alert.message}")
    _beep()

    snapshot_path = None
    if frame is not None:
        try:
            SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = SNAPSHOTS_DIR / f"{alert.camera}_{alert.model}_{alert.label}_{ts}.jpg"
            cv2.imwrite(str(filename), frame)
            snapshot_path = str(filename)
            log.info(f"snapshot : {filename.name}")
        except Exception as e:
            log.error(f"échec snapshot : {e}")

    alert.db_id = log_alert(alert, snapshot_path)
    _telegram_alert(alert, snapshot_path, severity)
    return alert
