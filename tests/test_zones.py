"""Tests du filtrage par zone d'intérêt."""

import json

import pytest

from app import zones as zones_module
from app.zones import ZoneFilter, anchor_point, point_in_polygon


@pytest.fixture(autouse=True)
def zones_file(tmp_path, monkeypatch):
    """Isole chaque test dans son propre fichier de zones, cache vidé."""
    path = tmp_path / "zones.json"
    monkeypatch.setattr(zones_module, "ZONES_PATH", path)
    monkeypatch.setattr(zones_module, "_cache", None)
    monkeypatch.setattr(zones_module, "_cache_time", 0.0)
    return path


def write_zones(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    zones_module._cache = None  # forcer la relecture


# ── Géométrie ────────────────────────────────────────────────────────

CARRE = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]


def test_point_dans_le_polygone():
    assert point_in_polygon(0.25, 0.25, CARRE)


def test_point_hors_du_polygone():
    assert not point_in_polygon(0.75, 0.75, CARRE)


def test_polygone_degenere_rejette_tout():
    """Moins de 3 sommets ne délimite aucune surface."""
    assert not point_in_polygon(0.1, 0.1, [[0.0, 0.0], [0.5, 0.5]])


def test_polygone_concave():
    """Un L : le creux ne doit pas être considéré comme intérieur."""
    forme_l = [[0.0, 0.0], [0.6, 0.0], [0.6, 0.2], [0.2, 0.2], [0.2, 0.6], [0.0, 0.6]]
    assert point_in_polygon(0.1, 0.5, forme_l)      # dans la branche verticale
    assert not point_in_polygon(0.5, 0.5, forme_l)  # dans le creux


def test_ancrage_au_sol():
    """L'ancrage est le milieu du bord bas, pas le centre de la boîte."""
    assert anchor_point((10.0, 20.0, 30.0, 60.0)) == (20.0, 60.0)


# ── ZoneFilter ───────────────────────────────────────────────────────


def test_sans_zone_tout_passe(zones_file):
    """Rétrocompatibilité : une caméra sans zone analyse le plein cadre."""
    f = ZoneFilter("cam1")
    assert f.zone_for("epi", (0.0, 0.0, 10.0, 10.0), 640, 480) == ""


def test_detection_dans_la_zone(zones_file):
    write_zones(zones_file, {"cam1": [{"name": "atelier", "polygon": CARRE, "models": ["epi"]}]})
    f = ZoneFilter("cam1")
    # ancrage (160, 120) sur 640x480 -> (0.25, 0.25) normalisé, dans le carré
    assert f.zone_for("epi", (140.0, 100.0, 180.0, 120.0), 640, 480) == "atelier"


def test_detection_hors_zone_rejetee(zones_file):
    write_zones(zones_file, {"cam1": [{"name": "atelier", "polygon": CARRE, "models": ["epi"]}]})
    f = ZoneFilter("cam1")
    # ancrage (480, 360) -> (0.75, 0.75), hors du carré
    assert f.zone_for("epi", (460.0, 340.0, 500.0, 360.0), 640, 480) is None


def test_modele_non_autorise_dans_la_zone(zones_file):
    """La zone atelier ne surveille que les EPI : un véhicule dedans est ignoré."""
    write_zones(zones_file, {"cam1": [{"name": "atelier", "polygon": CARRE, "models": ["epi"]}]})
    f = ZoneFilter("cam1")
    assert f.zone_for("vehicles", (140.0, 100.0, 180.0, 120.0), 640, 480) is None


def test_zone_sans_liste_de_modeles_accepte_tout(zones_file):
    write_zones(zones_file, {"cam1": [{"name": "site", "polygon": CARRE, "models": []}]})
    f = ZoneFilter("cam1")
    assert f.zone_for("vehicles", (140.0, 100.0, 180.0, 120.0), 640, 480) == "site"


def test_premiere_zone_correspondante_gagne(zones_file):
    write_zones(zones_file, {"cam1": [
        {"name": "quai", "polygon": CARRE, "models": ["vehicles"]},
        {"name": "site", "polygon": CARRE, "models": []},
    ]})
    f = ZoneFilter("cam1")
    assert f.zone_for("vehicles", (140.0, 100.0, 180.0, 120.0), 640, 480) == "quai"


def test_zones_independantes_par_camera(zones_file):
    write_zones(zones_file, {"cam1": [{"name": "atelier", "polygon": CARRE, "models": []}]})
    assert ZoneFilter("cam2").zone_for("epi", (140.0, 100.0, 180.0, 120.0), 640, 480) == ""


def test_coordonnees_normalisees_suivent_la_resolution(zones_file):
    """Le même point relatif doit être classé pareil quelle que soit la résolution."""
    write_zones(zones_file, {"cam1": [{"name": "atelier", "polygon": CARRE, "models": []}]})
    f = ZoneFilter("cam1")
    assert f.zone_for("epi", (0.0, 0.0, 320.0, 240.0), 1280, 960) == "atelier"
    assert f.zone_for("epi", (0.0, 0.0, 80.0, 60.0), 320, 240) == "atelier"


def test_dimensions_nulles_ne_plantent_pas(zones_file):
    """Une image dégénérée ne doit pas provoquer de division par zéro."""
    write_zones(zones_file, {"cam1": [{"name": "atelier", "polygon": CARRE, "models": []}]})
    assert ZoneFilter("cam1").zone_for("epi", (0.0, 0.0, 1.0, 1.0), 0, 0) == ""


def test_polygones_en_pixels(zones_file):
    write_zones(zones_file, {"cam1": [{"name": "atelier", "polygon": CARRE, "models": []}]})
    polys = ZoneFilter("cam1").polygons_in_pixels(640, 480)
    assert polys[0]["name"] == "atelier"
    assert polys[0]["points"] == [(0, 0), (320, 0), (320, 240), (0, 240)]
