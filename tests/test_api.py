"""Tests de l'API : gestion des caméras, santé, retour opérateur.

Ce sont les points d'entrée que l'interface utilise ; une régression ici se voit
en démonstration, pas dans les journaux.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import api as api_module
from app import cameras as cameras_module
from app import health as health_module
from app import storage as storage_module
from app import zones as zones_module
from app.models import Alert
from app.storage import log_alert


@pytest.fixture(autouse=True)
def environnement_isole(tmp_path, monkeypatch):
    """Chaque test travaille sur ses propres fichiers, jamais ceux de production."""
    monkeypatch.setattr(cameras_module, "CAMERAS_PATH", tmp_path / "cameras.json")
    monkeypatch.setattr(cameras_module, "_cache", None)
    monkeypatch.setattr(cameras_module, "_cache_time", 0.0)
    monkeypatch.setattr(cameras_module, "_seed_from_yaml", lambda: {})

    monkeypatch.setattr(zones_module, "ZONES_PATH", tmp_path / "zones.json")
    monkeypatch.setattr(zones_module, "_cache", None)
    monkeypatch.setattr(zones_module, "_cache_time", 0.0)

    monkeypatch.setattr(health_module, "HEALTH_PATH", tmp_path / "health.json")
    monkeypatch.setattr(storage_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(storage_module, "_engine", None)
    monkeypatch.setattr(api_module, "LIVE_DIR", tmp_path / "live")
    yield
    monkeypatch.setattr(storage_module, "_engine", None)


@pytest.fixture
def client():
    return TestClient(api_module.app)


@pytest.fixture
def video_file(tmp_path):
    import cv2
    import numpy as np

    path = tmp_path / "source.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (64, 48))
    for _ in range(3):
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()
    return path


# ── Gestion des caméras ──────────────────────────────────────────────


def test_creation_d_une_camera(client):
    r = client.post("/api/cameras/quai", json={"source": "rtsp://x/1", "models": ["epi"]})
    assert r.status_code == 200
    assert r.json()["camera"]["source"] == "rtsp://x/1"

    cameras = client.get("/api/cameras").json()["cameras"]
    assert [c["name"] for c in cameras] == ["quai"]


def test_modeles_inconnus_refuses(client):
    """Une faute de frappe dans un nom de modèle doit être signalée tout de
    suite, pas produire une caméra qui ne détecte rien."""
    r = client.post("/api/cameras/quai", json={"source": 0, "models": ["epii"]})
    assert r.status_code == 400
    assert "epii" in r.json()["detail"]


def test_nom_de_camera_invalide(client):
    r = client.post("/api/cameras/a/b", json={"source": 0, "models": []})
    assert r.status_code in (400, 404)


def test_modification_d_une_camera(client):
    client.post("/api/cameras/quai", json={"source": 0, "models": ["epi"], "fps": 4})
    client.post("/api/cameras/quai", json={"source": 0, "models": ["epi"], "fps": 1})
    cam = client.get("/api/cameras").json()["cameras"][0]
    assert cam["fps"] == 1


def test_suppression_d_une_camera(client):
    client.post("/api/cameras/quai", json={"source": 0, "models": []})
    assert client.delete("/api/cameras/quai").status_code == 200
    assert client.get("/api/cameras").json()["cameras"] == []


def test_suppression_camera_inconnue(client):
    assert client.delete("/api/cameras/fantome").status_code == 404


def test_renommage(client):
    client.post("/api/cameras/ancienne", json={"source": 0, "models": []})
    r = client.post("/api/cameras/ancienne/rename", json={"nouveau_nom": "nouvelle"})
    assert r.status_code == 200
    assert client.get("/api/cameras").json()["cameras"][0]["name"] == "nouvelle"


# ── Test de connexion ────────────────────────────────────────────────


def test_source_valide(client, video_file):
    r = client.post("/api/cameras/test", json={"source": str(video_file)})
    body = r.json()
    assert body["ok"] is True
    assert body["kind"] == "video"
    assert body["width"] == 64


def test_source_invalide_repond_proprement(client):
    """Le bouton « Tester » doit afficher une erreur lisible, pas une erreur 500."""
    r = client.post("/api/cameras/test", json={"source": "videos/absent.mp4"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "introuvable" in r.json()["error"]


# ── Santé ────────────────────────────────────────────────────────────


def test_sante_sans_pipeline(client):
    """Sans pipeline en vie, l'interface doit le dire clairement."""
    body = client.get("/api/health").json()
    assert body["pipeline"]["running"] is False


def test_sante_avec_pipeline(client, monkeypatch):
    health_module.update_camera("quai", state="en ligne", cycle_ms=180)
    body = client.get("/api/health").json()
    assert body["pipeline"]["running"] is True
    assert body["cameras"]["quai"]["state"] == "en ligne"


def test_sante_compte_les_cameras(client):
    client.post("/api/cameras/a", json={"source": 0, "models": []})
    client.post("/api/cameras/b", json={"source": 1, "models": [], "enabled": False})
    body = client.get("/api/health").json()
    assert body["cameras_configurees"] == 2
    assert body["cameras_actives"] == 1


# ── Alertes : pagination et retour opérateur ─────────────────────────


def alerte(label="NO-Hardhat"):
    return Alert(camera="cam1", model="epi", label=label, confidence=0.9, message=label)


def test_liste_paginee(client):
    for i in range(7):
        log_alert(alerte(f"classe{i}"))
    body = client.get("/api/alerts?limit=3").json()
    assert len(body["items"]) == 3
    assert body["total"] == 7


