# SmokeWatch — Documentation technique

Système de supervision vidéo intelligente (VMS) pour site industriel cimentier.
Détection temps réel des risques HSE sur flux caméras, alertes immédiates aux
équipes de sécurité, historique et rapports.

---

## 1. Le problème et le choix d'architecture

### 1.1 Ce que le système doit faire

Surveiller en continu les flux vidéo du site et déclencher une alerte
exploitable quand un risque apparaît : départ de feu, fumée, ouvrier sans EPI,
personne au sol, arc électrique, véhicule en zone interdite, déchirure de
convoyeur.

La contrainte structurante n'est pas la détection elle-même, mais le **bruit** :
un système qui alerte des centaines de fois par jour est ignoré par les
opérateurs au bout d'une semaine. L'objectif du cahier des charges est de rester
sous **2 fausses alertes par jour et par caméra**.

### 1.2 Pourquoi Frigate a été abandonné

Le projet a d'abord été construit sur [Frigate](https://frigate.video), un VMS
open source. Trois blocages successifs ont conduit à l'écarter :

1. **Un seul modèle de détection par instance.** Frigate charge un détecteur
   unique partagé par toutes les caméras. Le projet a 8 modèles spécialisés, et
   plusieurs doivent tourner sur la même caméra (EPI *et* fumée sur la même
   zone). Ce n'est pas une limite de configuration, c'est son architecture.
