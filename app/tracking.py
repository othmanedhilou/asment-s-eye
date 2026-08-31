"""Suivi des objets d'une image à l'autre, et comptage de franchissements.

Sans suivi, chaque image est analysée isolément : le système voit « une
personne », puis « une personne », sans jamais comprendre que c'est **la même**.
Trois conséquences concrètes — impossible de compter (trois images d'un camion
font trois camions), impossible de connaître un sens de passage, et le seul
rempart contre le flot d'alertes reste le délai anti-répétition, qui est un
pansement plutôt qu'une compréhension.

Pourquoi un suiveur écrit ici plutôt que celui d'Ultralytics
------------------------------------------------------------
Ultralytics sait suivre (`model.track(persist=True)`), mais l'état du suivi est
attaché à **l'objet modèle**. Or les modèles sont chargés une seule fois et
partagés par toutes les caméras — c'est ce qui permet d'en faire tourner huit
sur une machine modeste. Deux caméras partageant un modèle mélangeraient donc
leurs pistes, et personne ne le verrait avant d'obtenir des comptages absurdes.

L'association par recouvrement de boîtes tient en quelques dizaines de lignes,
ne coûte presque rien, et laisse chaque caméra maîtresse de son état.
"""

from dataclasses import dataclass, field

from app.zones import point_in_polygon


