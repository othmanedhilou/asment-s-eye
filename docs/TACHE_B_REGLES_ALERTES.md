# Tache B — Regles metier et traitement des alertes

> **Projet** : Ciment's Eye — Systeme de Surveillance Intelligente par Vision Artificielle
> **Responsable tache B** : DRISSI Yassir
> **Entreprise** : Asment Temara / Heidelberg Materials (Ciments du Maroc)
> **Equipe AI** : DRISSI Yassir, BOUSHABI Othmane, KHALIL Ahlam, DHILOU Othmane
> **Encadrant** : HAJIJE Moucine
> **Duree** : 10 semaines (S1-S10), charge cible 50 jours-homme

---

## Contexte du projet

Ciment's Eye est un logiciel de supervision video intelligente pour le site cimentier d'Asment Temara. Les modeles de detection (fumee, feu, EPI, chute, vehicules) sont deja entraines et disponibles sous forme de poids YOLOv8. Le perimetre de cette tache couvre uniquement la realisation du logiciel qui les exploite.

Le pipeline de production du site : **Convoyeur -> Primost -> Four**.

---

## Position dans l'architecture

```
Personne A (Acquisition video & inference)
        |
        | objet Detection (classe, confiance, boite en pixels)
        v
>>> PERSONNE B — REGLES METIER & ALERTES <<<
        |
        | objet Alert (champs persistes en base)
        v
Personne C (Backend, donnees & API)
        |
        | schemas JSON REST + WebSocket
        v
Personne D (Interface & exploitation)
```

La tache B est le **maillon central** : elle recoit les detections brutes de A et produit les alertes filtrees pour C. Sans elle, chaque detection brute deviendrait une alerte, noyant les operateurs sous les faux positifs.

---

## Fichiers sources

```
app/rules.py       # Moteur de regles (ROI, confirmation temporelle, cooldown)
app/recorder.py    # Snapshots annotes et clips video
app/notifier.py    # Notifications e-mail et Telegram
app/replay.py      # Banc de rejeu pour tests reproductibles
tests/test_rules.py
tests/test_recorder.py
```

---

## Use cases couverts

| UC | Intitule | Priorite alerte | Modele source |
|----|----------|-----------------|---------------|
| UC-01 | Detection casques de securite | Haute | YOLOv8 fine-tune PPE |
| UC-02 | Detection incidents / chutes | Critique | YOLOv8 fine-tune + pose |
| UC-03 | Detection de feu | Critique | YOLOv8 fine-tune feu/fumee |
| UC-04 | Detection vehicules zone interdite | Haute | YOLOv8 pre-entraine COCO |
| UC-05 | Detection fumeurs | Moyenne | YOLOv8 fine-tune + pose |
| UC-06 | Detection places parking | Faible | YOLOv8 pre-entraine COCO |
| UC-08 | Detection gilets haute visibilite | Haute | YOLOv8 fine-tune PPE |
| UC-09 | Comptage personnes zone dangereuse | Haute | YOLOv8 pre-entraine COCO |
| UC-11 | Surveillance convoyeur | Haute | Anomaly detection |
| UC-12 | Fumee anormale four | Critique | YOLOv8 fine-tune feu/fumee |
| UC-13 | Fuite / deversement sol | Haute | Ground change detection |
| UC-14 | Intrusion zone restreinte | Critique | YOLOv8 pre-entraine COCO |

---

## Planification detaillee

| Tache | Charge | Semaine | Statut |
|-------|--------|---------|--------|
| Specification des regles avec le service HSE (par zone, par modele) | 3 j | S1 | A faire |
| Filtre de zone d'interet : polygone normalise, appartenance, tests | 4 j | S2 | A faire |
| Confirmation temporelle et cooldown : machine d'etat par detecteur | 5 j | S3 | A faire |
| Tests unitaires du moteur de regles | 3 j | S3 | A faire |
| Snapshots annotes : boites, bandeau horodate, gravite | 3 j | S4 | A faire |
| Clips video : pre-roll depuis le tampon, post-roll, ecriture asynchrone | 5 j | S4-S5 | A faire |
| Notifications e-mail et Telegram, file d'attente asynchrone | 5 j | S6 | A faire |
| Banc de rejeu : rejouer des sequences enregistrees a la place du flux | 5 j | S6-S7 | A faire |
| Campagne de reglage des seuils par zone, mesure des fausses alertes | 3 j | S8 | A faire |
| Documentation des regles et des parametres | 2 j | S8 | A faire |

