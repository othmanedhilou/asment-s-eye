import os
import threading
import time

import cv2

from app.logging_setup import setup_logging

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

log = setup_logging()


class RTSPStream:
    """Lecture d'un flux RTSP dans un thread dédié.

    Le thread vide le flux en continu et ne conserve que l'image la plus
    récente. Sans cela, l'inférence (~1 s par cycle) prendrait du retard sur la
    caméra (25-30 images/s) : le serveur RTSP accumulerait puis fermerait la
    connexion ("reader is too slow"), bloquant le pipeline.
    """

    def __init__(self, url, open_timeout: float = 10.0):
        """`url` est une URL RTSP, ou un indice de webcam locale (0, 1, ...)
        quand aucun serveur RTSP n'est disponible sur la machine."""
        self.url = url
        source = url
        if isinstance(source, str) and source.isdigit():
            source = int(source)
        if isinstance(source, int):
            self.cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        else:
            self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            raise ConnectionError(f"Impossible d'ouvrir le flux : {url}")
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except cv2.error:
            pass

        self._lock = threading.Lock()
        self._latest = None
        self._last_frame_time = time.monotonic()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

        # attendre la première image pour signaler tout de suite un flux muet
        deadline = time.monotonic() + open_timeout
        while self._latest is None and time.monotonic() < deadline:
            if self._stopped.is_set():
                raise ConnectionError(f"Flux interrompu dès l'ouverture : {url}")
            time.sleep(0.05)
        if self._latest is None:
            self.release()
            raise ConnectionError(f"Aucune image reçue depuis : {url}")

    def _reader(self):
        while not self._stopped.is_set():
            ok, frame = self.cap.read()
            if not ok:
                break
            with self._lock:
                self._latest = frame
                self._last_frame_time = time.monotonic()
        self._stopped.set()

    def read(self):
        """Dernière image reçue (None si le flux est terminé)."""
        with self._lock:
            return self._latest

    def frames_at_fps(self, fps: float, stale_timeout: float = 15.0):
        """Fournit la dernière image disponible à la cadence demandée.

        S'arrête si le flux se termine ou n'envoie plus rien pendant
        `stale_timeout` secondes, pour laisser le pipeline se reconnecter.
        """
        interval = 1.0 / fps
        while not self._stopped.is_set():
            start = time.monotonic()
            with self._lock:
                frame = self._latest
                age = time.monotonic() - self._last_frame_time
            if frame is None:
                break
            if age > stale_timeout:
                log.warning(f"flux muet depuis {age:.0f}s : {self.url}")
                break

            yield frame

            elapsed = time.monotonic() - start
            if elapsed < interval:
                time.sleep(interval - elapsed)

    def release(self):
        self._stopped.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        self.cap.release()
