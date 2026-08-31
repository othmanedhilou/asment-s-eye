from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.models import Alert

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "ciments_eye.db"

# Sévérité métier par modèle (logique HSE site cimentier)
SEVERITY_BY_MODEL = {
    "fire_smoke": "critique",
    "fall": "critique",         # personne au sol : secours immédiat
    "arc": "critique",
    "conveyor": "haute",       # bande déchirée : arrêt production + risque opérateur
    "gloves_glasses": "haute",   # inclut Fall-Detected
    "epi": "haute",
    "load_control": "haute",     # camion non bâché : risque routier, et amende
    "person_animal": "moyenne",
    "vehicles": "moyenne",
    # Incidents techniques (caméra hors ligne, disque plein). Sévérité distincte
    # pour ne pas polluer les statistiques HSE : une caméra débranchée n'est pas
    # un événement de sécurité au travail, mais doit alerter tout autant.
    "systeme": "technique",
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
    # Retour de l'opérateur : cette alerte était-elle fausse ? C'est à la fois
    # l'indicateur de qualité du système et la source d'images d'entraînement
    # étiquetées — un modèle ne progresse pas sans savoir quand il s'est trompé.
    false_positive: Mapped[bool] = mapped_column(Boolean, default=False)
    # "x1,y1,x2,y2,largeur,hauteur" en pixels : sert de pre-annotation lors de
    # l'export du jeu de donnees pour le re-entrainement.
    bbox: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Numéro de plaque lu, quand la caméra le permet. Conservé parce que c'est
    # la donnée qu'on recherchera après un incident impliquant un véhicule.
    plaque: Mapped[str | None] = mapped_column(String(20), nullable=True)


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
            "false_positive": "ALTER TABLE alerts ADD COLUMN false_positive BOOLEAN DEFAULT 0",
            "bbox": "ALTER TABLE alerts ADD COLUMN bbox VARCHAR(120)",
            "plaque": "ALTER TABLE alerts ADD COLUMN plaque VARCHAR(20)",
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
        "false_positive": bool(r.false_positive),
        "bbox": [float(v) for v in r.bbox.split(",")] if r.bbox else None,
        "plaque": r.plaque,
    }


def _bbox_to_text(alert: Alert) -> str | None:
    if not alert.bbox or not alert.frame_size:
        return None
    return ",".join(f"{v:.1f}" for v in (*alert.bbox, *alert.frame_size))


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
            bbox=_bbox_to_text(alert),
            plaque=alert.plaque,
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


def _filtrer(stmt, model=None, camera=None, severity=None, zone=None,
             acknowledged=None, false_positive=None, since_hours=None,
             label=None, plaque=None, hour_from=None, hour_to=None, **_ignores):
    """Applique les criteres de l'historique a une requete.

    Ecrit une seule fois et partage par la lecture, le comptage et la
    suppression : c'est la seule facon de garantir que le bouton « supprimer »
    efface exactement ce que la liste affiche.
    """
    if model:
        stmt = stmt.where(AlertRecord.model == model)
    if camera:
        stmt = stmt.where(AlertRecord.camera == camera)
    if severity:
        stmt = stmt.where(AlertRecord.severity == severity)
    if zone:
        stmt = stmt.where(AlertRecord.zone == zone)
    if false_positive is not None:
        stmt = stmt.where(AlertRecord.false_positive == false_positive)
    if acknowledged is not None:
        stmt = stmt.where(AlertRecord.acknowledged == acknowledged)
    if since_hours:
        cutoff = datetime.now() - timedelta(hours=since_hours)
        stmt = stmt.where(AlertRecord.timestamp >= cutoff)
    if label:
        stmt = stmt.where(AlertRecord.label.like(f"%{label}%"))
    if plaque:
        # Recherche partielle : apres un incident, on se souvient souvent de
        # quelques caracteres, rarement du numero complet.
        stmt = stmt.where(AlertRecord.plaque.like(f"%{plaque.upper()}%"))
    # Plage horaire : « toutes les alertes EPI entre 22 h et 6 h » est une
    # question d'exploitation courante, impossible a poser sans cela.
    if hour_from is not None and hour_to is not None:
        heure = func.cast(func.strftime("%H", AlertRecord.timestamp), Integer)
        if hour_from <= hour_to:
            stmt = stmt.where(heure >= hour_from, heure < hour_to)
        else:
            stmt = stmt.where((heure >= hour_from) | (heure < hour_to))
    return stmt