**Travail collectif (12 j supplementaires) :**

| Tache collective | Charge | Quand |
|------------------|--------|-------|
| Integration hebdomadaire et revues croisees | 4 j | S2-S9 |
| Campagne de tests systeme | 3 j | S8 |
| Reglage et validation sur site | 2 j | S9 |
| Documentation, memoire, soutenance | 3 j | S10 |

---

## Specifications techniques detaillees

### 1. Objet Detection (entree — fourni par Personne A)

```python
@dataclass
class Detection:
    """Objet normalise produit par la couche inference (Personne A)."""
    camera_id: str          # ex: "CAM-CONVOYEUR-01"
    timestamp: datetime     # horodatage de la frame analysee
    class_name: str         # ex: "person", "fire", "smoke", "no_helmet", "truck"
    confidence: float       # score de confiance [0.0, 1.0]
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2) en pixels
    frame_index: int        # numero de frame dans le flux
```

### 2. Objet Alert (sortie — consomme par Personne C)

```python
@dataclass
class Alert:
    """Objet produit par le moteur de regles apres filtrage."""
    alert_id: str               # identifiant unique genere (UUID)
    camera_id: str
    use_case: str               # "UC-01", "UC-03", etc.
    alert_type: str             # "PPE_VIOLATION", "FIRE", "INTRUSION", etc.
    zone: str                   # "convoyeur", "four", "primost", "parking"
    confidence: float           # confiance moyenne sur la periode de confirmation
    priority: str               # "CRITIQUE", "HAUTE", "MOYENNE", "FAIBLE"
    snapshot_path: str           # chemin du snapshot annote (MinIO)
    clip_path: str | None       # chemin du clip video si applicable
    detected_at: datetime       # debut de la detection
    confirmed_at: datetime      # moment de la confirmation (apres delai)
    metadata: dict              # donnees supplementaires (object_count, duration, etc.)
    status: str = "NEW"         # NEW / ACKNOWLEDGED / RESOLVED
```

### 3. Moteur de regles — app/rules.py

Le moteur de regles transforme un flux de `Detection` en `Alert` filtrees. Trois filtres successifs :

#### 3.1 Filtre de zone d'interet (ROI)

Chaque camera a des zones definies comme des polygones normalises (coordonnees [0,1]).

```python
from shapely.geometry import Point, Polygon

class ZoneFilter:
    """Filtre les detections selon les zones d'interet definies par camera."""

    def __init__(self, zones_config: dict):
        """
        zones_config format:
        {
            "CAM-CONVOYEUR-01": {
                "zone_dangereuse": {
                    "polygon": [[0.1, 0.2], [0.8, 0.2], [0.8, 0.9], [0.1, 0.9]],
                    "use_cases": ["UC-01", "UC-04", "UC-09"],
                    "label": "convoyeur"
                },
                "zone_interdite": {
                    "polygon": [[0.3, 0.1], [0.6, 0.1], [0.6, 0.5], [0.3, 0.5]],
                    "use_cases": ["UC-14"],
                    "label": "acces_four"
                }
            }
        }
        """
        self.zones = {}
        for cam_id, cam_zones in zones_config.items():
            self.zones[cam_id] = {}
            for zone_name, zone_def in cam_zones.items():
                self.zones[cam_id][zone_name] = {
                    "polygon": Polygon(zone_def["polygon"]),
                    "use_cases": zone_def["use_cases"],
                    "label": zone_def["label"]
                }

    def is_in_zone(self, detection: Detection, image_width: int, image_height: int) -> list[dict]:
        """Retourne les zones contenant le centre de la detection."""
        cx = (detection.bbox[0] + detection.bbox[2]) / 2 / image_width
        cy = (detection.bbox[1] + detection.bbox[3]) / 2 / image_height
        point = Point(cx, cy)

        matched_zones = []
        cam_zones = self.zones.get(detection.camera_id, {})
        for zone_name, zone_def in cam_zones.items():
            if zone_def["polygon"].contains(point):
                matched_zones.append({
                    "zone_name": zone_name,
                    "label": zone_def["label"],
                    "use_cases": zone_def["use_cases"]
                })
        return matched_zones
```

#### 3.2 Confirmation temporelle

Une detection isolee n'est pas une alerte. Le systeme attend que la detection persiste pendant un nombre minimal d'images consecutives avant de confirmer.

