"""Eprouve la chaine de plaque de bout en bout, comme le pipeline l'execute.

Le doute qui restait avant la mise en service n'etait pas « l'OCR sait-il
lire ? » — cela, on l'avait mesure — mais « la chaine complete ecrit-elle la
bonne plaque dans le registre ? ». Ce sont deux questions differentes : entre
les deux il y a la localisation par le modele, le decoupage, le vote sur
plusieurs images, et l'ecriture en base.

Ce script parcourt exactement le meme trajet que le pipeline :

    modele plate  ->  decoupage  ->  lire_region  ->  vote  ->  log_plate

Il rejoue la plaque marocaine de reference, « 65990 و 6 », y compris sa lettre
de serie arabe — celle qui ressortait en « 3 » tant qu'elle etait lue avec le
reste du numero.

Usage :
    python scripts/essai_plaque.py
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

VERITE = "65990و6"
POLICES = [r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\tahoma.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def police(taille):
    for chemin in POLICES:
        if Path(chemin).exists():
            return ImageFont.truetype(chemin, taille)
    raise SystemExit("aucune police capable d'ecrire l'arabe")


def fabriquer_scene() -> np.ndarray:
    """Une plaque marocaine posee sur un vehicule, comme une camera la verrait.

    La plaque seule ne suffit pas : le pipeline commence par la LOCALISER dans
    une image plus large. Une image qui ne contiendrait que la plaque
    court-circuiterait justement l'etape qu'on veut eprouver.
    """
    scene = Image.new("RGB", (1280, 720), (74, 78, 84))
    d = ImageDraw.Draw(scene)
    d.rectangle([180, 120, 1100, 560], fill=(196, 168, 46))      # carrosserie
    d.rectangle([250, 170, 1030, 330], fill=(38, 40, 44))        # pare-brise

    largeur, hauteur = 460, 118
    x0, y0 = 410, 400
    d.rectangle([x0, y0, x0 + largeur, y0 + hauteur], fill=(255, 255, 255),
                outline=(20, 20, 20), width=4)

    # L'ordre de lecture d'une plaque marocaine : numero | lettre | region.
    f_num = police(74)
    f_let = police(66)
    d.text((x0 + 28, y0 + 20), "65990", font=f_num, fill=(0, 0, 0))
    d.line([x0 + 248, y0 + 16, x0 + 248, y0 + hauteur - 16], fill=(0, 0, 0), width=4)
    d.text((x0 + 286, y0 + 22), "و", font=f_let, fill=(0, 0, 0))
    d.line([x0 + 356, y0 + 16, x0 + 356, y0 + hauteur - 16], fill=(0, 0, 0), width=4)
    d.text((x0 + 392, y0 + 20), "6", font=f_num, fill=(0, 0, 0))

    return cv2.cvtColor(np.array(scene), cv2.COLOR_RGB2BGR)


def main() -> int:
    from app.config import load_config
    from app.detectors import ModelRegistry
    from app.pipeline import _run_one_model
    from app.plates import PlateReader

    scene = fabriquer_scene()
    dossier = RACINE / "data" / "essais_plaques"
    dossier.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dossier / "reference.jpg"), scene)

    registry = ModelRegistry(load_config()["models"])
    registry.warmup(["plate"], 640)
    detections = _run_one_model(registry, "plate", scene, 640)
    print(f"1. localisation      {len(detections)} plaque(s) reperee(s)")
    if not detections:
        print("   ECHEC : le modele n'a pas trouve la plaque dans la scene.")
        return 1
    d = max(detections, key=lambda x: x.confidence)
    print(f"   confiance {d.confidence:.2f}")

    x1, y1, x2, y2 = [int(v) for v in d.bbox]
    crop = scene[y1:y2, x1:x2]

    # Le vote demande plusieurs images du meme vehicule : on rejoue la meme
    # plaque autant de fois que le pipeline en verrait pendant un passage.
    # `observer_plaque` ne rend rien : elle accumule un vote, parfois dans un
    # fil separe. Le resultat se cueille par `plaque()`, exactement comme le
    # pipeline le fait a chaque image.
    lecteur = PlateReader()
    # Charger le moteur AVANT de chronometrer : la premiere lecture d'un
    # processus met une minute a monter easyocr en memoire, et le pipeline, lui,
    # l'a deja fait depuis longtemps quand un vehicule se presente.
    lecteur._charger_ocr()
    lue = None
    for _ in range(40):
        lecteur.observer_plaque("essai_plaque", 1, crop)
        time.sleep(0.5)
        lue = lecteur.plaque("essai_plaque", 1)
        if lue and lue.get("texte"):
            break
    print(f"2. lecture           {lue}")
    if not lue or not lue.get("texte"):
        print("   ECHEC : aucune lecture retenue.")
        return 1

    texte = lue["texte"]
    exact = texte == VERITE
    print(f"3. comparaison       lu « {texte} »   attendu « {VERITE} »   "
          f"{'EXACT' if exact else 'DIFFERENT'}")

    from app.storage import log_plate, read_plates
    identifiant = log_plate(plaque=texte, camera="essai_plaque",
                            confidence=float(lue.get("confiance") or 0),
                            lectures=int(lue.get("lectures") or 0), snapshot=None)
    relu = [p for p in read_plates(limit=20) if p["camera"] == "essai_plaque"]
    print(f"4. registre          consigne #{identifiant}, relu : "
          f"{[(p['plaque'], p['confidence']) for p in relu][:3]}")

    fidele = bool(relu) and relu[0]["plaque"] == texte
    confiance_gardee = bool(relu) and bool(relu[0]["confidence"])

    # L'essai ne doit pas laisser de trace dans le registre : un passage
    # fabrique parmi de vrais passages fausserait toute lecture ulterieure.
    import sqlite3

    from app.storage import DB_PATH

    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("DELETE FROM plates WHERE camera = ?", ("essai_plaque",))
    print("5. nettoyage         passage d'essai retire du registre")

    if not fidele:
        print("   ECHEC : le registre ne rend pas ce qui a ete lu.")
        return 1
    if not confiance_gardee:
        print("   ECHEC : la confiance est retombee a zero en base.")
        return 1

    print(f"\n{'CHAINE COMPLETE OK' if exact else 'CHAINE OK, LECTURE INEXACTE'}")
    return 0 if exact else 1


if __name__ == "__main__":
    raise SystemExit(main())