def _effacer_media(chemin: str | None) -> bool:
    """Efface une capture ou un clip, si le chemin reste dans le dossier prevu.

    La verification n'est pas de la mefiance envers l'appelant : les chemins
    viennent de la base, donc d'anciennes versions du logiciel, et un chemin
    absolu herite d'une autre installation ne doit pas faire supprimer un
    fichier au hasard sur le disque.
    """
    if not chemin:
        return False
    racine = (DATA_DIR.parent / "clips").resolve()
    try:
        p = Path(chemin).resolve()
        p.relative_to(racine)
    except (ValueError, OSError):
        return False
    try:
        p.unlink()
        return True
    except OSError:
        return False


def delete_alerts(**filtres) -> dict:
    """Efface les alertes correspondant aux criteres, captures et clips compris.

    Les fichiers partent avec les lignes : garder des videos orphelines
    remplirait le disque sans que rien dans l'interface ne les montre. Un clip
    partage par plusieurs alertes n'est efface qu'une fois.
    """
    engine = _get_engine()
    supprimees = fichiers = 0
    with Session(engine) as session:
        lignes = session.scalars(_filtrer(select(AlertRecord), **filtres)).all()
        vus: set[str] = set()
        for r in lignes:
            for chemin in (r.snapshot, r.clip):
                if chemin and chemin not in vus:
                    vus.add(chemin)
                    fichiers += _effacer_media(chemin)
            session.delete(r)
            supprimees += 1
        session.commit()
    return {"supprimees": supprimees, "fichiers": fichiers}


def delete_alert(alert_id: int) -> bool:
    engine = _get_engine()
    with Session(engine) as session:
        r = session.get(AlertRecord, alert_id)
        if r is None:
            return False
        _effacer_media(r.snapshot)
        _effacer_media(r.clip)
        session.delete(r)
        session.commit()
        return True


def read_alerts(
    limit: int = 100,
    model: str | None = None,
    camera: str | None = None,
    severity: str | None = None,
    zone: str | None = None,
    acknowledged: bool | None = None,
    false_positive: bool | None = None,
    since_hours: int | None = None,
    offset: int = 0,
    label: str | None = None,
    plaque: str | None = None,
    hour_from: int | None = None,
    hour_to: int | None = None,
) -> list[dict]:
    engine = _get_engine()
    with Session(engine) as session:
        stmt = _filtrer(
            select(AlertRecord), model=model, camera=camera, severity=severity,
            zone=zone, acknowledged=acknowledged, false_positive=false_positive,
            since_hours=since_hours, label=label, plaque=plaque,
            hour_from=hour_from, hour_to=hour_to)
        stmt = stmt.order_by(AlertRecord.timestamp.desc()).offset(offset).limit(limit)
        return [_to_dict(r) for r in session.scalars(stmt).all()]


def count_alerts(**filters) -> int:
    """Nombre total d'alertes correspondant aux filtres, pour la pagination."""
    engine = _get_engine()
    with Session(engine) as session:
        stmt = _filtrer(select(func.count()).select_from(AlertRecord), **filters)
        return session.scalar(stmt) or 0


def alerts_for_day(camera: str | None, day: str) -> list[dict]:
    """Alertes d'une journée pour la frise chronologique.

    Parcourir une journée d'un coup d'œil et sauter d'une alerte à l'autre est
    le geste de base d'un opérateur de vidéosurveillance : après un incident, on
    remonte le temps. Une simple liste paginée ne le permet pas.
    """
    debut = datetime.strptime(day, "%Y-%m-%d")
    fin = debut + timedelta(days=1)

    engine = _get_engine()
    with Session(engine) as session:
        stmt = select(AlertRecord).where(
            AlertRecord.timestamp >= debut, AlertRecord.timestamp < fin)
        if camera:
            stmt = stmt.where(AlertRecord.camera == camera)
        stmt = stmt.order_by(AlertRecord.timestamp)
        records = session.scalars(stmt).all()

    return [{
        "id": r.id,
        "camera": r.camera,
        "label": r.label,
        "model": r.model,
        "severity": r.severity,
        "zone": r.zone or "",
        "timestamp": r.timestamp.isoformat(),
        # Position sur la journée, de 0 à 1 : la frise n'a pas à refaire ce calcul.
        "position": round((r.timestamp - debut).total_seconds() / 86400, 5),
        "acknowledged": bool(r.acknowledged),
        "false_positive": bool(r.false_positive),
        "snapshot": r.snapshot,
        "clip": r.clip,
    } for r in records]


