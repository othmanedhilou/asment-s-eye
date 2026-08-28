import os

# Doit être défini avant tout chargement d'OpenVINO : sinon chaque modèle utilise
# tous les cœurs en interne, et les exécuter en parallèle sature le CPU (thrashing).
os.environ.setdefault("OMP_NUM_THREADS", "1")

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from app.capture import RTSPStream
from app.config import load_config
from app.detectors import ModelRegistry
from app.models import Detection
from app.recorder import ClipRecorder
from app.settings import is_detect_enabled

LIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "live"


def _run_one_model(registry: ModelRegistry, model_name: str, frame, imgsz: int) -> list[Detection]:
    model = registry.get(model_name)
    conf = registry.conf_threshold(model_name)
    results = model.predict(source=frame, conf=conf, imgsz=imgsz, verbose=False)
    r = results[0]
    detections = []
    if r.boxes is None:
        return detections
    for box in r.boxes:
        label = model.names[int(box.cls[0])]
        confidence = float(box.conf[0])
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
        detections.append(
            Detection(camera="", model=model_name, label=label, confidence=confidence, bbox=(x1, y1, x2, y2))
        )
    return detections


def _draw_detection(frame, detection: Detection):
    x1, y1, x2, y2 = [int(v) for v in detection.bbox]
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
    label = f"{detection.label} {detection.confidence:.2f}"
    cv2.putText(frame, label, (x1, max(y1 - 8, 0)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)


def _save_live_frame(camera_name: str, frame):
    try:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        final_path = LIVE_DIR / f"{camera_name}.jpg"
        cv2.imwrite(str(final_path), frame)
    except Exception as e:
        print(f"[{camera_name}] échec sauvegarde frame live : {e}")


def run_camera(camera_name: str, config: dict, registry: ModelRegistry, on_detection=None):
    """on_detection(detection, frame) est appelé pour chaque détection, frame incluse."""
    cam_cfg = config["cameras"][camera_name]
    models_cfg = config["models"]
    all_models = cam_cfg["models"]
    # Modèles désactivés en dur dans config.yaml (ex: modèle pas fiable) : jamais chargés.
    available_models = [m for m in all_models if models_cfg.get(m, {}).get("enabled", True)]
    registry.preload(available_models)

    fps = config.get("inference", {}).get("fps", 2)
    imgsz = config.get("inference", {}).get("imgsz", 640)

    print(f"[{camera_name}] modèles disponibles : {available_models}")
    print(f"[{camera_name}] activation détection/alerte pilotable en direct via le panneau de contrôle")

    recorder = ClipRecorder(camera_name, fps=fps)

    max_workers = min(len(available_models), os.cpu_count() or 4)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while True:
            try:
                source = cam_cfg.get("source", cam_cfg.get("rtsp_url"))
                stream = RTSPStream(source)
                print(f"[{camera_name}] flux ouvert : {source}")
                try:
                    frame_count = 0
                    for frame in stream.frames_at_fps(fps):
                        frame_count += 1
                        cycle_start = time.monotonic()

                        active_models = [m for m in available_models if is_detect_enabled(m)]
                        futures = [
                            executor.submit(_run_one_model, registry, model_name, frame, imgsz)
                            for model_name in active_models
                        ]
                        annotated = frame.copy()
                        for future in futures:
                            for detection in future.result():
                                detection.camera = camera_name
                                print(f"[{camera_name}] {detection.model} -> {detection.label} ({detection.confidence:.2f})")
                                _draw_detection(annotated, detection)
                                if on_detection:
                                    alert = on_detection(detection, frame)
                                    if alert is not None and alert.db_id is not None:
                                        recorder.trigger(alert.db_id)

                        recorder.add_frame(annotated)
                        _save_live_frame(camera_name, annotated)

                        if frame_count % 10 == 0:
                            cycle_ms = (time.monotonic() - cycle_start) * 1000
                            print(f"[{camera_name}] cycle {len(active_models)} modèles (parallèle) = {cycle_ms:.0f} ms")
                finally:
                    stream.release()
            except ConnectionError as e:
                print(f"[{camera_name}] {e}")

            print(f"[{camera_name}] flux perdu, reconnexion dans 2s...")
            time.sleep(2)


if __name__ == "__main__":
    from app.notifier import local_alert
    from app.rules import AlertEngine
    from app.storage import cleanup_old_data

    cleanup_old_data()  # purge : médias > 30 j, alertes > 1 an
    config = load_config()
    registry = ModelRegistry(config["models"])
    engine = AlertEngine(on_alert=local_alert)  # délais anti-répétition selon la sévérité

    run_camera("webcam_test", config, registry, on_detection=engine.process)