2. **Interface figée.** Ni le design, ni la logique métier (sévérité,
   acquittement opérateur, cas d'usage du cahier des charges) ne sont
   modifiables.
3. **Coût CPU en doublon.** Une fois la détection fumée/feu ramenée dans le
   pipeline Python, Frigate continuait à faire tourner son propre détecteur
   OpenVINO sur le même processeur. Le simple fait de l'arrêter a fait passer le
   temps de cycle de **2600 ms à 1200 ms**.

Le système actuel est donc **entièrement autonome** : un seul processus de
détection, une seule interface web, un seul lien.

### 1.3 Architecture retenue

```
Source : webcam (0) · caméra IP (rtsp://) · fichier vidéo · dossier d'images
  v
app/capture.py ........ FrameSource : direct dans un thread dédié,
  |                     fichier lu séquentiellement sans perte
  v
app/cameras.py ........ registre des caméras, modifiable depuis l'interface
  v
app/pipeline.py ....... un thread par caméra ; N modèles en parallèle par image
  |                     (ThreadPoolExecutor + OpenVINO)
  v
app/tracking.py ....... suivi des objets, comptage, franchissement de ligne
  v
app/zones.py .......... filtrage géographique : la détection est-elle dans une
  |                     zone surveillée pour ce modèle, à cette heure ?
  v
app/rules.py .......... filtrage métier : classes surveillées, seuils renforcés,
  |                     anti-répétition calé sur la sévérité
  v
app/notifier.py ....... snapshot + bip + Telegram (routage par sévérité)
app/recorder.py ....... clip MP4 : 5 s avant / 10 s après l'alerte
app/storage.py ........ SQLite : historique, sévérité, acquittement, purge
app/health.py ......... état du pipeline et des caméras, publié en continu
app/logging_setup.py .. journal console + fichier tournant (logs/)
  v
app/api.py ............ API REST + interface web (port 8000)
```

Les deux processus à lancer sont `app.pipeline` (détection) et `app.api`
(interface). Ils communiquent **par le disque**, pas par le réseau :

| Canal | Fichier | Sens |
|---|---|---|
| Image live | `data/live/<caméra>.jpg` | pipeline → API |
| Alertes | `data/smokewatch.db` (SQLite) | pipeline → API |
| Réglages | `data/settings.json` | API → pipeline |
| Caméras | `config/cameras.json` | API → pipeline |
| Zones | `config/zones.json` | API → pipeline |
| Santé | `data/health.json` | pipeline → API |

Ce découplage a un avantage concret : **redémarrer l'interface n'interrompt pas
la détection**, et inversement.

---

## 2. Les modules, un par un

### 2.1 `app/capture.py` — lecture du flux

```python
class RTSPStream:
    def __init__(self, url, open_timeout=10.0)
```

`FrameSource` accepte quatre formes de source :

| Forme | Exemple | Lecture |
|---|---|---|
| Webcam | `0` | direct, thread dédié |
| Caméra IP | `rtsp://192.168.1.42/...` | direct, thread dédié |
| Fichier vidéo | `videos/incendie.mp4` | séquentielle, en boucle |
| Dossier d'images | `videos/sequence/` | séquentielle, en boucle |

**Les deux dernières formes ne sont pas un accessoire.** Sans accès aux caméras
du site, la seule scène disponible est la webcam de la machine de développement,
où il ne se passe jamais rien : impossible d'y régler un seuil, de mesurer un
modèle ou de produire des statistiques crédibles. Une vidéo rejouée traverse
exactement le même pipeline qu'une caméra — mêmes zones, mêmes modèles, mêmes
alertes.

Les deux natures de source imposent deux modes de lecture opposés :

- **Direct** — un thread vide le flux en continu et ne garde que l'image la plus
  récente. L'inférence saute des images plutôt que d'accumuler du retard.
- **Fichier** — lecture séquentielle, sans thread et **sans perte** : chaque
  image demandée est la suivante du fichier. Une vidéo rejouée doit donner le
  même résultat à chaque exécution, sinon elle ne peut servir ni à régler des
  seuils ni à comparer deux versions d'un modèle.

**Le point critique : le thread de lecture dédié.** L'inférence prend environ 1 s
par cycle, la caméra émet 25 à 30 images par seconde. Si on lisait le flux dans
la même boucle que l'inférence, le lecteur prendrait du retard, le serveur RTSP
accumulerait les images puis fermerait la connexion (`reader is too slow,
discarding frames` → `i/o timeout`), et le pipeline se figerait sur une lecture
qui ne répond plus.

C'est exactement le bug rencontré en production. La solution :

```python
def _reader(self):          # thread séparé, vide le flux en continu
    while not self._stopped.is_set():
        ok, frame = self.cap.read()
        with self._lock:
            self._latest = frame        # seule la dernière image survit
```

L'inférence pioche `self._latest` quand elle est prête. Elle ne peut
structurellement plus prendre de retard : elle saute des images au lieu de les
accumuler.

`frames_at_fps(fps)` cadence la sortie et abandonne si le flux est muet plus de
15 s, ce qui laisse le pipeline se reconnecter.

Le transport RTSP est forcé en **TCP** dès l'import :

```python
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
```

En UDP (le défaut), le NAT de Docker Desktop coupait le flux après une dizaine
de secondes.

### 2.2 `app/detectors.py` — chargement et préchauffage des modèles

`ModelRegistry` charge les modèles YOLO à la demande et les garde en mémoire.

**Le préchauffage n'est pas un détail de confort.** `YOLO(chemin)` ne lit que
les métadonnées : la compilation OpenVINO, elle, n'a lieu qu'à la **première
inférence** — mesurée à 5,3 s pour le premier modèle (initialisation du runtime
comprise) puis ~1 s par modèle suivant.

Si on laisse la boucle principale la déclencher, plusieurs modèles compilent
simultanément dans le pool de threads pendant que le flux caméra tourne. Sur une
machine à deux cœurs, la contention est telle que **le pipeline ne démarre
jamais** : le processus consomme du CPU sans jamais produire un seul cycle. Le
symptôme trompe, car il ressemble à un blocage réseau.

`warmup()` compile les modèles **un par un** avant d'entrer dans la boucle : une
dizaine de secondes au démarrage, puis plus rien.

### 2.3 `app/pipeline.py` — la boucle principale

```python
os.environ.setdefault("OMP_NUM_THREADS", "1")   # AVANT tout import OpenVINO
```

**Cette ligne doit rester la première du fichier.** OpenVINO utilise par défaut
tous les cœurs pour *chaque* modèle. Exécuter 6 modèles en parallèle revenait
alors à demander 6 × N threads sur N cœurs : le CPU passait son temps à changer
de contexte et le pipeline n'avançait plus du tout, tout en consommant 100 % du
processeur. Un thread par modèle, N modèles en parallèle : le CPU est utilisé
sans être saturé.

Le cœur de la boucle :

```python
active_models = [m for m in available_models if is_detect_enabled(m)]
futures = [executor.submit(_run_one_model, registry, m, frame, imgsz)
           for m in active_models]
```

La liste des modèles actifs est **relue à chaque image**, pas figée au
démarrage : cocher une case dans l'interface prend effet au cycle suivant, sans
redémarrage.

**Une caméra, un thread.** `main()` démarre `run_camera` dans un thread par
caméra déclarée. Le choix du thread plutôt que du processus est délibéré : les
modèles OpenVINO sont chargés **une seule fois et partagés** par toutes les
caméras (un jeu de modèles par processus coûterait plusieurs centaines de Mo
chacun), et l'inférence libère le GIL, donc le parallélisme est réel.

Chaque caméra peut surcharger `fps`, `imgsz` et `workers` dans sa propre section
de `config.yaml` : sur une machine contrainte, on ralentit une caméra secondaire
sans toucher aux autres.

**Un modèle qui échoue n'arrête plus le cycle.** `_run_one_model` absorbe ses
exceptions et renvoie une liste vide : sans cela, un seul modèle défaillant
remonterait par `future.result()` et interromprait la surveillance de tous les
autres.

Chaque détection est dessinée sur une copie de l'image (`annotated`), qui sert à
la fois au flux live et aux clips vidéo — l'opérateur voit les boîtes
englobantes.

En cas de perte de flux, la boucle extérieure reconnecte toutes les 2 s.

### 2.4 `app/cameras.py` — registre des caméras

Tant qu'ajouter une caméra oblige à éditer `config.yaml` sur le serveur, le
système dépend de la personne qui sait le faire. Et le jour du branchement sur
site, la fenêtre d'intervention est courte : il faut un formulaire et un bouton
« tester », pas une séance de débogage devant le responsable.

Les caméras vivent donc dans `config/cameras.json`, écrit par l'application et
**amorcé depuis `config.yaml` au premier démarrage** — rien de l'existant n'est
perdu. `config.yaml` reste la référence pour les modèles et les réglages
d'inférence.

Le `CameraSupervisor` du pipeline compare toutes les cinq secondes les caméras
configurées à celles qui tournent, et démarre, arrête ou redémarre les threads en
conséquence. Ajouter une caméra depuis l'interface serait sans effet s'il fallait
redémarrer le pipeline pour la voir apparaître.

Une caméra peut être **mise en pause** (`enabled: false`) : elle reste
configurée, avec ses zones, mais n'est plus traitée. C'est ce qu'il faut pendant
une maintenance, plutôt que la supprimer et tout ressaisir ensuite.

### 2.5 `app/health.py` — état du système

Un système de surveillance qui s'arrête devient **silencieux**, et le silence
ressemble exactement au calme. C'est le pire mode de défaillance possible :
personne ne remarque rien, jusqu'au jour où l'on cherche l'enregistrement d'un
incident qui n'a jamais été capté.

Le pipeline publie donc son état — état de chaque caméra, temps de cycle, images
par seconde réelles, erreurs, objets suivis — dans `data/health.json`, que
l'interface lit. Au-delà de 20 secondes sans mise à jour, le pipeline est déclaré
arrêté et l'interface l'affiche en rouge.

Une caméra injoignable plus de 30 secondes déclenche une **alerte technique**,
qui suit le même circuit que les alertes métier — journal, base, Telegram. Une
caméra débranchée n'est pas un événement de sécurité au travail, mais elle doit
réveiller quelqu'un au même titre.

La sévérité `technique` est distincte pour ne pas polluer les statistiques HSE.

### 2.6 `app/tracking.py` — suivi des objets

Chaque image était analysée isolément : le système voyait « une personne », puis
« une personne », sans jamais comprendre que c'était **la même**. Trois
conséquences — impossible de compter (trois images d'un camion font trois
camions), impossible de connaître un sens de passage, et le seul rempart contre
le flot d'alertes restait le délai anti-répétition, qui est un pansement plutôt
qu'une compréhension.

**Pourquoi ne pas utiliser le suivi d'Ultralytics.** `model.track(persist=True)`
existe, mais son état est attaché à **l'objet modèle**. Or les modèles sont
chargés une seule fois et partagés par toutes les caméras — c'est précisément ce
qui permet d'en faire tourner huit sur une machine modeste. Deux caméras
partageant un modèle mélangeraient donc leurs pistes, et personne ne le verrait
avant d'obtenir des comptages absurdes.

Le suiveur écrit ici est instancié **par caméra et par modèle**. Il associe les
détections en deux temps, et le second n'est pas un raffinement optionnel :

1. **par recouvrement de boîtes** — le critère le plus sûr, mais il suppose que
   l'objet a peu bougé entre deux images ;
2. **par proximité des centres** — indispensable ici. L'inférence tourne à une ou
   deux images par seconde ; un piéton parcourt alors plus que sa propre largeur
   et les boîtes ne se recouvrent plus du tout. Sans ce rattrapage, chaque image
   créerait un nouvel objet.

La tolérance reste bornée à une fois et demie la taille de l'objet : confondre
deux personnes distinctes est plus grave que manquer un franchissement.

Ce que le suivi apporte concrètement :

- **une alerte par personne** au lieu d'une par image — l'identifiant de suivi
  entre dans la clé anti-répétition, si bien qu'un deuxième ouvrier sans casque
  n'est plus masqué cinq minutes par l'alerte du premier ;
- le **comptage de franchissements** avec le sens, via des zones de type `ligne` ;
- l'**occupation** d'une zone : des objets distincts, pas des détections.

Le suivi coûte du temps de calcul : il s'active par caméra (`tracking: true`).

### 2.7 `app/benchmark.py` — banc de test

Sans mesure, tout changement — relever un seuil, ré-entraîner un modèle — se
juge à l'impression. Le vrai danger n'est pas de stagner : c'est de **reculer
sans le voir**. On relève un seuil, les fausses alertes chutent, on est satisfait
— et le modèle rate désormais un ouvrier sans casque sur trois au lieu d'un sur
deux. Personne ne le remarque, parce qu'un manque ne s'affiche nulle part.

Le principe tient en deux chiffres par classe :

| Mesure | Sur quels clips | Ce qu'elle dit |
|---|---|---|
| Taux de détection | ceux où la classe **doit** apparaître | part des images où le modèle la voit |
| Fausses détections/min | ceux où elle ne doit **pas** apparaître | volume de bruit produit |

Le clip le plus précieux du jeu est celui où il ne se passe **rien** : c'est lui
qui mesure le bruit, et le bruit est ce qui fait abandonner un système.

**Ce que la mesure ne vaut pas.** Les clips sont annotés au niveau du clip, pas
image par image : ce n'est donc pas une mAP, et les chiffres ne sont pas
comparables à ceux d'une publication. C'est suffisant pour comparer un avant et
un après sur le même jeu — l'usage visé — et il vaut mieux le dire que le
laisser croire.

La comparaison s'interprète dans les deux sens : sur le taux de détection, plus
haut vaut mieux ; sur le bruit, c'est l'inverse. `compare()` porte cette logique
plutôt que de la laisser au lecteur.

### 2.8 `app/zones.py` — zones d'intérêt (ROI)

Sans zones, un modèle analyse toute l'image : le détecteur d'EPI se déclenche sur
le parking, celui de véhicules sur la route derrière la clôture. **C'est la
première cause de fausses alertes sur site réel**, et donc l'obstacle principal à
l'objectif « moins de 2 fausses alertes par jour et par caméra ».

Une zone est un polygone associé aux modèles qui ont un sens dedans :

```json
{
  "webcam_test": [
    {"name": "atelier", "polygon": [[0.1, 0.2], [0.7, 0.2], [0.7, 0.9], [0.1, 0.9]],
     "models": ["epi", "gloves_glasses"]},
    {"name": "quai", "polygon": [[0.7, 0.3], [1.0, 0.3], [1.0, 1.0], [0.7, 1.0]],
     "models": ["vehicles"]}
  ]
}
```

Trois décisions de conception :

**Coordonnées normalisées (0.0 à 1.0).** Les zones restent valides si la
résolution de la caméra change, et l'interface peut les dessiner sans connaître
les dimensions réelles du flux.

**Point d'ancrage au sol.** L'appartenance est testée sur le milieu du bord bas
de la boîte, pas sur son centre : pour une personne debout ou un véhicule, c'est
le point de contact avec le sol. Le centre placerait un ouvrier au niveau de son
torse, donc potentiellement hors zone alors que ses pieds y sont.

**Test par lancer de rayon**, implémenté à la main plutôt qu'avec `shapely` :
une dépendance de moins à installer sur le serveur, pour vingt lignes stables.
Les polygones concaves sont gérés correctement (couvert par les tests).

Une caméra **sans zone déclarée** analyse toute l'image — le comportement
d'avant, pour ne rien casser tant que les zones ne sont pas dessinées.

Les zones se tracent à la souris dans l'interface (page **Zones**), sont
enregistrées dans `config/zones.json` et **prises en compte au cycle suivant**,
sans redémarrage. Elles sont aussi tracées sur l'image live, pour que l'opérateur
voie ce qui est réellement surveillé.

### 2.9 `app/rules.py` — du bruit brut aux alertes exploitables

C'est la couche qui rend le système utilisable. Trois filtres successifs.

**a) Classes surveillées.** Toutes les détections ne sont pas des alertes.
`ALERT_LABELS` ne retient que ce qui constitue un risque :

