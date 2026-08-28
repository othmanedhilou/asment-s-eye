# SmokeWatch

Système de supervision vidéo intelligente pour site cimentier (Ciments du Maroc).
Détection temps réel de la fumée, du feu, du non-port des EPI et d'autres risques
sur les flux caméras, avec alertes immédiates aux équipes de sécurité.

## Architecture

```
Caméras IP (RTSP)
        │
        ▼
   mediamtx ............ relais RTSP (conteneur Docker)
        │
        ▼
   app/pipeline.py ..... lecture des flux + inférence multi-modèles (OpenVINO, CPU)
        │
        ├──► app/rules.py ...... filtrage, seuils, anti-répétition
        │         │
        │         ▼
        │    app/notifier.py ... snapshot + son + Telegram
        │    app/recorder.py ... clip vidéo (5 s avant / 10 s après)
        │    app/storage.py .... base SQLite (historique, sévérité, acquittement)
        │
        ▼
   app/api.py .......... API REST + interface web (port 8000)
```

L'interface web unique (http://localhost:8000) regroupe le mur de caméras, le
tableau de bord, la gestion des alertes, les cas d'usage et les rapports.

## Démarrage

Prérequis : Python 3.13, Docker Desktop, ffmpeg.

```powershell
# 1. Environnement
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Relais vidéo
cd docker; docker compose up -d mediamtx; cd ..

# 3. Source vidéo de test (webcam locale)
sh docker/push_webcam.sh

# 4. Détection + interface
.\venv\Scripts\python.exe -m app.pipeline          # terminal 1
.\venv\Scripts\python.exe -m uvicorn app.api:app --port 8000   # terminal 2
```

Interface : http://localhost:8000

### Déploiement serveur

`scripts/install.ps1` installe le pipeline et l'interface en services Windows
(démarrage automatique, redémarrage en cas de crash). À exécuter en
administrateur sur la machine de supervision, NSSM requis.

## Configuration

**`config/config.yaml`** — caméras (URL RTSP, modèles associés), chemins des
modèles, seuils de confiance, cadence d'inférence.

**`.env`** — jeton du bot Telegram et destinataires (voir `.env.example`).
Le premier destinataire reçoit toutes les alertes, les suivants uniquement les
alertes critiques.

**Interface → Paramètres** — activation de la détection et des alertes par
modèle, appliquée immédiatement sans redémarrage.

## Modèles

Sept modèles YOLO entraînés sur Kaggle, convertis en OpenVINO pour accélérer
l'inférence sur CPU (aucun GPU requis) :

| Modèle | Classes |
|---|---|
| `fire_smoke` | Fire, Smoke |
| `epi` | Hardhat, Mask, Safety Vest et leurs absences, Person, machinery, vehicle, Safety Cone |
| `gloves_glasses` | Gloves, Goggles et leurs absences, Fall-Detected |
| `person_animal` | person, animal |
| `vehicles` | car, truck, bus, motorcycle, bicycle |
| `arc` | Arc Flash, Sparks |
| `load_control` | intact, torn, empty |

La correspondance avec les 12 cas d'usage du cahier des charges et l'état de
chacun sont visibles dans l'interface, page **Cas d'usage** (source :
`app/usecases.py`).

Pour convertir un nouveau modèle `.pt` en OpenVINO : `scripts/export_openvino.py`.

## Sévérité des alertes

| Niveau | Cas |
|---|---|
| Critique | feu, fumée, arc électrique, personne au sol |
| Haute | non-port des EPI |
| Moyenne | présence personne/animal, véhicules, chargement |

Les alertes critiques sont diffusées à tous les destinataires Telegram, les
autres au seul superviseur.

## Limites connues

- `load_control` produit des faux positifs confiants hors de son contexte
  d'entraînement : désactivé par défaut, ré-entraînement nécessaire avec des
  images négatives.
- `Fall-Detected` est peu représenté dans son dataset : seuil relevé à 0.80, un
  dataset dédié reste à entraîner.
- Le rappel de `NO-Hardhat` / `NO-Mask` est d'environ 54 % : à renforcer avec des
  images du site réel.
- Les zones d'intérêt (ROI) par caméra ne sont pas encore implémentées ; sans
  elles, les modèles analysent toute l'image.
- La lecture de plaques d'immatriculation (cas d'usage 9) et la surveillance de
  convoyeur (cas d'usage 12) restent à réaliser.

## Maintenance

Purge automatique au démarrage du pipeline : médias de plus de 30 jours,
alertes de plus d'un an (`cleanup_old_data` dans `app/storage.py`).

Export CSV de l'historique pour le service HSE : interface → **Rapports**.
