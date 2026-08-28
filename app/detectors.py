from pathlib import Path

from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ModelRegistry:
    """Charge et garde en mémoire les modèles YOLO utilisés par les caméras."""

    def __init__(self, models_config: dict):
        self._models_config = models_config
        self._loaded: dict[str, YOLO] = {}

    def get(self, name: str) -> YOLO:
        if name not in self._loaded:
            cfg = self._models_config[name]
            model_path = PROJECT_ROOT / cfg["file"]
            self._loaded[name] = YOLO(str(model_path))
        return self._loaded[name]

    def conf_threshold(self, name: str) -> float:
        return self._models_config[name].get("conf", 0.25)

    def preload(self, names: list[str]):
        for name in names:
            self.get(name)
