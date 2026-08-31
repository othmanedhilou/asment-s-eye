import time

from app.models import Alert, Detection
from app.settings import is_alert_enabled
from app.storage import severity_for

ALERT_LABELS = {
    "arc": {"Arc Flash", "Sparks"},
    "conveyor": {"crack"},
    "epi": {"NO-Hardhat", "NO-Mask", "NO-Safety Vest"},
    # Modèle de chute dédié. « down » vient du jeu d'entraînement retenu, où les
    # trois postures sont nommées up / bending / down. Seule la dernière alerte :
    # « bending » (penché) est justement ce que le modèle actuel confond avec une
    # chute, et c'est pour l'en distinguer qu'on l'entraîne.
    # « falling » alerte aussi quand le modèle le distingue : secourir pendant la
    # chute vaut mieux qu'après.
    "fall": {"fallen", "falling", "down"},
    "fire_smoke": {"Fire", "Smoke"},
    "gloves_glasses": {"NO-Gloves", "NO-Goggles", "Fall-Detected"},
    # Contrôle de sortie des camions. Les deux premières classes viennent du
    # modèle actuel ; les suivantes sont celles du modèle cible, entraîné sur le
    # portail. Déclarer les deux permet de remplacer le modèle sans toucher au
    # code — et « conforme » n'y figure pas : un camion en règle ne doit rien
    # déclencher, c'est précisément la classe qui manque au modèle actuel.
    # « truk_odol » vient du jeu d'entraînement retenu : Over Dimension Over
    # Load, le terme réglementaire indonésien pour un camion hors gabarit ou
    # surchargé. L'orthographe varie selon la version du jeu — « truk_odol »
    # dans la v9 téléchargée, « truck_odol » sur la fiche du dépôt. Les deux
    # sont déclarées : une faute ici et aucune détection ne serait reconnue,
    # sans le moindre message d'erreur.
    # « truk_normal » n'y figure pas : un camion conforme ne doit rien
    # déclencher. Idem pour « roda », les roues, annotées pour compter les essieux.
    # « empty » (benne vide) est retiré : une benne vide n'est pas une
    # infraction, et le modèle actuel la voyait partout — c'était la première
    # source de fausses alertes.
    "load_control": {"torn", "truk_odol", "truck_odol", "overloaded",
                     "bache_absente", "bache_partielle", "bache_dechiree", "surcharge"},
    "person_animal": {"person", "animal"},
    "vehicles": {"car", "truck", "bus", "motorcycle", "bicycle"},
}

# Seuils de confiance renforcés pour les classes sujettes aux faux positifs
# (modèles entraînés sur peu de données pour ces classes précises).
# À retirer une fois ces modèles ré-entraînés avec un dataset plus riche.
# Libellés fusionnés en une seule alerte.
#
# Le modèle distingue mal la flamme de la fumée, et l'essai en conditions
# réelles l'a montré nettement : une flamme de briquet, saturée par le capteur
# en un halo blanc-gris, a été annoncée « Smoke 0,57 » — avec plus d'assurance
# que le « Fire 0,49 » de la même scène.
#
# Deux consequences, dont la seconde est la pire. D'abord l'interface affirmait
# quelque chose de faux. Ensuite, comme ce sont deux libellés différents, le
# verrou anti-répétition ne s'applique pas de l'un à l'autre : UNE flamme a
# produit QUATRE alertes.
#
# On fusionne. La distinction n'a de toute façon aucune consequence pratique :
# feu ou fumée, quelqu'un va voir immédiatement. Mieux vaut dire vrai et large
# que precis et faux.
LABELS_FUSIONNES = {
    ("fire_smoke", "Fire"): "feu ou fumée",
    ("fire_smoke", "Smoke"): "feu ou fumée",
}