```python
from collections import defaultdict
from dataclasses import dataclass, field

@dataclass
class DetectionState:
    """Etat d'une detection en cours de confirmation."""
    first_seen: datetime = None
    last_seen: datetime = None
    consecutive_frames: int = 0
    total_confidence: float = 0.0
    confirmed: bool = False

class TemporalConfirmer:
    """
    Machine d'etat par (camera, zone, use_case).
    Confirme une detection seulement apres N frames consecutives.
    """

    # Durees de confirmation par use case (en secondes)
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

    def __init__(self):
        # Cle : (camera_id, zone_label, use_case)
        self.states: dict[tuple, DetectionState] = defaultdict(DetectionState)

    def update(self, detection: Detection, zone_label: str, use_case: str) -> bool:
        """
        Met a jour l'etat et retourne True si la detection est confirmee.
        """
        key = (detection.camera_id, zone_label, use_case)
        state = self.states[key]
        now = detection.timestamp

        # Premiere detection ou gap trop long -> reset
        if state.last_seen is None or (now - state.last_seen).total_seconds() > 2.0:
            state.first_seen = now
            state.consecutive_frames = 0
            state.total_confidence = 0.0
            state.confirmed = False

        state.last_seen = now
        state.consecutive_frames += 1
        state.total_confidence += detection.confidence

        delay = self.CONFIRMATION_DELAYS.get(use_case, 3.0)
        elapsed = (now - state.first_seen).total_seconds()

        if elapsed >= delay and not state.confirmed:
            state.confirmed = True
            return True

        return False

    def reset(self, camera_id: str, zone_label: str, use_case: str):
        """Reset apres cooldown expire."""
        key = (camera_id, zone_label, use_case)
        if key in self.states:
            del self.states[key]
```

#### 3.3 Cooldown (anti-spam)

Apres une alerte confirmee, aucune nouvelle alerte identique n'est emise pendant une duree de cooldown.

```python
class CooldownManager:
    """Empeche le spam d'alertes identiques."""

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

    def __init__(self):
        self.last_alert_time: dict[tuple, datetime] = {}

    def is_in_cooldown(self, camera_id: str, zone_label: str, use_case: str) -> bool:
        key = (camera_id, zone_label, use_case)
        last_time = self.last_alert_time.get(key)
        if last_time is None:
            return False
        cooldown = self.COOLDOWN_DURATIONS.get(use_case, 60)
        return (datetime.now() - last_time).total_seconds() < cooldown

    def register_alert(self, camera_id: str, zone_label: str, use_case: str):
        key = (camera_id, zone_label, use_case)
        self.last_alert_time[key] = datetime.now()
```

#### 3.4 Pipeline complet du moteur de regles

```python
class RuleEngine:
    """
    Pipeline complet : Detection -> filtrage ROI -> confirmation -> cooldown -> Alert
    """

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

    # Mapping : quelle classe de detection declenche quel UC
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

    def __init__(self, zones_config: dict, image_dimensions: dict):
        self.zone_filter = ZoneFilter(zones_config)
        self.confirmer = TemporalConfirmer()
        self.cooldown = CooldownManager()
        self.image_dimensions = image_dimensions  # {cam_id: (width, height)}

    def process(self, detection: Detection) -> list[Alert]:
        """Traite une detection et retourne 0 ou N alertes."""
        alerts = []

        # 1. Determiner les UC possibles pour cette classe
        possible_ucs = self.CLASS_TO_UC.get(detection.class_name, [])
        if not possible_ucs:
            return alerts

        # 2. Verifier les zones
        dims = self.image_dimensions.get(detection.camera_id, (1920, 1080))
        matched_zones = self.zone_filter.is_in_zone(detection, dims[0], dims[1])

        for zone_info in matched_zones:
            for uc in possible_ucs:
                if uc not in zone_info["use_cases"]:
                    continue

                # 3. Verifier le cooldown
                if self.cooldown.is_in_cooldown(
                    detection.camera_id, zone_info["label"], uc
                ):
                    continue

                # 4. Confirmation temporelle
                confirmed = self.confirmer.update(
                    detection, zone_info["label"], uc
                )

                if confirmed:
                    alert = Alert(
                        alert_id=str(uuid.uuid4()),
                        camera_id=detection.camera_id,
                        use_case=uc,
                        alert_type=self.ALERT_TYPE_MAP.get(uc, "UNKNOWN"),
                        zone=zone_info["label"],
                        confidence=detection.confidence,
                        priority=self.PRIORITY_MAP.get(uc, "MOYENNE"),
                        snapshot_path="",  # rempli par le recorder
                        clip_path=None,
                        detected_at=detection.timestamp,
                        confirmed_at=datetime.now(),
                        metadata={
                            "class_name": detection.class_name,
                            "bbox": detection.bbox,
                            "zone_name": zone_info["zone_name"],
                        },
                    )
                    alerts.append(alert)
                    self.cooldown.register_alert(
                        detection.camera_id, zone_info["label"], uc
                    )

        return alerts
```

