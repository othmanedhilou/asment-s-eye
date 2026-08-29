from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Float, String, create_engine, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.models import Alert

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "smokewatch.db"

# Sévérité métier par modèle (logique HSE site cimentier)
SEVERITY_BY_MODEL = {
    "fire_smoke": "critique",
    "arc": "critique",
    "conveyor": "haute",       # bande déchirée : arrêt production + risque opérateur
    "gloves_glasses": "haute",   # inclut Fall-Detected
    "epi": "haute",
    "load_control": "moyenne",
    "person_animal": "moyenne",
    "vehicles": "moyenne",
}

# Fall-Detected est critique même si le modèle est classé "haute"
SEVERITY_BY_LABEL = {
    "Fall-Detected": "critique",
}


def severity_for(model: str, label: str) -> str:
    return SEVERITY_BY_LABEL.get(label) or SEVERITY_BY_MODEL.get(model, "moyenne")


class Base(DeclarativeBase):
    pass


class AlertRecord(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    camera: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(100))
    confidence: Mapped[float] = mapped_column(Float)
    message: Mapped[str] = mapped_column(String(500))
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    snapshot: Mapped[str | None] = mapped_column(String(500), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="moyenne")
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    ack_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ack_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    clip: Mapped[str | None] = mapped_column(String(500), nullable=True)
    zone: Mapped[str | None] = mapped_column(String(100), nullable=True)


_engine = None


def _migrate(engine):
    """Ajoute les colonnes manquantes sur une base créée avant cette version."""
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(alerts)"))}
        migrations = {
            "severity": "ALTER TABLE alerts ADD COLUMN severity VARCHAR(20) DEFAULT 'moyenne'",
            "acknowledged": "ALTER TABLE alerts ADD COLUMN acknowledged BOOLEAN DEFAULT 0",
            "ack_by": "ALTER TABLE alerts ADD COLUMN ack_by VARCHAR(100)",
            "ack_at": "ALTER TABLE alerts ADD COLUMN ack_at DATETIME",
            "clip": "ALTER TABLE alerts ADD COLUMN clip VARCHAR(500)",
            "zone": "ALTER TABLE alerts ADD COLUMN zone VARCHAR(100)",
        }
        for col, ddl in migrations.items():
            if col not in cols:
                conn.execute(text(ddl))
        conn.commit()


def _get_engine():
    global _engine
    if _engine is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{DB_PATH}")
        Base.metadata.create_all(_engine)
        _migrate(_engine)
    return _engine


def _to_dict(r: AlertRecord) -> dict:
    return {
        "id": r.id,
        "camera": r.camera,
        "model": r.model,
        "label": r.label,
        "confidence": r.confidence,
        "message": r.message,
        "timestamp": r.timestamp.isoformat(),
        "snapshot": r.snapshot,
        "severity": r.severity,
        "acknowledged": bool(r.acknowledged),
        "ack_by": r.ack_by,
        "ack_at": r.ack_at.isoformat() if r.ack_at else None,
        "clip": r.clip,
        "zone": r.zone or "",
    }


def log_alert(alert: Alert, snapshot_path: str | None = None) -> int:
    engine = _get_engine()
    with Session(engine) as session:
        record = AlertRecord(
            camera=alert.camera,
            model=alert.model,
            label=alert.label,
            confidence=alert.confidence,
            message=alert.message,
            timestamp=alert.timestamp,
            snapshot=snapshot_path,
            severity=severity_for(alert.model, alert.label),
            zone=alert.zone or None,
        )
        session.add(record)
        session.commit()
        return record.id


def update_alert_clip(alert_id: int, clip_path: str):
    engine = _get_engine()
    with Session(engine) as session:
        record = session.get(AlertRecord, alert_id)
        if record is not None:
            record.clip = clip_path
            session.commit()


def cleanup_old_data(snapshot_days: int = 30, alert_days: int = 365):
    """Purge : fichiers médias > snapshot_days, alertes en base > alert_days."""
    import os

    cutoff_files = datetime.now() - timedelta(days=snapshot_days)
    for media_dir in [DATA_DIR.parent / "clips" / "snapshots", DATA_DIR.parent / "clips" / "videos"]:
        if not media_dir.exists():
            continue
        for f in media_dir.iterdir():
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < cutoff_files:
                    os.remove(f)
            except OSError:
                pass

    cutoff_alerts = datetime.now() - timedelta(days=alert_days)
    engine = _get_engine()
    with Session(engine) as session:
        old = session.scalars(select(AlertRecord).where(AlertRecord.timestamp < cutoff_alerts)).all()
        for r in old:
            session.delete(r)
        session.commit()


