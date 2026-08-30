# SmokeWatch

Système de supervision vidéo intelligente pour site cimentier (Ciments du Maroc).
Détection temps réel de la fumée, du feu, du non-port des EPI et d'autres risques
sur les flux caméras, avec alertes immédiates aux équipes de sécurité.

| Document | Pour qui |
|---|---|
| [DOCUMENTATION.md](DOCUMENTATION.md) | architecture, choix de conception, pièges rencontrés |
| [docs/GUIDE_INSTALLATION.md](docs/GUIDE_INSTALLATION.md) | le technicien qui installe sur le serveur |
| [docs/GUIDE_OPERATEUR.md](docs/GUIDE_OPERATEUR.md) | l'équipe sécurité qui l'utilise au quotidien |
| [docs/GUIDE_REENTRAINEMENT.md](docs/GUIDE_REENTRAINEMENT.md) | corriger un modèle : jeux de données, méthode, déploiement |
| [notebooks/reentrainement.ipynb](notebooks/reentrainement.ipynb) | carnet Colab prêt à l'emploi |

## Architecture

```
Caméras (webcam, caméra IP RTSP, fichier vidéo, dossier d'images)
        │
        ▼
   app/capture.py ...... thread de lecture dédié, une source = une caméra
        │
        ▼
   app/pipeline.py ..... un thread par caméra, modèles en parallèle (OpenVINO, CPU)
        │
        ├──► app/tracking.py ... suivi des objets, comptage, franchissements
        ├──► app/zones.py ...... la détection est-elle dans une zone surveillée ?
        │         │
        │         ▼
        │    app/rules.py ...... classes surveillées, seuils, anti-répétition
        │         │
        │         ▼
        │    app/notifier.py ... snapshot + son + Telegram
        │    app/recorder.py ... clip vidéo (5 s avant / 10 s après)
        │    app/storage.py .... base SQLite (historique, sévérité, acquittement)
        │    app/health.py ..... état du pipeline et des caméras
        │
        ▼
   app/api.py .......... API REST + interface web (port 8000)
```

L'interface unique (http://localhost:8000) regroupe le mur de caméras, le tableau
de bord, les zones, les alertes, les cas d'usage, les rapports et l'état système.

## Démarrage

Prérequis : Python 3.13. Ni Docker ni ffmpeg ne sont nécessaires.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe scripts\export_openvino.py     # une seule fois

.\venv\Scripts\python.exe -u -m app.pipeline                                   # terminal 1
.\venv\Scripts\python.exe -m uvicorn app.api:app --host 0.0.0.0 --port 8000    # terminal 2
```

Interface : http://localhost:8000

### Sources vidéo

Une caméra peut être quatre choses, déclarées depuis l'interface :

| Source | Exemple | Usage |
|---|---|---|
| Webcam | `0` | développement |
| Caméra IP | `rtsp://user:mdp@192.168.1.42:554/stream1` | site réel |
| Fichier vidéo | `videos/incendie.mp4` | rejeu, mise au point, mesure |
| Dossier d'images | `videos/sequence/` | séquences annotées |

Le rejeu de fichiers n'est pas un accessoire : sans accès aux caméras du site, la
seule scène disponible serait une webcam de bureau, où il ne se passe jamais
rien. Une vidéo rejouée traverse exactement le même pipeline qu'une caméra.

### Déploiement serveur

`scripts/install.ps1` installe le pipeline et l'interface en services Windows
(démarrage au boot, redémarrage après incident, journaux tournants). NSSM requis,
à lancer en administrateur. `scripts/uninstall.ps1` fait l'inverse.

## Configuration

**Interface → Caméras** — ajouter, modifier, tester et supprimer une caméra. Le
pipeline s'aligne en quelques secondes, sans redémarrage. Plus besoin d'éditer un
fichier sur le serveur.

**Interface → Zones** — tracé des zones à la souris. Une zone peut *surveiller*
ou *masquer*, s'appliquer à certains modèles, à certaines heures, et porter ses
propres seuils. Enregistré dans `config/zones.json`, appliqué au cycle suivant.

**Interface → Paramètres** — détection et alertes activables par modèle.

