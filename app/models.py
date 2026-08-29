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


@dataclass
class Alert:
    camera: str
    model: str
    label: str
    confidence: float
    timestamp: datetime = field(default_factory=datetime.now)
    message: str = ""
    zone: str = ""
    db_id: int | None = None  # rempli après insertion en base
