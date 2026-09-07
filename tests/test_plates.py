"""Tests de la lecture de plaques.

Le moteur de lecture n'est pas requis : ce qui se teste ici est la mise en
forme, le filtrage du bruit et le vote — c'est-à-dire ce qui transforme des
lectures médiocres en un numéro exploitable.
"""

from pathlib import Path

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


def test_une_plaque_deja_cadree_est_lue_sans_relocalisation():
    """Le modele `plate` cadre serre ; refaire une localisation par traitement
    d'image dessus ne ferait que degrader ce qu'il a trouve.

    Avant ce correctif, la lecture ne se declenchait que sur une detection du
    modele `vehicles` : une camera qui ne le faisait pas tourner ne lisait
    aucune plaque, sans que rien ne le dise.
    """
    import numpy as np

    from app.plates import PlateReader

    lecteur = PlateReader(asynchrone=False)
    lecteur._ocr_teste = True
    lecteur._ocr = object()
    lecteur.lire_region = lambda img: ("12345A6", 0.9)

    zone = np.zeros((40, 200, 3), dtype=np.uint8)      # 200 px : au-dessus du minimum
    for _ in range(3):
        lecteur.observer_plaque("portail", 1, zone)

    assert lecteur.diagnostic("portail")["lectures_abouties"] >= 2
    assert lecteur.plaque("portail", 1)["texte"] == "12345A6"


def test_une_plaque_cadree_trop_petite_est_comptee_sans_etre_lue():
    import numpy as np

    from app.plates import PlateReader

    lecteur = PlateReader(asynchrone=False)
    lecteur._ocr_teste = True
    lecteur._ocr = object()
    lecteur.lire_region = lambda img: ("NE-DOIT-PAS-ETRE-APPELE", 1.0)

    lecteur.observer_plaque("portail", 1, np.zeros((20, 60, 3), dtype=np.uint8))
    d = lecteur.diagnostic("portail")
    assert d["trop_petites"] == 1 and d["lectures_tentees"] == 0
    assert "trop petite" in d["raison"]


# ── Une plaque se lit en entier ──────────────────────────────────────


class _OcrFactice:
    """Rend des boîtes comme easyocr : (polygone, texte, score)."""

    def __init__(self, boites):
        self.boites = boites

    def readtext(self, image, **kw):
        return self.boites


def _boite(x, y, w=40, h=18):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def test_les_trois_groupes_d_une_plaque_sont_assembles():
    """Une plaque marocaine se lit en trois groupes : le numéro de série, la
    lettre, le numéro de région. easyocr en fait trois boîtes distinctes — et
    ne garder que la mieux notée ne rendait que le premier groupe.
    """
    from app.plates import PlateReader

    lecteur = PlateReader(asynchrone=False)
    lecteur._ocr_teste = True
    lecteur._ocr = _OcrFactice([
        (_boite(120, 10, 20), "6", 0.88),      # région, à droite
        (_boite(10, 10, 60), "12345", 0.95),   # série, à gauche
        (_boite(85, 10, 22), "A", 0.91),       # lettre, au milieu
    ])

    texte, score = lecteur.lire_region(_image(100, 260))
    assert texte == "12345A6", f"lu {texte!r}"
    # La confiance est celle du maillon le plus faible : un groupe mal lu
    # suffit à fausser la plaque entière.
    assert score == 0.88


def test_une_plaque_sur_deux_lignes_se_lit_ligne_par_ligne():
    """Ordonner sur l'abscisse seule met tout dans le désordre dès qu'une
    plaque a deux lignes : le second groupe du haut passe après le premier du
    bas."""
    from app.plates import PlateReader

    lecteur = PlateReader(asynchrone=False)
    lecteur._ocr_teste = True
    lecteur._ocr = _OcrFactice([
        (_boite(70, 40, 40), "678", 0.90),   # bas droite
        (_boite(70, 5, 40), "234", 0.93),    # haut droite
        (_boite(10, 40, 40), "5", 0.92),     # bas gauche
        (_boite(10, 5, 40), "1", 0.94),      # haut gauche
    ])

    texte, _ = lecteur.lire_region(_image(120, 200))
    assert texte == "1234" + "5678", f"lu {texte!r}"


def _image(hauteur, largeur):
    import numpy as np

    return np.zeros((hauteur, largeur, 3), dtype=np.uint8)


def test_une_lettre_arabe_n_est_jamais_prise_pour_un_chiffre():
    """Une plaque marocaine s'écrit « chiffres · lettre arabe · chiffres ».
    La table des confusions ne doit jamais toucher la lettre."""
    from app.plates import corriger_confusions

    assert corriger_confusions("12345\u06486") == "12345\u06486"
    # Aux extrémités non plus, si jamais l'assemblage la place là.
    assert corriger_confusions("\u0648123\u0648") == "\u0648123\u0648"
    # Les confusions latines restent corrigées SUR UNE PLAQUE MAROCAINE.
    assert corriger_confusions("O123وS") == "0123و5"


