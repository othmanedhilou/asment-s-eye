"""Tests des sources vidéo : classification, lecture séquentielle, bouclage."""

import cv2
import numpy as np
import pytest

from app.capture import FrameSource, classify_source, probe_source, resolve_path


@pytest.fixture
def video_file(tmp_path):
    """Une vidéo de 5 images, chacune d'une teinte différente."""
    path = tmp_path / "test.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (64, 48))
    for i in range(5):
        frame = np.full((48, 64, 3), (i * 40, 0, 0), dtype=np.uint8)
        writer.write(frame)
    writer.release()
    assert path.exists() and path.stat().st_size > 0
    return path


@pytest.fixture
def image_dir(tmp_path):
    directory = tmp_path / "sequence"
    directory.mkdir()
    for i in range(3):
        cv2.imwrite(str(directory / f"{i:03d}.jpg"), np.full((48, 64, 3), i * 60, dtype=np.uint8))
    return directory


# ── Classification des sources ───────────────────────────────────────


def test_entier_est_une_webcam():
    assert classify_source(0) == "webcam"


def test_chaine_numerique_est_une_webcam():
    """`source: "0"` en YAML doit se comporter comme `source: 0`."""
    assert classify_source("0") == "webcam"


def test_url_est_un_flux_rtsp():
    assert classify_source("rtsp://192.168.1.42:554/stream") == "rtsp"


def test_fichier_video_reconnu(video_file):
    assert classify_source(str(video_file)) == "video"


def test_dossier_reconnu_comme_images(image_dir):
    assert classify_source(str(image_dir)) == "images"


def test_chemin_relatif_resolu_depuis_la_racine():
    """Un service Windows ne démarre pas dans le dossier du projet."""
    assert resolve_path("videos/x.mp4").is_absolute()


# ── Lecture d'un fichier vidéo ───────────────────────────────────────


def test_lecture_sequentielle_sans_perte(video_file):
    """Chaque appel rend l'image suivante : une vidéo rejouée doit être
    reproductible, sinon elle ne peut servir à mesurer un modèle."""
    source = FrameSource(str(video_file), loop=False)
    frames = []
    while (frame := source.read()) is not None:
        frames.append(frame)
    source.release()
    assert len(frames) == 5


def test_bouclage_de_la_video(video_file):
    """Une vidéo de test doit pouvoir tourner aussi longtemps qu'une caméra."""
    source = FrameSource(str(video_file), loop=True)
    frames = [source.read() for _ in range(12)]
    source.release()
    assert all(f is not None for f in frames)
    assert source.frames_read == 12


def test_fin_de_video_sans_bouclage(video_file):
    source = FrameSource(str(video_file), loop=False)
    for _ in range(5):
        source.read()
    assert source.read() is None
    source.release()


def test_cadence_respectee(video_file):
    source = FrameSource(str(video_file), loop=True)
    frames = []
    for frame in source.frames_at_fps(50):
        frames.append(frame)
        if len(frames) == 4:
            break
    source.release()
    assert len(frames) == 4


def test_proprietes_de_la_video(video_file):
    source = FrameSource(str(video_file), loop=False)
    props = source.properties()
    source.release()
    assert props["kind"] == "video"
    assert props["width"] == 64 and props["height"] == 48
    assert props["frames"] == 5


# ── Dossier d'images ─────────────────────────────────────────────────


def test_lecture_d_un_dossier_d_images(image_dir):
    source = FrameSource(str(image_dir), loop=False)
    frames = []
    while (frame := source.read()) is not None:
        frames.append(frame)
    source.release()
    assert len(frames) == 3


def test_dossier_d_images_boucle(image_dir):
    source = FrameSource(str(image_dir), loop=True)
    frames = [source.read() for _ in range(7)]
    source.release()
    assert all(f is not None for f in frames)


# ── Erreurs ──────────────────────────────────────────────────────────


def test_fichier_inexistant():
    with pytest.raises(ConnectionError):
        FrameSource("videos/nexiste_pas.mp4")


def test_dossier_vide(tmp_path):
    vide = tmp_path / "vide"
    vide.mkdir()
    with pytest.raises(ConnectionError):
        FrameSource(str(vide))


# ── Test de connexion (bouton de l'interface) ────────────────────────


def test_sonde_reussie(video_file):
    result = probe_source(str(video_file))
    assert result["ok"] is True
    assert result["width"] == 64


def test_sonde_echouee_sans_exception():
    """Le bouton « Tester » doit afficher une erreur lisible, pas planter."""
    result = probe_source("videos/nexiste_pas.mp4")
    assert result["ok"] is False
    assert "introuvable" in result["error"]