**`config/config.yaml`** — modèles disponibles, seuils de confiance, cadence et
résolution par défaut.

**`.env`** — jeton du bot Telegram et destinataires (voir `.env.example`). Le
premier reçoit toutes les alertes, les suivants uniquement les critiques.

**`SMOKEWATCH_LOG_LEVEL=DEBUG`** — trace chaque détection.

## Modèles

Huit modèles YOLO entraînés sur Kaggle, convertis en OpenVINO pour l'inférence
sur processeur (aucun GPU requis) :

| Modèle | Classes | Fiabilité |
|---|---|---|
| `fire_smoke` | Fire, Smoke | validée |
| `arc` | Arc Flash, Sparks | correcte |
| `epi` | Hardhat, Mask, Safety Vest et leurs absences | rappel ~54 % sur NO-Hardhat |
| `gloves_glasses` | Gloves, Goggles et leurs absences, Fall-Detected | chute peu fiable |
| `conveyor` | crack (déchirure de bande) | jamais éprouvée sur site |
| `person_animal` | person, animal | correcte |
| `vehicles` | car, truck, bus, motorcycle, bicycle | plaque non lue |
| `load_control` | intact, torn, empty | **désactivé** — faux positifs confiants |

Outils : `scripts/export_openvino.py` (conversion), `scripts/inspect_models.py`
(classes de chaque modèle), `scripts/run_inference.py` (essai sur une image).

## Mesurer la qualité

Deux mécanismes se complètent, et le second dépend du premier.

**Le retour des opérateurs.** Chaque alerte peut être déclarée fausse d'un clic.
Ce geste alimente les indicateurs (taux de fausses alertes par modèle et par
caméra, délai moyen de prise en charge) *et* constitue une bibliothèque d'erreurs
étiquetées.

**Le banc de test.** Des clips de référence dont on sait ce qu'ils contiennent :

```powershell
copy config\benchmark.example.yaml config\benchmark.yaml   # décrire vos clips
.\venv\Scripts\python.exe scripts\benchmark.py run
.\venv\Scripts\python.exe scripts\benchmark.py compare --last
```

Sans lui, un changement de seuil ou un ré-entraînement se juge à l'impression.
Le vrai danger n'est pas de stagner : c'est de reculer sans le voir.

## Ré-entraîner un modèle

```powershell
.\venv\Scripts\python.exe scripts\export_dataset.py --model load_control --days 90
```

Produit un jeu de données YOLO issu de l'exploitation réelle : les alertes
marquées fausses deviennent des **images de fond** — c'est ainsi qu'on apprend à
un modèle à répondre « rien ici » — et les alertes justes fournissent des images
pré-annotées à partir des positions enregistrées.