# Combien de fois un objet doit avoir été vu avant de pouvoir déclencher.
# À deux images par seconde, trois images font une seconde et demie : assez
# pour écarter le clignotement, assez court pour ne pas rater un passage.
MIN_VUES_AVANT_ALERTE = 3

MIN_CONFIDENCE_OVERRIDE = {
    ("gloves_glasses", "Fall-Detected"): 0.80,
    # La classe la plus faible du modèle EPI, et la moins pertinente pour une
    # cimenterie : elle produisait l'essentiel des fausses alertes.
    ("epi", "NO-Mask"): 0.55,
    ("load_control", "torn"): 0.75,
    # Une webcam de bureau braquée sur un visage n'a aucune raison de produire
    # une voiture ou une fissure de convoyeur. Ces classes-là ne sortent d'un
    # modèle mal cadré que sous 60 % de confiance : on les exige franches.
    ("vehicles", "car"): 0.60,
    ("vehicles", "truck"): 0.60,
    ("vehicles", "bus"): 0.60,
    ("vehicles", "motorcycle"): 0.60,
    ("vehicles", "bicycle"): 0.60,
    ("person_animal", "animal"): 0.60,
    ("conveyor", "crack"): 0.60,
    ("arc", "Sparks"): 0.60,
}


# Délai minimal entre deux alertes identiques (même caméra, modèle, classe).
# Une situation persistante — un ouvrier sans casque qui reste en poste — ne doit
# pas générer une alerte par image, sinon les opérateurs ignorent le système.
# Les cas critiques sont rappelés plus souvent que les cas de routine.
COOLDOWN_BY_SEVERITY = {
    "critique": 60,      # 1 min
    "haute": 300,        # 5 min
    "moyenne": 900,      # 15 min
    "technique": 600,    # 10 min — une panne persiste, inutile de la répéter
}


_chute_dediee: bool | None = None


def _modele_chute_dedie_actif() -> bool:
    """Le modèle `fall` est-il déclaré et activé ?

    Lu une fois : activer un modèle demande de toute façon un redémarrage du
    pipeline, puisqu'il faut le charger.
    """
    global _chute_dediee
    if _chute_dediee is None:
        try:
            from app.config import load_config

            _chute_dediee = bool(load_config()["models"].get("fall", {}).get("enabled"))
        except Exception:
            _chute_dediee = False
    return _chute_dediee


