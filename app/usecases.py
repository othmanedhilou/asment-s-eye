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
        "model": "fall",
        "classes": ["down"],
        "etat": "partiel",
        "note": "Modèle dédié entraîné et en service. Il nomme trois postures — "
                "up / bending / down — et seule « down » alerte : un ouvrier penché "
                "est le quotidien d'une cimenterie, pas un incident. Éprouvé sur "
                "images inconnues : une personne seule au sol est vue (0,76 à 0,89), "
                "un homme debout ne déclenche rien. Limite mesurée : dès qu'un "
                "secouriste se penche au-dessus, il masque celui qui est à terre et "
                "l'alerte cesse. Reste à valider à la hauteur et à l'angle des "
                "caméras du site.",
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
        "model": None,
        "classes": [],
        "etat": "a_entrainer",
        "note": "Aucun modèle. Celui qui couvrait ce cas annonçait des lunettes sur "
                "des visages nus et n'en voyait pas sur des visages équipés : il a été "
                "retiré. Un cas d'usage porté par un modèle qui se trompe est plus "
                "coûteux qu'un cas ouvert. À entraîner sur les images du site.",
    },
    {
        "num": 7,
        "titre": "EPI — Gants",
        "model": None,
        "classes": [],
        "etat": "a_entrainer",
        "note": "Aucun modèle, pour la même raison que le cas 6 : les gants et les "
                "lunettes venaient du même modèle, retiré faute de fiabilité. "
                "À entraîner sur les images du site.",
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
        "note": "Chaîne complète : modèle de plaque dédié pour la localiser, lecture "
                "OCR arabe et latine, vote sur plusieurs images du même véhicule, puis "
                "consignation dans le registre des passages. La lettre de série arabe "
                "est relue à part — lue avec le reste, « و » ressortait en « 3 ». "
                "Réserve : le modèle vehicles reconnaît mal les engins de carrière "
                "(un CAT 797 n'est pas vu), à ré-entraîner sur les images du site.",
    },
    {
        "num": 10,
        "titre": "Contrôle de sortie des camions (bâchage, surcharge)",
        "model": None,
        "classes": [],
        "etat": "a_entrainer",
        "note": "Aucun modèle. Le précédent décrivait l'état d'une bâche et non la "
                "conformité d'un chargement : il ignorait la surcharge, n'avait aucune "
                "classe « conforme », et affirmait « benne vide » sur à peu près tout. "
                "Il a été retiré plutôt que conservé en l'état — un modèle qui se trompe "
                "coûte plus cher qu'un modèle absent. À entraîner sur les images du "
                "portail : bâche absente / partielle / déchirée / surcharge / conforme.",
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