Ensuite, sur Colab ou Kaggle : partir des poids actuels (pas de zéro), rester en
640 pixels (taille figée à l'export OpenVINO), mesurer avant/après avec le banc
de test, puis reconvertir avec `scripts/export_openvino.py`.

## Zones et suivi

Sans zone, un modèle analyse toute l'image : le détecteur d'EPI se déclenche sur
le parking, celui de véhicules sur la route derrière la clôture. **C'est la
première cause de fausses alertes sur site réel.**

Le suivi d'objets (`tracking: true` sur une caméra) attribue un identifiant
stable d'une image à l'autre. Il permet de compter sans doublon, de connaître un
sens de passage sur une ligne franchie, et surtout de n'alerter **qu'une fois par
personne** au lieu d'une fois par image — un deuxième ouvrier sans casque n'est
plus masqué par l'alerte du premier.

## Plaques et suivi entre caméras

**Lecture de plaques** (`plates: true`, suivi requis). Le module localise la
plaque dans le véhicule — par un modèle dédié s'il existe, sinon par vision
classique — puis lit et **vote sur plusieurs images du même véhicule**. C'est le
vote qui rend la lecture exploitable : une image isolée se trompe, dix images
concordantes ne se trompent pas. Rien n'est affirmé sous deux lectures qui
s'accordent, et sans moteur de lecture installé, le système signale une plaque
non lue plutôt que d'inventer un numéro.

Le moteur (easyocr) est **optionnel** et s'installe à part — voir
`requirements.txt`, la méthode compte : installé normalement, il remplacerait
OpenCV par sa variante *headless* et casserait la capture vidéo.

**Suivi d'une caméra à l'autre.** Le rapprochement s'appuie sur la classe,
l'apparence (histogramme de teintes), le délai, la topologie déclarée
(`voisins`) et, quand elle est connue, la plaque.

> Ce n'est **pas** une ré-identification au sens strict : il n'y a pas de modèle
> d'apparence. Deux ouvriers en tenue identique seront confondus. Seule une
> correspondance par plaque est certaine — l'interface distingue « certain »
> de « probable », et cette nuance ne doit pas disparaître.

## Enregistrement et rapports

**Clips d'alerte** — 5 s avant / 10 s après chaque alerte, liés à l'événement.

**Enregistrement continu** — optionnel (`recording: true` sur une caméra), par
segments de 5 minutes, avec rétention et **seuil d'espace libre** : sous 10 Go,
l'enregistrement s'arrête de lui-même. Un disque plein empêcherait le système
d'écrire ses alertes — la surveillance passe avant la conservation.

**Frise chronologique** — écran Alertes : la journée d'une caméra sur une barre,
chaque alerte marquée à son heure et colorée par gravité. Un clic affiche la
photo et le clip. C'est le geste de base après un incident : remonter le temps.

**Rapport PDF** — écran Rapports : gravité, localisation, fiabilité des
détections, délai de prise en charge. Destiné au responsable HSE, là où le CSV
s'adresse à qui veut retravailler les données.

## Sévérité des alertes

| Niveau | Cas | Anti-répétition |
|---|---|---|
| Critique | feu, fumée, arc électrique, personne au sol | 1 min |
| Haute | EPI manquant, déchirure de convoyeur | 5 min |
| Moyenne | présence, véhicules, chargement | 15 min |
| Technique | caméra hors ligne, incident système | 10 min |

Le délai s'applique par caméra, zone, modèle, classe — et par objet suivi quand
le suivi est actif. Chaque zone peut imposer son propre délai.

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest tests -q
```

179 tests couvrent les sources vidéo, la géométrie des zones et leurs horaires,
le suivi d'objets et le comptage, le moteur d'alerte, la persistance, les
indicateurs de qualité, l'API, le banc de test, l'enregistrement continu et le
rapport PDF. Ils s'exécutent aussi à chaque
envoi sur GitHub (`.github/workflows/tests.yml`).

## Sauvegarde

```powershell
.\venv\Scripts\python.exe scripts\backup.py create
.\venv\Scripts\python.exe scripts\backup.py restore <archive.zip>
```

Base d'alertes, caméras, zones et réglages. Le `.env` en est volontairement
exclu : une archive circule, un secret ne doit pas voyager avec.

## Limites connues

- Validé sur webcam et vidéos rejouées ; les caméras IP du site ne sont pas
  encore branchées.
- `load_control` produit des faux positifs confiants hors de son contexte
  d'entraînement : désactivé par défaut, ré-entraînement requis avec des images
  négatives.
- `Fall-Detected` peu représenté dans son dataset : seuil relevé à 0,80.
- Rappel `NO-Hardhat` / `NO-Mask` d'environ 54 % : à renforcer avec des images du
  site réel.
- `conveyor` n'a jamais été éprouvé sur une vraie bande transporteuse.
- Lecture de plaques (cas d'usage 9) non réalisée.
- Interface sans authentification : à réserver au réseau interne.
- Lecture de plaques sans modèle dédié : la localisation par vision classique
  tient sur une vue frontale nette, pas sur un angle marqué. Entraîner un modèle
  `plate` améliorerait nettement le résultat.
- Le rapprochement entre caméras n'est pas une ré-identification : sans modèle
  d'apparence, deux personnes habillées pareil sont confondues.

## Maintenance

Purge automatique au démarrage du pipeline : médias de plus de 30 jours, alertes
de plus d'un an. Journaux dans `logs/`, un fichier par jour, conservés 30 jours.
Export CSV de l'historique : interface → **Rapports**.
