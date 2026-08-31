"""Tests de la lecture de plaques.

Le moteur de lecture n'est pas requis : ce qui se teste ici est la mise en
forme, le filtrage du bruit et le vote — c'est-à-dire ce qui transforme des
lectures médiocres en un numéro exploitable.
"""

import numpy as np
import pytest

from app.plates import (
    PlateReader,
    corriger_confusions,
    normaliser,
    plausible,
    regions_candidates,
)


# ── Mise en forme ────────────────────────────────────────────────────


def test_normalisation_retire_les_separateurs():
    assert normaliser("12345-A-6") == "12345A6"
    assert normaliser("12 345 | b | 7") == "12345B7"


def test_normalisation_texte_vide():
    assert normaliser("") == ""
    assert normaliser(None) == ""


def test_confusions_corrigees_aux_extremites():
    """O lu à la place de 0 : erreur classique sur une plaque."""
    assert corriger_confusions("O1234A5") == "01234A5"


def test_lettre_de_serie_preservee():
    """Les plaques marocaines sont « chiffres - lettre - chiffres » : la lettre
    centrale est significative et ne doit jamais être transformée en chiffre."""
    assert corriger_confusions("12345B6") == "12345B6"
    assert "B" in corriger_confusions("12345B6")


# ── Filtrage du bruit ────────────────────────────────────────────────


def test_plaque_plausible():
    assert plausible("12345A6") is True
    assert plausible("AB123CD") is True


def test_mot_rejete():
    """Un lecteur de texte trouve des mots partout : calandre, autocollant,
    reflet. Exiger des chiffres élimine l'essentiel du bruit."""
    assert plausible("MERCEDES") is False
    assert plausible("STOP") is False


def test_trop_court_ou_trop_long_rejete():
    assert plausible("A1") is False
    assert plausible("123456789012") is False


def test_pas_assez_de_chiffres():
    assert plausible("ABCDE1") is False


# ── Vote sur plusieurs images ────────────────────────────────────────


def test_vote_majoritaire():
    """Une image isolée se trompe ; la lecture majoritaire sur plusieurs
    images du même véhicule est fiable."""
    lecteur = PlateReader()
    for texte in ["12345A6", "12345A6", "12345A8"]:
        lecteur._votes[("cam1", 1)][texte] += 1
        lecteur._scores[("cam1", 1)].append(0.8)

    plaque = lecteur.plaque("cam1", 1)
    assert plaque["texte"] == "12345A6"
    assert plaque["lectures"] == 3
    assert plaque["accord"] == 0.67


def test_une_seule_lecture_ne_suffit_pas():
    """Sous le seuil de concordance, on ne conclut pas."""
    lecteur = PlateReader(lectures_min=2)
    lecteur._votes[("cam1", 1)]["12345A6"] += 1
    lecteur._scores[("cam1", 1)].append(0.9)
    assert lecteur.plaque("cam1", 1) is None


def test_accord_parfait():
    lecteur = PlateReader()
    for _ in range(5):
        lecteur._votes[("cam1", 1)]["12345A6"] += 1
        lecteur._scores[("cam1", 1)].append(0.9)
    assert lecteur.plaque("cam1", 1)["accord"] == 1.0


def test_vehicules_distincts_ne_se_melangent_pas():
    lecteur = PlateReader()
    for _ in range(2):
        lecteur._votes[("cam1", 1)]["11111A1"] += 1
        lecteur._votes[("cam1", 2)]["22222B2"] += 1
        lecteur._scores[("cam1", 1)].append(0.9)
        lecteur._scores[("cam1", 2)].append(0.9)
    assert lecteur.plaque("cam1", 1)["texte"] == "11111A1"
    assert lecteur.plaque("cam1", 2)["texte"] == "22222B2"


def test_cameras_distinctes_ne_se_melangent_pas():
    lecteur = PlateReader()
    for _ in range(2):
        lecteur._votes[("cam1", 1)]["11111A1"] += 1
        lecteur._votes[("cam2", 1)]["22222B2"] += 1
        lecteur._scores[("cam1", 1)].append(0.9)
        lecteur._scores[("cam2", 1)].append(0.9)
    assert lecteur.plaque("cam2", 1)["texte"] == "22222B2"