def iou(a, b) -> float:
    """Recouvrement entre deux boîtes : 0 = disjointes, 1 = identiques."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    largeur, hauteur = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = largeur * hauteur
    if intersection <= 0:
        return 0.0

    aire_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    aire_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = aire_a + aire_b - intersection
    return intersection / union if union else 0.0


# Un modèle d'EPI ne voit pas « une personne » : il voit un casque, un gilet,
# une absence de casque. La même personne change donc d'étiquette d'une image à
# l'autre, et un suivi par étiquette stricte lui donnerait une identité neuve à
# chaque changement — donc une alerte neuve. On regroupe les étiquettes qui
# désignent le même objet physique.
FAMILLES = {
    "personne": {
        "person", "Person", "personne",
        "Hardhat", "NO-Hardhat", "Mask", "NO-Mask",
        "Safety Vest", "NO-Safety Vest", "Safety Cone",
        "Gloves", "NO-Gloves", "Goggles", "NO-Goggles",
        "up", "bending", "down", "fallen", "falling", "Fall-Detected",
    },
    "vehicule": {
        "car", "truck", "bus", "motorcycle", "bicycle", "van",
        "truk_odol", "truk_normal", "truck_odol", "normal_truck",
        "covered-trucks", "six-wheel covered-trucks",
    },
}

_FAMILLE_PAR_LABEL = {lab: fam for fam, labs in FAMILLES.items() for lab in labs}


def famille(label: str) -> str | None:
    return _FAMILLE_PAR_LABEL.get(label)


def centre(bbox):
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


@dataclass
class Track:
    id: int
    label: str
    bbox: tuple
    perdu: int = 0
    positions: list = field(default_factory=list)


def distance(a, b) -> float:
    (ax, ay), (bx, by) = centre(a), centre(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def diagonale(bbox) -> float:
    x1, y1, x2, y2 = bbox
    return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5


class SimpleTracker:
    """Associe les détections d'une image aux objets déjà suivis.

    Un suiveur par caméra et par modèle : les identifiants restent locaux, et
    deux caméras ne peuvent pas se contaminer.

    L'association se fait en deux temps, et le second n'est pas un raffinement
    optionnel. Le recouvrement de boîtes suppose que l'objet a peu bougé entre
    deux images — vrai à 25 images par seconde, faux ici : l'inférence tourne
    à une ou deux images par seconde, et un piéton parcourt alors plus que sa
    propre largeur. Les boîtes ne se recouvrent plus du tout, et chaque image
    créerait un nouvel objet — rendant tout comptage absurde.

    On rattrape donc par la proximité des centres, bornée à quelques fois la
    taille de l'objet pour ne pas confondre deux personnes distinctes.
    """

    def __init__(self, iou_min: float = 0.3, patience: int = 5, historique: int = 30,
                 distance_max_facteur: float = 1.5, iou_famille: float = 0.35,
                 distance_famille_facteur: float = 0.6):
        self.iou_min = iou_min
        # Rapprocher deux étiquettes différentes est plus risqué que rapprocher
        # deux fois la même : on l'exige plus recouvrant, et plus proche.
        #
        # Les valeurs viennent d'une mesure, pas d'une intuition : à 2 images
        # par seconde, un piéton se déplace d'environ un tiers de sa largeur
        # entre deux images, ce qui fait tomber le recouvrement autour de 0,5.
        # Un seuil de famille à 0,55 refusait donc le rapprochement au moment
        # même où il compte — quand la personne bouge.
        self.iou_famille = iou_famille
        self.distance_famille_facteur = distance_famille_facteur
        self.patience = patience   # images tolérées sans revoir l'objet
        self.historique = historique
        self.distance_max_facteur = distance_max_facteur
        self._tracks: dict[int, Track] = {}
        self._prochain_id = 1

    def _candidats(self, detections: list, libres: dict) -> list:
        """Paires (score, index détection, piste), les meilleures d'abord."""
        paires = []
        for i, det in enumerate(detections):
            for track_id, track in libres.items():
                meme_label = track.label == det.label
                meme_famille = (famille(track.label) is not None
                                and famille(track.label) == famille(det.label))
                if not meme_label and not meme_famille:
                    continue

                recouvrement = iou(det.bbox, track.bbox)
                seuil = self.iou_min if meme_label else self.iou_famille
                if recouvrement >= seuil:
                    # Le recouvrement reste le critère le plus sûr : on le
                    # place devant toute association par distance. Une même
                    # étiquette passe devant un simple air de famille.
                    paires.append((2.0 + recouvrement if meme_label else 1.5 + recouvrement,
                                   i, track_id))
                    continue

                # Rattrapage par proximité des centres. Il est franchement plus
                # serré entre étiquettes différentes : confondre deux objets
                # voisins casserait tous les comptages.
                facteur = (self.distance_max_facteur if meme_label
                           else self.distance_famille_facteur)
                limite = facteur * max(diagonale(track.bbox), 1.0)
                ecart = distance(det.bbox, track.bbox)
                if ecart <= limite:
                    score = 1.0 - ecart / limite
                    paires.append((score if meme_label else score * 0.5, i, track_id))
        paires.sort(reverse=True)
        return paires

    def update(self, detections: list) -> list:
        """Attribue un identifiant à chaque détection. Modifie `detections`."""
        libres = dict(self._tracks)
        assignes: dict[int, Track] = {}

        detections_prises: set[int] = set()
        for _, i, track_id in self._candidats(detections, libres):
            if i in detections_prises or track_id not in libres:
                continue
            track = libres.pop(track_id)
            track.bbox = detections[i].bbox
            # L'étiquette suit l'objet : un ouvrier qui remet son casque reste
            # le même ouvrier, la piste garde son identité et change d'état.
            track.label = detections[i].label
            track.perdu = 0
            track.positions.append(centre(detections[i].bbox))
            del track.positions[:-self.historique]
            detections[i].track_id = track.id
            assignes[track.id] = track
            detections_prises.add(i)

        for i, det in enumerate(detections):
            if i in detections_prises:
                continue
            track = Track(id=self._prochain_id, label=det.label, bbox=det.bbox,
                          positions=[centre(det.bbox)])
            self._prochain_id += 1
            det.track_id = track.id
            assignes[track.id] = track

        # Un objet momentanément masqué ne doit pas changer d'identité : on le
        # garde quelques images avant de l'oublier.
        for track in libres.values():
            track.perdu += 1
            if track.perdu <= self.patience:
                assignes[track.id] = track

        self._tracks = assignes
        return detections

    @property
    def actifs(self) -> int:
        return sum(1 for t in self._tracks.values() if t.perdu == 0)

    def traces(self) -> list:
        """Trajectoires visibles : (identifiant, points parcourus).

        Ce qui rend le suivi crédible à l'écran n'est pas la boîte — elle
        existe sans suivi — mais le trait qui relie les positions successives
        d'un même objet. C'est ce trait qui montre qu'on a compris que c'est le
        même.
        """
        return [(t.id, list(t.positions)) for t in self._tracks.values()
                if t.perdu == 0 and len(t.positions) > 1]


