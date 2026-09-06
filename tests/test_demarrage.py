"""Le logiciel doit pouvoir DÉMARRER.

Un commit a supprimé `app/bachage.py` en laissant ses appelants : le pipeline
échouait dès son import et ne démarrait plus du tout. Aucun test ne l'a vu —
aucun n'importait le pipeline.

C'est le test le plus bête de la suite, et celui qui manquait.
"""

import importlib

import pytest

MODULES = [
    "app.api", "app.pipeline", "app.rules", "app.storage", "app.plates",
    "app.tracking", "app.zones", "app.capture", "app.cameras", "app.recorder",
    "app.detectors", "app.reid", "app.report", "app.settings", "app.health",
    "app.usecases", "app.config", "app.models", "app.notifier",
]


@pytest.mark.parametrize("nom", MODULES)
def test_le_module_s_importe(nom):
    importlib.import_module(nom)


def test_les_modeles_declares_existent_sur_le_disque():
    """Un modèle déclaré dans config.yaml mais absent du disque fait démarrer
    une caméra sans lui, en silence si personne ne lit le journal."""
    from pathlib import Path

    from app.config import load_config

    racine = Path(__file__).resolve().parent.parent
    manquants = [nom for nom, cfg in load_config()["models"].items()
                 if not (racine / cfg["file"]).exists()]
    assert not manquants, f"declares mais absents : {manquants}"


def test_les_modeles_des_cameras_sont_declares():
    """Une caméra qui demande un modèle inconnu de config.yaml ne le fera
    jamais tourner, et rien dans l'interface ne le dira."""
    from app.cameras import load_cameras
    from app.config import load_config

    connus = set(load_config()["models"])
    for nom, cfg in load_cameras().items():
        inconnus = set(cfg.get("models", [])) - connus
        assert not inconnus, f"camera « {nom} » demande {inconnus}"


def test_tout_modele_declare_est_reglable_depuis_l_interface():
    """Un modèle absent de PIPELINE_MODELS tourne mais reste invisible dans
    Paramètres → Modèles : on ne peut ni le couper ni voir qu'il consomme du
    temps de calcul. C'est ce qui était arrivé à `plate`."""
    from app.config import load_config
    from app.settings import PIPELINE_MODELS

    declares = set(load_config()["models"])
    assert declares - set(PIPELINE_MODELS) == set(), \
        f"declares mais non reglables : {declares - set(PIPELINE_MODELS)}"
    assert set(PIPELINE_MODELS) - declares == set(), \
        f"reglables mais non declares : {set(PIPELINE_MODELS) - declares}"


def test_le_paquet_neutralise_les_boites_de_dialogue_du_chargeur():
    """OpenVINO énumère tous ses greffons au premier appel. Sur cette machine,
    le greffon GPU réclame une fonction OpenCL que le pilote de 2015 ne fournit
    pas, et Windows affiche une boîte MODALE qui bloque le démarrage. Sur un
    serveur qui démarre seul au boot, personne ne clique dessus.
    """
    import sys

    if sys.platform != "win32":
        pytest.skip("le mode d'erreur du chargeur est propre à Windows")

    import ctypes

    import app  # noqa: F401 — l'import pose le mode

    mode = ctypes.windll.kernel32.SetErrorMode(0)
    ctypes.windll.kernel32.SetErrorMode(mode)          # on le remet aussitôt
    assert mode & 0x0001, "SEM_FAILCRITICALERRORS n'est pas posé"


def test_openvino_voit_le_processeur():
    """Écarter un greffon inutilisable ne doit jamais coûter la détection."""
    import openvino as ov

    assert "CPU" in ov.Core().available_devices