def read_alerts(
    limit: int = 100,
    model: str | None = None,
    camera: str | None = None,
    severity: str | None = None,
    zone: str | None = None,
    acknowledged: bool | None = None,
    since_hours: int | None = None,
) -> list[dict]:
    engine = _get_engine()
    with Session(engine) as session:
        stmt = select(AlertRecord)
        if model:
            stmt = stmt.where(AlertRecord.model == model)
        if camera:
            stmt = stmt.where(AlertRecord.camera == camera)
        if severity:
            stmt = stmt.where(AlertRecord.severity == severity)
        if zone:
            stmt = stmt.where(AlertRecord.zone == zone)
        if acknowledged is not None:
            stmt = stmt.where(AlertRecord.acknowledged == acknowledged)
        if since_hours:
            cutoff = datetime.now() - timedelta(hours=since_hours)
            stmt = stmt.where(AlertRecord.timestamp >= cutoff)
        stmt = stmt.order_by(AlertRecord.timestamp.desc()).limit(limit)
        return [_to_dict(r) for r in session.scalars(stmt).all()]


def acknowledge_alert(alert_id: int, operator: str = "opérateur") -> bool:
    engine = _get_engine()
    with Session(engine) as session:
        record = session.get(AlertRecord, alert_id)
        if record is None:
            return False
        record.acknowledged = True
        record.ack_by = operator
        record.ack_at = datetime.now()
        session.commit()
        return True


def stats_summary() -> dict:
    engine = _get_engine()
    now = datetime.now()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    with Session(engine) as session:
        total_24h = session.scalar(
            select(func.count()).select_from(AlertRecord).where(AlertRecord.timestamp >= day_ago)
        )
        total_7d = session.scalar(
            select(func.count()).select_from(AlertRecord).where(AlertRecord.timestamp >= week_ago)
        )
        unack = session.scalar(
            select(func.count()).select_from(AlertRecord).where(AlertRecord.acknowledged == False)  # noqa: E712
        )
        critique_24h = session.scalar(
            select(func.count())
            .select_from(AlertRecord)
            .where(AlertRecord.timestamp >= day_ago, AlertRecord.severity == "critique")
        )
        by_model = session.execute(
            select(AlertRecord.model, func.count())
            .where(AlertRecord.timestamp >= week_ago)
            .group_by(AlertRecord.model)
        ).all()
        by_severity = session.execute(
            select(AlertRecord.severity, func.count())
            .where(AlertRecord.timestamp >= week_ago)
            .group_by(AlertRecord.severity)
        ).all()
        by_zone = session.execute(
            select(AlertRecord.zone, func.count())
            .where(AlertRecord.timestamp >= week_ago)
            .group_by(AlertRecord.zone)
        ).all()
    return {
        "total_24h": total_24h or 0,
        "total_7d": total_7d or 0,
        "non_acquittees": unack or 0,
        "critiques_24h": critique_24h or 0,
        "par_modele_7j": {m: c for m, c in by_model},
        "par_severite_7j": {s: c for s, c in by_severity},
        "par_zone_7j": {(z or "plein cadre"): c for z, c in by_zone},
    }


def stats_timeline(hours: int = 24) -> list[dict]:
    """Nombre d'alertes par heure sur les N dernières heures."""
    engine = _get_engine()
    cutoff = datetime.now() - timedelta(hours=hours)
    with Session(engine) as session:
        rows = session.execute(
            select(AlertRecord.timestamp, AlertRecord.severity).where(AlertRecord.timestamp >= cutoff)
        ).all()

    buckets: dict[str, dict] = {}
    now = datetime.now()
    for i in range(hours, -1, -1):
        h = (now - timedelta(hours=i)).strftime("%Hh")
        buckets[h] = {"heure": h, "total": 0, "critique": 0}
    for ts, sev in rows:
        h = ts.strftime("%Hh")
        if h in buckets:
            buckets[h]["total"] += 1
            if sev == "critique":
                buckets[h]["critique"] += 1
    return list(buckets.values())


def export_csv() -> str:
    """Toutes les alertes au format CSV (pour rapport HSE)."""
    import csv
    import io

    alerts = read_alerts(limit=10000)
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        ["ID", "Horodatage", "Caméra", "Zone", "Modèle", "Détection", "Confiance", "Sévérité",
         "Acquittée", "Acquittée par", "Acquittée le"]
    )
    for a in alerts:
        writer.writerow(
            [
                a["id"],
                a["timestamp"],
                a["camera"],
                a["zone"],
                a["model"],
                a["label"],
                f"{a['confidence']:.2f}",
                a["severity"],
                "oui" if a["acknowledged"] else "non",
                a["ack_by"] or "",
                a["ack_at"] or "",
            ]
        )
    return output.getvalue()
