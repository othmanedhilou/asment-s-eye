import os

# Doit être défini avant tout chargement d'OpenVINO : sinon chaque modèle utilise
# tous les cœurs en interne, et les exécuter en parallèle sature le CPU (thrashing).
os.environ.setdefault("OMP_NUM_THREADS", "1")

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from app.capture import RTSPStream
from app.config import load_config
from app.detectors import ModelRegistry
from app.logging_setup import setup_logging
from app.models import Detection
from app.recorder import ClipRecorder
from app.settings import is_detect_enabled
from app.zones import ZoneFilter

LIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "live"

log = setup_logging("pipeline")


def _run_one_model(registry: ModelRegistry, model_name: str, frame, imgsz: int) -> list[Detection]:
    """Exécute un modèle sur une image.

    Toute exception est absorbée ici : sans cela, un seul modèle défaillant
    (fichier corrompu, mémoire insuffisante) remonterait par `future.result()`
    et arrêterait le cycle complet — donc la surveillance des autres modèles.
    """
    try:
        model = registry.get(model_name)
        conf = registry.conf_threshold(model_name)
        results = model.predict(source=frame, conf=conf, imgsz=imgsz, verbose=False)
        r = results[0]
        if r.boxes is None:
            return []
        detections = []
        for box in r.boxes:
            label = model.names[int(box.cls[0])]
            confidence = float(box.conf[0])
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
            detections.append(
                Detection(camera="", model=model_name, label=label, confidence=confidence,
                          bbox=(x1, y1, x2, y2))
            )
        return detections
    except Exception as e:
        log.error(f"modèle {model_name} en échec sur cette image : {e}")
        return []


