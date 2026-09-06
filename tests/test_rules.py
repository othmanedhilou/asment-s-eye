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


def test_classes_du_jeu_de_chute_retenu():
    """Le jeu retenu nomme les postures up / bending / down."""
    engine = AlertEngine()
    assert engine.process(detection(model="fall", label="down")) is not None
    assert engine.process(detection(model="fall", label="up")) is None
    assert engine.process(detection(model="fall", label="bending")) is None


# ── Une alerte par objet suivi, pas une par image ────────────────────


def suivi(track_id, label="NO-Hardhat", vues=5):
    """Un objet suivi et déjà confirmé, sauf indication contraire."""
    d = detection(label=label)
    d.track_id = track_id
    d.track_hits = vues
    return d


def test_un_objet_suivi_n_alerte_qu_une_seule_fois():
    """Le suivi transforme l'alerte en événement : cet ouvrier-là, sans casque,
    se signale une fois. Sans cela, il se signalerait à chaque image."""
    moteur = AlertEngine()
    assert moteur.process(suivi(7)) is not None
    for _ in range(50):
        assert moteur.process(suivi(7)) is None


def test_deux_ouvriers_au_meme_manquement_ne_font_qu_une_alerte():
    """Le compromis assumé, écrit noir sur blanc.

    Deux personnes sans casque devant la même caméra dans la même minute ne
    produisent qu'une alerte. On préférerait en produire deux — mais le suivi
    n'est pas assez fiable à basse cadence pour distinguer « deuxième ouvrier »
    de « premier ouvrier dont j'ai perdu la trace », et la seconde erreur coûte
    bien plus cher : c'est elle qui noie l'opérateur.

    Les caméras et les libellés restent indépendants (tests ci-dessous)."""
    moteur = AlertEngine()
    assert moteur.process(suivi(1)) is not None
    assert moteur.process(suivi(2)) is None


def test_deux_cameras_alertent_chacune_pour_son_compte():
    moteur = AlertEngine()
    a = suivi(1); a.camera = "portail"
    b = suivi(2); b.camera = "quai"
    assert moteur.process(a) is not None
    assert moteur.process(b) is not None


def test_sans_suivi_le_delai_anti_repetition_reste_le_seul_rempart():
    moteur = AlertEngine(cooldown_seconds=3600)
    assert moteur.process(detection()) is not None
    assert moteur.process(detection()) is None


def test_la_memoire_des_alertes_ne_grossit_pas_indefiniment():
    moteur = AlertEngine()
    moteur.MEMOIRE_MAX = 100
    for i in range(400):
        moteur.process(suivi(i))
    assert len(moteur._last_alert) <= 200


def test_une_apparition_fugace_n_alerte_pas():
    """Le defaut mesure en conditions reelles : NO-Mask clignotait a 0,42 de
    confiance et creait une piste neuve a chaque retour, donc une alerte neuve.
    Une piste doit avoir ete vue plusieurs fois pour compter."""
    moteur = AlertEngine()
    d = suivi(1, vues=1)
    assert moteur.process(d) is None
    d.track_hits = 2
    assert moteur.process(d) is None
    d.track_hits = 3
    assert moteur.process(d) is not None


def test_un_masque_absent_exige_plus_de_certitude():
    moteur = AlertEngine()
    faible = detection(model="epi", label="NO-Mask", confidence=0.45)
    faible.track_id, faible.track_hits = 1, 5
    assert moteur.process(faible) is None

    franche = detection(model="epi", label="NO-Mask", confidence=0.70)
    franche.track_id, franche.track_hits = 2, 5
    assert moteur.process(franche) is not None


# ── Le second verrou : le suivi n'est pas infaillible ────────────────


def test_un_suivi_qui_lache_ne_rouvre_pas_les_vannes():
    """Mesuré en conditions réelles : à 0,4 image par seconde, un objet qui
    bouge n'est plus rapproché du précédent et repart avec une identité neuve.
    Sans un verrou par libellé, chaque perte de piste produisait une alerte —
    sept « gilet absent » en trois minutes."""
    moteur = AlertEngine()
    assert moteur.process(suivi(1)) is not None
    for identifiant in range(2, 30):          # le suiveur perd la piste sans cesse
        assert moteur.process(suivi(identifiant)) is None


