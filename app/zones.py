"""Zones d'intérêt (ROI) par caméra.

Sans zones, un modèle analyse toute l'image : le détecteur d'EPI se déclenche
sur le parking, celui de véhicules sur la route derrière la clôture. C'est la
première cause de fausses alertes sur site réel.

Une zone est un polygone dessiné sur l'image, associé aux modèles qui ont un
sens dedans (EPI dans l'atelier, véhicules sur le quai de chargement). Une
détection n'est retenue que si son point d'ancrage tombe dans au moins une zone
acceptant son modèle.

Les coordonnées sont **normalisées** (0.0 à 1.0) : les zones restent valides si
la résolution de la caméra change, et se dessinent dans l'interface sans
connaître les dimensions réelles du flux.

Une caméra sans zone déclarée analyse toute l'image — le comportement d'avant,
pour ne rien casser tant que les zones ne sont pas dessinées.
"""

import json
import time
from pathlib import Path

ZONES_PATH = Path(__file__).resolve().parent.parent / "config" / "zones.json"

# Les zones sont relues à chaque image par le pipeline : on garde le fichier en
# cache une seconde, comme pour les réglages, afin de rester réactif à l'éditeur
# sans marteler le disque.
_CACHE_TTL = 1.0
_cache: dict | None = None
_cache_time = 0.0


def load_zones() -> dict:
    """{"camera": [{"name": ..., "polygon": [[x, y], ...], "models": [...]}]}"""
    global _cache, _cache_time
    now = time.monotonic()
    if _cache is not None and now - _cache_time < _CACHE_TTL:
        return _cache

    data = {}
    if ZONES_PATH.exists():
        try:
            with open(ZONES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

    _cache = data
    _cache_time = now
    return data


def save_zones(data: dict):
    global _cache, _cache_time
    ZONES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ZONES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    _cache = data
    _cache_time = time.monotonic()


def anchor_point(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    """Point de la boîte qui détermine son appartenance à une zone.

    On prend le milieu du bord bas : pour une personne ou un véhicule, c'est le
    contact avec le sol. Le centre de la boîte placerait un ouvrier debout au
    niveau de son torse, donc potentiellement hors zone alors que ses pieds y
    sont — ou l'inverse près d'une limite.
    """
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, y2)


def point_in_polygon(x: float, y: float, polygon: list) -> bool:
    """Test d'appartenance par lancer de rayon (algorithme pair-impair).

    Implémenté à la main plutôt qu'avec shapely : une dépendance de moins à
    installer sur le serveur, pour vingt lignes de code stable.
    """
    if len(polygon) < 3:
        return False

    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        # Le segment croise-t-il la demi-droite horizontale partant du point ?
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


class ZoneFilter:
    """Filtre les détections d'une caméra selon ses zones.

    Instancié une fois par caméra, relit les zones à chaque appel (via le cache)
    pour que l'éditeur prenne effet sans redémarrer le pipeline.
    """

    def __init__(self, camera: str):
        self.camera = camera

    def zones(self) -> list:
        return load_zones().get(self.camera, [])

    def zone_for(self, model: str, bbox, frame_width: int, frame_height: int) -> str | None:
        """Nom de la première zone qui accepte cette détection.

        Retourne "" si la caméra n'a aucune zone (tout est accepté, sans nom de
        zone), None si les zones existent mais qu'aucune ne correspond.
        """
        zones = self.zones()
        if not zones:
            return ""  # aucune zone définie : analyse plein cadre

        if not frame_width or not frame_height:
            return ""

        px, py = anchor_point(bbox)
        nx, ny = px / frame_width, py / frame_height

        for zone in zones:
            models = zone.get("models") or []
            # Une zone sans liste de modèles s'applique à tous.
            if models and model not in models:
                continue
            if point_in_polygon(nx, ny, zone.get("polygon", [])):
                return zone.get("name", "zone")
        return None

    def polygons_in_pixels(self, frame_width: int, frame_height: int) -> list:
        """Zones converties en pixels, pour les dessiner sur l'image."""
        out = []
        for zone in self.zones():
            pts = [(int(x * frame_width), int(y * frame_height)) for x, y in zone.get("polygon", [])]
            if len(pts) >= 3:
                out.append({"name": zone.get("name", "zone"), "points": pts})
        return out