```python
ALERT_LABELS = {
    "arc":            {"Arc Flash", "Sparks"},
    "conveyor":       {"crack"},
    "epi":            {"NO-Hardhat", "NO-Mask", "NO-Safety Vest"},
    "fire_smoke":     {"Fire", "Smoke"},
    "gloves_glasses": {"NO-Gloves", "NO-Goggles", "Fall-Detected"},
    "load_control":   {"torn", "empty"},
    "person_animal":  {"person", "animal"},
    "vehicles":       {"car", "truck", "bus", "motorcycle", "bicycle"},
}
```

Un ouvrier **avec** casque (`Hardhat`) est détecté mais ne déclenche rien.

**b) Seuils renforcés sur les classes peu fiables.**

```python
MIN_CONFIDENCE_OVERRIDE = {
    ("gloves_glasses", "Fall-Detected"): 0.80,
    ("load_control", "torn"):            0.75,
    ("load_control", "empty"):           0.75,
}
```

Correctif temporaire, à retirer après ré-entraînement de ces modèles.

**c) Anti-répétition calé sur la sévérité.** C'est le levier qui permet de tenir
l'objectif du cahier des charges.

```python
COOLDOWN_BY_SEVERITY = {
    "critique": 60,    # 1 min
    "haute":    300,   # 5 min
    "moyenne":  900,   # 15 min
}
```

