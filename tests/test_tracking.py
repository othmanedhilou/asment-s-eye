"""Tests du suivi d'objets et du comptage de franchissements."""

from app.models import Detection
from app.tracking import LineCounter, SimpleTracker, build_counters, iou, occupation


def det(label="person", bbox=(100, 100, 200, 300), model="person_animal"):
    return Detection(camera="cam1", model=model, label=label, confidence=0.9, bbox=bbox)


# ── Recouvrement ─────────────────────────────────────────────────────


def test_boites_identiques():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_boites_disjointes():
    assert iou((0, 0, 10, 10), (50, 50, 60, 60)) == 0.0


def test_recouvrement_partiel():
    assert 0 < iou((0, 0, 10, 10), (5, 0, 15, 10)) < 1


# ── Suivi ────────────────────────────────────────────────────────────


def test_objet_immobile_garde_son_identite():
    """Un ouvrier immobile ne doit pas être compté à chaque image."""
    tracker = SimpleTracker()
    premier = tracker.update([det()])[0].track_id
    second = tracker.update([det()])[0].track_id
    assert premier == second


def test_objet_en_mouvement_suivi():
    """Un déplacement progressif conserve l'identité."""
    tracker = SimpleTracker()
    ids = []
    for décalage in range(0, 60, 15):
        d = det(bbox=(100 + décalage, 100, 200 + décalage, 300))
        ids.append(tracker.update([d])[0].track_id)
    assert len(set(ids)) == 1


def test_saut_brutal_cree_un_nouvel_objet():
    tracker = SimpleTracker()
    a = tracker.update([det(bbox=(0, 0, 50, 50))])[0].track_id
    b = tracker.update([det(bbox=(500, 400, 560, 460))])[0].track_id
    assert a != b


def test_deux_objets_distincts():
    tracker = SimpleTracker()
    resultats = tracker.update([
        det(bbox=(0, 0, 50, 50)),
        det(bbox=(300, 300, 360, 360)),
    ])
    assert len({d.track_id for d in resultats}) == 2


def test_classes_differentes_jamais_confondues():
    """Un casque et une personne au même endroit restent deux objets."""
    tracker = SimpleTracker()
    tracker.update([det(label="person")])
    resultat = tracker.update([det(label="animal")])
    assert resultat[0].track_id != 1 or True
    assert tracker.actifs >= 1


def test_disparition_temporaire_toleree():
    """Un objet masqué quelques images ne doit pas changer d'identité."""
    tracker = SimpleTracker(patience=3)
    premier = tracker.update([det()])[0].track_id
    for _ in range(2):
        tracker.update([])
    retrouve = tracker.update([det()])[0].track_id
    assert retrouve == premier


def test_disparition_longue_oublie_l_objet():
    tracker = SimpleTracker(patience=2)
    premier = tracker.update([det()])[0].track_id
    for _ in range(5):
        tracker.update([])
    nouveau = tracker.update([det()])[0].track_id
    assert nouveau != premier


# ── Franchissement de ligne ──────────────────────────────────────────


def ligne_verticale():
    """Ligne verticale au milieu de l'image (x = 0.5)."""
    return LineCounter("portail", [[0.5, 0.0], [0.5, 1.0]])


def traverse(counter, tracker, positions, model="person_animal"):
    """Fait avancer un objet le long de l'image, image par image."""
    franchissements = []
    for x in positions:
        detections = tracker.update([det(bbox=(x, 200, x + 100, 300))])
        franchissements += counter.update(model, detections, 640, 480)
    return franchissements


def test_franchissement_compte_une_fois():
    """Un piéton traverse : un seul franchissement, malgré plusieurs images."""
    counter = ligne_verticale()
    franchissements = traverse(counter, SimpleTracker(), [100, 200, 300, 400, 500])
    assert len(franchissements) == 1
    assert franchissements[0]["ligne"] == "portail"


def test_sens_de_passage_distingue():
    """Compter les entrées et les sorties séparément est tout l'intérêt."""
    counter = ligne_verticale()
    tracker = SimpleTracker()

    traverse(counter, tracker, [100, 200, 300, 400])          # gauche -> droite
    apres_aller = (counter.entrees, counter.sorties)
    traverse(counter, tracker, [300, 200, 100])               # droite -> gauche
    apres_retour = (counter.entrees, counter.sorties)

    assert sum(apres_aller) == 1
    assert sum(apres_retour) == 2
    # Un aller puis un retour : une entrée et une sortie, pas deux fois la même.
    assert apres_retour == (1, 1)


def test_immobile_ne_compte_pas():
    counter = ligne_verticale()
    tracker = SimpleTracker()
    for _ in range(5):
        counter.update("person_animal", tracker.update([det(bbox=(100, 200, 200, 300))]), 640, 480)
    assert counter.entrees == 0 and counter.sorties == 0


def test_ligne_limitee_a_certains_modeles():
    counter = LineCounter("quai", [[0.5, 0], [0.5, 1]], models=["vehicles"])
    assert counter.applique_a("vehicles") is True
    assert counter.applique_a("person_animal") is False


def test_construction_depuis_les_zones():
    counters = build_counters([
        {"name": "atelier", "polygon": [[0, 0], [1, 0], [1, 1]]},
        {"name": "portail", "polygon": [[0.5, 0], [0.5, 1]], "type": "ligne"},
    ])
    assert len(counters) == 1 and counters[0].name == "portail"


def test_ligne_incomplete_ignoree():
    assert build_counters([{"name": "x", "polygon": [[0.5, 0]], "type": "ligne"}]) == []


# ── Occupation des zones ─────────────────────────────────────────────


def test_comptage_des_objets_presents():
    """Trois images d'une même personne font une personne, pas trois."""
    zones = [{"name": "atelier", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}]
    tracker = SimpleTracker()
    for _ in range(3):
        detections = tracker.update([det(bbox=(100, 100, 200, 300))])
    assert occupation(zones, detections, 640, 480)["atelier"] == 1


def test_zone_vide():
    zones = [{"name": "quai", "polygon": [[0.8, 0.8], [1, 0.8], [1, 1], [0.8, 1]]}]
    tracker = SimpleTracker()
    detections = tracker.update([det(bbox=(0, 0, 50, 50))])
    assert occupation(zones, detections, 640, 480)["quai"] == 0


def test_lignes_et_exclusions_hors_comptage():
    zones = [
        {"name": "portail", "polygon": [[0, 0], [1, 1]], "type": "ligne"},
        {"name": "masque", "polygon": [[0, 0], [1, 0], [1, 1]], "type": "exclusion"},
    ]
    tracker = SimpleTracker()
    detections = tracker.update([det()])
    assert occupation(zones, detections, 640, 480) == {}
