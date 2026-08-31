"""Teste un modèle Ciment's Eye sur une image ou une vidéo.

Usage:
    python scripts/run_inference.py --model epi --source chemin/vers/image.jpg
    python scripts/run_inference.py --model fire_smoke --source chemin/vers/video.mp4
    python scripts/run_inference.py --model epi --source 0   # webcam

Les modèles disponibles se déduisent des fichiers models/ciments_eye_<nom>_best.pt
Le résultat annoté est sauvegardé dans runs/detect/predict*/ par Ultralytics.
"""

import argparse
from pathlib import Path

from ultralytics import YOLO

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def resolve_model_path(name: str) -> Path:
    candidate = MODELS_DIR / f"ciments_eye_{name}_best.pt"
    if candidate.exists():
        return candidate

    direct = Path(name)
    if direct.exists():
        return direct

    available = sorted(p.stem for p in MODELS_DIR.glob("*.pt"))
    raise FileNotFoundError(
        f"Modèle '{name}' introuvable. Disponibles : {available}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="nom du modèle (ex: epi, fire_smoke) ou chemin .pt")
    parser.add_argument("--source", required=True, help="chemin image/vidéo, ou 0 pour webcam")
    parser.add_argument("--conf", type=float, default=0.25, help="seuil de confiance")
    parser.add_argument("--show", action="store_true", help="afficher le résultat dans une fenêtre")
    args = parser.parse_args()

    model_path = resolve_model_path(args.model)
    print(f"Modèle : {model_path.name}")

    model = YOLO(str(model_path))
    print(f"Classes : {model.names}")

    source = int(args.source) if args.source.isdigit() and len(args.source) == 1 else args.source

    results = model.predict(
        source=source,
        conf=args.conf,
        save=True,
        show=args.show,
    )

    for r in results:
        n_det = len(r.boxes) if r.boxes is not None else 0
        print(f"{n_det} détection(s)")
        if r.boxes is not None:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                print(f"  - {model.names[cls_id]} ({conf:.2f})")

    if results:
        save_dir = results[0].save_dir
        print(f"\nRésultat(s) sauvegardé(s) dans : {save_dir}")


if __name__ == "__main__":
    main()
