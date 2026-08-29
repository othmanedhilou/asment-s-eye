"""Banc de test : mesurer la qualité de détection sur des vidéos de référence.

Sans lui, toute modification — relever un seuil, ré-entraîner un modèle — se
juge à l'impression. Le vrai danger n'est pas de stagner, c'est de reculer sans
le voir : on relève un seuil, les fausses alertes chutent, et le modèle rate
désormais un ouvrier sans casque sur trois au lieu d'un sur deux. Un manque ne
s'affiche nulle part.

Le principe est volontairement simple : des clips dont on sait ce qu'ils
contiennent, et deux chiffres par modèle.

    taux de détection      sur les clips où la classe DOIT apparaître,
                           part des images où le modèle la voit
    fausses détections     sur les clips où elle ne doit PAS apparaître,
      par minute           combien de fois le modèle la croit présente

Ce n'est pas une mAP : les clips ne sont pas annotés image par image, seulement
au niveau du clip. C'est assez pour comparer un avant et un après, ce qui est
exactement l'usage visé — et c'est honnête sur ce que la mesure vaut.
"""

import json
from datetime import datetime
from pathlib import Path

import yaml

from app.capture import FrameSource, resolve_path
from app.config import load_config
from app.detectors import ModelRegistry
from app.logging_setup import setup_logging

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_CONFIG = PROJECT_ROOT / "config" / "benchmark.yaml"
RESULTS_DIR = PROJECT_ROOT / "data" / "benchmarks"

log = setup_logging()


def load_benchmark(path: Path = BENCHMARK_CONFIG) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Jeu de test introuvable : {path}\n"
            "Créez-le à partir de config/benchmark.example.yaml"
        )
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _analyse_clip(clip: dict, registry: ModelRegistry, models: list[str],
                  stride: int, imgsz: int) -> dict:
    """Fait tourner les modèles sur un clip et compte ce qui est vu."""
    source_path = resolve_path(clip["file"])
    if not source_path.exists():
        return {"file": clip["file"], "erreur": "fichier introuvable"}

    stream = FrameSource(clip["file"], loop=False)
    props = stream.properties()
    fps = props.get("fps") or 10.0

    # (modèle, classe) -> nombre d'images où la classe est vue
    vues: dict[tuple[str, str], int] = {}
    analysees = 0
    lues = 0

    try:
        while True:
            frame = stream.read()
            if frame is None:
                break
            lues += 1
            # Analyser une image sur `stride` : deux images consécutives d'une
            # vidéo à 25 img/s sont quasi identiques, les analyser toutes ne
            # change pas le résultat et multiplie le temps de calcul.
            if (lues - 1) % stride:
                continue
            analysees += 1

            for model_name in models:
                model = registry.get(model_name)
                conf = registry.conf_threshold(model_name)
                results = model.predict(source=frame, conf=conf, imgsz=imgsz, verbose=False)
                boxes = results[0].boxes
                if boxes is None:
                    continue
                for box in boxes:
                    label = model.names[int(box.cls[0])]
                    vues[(model_name, label)] = vues.get((model_name, label), 0) + 1
    finally:
        stream.release()

    duree_min = (lues / fps) / 60 if fps else 0

    attendu = clip.get("attendu") or {}
    resultat = {
        "file": clip["file"],
        "images_analysees": analysees,
        "duree_minutes": round(duree_min, 2),
        "detections": {},
        "fausses": {},
    }

    for model_name, classes in attendu.items():
        for label in classes:
            compte = vues.get((model_name, label), 0)
            resultat["detections"][f"{model_name}/{label}"] = {
                "images_vues": compte,
                "taux": round(compte / analysees, 3) if analysees else 0.0,
            }

    for (model_name, label), compte in vues.items():
        if label in (attendu.get(model_name) or []):
            continue
        # Tolérance : certaines classes accompagnent normalement la scène
        if label in (clip.get("toleres") or []):
            continue
        resultat["fausses"][f"{model_name}/{label}"] = {
            "images_vues": compte,
            "par_minute": round(compte / duree_min, 1) if duree_min else 0.0,
        }

    return resultat


