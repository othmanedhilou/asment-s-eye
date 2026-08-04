"""
Moteur de regles metier - Ciment's Eye, Tache B.

Transforme un flux de Detection (produit par Personne A) en Alert filtrees
(consommees par Personne C), via trois filtres successifs :
    1. ZoneFilter        - la detection est-elle dans une zone d'interet ?
    2. TemporalConfirmer  - la detection persiste-t-elle assez longtemps ?
    3. CooldownManager    - une alerte identique a-t-elle deja ete emise recemment ?

Voir docs/TACHE_B_REGLES_ALERTES.md pour la specification complete.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from shapely.geometry import Point, Polygon

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contrats d'interface (A -> B -> C)
#
# Les objets Detection et Alert sont normalement fournis/factorises par les
# contrats d'equipe (fin S2). Ils sont definis ici en attendant leur livraison
# afin que le moteur de regles reste developpable et testable independamment
# (regle du bouchon, cf. docs/TACHE_B_REGLES_ALERTES.md).
# ---------------------------------------------------------------------------


@dataclass
class Detection:
    """Objet normalise produit par la couche inference (Personne A)."""

    camera_id: str
    timestamp: datetime
    class_name: str
    confidence: float
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2) en pixels
    frame_index: int


@dataclass
class Alert:
    """Objet produit par le moteur de regles apres filtrage, consomme par Personne C."""

    alert_id: str
    camera_id: str
    use_case: str
    alert_type: str
    zone: str
    confidence: float
    priority: str
    snapshot_path: str
    clip_path: str | None
    detected_at: datetime
    confirmed_at: datetime
    metadata: dict
    status: str = "NEW"


# ---------------------------------------------------------------------------
# 1. Filtre de zone d'interet (ROI)
# ---------------------------------------------------------------------------


class ZoneFilter:
    """Filtre les detections selon les zones d'interet definies par camera."""

    def __init__(self, zones_config: dict):
        """
        zones_config format :
        {
            "CAM-CONVOYEUR-01": {
                "zone_dangereuse": {
                    "polygon": [[0.1, 0.2], [0.8, 0.2], [0.8, 0.9], [0.1, 0.9]],
                    "use_cases": ["UC-01", "UC-04", "UC-09"],
                    "label": "convoyeur"
                }
            }
        }
        Les coordonnees du polygone sont normalisees dans [0, 1].
        """
        self.zones: dict[str, dict] = {}
        for cam_id, cam_zones in zones_config.items():
            self.zones[cam_id] = {}
            for zone_name, zone_def in cam_zones.items():
                self.zones[cam_id][zone_name] = {
                    "polygon": Polygon(zone_def["polygon"]),
                    "use_cases": zone_def["use_cases"],
                    "label": zone_def["label"],
                }

    def is_in_zone(self, detection: Detection, image_width: int, image_height: int) -> list[dict]:
        """Retourne les zones dont le polygone contient le centre de la detection."""
        cx = (detection.bbox[0] + detection.bbox[2]) / 2 / image_width
        cy = (detection.bbox[1] + detection.bbox[3]) / 2 / image_height
        point = Point(cx, cy)

        matched_zones = []
        cam_zones = self.zones.get(detection.camera_id, {})
        for zone_name, zone_def in cam_zones.items():
            # `covers` inclut la frontiere du polygone, contrairement a `contains`
            # (une detection pile sur le bord ne doit pas etre perdue).
            if zone_def["polygon"].covers(point):
                matched_zones.append(
                    {
                        "zone_name": zone_name,
                        "label": zone_def["label"],
                        "use_cases": zone_def["use_cases"],
                    }
                )
        return matched_zones


# ---------------------------------------------------------------------------
# 2. Confirmation temporelle
# ---------------------------------------------------------------------------


@dataclass
class DetectionState:
    """Etat d'une detection en cours de confirmation, pour une cle (camera, zone, use_case)."""

    first_seen: datetime | None = None
    last_seen: datetime | None = None
    consecutive_frames: int = 0
    total_confidence: float = 0.0
    confirmed: bool = False