---

### 4. Recorder — app/recorder.py

#### 4.1 Snapshots annotes

```python
import cv2
from datetime import datetime

PRIORITY_COLORS = {
    "CRITIQUE": (0, 0, 255),   # rouge
    "HAUTE": (0, 165, 255),    # orange
    "MOYENNE": (0, 255, 255),  # jaune
    "FAIBLE": (0, 255, 0),     # vert
}

class SnapshotRecorder:
    """Genere des snapshots annotes avec boites de detection et bandeau."""

    def create_snapshot(
        self,
        frame: np.ndarray,
        alert: Alert,
        detection: Detection
    ) -> bytes:
        """
        Annote la frame avec :
        - bounding box coloree selon la priorite
        - label (classe + confiance)
        - bandeau inferieur : camera, zone, horodatage, UC
        """
        annotated = frame.copy()
        color = PRIORITY_COLORS.get(alert.priority, (255, 255, 255))
        x1, y1, x2, y2 = detection.bbox

        # Boite de detection
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)

        # Label
        label = f"{detection.class_name} {detection.confidence:.0%}"
        cv2.putText(annotated, label, (x1, y1 - 10),
                     cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        # Bandeau inferieur
        h, w = annotated.shape[:2]
        banner_h = 40
        cv2.rectangle(annotated, (0, h - banner_h), (w, h), (0, 0, 0), -1)
        text = (
            f"{alert.camera_id} | {alert.zone} | "
            f"{alert.use_case} | {alert.priority} | "
            f"{alert.detected_at.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        cv2.putText(annotated, text, (10, h - 12),
                     cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        _, buffer = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return buffer.tobytes()
```

#### 4.2 Clips video

```python
import asyncio
from collections import deque

class ClipRecorder:
    """
    Enregistre des clips autour de l'evenement.
    Utilise le buffer de pre-roll fourni par la couche acquisition (Personne A).
    """

    PRE_ROLL_SECONDS = 5    # secondes avant l'alerte
    POST_ROLL_SECONDS = 10  # secondes apres l'alerte

    async def record_clip(
        self,
        camera_id: str,
        pre_roll_buffer: deque,  # fourni par Personne A
        alert: Alert,
        fps: int = 4
    ) -> str:
        """
        Assemble le pre-roll + post-roll en un clip MP4.
        Retourne le chemin du fichier.
        """
        clip_path = f"/tmp/clips/{alert.alert_id}.mp4"
        h, w = pre_roll_buffer[0].shape[:2]

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(clip_path, fourcc, fps, (w, h))

        # Ecrire le pre-roll
        for frame in pre_roll_buffer:
            writer.write(frame)

        # Capturer le post-roll (attendre POST_ROLL_SECONDS)
        # Le flux continue d'arriver via le buffer partage
        await asyncio.sleep(self.POST_ROLL_SECONDS)

        writer.release()
        return clip_path
```

---

### 5. Notifier — app/notifier.py

