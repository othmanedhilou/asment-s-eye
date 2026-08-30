"""Rapprochement d'un objet vu sur plusieurs caméras.

Ce que fait cette brique, et ce qu'elle ne fait pas
--------------------------------------------------
Suivre une personne ou un véhicule d'une caméra à l'autre s'appelle la
**ré-identification**. Faite correctement, elle repose sur un réseau
d'apparence dédié (type OSNet) qui produit une signature apprise, robuste au
changement d'angle et d'éclairage. C'est un modèle de plus, et cette machine
tourne déjà à quelques centaines de millisecondes par cycle sur deux cœurs.

Ce qui est implémenté ici est plus modeste, et il faut le dire clairement :
une **correspondance probable**, fondée sur quatre éléments simples et
quasi gratuits.

    la classe          une personne ne devient pas un camion
    l'apparence        histogramme de couleurs — un ouvrier en gilet orange
                       reste orange d'une caméra à l'autre
    le temps           un objet ne peut pas réapparaître dans la seconde à
                       l'autre bout du site, ni trois heures plus tard
    la topologie       si l'on déclare quelles caméras se suivent, on écarte
                       les rapprochements géographiquement impossibles

Et un cinquième, décisif quand il est disponible : **la plaque**. Deux
véhicules portant le même numéro sont le même véhicule — là où l'apparence
hésite entre deux camions blancs, la plaque tranche.

Le résultat porte donc toujours un score et se nomme « correspondance
probable ». Il ne doit jamais être présenté comme une certitude : deux ouvriers
en tenue identique produisent la même signature, et c'est une limite du
principe, pas un défaut d'implémentation.

Le jour où un modèle d'apparence est disponible, il remplace `signature()` sans
rien changer au reste.
"""

import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.logging_setup import setup_logging

log = setup_logging()

# Fenêtre pendant laquelle un objet disparu d'une caméra peut réapparaître
# ailleurs. Trop courte, on rate les trajets longs ; trop large, on rapproche
# n'importe quoi.
FENETRE_SECONDES = 120.0

# En dessous de ce score, on préfère ne rien affirmer. Un faux rapprochement
# est plus nuisible qu'une absence de rapprochement : il raconte une histoire.
SIMILARITE_MIN = 0.62


def signature(crop) -> list:
    """Signature d'apparence : histogramme de couleurs normalisé.

    En HSV plutôt qu'en RVB : la teinte résiste bien mieux aux différences
    d'éclairage entre deux caméras, ce qui est exactement le problème posé.
    """
    if crop is None or crop.size == 0:
        return []
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist, norm_type=cv2.NORM_L1)
    return hist.flatten().tolist()


def similarite(a: list, b: list) -> float:
    """Intersection d'histogrammes : 0 = rien en commun, 1 = identiques."""
    if not a or not b or len(a) != len(b):
        return 0.0
    va, vb = np.array(a), np.array(b)
    return float(np.minimum(va, vb).sum())


@dataclass
class Empreinte:
    camera: str
    track_id: int
    label: str
    global_id: int
    signature: list
    plaque: str | None = None
    vu_a: float = field(default_factory=time.monotonic)


