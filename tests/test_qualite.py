"""Tests du retour opérateur et des indicateurs de qualité.

C'est la boucle qui permet au système de savoir quand il s'est trompé : sans
elle, un modèle refait indéfiniment la même erreur.
"""

from datetime import datetime, timedelta

import pytest

from app import storage as storage_module
from app.models import Alert
from app.storage import (
    acknowledge_alert,
    count_alerts,
    export_csv,
    log_alert,
    mark_false_positive,
    quality_stats,
    read_alerts,
)


@pytest.fixture(autouse=True)
def base_temporaire(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(storage_module, "_engine", None)
    yield
    monkeypatch.setattr(storage_module, "_engine", None)


def alerte(model="epi", label="NO-Hardhat", camera="cam1", zone="", timestamp=None):
    return Alert(camera=camera, model=model, label=label, confidence=0.9,
                 message=f"{label} sur {camera}", zone=zone,
                 timestamp=timestamp or datetime.now())


# ── Marquage des fausses alertes ─────────────────────────────────────


def test_marquage_fausse_alerte():
    alert_id = log_alert(alerte())
    assert mark_false_positive(alert_id) is True
    assert read_alerts()[0]["false_positive"] is True


def test_fausse_alerte_vaut_prise_en_charge():
    """L'opérateur a traité l'événement : il a conclu que le système se trompait."""
    alert_id = log_alert(alerte())
    mark_false_positive(alert_id, operator="chef_poste")
    a = read_alerts()[0]
    assert a["acknowledged"] is True
    assert a["ack_by"] == "chef_poste"


def test_retour_en_arriere_possible():
    alert_id = log_alert(alerte())
    mark_false_positive(alert_id, True)
    mark_false_positive(alert_id, False)
    assert read_alerts()[0]["false_positive"] is False


def test_marquage_conserve_l_acquittement_existant():
    alert_id = log_alert(alerte())
    acknowledge_alert(alert_id, "premier")
    mark_false_positive(alert_id, operator="second")
    assert read_alerts()[0]["ack_by"] == "premier"


def test_marquage_alerte_inexistante():
    assert mark_false_positive(9999) is False


def test_filtre_sur_les_fausses_alertes():
    faux = log_alert(alerte())
    log_alert(alerte(label="NO-Mask"))
    mark_false_positive(faux)
    assert len(read_alerts(false_positive=True)) == 1
    assert len(read_alerts(false_positive=False)) == 1


# ── Indicateurs de qualité ───────────────────────────────────────────


def test_taux_de_fausses_alertes_par_modele():
    for _ in range(3):
        log_alert(alerte(model="load_control", label="empty"))
    faux = log_alert(alerte(model="load_control", label="empty"))
    mark_false_positive(faux)

    stats = quality_stats()
    assert stats["par_modele"]["load_control"]["alertes"] == 4
    assert stats["par_modele"]["load_control"]["fausses"] == 1
    assert stats["par_modele"]["load_control"]["taux_faux"] == 0.25


def test_fausses_alertes_par_jour_et_par_camera():
    """L'objectif du cahier des charges se mesure ainsi : moins de 2 par jour."""
    for _ in range(6):
        faux = log_alert(alerte(camera="quai"))
        mark_false_positive(faux)

    stats = quality_stats(days=3)
    assert stats["par_camera"]["quai"]["fausses_par_jour"] == 2.0


def test_delai_moyen_de_prise_en_charge():
    """Indicateur HSE : les alertes atteignent-elles réellement quelqu'un ?"""
    engine = storage_module._get_engine()
    from sqlalchemy.orm import Session

    alert_id = log_alert(alerte(timestamp=datetime.now() - timedelta(minutes=10)))
    acknowledge_alert(alert_id, "op")
    with Session(engine) as session:
        record = session.get(storage_module.AlertRecord, alert_id)
        record.ack_at = record.timestamp + timedelta(minutes=5)
        session.commit()

    stats = quality_stats()
    assert stats["delai_prise_en_charge_s"] == 300
    assert stats["alertes_traitees"] == 1


def test_delai_absent_si_rien_n_est_traite():
    log_alert(alerte())
    assert quality_stats()["delai_prise_en_charge_s"] is None


# ── Recherche avancée et pagination ──────────────────────────────────


def test_pagination():
    for i in range(10):
        log_alert(alerte(label=f"classe{i}"))
    page1 = read_alerts(limit=4, offset=0)
    page2 = read_alerts(limit=4, offset=4)
    assert len(page1) == 4 and len(page2) == 4
    assert {a["id"] for a in page1}.isdisjoint({a["id"] for a in page2})


def test_comptage_total_pour_la_pagination():
    for _ in range(7):
        log_alert(alerte())
    log_alert(alerte(model="vehicles", label="car"))
    assert count_alerts() == 8
    assert count_alerts(model="epi") == 7


def test_recherche_par_classe():
    log_alert(alerte(label="NO-Hardhat"))
    log_alert(alerte(label="NO-Mask"))
    assert len(read_alerts(label="Hardhat")) == 1


def test_filtre_par_plage_horaire():
    """« Toutes les alertes EPI entre 22 h et 6 h » — question d'exploitation."""
    minuit = datetime.now().replace(hour=1, minute=0, second=0, microsecond=0)
    midi = datetime.now().replace(hour=13, minute=0, second=0, microsecond=0)
    log_alert(alerte(timestamp=minuit))
    log_alert(alerte(timestamp=midi))

    de_nuit = read_alerts(hour_from=22, hour_to=6)   # à cheval sur minuit
    de_jour = read_alerts(hour_from=8, hour_to=18)
    assert len(de_nuit) == 1
    assert len(de_jour) == 1


def test_export_csv_signale_les_fausses_alertes():
    faux = log_alert(alerte())
    mark_false_positive(faux)
    csv = export_csv()
    assert "Fausse alerte" in csv.splitlines()[0]
