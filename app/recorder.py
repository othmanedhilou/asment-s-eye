"""Enregistreur de clips vidéo autour des alertes.

Garde en mémoire les N dernières secondes de flux (pré-événement) ; quand une
alerte se déclenche, capture aussi les secondes suivantes puis écrit un MP4.
L'opérateur voit ainsi ce qui s'est passé AVANT et APRÈS la détection.
"""

import shutil
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import cv2

from app.logging_setup import setup_logging
from app.storage import update_alert_clip

CLIPS_DIR = Path(__file__).resolve().parent.parent / "clips" / "videos"

log = setup_logging()


# Le clip doit se lire DANS UN NAVIGATEUR, ce qui elimine la plupart des codecs
# qu'OpenCV sait ecrire.
#
# Constat sur cette machine : les clips sortaient en FMP4 (MPEG-4 Part 2).
# Le fichier etait valide, decodable par OpenCV, et aucun navigateur ne
# l'affichait — donc « le clip ne marche pas », alors que tout fonctionnait
# sauf la derniere etape. H.264 exige la bibliotheque OpenH264, absente ici et
# qu'on ne peut pas supposer presente sur un site coupe d'Internet.
#
# VP8 dans un conteneur WebM est lu nativement par Chrome, Edge et Firefox, et
# n'exige aucune bibliotheque supplementaire. On retombe sur MPEG-4 si
# l'encodeur manque : un clip illisible dans le navigateur vaut mieux qu'aucun
# clip, il reste ouvrable avec un lecteur video.
ENCODEURS = [("VP80", ".webm"), ("VP90", ".webm"), ("mp4v", ".mp4")]


