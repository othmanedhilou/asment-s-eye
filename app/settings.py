"""Réglages en direct (détection / alerte par modèle), modifiables sans redémarrer
le pipeline via le panneau de contrôle (web/control_panel.py)."""

import json
import time
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "settings.json"

PIPELINE_MODELS = ["arc", "conveyor", "epi", "fire_smoke", "gloves_glasses", "load_control",
                   "person_animal", "vehicles"]

# Le pipeline interroge ces réglages pour chaque détection (plusieurs dizaines de
# fois par seconde) : on garde le contenu en cache une seconde pour éviter de
# relire le disque en boucle, tout en restant réactif aux changements de l'UI.
_CACHE_TTL = 1.0
_cache: dict | None = None
_cache_time = 0.0


def load_settings() -> dict:
    global _cache, _cache_time
    now = time.monotonic()
    if _cache is not None and now - _cache_time < _CACHE_TTL:
        return _cache

    data = {}
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

    changed = False
    for m in PIPELINE_MODELS:
        if m not in data:
            data[m] = {"detect": True, "alert": True}
            changed = True
    if changed:
        save_settings(data)

    _cache = data
    _cache_time = now
    return data


def save_settings(data: dict):
    global _cache, _cache_time
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    _cache = data
    _cache_time = time.monotonic()


def is_detect_enabled(model: str) -> bool:
    return load_settings().get(model, {}).get("detect", True)


def is_alert_enabled(model: str) -> bool:
    return load_settings().get(model, {}).get("alert", True)


def set_model_setting(model: str, key: str, value: bool):
    data = load_settings()
    data.setdefault(model, {"detect": True, "alert": True})[key] = value
    save_settings(data)
