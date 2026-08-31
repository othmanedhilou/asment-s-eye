import os

# Doit être défini avant tout chargement d'OpenVINO : sinon chaque modèle utilise
# tous les cœurs en interne, et les exécuter en parallèle sature le CPU (thrashing).
os.environ.setdefault("OMP_NUM_THREADS", "1")

import threading
import time
from datetime import datetime
from pathlib import Path

import cv2

from app.bachage import ControleBachage
from app.cameras import active_cameras, camera_source
from app.capture import FrameSource
from app.config import load_config
from app.detectors import ModelRegistry
from app.health import forget_camera, set_global, update_camera
from app.logging_setup import setup_logging
from app.models import Detection
from app.recorder import ClipRecorder, ContinuousRecorder
from app.plates import PlateReader
from app.reid import TrackRegistry
from app.settings import is_detect_enabled
from app.tracking import SimpleTracker, build_counters, occupation
from app.zones import ZoneFilter

LIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "live"
COLLECTE_DIR = Path(__file__).resolve().parent.parent / "datasets" / "collecte"

log = setup_logging("pipeline")

# Délai sans image au-delà duquel une caméra est déclarée hors ligne et signalée.
OFFLINE_ALERT_AFTER = 30.0

# Registre partagé des objets vus, renseigné au démarrage.
TRACKS: TrackRegistry | None = None


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


# Une couleur par objet suivi. Deux personnes côte à côte doivent se
# distinguer d'un coup d'oeil ; un identifiant écrit ne suffit pas, on ne le lit
# pas sur une vignette de 200 px.
_TEINTES = [(64, 210, 255), (120, 255, 120), (255, 170, 80), (200, 120, 255),
            (90, 230, 230), (255, 120, 170), (150, 200, 90), (255, 220, 100)]


def _couleur_piste(track_id) -> tuple:
    if track_id is None:
        return (0, 0, 255)
    return _TEINTES[track_id % len(_TEINTES)]


def _draw_detection(frame, detection: Detection):
    x1, y1, x2, y2 = [int(v) for v in detection.bbox]
    couleur = _couleur_piste(detection.track_id)
    cv2.rectangle(frame, (x1, y1), (x2, y2), couleur, 2)

    label = ""
    if detection.track_id is not None:
        label = f"#{detection.track_id} "
    label += f"{detection.label} {detection.confidence:.2f}"
    if detection.plaque:
        label += f" · {detection.plaque}"
    if detection.zone:
        label += f" [{detection.zone}]"

    # Un fond plein derrière le texte : sans lui, l'étiquette devient illisible
    # dès que l'objet passe devant quelque chose de clair.
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    haut = max(y1 - th - 8, 0)
    cv2.rectangle(frame, (x1, haut), (x1 + tw + 8, haut + th + 8), couleur, -1)
    cv2.putText(frame, label, (x1 + 4, haut + th + 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 1, cv2.LINE_AA)


def _draw_traces(frame, tracker):
    """Trace le chemin parcouru par chaque objet suivi."""
    for track_id, points in tracker.traces():
        couleur = _couleur_piste(track_id)
        for a, b in zip(points, points[1:]):
            cv2.line(frame, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), couleur, 2,
                     cv2.LINE_AA)
        x, y = points[-1]
        cv2.circle(frame, (int(x), int(y)), 4, couleur, -1)