def _cote(point, a, b) -> float:
    """De quel côté de la droite (a, b) se trouve le point ? Signe du produit
    vectoriel : positif d'un côté, négatif de l'autre."""
    return ((b[0] - a[0]) * (point[1] - a[1])) - ((b[1] - a[1]) * (point[0] - a[0]))


class LineCounter:
    """Compte les franchissements d'une ligne, avec le sens.

    Une ligne est un segment tracé sur l'image (deux sommets, en coordonnées
    normalisées). On regarde de quel côté se trouvait l'objet à l'image
    précédente et de quel côté il se trouve maintenant : un changement de signe
    est un franchissement.
    """

    def __init__(self, name: str, polygon: list, models: list | None = None):
        self.name = name
        self.a = tuple(polygon[0])
        self.b = tuple(polygon[1])
        self.models = models or []
        self.entrees = 0
        self.sorties = 0
        self._dernier_cote: dict[tuple[str, int], float] = {}

    def applique_a(self, model: str) -> bool:
        return not self.models or model in self.models

    def update(self, model: str, detections: list, frame_width: int, frame_height: int) -> list:
        """Renvoie la liste des franchissements survenus sur cette image."""
        if not self.applique_a(model) or not frame_width or not frame_height:
            return []

        franchissements = []
        for det in detections:
            track_id = getattr(det, "track_id", None)
            if track_id is None:
                continue

            cx, cy = centre(det.bbox)
            point = (cx / frame_width, cy / frame_height)
            cote = _cote(point, self.a, self.b)
            cle = (model, track_id)
            precedent = self._dernier_cote.get(cle)
            self._dernier_cote[cle] = cote

            if precedent is None or cote == 0 or precedent == 0:
                continue
            if (precedent > 0) == (cote > 0):
                continue

            sens = "entree" if cote > 0 else "sortie"
            if sens == "entree":
                self.entrees += 1
            else:
                self.sorties += 1
            franchissements.append({"ligne": self.name, "sens": sens,
                                    "classe": det.label, "track_id": track_id})

        return franchissements

    def counts(self) -> dict:
        return {"ligne": self.name, "entrees": self.entrees, "sorties": self.sorties}


def build_counters(zones: list) -> list[LineCounter]:
    """Construit les compteurs à partir des zones de type « ligne »."""
    counters = []
    for zone in zones:
        if zone.get("type") != "ligne":
            continue
        polygon = zone.get("polygon") or []
        if len(polygon) < 2:
            continue
        counters.append(LineCounter(zone.get("name", "ligne"), polygon, zone.get("models")))
    return counters


def occupation(zones: list, detections: list, frame_width: int, frame_height: int) -> dict:
    """Nombre d'objets distincts présents dans chaque zone de surveillance.

    Compte des objets suivis, pas des détections : un ouvrier immobile pendant
    dix images compte pour un, pas pour dix.
    """
    resultat: dict[str, set] = {}
    if not frame_width or not frame_height:
        return {}

    for zone in zones:
        if zone.get("type") in ("exclusion", "ligne"):
            continue
        nom = zone.get("name", "zone")
        vus = resultat.setdefault(nom, set())
        for det in detections:
            track_id = getattr(det, "track_id", None)
            if track_id is None:
                continue
            cx, cy = centre(det.bbox)
            if point_in_polygon(cx / frame_width, cy / frame_height, zone.get("polygon", [])):
                vus.add((det.model, track_id))

    return {nom: len(vus) for nom, vus in resultat.items()}