```python
import smtplib
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

class AlertNotifier:
    """
    File d'attente asynchrone de notifications.
    Supporte e-mail et Telegram.
    """

    # Qui notifier selon la priorite
    NOTIFICATION_RULES = {
        "CRITIQUE": {
            "email": True,
            "telegram": True,
            "recipients": ["securite@asmenttemara.ma", "superviseur@asmenttemara.ma"]
        },
        "HAUTE": {
            "email": True,
            "telegram": True,
            "recipients": ["superviseur@asmenttemara.ma"]
        },
        "MOYENNE": {
            "email": True,
            "telegram": False,
            "recipients": ["superviseur@asmenttemara.ma"]
        },
        "FAIBLE": {
            "email": False,
            "telegram": False,
            "recipients": []
        },
    }

    def __init__(self, smtp_config: dict, telegram_config: dict):
        self.smtp_config = smtp_config
        self.telegram_config = telegram_config
        self.queue: asyncio.Queue = asyncio.Queue()

    async def enqueue(self, alert: Alert, snapshot_bytes: bytes | None = None):
        """Ajoute une alerte a la file de notifications."""
        await self.queue.put((alert, snapshot_bytes))

    async def worker(self):
        """Boucle de traitement asynchrone des notifications."""
        while True:
            alert, snapshot = await self.queue.get()
            rules = self.NOTIFICATION_RULES.get(alert.priority, {})

            if rules.get("email"):
                await self._send_email(alert, snapshot, rules["recipients"])

            if rules.get("telegram"):
                await self._send_telegram(alert, snapshot)

            self.queue.task_done()

    async def _send_email(self, alert: Alert, snapshot: bytes, recipients: list):
        """Envoi e-mail avec snapshot en piece jointe."""
        # Implementation SMTP asynchrone
        pass

    async def _send_telegram(self, alert: Alert, snapshot: bytes):
        """Envoi Telegram avec image."""
        # Implementation API Telegram Bot
        pass
```

---

### 6. Banc de rejeu — app/replay.py

Le banc de rejeu est **critique** : c'est ce qui rend les resultats reproductibles et defensibles dans le memoire.

```python
import cv2
from pathlib import Path

class ReplayBench:
    """
    Rejoue des sequences video enregistrees a la place du flux live.
    Permet de mesurer de maniere reproductible :
    - le taux de fausses alertes
    - le delai de detection
    - l'impact de chaque filtre (ROI, confirmation, cooldown)
    """

    def __init__(self, rule_engine: RuleEngine):
        self.rule_engine = rule_engine
        self.results = []

    def replay_sequence(
        self,
        video_path: str,
        camera_id: str,
        detector,  # fourni par Personne A
        ground_truth: list[dict] | None = None
    ) -> dict:
        """
        Rejoue une sequence et collecte les alertes generees.

        ground_truth format (optionnel, pour mesure de performance) :
        [
            {"timestamp": 5.2, "event": "fire", "is_true_positive": True},
            {"timestamp": 12.0, "event": "dust_cloud", "is_true_positive": False},
        ]
        """
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        alerts_generated = []
        frame_count = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            # Simuler la detection (Personne A)
            detections = detector.detect(frame, camera_id)

            for detection in detections:
                alerts = self.rule_engine.process(detection)
                for alert in alerts:
                    alert.metadata["replay_frame"] = frame_count
                    alert.metadata["replay_timestamp"] = frame_count / fps
                    alerts_generated.append(alert)

            frame_count += 1

        cap.release()

        return {
            "video_path": video_path,
            "total_frames": frame_count,
            "duration_seconds": frame_count / fps,
            "alerts_count": len(alerts_generated),
            "alerts": alerts_generated,
        }

    def comparative_test(self, video_path: str, camera_id: str, detector) -> dict:
        """
        Experience centrale du projet :
        Rejoue la meme sequence dans 4 configurations progressives.
        Mesure l'apport de chaque filtre anti-fausses alertes.
        """
        configs = {
            "C0_modele_brut": {"roi": False, "confirmation": False, "cooldown": False},
            "C1_avec_roi": {"roi": True, "confirmation": False, "cooldown": False},
            "C2_avec_confirmation": {"roi": True, "confirmation": True, "cooldown": False},
            "C3_systeme_complet": {"roi": True, "confirmation": True, "cooldown": True},
        }

        results = {}
        for config_name, flags in configs.items():
            engine = self._build_engine(flags)
            self.rule_engine = engine
            result = self.replay_sequence(video_path, camera_id, detector)
            results[config_name] = {
                "alerts_total": result["alerts_count"],
                "alerts_by_uc": self._count_by_uc(result["alerts"]),
            }

        return results

    def _build_engine(self, flags: dict) -> RuleEngine:
        """Construit un moteur de regles avec les filtres actives/desactives."""
        # Implementation : creer un RuleEngine configurable
        pass

    def _count_by_uc(self, alerts: list[Alert]) -> dict:
        counts = {}
        for a in alerts:
            counts[a.use_case] = counts.get(a.use_case, 0) + 1
        return counts
```

#### Sequences de reference a constituer

