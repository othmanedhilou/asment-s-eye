"""Tests du contrôle de bâchage par absence de détection.

L'enjeu de ces tests n'est pas de vérifier qu'on détecte les camions non bâchés
— c'est facile. C'est de vérifier qu'on **ne les invente pas** : un camion trop
lointain, une bâche manquée sur une seule image, une bâche appartenant au camion
voisin. Chacun de ces cas produirait une accusation fausse.
"""

from app.bachage import ControleBachage, recouvrement
from app.models import Detection

LARGEUR, HAUTEUR = 1280, 720
AIRE = LARGEUR * HAUTEUR


def camion(track_id=1, bbox=None, label="truk_normal", plaque=None):
    # Par défaut, un camion occupant ~14 % de l'image : largement visible.
    bbox = bbox or (300.0, 200.0, 800.0, 560.0)
    return Detection(camera="portail", model="load_control", label=label,
                     confidence=0.9, bbox=bbox, track_id=track_id, plaque=plaque)


def bache(bbox=(350.0, 220.0, 750.0, 380.0)):
    return Detection(camera="portail", model="load_control", label="tarp",
                     confidence=0.8, bbox=bbox, track_id=99)


def analyser(controle, detections, fois=1):
    constats = []
    for _ in range(fois):
        constats = controle.analyser(detections, LARGEUR, HAUTEUR)
    return constats


# ── Recouvrement ─────────────────────────────────────────────────────


def test_bache_entierement_dans_le_camion():
    assert recouvrement((10, 10, 20, 20), (0, 0, 100, 100)) == 1.0


def test_bache_hors_du_camion():
    assert recouvrement((200, 200, 250, 250), (0, 0, 100, 100)) == 0.0


def test_recouvrement_partiel():
    assert 0 < recouvrement((90, 90, 110, 110), (0, 0, 100, 100)) < 1


# ── Le cas nominal ───────────────────────────────────────────────────


def test_camion_bache_ne_declenche_rien():
    controle = ControleBachage("portail")
    assert analyser(controle, [camion(), bache()], fois=5) == []


def test_camion_sans_bache_signale_apres_confirmation():
    controle = ControleBachage("portail", images_min=3)
    constats = analyser(controle, [camion()], fois=3)
    assert len(constats) == 1
    assert constats[0].label == "bache_absente"


def test_une_seule_image_ne_suffit_pas():
    """Une bâche manquée sur une image ne prouve rien."""
    controle = ControleBachage("portail", images_min=3)
    assert analyser(controle, [camion()], fois=1) == []
    assert analyser(controle, [camion()], fois=1) == []


def test_pas_de_signalement_repete():
    """Un camion arrêté au portail ne doit pas produire une alerte par image."""
    controle = ControleBachage("portail", images_min=2)
    premiers = analyser(controle, [camion()], fois=2)
    suivants = analyser(controle, [camion()], fois=10)
    assert len(premiers) == 1
    assert suivants == []


# ── Ce qu'on refuse d'affirmer ───────────────────────────────────────


def test_camion_trop_lointain_ignore():
    """Sous 3 % de l'image, on ne peut rien dire de son chargement."""
    controle = ControleBachage("portail")
    minuscule = camion(bbox=(10.0, 10.0, 90.0, 60.0))   # ~0,5 % de l'image
    assert analyser(controle, [minuscule], fois=10) == []


def test_camion_sans_suivi_ignore():
    """Sans identifiant de suivi, impossible de confirmer sur plusieurs images."""
    controle = ControleBachage("portail")
    assert analyser(controle, [camion(track_id=None)], fois=10) == []


def test_bache_du_camion_voisin_ne_compte_pas():
    """Une bâche détectée à côté ne couvre pas ce camion-ci."""
    controle = ControleBachage("portail", images_min=2)
    voisine = bache(bbox=(900.0, 220.0, 1200.0, 380.0))
    constats = analyser(controle, [camion(), voisine], fois=2)
    assert len(constats) == 1


def test_bache_effleurant_le_camion_ne_compte_pas():
    """Un chevauchement marginal n'est pas une couverture."""
    controle = ControleBachage("portail", images_min=2, recouvrement_min=0.15)
    effleure = bache(bbox=(780.0, 540.0, 900.0, 700.0))
    assert len(analyser(controle, [camion(), effleure], fois=2)) == 1


def test_decompte_remis_a_zero_si_la_bache_reapparait():
    """Deux images sans bâche puis une avec : la bâche était simplement
    masquée, pas absente."""
    controle = ControleBachage("portail", images_min=3)
    analyser(controle, [camion()], fois=2)
    analyser(controle, [camion(), bache()], fois=1)
    assert analyser(controle, [camion()], fois=2) == []


def test_camion_qui_s_eloigne_remet_le_decompte_a_zero():
    controle = ControleBachage("portail", images_min=3)
    analyser(controle, [camion()], fois=2)
    analyser(controle, [camion(bbox=(10.0, 10.0, 90.0, 60.0))], fois=1)   # trop loin
    assert analyser(controle, [camion()], fois=2) == []


# ── Contenu du constat ───────────────────────────────────────────────


def test_la_plaque_est_reportee():
    """C'est ce qui rend le constat opposable : quel camion, exactement."""
    controle = ControleBachage("portail", images_min=2)
    constats = analyser(controle, [camion(plaque="12345A6")], fois=2)
    assert constats[0].plaque == "12345A6"


def test_camions_distincts_traites_separement():
    controle = ControleBachage("portail", images_min=2)
    bache_du_premier = bache(bbox=(310.0, 210.0, 790.0, 400.0))
    detections = [camion(track_id=1),
                  camion(track_id=2, bbox=(850.0, 200.0, 1250.0, 560.0)),
                  bache_du_premier]
    constats = analyser(controle, detections, fois=2)
    assert len(constats) == 1
    assert constats[0].track_id == 2


def test_aucun_camion_aucun_constat():
    assert ControleBachage("portail").analyser([bache()], LARGEUR, HAUTEUR) == []


def test_image_degeneree_sans_effet():
    assert ControleBachage("portail").analyser([camion()], 0, 0) == []
