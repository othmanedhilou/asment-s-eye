"""Ecarte les greffons OpenVINO que cette machine ne peut pas charger.

Le probleme
-----------
OpenVINO enumere TOUS ses greffons au premier appel — processeur, GPU, NPU —
avant de choisir. Sur un i5 de 2015, le greffon GPU reclame une fonction OpenCL
(`clCreateBufferWithProperties`, OpenCL 2.0) que le pilote Intel HD 5500 ne
fournit pas. Le chargeur Windows affiche alors une boite MODALE :

    The procedure entry point clCreateBufferWithProperties could not be
    located in the dynamic link library openvino_intel_gpu_plugin.dll

Elle bloque le demarrage jusqu'a ce que quelqu'un clique. Sur un serveur qui
demarre seul au boot, personne ne clique.

Deux protections, et il faut les deux
-------------------------------------
`app/__init__.py` appelle SetErrorMode : Windows fait echouer ces chargements
en silence. Cela suffit pour tout point d'entree qui importe le paquet `app`.

Ce script va plus loin en renommant les greffons inutilisables : le chargement
n'est plus tente du tout. C'est la seule protection qui vaille pour un
programme tiers — `yolo`, un carnet — qui n'importerait pas `app`.

Le projet exporte ses modeles pour le PROCESSEUR : aucun de ces greffons ne
sert. L'operation est reversible — voir --retablir.

Usage :
    python scripts/desactiver_gpu_openvino.py
    python scripts/desactiver_gpu_openvino.py --retablir
"""

import sys
from pathlib import Path

# Greffons dont ce projet n'a aucun usage : l'inference tourne sur le
# processeur, et l'export OpenVINO est fait pour lui.
INUTILISES = ("openvino_intel_gpu_plugin.dll", "openvino_intel_npu_plugin.dll")
SUFFIXE = ".inutilise"


def dossier_greffons() -> Path:
    try:
        import openvino
    except ImportError:
        raise SystemExit("openvino n'est pas installe dans cet environnement")
    return Path(openvino.__file__).resolve().parent / "libs"


def main():
    libs = dossier_greffons()
    retablir = "--retablir" in sys.argv
    agi = 0

    for nom in INUTILISES:
        actif, ecarte = libs / nom, libs / (nom + SUFFIXE)
        if retablir:
            if ecarte.exists() and not actif.exists():
                ecarte.rename(actif)
                print(f"  retabli : {nom}")
                agi += 1
        elif actif.exists():
            if ecarte.exists():
                ecarte.unlink()
            actif.rename(ecarte)
            print(f"  ecarte  : {nom}")
            agi += 1

    if not agi:
        print("  rien a faire : les greffons sont deja dans l'etat demande")

    # On verifie que le processeur repond toujours : ecarter un greffon ne doit
    # jamais coûter la detection.
    import openvino as ov

    devices = ov.Core().available_devices
    print(f"  peripheriques disponibles : {devices}")
    if "CPU" not in devices:
        raise SystemExit("ATTENTION : le processeur n'est plus disponible, "
                         "relancez avec --retablir")


if __name__ == "__main__":
    main()
