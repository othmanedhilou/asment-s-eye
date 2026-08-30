"""Registre des 12 cas d'usage du cahier des charges (section 3.2),
mappés sur les modèles réellement entraînés.

Un modèle physique peut couvrir plusieurs cas d'usage (ex: fire_smoke couvre
Fumée + Feu ; epi couvre Casque + Gilet + Masque).
"""

from app.settings import load_settings

# (numéro CDC, titre, modèle physique, classes concernées, état)
# état : "operationnel" | "partiel" | "a_entrainer"
USE_CASES = [
    {
        "num": 1,
        "titre": "Détection fumée",
        "model": "fire_smoke",
        "classes": ["Smoke"],
        "etat": "operationnel",
        "note": "",
    },
    {
        "num": 2,
        "titre": "Détection feu / flamme",
        "model": "fire_smoke",
        "classes": ["Fire"],
        "etat": "operationnel",
        "note": "",
    },
    {
        "num": 3,
        "titre": "Personne KO / horizontale",
        "model": "gloves_glasses",
        "classes": ["Fall-Detected"],
        "etat": "partiel",
        "note": "Fiabilité limitée — dataset dédié à entraîner (seuil renforcé à 0.80)",
    },
    {
        "num": 4,
        "titre": "EPI — Casque",
        "model": "epi",
        "classes": ["Hardhat", "NO-Hardhat"],
        "etat": "operationnel",
        "note": "Rappel NO-Hardhat ~54% — à renforcer avec images du site",
    },
    {
        "num": 5,
        "titre": "EPI — Gilet de sécurité",
        "model": "epi",
        "classes": ["Safety Vest", "NO-Safety Vest"],
        "etat": "operationnel",
        "note": "",
    },
    {
        "num": 6,
        "titre": "EPI — Lunettes sécurité",
        "model": "gloves_glasses",
        "classes": ["Goggles", "NO-Goggles"],
        "etat": "operationnel",
        "note": "",
    },
    {
        "num": 7,
        "titre": "EPI — Gants",
        "model": "gloves_glasses",
        "classes": ["Gloves", "NO-Gloves"],
        "etat": "operationnel",
        "note": "",
    },
    {
        "num": 8,
        "titre": "Détection personne & animal",
        "model": "person_animal",
        "classes": ["person", "animal"],
        "etat": "operationnel",
        "note": "",
    },
    {
        "num": 9,
        "titre": "Véhicule + matriculation",
        "model": "vehicles",
        "classes": ["car", "truck", "bus", "motorcycle", "bicycle"],
        "etat": "partiel",
        "note": "Véhicules détectés ; plaques lues par vote sur plusieurs images "
                "(suivi requis). Localisation par vision classique : un modèle de "
                "plaque dédié améliorerait nettement le taux de lecture.",
    },
    {
        "num": 10,
        "titre": "Contrôle de sortie des camions (bâchage, surcharge)",
        "model": "load_control",
        "classes": ["intact", "torn", "empty"],
        "etat": "a_entrainer",
        "note": "Le modèle actuel décrit l'état d'une bâche, pas la conformité d'un "
                "chargement : il ignore la surcharge et n'a aucune classe « conforme », "
                "d'où ses affirmations hors contexte. À remplacer par un modèle entraîné "
                "sur les images du portail (bâche absente / partielle / déchirée / "
                "surcharge / conforme) — voir docs/GUIDE_REENTRAINEMENT.md",
    },
    {
        "num": 11,
        "titre": "Arc électrique",
        "model": "arc",
        "classes": ["Arc Flash", "Sparks"],
        "etat": "operationnel",
        "note": "",
    },
    {
        "num": 12,
        "titre": "Surveillance convoyeur",
        "model": "conveyor",
        "classes": ["crack"],
        "etat": "partiel",
        "note": "Modèle à classe unique (crack) — jamais validé sur images du site",
    },
]


def usecases_with_status() -> list[dict]:
    """Enrichit chaque cas d'usage avec l'état détection/alerte en direct."""
    settings = load_settings()
    result = []
    for uc in USE_CASES:
        entry = dict(uc)
        if uc["model"] and uc["model"] in settings:
            entry["detect"] = settings[uc["model"]]["detect"]
            entry["alert"] = settings[uc["model"]]["alert"]
        else:
            entry["detect"] = False
            entry["alert"] = False
        result.append(entry)
    return result
