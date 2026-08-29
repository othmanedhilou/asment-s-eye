"""Gestion des caméras — source de vérité modifiable depuis l'interface.

Tant qu'ajouter une caméra oblige à éditer `config.yaml` sur le serveur, le
système dépend de la personne qui sait le faire. Un exploitant doit pouvoir
raccorder une caméra seul, et le jour du branchement sur site la fenêtre
d'intervention est courte : il faut un formulaire, pas une séance de débogage.

Les caméras vivent donc dans `config/cameras.json`, écrit par l'application.
`config.yaml` reste la référence pour les modèles et les réglages d'inférence,
et sert à amorcer le fichier au premier démarrage — rien n'est perdu.
"""

import json
import threading
import time
from pathlib import Path

from app.config import load_config

CAMERAS_PATH = Path(__file__).resolve().parent.parent / "config" / "cameras.json"

_CACHE_TTL = 1.0
_cache: dict | None = None
_cache_time = 0.0
_lock = threading.Lock()


def _seed_from_yaml() -> dict:
    """Reprend les caméras déclarées dans config.yaml au premier démarrage."""
    try:
        cameras = load_config().get("cameras", {}) or {}
    except Exception:
        cameras = {}
    return {name: dict(cfg) for name, cfg in cameras.items()}


def load_cameras() -> dict:
    global _cache, _cache_time
    now = time.monotonic()
    if _cache is not None and now - _cache_time < _CACHE_TTL:
        return _cache

    data = None
    if CAMERAS_PATH.exists():
        try:
            with open(CAMERAS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = None

    if data is None:
        data = _seed_from_yaml()
        save_cameras(data)
        return data

    _cache = data
    _cache_time = now
    return data


def save_cameras(data: dict):
    global _cache, _cache_time
    with _lock:
        CAMERAS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CAMERAS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        _cache = data
        _cache_time = time.monotonic()


def get_camera(name: str) -> dict | None:
    return load_cameras().get(name)


def upsert_camera(name: str, config: dict) -> dict:
    """Crée ou met à jour une caméra. Le pipeline s'aligne au cycle suivant."""
    data = dict(load_cameras())
    existing = data.get(name, {})
    merged = {**existing, **config}
    data[name] = merged
    save_cameras(data)
    return merged


def delete_camera(name: str) -> bool:
    data = dict(load_cameras())
    if name not in data:
        return False
    del data[name]
    save_cameras(data)
    return True


def rename_camera(old: str, new: str) -> bool:
    data = dict(load_cameras())
    if old not in data or new in data:
        return False
    data[new] = data.pop(old)
    save_cameras(data)
    return True


def active_cameras() -> dict:
    """Caméras à traiter : une caméra désactivée reste configurée mais en pause.

    Utile pour suspendre une caméra en maintenance sans perdre ses zones ni ses
    réglages.
    """
    return {name: cfg for name, cfg in load_cameras().items() if cfg.get("enabled", True)}


def camera_source(cfg: dict):
    """Source de lecture d'une caméra, quelle que soit la clé utilisée.

    `source` est la forme actuelle ; `rtsp_url` est conservée pour les
    configurations écrites avant l'unification des sources.
    """
    return cfg.get("source", cfg.get("rtsp_url"))