def _draw_zones(frame, zone_filter: ZoneFilter):
    """Trace les zones sur l'image live : l'opérateur voit ce qui est surveillé."""
    import numpy as np

    h, w = frame.shape[:2]
    for zone in zone_filter.polygons_in_pixels(w, h):
        # Les zones d'exclusion en rouge, les zones de surveillance en orange :
        # la couleur dit immédiatement si l'on regarde ou si l'on ignore.
        color = (60, 60, 220) if zone.get("type") == "exclusion" else (0, 200, 255)
        pts = np.array(zone["points"], dtype=np.int32)
        cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2)
        x, y = zone["points"][0]
        cv2.putText(frame, zone["name"], (x, max(y - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)


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


def _collecter_image(camera_name: str, frame, contexte: str, plaque: str | None = None):
    """Enregistre une image brute pour constituer un jeu d'entraînement.

    Constituer un jeu de données à la main demande des heures de tri. Or le
    système sait déjà **quand** l'événement intéressant se produit : au
    franchissement d'une ligne, par exemple à la sortie du site. Une image par
    passage, prise automatiquement, donne en quelques jours un jeu propre,
    cadré comme la caméra réelle — ce qu'aucun jeu public ne peut offrir.

    L'image est enregistrée SANS les boîtes de détection : elle servira à
    annoter, et une annotation par-dessus une autre n'a aucun sens.
    """
    try:
        dossier = COLLECTE_DIR / camera_name / contexte
        dossier.mkdir(parents=True, exist_ok=True)
        horodatage = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        suffixe = f"_{plaque}" if plaque else ""
        chemin = dossier / f"{horodatage}{suffixe}.jpg"
        cv2.imwrite(str(chemin), frame)
        log.info(f"[{camera_name}] image collectée : {chemin.name}")
    except Exception as e:
        log.warning(f"[{camera_name}] échec de collecte : {e}")


def _suivre_entre_cameras(camera_name, detection, frame, tracks, lecteur_plaques):
    """Lit la plaque d'un véhicule, puis cherche s'il vient d'une autre caméra.

    L'ordre compte : la plaque, quand elle est connue, tranche un rapprochement
    que l'apparence seule laisserait incertain.
    """
    if detection.track_id is None:
        return

    x1, y1, x2, y2 = [int(v) for v in detection.bbox]
    h, w = frame.shape[:2]
    crop = frame[max(y1, 0):min(y2, h), max(x1, 0):min(x2, w)]
    if crop.size == 0:
        return

    if lecteur_plaques is not None:
        etat = lecteur_plaques.observer(camera_name, detection.track_id, crop)
        if etat and etat.get("texte"):
            detection.plaque = etat["texte"]

    if tracks is not None:
        resultat = tracks.observer(camera_name, detection.track_id,
                                   detection.label, crop, detection.plaque)
        detection.global_id = resultat["global_id"]


def run_camera(camera_name: str, cam_cfg: dict, config: dict, registry: ModelRegistry,
               on_detection=None, stop_event: threading.Event | None = None,
               on_incident=None, tracks: TrackRegistry | None = None):
    """Boucle de traitement d'une caméra. Bloquante : à lancer dans un thread.

    on_detection(detection, frame) est appelé pour chaque détection retenue.
    on_incident(camera, label, message) signale une panne (source injoignable).
    """
    models_cfg = config["models"]
    inference_cfg = config.get("inference", {})
    all_models = cam_cfg.get("models", [])

    # Modèles désactivés en dur dans config.yaml (ex: modèle pas fiable) : jamais chargés.
    # Un modele declare mais dont le fichier n'existe pas encore — la chute et
    # les plaques restent a entrainer — ne doit pas emporter la camera au
    # demarrage. On l'ecarte, et on le dit : un modele silencieux qu'on croit
    # actif est pire qu'un modele absent qu'on sait absent.
    racine = Path(__file__).resolve().parent.parent
    available_models = []
    for m in all_models:
        cfg_modele = models_cfg.get(m, {})
        if not cfg_modele.get("enabled", True):
            continue
        fichier = cfg_modele.get("file")
        if fichier and not (racine / fichier).exists():
            log.warning(f"[{camera_name}] modele '{m}' demande mais absent "
                        f"({fichier}) — ignore")
            continue
        available_models.append(m)

    # Cadence et résolution réglables par caméra : sur une machine contrainte, on
    # peut ralentir une caméra secondaire sans toucher aux autres.
    fps = cam_cfg.get("fps", inference_cfg.get("fps", 2))
    imgsz = cam_cfg.get("imgsz", inference_cfg.get("imgsz", 640))
    max_workers = cam_cfg.get("workers", inference_cfg.get("workers"))
    if not max_workers:
        max_workers = max(1, min(len(available_models) or 1, (os.cpu_count() or 4)))

    zone_filter = ZoneFilter(camera_name)
    recorder = ClipRecorder(camera_name, fps=fps)

    # Enregistrement continu : desactive par defaut, car il consomme beaucoup de
    # disque. L'enregistreur s'arrete de lui-meme si l'espace libre passe sous
    # son seuil — la detection doit rester prioritaire sur la conservation.
    enregistrement_continu = None
    if cam_cfg.get("recording"):
        enregistrement_continu = ContinuousRecorder(
            camera_name, fps=fps,
            segment_minutes=cam_cfg.get("segment_minutes", 5),
            retention_days=cam_cfg.get("retention_days", 7),
        )
        log.info(f"[{camera_name}] enregistrement continu actif "
                 f"(retention {enregistrement_continu.retention_days} j)")

    # Suivi des objets : desactive par defaut car il coute du temps de calcul.
    # A activer sur les cameras ou l'on veut compter, connaitre un sens de
    # passage, ou n'alerter qu'une fois par personne plutot qu'une fois par image.
    suivi_actif = bool(cam_cfg.get("tracking", False))
    trackers: dict[str, SimpleTracker] = {}
    compteurs = build_counters(zone_filter.zones()) if suivi_actif else []
    if suivi_actif:
        log.info(f"[{camera_name}] suivi d'objets actif"
                 + (f", {len(compteurs)} ligne(s) de comptage" if compteurs else ""))

    # Contrôle du bâchage : déduit l'absence de bâche de son absence de
    # détection. Exige le suivi, comme la lecture de plaques : la confirmation
    # se fait sur plusieurs images du MEME camion.
    controle_bachage = None
    if cam_cfg.get("bachage") and suivi_actif:
        controle_bachage = ControleBachage(camera_name)
        log.info(f"[{camera_name}] contrôle du bâchage actif")
    elif cam_cfg.get("bachage"):
        log.warning(f"[{camera_name}] contrôle du bâchage demandé mais suivi désactivé : "
                    "sans suivi, aucune confirmation possible sur plusieurs images")

    # Lecture de plaques : n'a de sens qu'avec le suivi, puisque la fiabilité
    # vient du vote sur plusieurs images du MEME vehicule.
    lecteur_plaques = None
    if cam_cfg.get("plates") and suivi_actif:
        lecteur_plaques = PlateReader(
            registry=registry,
            plate_model="plate" if "plate" in models_cfg else None)
        log.info(f"[{camera_name}] lecture de plaques active")
    elif cam_cfg.get("plates"):
        log.warning(f"[{camera_name}] lecture de plaques demandée mais suivi désactivé : "
                    "sans suivi, aucun vote possible, la lecture serait peu fiable")

    log.info(f"[{camera_name}] modèles : {available_models} | fps={fps} imgsz={imgsz} workers={max_workers}")
    update_camera(camera_name, state="demarrage", models=available_models, fps_cible=fps)

    log.info(f"[{camera_name}] préchauffage des modèles (compilation OpenVINO)...")
    warm_start = time.monotonic()
    registry.warmup(available_models, imgsz)
    log.info(f"[{camera_name}] modèles prêts en {time.monotonic() - warm_start:.1f} s")

    if zone_filter.zones():
        log.info(f"[{camera_name}] zones : {[z.get('name') for z in zone_filter.zones()]}")
    else:
        log.info(f"[{camera_name}] aucune zone définie : analyse plein cadre")

    from concurrent.futures import ThreadPoolExecutor

    offline_since = None
    incident_signale = False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while stop_event is None or not stop_event.is_set():
            try:
                source = camera_source(cam_cfg)
                stream = FrameSource(source)
                log.info(f"[{camera_name}] source ouverte : {source} ({stream.kind})")
                update_camera(camera_name, state="en ligne", source=str(source),
                              kind=stream.kind, error=None)
                if incident_signale and on_incident:
                    on_incident(camera_name, "camera_retablie",
                                f"Caméra {camera_name} de nouveau en ligne")
                offline_since = None
                incident_signale = False

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
                        retenues = []

                        for model_name, future in zip(active_models, futures):
                            detections = future.result()

                            if suivi_actif:
                                tracker = trackers.setdefault(model_name, SimpleTracker())
                                detections = tracker.update(detections)
                                _draw_traces(annotated, tracker)
                                for detection in detections:
                                    detection.model = model_name
                                    _suivre_entre_cameras(
                                        camera_name, detection, frame, tracks,
                                        lecteur_plaques if model_name == "vehicles" else None)
                                for compteur in compteurs:
                                    for passage in compteur.update(model_name, detections, w, h):
                                        log.info(f"[{camera_name}] franchissement "
                                                 f"{passage['ligne']} ({passage['sens']}) "
                                                 f"— {passage['classe']}")
                                        # Le franchissement est le bon moment
                                        # pour photographier : c'est là que le
                                        # camion est cadré comme il le sera en
                                        # exploitation.
                                        if cam_cfg.get("collecte"):
                                            plaque = next((d.plaque for d in detections
                                                           if d.track_id == passage["track_id"]
                                                           and d.plaque), None)
                                            _collecter_image(
                                                camera_name, frame,
                                                f"{passage['ligne']}_{passage['sens']}", plaque)

                            # Le constat d'absence de bâche se fait sur l'ensemble
                            # des détections du modèle, camions ET bâches : c'est
                            # leur relation qui porte l'information, pas chacune
                            # prise isolément.
                            if controle_bachage is not None and model_name == "load_control":
                                detections = detections + controle_bachage.analyser(detections, w, h)

                            for detection in detections:
                                matched = zone_filter.match(detection.model, detection.bbox, w, h)
                                if matched is None:
                                    continue  # hors zone, masqué, ou hors horaire
                                detection.camera = camera_name
                                detection.zone = matched["name"]
                                detection.zone_conf = matched.get("conf")
                                detection.zone_cooldown = matched.get("cooldown")
                                detection.frame_size = (w, h)
                                where = f" dans {detection.zone}" if detection.zone else ""
                                plaque = f" [{detection.plaque}]" if detection.plaque else ""
                                log.debug(f"[{camera_name}] {detection.model} -> {detection.label} "
                                          f"({detection.confidence:.2f}){where}{plaque}")
                                _draw_detection(annotated, detection)
                                retenues.append(detection)
                                if on_detection:
                                    alert = on_detection(detection, frame)
                                    if alert is not None and alert.db_id is not None:
                                        recorder.trigger(alert.db_id)

                        recorder.add_frame(annotated)
                        # L'enregistrement continu conserve l'image BRUTE : les
                        # boites de detection sont une interpretation, pas une
                        # preuve, et elles ne doivent pas figurer sur un
                        # enregistrement qui peut servir de constat.
                        if enregistrement_continu is not None:
                            enregistrement_continu.add_frame(frame)
                        _save_live_frame(camera_name, annotated)

                        cycle_ms = (time.monotonic() - cycle_start) * 1000
                        etat = {
                            "state": "en ligne",
                            "cycle_ms": round(cycle_ms),
                            "fps_reel": round(1000 / cycle_ms, 2) if cycle_ms else None,
                            "modeles_actifs": len(active_models),
                        }
                        # « suivi » est publié même à false : sans lui, une
                        # caméra sans suivi et une caméra dont le suivi ne
                        # trouve rien se ressemblent, et on ne sait pas laquelle
                        # des deux on regarde.
                        etat["suivi"] = suivi_actif
                        if suivi_actif:
                            etat["objets_suivis"] = sum(t.actifs for t in trackers.values())
                            if compteurs:
                                etat["franchissements"] = [c.counts() for c in compteurs]
                            # Objets distincts presents par zone : un ouvrier
                            # immobile compte pour un, pas pour une image.
                            presents = occupation(zone_filter.zones(), retenues, w, h)
                            if presents:
                                etat["occupation"] = presents
                            plaques = [d.plaque for d in retenues if d.plaque]
                            if plaques:
                                etat["plaques"] = sorted(set(plaques))
                        if lecteur_plaques is not None:
                            etat["lecture_plaques"] = lecteur_plaques.diagnostic(camera_name)
                        update_camera(camera_name, **etat)
                        if frame_count % 10 == 0:
                            log.info(f"[{camera_name}] cycle {len(active_models)} modèles = {cycle_ms:.0f} ms")
                finally:
                    stream.release()
                    if enregistrement_continu is not None:
                        enregistrement_continu.release()
            except ConnectionError as e:
                log.warning(f"[{camera_name}] {e}")
                update_camera(camera_name, state="hors ligne", error=str(e))
                if offline_since is None:
                    offline_since = time.monotonic()
            except Exception as e:
                log.exception(f"[{camera_name}] erreur inattendue : {e}")
                update_camera(camera_name, state="erreur", error=str(e))
                if offline_since is None:
                    offline_since = time.monotonic()

            if stop_event is not None and stop_event.is_set():
                break

            # Une coupure brève est normale (reconnexion réseau). On n'alerte que
            # si la caméra reste injoignable : sinon le moindre hoquet réveille
            # l'équipe de nuit pour rien.
            # Et une seule fois. Une panne ne se répète pas : elle dure. Répéter
            # l'alerte toutes les trente secondes noierait le reste et
            # apprendrait à l'agent à ignorer la colonne.
            if offline_since is not None and on_incident and not incident_signale \
                    and time.monotonic() - offline_since > OFFLINE_ALERT_AFTER:
                on_incident(camera_name, "camera_hors_ligne",
                            f"Caméra {camera_name} ne répond plus")
                incident_signale = True

            log.warning(f"[{camera_name}] source perdue, nouvelle tentative dans 2 s...")
            update_camera(camera_name, state="reconnexion")
            time.sleep(2)

    log.info(f"[{camera_name}] arrêté")
    update_camera(camera_name, state="arrêté")


class CameraSupervisor:
    """Garde les threads de caméras alignés sur la configuration.

    Ajouter une caméra depuis l'interface ne servirait à rien s'il fallait
    redémarrer le pipeline pour qu'elle soit prise en compte. Le superviseur
    compare régulièrement les caméras actives à celles qui tournent, et démarre
    ou arrête ce qu'il faut.
    """

    def __init__(self, config: dict, registry: ModelRegistry, on_detection, on_incident,
                 interval: float = 5.0, tracks: TrackRegistry | None = None):
        self.config = config
        self.registry = registry
        self.on_detection = on_detection
        self.on_incident = on_incident
        self.interval = interval
        self.tracks = tracks
        self._threads: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._configs: dict[str, dict] = {}
        self._shutdown = threading.Event()

    def _start(self, name: str, cfg: dict):
        stop_event = threading.Event()
        thread = threading.Thread(
            target=run_camera,
            args=(name, cfg, self.config, self.registry, self.on_detection, stop_event,
                  self.on_incident, self.tracks),
            name=f"cam-{name}",
            daemon=True,
        )
        thread.start()
        self._threads[name] = (thread, stop_event)
        self._configs[name] = cfg
        log.info(f"caméra démarrée : {name}")

    def _stop(self, name: str, forget: bool = True):
        thread, stop_event = self._threads.pop(name, (None, None))
        self._configs.pop(name, None)
        if stop_event is not None:
            stop_event.set()
        if thread is not None:
            thread.join(timeout=10)
        if forget:
            forget_camera(name)
        log.info(f"caméra arrêtée : {name}")

    def reconcile(self):
        wanted = active_cameras()

        for name in list(self._threads):
            if name not in wanted:
                self._stop(name)
            elif wanted[name] != self._configs.get(name):
                # Changement de source, de modèles ou de cadence : redémarrage
                # du thread, seul moyen sûr de repartir sur la bonne source.
                log.info(f"caméra modifiée, redémarrage : {name}")
                self._stop(name, forget=False)
                self._start(name, wanted[name])
            elif not self._threads[name][0].is_alive():
                log.error(f"thread de la caméra {name} mort, relance")
                self._stop(name, forget=False)
                self._start(name, wanted[name])

        for name, cfg in wanted.items():
            if name not in self._threads:
                self._start(name, cfg)

    def run(self):
        try:
            while not self._shutdown.is_set():
                self.reconcile()
                if self.tracks is not None:
                    set_global(correspondances=self.tracks.recentes(20),
                               objets_inter_cameras=self.tracks.objets_suivis)
                self._shutdown.wait(self.interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self):
        self._shutdown.set()
        for name in list(self._threads):
            self._stop(name)


# Port sans service, reserve au verrou. Se liberer tout seul a la mort du
# processus est exactement ce qu'on veut : un fichier de verrou survivrait a un
# arret brutal et empecherait tout redemarrage.
PORT_VERROU = 8791
_verrou = None


def verrou_unique() -> bool:
    """Un seul pipeline a la fois.

    Deux pipelines sur la meme machine, c'est deux fois l'inference sur deux
    coeurs, deux ecritures concurrentes de la meme image live et deux etats de
    sante qui s'ecrasent. Le symptome visible est une interface lente et des
    compteurs incoherents, et la cause est invisible depuis l'interface. On
    refuse donc de demarrer plutot que de degrader silencieusement.
    """
    global _verrou
    import socket

    _verrou = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _verrou.bind(("127.0.0.1", PORT_VERROU))
        _verrou.listen(1)
        return True
    except OSError:
        _verrou.close()
        _verrou = None
        return False


def main():
    """Démarre la supervision : une caméra, un thread, ajustés en continu.

    Un thread par caméra plutôt qu'un processus : les modèles OpenVINO sont
    chargés une seule fois et partagés par toutes les caméras (un jeu de modèles
    par processus coûterait plusieurs centaines de Mo chacun), et l'inférence
    libère le GIL, donc le parallélisme est réel.
    """

    if not verrou_unique():
        log.error("un pipeline tourne deja sur cette machine (port "
                  f"{PORT_VERROU} occupe) — demarrage refuse")
        return

    from app.notifier import local_alert, system_alert
    from app.rules import AlertEngine
    from app.storage import cleanup_old_data

    cleanup_old_data()  # purge : médias > 30 j, alertes > 1 an

    config = load_config()
    registry = ModelRegistry(config["models"])
    engine = AlertEngine(on_alert=local_alert)  # délais anti-répétition selon la sévérité

    def on_incident(camera, label, message):
        log.error(message)
        try:
            system_alert(camera, label, message)
        except Exception as e:
            log.error(f"échec de la notification d'incident : {e}")

    cameras = active_cameras()
    if not cameras:
        log.warning("aucune caméra active — ajoutez-en une depuis l'interface")
    else:
        log.info(f"caméras actives : {list(cameras)}")

    # Topologie déclarée : quelles caméras peuvent se passer un objet. Sans
    # elle, on n'exclut aucun rapprochement — ce qui produit plus de faux
    # rapprochements sur un grand site.
    voisins = {nom: cfg.get("voisins", []) for nom, cfg in cameras.items() if cfg.get("voisins")}
    tracks = TrackRegistry(voisins=voisins)
    if voisins:
        log.info(f"topologie des caméras : {voisins}")

    global TRACKS
    TRACKS = tracks

    CameraSupervisor(config, registry, engine.process, on_incident, tracks=tracks).run()


if __name__ == "__main__":
    main()
