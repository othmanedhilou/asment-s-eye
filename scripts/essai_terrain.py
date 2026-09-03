"""Eprouve chaque modele sur des images reelles qu'il n'a jamais vues.

`audit_modeles.py` verifie que les modeles chargent et tournent.
Celui-ci repond a une autre question, la seule qui compte avant une mise en
service : est-ce qu'ils VOIENT juste, sur des images venues d'ailleurs que de
leur propre jeu d'entrainement ?

Les images sont prises sur Wikimedia Commons, par recherche thematique. Aucune
n'a servi a l'entrainement : un modele qui reussit sur son jeu de validation et
echoue ici ne generalisera pas davantage sur le site.

CE QUE CET ESSAI NE MESURE PAS
------------------------------
Il ne vaut que pour les modeles dont le sujet apparait dans des photographies
ordinaires : equipements de protection, incendie, personnes, soudure. Ceux-la
ont ete eprouves pour de bon.

Il ne vaut PAS pour les modeles entraines sur un cadrage etroit et specifique.
Le modele de convoyeur vient de BeltCrack : des gros plans de la SURFACE de la
bande. Le juger sur une photo de galerie de convoyeur traversant un champ, ou
la bande n'est meme pas visible, ne dit rien de lui. Meme chose pour le
controle de chargement, entraine sur des vues de benne prises au portail.

Pour ceux-la, le seul essai valable se fait sur VOTRE camera, cadree comme elle
le sera en exploitation.

Ce que l'essai revele quand meme sur eux : faute de classe « rien a signaler »,
ils rendent une detection sur a peu pres n'importe quoi — le modele de
convoyeur a annonce « crack 0,52 » sur un visage humain, puis sur un diagramme
de circulation oceanique intitule « ocean conveyor belt ». D'ou une regle
d'exploitation : n'activer un modele que sur une camera qui regarde son sujet.

Deux precautions apprises a l'usage :
  - le Python de la machine peut avoir des certificats expires ; on passe par
    curl, qui a son propre magasin
  - Wikimedia limite les requetes rapprochees et repond alors du vide, sans
    message d'erreur. D'ou la pause entre chaque appel.

Usage :
    python scripts/essai_terrain.py            # telecharge puis evalue
    python scripts/essai_terrain.py --evalue   # reutilise les images deja la
"""

import json
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import cv2  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from app.config import load_config  # noqa: E402
from app.detectors import ModelRegistry  # noqa: E402
from app.pipeline import _run_one_model  # noqa: E402
from app.rules import ALERT_LABELS  # noqa: E402

DEST = RACINE / "data" / "essais_terrain"
UA = "CimentsEye-audit/1.0 (essai local de modeles de vision)"
PAUSE = 3

# Pour chaque modele, des scenes ou il DOIT voir quelque chose, et des scenes
# ou il ne doit RIEN voir. Le second cas compte autant que le premier : un
# modele qui alerte sur tout ne sert a rien.
SCENES = {
    "epi": ["construction worker safety helmet vest",
            "construction workers without helmets"],
    "fire_smoke": ["industrial fire", "wildfire smoke plume"],
    "person_animal": ["people walking city street", "dog outdoors"],
    "vehicles": ["dump truck construction site", "heavy truck highway"],
    "arc": ["arc welding sparks", "welder shielded metal arc welding"],
    "conveyor": ["conveyor belt quarry", "damaged conveyor belt"],
    "load_control": ["truck covered tarpaulin cargo", "overloaded truck"],
}


def curl(url: str, binaire: bool = False):
    time.sleep(PAUSE)
    r = subprocess.run(["curl", "-s", "--max-time", "60", "-A", UA, url],
                       capture_output=True)
    return r.stdout if binaire else r.stdout.decode("utf-8", "replace")


def telecharger():
    total = 0
    for modele, termes in SCENES.items():
        dossier = DEST / modele
        dossier.mkdir(parents=True, exist_ok=True)
        n = len(list(dossier.glob("*.jpg")))
        for terme in termes:
            if n >= 4:
                break
            url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
                   f"&generator=search&gsrnamespace=6&gsrlimit=5"
                   f"&gsrsearch={urllib.parse.quote(terme)}"
                   "&prop=imageinfo&iiprop=url%7Cmime&iiurlwidth=1100")
            try:
                pages = json.loads(curl(url)).get("query", {}).get("pages", {})
            except json.JSONDecodeError:
                print(f"  {modele} : reponse illisible pour « {terme} »")
                continue
            for page in pages.values():
                if n >= 4:
                    break
                info = page.get("imageinfo", [{}])[0]
                lien = info.get("thumburl") or info.get("url", "")
                if not lien or not info.get("mime", "").startswith("image/"):
                    continue
                if lien.lower().endswith((".svg", ".tif", ".tiff")):
                    continue
                octets = curl(lien, binaire=True)
                if len(octets) < 15000:
                    continue
                (dossier / f"{n:02d}.jpg").write_bytes(octets)
                (dossier / f"{n:02d}.txt").write_text(page["title"][5:90], encoding="utf-8")
                n += 1
                total += 1
        print(f"  {modele:<16} {n} image(s)")
    print(f"\n{total} image(s) ajoutee(s) dans {DEST}")


def evaluer():
    registry = ModelRegistry(load_config()["models"])
    for modele in SCENES:
        images = sorted((DEST / modele).glob("*.jpg"))
        if not images:
            print(f"\n### {modele} — aucune image")
            continue
        try:
            registry.warmup([modele], 640)
        except Exception as e:
            print(f"\n### {modele} — chargement impossible : {e}")
            continue
        alerte = ", ".join(sorted(ALERT_LABELS.get(modele, []))[:6]) or "aucune"
        print(f"\n### {modele}   (alerte si : {alerte})")
        for chemin in images:
            legende = chemin.with_suffix(".txt")
            titre = legende.read_text(encoding="utf-8") if legende.exists() else chemin.name
            image = cv2.imread(str(chemin))
            if image is None:
                print(f"  {titre[:54]:<54} image illisible")
                continue
            dets = _run_one_model(registry, modele, image, 640)
            vus = ", ".join(f"{d.label} {d.confidence:.2f}" for d in dets[:6]) or "RIEN"
            print(f"  {titre[:54]:<54} -> {vus}")


if __name__ == "__main__":
    if "--evalue" not in sys.argv:
        print("Telechargement des images d'essai...")
        telecharger()
    evaluer()
