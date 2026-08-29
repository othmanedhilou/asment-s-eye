"""Lecture des sources vidéo.

Une « caméra » au sens du pipeline peut être quatre choses :

    0                          webcam locale
    rtsp://192.168.1.42/...    caméra IP du site
    videos/incendie.mp4        fichier vidéo, rejoué en boucle
    videos/sequence/           dossier d'images, rejoué en boucle

Les deux dernières formes existent parce qu'on ne peut pas toujours brancher une
caméra réelle : sans elles, la seule scène disponible est la webcam de la machine
de développement, où il ne se passe jamais rien. Le pipeline ne fait aucune
différence entre ces sources — mêmes zones, mêmes modèles, mêmes alertes.
"""

import os
import threading
import time
from pathlib import Path

import cv2

from app.logging_setup import setup_logging

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

log = setup_logging()

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".wmv"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def classify_source(source):
    """Détermine la nature d'une source : "webcam", "rtsp", "video", "images".

    Sert au pipeline (choix du mode de lecture) et à l'interface (test de
    connexion, message d'erreur adapté).
    """
    if isinstance(source, int):
        return "webcam"
    if isinstance(source, str):
        if source.isdigit():
            return "webcam"
        if "://" in source:
            return "rtsp"
        path = resolve_path(source)
        if path.is_dir():
            return "images"
        if path.suffix.lower() in VIDEO_SUFFIXES:
            return "video"
        if path.suffix.lower() in IMAGE_SUFFIXES:
            return "images"
    return "rtsp"