def _ouvrir_video(base: Path, fps: float, largeur: int, hauteur: int):
    """Ouvre un writer avec le premier encodeur disponible. (chemin, writer)."""
    for code, extension in ENCODEURS:
        chemin = base.with_suffix(extension)
        writer = cv2.VideoWriter(str(chemin), cv2.VideoWriter_fourcc(*code),
                                 fps, (largeur, hauteur))
        if writer.isOpened():
            return chemin, writer
        writer.release()
        chemin.unlink(missing_ok=True)
    return base, None


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

    def release(self):
        """Écrit le clip en cours, meme incomplet, avant que la camera s'arrete.

        Un clip attend dix secondes d'images apres l'alerte. Si la camera
        s'arrete pendant ce temps — pipeline redemarre, camera mise en pause,
        fichier video termine — les images accumulees partaient a la poubelle
        et l'alerte restait sans preuve. Or les alertes qui precedent un arret
        sont souvent celles qui l'expliquent.

        Un clip de trois secondes vaut mieux qu'aucun clip.
        """
        if self._active is not None:
            self._write()

    def trigger(self, alert_id: int):
        """Rattache une alerte au clip en cours, ou en démarre un.

        Une alerte survenue pendant qu'un clip s'enregistre était auparavant
        ignorée, et repartait sans preuve vidéo : quarante alertes sur
        quatre-vingt-dix-huit se retrouvaient sans clip, précisément celles qui
        arrivaient en rafale — donc les plus intéressantes. Elles partagent
        maintenant le clip en cours, qui couvre de toute façon leur instant.
        """
        if self._active is not None:
            if alert_id not in self._active["alert_ids"]:
                self._active["alert_ids"].append(alert_id)
            return
        pre_frames = list(self._buffer)
        self._active = {
            "alert_ids": [alert_id],
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
            premiere = active["alert_ids"][0]
            path, writer = _ouvrir_video(
                CLIPS_DIR / f"{self.camera}_alerte{premiere}_{ts}", self.fps, w, h)
            if writer is None:
                log.error(f"[{self.camera}] aucun encodeur video disponible")
                return
            for f in frames:
                writer.write(f)
            writer.release()
            for alert_id in active["alert_ids"]:
                update_alert_clip(alert_id, str(path))
            nb = len(active["alert_ids"])
            log.info(f"[{self.camera}] clip enregistré : {path.name}"
                     + (f" ({nb} alertes)" if nb > 1 else ""))
        except Exception as e:
            log.error(f"[{self.camera}] échec enregistrement clip : {e}")


class ContinuousRecorder:
    """Enregistrement vidéo continu, par segments.

    Les clips d'alerte montrent ce que le système a *vu*. L'enregistrement
    continu montre ce qui s'est *passé* — y compris ce que le système a manqué.
    C'est ce que fait un VMS classique, et c'est souvent la seule preuve
    disponible après un incident.

    Trois précautions, parce que cette fonction est celle qui peut mettre le
    serveur à genoux :

    - **des segments courts** (5 min par défaut) plutôt qu'un fichier par jour :
      un fichier en cours d'écriture qui se corrompt ne fait perdre que quelques
      minutes, et l'opérateur peut consulter la journée sans télécharger 20 Go.
    - **une rétention automatique**, appliquée à chaque rotation.
    - **un seuil d'espace libre** : en dessous, l'enregistrement s'arrête de
      lui-même. Un disque plein empêcherait la détection d'écrire ses snapshots
      et sa base — autrement dit, la surveillance s'arrêterait pour conserver
      des vidéos. C'est l'inverse de la priorité.
    """

    def __init__(self, camera: str, fps: float = 4, segment_minutes: int = 5,
                 retention_days: int = 7, min_free_gb: float = 10.0):
        self.camera = camera
        self.fps = max(fps, 1)
        self.segment_frames = max(1, int(segment_minutes * 60 * self.fps))
        self.retention_days = retention_days
        self.min_free_gb = min_free_gb

        self.dossier = CLIPS_DIR.parent / "continu" / camera
        self._writer = None
        self._frames = 0
        self._suspendu = False

    def _espace_libre_go(self) -> float:
        try:
            usage = shutil.disk_usage(str(CLIPS_DIR.parent))
            return usage.free / 1024 ** 3
        except OSError:
            return float("inf")

    def _ouvrir_segment(self, frame):
        h, w = frame.shape[:2]
        jour = self.dossier / f"{datetime.now():%Y-%m-%d}"
        jour.mkdir(parents=True, exist_ok=True)
        # Le nom est horodaté à la seconde. Deux segments ouverts dans la même
        # seconde — après une reprise, ou avec des segments très courts — se
        # marcheraient dessus : on suffixe plutôt que d'écraser un enregistrement.
        chemin = jour / f"{datetime.now():%H%M%S}.mp4"
        suffixe = 1
        while chemin.exists():
            chemin = jour / f"{datetime.now():%H%M%S}_{suffixe}.mp4"
            suffixe += 1
        self._writer = cv2.VideoWriter(
            str(chemin), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h))
        self._frames = 0
        log.debug(f"[{self.camera}] nouveau segment : {chemin.name}")

    def _fermer_segment(self):
        if self._writer is not None:
            self._writer.release()
            self._writer = None

    def add_frame(self, frame):
        if frame is None:
            return
        try:
            if self._writer is None or self._frames >= self.segment_frames:
                self._fermer_segment()

                libre = self._espace_libre_go()
                if libre < self.min_free_gb:
                    if not self._suspendu:
                        log.warning(
                            f"[{self.camera}] enregistrement continu suspendu : "
                            f"{libre:.1f} Go libres (seuil {self.min_free_gb} Go). "
                            "La détection reste prioritaire sur la conservation.")
                        self._suspendu = True
                    return
                if self._suspendu:
                    log.info(f"[{self.camera}] espace disque rétabli, enregistrement repris")
                    self._suspendu = False

                self.purge()
                self._ouvrir_segment(frame)

            self._writer.write(frame)
            self._frames += 1
        except Exception as e:
            log.error(f"[{self.camera}] échec enregistrement continu : {e}")
            self._fermer_segment()

    def purge(self):
        """Supprime les journées au-delà de la rétention."""
        if not self.dossier.exists():
            return
        limite = datetime.now() - timedelta(days=self.retention_days)
        for jour in self.dossier.iterdir():
            if not jour.is_dir():
                continue
            try:
                if datetime.strptime(jour.name, "%Y-%m-%d") < limite:
                    shutil.rmtree(jour, ignore_errors=True)
                    log.info(f"[{self.camera}] enregistrements du {jour.name} purgés")
            except ValueError:
                continue

    def release(self):
        self._fermer_segment()