Le délai s'applique par **(caméra, zone, modèle, classe)** — un véhicule sur le
quai et un autre devant l'atelier sont deux situations distinctes, chacune doit
alerter. Un ouvrier sans casque qui
reste à son poste génère une alerte toutes les 5 minutes, pas une par image.
Avec l'ancien délai unique de 15 s, une personne assise devant la caméra
produisait **448 alertes en 24 h** ; après ce changement, la même scène en
produit une poignée.

### 2.10 `app/storage.py` — base de données et sévérité

**La sévérité métier** est calculée à l'enregistrement, pas saisie :

```python
SEVERITY_BY_MODEL = {
    "fire_smoke": "critique",   "arc":            "critique",
    "conveyor":   "haute",      "gloves_glasses": "haute",
    "epi":        "haute",      "load_control":   "moyenne",
    "person_animal": "moyenne", "vehicles":       "moyenne",
}
SEVERITY_BY_LABEL = {"Fall-Detected": "critique"}   # prime sur le modèle
```

`Fall-Detected` appartient au modèle `gloves_glasses` (classé « haute »), mais
une personne au sol est toujours critique : la table par label a la priorité.

**Schéma de la table `alerts`** (SQLAlchemy → SQLite) :

| Colonne | Rôle |
|---|---|
| `id` | clé primaire, sert à lier le clip vidéo |
| `camera`, `model`, `label`, `confidence` | origine de la détection |
| `message`, `timestamp` | libellé et horodatage |
| `snapshot`, `clip` | chemins des médias |
| `severity` | critique / haute / moyenne |
| `acknowledged`, `ack_by`, `ack_at` | traçabilité de la prise en charge |

`_migrate()` ajoute les colonnes manquantes au démarrage (`PRAGMA table_info`
puis `ALTER TABLE`) : une base créée par une version antérieure continue de
fonctionner sans manipulation.

`cleanup_old_data()` purge au démarrage les médias de plus de 30 jours et les
alertes de plus d'un an — sans quoi le disque se remplit silencieusement.

### 2.11 `app/notifier.py` — notification

Pour chaque alerte : log console, bip sonore (`winsound`), snapshot JPEG dans
`clips/snapshots/`, insertion en base, puis envoi Telegram.

**Routage par sévérité** — tout le monde n'a pas besoin de tout recevoir :

```python
def _recipients_for(severity):
    if severity == "critique":
        return TELEGRAM_CHAT_IDS        # toute l'équipe
    return TELEGRAM_CHAT_IDS[:1]        # superviseur seul
```

Les destinataires viennent de `.env` (`TELEGRAM_CHAT_IDS=111,222,333`).
Telegram a été retenu plutôt que SMS ou WhatsApp : gratuit, illimité, aucun
compte tiers payant, et l'application est déjà installée sur les téléphones.

### 2.12 `app/recorder.py` — clips vidéo

Un snapshot ne dit pas ce qui s'est passé **avant** l'alerte. Le `ClipRecorder`
garde en mémoire un tampon glissant des 5 dernières secondes :

```python
self._buffer = deque(maxlen=int(pre_seconds * fps))
```

Au déclenchement, il concatène ce tampon avec les 10 secondes suivantes et écrit
un MP4 (codec `mp4v`), puis lie le fichier à l'alerte en base.

Un seul clip à la fois : `trigger()` sort immédiatement si un enregistrement est
en cours, sinon des dizaines de vidéos se chevaucheraient.

### 2.12b `app/recorder.py` — enregistrement continu

Les clips d'alerte montrent ce que le système a **vu**. L'enregistrement continu
montre ce qui s'est **passé**, y compris ce que le système a manqué — et c'est
souvent la seule preuve disponible après un incident.

C'est aussi la fonction qui peut mettre le serveur à genoux, puisqu'elle écrit
en permanence. Trois précautions l'encadrent :

- **des segments de 5 minutes** plutôt qu'un fichier par jour : un fichier
  corrompu ne fait perdre que quelques minutes, et une journée se consulte sans
  télécharger des dizaines de Go ;
- **une rétention automatique** (7 jours par défaut), appliquée à chaque
  rotation de segment ;
