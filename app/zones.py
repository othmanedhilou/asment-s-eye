"""Zones d'intérêt (ROI) par caméra.

Sans zones, un modèle analyse toute l'image : le détecteur d'EPI se déclenche
sur le parking, celui de véhicules sur la route derrière la clôture. C'est la
première cause de fausses alertes sur site réel.

Une zone est un polygone dessiné sur l'image. Elle porte trois choses en plus de
sa géométrie, et chacune répond à un cas concret :

- **son type** — surveiller, ou au contraire ignorer. Masquer la route au fond du
  champ prend dix secondes ; contourner finement l'atelier pour l'éviter prend
  dix minutes et casse au premier déplacement de caméra.
- **son horaire** — la même détection ne veut pas dire la même chose selon
  l'heure. Le port du casque se contrôle pendant les postes ; une présence près
  des fours à 3 h du matin est justement l'anomalie recherchée.
- **ses seuils** — une zone de passage très fréquentée n'a pas besoin des mêmes
  réglages qu'un local technique désert.

Les coordonnées sont **normalisées** (0.0 à 1.0) : les zones restent valides si
la résolution de la caméra change, et se dessinent dans l'interface sans
connaître les dimensions réelles du flux.

Une caméra sans zone de surveillance analyse toute l'image — le comportement
d'avant, pour ne rien casser tant que les zones ne sont pas dessinées.
"""

import json
import threading
import time
from datetime import datetime, time as dtime
from pathlib import Path

ZONES_PATH = Path(__file__).resolve().parent.parent / "config" / "zones.json"

# Les zones sont relues à chaque image par le pipeline : on garde le fichier en
# cache une seconde, comme pour les réglages, afin de rester réactif à l'éditeur
# sans marteler le disque.
_CACHE_TTL = 1.0
_cache: dict | None = None
_cache_time = 0.0
_lock = threading.Lock()

# Zone implicite d'une caméra sans zone de surveillance : tout est analysé.
PLEIN_CADRE = {"name": "", "conf": None, "cooldown": None}


def load_zones() -> dict:
    """{"camera": [{"name", "polygon", "models", "type", "schedule", ...}]}"""
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
    with _lock:
        ZONES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ZONES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        _cache = data
        _cache_time = time.monotonic()


# ── Géométrie ────────────────────────────────────────────────────────


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


# ── Horaires ─────────────────────────────────────────────────────────


def _parse_hhmm(value: str) -> dtime | None:
    try:
        hour, minute = value.split(":")
        return dtime(int(hour), int(minute))
    except (ValueError, AttributeError):
        return None


def schedule_active(schedule: dict | None, now: datetime | None = None) -> bool:
    """La zone est-elle surveillée en ce moment ?

    Un horaire absent ou incomplet signifie « tout le temps » : une erreur de
    saisie ne doit jamais éteindre silencieusement une surveillance.
    """
    if not schedule:
        return True

    now = now or datetime.now()

    days = schedule.get("days")
    if days:
        # 0 = lundi, conformément à datetime.weekday()
        if now.weekday() not in days:
            return False

    start = _parse_hhmm(schedule.get("start", ""))
    end = _parse_hhmm(schedule.get("end", ""))
    if start is None or end is None:
        return True

    current = now.time()
    if start <= end:
        return start <= current < end
    # Plage à cheval sur minuit (22:00 → 06:00) : la surveillance de nuit est le
    # cas d'usage le plus fréquent, elle ne doit pas être le cas non géré.
    return current >= start or current < end


# ── Filtrage ─────────────────────────────────────────────────────────


class ZoneFilter:
    """Filtre les détections d'une caméra selon ses zones.

    Instancié une fois par caméra, relit les zones à chaque appel (via le cache)
    pour que l'éditeur prenne effet sans redémarrer le pipeline.
    """

    def __init__(self, camera: str):
        self.camera = camera

    def zones(self) -> list:
        return load_zones().get(self.camera, [])

    def _applies(self, zone: dict, model: str, now: datetime | None) -> bool:
        models = zone.get("models") or []
        # Une zone sans liste de modèles s'applique à tous.
        if models and model not in models:
            return False
        return schedule_active(zone.get("schedule"), now)

    def match(self, model: str, bbox, frame_width: int, frame_height: int,
              now: datetime | None = None) -> dict | None:
        """Zone retenue pour cette détection, ou None si elle doit être ignorée.

        Renvoie un dictionnaire portant le nom de la zone et ses éventuels
        seuils propres. `name` vide signifie « plein cadre ».
        """
        zones = self.zones()
        if not zones or not frame_width or not frame_height:
            return dict(PLEIN_CADRE)

        px, py = anchor_point(bbox)
        nx, ny = px / frame_width, py / frame_height

        inclusions = [z for z in zones if z.get("type") != "exclusion"]

        # Les exclusions priment : un objet dans une zone masquée est ignoré,
        # même s'il tombe aussi dans une zone surveillée qui la chevauche.
        for zone in zones:
            if zone.get("type") != "exclusion":
                continue
            if not self._applies(zone, model, now):
                continue
            if point_in_polygon(nx, ny, zone.get("polygon", [])):
                return None

        if not inclusions:
            return dict(PLEIN_CADRE)

        for zone in inclusions:
            if not self._applies(zone, model, now):
                continue
            if point_in_polygon(nx, ny, zone.get("polygon", [])):
                return {
                    "name": zone.get("name", "zone"),
                    "conf": zone.get("conf"),
                    "cooldown": zone.get("cooldown"),
                }
        return None

    def zone_for(self, model: str, bbox, frame_width: int, frame_height: int,
                 now: datetime | None = None) -> str | None:
        """Nom de la zone retenue, "" pour le plein cadre, None si ignorée."""
        matched = self.match(model, bbox, frame_width, frame_height, now)
        return None if matched is None else matched["name"]

    def polygons_in_pixels(self, frame_width: int, frame_height: int) -> list:
        """Zones converties en pixels, pour les dessiner sur l'image."""
        out = []
        for zone in self.zones():
            pts = [(int(x * frame_width), int(y * frame_height)) for x, y in zone.get("polygon", [])]
            if len(pts) >= 3:
                out.append({
                    "name": zone.get("name", "zone"),
                    "points": pts,
                    "type": zone.get("type", "surveillance"),
                })
        return out