def run_benchmark(config_path: Path = BENCHMARK_CONFIG, models: list[str] | None = None,
                  stride: int | None = None, save: bool = True) -> dict:
    bench = load_benchmark(config_path)
    clips = bench.get("clips") or []
    if not clips:
        raise ValueError("Le jeu de test ne contient aucun clip")

    stride = stride or bench.get("stride", 5)
    config = load_config()
    imgsz = config.get("inference", {}).get("imgsz", 640)
    registry = ModelRegistry(config["models"])

    if models is None:
        models = sorted({m for clip in clips for m in (clip.get("attendu") or {})}
                        | set(bench.get("models") or []))
    if not models:
        models = [m for m, cfg in config["models"].items() if cfg.get("enabled", True)]

    log.info(f"banc de test : {len(clips)} clip(s), modèles {models}, 1 image sur {stride}")
    registry.warmup(models, imgsz)

    resultats = []
    for clip in clips:
        log.info(f"analyse de {clip['file']}...")
        resultats.append(_analyse_clip(clip, registry, models, stride, imgsz))

    rapport = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "stride": stride,
        "modeles": models,
        "clips": resultats,
        "synthese": _synthese(resultats),
    }

    if save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = RESULTS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(rapport, indent=2, ensure_ascii=False), encoding="utf-8")
        rapport["fichier"] = str(path)
        log.info(f"rapport enregistré : {path}")

    return rapport


def _synthese(resultats: list[dict]) -> dict:
    """Agrège par classe : un taux de détection, un volume de fausses détections."""
    detections: dict[str, list[float]] = {}
    fausses: dict[str, float] = {}

    for clip in resultats:
        for cle, valeurs in (clip.get("detections") or {}).items():
            detections.setdefault(cle, []).append(valeurs["taux"])
        for cle, valeurs in (clip.get("fausses") or {}).items():
            fausses[cle] = fausses.get(cle, 0.0) + valeurs["par_minute"]

    return {
        "taux_detection": {k: round(sum(v) / len(v), 3) for k, v in detections.items()},
        "fausses_par_minute": {k: round(v, 1) for k, v in sorted(
            fausses.items(), key=lambda kv: kv[1], reverse=True)},
    }


def list_results() -> list[Path]:
    if not RESULTS_DIR.exists():
        return []
    return sorted(RESULTS_DIR.glob("*.json"))


def load_result(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(avant: dict, apres: dict) -> dict:
    """Écart entre deux passages : c'est la seule preuve d'une amélioration."""
    a, b = avant["synthese"], apres["synthese"]
    lignes = []

    for cle in sorted(set(a["taux_detection"]) | set(b["taux_detection"])):
        avant_v = a["taux_detection"].get(cle)
        apres_v = b["taux_detection"].get(cle)
        lignes.append({
            "mesure": "taux de détection",
            "classe": cle,
            "avant": avant_v,
            "apres": apres_v,
            "ecart": round((apres_v or 0) - (avant_v or 0), 3),
            "amelioration": (apres_v or 0) > (avant_v or 0),
        })

    for cle in sorted(set(a["fausses_par_minute"]) | set(b["fausses_par_minute"])):
        avant_v = a["fausses_par_minute"].get(cle, 0.0)
        apres_v = b["fausses_par_minute"].get(cle, 0.0)
        lignes.append({
            "mesure": "fausses détections/min",
            "classe": cle,
            "avant": avant_v,
            "apres": apres_v,
            "ecart": round(apres_v - avant_v, 1),
            # Ici, moins il y en a, mieux c'est : l'écart s'interprète à l'envers.
            "amelioration": apres_v < avant_v,
        })

    return {"avant": avant.get("date"), "apres": apres.get("date"), "lignes": lignes}
