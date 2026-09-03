"""Lecture des plaques d'immatriculation.

Ce que cette brique fait, et ce qu'elle ne fait pas
--------------------------------------------------
Le modèle `vehicles` détecte des véhicules, pas des plaques. Lire du texte sur
l'image entière d'un camion produirait des numéros fantaisistes. Il faut donc
d'abord **localiser la plaque dans le véhicule**, ensuite seulement lire.

Deux voies, dans cet ordre de préférence :

1. **Un modèle de détection de plaque**, s'il est déclaré dans la configuration
   sous le nom `plate`. C'est la voie fiable. Le jour où vous entraînez
   `ciments_eye_plate_best.pt`, il est utilisé automatiquement.
2. **Une recherche par vision classique** : une plaque est un rectangle clair,
   très allongé (rapport 2:1 à 6:1), riche en contours verticaux, situé dans la
   moitié basse du véhicule. C'est l'approche d'avant l'apprentissage profond :
   elle fonctionne sur une prise de vue frontale nette, et échoue sur un angle
   marqué ou une image floue.

Le vote sur plusieurs images
----------------------------
C'est ce qui rend la lecture exploitable. Une image isolée donne un résultat
médiocre — un caractère mal lu suffit à rendre le numéro faux. Mais le suivi
d'objets fournit **plusieurs images du même véhicule** : on lit à chaque fois,
et on retient la lecture majoritaire. Dix images médiocres valent mieux qu'une
bonne, et la confiance rendue reflète l'accord entre les lectures.

Le moteur de lecture (easyocr) est **optionnel** : sans lui, le système signale
une plaque détectée sans la lire, plutôt que d'inventer un numéro.
"""

import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from app.logging_setup import setup_logging

log = setup_logging()

# Confusions classiques d'un lecteur de texte sur une plaque. On ne corrige que
# dans le sens lettre -> chiffre : sur une plaque, les caractères ambigus sont
# bien plus souvent des chiffres.
CONFUSIONS = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "S": "5", "Z": "2", "B": "8"}

LONGUEUR_MIN = 4
LONGUEUR_MAX = 10
CHIFFRES_MIN = 3

# Nombre de lectures concordantes avant de considérer un numéro comme établi.
# Largeur minimale d'une plaque pour esperer la lire.
#
# La regle du metier en lecture automatique : il faut environ 20 a 25 pixels de
# hauteur de caractere, soit une plaque d'a peu pres 120 pixels de large. En
# dessous de 90, aucun moteur ne rend autre chose que du bruit — et sur deux
# coeurs, tenter la lecture coute une seconde qu'on ne recuperera pas.
#
# On refuse donc explicitement, et on le DIT : c'est la difference entre « le
# logiciel ne marche pas » et « cette camera est trop loin du portail ».
LARGEUR_MIN_PLAQUE = 90

LECTURES_MIN = 2

# Au-delà, on cesse de lire ce véhicule. La lecture coûte ~1,4 s sur ce
# processeur : s'acharner sur un camion à l'arrêt priverait les autres caméras
# de temps de calcul sans rien apprendre de plus.
LECTURES_MAX = 8


def normaliser(texte: str) -> str:
    """Met le texte en forme de plaque : majuscules, sans séparateurs."""
    if not texte:
        return ""
    propre = "".join(c for c in texte.upper() if c.isalnum())
    return propre


def plausible(texte: str) -> bool:
    """Un numéro de plaque a une forme reconnaissable.

    Ce filtre écarte l'essentiel du bruit : un lecteur de texte trouve des mots
    partout — sur une calandre, un autocollant, un reflet. Exiger une longueur
    et une proportion de chiffres élimine ces lectures sans rien coûter.
    """
    if not (LONGUEUR_MIN <= len(texte) <= LONGUEUR_MAX):
        return False
    chiffres = sum(c.isdigit() for c in texte)
    return chiffres >= CHIFFRES_MIN


def corriger_confusions(texte: str) -> str:
    """Remplace les lettres ambiguës par leur chiffre, hors position de série.

    Les plaques marocaines suivent « chiffres - lettre - chiffres » : la lettre
    centrale est significative et ne doit pas être écrasée. On ne corrige donc
    que les extrémités.
    """
    if len(texte) < 3:
        return texte
    caracteres = list(texte)
    for i in (0, len(caracteres) - 1):
        caracteres[i] = CONFUSIONS.get(caracteres[i], caracteres[i])
    return "".join(caracteres)