- **un seuil d'espace libre** (10 Go) sous lequel l'enregistrement s'arrête de
  lui-même. Un disque plein empêcherait le système d'écrire ses snapshots et sa
  base : la surveillance s'arrêterait pour conserver des vidéos, ce qui est
  l'inverse de la priorité.

L'enregistrement conserve l'image **brute**, sans les boîtes de détection :
celles-ci sont une interprétation du système, pas un constat, et n'ont pas leur
place sur un enregistrement qui peut servir de preuve.

Désactivé par défaut, activable par caméra (`recording: true`).

### 2.12c `app/report.py` — rapport PDF pour le HSE

L'export CSV sert à qui veut retravailler les données. Un responsable HSE, lui,
veut un document qu'il peut lire, classer et présenter — avec les chiffres qui
engagent une décision, pas la liste de tous les événements.

Le rapport répond à quatre questions, dans cet ordre : combien d'alertes et de
quelle gravité, où (caméra et zone), le système est-il crédible (part de fausses
alertes par détection), et les alertes atteignent-elles quelqu'un (délai moyen de
prise en charge).

### 2.13 `app/settings.py` — réglages en direct

`data/settings.json` porte, pour chaque modèle, deux booléens : `detect`
(exécuter le modèle) et `alert` (déclencher une alerte). Modifiables depuis
l'interface, sans toucher au code ni redémarrer.

Le pipeline interroge ces réglages **plusieurs dizaines de fois par seconde**.
Une première version relisait le fichier à chaque appel — 28 lectures disque par
seconde. D'où le cache d'une seconde : assez court pour rester réactif à l'UI,
assez long pour supprimer le martèlement disque.

### 2.14 `app/usecases.py` — traçabilité du cahier des charges

Registre des **12 cas d'usage** du cahier des charges, mappés sur les modèles
réellement entraînés, avec un état honnête par cas : `operationnel`, `partiel`,
`a_entrainer`. Un modèle peut couvrir plusieurs cas (`fire_smoke` couvre fumée
*et* feu ; `epi` couvre casque, gilet et masque).

### 2.15 `app/api.py` — API REST et interface

FastAPI. Endpoints :

| Route | Rôle |
|---|---|
| `GET /` | interface web |
| `GET /video/{caméra}.jpg` | dernière image annotée |
| `GET /api/cameras` | caméras, modèles affectés, zones, état en ligne et âge de la dernière image |
| `GET /api/alerts` | historique filtrable (modèle, sévérité, acquittement, période) |
| `POST /api/alerts/{id}/ack` | prise en charge par un opérateur |
| `GET /api/stats/summary` | indicateurs 24 h / 7 j |
| `GET /api/stats/timeline` | activité heure par heure |
| `GET /api/settings` · `POST /api/settings/{modèle}/{clé}` | réglages en direct |
| `GET /api/zones` · `GET/POST /api/zones/{caméra}` | lecture et enregistrement des zones |
| `GET /api/usecases` | les 12 cas d'usage et leur état |
| `GET /api/snapshot` · `GET /api/clip` | médias d'une alerte |
| `GET /api/export/csv` | export pour le service HSE |

L'interface (`web/`) comporte six pages : tableau de bord, mur de caméras,
**zones**, alertes, cas d'usage, rapports.

**L'état « en ligne » repose sur la fraîcheur de l'image**, pas sur l'existence
du fichier : une vignette figée laissée par un pipeline mort passerait sinon pour
du direct indéfiniment. Au-delà de 15 s sans nouvelle image, la caméra est
déclarée hors ligne.

### 2.16 `app/logging_setup.py` — journalisation

Tout passait auparavant par `print()` : rien n'était conservé. Après un incident
nocturne — flux perdu, modèle qui plante, alerte non partie — il ne restait
aucune trace à analyser le lendemain.

Les messages partent maintenant vers la console **et** vers `logs/`, avec un
fichier par jour, rotation automatique et rétention de 30 jours. Le niveau se
règle sans toucher au code :

```powershell
$env:SMOKEWATCH_LOG_LEVEL = "DEBUG"   # trace chaque détection
```

`DEBUG` est précieux pour comprendre pourquoi une zone ne déclenche pas ou pour
régler un seuil ; trop verbeux en exploitation, où `INFO` suffit.

---

## 3. Les modèles

Huit modèles YOLO entraînés séparément, convertis en OpenVINO.

| Modèle | Classes d'alerte | Sévérité | Fiabilité |
|---|---|---|---|
| `fire_smoke` | Fire, Smoke | critique | validé en conditions réelles |
| `arc` | Arc Flash, Sparks | critique | OK |
| `epi` | NO-Hardhat, NO-Mask, NO-Safety Vest | haute | rappel ~54 % sur NO-Hardhat |
| `gloves_glasses` | NO-Gloves, NO-Goggles, Fall-Detected | haute | Fall-Detected peu fiable |
| `conveyor` | crack | haute | jamais validé sur site |
| `person_animal` | person, animal | moyenne | OK |
| `vehicles` | car, truck, bus, motorcycle, bicycle | moyenne | OK (plaque non lue) |
| `load_control` | torn, empty | moyenne | **désactivé** (voir 3.2) |

### 3.1 Pourquoi OpenVINO

La machine cible n'a **pas de GPU**. PyTorch sur CPU est lent. OpenVINO est le
runtime d'inférence optimisé d'Intel pour processeur : sur les mêmes modèles et
le même matériel, le gain mesuré est de l'ordre de **2,5×**, sans rien changer
au code métier.

Conversion (une seule fois par machine) :

```powershell
.\venv\Scripts\python.exe scripts\export_openvino.py
```

Chaque `.pt` produit un dossier `models/smokewatch_<nom>_best_openvino_model`.

