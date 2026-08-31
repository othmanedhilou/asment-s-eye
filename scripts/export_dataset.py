"""Exporte un jeu de donnees YOLO a partir des alertes reelles.

    python scripts/export_dataset.py --model epi
    python scripts/export_dataset.py --model load_control --days 90

Pourquoi cet outil existe
-------------------------
Un modele ne progresse pas en le reentrainant sur les memes donnees. Il progresse
en lui montrant ce qu'il rate et ce qu'il croit voir a tort. Or c'est exactement
ce que l'exploitation produit chaque jour :

  * les alertes marquees FAUSSES par les operateurs deviennent des images de
    fond, sans annotation. C'est ainsi qu'on apprend a un modele a repondre
    "rien ici" — precisement ce qui manque a load_control, qui affirme `empty`
    a 0,89 sur une scene de bureau parce qu'il n'a jamais vu d'image negative.

  * les alertes justes fournissent des images pre-annotees, a partir de la
    position enregistree. Il reste a verifier les boites, pas a tout tracer.

Le dossier produit se depose tel quel sur Colab ou Kaggle.
"""

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config  # noqa: E402
from app.detectors import ModelRegistry  # noqa: E402
from app.storage import read_alerts  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def classes_du_modele(model_name: str) -> list[str]:
    """Ordre des classes tel que le modele les connait — il doit etre conserve."""
    config = load_config()
    registry = ModelRegistry(config["models"])
    model = registry.get(model_name)
    names = model.names
    return [names[i] for i in sorted(names)]


def yolo_line(classe_index: int, bbox: list[float]) -> str:
    """Une boite au format YOLO : classe, centre x, centre y, largeur, hauteur,
    le tout rapporte a la taille de l'image."""
    x1, y1, x2, y2, w, h = bbox
    cx = ((x1 + x2) / 2) / w
    cy = ((y1 + y2) / 2) / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h
    return f"{classe_index} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, help="modele a enrichir (ex: epi)")
    parser.add_argument("--days", type=int, default=30, help="periode a exporter")
    parser.add_argument("--out", help="dossier de sortie")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()

    out = Path(args.out) if args.out else PROJECT_ROOT / "datasets" / f"{args.model}_{datetime.now():%Y%m%d}"
    images_dir = out / "images" / "train"
    labels_dir = out / "labels" / "train"
    a_verifier = out / "a_verifier"
    for d in (images_dir, labels_dir, a_verifier):
        d.mkdir(parents=True, exist_ok=True)

    alertes = read_alerts(limit=args.limit, model=args.model, since_hours=args.days * 24)
    if not alertes:
        print(f"Aucune alerte pour le modele '{args.model}' sur {args.days} jours.")
        print("Laissez tourner le systeme, et demandez aux operateurs de marquer")
        print("les fausses alertes : c'est ce qui alimente ce jeu de donnees.")
        return

    try:
        classes = classes_du_modele(args.model)
    except Exception as e:
        print(f"Impossible de lire les classes du modele : {e}")
        return

    index_par_classe = {nom: i for i, nom in enumerate(classes)}

    fonds = 0        # images negatives (fausses alertes)
    annotees = 0     # images pre-annotees (alertes justes)
    sans_position = 0
    manquantes = 0

    for alerte in alertes:
        snapshot = alerte.get("snapshot")
        if not snapshot or not Path(snapshot).exists():
            manquantes += 1
            continue

        nom = f"{alerte['id']:07d}.jpg"

        if alerte["false_positive"]:
            # Image de fond : copiee SANS fichier d'annotation. Un fichier vide
            # et un fichier absent ont le meme sens pour YOLO, mais l'absence
            # est la convention.
            shutil.copy2(snapshot, images_dir / nom)
            fonds += 1
            continue

        bbox = alerte.get("bbox")
        index = index_par_classe.get(alerte["label"])
        if not bbox or index is None:
            # Alertes anterieures a l'enregistrement des positions : l'image est
            # utile, mais elle demande une annotation manuelle.
            shutil.copy2(snapshot, a_verifier / nom)
            sans_position += 1
            continue

        shutil.copy2(snapshot, images_dir / nom)
        (labels_dir / f"{alerte['id']:07d}.txt").write_text(
            yolo_line(index, bbox) + "\n", encoding="utf-8")
        annotees += 1

    noms = "\n".join(f"  {i}: {nom}" for i, nom in enumerate(classes))
    (out / "data.yaml").write_text(
        f"""# Jeu de donnees issu de l'exploitation reelle de Ciment's Eye
# Modele : {args.model} — periode : {args.days} derniers jours
#
# Les images sans fichier .txt correspondant sont des IMAGES DE FOND : des
# scenes ou le modele a alerte a tort. Elles lui apprennent a se taire.
#
# Les annotations sont des PRE-ANNOTATIONS issues des detections : verifiez-les
# avant d'entrainer, un modele appris sur ses propres erreurs les repete.

path: {out.resolve().as_posix()}
train: images/train
val: images/train

names:
{noms}
""", encoding="utf-8")

    print(f"Jeu de donnees ecrit dans : {out}")
    print()
    print(f"  {annotees:5} image(s) pre-annotee(s)      (alertes jugees justes)")
    print(f"  {fonds:5} image(s) de fond            (fausses alertes -> apprend a se taire)")
    if sans_position:
        print(f"  {sans_position:5} image(s) dans a_verifier/   (position non enregistree, a annoter)")
    if manquantes:
        print(f"  {manquantes:5} snapshot(s) introuvable(s)  (purges apres 30 jours)")
    print()

    if fonds == 0:
        print("Aucune image de fond : personne n'a encore marque de fausse alerte.")
        print("C'est pourtant la partie la plus utile du jeu de donnees.")
        print()

    print("Suite, sur Colab ou Kaggle :")
    print(f"  1. deposer le dossier {out.name}/ sur le GPU")
    print("  2. verifier les pre-annotations (Roboflow, labelImg)")
    print("  3. entrainer a partir du modele actuel, pas de zero :")
    print(f"       model = YOLO('ciments_eye_{args.model}_best.pt')")
    print(f"       model.train(data='{out.name}/data.yaml', epochs=30, imgsz=640)")
    print("  4. mesurer avant/apres :  python scripts/benchmark.py compare --last")
    print("  5. reconvertir :          python scripts/export_openvino.py")


if __name__ == "__main__":
    main()
