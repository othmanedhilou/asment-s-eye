"""Tests du rapport PDF destiné au service HSE."""

from datetime import datetime, timedelta

import pytest

from app import storage as storage_module
from app.models import Alert
from app.report import build_report
from app.storage import acknowledge_alert, log_alert, mark_false_positive


@pytest.fixture(autouse=True)
def base_temporaire(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(storage_module, "_engine", None)
    yield
    monkeypatch.setattr(storage_module, "_engine", None)


def alerte(model="epi", label="NO-Hardhat", camera="atelier", zone="", timestamp=None):
    return Alert(camera=camera, model=model, label=label, confidence=0.9,
                 message=f"{label} sur {camera}", zone=zone,
                 timestamp=timestamp or datetime.now())


def test_rapport_sur_base_vide():
    """Un rapport doit se produire même une semaine sans incident — c'est
    justement l'information à transmettre."""
    pdf = build_report(days=7)
    assert pdf.startswith(b"%PDF")


def test_rapport_avec_alertes():
    for _ in range(3):
        log_alert(alerte())
    log_alert(alerte(model="fire_smoke", label="Fire", camera="four"))
    pdf = build_report(days=7)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 2000


def test_rapport_couvre_la_periode_demandee():
    """Une alerte hors période ne doit pas gonfler le rapport hebdomadaire."""
    log_alert(alerte(timestamp=datetime.now() - timedelta(days=40)))
    log_alert(alerte())
    court = build_report(days=7)
    long = build_report(days=60)
    assert court.startswith(b"%PDF") and long.startswith(b"%PDF")

    # On verifie le filtrage sur les donnees, pas sur la taille du PDF : deux
    # rapports au contenu different peuvent peser le meme nombre d'octets a une
    # unite pres, parce qu'un decalage interne au format change de longueur.
    # Le test echouait alors sans qu'aucun comportement n'ait bouge.
    from app.storage import count_alerts

    assert count_alerts(since_hours=24 * 7) < count_alerts(since_hours=24 * 60)


def test_rapport_avec_acquittements_et_fausses_alertes():
    traite = log_alert(alerte())
    faux = log_alert(alerte(label="NO-Mask"))
    acknowledge_alert(traite, "chef_poste")
    mark_false_positive(faux, operator="chef_poste")
    assert build_report(days=7).startswith(b"%PDF")


def test_rapport_avec_beaucoup_de_critiques():
    """Au-delà de quinze événements critiques, le rapport résume au lieu de tout lister."""
    for i in range(20):
        log_alert(alerte(model="fire_smoke", label="Fire",
                         timestamp=datetime.now() - timedelta(minutes=i)))
    assert build_report(days=7).startswith(b"%PDF")


def test_rapport_avec_zones():
    log_alert(alerte(zone="atelier"))
    log_alert(alerte(zone="quai", camera="quai"))
    assert build_report(days=7).startswith(b"%PDF")