**Attention : la taille d'entrée est figée à l'export** (640×640). Mettre
`imgsz: 480` dans `config.yaml` fait planter l'inférence — il faut réexporter à
la taille voulue.

### 3.2 Le cas `load_control`

Ce modèle détecte `empty` avec une confiance de **0,89** sur une scène de bureau
qui n'a rien à voir avec du chargement de camion. Ce n'est pas un problème de
seuil : le modèle est confiant *et* faux, donc aucun seuillage ne le corrigera.

Cause probable : son dataset d'entraînement ne contient aucune image négative
(sans chargement). Le modèle n'a jamais appris à répondre « rien ici » et force
une classification parmi `intact` / `torn` / `empty` quoi qu'il voie.

Il est donc **désactivé par défaut** (`data/settings.json`), tout en restant
visible et activable dans l'interface. Le correctif réel est un ré-entraînement
avec des images négatives.

---

## 4. Configuration

### 4.1 `config/config.yaml`

```yaml
cameras:
  webcam_test:
    source: 0                                   # webcam locale
    rtsp_url: "rtsp://localhost:8554/webcam"    # utilisé si `source` absent
    models: [arc, conveyor, epi, fire_smoke,
             gloves_glasses, load_control, person_animal, vehicles]

models:
  fire_smoke:
    file: models/smokewatch_fire_smoke_best_openvino_model
    conf: 0.35
    enabled: true

inference:
  fps: 4       # images analysées par seconde et par caméra
  imgsz: 640   # DOIT correspondre à la taille figée à l'export OpenVINO
```

Chaque caméra peut surcharger `fps`, `imgsz` et `workers`, et tourne dans son
propre thread. Déclarer une caméra supplémentaire suffit à la mettre en service :

```yaml
  quai_chargement:
    rtsp_url: "rtsp://user:motdepasse@192.168.1.42:554/stream1"
    models: [vehicles, person_animal, epi]
    fps: 2        # cadence propre à cette caméra
    workers: 2    # threads d'inférence dédiés
```

**Deux niveaux d'activation, à ne pas confondre :**

- `enabled` dans `config.yaml` → le modèle n'est **jamais chargé** (gain mémoire)
- `detect` / `alert` dans `data/settings.json` → pilotage **en direct** depuis
  l'interface, modèle chargé

Ajouter une caméra revient à ajouter une entrée sous `cameras:`. Le mur de
caméras de l'interface la reprend automatiquement.

### 4.2 `.env`

```
TELEGRAM_BOT_TOKEN=<jeton BotFather>
TELEGRAM_CHAT_IDS=<id1>,<id2>,<id3>
```

**Ce fichier ne doit jamais être versionné** (il est dans `.gitignore`). Un
jeton exposé permet de prendre le contrôle du bot : envoyer de fausses alertes à
l'équipe sécurité, lire les messages. En cas d'exposition, le révoquer via
`/revoke` auprès de `@BotFather` et mettre à jour `.env` sur chaque machine.

Obtenir un `chat_id` : envoyer un message au bot, puis appeler
`https://api.telegram.org/bot<TOKEN>/getUpdates`.

---

## 5. Installation et exploitation

