import time

from app.models import Alert, Detection
from app.settings import is_alert_enabled
from app.storage import severity_for

ALERT_LABELS = {
    "arc": {"Arc Flash", "Sparks"},
    "conveyor": {"crack"},
    "epi": {"NO-Hardhat", "NO-Mask", "NO-Safety Vest"},
    # Modèle de chute dédié. « falling » (en train de tomber) alerte aussi :
    # secourir pendant la chute vaut mieux qu'après.
    "fall": {"fallen", "falling"},
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
    "load_control": {"torn", "empty", "truk_odol", "truck_odol", "overloaded",
                     "bache_absente", "bache_partielle", "bache_dechiree", "surcharge"},
    "person_animal": {"person", "animal"},
    "vehicles": {"car", "truck", "bus", "motorcycle", "bicycle"},
}

# Seuils de confiance renforcés pour les classes sujettes aux faux positifs
# (modèles entraînés sur peu de données pour ces classes précises).
# À retirer une fois ces modèles ré-entraînés avec un dataset plus riche.
MIN_CONFIDENCE_OVERRIDE = {
    ("gloves_glasses", "Fall-Detected"): 0.80,
    ("load_control", "torn"): 0.75,
    ("load_control", "empty"): 0.75,
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
        key = (detection.camera, detection.zone, detection.model, detection.label,
               detection.track_id)
        now = time.monotonic()
        last = self._last_alert.get(key, 0.0)
        if now - last < self._cooldown_for(detection.model, detection.label,
                                           detection.zone_cooldown):
            return None

        self._last_alert[key] = now
        where = f" dans {detection.zone}" if detection.zone else ""
        alert = Alert(
            camera=detection.camera,
            model=detection.model,
            label=detection.label,
            confidence=detection.confidence,
            zone=detection.zone,
            bbox=detection.bbox,
            frame_size=detection.frame_size,
            plaque=detection.plaque,
            message=f"{detection.label} détecté sur {detection.camera}{where} "
                    f"(confiance {detection.confidence:.2f})",
        )
        if self.on_alert:
            self.on_alert(alert, frame)
        return alert
