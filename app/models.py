from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Detection:
    camera: str
    model: str
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    timestamp: datetime = field(default_factory=datetime.now)
    zone: str = ""  # zone où la détection a été retenue ("" = plein cadre)
    # Réglages propres à la zone, quand elle en définit (sinon on retombe sur
    # les valeurs globales du modèle et de la sévérité).
    zone_conf: float | None = None
    zone_cooldown: float | None = None
    frame_size: tuple[int, int] | None = None  # (largeur, hauteur) de l'image source
    track_id: int | None = None  # identifiant de suivi, quand la caméra l'active


@dataclass
class Alert:
    camera: str
    model: str
    label: str
    confidence: float
    timestamp: datetime = field(default_factory=datetime.now)
    message: str = ""
    zone: str = ""
    # Position de la detection, conservee pour le re-entrainement : une image
    # deja pre-annotee demande une correction, pas une annotation complete.
    bbox: tuple[float, float, float, float] | None = None
    frame_size: tuple[int, int] | None = None
    db_id: int | None = None  # rempli après insertion en base