def days_with_alerts(camera: str | None = None, limit: int = 30) -> list[str]:
    """Journées ayant produit des alertes, la plus récente d'abord.

    Évite de proposer un calendrier où la plupart des dates sont vides.
    """
    engine = _get_engine()
    with Session(engine) as session:
        stmt = select(func.strftime("%Y-%m-%d", AlertRecord.timestamp)).distinct()
        if camera:
            stmt = stmt.where(AlertRecord.camera == camera)
        jours = session.scalars(stmt).all()
    return sorted((j for j in jours if j), reverse=True)[:limit]


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


def mark_false_positive(alert_id: int, is_false: bool = True,
                        operator: str = "opérateur") -> bool:
    """Déclare une alerte fausse (ou revient sur ce jugement).

    Marquer une fausse alerte vaut prise en charge : l'opérateur a bien traité
    l'événement, il a seulement conclu que le système s'était trompé.
    """
    engine = _get_engine()
    with Session(engine) as session:
        record = session.get(AlertRecord, alert_id)
        if record is None:
            return False
        record.false_positive = is_false
        if is_false and not record.acknowledged:
            record.acknowledged = True
            record.ack_by = operator
            record.ack_at = datetime.now()
        session.commit()
        return True


def quality_stats(days: int = 30) -> dict:
    """Qualité de détection telle que les opérateurs la constatent.

    C'est ce qui permet de répondre à l'objectif du cahier des charges — moins
    de 2 fausses alertes par jour et par caméra — avec un chiffre plutôt qu'une
    impression.
    """
    engine = _get_engine()
    cutoff = datetime.now() - timedelta(days=days)
    with Session(engine) as session:
        rows = session.execute(
            select(AlertRecord.model,
                   func.count(),
                   func.sum(func.cast(AlertRecord.false_positive, Integer)))
            .where(AlertRecord.timestamp >= cutoff)
            .group_by(AlertRecord.model)
        ).all()

        par_camera = session.execute(
            select(AlertRecord.camera,
                   func.count(),
                   func.sum(func.cast(AlertRecord.false_positive, Integer)))
            .where(AlertRecord.timestamp >= cutoff)
            .group_by(AlertRecord.camera)
        ).all()

        # Délai moyen de prise en charge : indicateur HSE classique, il dit si
        # les alertes atteignent réellement quelqu'un.
        ack_rows = session.execute(
            select(AlertRecord.timestamp, AlertRecord.ack_at)
            .where(AlertRecord.timestamp >= cutoff, AlertRecord.acknowledged == True)  # noqa: E712
        ).all()

    delais = [(ack - ts).total_seconds() for ts, ack in ack_rows if ack and ack > ts]
    jours = max(days, 1)

    return {
        "periode_jours": days,
        "par_modele": {
            model: {
                "alertes": total,
                "fausses": int(fausses or 0),
                "taux_faux": round((fausses or 0) / total, 3) if total else 0.0,
            }
            for model, total, fausses in rows
        },
        "par_camera": {
            camera: {
                "alertes": total,
                "fausses": int(fausses or 0),
                "fausses_par_jour": round((fausses or 0) / jours, 2),
            }
            for camera, total, fausses in par_camera
        },
        "delai_prise_en_charge_s": round(sum(delais) / len(delais)) if delais else None,
        "alertes_traitees": len(delais),
    }


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
         "Acquittée", "Acquittée par", "Acquittée le", "Fausse alerte"]
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
                "oui" if a["false_positive"] else "non",
            ]
        )
    return output.getvalue()
