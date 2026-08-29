"""Tests de la gestion des caméras (source de vérité modifiable sans YAML)."""

import json

import pytest

from app import cameras as cameras_module
from app.cameras import (
    active_cameras,
    camera_source,
    delete_camera,
    get_camera,
    load_cameras,
    rename_camera,
    upsert_camera,
)


@pytest.fixture(autouse=True)
def fichier_cameras(tmp_path, monkeypatch):
    path = tmp_path / "cameras.json"
    monkeypatch.setattr(cameras_module, "CAMERAS_PATH", path)
    monkeypatch.setattr(cameras_module, "_cache", None)
    monkeypatch.setattr(cameras_module, "_cache_time", 0.0)
    monkeypatch.setattr(cameras_module, "_seed_from_yaml", lambda: {})
    return path


def test_amorcage_depuis_le_yaml(tmp_path, monkeypatch):
    """Au premier démarrage, les caméras du YAML sont reprises : rien n'est perdu."""
    monkeypatch.setattr(cameras_module, "_seed_from_yaml",
                        lambda: {"webcam_test": {"source": 0, "models": ["epi"]}})
    monkeypatch.setattr(cameras_module, "_cache", None)
    assert "webcam_test" in load_cameras()
    assert cameras_module.CAMERAS_PATH.exists()


def test_creation_puis_relecture():
    upsert_camera("quai", {"source": "rtsp://x/1", "models": ["vehicles"]})
    assert get_camera("quai")["source"] == "rtsp://x/1"


def test_mise_a_jour_partielle_preserve_le_reste():
    """Changer la cadence ne doit pas effacer la liste des modèles."""
    upsert_camera("quai", {"source": "rtsp://x/1", "models": ["vehicles"], "fps": 4})
    upsert_camera("quai", {"fps": 1})
    cam = get_camera("quai")
    assert cam["fps"] == 1
    assert cam["models"] == ["vehicles"]


def test_suppression():
    upsert_camera("quai", {"source": 0})
    assert delete_camera("quai") is True
    assert get_camera("quai") is None


def test_suppression_inexistante():
    assert delete_camera("fantome") is False


def test_renommage():
    upsert_camera("ancienne", {"source": 0})
    assert rename_camera("ancienne", "nouvelle") is True
    assert get_camera("nouvelle") is not None and get_camera("ancienne") is None


def test_renommage_refuse_si_le_nom_existe():
    upsert_camera("a", {"source": 0})
    upsert_camera("b", {"source": 1})
    assert rename_camera("a", "b") is False


def test_camera_desactivee_exclue_du_traitement():
    """Suspendre une caméra en maintenance sans perdre ses zones ni ses réglages."""
    upsert_camera("active", {"source": 0})
    upsert_camera("en_panne", {"source": 1, "enabled": False})
    actives = active_cameras()
    assert "active" in actives and "en_panne" not in actives
    assert get_camera("en_panne") is not None


def test_source_moderne():
    assert camera_source({"source": "videos/test.mp4"}) == "videos/test.mp4"


def test_source_ancienne_toujours_lue():
    """Les configurations écrites avant l'unification des sources restent valides."""
    assert camera_source({"rtsp_url": "rtsp://x/1"}) == "rtsp://x/1"


def test_source_moderne_prioritaire():
    assert camera_source({"source": 0, "rtsp_url": "rtsp://x/1"}) == 0


def test_fichier_illisible_repart_du_yaml(fichier_cameras, monkeypatch):
    """Un JSON corrompu ne doit pas empêcher le pipeline de démarrer."""
    fichier_cameras.write_text("{ ceci n'est pas du json", encoding="utf-8")
    monkeypatch.setattr(cameras_module, "_cache", None)
    monkeypatch.setattr(cameras_module, "_seed_from_yaml", lambda: {"secours": {"source": 0}})
    assert "secours" in load_cameras()


def test_ecriture_sur_disque(fichier_cameras):
    upsert_camera("quai", {"source": 0})
    contenu = json.loads(fichier_cameras.read_text(encoding="utf-8"))
    assert "quai" in contenu
