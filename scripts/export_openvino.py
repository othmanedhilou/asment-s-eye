"""Convertit tous les modèles .pt en OpenVINO (accélération CPU) pour le pipeline."""

from pathlib import Path

from ultralytics import YOLO

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

MODEL_NAMES = [
    "arc",
    "conveyor",
    "epi",
    "fall",
    "fire_smoke",
    "gloves_glasses",
    "load_control",
    "person_animal",
    # Localisation des plaques. Sans lui, la localisation se fait par
    # traitement d'image classique, qui propose beaucoup de faux candidats —
    # et chacun coute une seconde d'OCR sur deux coeurs.
    "plate",
    "vehicles",
]


def main():
    for name in MODEL_NAMES:
        pt_path = MODELS_DIR / f"ciments_eye_{name}_best.pt"
        out_dir = MODELS_DIR / f"ciments_eye_{name}_best_openvino_model"
        if out_dir.exists():
            print(f"[{name}] déjà converti, skip")
            continue
        # Tous les modèles déclarés ne sont pas encore entraînés — la chute et
        # les plaques restent à faire. Le script doit convertir ce qu'il a et
        # nommer ce qui manque, pas s'arrêter au premier absent.
        if not pt_path.exists():
            print(f"[{name}] absent ({pt_path.name}) — à entraîner, ignoré")
            continue
        print(f"[{name}] conversion en cours...")
        model = YOLO(str(pt_path))
        model.export(format="openvino", imgsz=640)
        print(f"[{name}] OK -> {out_dir}")


if __name__ == "__main__":
    main()
