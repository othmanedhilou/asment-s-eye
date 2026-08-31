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
    "vehicles",
]


def main():
    for name in MODEL_NAMES:
        pt_path = MODELS_DIR / f"ciments_eye_{name}_best.pt"
        out_dir = MODELS_DIR / f"ciments_eye_{name}_best_openvino_model"
        if out_dir.exists():
            print(f"[{name}] déjà converti, skip")
            continue
        print(f"[{name}] conversion en cours...")
        model = YOLO(str(pt_path))
        model.export(format="openvino", imgsz=640)
        print(f"[{name}] OK -> {out_dir}")


if __name__ == "__main__":
    main()