### 5.1 Mise en route sur une machine neuve

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt openvino
.\venv\Scripts\python.exe scripts\export_openvino.py     # une seule fois
```

Puis, dans deux terminaux :

```powershell
.\venv\Scripts\python.exe -u -m app.pipeline
.\venv\Scripts\python.exe -m uvicorn app.api:app --host 0.0.0.0 --port 8000
```

Interface : **http://localhost:8000**

Le dossier `venv/` et les modèles ne sont pas versionnés : ils se recréent sur
chaque machine.

Les versions sont **épinglées** dans `requirements.txt`. Sans cela, deux machines
installées à quelques mois d'écart n'ont pas le même environnement, et un modèle
exporté par l'une peut refuser de se charger sur l'autre.

Vérifier que tout est sain :

```powershell
.\venv\Scripts\python.exe -m pytest tests -q            # logique métier
.\venv\Scripts\python.exe scripts\inspect_models.py    # modèles et classes
```

### 5.2 Source vidéo

Deux voies, **mutuellement exclusives** — une webcam ne peut être ouverte que
par un seul programme à la fois :

- **Webcam directe** (`source: 0`) : le pipeline lit la caméra. Aucune
  dépendance externe. C'est le mode de développement.
- **RTSP** (retirer `source`) : mediamtx tourne dans Docker, `ffmpeg` y pousse
  la webcam. Utile uniquement pour simuler une caméra IP — les vraies caméras du
  site parlent RTSP nativement et n'ont besoin ni d'ffmpeg ni de mediamtx.

Lister les caméras DirectShow disponibles :

```powershell
ffmpeg -list_devices true -f dshow -i dummy
```

### 5.3 Déploiement en entreprise

**Ce n'est pas un logiciel de bureau.** Un `.exe` installé sur chaque poste
poserait trois problèmes : il faudrait le maintenir sur chaque machine, il ne
tournerait que pendant la session de l'utilisateur, et la détection s'arrêterait
dès l'extinction du PC.

Le modèle est **serveur + navigateur** :

```
Serveur (PC dédié, allumé en permanence, sur le réseau de l'entreprise)
   |-- app.pipeline   : service Windows, redémarrage automatique
   |-- app.api        : service Windows, port 8000
                          |
   Équipe sécurité --------+  navigateur : http://ip-serveur:8000
                          +  Telegram sur téléphone (alertes push)
```

Côté équipe sécurité : **aucune installation**. Un navigateur, et l'application
Telegram déjà présente sur leur téléphone.

`scripts/install.ps1` installe les deux processus en services Windows
(démarrage au boot, redémarrage automatique après un crash, logs tournants). Il
est **rejouable** : le relancer après une mise à jour du code réinstalle
proprement les services. `scripts/uninstall.ps1` fait l'inverse sans toucher au
code, aux modèles ni à la base.

Il exige NSSM (`https://nssm.cc/download`) dans le PATH — Python n'étant pas un
service Windows natif — et vérifie avant d'agir : droits administrateur, présence
de NSSM, environnement virtuel complet, port 8000 libre. Chacune de ces erreurs
produirait sinon un échec au milieu de l'installation, avec des services à moitié
créés.

### 5.4 Sauvegarde et mise à jour

```powershell
.\venv\Scripts\python.exe scripts\backup.py create
.\venv\Scripts\python.exe scripts\backup.py restore <archive.zip>
```

Sont sauvegardés la base d'alertes, les caméras, les zones, les réglages et la
configuration — tout ce qui s'est construit à l'usage et ne se retrouve pas dans
Git. Un an d'historique d'alertes est une donnée HSE.

Le `.env` en est **volontairement** exclu : une archive de sauvegarde circule
(copiée sur une clé, envoyée par messagerie), et un secret ne doit pas voyager
avec elle. Il fait deux lignes, on le recopie à la main.

La restauration met les fichiers actuels de côté avant d'écraser : si la
restauration se révèle être une erreur, rien n'est perdu. **Une sauvegarde
jamais restaurée n'est pas une sauvegarde** — testez-la sur une machine de
développement avant d'en avoir besoin.

Mise à jour du serveur : arrêter les services, sauvegarder, `git pull`,
réinstaller les dépendances, lancer les tests, relancer les services. Le détail
figure dans le guide d'installation.

### 5.5 Intégration continue

`.github/workflows/tests.yml` exécute les tests à chaque envoi sur GitHub, plus
une vérification des imports (une erreur d'import empêche le service de démarrer
sans forcément faire échouer un test unitaire) et de la cohérence de
`config.yaml`. Vous êtes plusieurs sur ce dépôt : une régression doit être
signalée par la machine, pas découverte en démonstration.

### 5.6 Documentation destinée aux utilisateurs

Ce document s'adresse à quelqu'un qui lit le code. Deux autres existent pour ceux
qui n'en ouvriront jamais une ligne :

- `docs/GUIDE_OPERATEUR.md` — pour l'équipe sécurité : que faire devant une
  alerte, pourquoi le bouton « fausse alerte » compte, comment retrouver un
  événement passé.
- `docs/GUIDE_INSTALLATION.md` — pour le technicien : dimensionnement,
  installation, raccordement des caméras, mise en service, dépannage.

Un système que personne ne sait utiliser finit ignoré, et la qualité de la
détection n'y change rien.

---

## 6. Performances mesurées

Toutes les mesures sont faites **sans GPU**.

### 6.1 Machine de développement d'origine

| Configuration | Temps par cycle |
|---|---|
| 6 modèles, séquentiel, PyTorch CPU | 2000–2800 ms |
| 6 modèles, parallèle, OpenVINO | 900–1140 ms |
| idem, après arrêt de Frigate (CPU rendu) | 176–273 ms |

Trois gains cumulés : le runtime OpenVINO, la parallélisation des modèles avec un
thread chacun, et la suppression du détecteur Frigate redondant.

### 6.2 Machine de reprise — Intel i5-5300U, 2 cœurs / 4 threads

Mesures faites sur ce processeur d'ordinateur portable de 2015, volontairement
modeste, pour donner une borne basse crédible :

| Mesure | Valeur |
|---|---|
| Inférence à chaud, **par modèle** | 178–227 ms |
| Cycle complet, **7 modèles en parallèle** | 1130–1604 ms |
| Compilation OpenVINO du 1er modèle (démarrage) | 5,3 s |
| Compilation de chaque modèle suivant | 0,6–1,0 s |
| Mémoire, par modèle supplémentaire | ~50 Mo |
| Mémoire, 3 modèles chargés (processus complet) | ~480 Mo |

Deux enseignements : la mémoire n'est **pas** le facteur limitant (8 modèles
tiennent dans moins de 800 Mo), et le coût dominant est le temps CPU d'inférence.

### 6.3 Dimensionner le serveur

Le raisonnement tient en une règle : **chaque inférence coûte environ 0,19 s de
temps CPU** (sur le processeur ci-dessus ; un CPU serveur récent divise cette
valeur par 2 à 3). Un cœur fournit 1 s de temps CPU par seconde. D'où :

```
cœurs nécessaires ≈ caméras × modèles par caméra × images/s × 0,19
```

| Scénario | Calcul | Cœurs |
|---|---|---|
| 1 caméra, 7 modèles, 1 img/s | 1 × 7 × 1 × 0,19 | ~1,3 |
| 7 caméras, 3 modèles ciblés, 1 img/s | 7 × 3 × 1 × 0,19 | ~4 |
| 7 caméras, 3 modèles, 2 img/s | 7 × 3 × 2 × 0,19 | ~8 |
| 7 caméras, 7 modèles, 2 img/s | 7 × 7 × 2 × 0,19 | ~19 |

La dernière ligne montre l'intérêt des zones : affecter à chaque caméra
uniquement les modèles pertinents pour ce qu'elle voit (EPI dans l'atelier,
véhicules au quai) divise la charge par deux ou trois, sans rien perdre de la
couverture réelle.

**Recommandation** pour les 7 caméras du site : un CPU **8 cœurs**, 16 Go de RAM,
et des modèles ciblés par caméra à 1–2 images par seconde. Prévoir le disque
selon la rétention : clips et snapshots sont purgés à 30 jours par défaut.

---

## 7. Limites connues

1. **Aucune caméra IP réelle branchée.** Le multi-caméra tourne (validé avec
   une webcam et un fichier vidéo simultanément), mais l'authentification
   caméra, la latence réseau et la charge à 7 flux restent à éprouver sur site.