| Sequence | Resultat attendu |
|----------|-----------------|
| Depart de fumee reel (four) | Alerte UC-03 en moins de 15 s |
| Passage camion soulevant de la poussiere | Aucune alerte (pas de confusion fumee/poussiere) |
| Panache de vapeur du refroidisseur | Aucune alerte |
| Brume matinale | Aucune alerte |
| Ouvrier sans casque en zone production | Alerte UC-01 apres 3 s |
| Ouvrier sans casque au parking | Aucune alerte (hors zone obligatoire) |
| Personne tombee au sol > 10 s | Alerte UC-02 |
| Personne qui se penche brievement | Aucune alerte |
| Vehicule dans zone interdite > 2 s | Alerte UC-04 |
| Flux interrompu en cours de sequence | Camera marquee hors ligne, reprise |

---

### 7. Configuration — structure YAML

```yaml
# config.yaml — section regles (Personne B)

rules:
  # Seuils de confiance par modele (calibres sur site)
  confidence_thresholds:
    fire: 0.70
    smoke: 0.65
    no_helmet: 0.80
    no_vest: 0.80
    person: 0.60
    fallen_person: 0.75
    truck: 0.70
    car: 0.70

  # Zones d'interet par camera
  zones:
    CAM-CONVOYEUR-01:
      zone_convoyeur:
        polygon: [[0.05, 0.15], [0.95, 0.15], [0.95, 0.85], [0.05, 0.85]]
        use_cases: [UC-01, UC-08, UC-09, UC-11]
        label: convoyeur
      zone_interdite_convoyeur:
        polygon: [[0.30, 0.20], [0.70, 0.20], [0.70, 0.60], [0.30, 0.60]]
        use_cases: [UC-14]
        label: convoyeur_interdit

    CAM-FOUR-01:
      zone_four:
        polygon: [[0.10, 0.10], [0.90, 0.10], [0.90, 0.90], [0.10, 0.90]]
        use_cases: [UC-01, UC-03, UC-08, UC-09, UC-12]
        label: four
      zone_acces_four:
        polygon: [[0.40, 0.60], [0.60, 0.60], [0.60, 0.95], [0.40, 0.95]]
        use_cases: [UC-14, UC-04]
        label: acces_four

    CAM-PARKING-01:
      zone_parking:
        polygon: [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
        use_cases: [UC-04, UC-06]
        label: parking

    CAM-ENTREE-01:
      zone_entree:
        polygon: [[0.20, 0.10], [0.80, 0.10], [0.80, 0.90], [0.20, 0.90]]
        use_cases: [UC-04, UC-15]
        label: entree_site

  # Delais de confirmation (secondes)
  confirmation_delays:
    UC-01: 3.0
    UC-02: 10.0
    UC-03: 1.0
    UC-04: 2.0
    UC-05: 5.0
    UC-06: 0.0
    UC-08: 3.0
    UC-09: 5.0
    UC-11: 5.0
    UC-12: 2.0
    UC-13: 5.0
    UC-14: 1.0

  # Cooldown (secondes)
  cooldown_durations:
    UC-01: 60
    UC-02: 120
    UC-03: 30
    UC-04: 60
    UC-05: 120
    UC-06: 30
    UC-08: 60
    UC-09: 60
    UC-11: 120
    UC-12: 30
    UC-13: 120
    UC-14: 30

notifications:
  smtp:
    host: ""          # a remplir sur site
    port: 587
    user: ""
    password: ""      # jamais dans le depot Git
  telegram:
    bot_token: ""     # jamais dans le depot Git
    chat_id: ""
```

---

## Contrats d'interface a respecter

| Frontiere | Contrat | Responsable | A figer en |
|-----------|---------|-------------|------------|
| A -> B | objet `Detection` : classe, confiance, boite en pixels | Personne A | fin S2 |
| B -> C | objet `Alert` et champs persistes en base | **Personne B (moi)** | fin S2 |
| C -> D | schemas JSON de l'API REST et du WebSocket | Personne C | fin S2 |
| C -> tous | structure de `config.yaml` | Personne C | fin S2 |
| A -> tous | liste des classes reelles de chaque modele fourni | Personne A | fin S2 |

**Regle** : tant qu'un contrat n'est pas livre, developper contre un bouchon (detecteur factice, API simulee). Les bouchons servent ensuite aux tests automatises.

---

## Tests unitaires obligatoires

