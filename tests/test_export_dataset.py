"""Test de la conversion des détections au format YOLO.

Une erreur ici produirait un jeu de données silencieusement faux : le modèle
s'entraînerait sur des boîtes décalées, et personne ne le verrait avant la
mesure finale.
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export_dataset.py"
spec = importlib.util.spec_from_file_location("export_dataset", SCRIPT)
export_dataset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_dataset)

yolo_line = export_dataset.yolo_line


def test_boite_centree():
    """Une boîte au centre d'une image 640×480, de 320×240."""
    ligne = yolo_line(0, [160, 120, 480, 360, 640, 480])
    classe, cx, cy, w, h = ligne.split()
    assert classe == "0"
    assert float(cx) == 0.5 and float(cy) == 0.5
    assert float(w) == 0.5 and float(h) == 0.5


def test_boite_en_coin():
    ligne = yolo_line(3, [0, 0, 64, 48, 640, 480])
    classe, cx, cy, w, h = ligne.split()
    assert classe == "3"
    assert float(cx) == 0.05 and float(cy) == 0.05
    assert float(w) == 0.1 and float(h) == 0.1


def test_valeurs_toujours_normalisees():
    """Quelle que soit la résolution, YOLO attend des valeurs entre 0 et 1."""
    for largeur, hauteur in [(640, 480), (1920, 1080), (320, 240)]:
        ligne = yolo_line(1, [10, 10, largeur - 10, hauteur - 10, largeur, hauteur])
        valeurs = [float(v) for v in ligne.split()[1:]]
        assert all(0.0 <= v <= 1.0 for v in valeurs)


def test_index_de_classe_conserve():
    """L'ordre des classes du modèle doit être respecté : un décalage
    entraînerait le modèle à confondre casque et gilet."""
    assert yolo_line(7, [0, 0, 10, 10, 100, 100]).startswith("7 ")