class TemporalConfirmer:
    """
    Machine d'etat par (camera, zone, use_case).
    Confirme une detection seulement apres un delai minimal de presence continue,
    afin qu'une detection isolee (bruit d'un seul frame) ne devienne pas une alerte.
    """

    # Duree de confirmation par use case (en secondes)
    CONFIRMATION_DELAYS = {
        "UC-01": 3.0,   # casque absent > 3s
        "UC-02": 10.0,  # posture allongee > 10s
        "UC-03": 1.0,   # feu = confirmation rapide
        "UC-04": 2.0,   # vehicule zone interdite > 2s
        "UC-05": 5.0,   # geste fumeur > 5s
        "UC-06": 0.0,   # parking = mise a jour immediate
        "UC-08": 3.0,   # gilet absent > 3s
        "UC-09": 5.0,   # surpopulation > 5s
        "UC-11": 5.0,   # anomalie convoyeur > 5s
        "UC-12": 2.0,   # fumee anormale four > 2s
        "UC-13": 5.0,   # deversement > 5s
        "UC-14": 1.0,   # intrusion = confirmation rapide
    }

    DEFAULT_DELAY = 3.0
    GAP_RESET_SECONDS = 2.0  # au-dela, on considere la detection interrompue

    def __init__(self):
        # Cle : (camera_id, zone_label, use_case)
        self.states: dict[tuple, DetectionState] = defaultdict(DetectionState)

    def update(self, detection: Detection, zone_label: str, use_case: str) -> bool:
        """Met a jour l'etat et retourne True si la detection vient d'etre confirmee."""
        key = (detection.camera_id, zone_label, use_case)
        state = self.states[key]
        now = detection.timestamp

        # Premiere detection ou gap trop long depuis la derniere -> on repart de zero
        if state.last_seen is None or (now - state.last_seen).total_seconds() > self.GAP_RESET_SECONDS:
            state.first_seen = now
            state.consecutive_frames = 0
            state.total_confidence = 0.0
            state.confirmed = False

        state.last_seen = now
        state.consecutive_frames += 1
        state.total_confidence += detection.confidence

        delay = self.CONFIRMATION_DELAYS.get(use_case, self.DEFAULT_DELAY)
        elapsed = (now - state.first_seen).total_seconds()

        if elapsed >= delay and not state.confirmed:
            state.confirmed = True
            logger.info(
                "Confirmation temporelle : camera=%s zone=%s use_case=%s apres %.1fs",
                detection.camera_id, zone_label, use_case, elapsed,
            )
            return True

        return False

    def reset(self, camera_id: str, zone_label: str, use_case: str):
        """Force la reinitialisation d'un etat (par exemple apres expiration du cooldown)."""
        key = (camera_id, zone_label, use_case)
        self.states.pop(key, None)


# ---------------------------------------------------------------------------
# 3. Cooldown (anti-spam)
# ---------------------------------------------------------------------------


class CooldownManager:
    """Empeche le spam d'alertes identiques (meme camera, meme zone, meme use case)."""

    # Cooldown par use case (en secondes)
    COOLDOWN_DURATIONS = {
        "UC-01": 60,    # 1 alerte/minute max par camera
        "UC-02": 120,   # chute = 2 min cooldown
        "UC-03": 30,    # feu = cooldown court (critique)
        "UC-04": 60,
        "UC-05": 120,
        "UC-06": 30,    # parking = updates frequentes
        "UC-08": 60,
        "UC-09": 60,
        "UC-11": 120,
        "UC-12": 30,
        "UC-13": 120,
        "UC-14": 30,    # intrusion = cooldown court
    }

    DEFAULT_COOLDOWN = 60

    def __init__(self):
        self.last_alert_time: dict[tuple, datetime] = {}

    def is_in_cooldown(self, camera_id: str, zone_label: str, use_case: str, now: datetime) -> bool:
        """
        `now` est fourni par l'appelant (temps de la detection courante) plutot que
        lu via datetime.now() : le banc de rejeu doit pouvoir rejouer une sequence
        avec des horodatages issus de la video, pour rester reproductible.
        """
        key = (camera_id, zone_label, use_case)
        last_time = self.last_alert_time.get(key)
        if last_time is None:
            return False
        cooldown = self.COOLDOWN_DURATIONS.get(use_case, self.DEFAULT_COOLDOWN)
        return (now - last_time).total_seconds() < cooldown

    def register_alert(self, camera_id: str, zone_label: str, use_case: str, now: datetime):
        key = (camera_id, zone_label, use_case)
        self.last_alert_time[key] = now


# ---------------------------------------------------------------------------
# 4. Pipeline complet
# ---------------------------------------------------------------------------