def test_oubli_d_un_vehicule():
    lecteur = PlateReader()
    for _ in range(2):
        lecteur._votes[("cam1", 1)]["12345A6"] += 1
        lecteur._scores[("cam1", 1)].append(0.9)
    lecteur.oublier("cam1", 1)
    assert lecteur.plaque("cam1", 1) is None


def test_vehicule_inconnu():
    assert PlateReader().plaque("cam1", 99) is None


# ── Localisation ─────────────────────────────────────────────────────


def test_image_trop_petite_ignoree():
    """Sous une certaine taille, aucune plaque ne peut être lisible : inutile
    de dépenser du calcul dessus."""
    assert regions_candidates(np.zeros((10, 20, 3), dtype=np.uint8)) == []


def test_image_vide_ignoree():
    assert regions_candidates(None) == []
    assert regions_candidates(np.zeros((0, 0, 3), dtype=np.uint8)) == []


def test_regions_sur_image_uniforme():
    """Une image sans contraste ne contient aucune plaque : ne rien trouver est
    le bon comportement, pas un échec."""
    assert regions_candidates(np.full((200, 300, 3), 128, dtype=np.uint8)) == []


def test_plaque_synthetique_localisee():
    """Rectangle clair et allongé dans la moitié basse : la forme même d'une plaque."""
    vehicule = np.full((240, 320, 3), 60, dtype=np.uint8)
    import cv2
    cv2.rectangle(vehicule, (90, 170), (230, 205), (235, 235, 235), -1)
    for i in range(6):   # caractères sombres, pour le contraste local
        cv2.rectangle(vehicule, (100 + i * 22, 178), (114 + i * 22, 198), (20, 20, 20), -1)

    regions = regions_candidates(vehicule)
    assert regions, "aucune région candidate trouvée sur une plaque synthétique"
    x, y, w, h = regions[0]
    assert 2.0 <= w / h <= 6.5


# ── Absence de moteur de lecture ─────────────────────────────────────


def test_sans_moteur_aucune_invention(monkeypatch):
    """Sans moteur de lecture, le système signale une plaque sans la lire —
    plutôt que d'inventer un numéro."""
    lecteur = PlateReader()
    lecteur._ocr_teste = True
    lecteur._ocr = None
    assert lecteur.lire_region(np.zeros((40, 120, 3), dtype=np.uint8)) == ("", 0.0)


def test_observation_sans_suivi():
    """Sans identifiant de suivi, pas de vote possible : on ne lit pas."""
    assert PlateReader().observer("cam1", None, np.zeros((100, 200, 3), dtype=np.uint8)) is None


# ── Rendre compte plutôt que se taire ────────────────────────────────


def test_une_plaque_trop_petite_n_est_pas_lue_mais_expliquee():
    """Mesuré sur la vidéo d'essai : la plaque du véhicule détecté faisait
    60 × 20 px. easyocr en tirait « Lotshl » à 0,04 de confiance. Le défaut
    n'était pas de rejeter cette lecture — c'était de ne rien dire, laissant
    croire à une panne du logiciel plutôt qu'à un cadrage de caméra."""
    import numpy as np

    from app.plates import LARGEUR_MIN_PLAQUE, PlateReader

    lecteur = PlateReader(asynchrone=False)
    lecteur._ocr_teste = True
    lecteur._ocr = object()                      # moteur présent, jamais appelé
    lecteur.localiser = lambda crop: [(0, 0, 60, 20)]

    lecteur._travailler("portail", 1, np.zeros((100, 200, 3), dtype=np.uint8))

    d = lecteur.diagnostic("portail")
    assert d["trop_petites"] == 1
    assert d["lectures_tentees"] == 0
    assert d["largeur_max_vue"] == 60
    assert d["largeur_requise"] == LARGEUR_MIN_PLAQUE
    assert "trop petite" in d["raison"]
    assert "60 px" in d["raison"]


def test_sans_moteur_la_raison_le_dit():
    from app.plates import PlateReader

    lecteur = PlateReader(asynchrone=False)
    lecteur._ocr_teste = True
    lecteur._ocr = None
    assert "moteur" in lecteur.raison("portail")
