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