def test_une_plaque_avec_lettre_arabe_reste_plausible():
    from app.plates import plausible

    assert plausible("12345\u06486")


def test_les_chiffres_arabes_sont_ramenes_en_chiffres_occidentaux():
    """Le moteur arabe connaît aussi ٠١٢٣ : c'est le même nombre, et un
    registre doit rester cherchable avec un clavier ordinaire."""
    from app.plates import normaliser

    assert normaliser("\u0661\u0662\u0663\u0664\u0665\u0648\u0666") == "12345\u06486"


def test_la_lettre_arabe_survit_a_la_normalisation():
    from app.plates import normaliser

    assert normaliser("12345 - \u0648 - 6") == "12345\u06486"


def test_le_jeu_de_caracteres_couvre_la_plaque_marocaine():
    """Restreindre les caractères reconnus est le réglage qui a le plus changé
    le résultat : le moteur arabe connaît quatre-vingt-sept signes, dont la
    ponctuation, et chacun est une confusion possible."""
    from app.plates import CARACTERES_PLAQUE, LETTRES_SERIE

    for c in "0123456789":
        assert c in CARACTERES_PLAQUE
    for c in LETTRES_SERIE:
        assert c in CARACTERES_PLAQUE
    assert "\u0648" in CARACTERES_PLAQUE          # و, la lettre du signalement
    assert "\u0660" not in CARACTERES_PLAQUE      # ٠, chiffre arabe : exclu
    assert "-" not in CARACTERES_PLAQUE and " " not in CARACTERES_PLAQUE


def test_une_plaque_etrangere_n_est_pas_corrigee_au_format_marocain():
    """La correction des extrémités suppose deux bouts en chiffres — vrai au
    Maroc, faux ailleurs. « SDN7484U », lu correctement par le moteur,
    ressortait « 5DN7484U »."""
    from app.plates import corriger_confusions

    assert corriger_confusions("SDN7484U") == "SDN7484U"
    # Avec la lettre de série, le format est marocain : on corrige.
    assert corriger_confusions("S1234\u0648O") == "51234\u06480"


def test_le_groupe_du_milieu_est_impose_comme_lettre():
    """Sur « 65990 و 6 », le moteur rendait « 9 » pour la lettre : و ressemble
    à un 9. Le groupe du milieu d'une plaque marocaine est TOUJOURS une lettre —
    l'imposer fait passer la lecture de 0,6 sur « 9 » à 0,91 sur « و ».

    C'est la structure de la plaque qui tranche, pas une meilleure
    reconnaissance.
    """
    import numpy as np

    from app.plates import PlateReader

    lecteur = PlateReader(asynchrone=False)
    lecteur._ocr_teste = True
    lecteur._ocr = _OcrFactice([
        (_boite(10, 10, 90), "65990", 0.95),
        (_boite(120, 10, 24), "9", 0.60),    # la lettre, prise pour un chiffre
        (_boite(170, 10, 24), "6", 0.92),
    ])
    lecteur._relire_en_lettre = lambda image, boite: "\u0648"

    texte, _ = lecteur.lire_region(np.zeros((60, 220, 3), dtype=np.uint8))
    assert texte == "65990\u06486", f"lu {texte!r}"


def test_sans_troisieme_groupe_rien_n_est_impose():
    """Deux groupes seulement : on ne sait pas lequel serait la lettre, et
    inventer une lettre serait pire que de n'en pas mettre."""
    import numpy as np

    from app.plates import PlateReader

    lecteur = PlateReader(asynchrone=False)
    lecteur._ocr_teste = True
    lecteur._ocr = _OcrFactice([
        (_boite(10, 10, 90), "65990", 0.95),
        (_boite(170, 10, 24), "6", 0.92),
    ])
    appele = []
    lecteur._relire_en_lettre = lambda image, boite: appele.append(1) or "\u0648"

    texte, _ = lecteur.lire_region(np.zeros((60, 220, 3), dtype=np.uint8))
    assert texte == "659906"
    assert not appele, "la relecture ne doit pas se declencher sur deux groupes"


# ── L'ordre d'écriture de la plaque ──────────────────────────────────
#
# Dès qu'une lettre arabe figure dans une boîte, le moteur rend la ligne en
# ordre logique arabe : « 65990 و 6 » revient « 6 و 65990 ». Affichée, elle
# paraît juste — le navigateur la remet à l'endroit — mais en base c'est un
# autre numéro. Le défaut n'était visible qu'en comparant les points de code.


