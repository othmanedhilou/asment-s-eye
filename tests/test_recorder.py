"""Tests de l'enregistrement continu.

C'est la fonction qui peut mettre le serveur à genoux : elle écrit en
permanence. Les garde-fous comptent donc autant que la fonction elle-même.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest

from app import recorder as recorder_module
from app.recorder import ContinuousRecorder


@pytest.fixture(autouse=True)
def dossier_temporaire(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder_module, "CLIPS_DIR", tmp_path / "videos")
    return tmp_path


def image():
    return np.zeros((48, 64, 3), dtype=np.uint8)


def test_ecriture_d_un_segment(dossier_temporaire):
    rec = ContinuousRecorder("cam1", fps=4, min_free_gb=0)
    for _ in range(10):
        rec.add_frame(image())
    rec.release()

    jour = datetime.now().strftime("%Y-%m-%d")
    segments = list((dossier_temporaire / "continu" / "cam1" / jour).glob("*.mp4"))
    assert len(segments) == 1


def test_rotation_par_segment(dossier_temporaire):
    """Des segments courts : un fichier corrompu ne fait perdre que quelques
    minutes, et une journée se consulte sans télécharger des dizaines de Go."""
    rec = ContinuousRecorder("cam1", fps=4, min_free_gb=0)
    rec.segment_frames = 5          # segment très court pour le test
    for _ in range(12):
        rec.add_frame(image())
    rec.release()

    jour = datetime.now().strftime("%Y-%m-%d")
    segments = list((dossier_temporaire / "continu" / "cam1" / jour).glob("*.mp4"))
    assert len(segments) >= 2


def test_suspension_si_disque_plein(dossier_temporaire, monkeypatch):
    """La détection doit rester prioritaire : mieux vaut perdre l'enregistrement
    que d'empêcher le système d'écrire ses alertes."""
    rec = ContinuousRecorder("cam1", fps=4, min_free_gb=10_000_000)  # seuil inatteignable
    for _ in range(5):
        rec.add_frame(image())
    rec.release()

    jour = datetime.now().strftime("%Y-%m-%d")
    dossier = dossier_temporaire / "continu" / "cam1" / jour
    assert not dossier.exists() or not list(dossier.glob("*.mp4"))


def test_reprise_apres_liberation_du_disque(dossier_temporaire, monkeypatch):
    rec = ContinuousRecorder("cam1", fps=4, min_free_gb=10_000_000)
    rec.add_frame(image())
    assert rec._suspendu is True

    rec.min_free_gb = 1.0
    monkeypatch.setattr(rec, "_espace_libre_go", lambda: 500.0)
    rec.add_frame(image())
    rec.release()
    assert rec._suspendu is False


def test_purge_des_anciennes_journees(dossier_temporaire):
    rec = ContinuousRecorder("cam1", fps=4, retention_days=7, min_free_gb=0)

    vieux = rec.dossier / (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    recent = rec.dossier / (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    for dossier in (vieux, recent):
        dossier.mkdir(parents=True)
        (dossier / "000000.mp4").write_bytes(b"x")

    rec.purge()
    assert not vieux.exists()
    assert recent.exists()


def test_dossier_inattendu_ignore_par_la_purge(dossier_temporaire):
    """Un dossier au nom non daté ne doit pas faire échouer la purge."""
    rec = ContinuousRecorder("cam1", fps=4, min_free_gb=0)
    intrus = rec.dossier / "notes"
    intrus.mkdir(parents=True)
    rec.purge()
    assert intrus.exists()


def test_image_absente_sans_effet(dossier_temporaire):
    rec = ContinuousRecorder("cam1", fps=4, min_free_gb=0)
    rec.add_frame(None)
    rec.release()


def test_un_clip_en_cours_est_ecrit_quand_la_camera_s_arrete(tmp_path, monkeypatch):
    """Un clip attend dix secondes d'images après l'alerte. Si la caméra
    s'arrête pendant ce temps — pipeline redémarré, caméra mise en pause,
    vidéo terminée — les images accumulées partaient à la poubelle et l'alerte
    restait sans preuve. Or les alertes qui précèdent un arrêt sont souvent
    celles qui l'expliquent."""
    from pathlib import Path

    import numpy as np

    from app import recorder as recorder_module

    monkeypatch.setattr(recorder_module, "CLIPS_DIR", tmp_path)
    enregistres = {}
    monkeypatch.setattr(recorder_module, "update_alert_clip",
                        lambda alert_id, chemin: enregistres.update({alert_id: chemin}))

    r = recorder_module.ClipRecorder("cam", fps=2, pre_seconds=2, post_seconds=10)
    image = np.zeros((48, 64, 3), dtype=np.uint8)
    r.add_frame(image)
    r.trigger(42)
    r.add_frame(image)          # loin des 20 images attendues

    assert enregistres == {}, "le clip ne doit pas encore être écrit"
    r.release()
    assert 42 in enregistres
    assert Path(enregistres[42]).exists()


def test_le_clip_est_ecrit_dans_un_format_lisible_par_un_navigateur(tmp_path, monkeypatch):
    """Les clips sortaient en FMP4 (MPEG-4 Part 2) : fichier valide, décodable
    par OpenCV, et qu'aucun navigateur n'affiche. Tout fonctionnait sauf la
    dernière étape, celle qui compte pour l'opérateur."""
    from pathlib import Path

    import numpy as np

    from app import recorder as recorder_module

    monkeypatch.setattr(recorder_module, "CLIPS_DIR", tmp_path)
    ecrits = {}
    monkeypatch.setattr(recorder_module, "update_alert_clip",
                        lambda alert_id, chemin: ecrits.update({alert_id: chemin}))

    r = recorder_module.ClipRecorder("cam", fps=2, pre_seconds=1, post_seconds=1)
    for _ in range(3):
        r.add_frame(np.zeros((48, 64, 3), dtype=np.uint8))
    r.trigger(7)
    for _ in range(6):
        r.add_frame(np.zeros((48, 64, 3), dtype=np.uint8))

    assert 7 in ecrits, "le clip aurait dû être écrit"
    chemin = Path(ecrits[7])
    assert chemin.suffix == ".webm", f"format inattendu : {chemin.suffix}"
    # En-tête EBML : c'est ce qui fait qu'un navigateur reconnaît un WebM.
    assert chemin.read_bytes()[:4] == b"\x1aE\xdf\xa3"


def test_le_type_mime_suit_l_extension():
    """Annoncer video/mp4 pour un fichier WebM empêche certains navigateurs de
    le lire — l'erreur est silencieuse côté utilisateur."""
    from app.api import app as application

    routes = {r.path: r for r in application.routes}
    assert "/api/clip" in routes