def resolve_path(source: str) -> Path:
    """Un chemin relatif est compris depuis la racine du projet.

    Sans cela, une source configurée en `videos/feu.mp4` ne fonctionnerait que si
    le processus a été lancé depuis le bon dossier — ce qui n'est jamais le cas
    d'un service Windows.
    """
    path = Path(source)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def list_images(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


class FrameSource:
    """Fournit des images depuis n'importe quelle source, à cadence maîtrisée.

    Deux modes de lecture, selon la nature de la source :

    **Flux en direct** (webcam, RTSP) — un thread dédié vide le flux en continu
    et ne conserve que l'image la plus récente. Sans cela, l'inférence (~1 s par
    cycle) prendrait du retard sur la caméra (25-30 img/s) : le serveur RTSP
    accumulerait puis fermerait la connexion (« reader is too slow »), bloquant
    le pipeline. L'inférence saute des images plutôt que de les accumuler.

    **Fichier** (vidéo, dossier d'images) — lecture séquentielle, sans thread et
    sans perte : chaque image demandée est la suivante du fichier. Une vidéo
    rejouée doit donner le même résultat à chaque exécution, sinon elle ne peut
    servir ni à régler des seuils ni à mesurer la qualité d'un modèle.
    """

    def __init__(self, source, open_timeout: float = 10.0, loop: bool = True):
        self.source = source
        self.kind = classify_source(source)
        self.loop = loop
        self.frames_read = 0

        self._images: list[Path] = []
        self._image_index = 0
        self._cap = None
        self._lock = threading.Lock()
        self._latest = None
        self._last_frame_time = time.monotonic()
        self._stopped = threading.Event()
        self._thread = None

        if self.kind == "images":
            self._open_images()
        else:
            self._open_capture(open_timeout)

    # ── Ouverture ────────────────────────────────────────────────────

    def _open_images(self):
        directory = resolve_path(str(self.source))
        if directory.is_file():
            self._images = [directory]
        else:
            self._images = list_images(directory)
        if not self._images:
            raise ConnectionError(f"Aucune image trouvée dans : {self.source}")

    def _open_capture(self, open_timeout: float):
        if self.kind == "webcam":
            index = int(self.source)
            # CAP_DSHOW : sous Windows, l'ouverture par défaut est lente et
            # échoue sur certaines webcams intégrées.
            self._cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        elif self.kind == "video":
            path = resolve_path(str(self.source))
            if not path.exists():
                raise ConnectionError(f"Fichier vidéo introuvable : {path}")
            self._cap = cv2.VideoCapture(str(path))
        else:
            self._cap = cv2.VideoCapture(str(self.source))

        if not self._cap.isOpened():
            raise ConnectionError(f"Impossible d'ouvrir la source : {self.source}")

        if self.is_live:
            try:
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except cv2.error:
                pass
            self._start_reader(open_timeout)

    @property
    def is_live(self) -> bool:
        return self.kind in ("webcam", "rtsp")

    def _start_reader(self, open_timeout: float):
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

        # Attendre la première image signale tout de suite un flux muet, plutôt
        # que de laisser le pipeline tourner à vide.
        deadline = time.monotonic() + open_timeout
        while self._latest is None and time.monotonic() < deadline:
            if self._stopped.is_set():
                raise ConnectionError(f"Flux interrompu dès l'ouverture : {self.source}")
            time.sleep(0.05)
        if self._latest is None:
            self.release()
            raise ConnectionError(f"Aucune image reçue depuis : {self.source}")

    def _reader(self):
        while not self._stopped.is_set():
            ok, frame = self._cap.read()
            if not ok:
                break
            with self._lock:
                self._latest = frame
                self._last_frame_time = time.monotonic()
        self._stopped.set()

    # ── Lecture ──────────────────────────────────────────────────────

    def read(self):
        """Image suivante, ou None si la source est épuisée."""
        if self.is_live:
            with self._lock:
                return self._latest
        return self._read_sequential()

    def _read_sequential(self):
        if self.kind == "images":
            if self._image_index >= len(self._images):
                if not self.loop:
                    return None
                self._image_index = 0
            frame = cv2.imread(str(self._images[self._image_index]))
            self._image_index += 1
            self.frames_read += 1
            return frame

        ok, frame = self._cap.read()
        if not ok:
            if not self.loop:
                return None
            # Rejouer depuis le début : une vidéo de test doit pouvoir tourner
            # aussi longtemps qu'une caméra.
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
            if not ok:
                return None
        self.frames_read += 1
        return frame

    def frames_at_fps(self, fps: float, stale_timeout: float = 15.0):
        """Fournit des images à la cadence demandée.

        Sur un flux en direct, s'arrête si la source ne répond plus pendant
        `stale_timeout` secondes, pour laisser le pipeline se reconnecter.
        """
        interval = 1.0 / fps if fps > 0 else 0.0

        while not self._stopped.is_set():
            start = time.monotonic()

            if self.is_live:
                with self._lock:
                    frame = self._latest
                    age = time.monotonic() - self._last_frame_time
                if frame is None:
                    break
                if age > stale_timeout:
                    log.warning(f"source muette depuis {age:.0f} s : {self.source}")
                    break
            else:
                frame = self.read()
                if frame is None:
                    break

            yield frame

            elapsed = time.monotonic() - start
            if elapsed < interval:
                time.sleep(interval - elapsed)

    # ── Métadonnées ──────────────────────────────────────────────────

    def properties(self) -> dict:
        info = {"source": str(self.source), "kind": self.kind}
        if self.kind == "images":
            info["frames"] = len(self._images)
            return info
        if self._cap is not None:
            info["width"] = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            info["height"] = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            if self.kind == "video":
                info["frames"] = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                info["fps"] = round(self._cap.get(cv2.CAP_PROP_FPS) or 0, 2)
        return info

    def release(self):
        self._stopped.set()
        if self._thread is not None and self._thread.is_alive() \
                and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        if self._cap is not None:
            self._cap.release()


# Nom historique, conservé pour ne pas casser les appels existants.
RTSPStream = FrameSource


def probe_source(source, timeout: float = 10.0) -> dict:
    """Teste une source et renvoie son état, sans lever d'exception.

    Utilisé par le bouton « Tester la connexion » de l'interface : le jour du
    raccordement des caméras du site, il faut savoir en deux secondes si l'URL
    et les identifiants sont bons, pas chercher dans les journaux.
    """
    stream = None
    try:
        stream = FrameSource(source, open_timeout=timeout)
        frame = stream.read()
        if frame is None:
            return {"ok": False, "kind": classify_source(source),
                    "error": "Source ouverte mais aucune image reçue"}
        height, width = frame.shape[:2]
        info = stream.properties()
        info.update({"ok": True, "width": width, "height": height})
        return info
    except ConnectionError as e:
        return {"ok": False, "kind": classify_source(source), "error": str(e)}
    except Exception as e:
        return {"ok": False, "kind": classify_source(source), "error": f"Erreur inattendue : {e}"}
    finally:
        if stream is not None:
            stream.release()
