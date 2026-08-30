"""État de santé du système, partagé entre le pipeline et l'interface.

Un système de sécurité qui s'arrête devient silencieux — et le silence ressemble
exactement au calme. C'est le pire mode de défaillance possible : personne ne
remarque rien, jusqu'au jour où l'on cherche l'enregistrement d'un incident qui
n'a jamais été capté.

Le pipeline publie donc en continu son état ici, et l'interface le lit. Comme les
deux tournent dans des processus séparés, l'échange passe par un fichier, au même
titre que les images live et la base d'alertes.
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

HEALTH_PATH = Path(__file__).resolve().parent.parent / "data" / "health.json"

# Au-delà de ce délai sans mise à jour, le pipeline est considéré arrêté.
STALE_SECONDS = 20

_lock = threading.Lock()
_state: dict = {"cameras": {}, "global": {}}
_last_write = 0.0
_WRITE_INTERVAL = 2.0  # écrire à chaque image saturerait le disque pour rien


def _write(force: bool = False):
    global _last_write
    now = time.monotonic()
    if not force and now - _last_write < _WRITE_INTERVAL:
        return
    _last_write = now

    payload = {
        "pid": os.getpid(),
        "updated_at": datetime.now().isoformat(),
        "cameras": _state["cameras"],
        **_state["global"],
    }
    try:
        HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = HEALTH_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, HEALTH_PATH)
    except OSError:
        pass  # la santé ne doit jamais faire tomber la détection


def update_camera(name: str, **fields):
    """Met à jour l'état d'une caméra (état, fps, dernier cycle, erreur)."""
    with _lock:
        entry = _state["cameras"].setdefault(name, {})
        entry.update(fields)
        entry["updated_at"] = datetime.now().isoformat()
        _write(force=fields.get("state") is not None)


def set_global(**fields):
    """Informations qui ne relèvent pas d'une caméra en particulier —
    rapprochements entre caméras, par exemple."""
    with _lock:
        _state["global"].update(fields)
        _write(force=True)


def forget_camera(name: str):
    with _lock:
        _state["cameras"].pop(name, None)
        _write(force=True)


def read_health() -> dict:
    """Lu par l'interface. `running` indique si le pipeline donne signe de vie."""
    if not HEALTH_PATH.exists():
        return {"running": False, "cameras": {}, "updated_at": None}
    try:
        with open(HEALTH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"running": False, "cameras": {}, "updated_at": None}

    age = time.time() - HEALTH_PATH.stat().st_mtime
    data["running"] = age < STALE_SECONDS
    data["age_seconds"] = round(age, 1)
    return data


def system_metrics() -> dict:
    """Charge machine : sert à savoir si le serveur tient la charge.

    psutil est optionnel — son absence ne doit pas priver l'interface du reste
    des informations de santé.
    """
    metrics = {}
    try:
        import psutil

        metrics["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        metrics["memory_percent"] = mem.percent
        metrics["memory_available_mb"] = round(mem.available / 1024 / 1024)
        disk = psutil.disk_usage(str(HEALTH_PATH.parent))
        metrics["disk_free_gb"] = round(disk.free / 1024 / 1024 / 1024, 1)
        metrics["disk_percent"] = disk.percent
    except Exception:
        metrics["unavailable"] = "psutil non installé"
    return metrics