def test_le_verrou_par_libelle_finit_par_se_lever():
    moteur = AlertEngine(cooldown_seconds=0)
    assert moteur.process(suivi(1)) is not None
    assert moteur.process(suivi(2)) is not None


def test_deux_libelles_differents_ne_se_bloquent_pas():
    moteur = AlertEngine()
    assert moteur.process(suivi(1, label="NO-Hardhat")) is not None
    assert moteur.process(suivi(2, label="NO-Safety Vest")) is not None


# ── Feu et fumée : un seul événement ─────────────────────────────────


def test_feu_et_fumee_ne_font_qu_une_alerte():
    """Essai en conditions réelles : une flamme de briquet, saturée par le
    capteur en un halo blanc-gris, a été annoncée « Smoke 0,57 » — avec plus
    d'assurance que le « Fire 0,49 » de la même scène. Comme ce sont deux
    libellés, le verrou anti-répétition ne s'appliquait pas de l'un à l'autre :
    UNE flamme a produit QUATRE alertes."""
    moteur = AlertEngine()
    feu = detection(model="fire_smoke", label="Fire")
    feu.track_id, feu.track_hits = 1, 5
    premiere = moteur.process(feu)
    assert premiere is not None
    assert premiere.label == "fumée"

    from app.storage import severity_for
    assert severity_for("fire_smoke", premiere.label) == "critique"

    fumee = detection(model="fire_smoke", label="Smoke")
    fumee.track_id, fumee.track_hits = 2, 5
    assert moteur.process(fumee) is None


def test_le_message_reprend_le_libelle_fusionne():
    moteur = AlertEngine()
    d = detection(model="fire_smoke", label="Smoke")
    d.track_id, d.track_hits = 1, 5
    assert "fumée" in moteur.process(d).message


def test_la_premiere_alerte_part_meme_si_la_machine_vient_de_demarrer(monkeypatch):
    """time.monotonic() compte depuis le demarrage de la machine. Avec un
    sentinelle a 0.0, « now - 0.0 » tombait sous le delai anti-repetition tant
    que la machine avait moins de cinq minutes de vie : la premiere alerte
    etait avalee, precisement au moment ou l'on veut savoir que le systeme
    fonctionne."""
    monkeypatch.setattr(rules_module.time, "monotonic", lambda: 12.0)
    moteur = AlertEngine()
    assert moteur.process(detection()) is not None


def test_chute_est_critique():
    from app.storage import severity_for
    assert severity_for("fall", "down") == "critique"


# ── Chute : les trois postures du modèle ─────────────────────────────
#
# Le modèle entraîné ne connaît que up / bending / down. Ces tests portaient
# sur « fallen » et « falling », qui n'existent nulle part : ils passaient en
# vérifiant une table de libellés contre elle-même, sans jamais toucher au
# modèle. Chaque libellé testé ici sort réellement du .pt.


def test_modele_dedie_alerte_sur_personne_au_sol():
    assert AlertEngine().process(detection(model="fall", label="down")) is not None


def test_les_libelles_declares_existent_dans_le_modele():
    """Un libellé mal orthographié n'alerte jamais, et ne dit rien.

    C'est la panne la plus silencieuse du projet : la règle est écrite, le
    modèle tourne, et rien ne se déclenche jamais. On compare donc la table
    d'alerte aux classes réelles du fichier de poids.
    """
    from app.rules import ALERT_LABELS
    assert ALERT_LABELS["fall"] <= {"up", "bending", "down"}


def test_personne_debout_ne_declenche_rien():
    assert AlertEngine().process(detection(model="fall", label="up")) is None


def test_personne_penchee_ne_declenche_rien():
    """Un ouvrier qui se baisse n'est pas un ouvrier à terre."""
    assert AlertEngine().process(detection(model="fall", label="bending")) is None
