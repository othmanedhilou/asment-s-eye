"""Tests de la persistance : enregistrement, filtres, acquittement, purge."""

from datetime import datetime, timedelta

import pytest

from app import storage as storage_module
from app.models import Alert
from app.storage import (
    acknowledge_alert,
    cleanup_old_data,
    export_csv,
    log_alert,
    read_alerts,
    severity_for,
    stats_summary,
)


@pytest.fixture(autouse=True)
def base_temporaire(tmp_path, monkeypatch):
    """Chaque test travaille sur une base neuve, jamais celle de production."""
    monkeypatch.setattr(storage_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(storage_module, "_engine", None)
    yield
    monkeypatch.setattr(storage_module, "_engine", None)


def alerte(model="epi", label="NO-Hardhat", camera="cam1", zone="", confidence=0.9, timestamp=None):
    return Alert(
        camera=camera, model=model, label=label, confidence=confidence,
        message=f"{label} sur {camera}", zone=zone,
        timestamp=timestamp or datetime.now(),
    )


# ── Sévérité ─────────────────────────────────────────────────────────


def test_severite_par_modele():
    assert severity_for("fire_smoke", "Fire") == "critique"
    assert severity_for("epi", "NO-Hardhat") == "haute"
    assert severity_for("vehicles", "car") == "moyenne"


def test_severite_convoyeur_haute():
    """Une bande déchirée arrête la production et met un opérateur en danger."""
    assert severity_for("conveyor", "crack") == "haute"


def test_severite_par_label_prime():
    assert severity_for("gloves_glasses", "Fall-Detected") == "critique"


def test_modele_inconnu_par_defaut_moyenne():
    assert severity_for("inconnu", "truc") == "moyenne"


# ── Enregistrement et lecture ────────────────────────────────────────


def test_enregistrement_retourne_un_identifiant():
    assert isinstance(log_alert(alerte()), int)


def test_relecture_contient_les_champs():
    log_alert(alerte(zone="atelier"), snapshot_path="/chemin/img.jpg")
    a = read_alerts()[0]
    assert a["model"] == "epi"
    assert a["zone"] == "atelier"
    assert a["severity"] == "haute"
    assert a["snapshot"] == "/chemin/img.jpg"
    assert a["acknowledged"] is False


def test_zone_absente_devient_chaine_vide():
    log_alert(alerte())
    assert read_alerts()[0]["zone"] == ""


def test_filtre_par_modele():
    log_alert(alerte(model="epi"))
    log_alert(alerte(model="fire_smoke", label="Fire"))
    assert len(read_alerts(model="fire_smoke")) == 1


def test_filtre_par_zone():
    log_alert(alerte(zone="atelier"))
    log_alert(alerte(zone="quai"))
    assert len(read_alerts(zone="quai")) == 1


def test_filtre_par_severite():
    log_alert(alerte(model="fire_smoke", label="Fire"))
    log_alert(alerte(model="epi"))
    assert len(read_alerts(severity="critique")) == 1


def test_filtre_par_periode():
    log_alert(alerte(timestamp=datetime.now() - timedelta(hours=48)))
    log_alert(alerte())
    assert len(read_alerts(since_hours=24)) == 1


def test_ordre_antichronologique():
    log_alert(alerte(label="NO-Mask", timestamp=datetime.now() - timedelta(hours=1)))
    log_alert(alerte(label="NO-Hardhat"))
    assert read_alerts()[0]["label"] == "NO-Hardhat"


# ── Acquittement ─────────────────────────────────────────────────────


def test_acquittement_trace_l_operateur():
    alert_id = log_alert(alerte())
    assert acknowledge_alert(alert_id, "chef_poste") is True
    a = read_alerts()[0]
    assert a["acknowledged"] is True
    assert a["ack_by"] == "chef_poste"
    assert a["ack_at"] is not None


def test_acquittement_alerte_inexistante():
    assert acknowledge_alert(99999, "personne") is False


def test_filtre_a_traiter():
    ack_id = log_alert(alerte())
    log_alert(alerte(label="NO-Mask"))
    acknowledge_alert(ack_id, "op")
    assert len(read_alerts(acknowledged=False)) == 1


# ── Statistiques et export ───────────────────────────────────────────


def test_statistiques():
    log_alert(alerte(model="fire_smoke", label="Fire", zone="four"))
    log_alert(alerte(model="epi", zone="atelier"))
    s = stats_summary()
    assert s["total_24h"] == 2
    assert s["critiques_24h"] == 1
    assert s["non_acquittees"] == 2
    assert s["par_modele_7j"]["epi"] == 1
    assert s["par_zone_7j"]["four"] == 1


def test_statistiques_zone_vide_libellee():
    log_alert(alerte())
    assert stats_summary()["par_zone_7j"]["plein cadre"] == 1


def test_export_csv_contient_la_zone():
    log_alert(alerte(zone="atelier"))
    csv = export_csv()
    assert "Zone" in csv.splitlines()[0]
    assert "atelier" in csv


# ── Purge ────────────────────────────────────────────────────────────


def test_purge_supprime_les_alertes_anciennes():
    log_alert(alerte(timestamp=datetime.now() - timedelta(days=400)))
    log_alert(alerte())
    cleanup_old_data(alert_days=365)
    assert len(read_alerts()) == 1


def test_purge_conserve_les_alertes_recentes():
    log_alert(alerte())
    cleanup_old_data()
    assert len(read_alerts()) == 1