def test_une_seule_boite_arabe_est_remise_dans_l_ordre_imprime():
    """Le cas réel : sur une plaque nette, le moteur ne rend qu'UNE boîte.

    L'assemblage par groupes ne protégeait donc de rien — il ne joue qu'à
    partir de deux boîtes.
    """
    import numpy as np

    from app.plates import PlateReader

    lecteur = PlateReader(asynchrone=False)
    lecteur._ocr_teste = True
    # Ce que easyocr rend vraiment : tout le numéro, en ordre logique inversé.
    lecteur._ocr = _OcrFactice([(_boite(10, 10, 200), "6\u064865990", 0.99)])

    texte, _ = lecteur.lire_region(np.zeros((60, 240, 3), dtype=np.uint8))
    assert [hex(ord(c)) for c in texte] == [hex(ord(c)) for c in "65990\u06486"], \
        f"lu {[hex(ord(c)) for c in texte]}"


def test_une_plaque_latine_n_est_pas_reordonnee():
    """Sans arabe, rien ne doit bouger : l'algorithme bidi ne s'applique pas."""
    from app.plates import ordre_visuel

    assert ordre_visuel("7H2340") == "7H2340"
    assert ordre_visuel("") == ""


# ── Le budget de lectures d'un véhicule ──────────────────────────────


def test_une_image_sautee_ne_consomme_pas_une_tentative():
    """Le lecteur occupé saute l'image — elle ne doit pas être décomptée.

    Elle l'était : la première lecture d'un processus charge easyocr pendant
    une minute, et les images de cette minute étaient toutes abandonnées ici
    tout en faisant monter le compteur. Au bout de huit, le véhicule était
    déclaré épuisé alors qu'il n'avait été lu qu'une fois — une lecture unique
    ne fait pas les deux votes concordants exigés, et la plaque n'était jamais
    établie. Les caméras restaient sur « lecture en cours », sans erreur nulle
    part, puisque l'unique lecture faite était juste.
    """
    import numpy as np

    from app.plates import PlateReader

    lecteur = PlateReader()
    lecteur._ocr_teste = True
    lecteur._ocr = _OcrFactice([])
    zone = np.zeros((60, 240, 3), dtype=np.uint8)

    lecteur._en_cours = True          # le lecteur travaille déjà
    for _ in range(20):
        lecteur.observer_plaque("cam", 1, zone)

    assert lecteur._tentatives[("cam", 1)] == 0
    assert lecteur.a_lire("cam", 1), "le véhicule doit rester lisible"


# ── La plaque réelle d'un véhicule ────────────────────────────────────
#
# Ce cas vient d'un essai en conditions réelles, et il a révélé un défaut
# qu'aucune plaque reconstruite n'aurait montré : le moteur de reconnaissance
# ne rend que DEUX boîtes sur cette image — « 65990 » et « 95 » — cette
# seconde avalant la barre de séparation, la lettre arabe et le code de
# région. La relecture ciblée de la lettre, qui exige trois groupes, ne se
# déclenchait donc jamais, et la plaque était consignée « 6599095 ».


def test_plaque_reelle_lue_avec_sa_lettre_de_serie():
    """La photo d'un véhicule réel, lue de bout en bout.

    Marqué lent : il charge le moteur de reconnaissance. C'est le prix d'un
    test qui porte sur une vraie image plutôt que sur une image fabriquée
    pour lui plaire.
    """
    import cv2
    import pytest

    from app.plates import PlateReader

    chemin = Path(__file__).parent / "donnees" / "plaque_208.jpg"
    image = cv2.imread(str(chemin))
    assert image is not None, f"image d'essai introuvable : {chemin}"

    lecteur = PlateReader()
    if not lecteur.ocr_disponible:
        pytest.skip("moteur de reconnaissance indisponible")

    texte, _ = lecteur.lire_region(image)
    assert texte == "65990\u06486", \
        f"lu {[hex(ord(c)) for c in texte]}, attendu 65990 + U+0648 + 6"


def test_le_decoupage_sur_les_barres_s_efface_s_il_doute():
    """Ne rien rendre est un résultat : mieux vaut la lecture ordinaire
    qu'un découpage inventé sur une image qui n'est pas une plaque."""
    import numpy as np

    from app.plates import separer_sur_barres

    assert separer_sur_barres(np.zeros((60, 200, 3), dtype=np.uint8)) is None
    assert separer_sur_barres(np.full((60, 200, 3), 255, dtype=np.uint8)) is None
    assert separer_sur_barres(np.zeros((10, 20, 3), dtype=np.uint8)) is None
