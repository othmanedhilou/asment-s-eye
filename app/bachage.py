"""Contrôle du bâchage par absence de détection.

Le principe, proposé par l'exploitant et retenu
-----------------------------------------------
Aucun jeu de données public ne contient de camions **non bâchés** étiquetés
comme tels : ils n'apprennent qu'à reconnaître une bâche présente. On ne peut
donc pas entraîner un modèle à détecter directement l'infraction.

Mais on peut la déduire : **un camion détecté sans aucune bâche à l'intérieur de
sa boîte est un camion non bâché.** C'est le raisonnement par absence, et il est
correct — à une nuance près, qui commande toute la conception de ce module.

La nuance : l'absence est ambiguë
----------------------------------
« Aucune bâche détectée » peut signifier deux choses très différentes :

    il n'y en a pas          → infraction réelle
    on ne l'a pas vue        → contre-jour, angle, camion trop lointain

Rien ne les distingue dans le signal. Sans précaution, on obtiendrait donc une
avalanche de fausses alertes, et le contrôle serait abandonné en une semaine.

Trois garde-fous
----------------
1. **Taille minimale** — sous une certaine surface à l'image, un camion est trop
   petit pour qu'on puisse affirmer quoi que ce soit de son chargement. On se
   tait plutôt que d'affirmer.
2. **Confirmation sur plusieurs images** — une bâche manquée sur une image ne
   prouve rien. Il faut plusieurs images consécutives, du *même* camion suivi,
   sans aucune bâche. C'est la même logique que le vote sur les plaques.
3. **Le retour opérateur** — chaque alerte peut être marquée fausse d'un clic.
   Ces images alimentent le ré-entraînement, et le taux d'erreur devient
   mesurable au lieu d'être supposé.

Le troisième garde-fou est celui qui rend l'ensemble acceptable : on assume une
période de réglage, à condition de la mesurer et de la corriger.
"""

from collections import defaultdict

from app.logging_setup import setup_logging
from app.models import Detection

log = setup_logging()

# Classes du modèle. `truk_odol` / `truk_normal` viennent du jeu d'entraînement
# indonésien (Over Dimension Over Load) ; les autres couvrent les modèles futurs.
CLASSES_CAMION = {"truk_odol", "truk_normal", "truck", "camion", "overloaded", "notoverloaded"}
CLASSES_BACHE = {"tarp", "bache", "covered-trucks", "six-wheel covered-trucks"}

# Surface minimale du camion, en part de l'image. Un camion qui occupe moins de
# 3 % du cadre est trop lointain pour qu'on juge son chargement.
SURFACE_MIN = 0.03

# Images consécutives sans bâche avant de conclure. Sous ce seuil, une bâche
# simplement manquée déclencherait une alerte.
IMAGES_MIN = 3

# Part du camion que la bâche doit recouvrir pour compter. Une bâche détectée
# dans un coin de la boîte est probablement celle du camion d'à côté.
RECOUVREMENT_MIN = 0.15


def _surface(bbox) -> float:
    x1, y1, x2, y2 = bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def recouvrement(interieur, exterieur) -> float:
    """Part de `interieur` contenue dans `exterieur`.

    Volontairement asymétrique : on demande si la bâche est *dans* le camion,
    pas si les deux boîtes se ressemblent. Une bâche est plus petite que le
    camion qu'elle couvre.
    """
    ix1 = max(interieur[0], exterieur[0])
    iy1 = max(interieur[1], exterieur[1])
    ix2 = min(interieur[2], exterieur[2])
    iy2 = min(interieur[3], exterieur[3])

    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    aire = _surface(interieur)
    return intersection / aire if aire else 0.0


class ControleBachage:
    """Déduit l'absence de bâche, avec confirmation sur plusieurs images."""

    def __init__(self, camera: str, surface_min: float = SURFACE_MIN,
                 images_min: int = IMAGES_MIN, recouvrement_min: float = RECOUVREMENT_MIN):
        self.camera = camera
        self.surface_min = surface_min
        self.images_min = images_min
        self.recouvrement_min = recouvrement_min
        # identifiant de suivi -> images consécutives sans bâche
        self._sans_bache: dict = defaultdict(int)
        self._deja_signale: set = set()

    def analyser(self, detections: list, frame_width: int, frame_height: int) -> list:
        """Renvoie les détections « bâche absente » à ajouter, le cas échéant.

        Ne modifie pas `detections` : les camions et les bâches restent ce
        qu'ils sont, on ajoute seulement le constat d'infraction.
        """
        if not frame_width or not frame_height:
            return []

        camions = [d for d in detections if d.label in CLASSES_CAMION]
        baches = [d for d in detections if d.label in CLASSES_BACHE]
        if not camions:
            return []

        aire_image = frame_width * frame_height
        constats = []

        for camion in camions:
            if camion.track_id is None:
                # Sans suivi, impossible de confirmer sur plusieurs images :
                # on préfère ne rien affirmer plutôt qu'alerter sur une seule.
                continue

            if _surface(camion.bbox) / aire_image < self.surface_min:
                # Trop loin pour juger. On oublie ce qu'on avait compté : au
                # prochain passage plus près, le décompte repartira proprement.
                self._sans_bache.pop(camion.track_id, None)
                continue

            couverte = any(
                recouvrement(bache.bbox, camion.bbox) >= self.recouvrement_min
                for bache in baches
            )

            if couverte:
                self._sans_bache[camion.track_id] = 0
                continue

            self._sans_bache[camion.track_id] += 1
            if self._sans_bache[camion.track_id] < self.images_min:
                continue
            if camion.track_id in self._deja_signale:
                continue

            self._deja_signale.add(camion.track_id)
            constats.append(Detection(
                camera=self.camera,
                model="load_control",
                label="bache_absente",
                # La confiance reflète celle de la détection du camion : on est
                # sûr qu'il y a un camion, on déduit l'absence de bâche.
                confidence=camion.confidence,
                bbox=camion.bbox,
                zone=camion.zone,
                track_id=camion.track_id,
                plaque=camion.plaque,
                frame_size=(frame_width, frame_height),
            ))
            log.info(f"[{self.camera}] camion sans bâche confirmé sur "
                     f"{self.images_min} images"
                     + (f" — plaque {camion.plaque}" if camion.plaque else ""))

        return constats

    def oublier(self, track_id):
        self._sans_bache.pop(track_id, None)
        self._deja_signale.discard(track_id)
