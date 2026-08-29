"""Enregistreur de clips vidéo autour des alertes.

Garde en mémoire les N dernières secondes de flux (pré-événement) ; quand une
alerte se déclenche, capture aussi les secondes suivantes puis écrit un MP4.
L'opérateur voit ainsi ce qui s'est passé AVANT et APRÈS la détection.
"""

from collections import deque
from datetime import datetime
from pathlib import Path

import cv2

from app.logging_setup import setup_logging
from app.storage import update_alert_clip

CLIPS_DIR = Path(__file__).resolve().parent.parent / "clips" / "videos"

log = setup_logging()


class ClipRecorder:
    def __init__(self, camera: str, fps: float = 4, pre_seconds: int = 5, post_seconds: int = 10):
        self.camera = camera
        self.fps = fps
        self._buffer: deque = deque(maxlen=max(1, int(pre_seconds * fps)))
        self._post_needed = max(1, int(post_seconds * fps))
        self._active: dict | None = None

    def add_frame(self, frame):
        """À appeler pour CHAQUE frame du flux (annotée de préférence)."""
        self._buffer.append(frame)
        if self._active is not None:
            self._active["frames"].append(frame)
            if len(self._active["frames"]) >= self._active["target"]:
                self._write()

    def trigger(self, alert_id: int):
        """Démarre l'enregistrement d'un clip pour cette alerte (si pas déjà en cours)."""
        if self._active is not None:
            return
        pre_frames = list(self._buffer)
        self._active = {
            "alert_id": alert_id,
            "frames": pre_frames,
            "target": len(pre_frames) + self._post_needed,
        }

    def _write(self):
        active = self._active
        self._active = None
        frames = active["frames"]
        if not frames:
            return
        try:
            CLIPS_DIR.mkdir(parents=True, exist_ok=True)
            h, w = frames[0].shape[:2]
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = CLIPS_DIR / f"{self.camera}_alerte{active['alert_id']}_{ts}.mp4"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h))
            for f in frames:
                writer.write(f)
            writer.release()
            update_alert_clip(active["alert_id"], str(path))
            log.info(f"[{self.camera}] clip enregistré : {path.name}")
        except Exception as e:
            log.error(f"[{self.camera}] échec enregistrement clip : {e}")
