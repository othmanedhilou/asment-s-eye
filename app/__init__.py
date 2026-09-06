"""Ciment's Eye — vidéosurveillance intelligente.

Ce fichier ne contient qu'une chose, et elle a une raison précise.

OpenVINO énumère TOUS ses greffons au premier appel — processeur, GPU, NPU —
avant de choisir. Sur cette machine, un i5 de 2015, le greffon GPU réclame une
fonction OpenCL (`clCreateBufferWithProperties`, OpenCL 2.0) que le pilote
Intel HD 5500 ne fournit pas. Le chargeur Windows affiche alors une boîte de
dialogue MODALE — « Entry Point Not Found » — qui bloque le démarrage jusqu'à
ce que quelqu'un clique. Sur un serveur qui démarre seul au boot, personne ne
clique : le logiciel reste en attente indéfiniment.

L'échec lui-même est sans conséquence : OpenVINO écarte le greffon et continue
sur le processeur, qui est de toute façon le seul qu'on utilise. Seule la boîte
de dialogue pose problème.

SetErrorMode dit à Windows de faire échouer ces chargements silencieusement au
lieu d'interroger l'utilisateur. Placé ici, il s'applique avant tout import
d'OpenVINO, quel que soit le point d'entrée — pipeline, API, script d'audit.
"""

import sys

if sys.platform == "win32":  # pragma: no cover - dépend du système
    import ctypes

    # SEM_FAILCRITICALERRORS : pas de boîte pour un chargement de DLL raté.
    # SEM_NOOPENFILEERRORBOX : pas de boîte pour un fichier introuvable.
    _SEM_FAILCRITICALERRORS = 0x0001
    _SEM_NOOPENFILEERRORBOX = 0x8000
    try:
        ctypes.windll.kernel32.SetErrorMode(
            _SEM_FAILCRITICALERRORS | _SEM_NOOPENFILEERRORBOX)
    except Exception:
        # Un environnement sans kernel32 accessible : on continue sans. Le
        # logiciel marche, il posera seulement une question de trop.
        pass