class TrackRegistry:
    """Mémoire partagée des objets vus, toutes caméras confondues.

    Une seule instance pour tout le pipeline : les caméras tournent dans des
    threads distincts, d'où le verrou.
    """

    def __init__(self, fenetre: float = FENETRE_SECONDES,
                 seuil: float = SIMILARITE_MIN, voisins: dict | None = None):
        self.fenetre = fenetre
        self.seuil = seuil
        # {"cam1": ["cam2", "cam3"]} — si vide, toutes les caméras sont voisines
        self.voisins = voisins or {}
        self._lock = threading.Lock()
        self._empreintes: dict[tuple, Empreinte] = {}
        self._prochain_global = 1
        self.correspondances: list[dict] = []

    def _voisines(self, a: str, b: str) -> bool:
        """Deux caméras peuvent-elles se passer un objet ?"""
        if not self.voisins:
            return True          # topologie non déclarée : on n'exclut rien
        return b in self.voisins.get(a, []) or a in self.voisins.get(b, [])

    def _purger(self, maintenant: float):
        expirees = [cle for cle, e in self._empreintes.items()
                    if maintenant - e.vu_a > self.fenetre]
        for cle in expirees:
            del self._empreintes[cle]

    def observer(self, camera: str, track_id: int, label: str, crop,
                 plaque: str | None = None) -> dict:
        """Enregistre un objet vu, et cherche s'il vient d'une autre caméra.

        Renvoie son identifiant global et, le cas échéant, la correspondance
        trouvée avec son score.
        """
        maintenant = time.monotonic()
        empreinte_actuelle = signature(crop)

        with self._lock:
            self._purger(maintenant)
            cle = (camera, track_id)

            # Objet déjà connu sur cette caméra : on rafraîchit et on s'arrête.
            connu = self._empreintes.get(cle)
            if connu is not None:
                connu.vu_a = maintenant
                if empreinte_actuelle:
                    connu.signature = empreinte_actuelle
                if plaque and not connu.plaque:
                    connu.plaque = plaque
                return {"global_id": connu.global_id, "correspondance": None}

            meilleur, meilleur_score, par_plaque = None, 0.0, False
            for autre in self._empreintes.values():
                if autre.camera == camera or autre.label != label:
                    continue
                if not self._voisines(camera, autre.camera):
                    continue

                if plaque and autre.plaque:
                    # La plaque est un identifiant, pas une ressemblance. Deux
                    # numéros identiques désignent le même véhicule ; deux
                    # numéros différents l'excluent, même si les deux camions
                    # se ressemblent à s'y méprendre — c'est précisément le cas
                    # que l'apparence seule confondrait.
                    if plaque == autre.plaque:
                        meilleur, meilleur_score, par_plaque = autre, 1.0, True
                        break
                    continue

                score = similarite(empreinte_actuelle, autre.signature)
                if score > meilleur_score:
                    meilleur, meilleur_score = autre, score

            if meilleur is not None and (par_plaque or meilleur_score >= self.seuil):
                global_id = meilleur.global_id
                correspondance = {
                    "global_id": global_id,
                    "de": meilleur.camera,
                    "vers": camera,
                    "classe": label,
                    "score": round(meilleur_score, 2),
                    "plaque": plaque or meilleur.plaque,
                    # La certitude ne vient que de la plaque. Une ressemblance
                    # visuelle, même parfaite, reste une ressemblance : deux
                    # ouvriers en tenue identique produisent la même signature.
                    "certain": par_plaque,
                    "horodatage": time.time(),
                }
                self.correspondances.append(correspondance)
                del self.correspondances[:-200]
                log.info(
                    f"{'correspondance certaine' if par_plaque else 'correspondance probable'} : "
                    f"{label} de {meilleur.camera} vers {camera} "
                    + (f"(plaque {plaque})" if par_plaque else f"(score {meilleur_score:.2f})"))
            else:
                global_id = self._prochain_global
                self._prochain_global += 1
                correspondance = None

            self._empreintes[cle] = Empreinte(
                camera=camera, track_id=track_id, label=label,
                global_id=global_id, signature=empreinte_actuelle, plaque=plaque)

        return {"global_id": global_id, "correspondance": correspondance}

    def trajet(self, global_id: int) -> list:
        """Caméras traversées par un objet, dans l'ordre."""
        etapes = [c for c in self.correspondances if c["global_id"] == global_id]
        if not etapes:
            return []
        chemin = [etapes[0]["de"]]
        for etape in etapes:
            chemin.append(etape["vers"])
        return chemin

    def recentes(self, limite: int = 20) -> list:
        with self._lock:
            return list(reversed(self.correspondances[-limite:]))

    @property
    def objets_suivis(self) -> int:
        with self._lock:
            return len(self._empreintes)
