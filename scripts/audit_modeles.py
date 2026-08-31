"""Verifie que chaque modele fonctionne reellement, pas seulement qu'il existe.

`inspect_models.py` liste les fichiers et leurs classes. Ce script va plus
loin : il charge chaque modele OpenVINO comme le fait le pipeline, mesure le
temps d'inference, et le fait tourner sur une image reelle pour verifier qu'il
rend des detections plausibles.

C'est la difference entre « le fichier est la » et « le modele marche ».

Usage :
    python scripts/audit_modeles.py                    # sur la derniere image live
    python scripts/audit_modeles.py chemin/image.jpg   # sur une image donnee
"""

import os
import sys
import time
from pathlib import Path

# Avant tout import OpenVINO : sans cela, chaque modele reclame autant de fils
# qu'il y a de coeurs et ils se genent mutuellement.
os.environ.setdefault("OMP_NUM_THREADS", "1")

import cv2  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from app.config import load_config  # noqa: E402
from app.detectors import ModelRegistry  # noqa: E402
from app.rules import ALERT_LABELS, MIN_CONFIDENCE_OVERRIDE  # noqa: E402


def image_de_test(argument: str | None):
    if argument:
        img = cv2.imread(argument)
        if img is None:
            raise SystemExit(f"image illisible : {argument}")
        return img, argument

    live = sorted((RACINE / "data" / "live").glob("*.jpg"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if live:
        img = cv2.imread(str(live[0]))
        if img is not None:
            return img, live[0].name
    raise SystemExit("aucune image live disponible — donnez un chemin en argument")


def main():
    image, origine = image_de_test(sys.argv[1] if len(sys.argv) > 1 else None)
    h, w = image.shape[:2]
    print(f"image d'essai : {origine} ({w}x{h})\n")

    config = load_config()["models"]
    registry = ModelRegistry(config)

    largeur = max(len(n) for n in config)
    print(f"{'modele':<{largeur}}  {'fichier':<8} {'charge':<7} {'ms':>6}  classes / detections")
    print("-" * (largeur + 62))

    sains = defauts = 0
    for nom, cfg in sorted(config.items()):
        chemin = RACINE / cfg["file"]
        present = chemin.exists()
        if not present:
            print(f"{nom:<{largeur}}  {'ABSENT':<8} {'-':<7} {'-':>6}  a entrainer")
            defauts += 1
            continue

        try:
            debut = time.monotonic()
            modele = registry.get(nom)
            modele.predict(image, imgsz=640, conf=0.05, verbose=False)   # prechauffage
            charge_ms = (time.monotonic() - debut) * 1000

            debut = time.monotonic()
            resultat = modele.predict(image, imgsz=640,
                                      conf=cfg.get("conf", 0.4), verbose=False)[0]
            infer_ms = (time.monotonic() - debut) * 1000
        except Exception as e:
            print(f"{nom:<{largeur}}  {'present':<8} {'ECHEC':<7} {'-':>6}  {e}")
            defauts += 1
            continue

        classes = list(resultat.names.values())
        trouve = [f"{resultat.names[int(b.cls[0])]} {float(b.conf[0]):.2f}"
                  for b in (resultat.boxes or [])]

        # Les classes declarees dans rules.py doivent exister dans le modele,
        # sinon l'alerte ne partira jamais et rien ne le signalera.
        declarees = ALERT_LABELS.get(nom, set())
        fantomes = sorted(declarees - set(classes))

        print(f"{nom:<{largeur}}  {'present':<8} {'ok':<7} {infer_ms:>6.0f}  "
              f"{len(classes)} classes : {', '.join(classes[:6])}"
              + (" …" if len(classes) > 6 else ""))
        print(f"{'':<{largeur}}  {'':<8} {'':<7} {'':>6}  "
              f"sur cette image : {', '.join(trouve) if trouve else 'rien'}")
        if fantomes:
            print(f"{'':<{largeur}}  {'':<8} {'':<7} {'':>6}  "
                  f"⚠ classes declarees absentes du modele : {', '.join(fantomes)}")
            defauts += 1
        else:
            sains += 1

        seuils = {lab: v for (m, lab), v in MIN_CONFIDENCE_OVERRIDE.items() if m == nom}
        if seuils:
            print(f"{'':<{largeur}}  {'':<8} {'':<7} {'':>6}  "
                  f"seuils renforces : {seuils}")
        print(f"{'':<{largeur}}  {'':<8} {'':<7} {'':>6}  chargement {charge_ms:.0f} ms")

    print(f"\n{sains} modele(s) sains, {defauts} a regarder")


if __name__ == "__main__":
    main()
