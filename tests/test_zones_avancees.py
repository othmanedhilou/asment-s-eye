"""Tests des zones avancées : exclusions, horaires, seuils propres à la zone."""

import json
from datetime import datetime

import pytest

from app import zones as zones_module
from app.zones import ZoneFilter, schedule_active

CARRE = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
CARRE_LARGE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]

# Ancrage à (0.25, 0.25) sur une image 640x480 : dans le petit carré.
BBOX_DEDANS = (140.0, 100.0, 180.0, 120.0)
# Ancrage à (0.75, 0.75) : hors du petit carré.
BBOX_DEHORS = (460.0, 340.0, 500.0, 360.0)


@pytest.fixture(autouse=True)
def zones_file(tmp_path, monkeypatch):
    path = tmp_path / "zones.json"
    monkeypatch.setattr(zones_module, "ZONES_PATH", path)
    monkeypatch.setattr(zones_module, "_cache", None)
    monkeypatch.setattr(zones_module, "_cache_time", 0.0)
    return path


def write_zones(path, zones):
    path.write_text(json.dumps({"cam1": zones}), encoding="utf-8")
    zones_module._cache = None


# ── Zones d'exclusion ────────────────────────────────────────────────


def test_exclusion_rejette_la_detection(zones_file):
    """Masquer la route au fond du champ plutôt que contourner l'atelier."""
    write_zones(zones_file, [{"name": "route", "polygon": CARRE, "type": "exclusion"}])
    assert ZoneFilter("cam1").match("vehicles", BBOX_DEDANS, 640, 480) is None


def test_hors_exclusion_reste_plein_cadre(zones_file):
    """Une caméra n'ayant que des exclusions surveille tout le reste."""
    write_zones(zones_file, [{"name": "route", "polygon": CARRE, "type": "exclusion"}])
    matched = ZoneFilter("cam1").match("vehicles", BBOX_DEHORS, 640, 480)
    assert matched is not None and matched["name"] == ""


def test_exclusion_prime_sur_la_surveillance(zones_file):
    """Un écran de contrôle masqué à l'intérieur d'une zone surveillée."""
    write_zones(zones_file, [
        {"name": "atelier", "polygon": CARRE_LARGE},
        {"name": "ecran", "polygon": CARRE, "type": "exclusion"},
    ])
    f = ZoneFilter("cam1")
    assert f.match("epi", BBOX_DEDANS, 640, 480) is None            # sur l'écran
    assert f.match("epi", BBOX_DEHORS, 640, 480)["name"] == "atelier"


def test_exclusion_ciblee_sur_un_modele(zones_file):
    """La route gêne le modèle véhicules, pas la détection de fumée."""
    write_zones(zones_file, [
        {"name": "route", "polygon": CARRE, "type": "exclusion", "models": ["vehicles"]},
    ])
    f = ZoneFilter("cam1")
    assert f.match("vehicles", BBOX_DEDANS, 640, 480) is None
    assert f.match("fire_smoke", BBOX_DEDANS, 640, 480)["name"] == ""


# ── Horaires ─────────────────────────────────────────────────────────


def test_sans_horaire_toujours_actif():
    assert schedule_active(None) is True
    assert schedule_active({}) is True


def test_plage_de_jour():
    horaire = {"start": "06:00", "end": "22:00"}
    assert schedule_active(horaire, datetime(2026, 3, 12, 14, 0)) is True
    assert schedule_active(horaire, datetime(2026, 3, 12, 3, 0)) is False


def test_plage_de_nuit_a_cheval_sur_minuit():
    """La surveillance de nuit est le cas le plus fréquent, pas un cas limite."""
    horaire = {"start": "22:00", "end": "06:00"}
    assert schedule_active(horaire, datetime(2026, 3, 12, 23, 30)) is True
    assert schedule_active(horaire, datetime(2026, 3, 12, 2, 0)) is True
    assert schedule_active(horaire, datetime(2026, 3, 12, 14, 0)) is False


def test_jours_de_la_semaine():
    # 12 mars 2026 est un jeudi (weekday 3), le 14 un samedi (weekday 5)
    horaire = {"days": [0, 1, 2, 3, 4]}
    assert schedule_active(horaire, datetime(2026, 3, 12, 10, 0)) is True
    assert schedule_active(horaire, datetime(2026, 3, 14, 10, 0)) is False


def test_horaire_invalide_reste_actif():
    """Une saisie erronée ne doit jamais éteindre une surveillance en silence."""
    assert schedule_active({"start": "nimporte", "end": "quoi"}) is True


def test_zone_hors_horaire_ignore_la_detection(zones_file):
    write_zones(zones_file, [
        {"name": "atelier", "polygon": CARRE, "schedule": {"start": "06:00", "end": "22:00"}},
    ])
    f = ZoneFilter("cam1")
    nuit = datetime(2026, 3, 12, 3, 0)
    jour = datetime(2026, 3, 12, 10, 0)
    assert f.match("epi", BBOX_DEDANS, 640, 480, now=nuit) is None
    assert f.match("epi", BBOX_DEDANS, 640, 480, now=jour)["name"] == "atelier"


def test_surveillance_de_nuit_uniquement(zones_file):
    """Présence près des fours : anodine en journée, anormale la nuit."""
    write_zones(zones_file, [
        {"name": "fours", "polygon": CARRE, "models": ["person_animal"],
         "schedule": {"start": "22:00", "end": "06:00"}},
    ])
    f = ZoneFilter("cam1")
    assert f.match("person_animal", BBOX_DEDANS, 640, 480,
                   now=datetime(2026, 3, 12, 3, 0))["name"] == "fours"
    assert f.match("person_animal", BBOX_DEDANS, 640, 480,
                   now=datetime(2026, 3, 12, 14, 0)) is None


# ── Seuils propres à la zone ─────────────────────────────────────────


def test_seuils_de_zone_remontes(zones_file):
    write_zones(zones_file, [
        {"name": "quai", "polygon": CARRE, "conf": 0.7, "cooldown": 1800},
    ])
    matched = ZoneFilter("cam1").match("vehicles", BBOX_DEDANS, 640, 480)
    assert matched["conf"] == 0.7
    assert matched["cooldown"] == 1800


def test_plein_cadre_sans_seuils(zones_file):
    zones_file.write_text("{}", encoding="utf-8")
    zones_module._cache = None
    matched = ZoneFilter("cam1").match("epi", BBOX_DEDANS, 640, 480)
    assert matched["name"] == "" and matched["conf"] is None


def test_type_remonte_pour_l_affichage(zones_file):
    write_zones(zones_file, [{"name": "route", "polygon": CARRE, "type": "exclusion"}])
    polys = ZoneFilter("cam1").polygons_in_pixels(640, 480)
    assert polys[0]["type"] == "exclusion"
