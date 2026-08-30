"""Tests du moteur d'alerte : ce qui alerte, ce qui est filtré, et à quel rythme."""

import pytest

from app import rules as rules_module
from app.models import Detection
from app.rules import COOLDOWN_BY_SEVERITY, AlertEngine


@pytest.fixture(autouse=True)
def alertes_activees(monkeypatch):
    """Neutralise les réglages en direct : ici on teste les règles, pas l'UI."""
    monkeypatch.setattr(rules_module, "is_alert_enabled", lambda model: True)


def detection(model="epi", label="NO-Hardhat", confidence=0.9, camera="cam1", zone=""):
    return Detection(
        camera=camera, model=model, label=label, confidence=confidence,
        bbox=(0.0, 0.0, 10.0, 10.0), zone=zone,
    )


# ── Ce qui déclenche, ou non ─────────────────────────────────────────


def test_classe_surveillee_declenche():
    assert AlertEngine().process(detection()) is not None


def test_classe_non_surveillee_ignoree():
    """Un ouvrier AVEC casque est détecté, mais ne doit rien déclencher."""
    assert AlertEngine().process(detection(label="Hardhat")) is None


def test_modele_inconnu_ignore():
    assert AlertEngine().process(detection(model="modele_inexistant")) is None


def test_alertes_desactivees_pour_ce_modele(monkeypatch):
    monkeypatch.setattr(rules_module, "is_alert_enabled", lambda model: False)
    assert AlertEngine().process(detection()) is None


def test_seuil_renforce_bloque_sous_le_minimum():
    """Fall-Detected exige 0.80 : à 0.75 le modèle n'est pas assez sûr."""
    d = detection(model="gloves_glasses", label="Fall-Detected", confidence=0.75)
    assert AlertEngine().process(d) is None


def test_seuil_renforce_laisse_passer_au_dessus():
    d = detection(model="gloves_glasses", label="Fall-Detected", confidence=0.85)
    assert AlertEngine().process(d) is not None


# ── Anti-répétition ──────────────────────────────────────────────────


def test_repetition_immediate_bloquee():
    engine = AlertEngine()
    assert engine.process(detection()) is not None
    assert engine.process(detection()) is None


def test_cooldown_par_severite():
    engine = AlertEngine()
    assert engine._cooldown_for("fire_smoke", "Fire") == COOLDOWN_BY_SEVERITY["critique"]
    assert engine._cooldown_for("epi", "NO-Hardhat") == COOLDOWN_BY_SEVERITY["haute"]
    assert engine._cooldown_for("vehicles", "car") == COOLDOWN_BY_SEVERITY["moyenne"]


def test_chute_est_critique_malgre_son_modele():
    """gloves_glasses est classé « haute », mais une personne au sol est critique."""
    assert AlertEngine()._cooldown_for("gloves_glasses", "Fall-Detected") == COOLDOWN_BY_SEVERITY["critique"]


def test_cooldown_force_prime_sur_la_severite():
    engine = AlertEngine(cooldown_seconds=5)
    assert engine._cooldown_for("fire_smoke", "Fire") == 5


def test_zones_differentes_alertent_independamment():
    """Un véhicule sur le quai et un autre devant l'atelier : deux situations."""
    engine = AlertEngine()
    assert engine.process(detection(model="vehicles", label="car", zone="quai")) is not None
    assert engine.process(detection(model="vehicles", label="car", zone="atelier")) is not None
    assert engine.process(detection(model="vehicles", label="car", zone="quai")) is None


def test_cameras_differentes_alertent_independamment():
    engine = AlertEngine()
    assert engine.process(detection(camera="cam1")) is not None
    assert engine.process(detection(camera="cam2")) is not None


def test_classes_differentes_alertent_independamment():
    engine = AlertEngine()
    assert engine.process(detection(label="NO-Hardhat")) is not None
    assert engine.process(detection(label="NO-Mask")) is not None


# ── Contenu de l'alerte ──────────────────────────────────────────────


def test_zone_reportee_dans_l_alerte():
    alert = AlertEngine().process(detection(zone="atelier"))
    assert alert.zone == "atelier"
    assert "atelier" in alert.message


def test_message_sans_zone_reste_lisible():
    alert = AlertEngine().process(detection())
    assert alert.zone == ""
    assert "cam1" in alert.message


def test_callback_recoit_alerte_et_image():
    recues = []
    engine = AlertEngine(on_alert=lambda alert, frame: recues.append((alert, frame)))
    engine.process(detection(), frame="image")
    assert len(recues) == 1
    assert recues[0][1] == "image"


# ── Bascule vers le modèle de chute dédié ────────────────────────────


def test_chute_assuree_par_gloves_glasses_par_defaut(monkeypatch):
    """Tant que le modèle dédié n'existe pas, gloves_glasses assure la chute."""
    monkeypatch.setattr(rules_module, "_chute_dediee", False)
    d = detection(model="gloves_glasses", label="Fall-Detected", confidence=0.9)
    assert AlertEngine().process(d) is not None


def test_gloves_glasses_se_tait_quand_le_modele_dedie_existe(monkeypatch):
    """Sinon une même personne au sol déclencherait deux alertes, et le
    décompte perdrait tout sens pour les opérateurs."""
    monkeypatch.setattr(rules_module, "_chute_dediee", True)
    d = detection(model="gloves_glasses", label="Fall-Detected", confidence=0.9)
    assert AlertEngine().process(d) is None


def test_modele_dedie_alerte_sur_personne_au_sol(monkeypatch):
    monkeypatch.setattr(rules_module, "_chute_dediee", True)
    assert AlertEngine().process(detection(model="fall", label="fallen")) is not None


def test_modele_dedie_alerte_pendant_la_chute(monkeypatch):
    """Secourir pendant la chute vaut mieux qu'après."""
    monkeypatch.setattr(rules_module, "_chute_dediee", True)
    assert AlertEngine().process(detection(model="fall", label="falling")) is not None


def test_personne_debout_ne_declenche_rien(monkeypatch):
    """C'est exactement ce que le modèle actuel ne sait pas faire."""
    monkeypatch.setattr(rules_module, "_chute_dediee", True)
    assert AlertEngine().process(detection(model="fall", label="standing")) is None


def test_personne_penchee_ne_declenche_rien(monkeypatch):
    """Un ouvrier qui se baisse n'est pas un ouvrier à terre."""
    monkeypatch.setattr(rules_module, "_chute_dediee", True)
    assert AlertEngine().process(detection(model="fall", label="bending")) is None


def test_chute_est_critique():
    from app.storage import severity_for
    assert severity_for("fall", "fallen") == "critique"


# ── Contrôle de sortie des camions ───────────────────────────────────


def test_bache_absente_alerte():
    assert AlertEngine().process(detection(model="load_control", label="bache_absente")) is not None


def test_surcharge_alerte():
    assert AlertEngine().process(detection(model="load_control", label="surcharge")) is not None


def test_camion_conforme_ne_declenche_rien():
    """La classe qui manque au modèle actuel : un camion en règle doit se taire."""
    assert AlertEngine().process(detection(model="load_control", label="conforme")) is None


def test_camion_non_bache_est_haute_severite():
    """Risque routier et amende : ce n'est pas une observation de routine."""
    from app.storage import severity_for
    assert severity_for("load_control", "bache_absente") == "haute"