def _draw_detection(frame, detection: Detection):
    x1, y1, x2, y2 = [int(v) for v in detection.bbox]
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
    label = f"{detection.label} {detection.confidence:.2f}"
    if detection.zone:
        label += f" [{detection.zone}]"
    cv2.putText(frame, label, (x1, max(y1 - 8, 0)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)


def _draw_zones(frame, zone_filter: ZoneFilter):
    """Trace les zones sur l'image live : l'opérateur voit ce qui est surveillé."""
    import numpy as np

    h, w = frame.shape[:2]
    for zone in zone_filter.polygons_in_pixels(w, h):
        pts = np.array(zone["points"], dtype=np.int32)
        cv2.polylines(frame, [pts], isClosed=True, color=(0, 200, 255), thickness=2)
        x, y = zone["points"][0]
        cv2.putText(frame, zone["name"], (x, max(y - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)


def _save_live_frame(camera_name: str, frame, attempts: int = 5, delay: float = 0.02):
    """Écrit l'image live de façon atomique, avec reprise.

    L'API peut lire ce fichier au moment précis où on l'écrit : sans passage par
    un fichier temporaire, elle servirait une image tronquée. `os.replace` est
    atomique sur le même volume.

    Mais sous Windows, le remplacement échoue avec « Access is denied » tant que
    le fichier de destination est ouvert par un autre processus — ce qui arrive
    dès que l'interface rafraîchit la vignette au même instant (observé sur ~1
    cycle sur 25). On réessaie brièvement, puis on écrit directement : une image
    éventuellement tronquée à la lecture suivante vaut mieux qu'une image figée.
    """
    tmp_path = LIVE_DIR / f".{camera_name}.tmp.jpg"
    final_path = LIVE_DIR / f"{camera_name}.jpg"
    try:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(tmp_path), frame):
            log.warning(f"[{camera_name}] encodage de l'image live impossible")
            return

        for attempt in range(attempts):
            try:
                os.replace(tmp_path, final_path)
                return
            except PermissionError:
                if attempt < attempts - 1:
                    time.sleep(delay)

        # Le fichier est resté verrouillé : écriture directe en dernier recours.
        cv2.imwrite(str(final_path), frame)
        log.debug(f"[{camera_name}] image live écrite sans renommage (fichier verrouillé)")
    except Exception as e:
        log.warning(f"[{camera_name}] échec sauvegarde frame live : {e}")


def run_camera(camera_name: str, config: dict, registry: ModelRegistry, on_detection=None,
               stop_event: threading.Event | None = None):
    """Boucle de traitement d'une caméra. Bloquante : à lancer dans un thread.

    on_detection(detection, frame) est appelé pour chaque détection retenue.
    """
    cam_cfg = config["cameras"][camera_name]
    models_cfg = config["models"]
    inference_cfg = config.get("inference", {})
    all_models = cam_cfg["models"]

    # Modèles désactivés en dur dans config.yaml (ex: modèle pas fiable) : jamais chargés.
    available_models = [m for m in all_models if models_cfg.get(m, {}).get("enabled", True)]

    # Cadence et résolution réglables par caméra : sur une machine contrainte, on
    # peut ralentir une caméra secondaire sans toucher aux autres.
    fps = cam_cfg.get("fps", inference_cfg.get("fps", 2))
    imgsz = cam_cfg.get("imgsz", inference_cfg.get("imgsz", 640))
    max_workers = cam_cfg.get("workers", inference_cfg.get("workers"))
    if not max_workers:
        max_workers = max(1, min(len(available_models), (os.cpu_count() or 4)))

    zone_filter = ZoneFilter(camera_name)
    recorder = ClipRecorder(camera_name, fps=fps)

    log.info(f"[{camera_name}] modèles : {available_models} | fps={fps} imgsz={imgsz} workers={max_workers}")
    log.info(f"[{camera_name}] préchauffage des modèles (compilation OpenVINO)...")
    warm_start = time.monotonic()
    registry.warmup(available_models, imgsz)
    log.info(f"[{camera_name}] modèles prêts en {time.monotonic() - warm_start:.1f} s")
    if zone_filter.zones():
        log.info(f"[{camera_name}] zones actives : {[z.get('name') for z in zone_filter.zones()]}")
    else:
        log.info(f"[{camera_name}] aucune zone définie : analyse plein cadre")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while stop_event is None or not stop_event.is_set():
            try:
                source = cam_cfg.get("source", cam_cfg.get("rtsp_url"))
                stream = RTSPStream(source)
                log.info(f"[{camera_name}] flux ouvert : {source}")
                try:
                    frame_count = 0
                    for frame in stream.frames_at_fps(fps):
                        if stop_event is not None and stop_event.is_set():
                            break
                        frame_count += 1
                        cycle_start = time.monotonic()
                        h, w = frame.shape[:2]

                        active_models = [m for m in available_models if is_detect_enabled(m)]
                        futures = [
                            executor.submit(_run_one_model, registry, model_name, frame, imgsz)
                            for model_name in active_models
                        ]

                        annotated = frame.copy()
                        _draw_zones(annotated, zone_filter)

                        for future in futures:
                            for detection in future.result():
                                zone = zone_filter.zone_for(detection.model, detection.bbox, w, h)
                                if zone is None:
                                    continue  # hors des zones surveillées : ignoré
                                detection.camera = camera_name
                                detection.zone = zone
                                where = f" dans {zone}" if zone else ""
                                log.debug(f"[{camera_name}] {detection.model} -> {detection.label} "
                                          f"({detection.confidence:.2f}){where}")
                                _draw_detection(annotated, detection)
                                if on_detection:
                                    alert = on_detection(detection, frame)
                                    if alert is not None and alert.db_id is not None:
                                        recorder.trigger(alert.db_id)

                        recorder.add_frame(annotated)
                        _save_live_frame(camera_name, annotated)

                        if frame_count % 10 == 0:
                            cycle_ms = (time.monotonic() - cycle_start) * 1000
                            log.info(f"[{camera_name}] cycle {len(active_models)} modèles = {cycle_ms:.0f} ms")
                finally:
                    stream.release()
            except ConnectionError as e:
                log.warning(f"[{camera_name}] {e}")
            except Exception as e:
                log.exception(f"[{camera_name}] erreur inattendue : {e}")

            if stop_event is not None and stop_event.is_set():
                break
            log.warning(f"[{camera_name}] flux perdu, reconnexion dans 2 s...")
            time.sleep(2)

    log.info(f"[{camera_name}] arrêté")


def main():
    """Démarre toutes les caméras déclarées, une par thread.

    Un thread par caméra plutôt qu'un processus : les modèles OpenVINO sont
    chargés une seule fois et partagés par toutes les caméras (un jeu de modèles
    par processus coûterait plusieurs Go de RAM), et la libération du GIL pendant
    l'inférence permet un vrai parallélisme.
    """
    from app.notifier import local_alert
    from app.rules import AlertEngine
    from app.storage import cleanup_old_data

    cleanup_old_data()  # purge : médias > 30 j, alertes > 1 an

    config = load_config()
    cameras = list(config.get("cameras", {}).keys())
    if not cameras:
        log.error("aucune caméra déclarée dans config.yaml")
        return

    registry = ModelRegistry(config["models"])
    engine = AlertEngine(on_alert=local_alert)  # délais anti-répétition selon la sévérité

    log.info(f"démarrage sur {len(cameras)} caméra(s) : {cameras}")
    stop_event = threading.Event()
    threads = []
    for camera_name in cameras:
        t = threading.Thread(
            target=run_camera,
            args=(camera_name, config, registry, engine.process, stop_event),
            name=f"cam-{camera_name}",
            daemon=True,
        )
        t.start()
        threads.append(t)

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.info("arrêt demandé, fermeture des caméras...")
        stop_event.set()
        for t in threads:
            t.join(timeout=10)


if __name__ == "__main__":
    main()
