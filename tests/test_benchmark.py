"""Tests du banc de test : agrégation des mesures et comparaison avant/après."""

from app.benchmark import _synthese, compare


def clip(nom, detections=None, fausses=None):
    return {"file": nom, "images_analysees": 100, "duree_minutes": 1.0,
            "detections": detections or {}, "fausses": fausses or {}}


def test_taux_moyenne_sur_les_clips():
    resultats = [
        clip("a.mp4", detections={"epi/NO-Hardhat": {"images_vues": 40, "taux": 0.4}}),
        clip("b.mp4", detections={"epi/NO-Hardhat": {"images_vues": 60, "taux": 0.6}}),
    ]
    assert _synthese(resultats)["taux_detection"]["epi/NO-Hardhat"] == 0.5


def test_fausses_cumulees_sur_les_clips():
    resultats = [
        clip("a.mp4", fausses={"load_control/empty": {"images_vues": 30, "par_minute": 12.0}}),
        clip("b.mp4", fausses={"load_control/empty": {"images_vues": 20, "par_minute": 8.0}}),
    ]
    assert _synthese(resultats)["fausses_par_minute"]["load_control/empty"] == 20.0


def test_fausses_triees_par_gravite():
    resultats = [clip("a.mp4", fausses={
        "epi/NO-Mask": {"images_vues": 1, "par_minute": 2.0},
        "load_control/empty": {"images_vues": 9, "par_minute": 30.0},
    })]
    ordre = list(_synthese(resultats)["fausses_par_minute"])
    assert ordre[0] == "load_control/empty"


# ── Comparaison avant / après ────────────────────────────────────────


def rapport(taux, fausses, date="2026-01-01T00:00:00"):
    return {"date": date, "synthese": {"taux_detection": taux, "fausses_par_minute": fausses}}


def test_progression_du_taux_de_detection():
    """54 % → 71 % sur NO-Hardhat : c'est la phrase d'une soutenance."""
    diff = compare(rapport({"epi/NO-Hardhat": 0.54}, {}),
                   rapport({"epi/NO-Hardhat": 0.71}, {}))
    ligne = diff["lignes"][0]
    assert ligne["ecart"] == 0.17
    assert ligne["amelioration"] is True


def test_regression_du_taux_detectee():
    """Le vrai danger : reculer sans le voir."""
    diff = compare(rapport({"epi/NO-Hardhat": 0.54}, {}),
                   rapport({"epi/NO-Hardhat": 0.40}, {}))
    assert diff["lignes"][0]["amelioration"] is False


def test_moins_de_fausses_detections_est_une_amelioration():
    """Sur le bruit, l'écart s'interprète à l'envers : moins vaut mieux."""
    diff = compare(rapport({}, {"load_control/empty": 30.0}),
                   rapport({}, {"load_control/empty": 4.0}))
    ligne = diff["lignes"][0]
    assert ligne["ecart"] == -26.0
    assert ligne["amelioration"] is True


def test_classe_apparue_apres_coup():
    diff = compare(rapport({}, {}), rapport({"conveyor/crack": 0.8}, {}))
    ligne = diff["lignes"][0]
    assert ligne["avant"] is None and ligne["apres"] == 0.8


def test_bruit_disparu():
    diff = compare(rapport({}, {"epi/NO-Mask": 5.0}), rapport({}, {}))
    ligne = diff["lignes"][0]
    assert ligne["apres"] == 0.0 and ligne["amelioration"] is True
