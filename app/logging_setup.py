"""Journalisation du système.

Jusqu'ici tout passait par `print()` : rien n'était conservé. Après un incident
nocturne — flux perdu, modèle qui plante, alerte non partie — il ne restait
aucune trace à analyser le lendemain.

Écrit à la fois sur la console (exploitation en direct) et dans `logs/`, avec
rotation quotidienne et rétention de 30 jours pour que le disque ne se remplisse
pas silencieusement.
"""

import os
import sys
from pathlib import Path

from loguru import logger

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

_configured = False


def setup_logging(name: str = "ciments_eye", level: str | None = None):
    """À appeler une fois au démarrage de chaque processus (pipeline, API).

    Le niveau se règle par la variable d'environnement CIMENTS_EYE_LOG_LEVEL.
    En DEBUG, chaque détection est tracée — utile pour régler des seuils ou
    comprendre pourquoi une zone ne déclenche pas, trop verbeux en exploitation.
    """
    global _configured
    if _configured:
        return logger

    level = level or os.getenv("CIMENTS_EYE_LOG_LEVEL", "INFO").upper()

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()  # retire le handler par défaut de loguru

    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    logger.add(
        LOGS_DIR / f"{name}_{{time:YYYY-MM-DD}}.log",
        level=level,
        rotation="00:00",          # un fichier par jour
        retention="30 days",       # au-delà, purgé automatiquement
        encoding="utf-8",
        enqueue=True,              # sûr entre les threads des caméras
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    )

    _configured = True
    return logger
