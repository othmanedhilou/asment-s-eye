"""Tests du rapprochement d'un objet vu sur plusieurs caméras.

Ce qui est vérifié ici n'est pas une identification certaine — le module ne
prétend pas en produire — mais que les rapprochements impossibles sont bien
écartés : classe différente, caméra non voisine, délai dépassé, apparence trop
éloignée. Un faux rapprochement est plus nuisible qu'une absence de
rapprochement : il raconte une histoire.
"""

import numpy as np
import pytest

from app.reid import TrackRegistry, signature, similarite


def carre(couleur, taille=64):
    """Imagette unie, en BGR."""
    return np.full((taille, taille, 3), couleur, dtype=np.uint8)


ORANGE = carre((30, 120, 240))     # gilet de sécurité
BLEU = carre((200, 90, 20))
ORANGE_BIS = carre((35, 125, 235))  # même objet, éclairage légèrement différent


# ── Signature d'apparence ────────────────────────────────────────────


def test_signature_produite():
    assert len(signature(ORANGE)) > 0


def test_image_vide_sans_signature():
    assert signature(None) == []
    assert signature(np.zeros((0, 0, 3), dtype=np.uint8)) == []


def test_meme_couleur_forte_similarite():
    assert similarite(signature(ORANGE), signature(ORANGE_BIS)) > 0.6


def test_couleurs_differentes_faible_similarite():
    assert similarite(signature(ORANGE), signature(BLEU)) < 0.4


def test_similarite_de_tailles_incompatibles():
    assert similarite([0.5, 0.5], [0.3, 0.3, 0.4]) == 0.0


# ── Rapprochement entre caméras ──────────────────────────────────────


def test_premier_objet_recoit_un_identifiant():
    registre = TrackRegistry()
    r = registre.observer("cam1", 1, "person", ORANGE)
    assert r["global_id"] == 1
    assert r["correspondance"] is None


def test_meme_objet_retrouve_sur_une_autre_camera():
    """Un ouvrier en gilet orange quitte une caméra et entre dans une autre."""
    registre = TrackRegistry()
    depart = registre.observer("cam1", 1, "person", ORANGE)
    arrivee = registre.observer("cam2", 7, "person", ORANGE_BIS)

    assert arrivee["global_id"] == depart["global_id"]
    assert arrivee["correspondance"]["de"] == "cam1"
    assert arrivee["correspondance"]["vers"] == "cam2"
    assert arrivee["correspondance"]["certain"] is False   # jamais une certitude


def test_apparence_trop_differente_non_rapprochee():
    registre = TrackRegistry()
    a = registre.observer("cam1", 1, "person", ORANGE)
    b = registre.observer("cam2", 2, "person", BLEU)
    assert b["global_id"] != a["global_id"]


def test_classes_differentes_jamais_rapprochees():
    """Une personne ne devient pas un camion, quelle que soit la couleur."""
    registre = TrackRegistry()
    a = registre.observer("cam1", 1, "person", ORANGE)
    b = registre.observer("cam2", 2, "truck", ORANGE)
    assert b["global_id"] != a["global_id"]


def test_meme_camera_pas_de_rapprochement():
    """Sur une même caméra, c'est le suivi qui fait le travail."""
    registre = TrackRegistry()
    registre.observer("cam1", 1, "person", ORANGE)
    b = registre.observer("cam1", 2, "person", ORANGE_BIS)
    assert b["correspondance"] is None


def test_delai_depasse():
    """Un objet ne réapparaît pas trois heures plus tard à l'autre bout du site."""
    registre = TrackRegistry(fenetre=0.0)
    a = registre.observer("cam1", 1, "person", ORANGE)
    b = registre.observer("cam2", 2, "person", ORANGE_BIS)
    assert b["global_id"] != a["global_id"]


def test_objet_revu_sur_sa_camera_rafraichi():
    registre = TrackRegistry()
    a = registre.observer("cam1", 1, "person", ORANGE)
    encore = registre.observer("cam1", 1, "person", ORANGE)
    assert encore["global_id"] == a["global_id"]
    assert encore["correspondance"] is None


# ── Topologie des caméras ────────────────────────────────────────────


def test_cameras_non_voisines_ecartees():
    """Deux caméras aux extrémités du site ne peuvent pas se passer un objet."""
    registre = TrackRegistry(voisins={"cam1": ["cam2"], "cam3": ["cam4"]})
    a = registre.observer("cam1", 1, "person", ORANGE)
    b = registre.observer("cam4", 2, "person", ORANGE_BIS)
    assert b["global_id"] != a["global_id"]


def test_cameras_voisines_rapprochees():
    registre = TrackRegistry(voisins={"cam1": ["cam2"]})
    a = registre.observer("cam1", 1, "person", ORANGE)
    b = registre.observer("cam2", 2, "person", ORANGE_BIS)
    assert b["global_id"] == a["global_id"]


def test_voisinage_symetrique():
    """Déclarer cam1 voisine de cam2 suffit : le passage va dans les deux sens."""
    registre = TrackRegistry(voisins={"cam1": ["cam2"]})
    a = registre.observer("cam2", 1, "person", ORANGE)
    b = registre.observer("cam1", 2, "person", ORANGE_BIS)
    assert b["global_id"] == a["global_id"]


# ── La plaque tranche ────────────────────────────────────────────────


def test_plaque_identique_prime_sur_l_apparence():
    """Deux camions de couleurs différentes à l'image, même numéro : c'est le
    même véhicule. La plaque est un identifiant, l'apparence une ressemblance."""
    registre = TrackRegistry()
    a = registre.observer("cam1", 1, "truck", ORANGE, plaque="12345A6")
    b = registre.observer("cam2", 2, "truck", BLEU, plaque="12345A6")

    assert b["global_id"] == a["global_id"]
    assert b["correspondance"]["certain"] is True
    assert b["correspondance"]["score"] == 1.0


def test_plaques_differentes_pas_de_rapprochement_force():
    """Deux camions blancs identiques mais de numéros différents restent deux
    véhicules — c'est exactement le cas que l'apparence seule confondrait."""
    registre = TrackRegistry()
    a = registre.observer("cam1", 1, "truck", ORANGE, plaque="11111A1")
    b = registre.observer("cam2", 2, "truck", ORANGE_BIS, plaque="22222B2")
    # L'apparence peut encore les rapprocher, mais alors sans certitude.
    if b["global_id"] == a["global_id"]:
        assert b["correspondance"]["certain"] is False


# ── Trajet et journal ────────────────────────────────────────────────


def test_trajet_reconstitue():
    registre = TrackRegistry()
    a = registre.observer("cam1", 1, "person", ORANGE)
    registre.observer("cam2", 2, "person", ORANGE_BIS)
    registre.observer("cam3", 3, "person", ORANGE)
    assert registre.trajet(a["global_id"]) == ["cam1", "cam2", "cam3"]


def test_trajet_inconnu():
    assert TrackRegistry().trajet(999) == []


def test_correspondances_recentes_en_premier():
    registre = TrackRegistry()
    registre.observer("cam1", 1, "person", ORANGE)
    registre.observer("cam2", 2, "person", ORANGE_BIS)
    registre.observer("cam3", 3, "person", ORANGE)
    recentes = registre.recentes()
    assert recentes[0]["vers"] == "cam3"


def test_compte_des_objets_suivis():
    registre = TrackRegistry()
    registre.observer("cam1", 1, "person", ORANGE)
    registre.observer("cam2", 2, "truck", BLEU)
    assert registre.objets_suivis == 2
