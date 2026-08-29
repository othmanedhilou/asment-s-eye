from pathlib import Path

import numpy as np
from ultralytics import YOLO

from app.logging_setup import setup_logging

log = setup_logging()

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

    def warmup(self, names: list[str], imgsz: int = 640):
        """Force la compilation OpenVINO de chaque modèle, un par un.

        `YOLO(...)` ne fait que lire les métadonnées : la compilation, coûteuse
        (~5 s pour le premier modèle, ~1 s ensuite), n'a lieu qu'à la première
        inférence. Si on laisse la boucle principale la déclencher, plusieurs
        modèles compilent simultanément dans le pool de threads pendant que le
        flux caméra tourne — sur une machine à deux cœurs, la contention est
        telle que le pipeline ne démarre jamais.

        Les compiler séquentiellement avant d'entrer dans la boucle coûte une
        dizaine de secondes au démarrage, puis plus rien.
        """
        blank = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
        for name in names:
            model = self.get(name)
            try:
                model.predict(source=blank, imgsz=imgsz, verbose=False)
            except Exception as e:
                log.error(f"échec du préchauffage de {name} : {e}")