def regions_candidates(crop, max_regions: int = 4) -> list:
    """Zones du véhicule susceptibles d'être une plaque, les meilleures d'abord.

    Recherche par vision classique : contraste local (chapeau noir), contours
    verticaux, puis filtrage géométrique. Utilisée seulement en l'absence d'un
    modèle de détection de plaque.
    """
    if crop is None or crop.size == 0:
        return []

    hauteur, largeur = crop.shape[:2]
    if hauteur < 20 or largeur < 40:
        return []   # trop petit pour contenir une plaque lisible

    gris = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    noyau = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 5))
    chapeau = cv2.morphologyEx(gris, cv2.MORPH_BLACKHAT, noyau)

    sobel = cv2.Sobel(chapeau, cv2.CV_32F, 1, 0, ksize=3)
    sobel = np.absolute(sobel)
    if sobel.max() > sobel.min():
        sobel = 255 * (sobel - sobel.min()) / (sobel.max() - sobel.min())
    sobel = sobel.astype("uint8")

    sobel = cv2.GaussianBlur(sobel, (5, 5), 0)
    sobel = cv2.morphologyEx(sobel, cv2.MORPH_CLOSE, noyau)
    _, seuil = cv2.threshold(sobel, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    contours, _ = cv2.findContours(seuil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidats = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h == 0 or w < 40 or h < 12:
            continue
        rapport = w / h
        if not 2.0 <= rapport <= 6.5:
            continue
        # Une plaque occupe une part modeste du véhicule, dans sa moitié basse.
        if (w * h) > 0.35 * (largeur * hauteur):
            continue
        score = (y + h / 2) / hauteur          # plus bas = plus probable
        candidats.append((score, (x, y, w, h)))

    candidats.sort(reverse=True)
    return [boite for _, boite in candidats[:max_regions]]


class PlateReader:
    """Localise et lit les plaques, avec vote sur les images successives."""

    def __init__(self, registry=None, plate_model: str | None = None,
                 lectures_min: int = LECTURES_MIN, lectures_max: int = LECTURES_MAX,
                 asynchrone: bool = True):
        self.registry = registry
        self.plate_model = plate_model
        self.lectures_min = lectures_min
        self.lectures_max = lectures_max
        self._ocr = None
        self._ocr_teste = False
        self._lock = threading.Lock()
        # (caméra, identifiant de suivi) -> lectures accumulées
        self._votes: dict[tuple, Counter] = defaultdict(Counter)
        self._scores: dict[tuple, list] = defaultdict(list)
        self._tentatives: Counter = Counter()
        # De quoi expliquer une absence de plaque, camera par camera.
        self._diagnostic: dict[str, Counter] = defaultdict(Counter)
        self._largeur_vue: dict[str, int] = {}

        # La lecture prend plus d'une seconde : exécutée dans la boucle vidéo,
        # elle figerait la caméra. Un seul fil d'exécution, et on abandonne
        # l'image plutôt que d'accumuler du retard — la suivante fera l'affaire.
        self._executor = ThreadPoolExecutor(max_workers=1) if asynchrone else None
        self._en_cours = False

    # ── Moteur de lecture ────────────────────────────────────────────

    @property
    def ocr_disponible(self) -> bool:
        self._charger_ocr()
        return self._ocr is not None

    def _charger_ocr(self):
        if self._ocr_teste:
            return
        self._ocr_teste = True
        try:
            import easyocr

            # Chargement long (modèles à télécharger la première fois) : il n'a
            # lieu qu'au premier véhicule rencontré, pas au démarrage.
            self._ocr = easyocr.Reader(["en"], gpu=False, verbose=False)
            log.info("lecture de plaques : moteur easyocr chargé")
        except ImportError:
            log.info("lecture de plaques : easyocr absent, plaques localisées mais non lues")
        except Exception as e:
            log.warning(f"lecture de plaques : moteur indisponible ({e})")

    # ── Localisation ─────────────────────────────────────────────────

    def localiser(self, crop) -> list:
        """Boîtes de plaque dans l'image d'un véhicule, en coordonnées locales."""
        if self.plate_model and self.registry is not None:
            try:
                model = self.registry.get(self.plate_model)
                resultats = model.predict(source=crop, verbose=False,
                                          conf=self.registry.conf_threshold(self.plate_model))
                boites = resultats[0].boxes
                if boites is not None and len(boites):
                    return [tuple(int(v) for v in (b.xyxy[0][0], b.xyxy[0][1],
                                                   b.xyxy[0][2] - b.xyxy[0][0],
                                                   b.xyxy[0][3] - b.xyxy[0][1]))
                            for b in boites]
            except Exception as e:
                log.error(f"modèle de plaque en échec : {e}")
        return regions_candidates(crop)

    # ── Lecture ──────────────────────────────────────────────────────

    def lire_region(self, image) -> tuple[str, float]:
        """Lit une zone de plaque. Renvoie ("", 0.0) si rien d'exploitable."""
        self._charger_ocr()
        if self._ocr is None or image is None or image.size == 0:
            return "", 0.0

        try:
            # Agrandir : les lecteurs de texte échouent sur de petits caractères,
            # et une plaque fait souvent moins de 30 pixels de haut dans la scène.
            h, w = image.shape[:2]
            if h < 48:
                facteur = 48 / max(h, 1)
                image = cv2.resize(image, (int(w * facteur), 48), interpolation=cv2.INTER_CUBIC)

            lectures = self._ocr.readtext(image, detail=1, paragraph=False)
        except Exception as e:
            log.error(f"échec de lecture de plaque : {e}")
            return "", 0.0

        # Une plaque marocaine se lit en TROIS groupes separes : le numero de
        # serie, la lettre arabe, le numero de region. easyocr en fait trois
        # boites distinctes — et ne garder que la mieux notee ne rendait que le
        # premier groupe. On les assemble dans l'ordre ou ils sont ecrits.
        groupes = []
        for boite, texte, score in lectures:
            morceau = normaliser(texte)
            if not morceau:
                continue
            try:
                xs = [float(point[0]) for point in boite]
                ys = [float(point[1]) for point in boite]
                gauche, milieu, hauteur = min(xs), sum(ys) / len(ys), max(ys) - min(ys)
            except (TypeError, IndexError, ZeroDivisionError):
                gauche, milieu, hauteur = 0.0, 0.0, 1.0
            groupes.append([gauche, milieu, hauteur, morceau, float(score)])

        if groupes:
            # Ordonner sur l'abscisse seule suffit pour une plaque d'une ligne,
            # mais met tout dans le desordre des qu'elle en a deux : le second
            # groupe de la ligne du haut se retrouve apres le premier de celle
            # du bas. On regroupe donc d'abord par ligne, puis de gauche a
            # droite dans chaque ligne — l'ordre de lecture.
            hauteur_type = sorted(g[2] for g in groupes)[len(groupes) // 2] or 1.0
            groupes.sort(key=lambda g: g[1])
            ligne, base = 0, groupes[0][1]
            for g in groupes:
                if g[1] - base > hauteur_type * 0.6:
                    ligne += 1
                    base = g[1]
                g.append(ligne)
            groupes.sort(key=lambda g: (g[5], g[0]))
            assemble = corriger_confusions("".join(g[3] for g in groupes))
            if plausible(assemble):
                # La confiance d'une plaque assemblee est celle de son maillon
                # le plus faible : un groupe mal lu suffit a la fausser.
                return assemble, min(g[4] for g in groupes)

        # Repli : la meilleure boite seule, quand l'assemblage ne tient pas.
        meilleur, meilleur_score = "", 0.0
        for _, texte, score in lectures:
            candidat = corriger_confusions(normaliser(texte))
            if plausible(candidat) and score > meilleur_score:
                meilleur, meilleur_score = candidat, float(score)
        return meilleur, meilleur_score

    def _travailler(self, camera: str, track_id, crop):
        """Localise puis lit — exécuté hors de la boucle vidéo."""
        try:
            cle = (camera, track_id)
            boites = self.localiser(crop)
            with self._lock:
                self._diagnostic[camera]["regions"] += len(boites)
                if not boites:
                    self._diagnostic[camera]["sans_region"] += 1

            for x, y, w, h in boites:
                with self._lock:
                    self._largeur_vue[camera] = max(self._largeur_vue.get(camera, 0), w)
                # Trop petite : on ne lance pas une lecture d'une seconde pour
                # obtenir du bruit, et on retient la raison.
                if w < LARGEUR_MIN_PLAQUE:
                    with self._lock:
                        self._diagnostic[camera]["trop_petite"] += 1
                    continue

                zone = crop[max(y, 0):y + h, max(x, 0):x + w]
                texte, score = self.lire_region(zone)
                with self._lock:
                    self._diagnostic[camera]["lectures"] += 1
                    if texte:
                        self._diagnostic[camera]["lues"] += 1
                        self._votes[cle][texte] += 1
                        self._scores[cle].append(score)
                    else:
                        self._diagnostic[camera]["illisibles"] += 1
        except Exception as e:
            log.error(f"lecture de plaque interrompue : {e}")
        finally:
            self._en_cours = False

    def a_lire(self, camera: str, track_id) -> bool:
        """Faut-il encore lire ce véhicule ?

        On s'arrête dès que le numéro est établi, ou après un nombre borné de
        tentatives : un camion stationné ne doit pas monopoliser le lecteur.
        """
        if track_id is None:
            return False
        cle = (camera, track_id)
        if self._tentatives[cle] >= self.lectures_max:
            return False
        return self.plaque(camera, track_id) is None

    def observer(self, camera: str, track_id, crop) -> dict | None:
        """Soumet l'image d'un véhicule suivi à la lecture de plaque.

        Ne bloque pas : la lecture se fait en arrière-plan, et le résultat
        apparaît sur une image suivante. Renvoie l'état courant de la plaque.
        """
        if track_id is None or crop is None or crop.size == 0:
            return None

        if self.a_lire(camera, track_id):
            self._tentatives[(camera, track_id)] += 1
            if self._executor is None:
                self._travailler(camera, track_id, crop)
            elif not self._en_cours:
                # Le lecteur est occupé : on saute cette image plutôt que de
                # faire la queue. Un véhicule reste visible plusieurs images.
                self._en_cours = True
                self._executor.submit(self._travailler, camera, track_id, crop.copy())

        etabli = self.plaque(camera, track_id)
        if etabli:
            return etabli
        return {"localisee": True, "texte": None, "raison": self.raison(camera)}

    def raison(self, camera: str) -> str:
        """Pourquoi aucune plaque n'a encore ete etablie sur cette camera.

        Une absence silencieuse laisse croire a une panne. Ici on nomme la
        cause, et la plus frequente n'est pas logicielle : la camera est trop
        loin, ou cadre trop large.
        """
        if not self.ocr_disponible:
            return "moteur de lecture indisponible"
        d = self._diagnostic.get(camera, Counter())
        if d["trop_petite"] and not d["lectures"]:
            vue = self._largeur_vue.get(camera, 0)
            return (f"plaque trop petite : {vue} px de large, il en faut "
                    f"{LARGEUR_MIN_PLAQUE}. Rapprochez la camera ou resserrez le cadrage.")
        if d["sans_region"] and not d["regions"]:
            return "aucune plaque reperee sur le vehicule"
        if d["illisibles"] and not d["lues"]:
            return "plaque reperee mais illisible : contre-jour, flou ou angle trop ferme"
        return "lecture en cours"

    def diagnostic(self, camera: str) -> dict:
        """Compteurs de lecture, pour l'ecran d'etat du systeme."""
        d = self._diagnostic.get(camera, Counter())
        return {
            "regions_reperees": d["regions"],
            "trop_petites": d["trop_petite"],
            "lectures_tentees": d["lectures"],
            "lectures_abouties": d["lues"],
            "illisibles": d["illisibles"],
            "largeur_max_vue": self._largeur_vue.get(camera, 0),
            "largeur_requise": LARGEUR_MIN_PLAQUE,
            "raison": self.raison(camera),
        }

    def observer_plaque(self, camera: str, track_id, zone) -> None:
        """Soumet une zone DEJA cadree sur la plaque, sans re-localiser.

        Le modele `plate` cadre serre ; refaire une localisation par traitement
        d'image dessus ne ferait que degrader ce qu'il a trouve.
        """
        if track_id is None or zone is None or zone.size == 0:
            return
        if not self.a_lire(camera, track_id):
            return

        largeur = zone.shape[1]
        with self._lock:
            self._largeur_vue[camera] = max(self._largeur_vue.get(camera, 0), largeur)
        if largeur < LARGEUR_MIN_PLAQUE:
            with self._lock:
                self._diagnostic[camera]["regions"] += 1
                self._diagnostic[camera]["trop_petite"] += 1
            return

        self._tentatives[(camera, track_id)] += 1

        def travail(image):
            try:
                texte, score = self.lire_region(image)
                with self._lock:
                    self._diagnostic[camera]["regions"] += 1
                    self._diagnostic[camera]["lectures"] += 1
                    if texte:
                        self._diagnostic[camera]["lues"] += 1
                        self._votes[(camera, track_id)][texte] += 1
                        self._scores[(camera, track_id)].append(score)
                    else:
                        self._diagnostic[camera]["illisibles"] += 1
            finally:
                self._en_cours = False

        if self._executor is None:
            self._en_cours = True
            travail(zone)
        elif not self._en_cours:
            self._en_cours = True
            self._executor.submit(travail, zone.copy())

    def plaque(self, camera: str, track_id) -> dict | None:
        """Lecture retenue pour ce véhicule, une fois assez d'images accumulées."""
        with self._lock:
            votes = self._votes.get((camera, track_id))
            if not votes:
                return None
            texte, occurrences = votes.most_common(1)[0]
            total = sum(votes.values())
        if occurrences < self.lectures_min:
            return None

        scores = self._scores.get((camera, track_id)) or [0.0]
        return {
            "localisee": True,
            "texte": texte,
            "lectures": total,
            "accord": round(occurrences / total, 2),
            "confiance": round(sum(scores) / len(scores), 2),
        }

    def oublier(self, camera: str, track_id):
        with self._lock:
            self._votes.pop((camera, track_id), None)
            self._scores.pop((camera, track_id), None)
            self._tentatives.pop((camera, track_id), None)
