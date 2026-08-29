# SmokeWatch

Système de supervision vidéo intelligente pour site cimentier (Ciments du Maroc).
Détection temps réel de la fumée, du feu, du non-port des EPI et d'autres risques
sur les flux caméras, avec alertes immédiates aux équipes de sécurité.

📖 **[DOCUMENTATION.md](DOCUMENTATION.md)** — architecture détaillée, rôle de
chaque module, dimensionnement du serveur, pièges de production.

## Architecture

```
Caméras (webcam locale ou caméras IP en RTSP)
        │
        ▼
   app/capture.py ...... thread de lecture dédié (ne garde que la dernière image)
        │
        ▼
   app/pipeline.py ..... un thread par caméra, modèles en parallèle (OpenVINO, CPU)
        │
        ├──► app/zones.py ...... la détection est-elle dans une zone surveillée ?
        │         │
        │         ▼
        │    app/rules.py ...... classes surveillées, seuils, anti-répétition
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
tableau de bord, les zones, la gestion des alertes, les cas d'usage et les
rapports.

## Démarrage

Prérequis : Python 3.13. Docker et ffmpeg ne servent qu'à simuler une caméra IP.

```powershell
# 1. Environnement
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Conversion des modèles en OpenVINO (une seule fois par machine)
.\venv\Scripts\python.exe scripts\export_openvino.py

# 3. Détection + interface
.\venv\Scripts\python.exe -u -m app.pipeline                              # terminal 1
.\venv\Scripts\python.exe -m uvicorn app.api:app --host 0.0.0.0 --port 8000   # terminal 2
```

Interface : http://localhost:8000

### Source vidéo

Deux voies **mutuellement exclusives** — une webcam ne peut être ouverte que par
un seul programme à la fois :

- **Webcam directe** : `source: 0` dans `config/config.yaml`. Aucune dépendance.
- **RTSP** : retirer `source`, puis lancer le relais et le flux de test.
  ```powershell
  cd docker; docker compose up -d mediamtx; cd ..
  .\docker\push_webcam.ps1 -Device "Integrated Camera"
  ```
  Les vraies caméras du site parlent RTSP nativement : ni Docker ni ffmpeg ne
  sont nécessaires pour elles.

### Déploiement serveur

`scripts/install.ps1` installe le pipeline et l'interface en services Windows
(démarrage au boot, redémarrage après un crash, logs tournants). À exécuter en
administrateur, **NSSM requis**. Le script est rejouable et vérifie ses
prérequis. `scripts/uninstall.ps1` retire les services sans toucher aux données.

## Configuration

**`config/config.yaml`** — caméras (source ou URL RTSP, modèles associés, `fps`
et `workers` par caméra), chemins des modèles, seuils de confiance.

**`.env`** — jeton du bot Telegram et destinataires (voir `.env.example`). Le
premier reçoit toutes les alertes, les suivants uniquement les critiques.

**Interface → Zones** — tracé des zones d'intérêt à la souris, et choix des
modèles actifs dans chacune. Enregistré dans `config/zones.json`, appliqué au
cycle suivant.

**Interface → Paramètres** — activation de la détection et des alertes par
modèle, appliquée immédiatement.

**`SMOKEWATCH_LOG_LEVEL`** — `DEBUG` trace chaque détection ; `INFO` par défaut.

## Modèles

Huit modèles YOLO entraînés sur Kaggle, convertis en OpenVINO pour accélérer
l'inférence sur CPU (aucun GPU requis) :

| Modèle | Classes |
|---|---|
| `fire_smoke` | Fire, Smoke |
| `epi` | Hardhat, Mask, Safety Vest et leurs absences, Person, machinery, vehicle, Safety Cone |
| `gloves_glasses` | Gloves, Goggles et leurs absences, Fall-Detected |
| `person_animal` | person, animal |
| `vehicles` | car, truck, bus, motorcycle, bicycle |
| `arc` | Arc Flash, Sparks |
| `conveyor` | crack (déchirure de bande transporteuse) |
| `load_control` | intact, torn, empty |

La correspondance avec les 12 cas d'usage du cahier des charges et l'état de
chacun sont visibles dans l'interface, page **Cas d'usage** (source :
`app/usecases.py`).

Convertir un nouveau modèle `.pt` : `scripts/export_openvino.py`.
Lister les modèles présents et leurs classes : `scripts/inspect_models.py`.
Tester un modèle sur une image ou une vidéo : `scripts/run_inference.py`.

## Zones d'intérêt

Sans zone, un modèle analyse toute l'image : le détecteur d'EPI se déclenche sur
le parking, celui de véhicules sur la route derrière la clôture. **C'est la
première cause de fausses alertes sur site réel.**

Une zone est un polygone associé aux modèles qui ont un sens dedans (EPI dans
l'atelier, véhicules au quai). Les coordonnées sont normalisées, donc valides
quelle que soit la résolution. Une caméra sans zone est analysée en entier.

## Sévérité des alertes

| Niveau | Cas | Anti-répétition |
|---|---|---|
| Critique | feu, fumée, arc électrique, personne au sol | 1 min |
| Haute | non-port des EPI, déchirure de convoyeur | 5 min |
| Moyenne | présence personne/animal, véhicules, chargement | 15 min |

Le délai s'applique par caméra, zone, modèle et classe. Les alertes critiques
sont diffusées à tous les destinataires Telegram, les autres au seul superviseur.

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest tests -q
```

51 tests couvrent la géométrie des zones, le moteur d'alerte (sévérité, seuils,
anti-répétition) et la persistance (filtres, acquittement, statistiques, purge).

## Limites connues

- Validé sur une webcam ; les caméras IP du site ne sont pas encore branchées.
- `load_control` produit des faux positifs confiants hors de son contexte
  d'entraînement : désactivé par défaut, ré-entraînement nécessaire avec des
  images négatives.
- `Fall-Detected` est peu représenté dans son dataset : seuil relevé à 0.80.
- Le rappel de `NO-Hardhat` / `NO-Mask` est d'environ 54 % : à renforcer avec des
  images du site réel.
- `conveyor` n'a jamais été éprouvé sur une vraie bande transporteuse.
- La lecture de plaques d'immatriculation (cas d'usage 9) reste à réaliser.
- Interface sans authentification (choix assumé).

## Maintenance

Purge automatique au démarrage du pipeline : médias de plus de 30 jours, alertes
de plus d'un an (`cleanup_old_data` dans `app/storage.py`).

Journaux dans `logs/`, un fichier par jour, conservés 30 jours.

Export CSV de l'historique pour le service HSE : interface → **Rapports**.