```python
# tests/test_rules.py

class TestZoneFilter:
    def test_detection_inside_zone(self):
        """Une detection dans la zone doit etre acceptee."""
        pass

    def test_detection_outside_zone(self):
        """Une detection hors zone doit etre rejetee."""
        pass

    def test_detection_on_boundary(self):
        """Une detection sur le bord du polygone."""
        pass

class TestTemporalConfirmer:
    def test_single_detection_not_confirmed(self):
        """Une seule detection ne doit pas generer d'alerte."""
        pass

    def test_sustained_detection_confirmed(self):
        """Detections soutenues > delai doivent confirmer."""
        pass

    def test_gap_resets_counter(self):
        """Un gap > 2s entre detections reset le compteur."""
        pass

    def test_different_delays_per_uc(self):
        """Chaque UC a son propre delai de confirmation."""
        pass

class TestCooldown:
    def test_alert_during_cooldown_blocked(self):
        """Pas de nouvelle alerte pendant le cooldown."""
        pass

    def test_alert_after_cooldown_allowed(self):
        """Alerte autorisee apres expiration du cooldown."""
        pass

class TestRuleEngine:
    def test_full_pipeline_fire(self):
        """Feu detecte dans zone -> alerte CRITIQUE en < 1s."""
        pass

    def test_full_pipeline_false_positive_dust(self):
        """Poussiere hors zone -> aucune alerte."""
        pass

    def test_two_simultaneous_alerts(self):
        """Deux alertes sur deux cameras = les deux traitees."""
        pass
```

---

## Livrables de la tache B

| Livrable | Description | Semaine |
|----------|-------------|---------|
| Specification regles HSE | Document des regles par zone et par modele | S1 |
| `app/rules.py` | Moteur de regles complet (ROI + confirmation + cooldown) | S2-S3 |
| `tests/test_rules.py` | Tests unitaires du moteur de regles | S3 |
| `app/recorder.py` | Snapshots annotes + clips video | S4-S5 |
| `app/notifier.py` | Notifications e-mail et Telegram | S6 |
| `app/replay.py` | Banc de rejeu reproductible | S6-S7 |
| Tableau seuils | Seuils recommandes par zone, resultats fausses alertes | S8 |
| Documentation regles | Parametres et justifications pour chaque reglage | S8 |
| Tableau C0-C3 | Experience comparative : apport de chaque filtre | S9 |

---

## Points d'attention critiques

1. **Le banc de rejeu est la piece maitresse.** Sans lui, chaque mesure de fausses alertes depend de ce qui se passe sur le site ce jour-la et n'est pas defensible dans le memoire. C'est ce qui rend les resultats reproductibles.

2. **Poussiere vs fumee.** Le defi majeur specifique a la cimenterie : l'environnement genere naturellement beaucoup de poussiere blanche/grise visuellement proche de la fumee. Les seuils de confiance pour UC-03 et UC-12 doivent etre calibres specifiquement par camera, pas un seuil global.

3. **Frontiere thread/async.** La remontee d'alerte traverse une frontiere delicate : le moteur d'inference (Personne A) tourne dans un thread, l'API (Personne C) dans une boucle asynchrone. Tester explicitement que les alertes ne se perdent pas a cette jonction.

4. **Seuil par camera, pas global.** Une zone poussiereuse demandera un seuil plus eleve qu'une zone propre. Le fichier config.yaml doit permettre un seuil par camera/zone.

5. **Objectif fausses alertes.** Au-dela de 2 fausses alertes par jour et par camera, l'experience montre que le systeme est ignore puis eteint par les operateurs. C'est le critere d'adoption.

6. **Latence cible.** Image capturee -> notification recue : moins de 3 secondes pour les alertes CRITIQUE (feu, intrusion).

---

## Dependances Python

```
# requirements.txt — dependances tache B
shapely>=2.0.0          # geometrie polygones (ROI)
opencv-python-headless>=4.9.0
numpy>=1.24.0
pyyaml>=6.0             # lecture config
pytest>=7.4.0           # tests
pytest-asyncio>=0.21.0  # tests async
aiosmtplib>=2.0.0       # envoi e-mail async
httpx>=0.24.0           # appels API Telegram
```

---

## Branche Git

```
feat/rules    # branche dediee tache B
```

Fusion dans `main` en fin de semaine, revue croisee obligatoire par une personne d'un autre role.

---

*Ciment's Eye — Tache B / Asment Temara 2025*
*DRISSI Yassir | Encadrant : HAJIJE Moucine*