class AlertEngine:
    """Filtre les détections brutes et déclenche une alerte avec cooldown par (caméra, modèle, label)."""

    def __init__(self, cooldown_seconds: float | None = None, on_alert=None):
        # cooldown_seconds force un délai unique ; sinon délai selon la sévérité
        self.cooldown_seconds = cooldown_seconds
        self.on_alert = on_alert
        self._last_alert: dict[tuple[str, str, str], float] = {}

    # Une piste par personne et par passage : sur une journée de trafic, la
    # table grossit sans jamais rétrécir. On la borne.
    MEMOIRE_MAX = 20_000

    def _oublier_les_vieilles(self):
        if len(self._last_alert) <= self.MEMOIRE_MAX:
            return
        ordre = sorted(self._last_alert.items(), key=lambda kv: kv[1])
        for cle, _ in ordre[: len(ordre) // 4]:
            del self._last_alert[cle]

    def _cooldown_for(self, model: str, label: str, zone_cooldown: float | None = None) -> float:
        if self.cooldown_seconds is not None:
            return self.cooldown_seconds
        # Un délai propre à la zone prime : une zone de passage n'a pas le même
        # rythme acceptable qu'un local technique désert.
        if zone_cooldown is not None:
            return zone_cooldown
        return COOLDOWN_BY_SEVERITY.get(severity_for(model, label), 300)

    def process(self, detection: Detection, frame=None):
        labels = ALERT_LABELS.get(detection.model)
        if labels is None or detection.label not in labels:
            return None

        # Tant que le modèle de chute dédié n'existe pas, la chute est assurée
        # par gloves_glasses. Dès qu'il est activé, cette classe se tait : sans
        # cela, une même personne au sol déclencherait deux alertes, et les
        # opérateurs perdraient confiance dans le décompte.
        if detection.model == "gloves_glasses" and detection.label == "Fall-Detected"                 and _modele_chute_dedie_actif():
            return None

        if not is_alert_enabled(detection.model):
            return None

        # Une apparition fugace n'est pas une observation. Tant que la caméra
        # suit ses objets, on attend d'avoir vu celui-ci plusieurs fois : c'est
        # ce qui distingue un ouvrier présent d'un scintillement du modèle.
        if detection.track_id is not None \
                and detection.track_hits < MIN_VUES_AVANT_ALERTE:
            return None

        min_conf = MIN_CONFIDENCE_OVERRIDE.get((detection.model, detection.label))
        if min_conf is not None and detection.confidence < min_conf:
            return None

        # Seuil propre à la zone : on peut exiger plus de certitude là où le
        # modèle se trompe souvent, sans durcir toute la caméra.
        if detection.zone_conf is not None and detection.confidence < detection.zone_conf:
            return None

        # La zone fait partie de la cle : un vehicule sur le quai et un autre
        # devant l'atelier sont deux situations distinctes, chacune doit alerter.
        #
        # L'identifiant de suivi aussi, quand la camera l'active : sans lui, un
        # deuxieme ouvrier sans casque reste masque pendant cinq minutes par
        # l'alerte du premier. Avec lui, chaque personne alerte une fois.
        now = time.monotonic()

        # DEUX verrous, et il faut les deux.
        #
        # Le premier tient à l'objet : cet ouvrier-là, sans casque, se signale
        # UNE fois tant que sa piste vit. C'est ce que le suivi permet.
        #
        # Le second tient au libellé, sans regarder la piste. Il est
        # indispensable parce que le suivi n'est pas infaillible : mesuré sur
        # cette machine, le cycle tombe à 2,7 s quand deux modèles tournent, et
        # à cette cadence un objet qui bouge n'est plus rapproché du précédent.
        # Il repart alors avec une identité neuve — donc, sans ce second verrou,
        # une alerte neuve. C'est exactement ce qui produisait sept « gilet
        # absent » en trois minutes.
        #
        # Le prix à payer : deux ouvriers sans casque dans la même minute ne
        # font qu'une alerte. C'est le compromis qu'ont tous les VMS, et il vaut
        # mieux qu'une colonne d'alertes que plus personne ne lit.
        # Le libellé annoncé peut regrouper plusieurs classes du modèle. Il sert
        # aussi de clé : sans cela, deux noms pour la même chose contournent le
        # verrou anti-répétition.
        libelle = LABELS_FUSIONNES.get((detection.model, detection.label),
                                       detection.label)
        cle_objet = (detection.camera, detection.zone, detection.model,
                     libelle, detection.track_id)
        cle_libelle = (detection.camera, detection.zone, detection.model,
                       libelle, None)

        if detection.track_id is not None and cle_objet in self._last_alert:
            return None

        dernier = self._last_alert.get(cle_libelle, 0.0)
        if now - dernier < self._cooldown_for(detection.model, libelle,
                                              detection.zone_cooldown):
            return None

        self._last_alert[cle_objet] = now
        self._last_alert[cle_libelle] = now
        self._oublier_les_vieilles()
        where = f" dans {detection.zone}" if detection.zone else ""
        alert = Alert(
            camera=detection.camera,
            model=detection.model,
            label=libelle,
            confidence=detection.confidence,
            zone=detection.zone,
            bbox=detection.bbox,
            frame_size=detection.frame_size,
            plaque=detection.plaque,
            message=f"{libelle} détecté sur {detection.camera}{where} "
                    f"(confiance {detection.confidence:.2f})",
        )
        if self.on_alert:
            self.on_alert(alert, frame)
        return alert