def test_marquage_fausse_alerte(client):
    alert_id = log_alert(alerte())
    r = client.post(f"/api/alerts/{alert_id}/false", json={"is_false": True})
    assert r.status_code == 200
    assert client.get("/api/alerts").json()["items"][0]["false_positive"] is True


def test_marquage_alerte_inexistante(client):
    assert client.post("/api/alerts/9999/false", json={}).status_code == 404


def test_statistiques_qualite(client):
    alert_id = log_alert(alerte())
    client.post(f"/api/alerts/{alert_id}/false", json={"is_false": True})
    body = client.get("/api/stats/quality").json()
    assert body["par_modele"]["epi"]["fausses"] == 1


# ── Zones ────────────────────────────────────────────────────────────


def test_enregistrement_d_une_zone_d_exclusion(client):
    client.post("/api/cameras/cam1", json={"source": 0, "models": []})
    r = client.post("/api/zones/cam1", json={"zones": [{
        "name": "route",
        "polygon": [[0, 0], [0.5, 0], [0.5, 0.5]],
        "type": "exclusion",
    }]})
    assert r.status_code == 200
    zone = client.get("/api/zones/cam1").json()["zones"][0]
    assert zone["type"] == "exclusion"


def test_zone_avec_horaire(client):
    client.post("/api/cameras/cam1", json={"source": 0, "models": []})
    client.post("/api/zones/cam1", json={"zones": [{
        "name": "fours",
        "polygon": [[0, 0], [0.5, 0], [0.5, 0.5]],
        "schedule": {"start": "22:00", "end": "06:00"},
    }]})
    zone = client.get("/api/zones/cam1").json()["zones"][0]
    assert zone["schedule"]["start"] == "22:00"


def test_zone_trop_petite_refusee(client):
    client.post("/api/cameras/cam1", json={"source": 0, "models": []})
    r = client.post("/api/zones/cam1", json={"zones": [{
        "name": "x", "polygon": [[0, 0], [0.5, 0]],
    }]})
    assert r.status_code == 400


def test_zone_sur_camera_inconnue(client):
    r = client.post("/api/zones/fantome", json={"zones": []})
    assert r.status_code == 404


# ── Import de fichiers de test ───────────────────────────────────────


@pytest.fixture
def dossier_uploads(tmp_path, monkeypatch):
    dossier = tmp_path / "videos"
    dossier.mkdir()
    monkeypatch.setattr(api_module, "UPLOADS_DIR", dossier)
    return dossier


def test_import_d_une_video(client, dossier_uploads, video_file):
    r = client.post("/api/uploads",
                    files={"file": ("essai.mp4", video_file.read_bytes(), "video/mp4")})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "videos/essai.mp4"
    assert (dossier_uploads / "essai.mp4").exists()


def test_format_refuse(client, dossier_uploads):
    """Liste blanche : un fichier déposé ne doit jamais être autre chose qu'une
    vidéo ou une image."""
    r = client.post("/api/uploads",
                    files={"file": ("script.exe", b"MZ...", "application/octet-stream")})
    assert r.status_code == 400
    assert "Format non accepte" in r.json()["detail"]


def test_fichier_illisible_refuse(client, dossier_uploads):
    """Un envoi tronqué se découvrirait sinon au démarrage de la caméra."""
    r = client.post("/api/uploads",
                    files={"file": ("casse.mp4", b"ceci n'est pas une video", "video/mp4")})
    assert r.status_code == 400
    assert not list(dossier_uploads.iterdir())   # rien ne reste sur le disque


def test_nom_de_fichier_assaini(client, dossier_uploads, video_file):
    """Un nom venu du navigateur ne doit pas pouvoir écrire hors du dossier."""
    r = client.post("/api/uploads",
                    files={"file": ("../../evasion.mp4", video_file.read_bytes(), "video/mp4")})
    assert r.status_code == 200
    assert (dossier_uploads / "evasion.mp4").exists()


def test_pas_d_ecrasement(client, dossier_uploads, video_file):
    """Une caméra peut déjà utiliser le fichier existant."""
    contenu = video_file.read_bytes()
    client.post("/api/uploads", files={"file": ("essai.mp4", contenu, "video/mp4")})
    r = client.post("/api/uploads", files={"file": ("essai.mp4", contenu, "video/mp4")})
    assert r.json()["nom"] == "essai_1.mp4"


def test_liste_des_fichiers(client, dossier_uploads, video_file):
    client.post("/api/uploads", files={"file": ("essai.mp4", video_file.read_bytes(), "video/mp4")})
    fichiers = client.get("/api/uploads").json()["fichiers"]
    assert fichiers[0]["type"] == "video"
    assert fichiers[0]["source"] == "videos/essai.mp4"


def test_suppression(client, dossier_uploads, video_file):
    client.post("/api/uploads", files={"file": ("essai.mp4", video_file.read_bytes(), "video/mp4")})
    assert client.delete("/api/uploads/essai.mp4").status_code == 200
    assert client.get("/api/uploads").json()["fichiers"] == []


def test_suppression_refusee_si_utilise(client, dossier_uploads, video_file):
    """Supprimer un fichier utilisé par une caméra la rendrait aveugle."""
    client.post("/api/uploads", files={"file": ("essai.mp4", video_file.read_bytes(), "video/mp4")})
    client.post("/api/cameras/rejeu", json={"source": "videos/essai.mp4", "models": []})
    r = client.delete("/api/uploads/essai.mp4")
    assert r.status_code == 409
    assert "rejeu" in r.json()["detail"]
