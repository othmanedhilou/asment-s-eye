"""Liste les modeles presents et les classes que chacun sait detecter.

Utile pour verifier apres un transfert de machine que les .pt sont complets,
et pour retrouver le nom exact des classes a declarer dans rules.py.

Usage : python scripts/inspect_models.py
"""

from pathlib import Path

from ultralytics import YOLO

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def main():
    pt_files = sorted(MODELS_DIR.glob("*.pt"))
    if not pt_files:
        print("⚠️ Aucun modèle trouvé dans models/")
        return

    for pt_file in pt_files:
        print(f"\n=== {pt_file.name} ===")
        try:
            model = YOLO(str(pt_file))
            print(f"✅ Chargé | Tâche : {model.task}")
            print(f"Classes ({len(model.names)}) : {model.names}")
        except Exception as e:
            print(f"❌ Échec du chargement : {e}")


if __name__ == "__main__":
    main()