2. **Zones jamais dessinées sur une scène réelle.** Le mécanisme est testé
   (29 tests unitaires, exclusions et horaires compris), mais les zones utiles
   du site restent à tracer.
3. **Trois modèles à ré-entraîner** : `load_control` (inutilisable),
   `Fall-Detected` (peu fiable), et le rappel EPI (~54 % sur NO-Hardhat, soit un
   ouvrier sans casque sur deux qui passe inaperçu).
4. **Modèle `conveyor` jamais éprouvé** : intégré et exporté, mais aucune bande
   transporteuse n'est passée devant l'objectif.
5. **Pas de lecture de plaque** (OCR) sur le modèle véhicules.
6. **Interface sans authentification**, choix assumé et confirmé.
7. **Pas d'enregistrement vidéo continu** : seuls des clips de 15 s autour des
   alertes sont conservés.
8. **Services Windows non éprouvés** : le script est écrit et vérifie ses
   prérequis, mais NSSM n'est pas installé sur la machine de développement.
9. **Banc de test sans clips réels.** Le mécanisme fonctionne, mais il n'a
   tourné que sur une mire de synthèse : les vidéos de référence (chantier,
   incendie, quai de chargement, scène calme) restent à réunir. Tant qu'elles
   manquent, les chiffres de qualité des modèles ne sont pas mesurables.
10. **Suivi d'objets peu éprouvé** : validé par les tests et actif sans
   incident, mais jamais confronté à une scène chargée où plusieurs personnes se
   croisent — le cas où une association par proximité peut se tromper.
11. **Enregistrement continu jamais laissé tourner longtemps** : les garde-fous
   (rotation, rétention, seuil disque) sont testés unitairement, mais la machine
   de développement n'a que quelques Go libres — le comportement sur plusieurs
   jours reste à observer sur le serveur.
12. **Lecture de plaques non réalisée**, et volontairement. Le modèle
   `vehicles` détecte des véhicules, pas des plaques : y brancher un moteur de
   reconnaissance de texte produirait des lectures fantaisistes sur des images
   entières de camions. Il faudrait d'abord un modèle de détection de plaque,
   puis un moteur de lecture — deux briques qui n'existent pas dans le projet.
   Mieux vaut une fonction absente qu'une fonction qui donne de faux numéros.

---

## 8. Pièges rencontrés — à ne pas réintroduire

Chacun de ces points a coûté du temps de diagnostic ; ils sont documentés ici
pour éviter de les redécouvrir.

| Symptôme | Cause | Correctif |
|---|---|---|
| Flux coupé après ~10 s | RTSP en UDP bloqué par le NAT Docker | forcer TCP |
| Pipeline figé, CPU à 100 % | sur-souscription de threads OpenVINO | `OMP_NUM_THREADS=1` avant tout import |
| Pipeline figé, CPU **bas** | lecture RTSP dans la boucle d'inférence, le serveur ferme la connexion | thread de lecture dédié |
| Pipeline qui ne démarre jamais, CPU moyen | compilation OpenVINO de plusieurs modèles en parallèle au premier cycle | préchauffage séquentiel avant la boucle |
| Caméra affichée « LIVE » alors que rien ne tourne | état déduit de l'existence du fichier image | état déduit de sa fraîcheur (< 15 s) |
| Image live parfois tronquée dans l'interface | l'API lit le fichier pendant son écriture | écriture dans un fichier temporaire puis `os.replace` (atomique) |
| `imgsz: 480` fait planter | taille d'entrée figée à l'export OpenVINO | rester à 640, ou réexporter |
| Sortie console vide | Python bufferise hors terminal | lancer avec `python -u` |
| 28 lectures disque par seconde | réglages relus à chaque détection | cache d'une seconde |
| Centaines d'alertes par jour | délai anti-répétition unique de 15 s | délais par sévérité (60/300/900 s) |
| `cv2.imwrite` échoue par intermittence | OneDrive verrouille le fichier pendant la synchronisation | écriture tolérante aux échecs |

---

## 9. Reste à faire, par priorité

**Ce qui demande autre chose que du code**

1. **Réunir les vidéos de référence** du banc de test : chantier avec et sans
   EPI, départ de feu, quai de chargement, et surtout une scène banale où il ne
   se passe rien. Sans elles, la qualité des modèles reste une impression.
2. **Ré-entraîner** le rappel EPI d'abord (54 % : un ouvrier sans casque sur
   deux passe inaperçu), puis `load_control` avec les images négatives que
   produit le bouton « fausse alerte », puis la détection de chute.
   `scripts/export_dataset.py` prépare le jeu de données ; le protocole adapté à
   un GPU modeste figure dans le README.
3. **Brancher les caméras IP du site**, tracer leurs zones, vérifier la charge.
4. **Éprouver les services Windows** sur le serveur cible, avec NSSM installé.

**Améliorations restantes**

5. Envoi automatique du rapport PDF chaque semaine (il se télécharge aujourd'hui
   à la demande depuis l'écran Rapports).
6. Lecture de plaques : demande d'abord un modèle de détection de plaque, puis
   un moteur de reconnaissance de texte. Voir la limite 12.
7. Authentification, si l'interface doit un jour sortir du réseau interne.

Le groupe Telegram ne demande aucun développement : il suffit d'indiquer l'id du
groupe (négatif, de la forme `-100…`) dans `TELEGRAM_CHAT_IDS`.

**Déjà traité** (rappel, pour ne pas le rouvrir par erreur) : sources vidéo
unifiées, gestion des caméras sans fichier de configuration, supervision et
alertes techniques, zones avec exclusions et horaires, suivi d'objets et
comptage, retour opérateur sur les fausses alertes, indicateurs de qualité,
banc de test, export du jeu de données, recherche croisée et pagination, frise
chronologique, mur de caméras configurable, enregistrement continu, rapport PDF,
journalisation, sauvegarde, intégration continue, guides opérateur et
installation.