class RuleEngine:
    """Pipeline complet : Detection -> filtrage ROI -> confirmation -> cooldown -> Alert."""

    PRIORITY_MAP = {
        "UC-01": "HAUTE",
        "UC-02": "CRITIQUE",
        "UC-03": "CRITIQUE",
        "UC-04": "HAUTE",
        "UC-05": "MOYENNE",
        "UC-06": "FAIBLE",
        "UC-08": "HAUTE",
        "UC-09": "HAUTE",
        "UC-11": "HAUTE",
        "UC-12": "CRITIQUE",
        "UC-13": "HAUTE",
        "UC-14": "CRITIQUE",
    }

    ALERT_TYPE_MAP = {
        "UC-01": "PPE_VIOLATION",
        "UC-02": "INCIDENT",
        "UC-03": "FIRE",
        "UC-04": "VEHICLE_INTRUSION",
        "UC-05": "SMOKING",
        "UC-06": "PARKING_UPDATE",
        "UC-08": "PPE_VIOLATION",
        "UC-09": "OVERCROWDING",
        "UC-11": "PROCESS_ANOMALY",
        "UC-12": "ABNORMAL_SMOKE",
        "UC-13": "SPILL",
        "UC-14": "INTRUSION",
    }

    # Mapping : quelle classe de detection peut declencher quel use case
    CLASS_TO_UC = {
        "no_helmet": ["UC-01"],
        "no_vest": ["UC-08"],
        "person": ["UC-09", "UC-14"],
        "fallen_person": ["UC-02"],
        "fire": ["UC-03"],
        "smoke": ["UC-03", "UC-12"],
        "truck": ["UC-04", "UC-06"],
        "car": ["UC-04", "UC-06"],
        "smoking_gesture": ["UC-05"],
    }

    DEFAULT_IMAGE_DIMENSIONS = (1920, 1080)

    def __init__(self, zones_config: dict, image_dimensions: dict):
        """
        zones_config : cf. ZoneFilter.
        image_dimensions : {camera_id: (width, height)}, utilise pour normaliser
        les boites en pixels avant le test d'appartenance a une zone.
        """
        self.zone_filter = ZoneFilter(zones_config)
        self.confirmer = TemporalConfirmer()
        self.cooldown = CooldownManager()
        self.image_dimensions = image_dimensions

    def process(self, detection: Detection) -> list[Alert]:
        """Traite une detection et retourne 0 a N alertes confirmees."""
        alerts: list[Alert] = []

        # 1. Determiner les use cases que cette classe de detection peut declencher
        possible_ucs = self.CLASS_TO_UC.get(detection.class_name, [])
        if not possible_ucs:
            return alerts

        # 2. Verifier dans quelles zones tombe la detection
        width, height = self.image_dimensions.get(detection.camera_id, self.DEFAULT_IMAGE_DIMENSIONS)
        matched_zones = self.zone_filter.is_in_zone(detection, width, height)

        for zone_info in matched_zones:
            for uc in possible_ucs:
                if uc not in zone_info["use_cases"]:
                    continue

                # 3. Cooldown : pas de nouvelle alerte identique trop recente
                if self.cooldown.is_in_cooldown(
                    detection.camera_id, zone_info["label"], uc, detection.timestamp
                ):
                    continue

                # 4. Confirmation temporelle : la detection doit persister
                confirmed = self.confirmer.update(detection, zone_info["label"], uc)
                if not confirmed:
                    continue

                alert = Alert(
                    alert_id=str(uuid.uuid4()),
                    camera_id=detection.camera_id,
                    use_case=uc,
                    alert_type=self.ALERT_TYPE_MAP.get(uc, "UNKNOWN"),
                    zone=zone_info["label"],
                    confidence=detection.confidence,
                    priority=self.PRIORITY_MAP.get(uc, "MOYENNE"),
                    snapshot_path="",  # rempli par le recorder (app/recorder.py)
                    clip_path=None,
                    detected_at=detection.timestamp,
                    confirmed_at=detection.timestamp,
                    metadata={
                        "class_name": detection.class_name,
                        "bbox": detection.bbox,
                        "zone_name": zone_info["zone_name"],
                    },
                )
                alerts.append(alert)
                self.cooldown.register_alert(
                    detection.camera_id, zone_info["label"], uc, detection.timestamp
                )
                logger.info(
                    "Alerte generee : %s camera=%s zone=%s priorite=%s",
                    alert.alert_type, alert.camera_id, alert.zone, alert.priority,
                )

        return alerts
