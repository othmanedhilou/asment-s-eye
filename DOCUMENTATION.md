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
Caméra
  |  webcam locale (indice 0)  ou  caméra IP (RTSP)
  v
app/capture.py ........ RTSPStream : thread de lecture dédié
  |                     ne garde que l'image la plus récente
  v
app/pipeline.py ....... boucle principale : N modèles en parallèle par image
  |                     (ThreadPoolExecutor + OpenVINO)
  v
app/rules.py .......... filtrage : classes surveillées, seuils renforcés,
  |                     anti-répétition calé sur la sévérité
  v
app/notifier.py ....... snapshot + bip + Telegram (routage par sévérité)
app/recorder.py ....... clip MP4 : 5 s avant / 10 s après l'alerte
app/storage.py ........ SQLite : historique, sévérité, acquittement, purge
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

Ce découplage a un avantage concret : **redémarrer l'interface n'interrompt pas
la détection**, et inversement.

---

## 2. Les modules, un par un

### 2.1 `app/capture.py` — lecture du flux

```python
class RTSPStream:
    def __init__(self, url, open_timeout=10.0)
```

`url` accepte deux formes :
- un **indice de webcam locale** (`0`) → ouvert via `cv2.CAP_DSHOW` sous Windows
- une **URL RTSP** (`rtsp://...`) pour les caméras IP du site

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

### 2.2 `app/detectors.py` — chargement des modèles

`ModelRegistry` charge les modèles YOLO à la demande et les garde en mémoire.
Le chargement est coûteux (compilation OpenVINO à la première inférence), il ne
doit se produire qu'une fois par processus.

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

Chaque détection est dessinée sur une copie de l'image (`annotated`), qui sert à
la fois au flux live et aux clips vidéo — l'opérateur voit les boîtes
englobantes.

En cas de perte de flux, la boucle extérieure reconnecte toutes les 2 s.

### 2.4 `app/rules.py` — du bruit brut aux alertes exploitables

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

Le délai s'applique par **(caméra, modèle, classe)**. Un ouvrier sans casque qui
reste à son poste génère une alerte toutes les 5 minutes, pas une par image.
Avec l'ancien délai unique de 15 s, une personne assise devant la caméra
produisait **448 alertes en 24 h** ; après ce changement, la même scène en
produit une poignée.

### 2.5 `app/storage.py` — base de données et sévérité

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

### 2.6 `app/notifier.py` — notification

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

### 2.7 `app/recorder.py` — clips vidéo

Un snapshot ne dit pas ce qui s'est passé **avant** l'alerte. Le `ClipRecorder`
garde en mémoire un tampon glissant des 5 dernières secondes :

```python
self._buffer = deque(maxlen=int(pre_seconds * fps))
```

Au déclenchement, il concatène ce tampon avec les 10 secondes suivantes et écrit
un MP4 (codec `mp4v`), puis lie le fichier à l'alerte en base.

Un seul clip à la fois : `trigger()` sort immédiatement si un enregistrement est
en cours, sinon des dizaines de vidéos se chevaucheraient.

### 2.8 `app/settings.py` — réglages en direct

`data/settings.json` porte, pour chaque modèle, deux booléens : `detect`
(exécuter le modèle) et `alert` (déclencher une alerte). Modifiables depuis
l'interface, sans toucher au code ni redémarrer.

Le pipeline interroge ces réglages **plusieurs dizaines de fois par seconde**.
Une première version relisait le fichier à chaque appel — 28 lectures disque par
seconde. D'où le cache d'une seconde : assez court pour rester réactif à l'UI,
assez long pour supprimer le martèlement disque.

### 2.9 `app/usecases.py` — traçabilité du cahier des charges

Registre des **12 cas d'usage** du cahier des charges, mappés sur les modèles
réellement entraînés, avec un état honnête par cas : `operationnel`, `partiel`,
`a_entrainer`. Un modèle peut couvrir plusieurs cas (`fire_smoke` couvre fumée
*et* feu ; `epi` couvre casque, gilet et masque).

### 2.10 `app/api.py` — API REST et interface

FastAPI. Endpoints :

| Route | Rôle |
|---|---|
| `GET /` | interface web |
| `GET /video/{caméra}.jpg` | dernière image annotée |
| `GET /api/cameras` | caméras, modèles affectés, état en ligne |
| `GET /api/alerts` | historique filtrable (modèle, sévérité, acquittement, période) |
| `POST /api/alerts/{id}/ack` | prise en charge par un opérateur |
| `GET /api/stats/summary` | indicateurs 24 h / 7 j |
| `GET /api/stats/timeline` | activité heure par heure |
| `GET /api/settings` · `POST /api/settings/{modèle}/{clé}` | réglages en direct |
| `GET /api/usecases` | les 12 cas d'usage et leur état |
| `GET /api/snapshot` · `GET /api/clip` | médias d'une alerte |
| `GET /api/export/csv` | export pour le service HSE |

L'interface (`web/`) comporte cinq pages : tableau de bord, mur de caméras,
alertes, cas d'usage, rapports.

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

`scripts/install.ps1` installe les deux processus en services Windows.

---

## 6. Performances mesurées

Toutes les mesures sont faites **sans GPU**.

| Configuration | Temps par cycle |
|---|---|
| 6 modèles, séquentiel, PyTorch CPU | 2000–2800 ms |
| 6 modèles, parallèle, OpenVINO | 900–1140 ms |
| idem, après arrêt de Frigate (CPU rendu) | 176–273 ms |

Trois gains cumulés : le runtime OpenVINO, la parallélisation des modèles avec
un thread chacun, et la suppression du détecteur Frigate redondant.

**Ces chiffres dépendent fortement du processeur.** Ils ont été obtenus sur la
machine de développement d'origine. Sur un CPU à 2 cœurs (type i5-5300U), faire
tourner 8 modèles par image n'est pas réaliste : il faut réduire le nombre de
modèles actifs simultanément — ce que l'interface permet en un clic — ou baisser
`fps`.

---

## 7. Limites connues

1. **Pas de ROI (zones d'intérêt).** Chaque modèle analyse l'image entière. Sur
   site réel, le modèle EPI tournerait aussi sur le parking. C'est le principal
   manque avant une mise en production.
2. **Validé sur une seule webcam**, pas sur les caméras IP du site.
3. **Trois modèles à ré-entraîner** : `load_control` (inutilisable),
   `Fall-Detected` (peu fiable), et le rappel EPI (~54 % sur NO-Hardhat, soit un
   ouvrier sans casque sur deux qui passe inaperçu).
4. **Pas de lecture de plaque** (OCR) sur le modèle véhicules.
5. **Interface sans authentification**, choix assumé pour la phase de test.
6. **Un seul flux validé** ; la montée à 7 caméras × 8 modèles demandera un
   arbitrage (modèles dédiés par caméra, ou fps réduit).

---

## 8. Pièges rencontrés — à ne pas réintroduire

Chacun de ces points a coûté du temps de diagnostic ; ils sont documentés ici
pour éviter de les redécouvrir.

| Symptôme | Cause | Correctif |
|---|---|---|
| Flux coupé après ~10 s | RTSP en UDP bloqué par le NAT Docker | forcer TCP |
| Pipeline figé, CPU à 100 % | sur-souscription de threads OpenVINO | `OMP_NUM_THREADS=1` avant tout import |
| Pipeline figé, CPU **bas** | lecture RTSP dans la boucle d'inférence, le serveur ferme la connexion | thread de lecture dédié |
| `imgsz: 480` fait planter | taille d'entrée figée à l'export OpenVINO | rester à 640, ou réexporter |
| Sortie console vide | Python bufferise hors terminal | lancer avec `python -u` |
| 28 lectures disque par seconde | réglages relus à chaque détection | cache d'une seconde |
| Centaines d'alertes par jour | délai anti-répétition unique de 15 s | délais par sévérité (60/300/900 s) |
| `cv2.imwrite` échoue par intermittence | OneDrive verrouille le fichier pendant la synchronisation | écriture tolérante aux échecs |

---

## 9. Reste à faire, par priorité

**Bloquant pour une mise en production**

1. **ROI par caméra** — outil de dessin de zones dans l'interface, puis filtrage
   des détections. Sans cela, l'objectif « moins de 2 fausses alertes par jour
   et par caméra » ne tiendra pas sur site.
2. **Brancher les caméras IP du site** et vérifier que le CPU tient la charge.
3. **Ré-entraîner** `load_control` et `Fall-Detected`, enrichir le dataset EPI
   avec des images du site réel.
4. **Services automatiques** — le système doit survivre à un redémarrage du
   serveur.

**Niveau professionnel**

5. Groupe Telegram dédié plutôt que des `chat_id` individuels.
6. Authentification de l'interface.
7. Statistiques HSE : taux de conformité EPI par zone, délai moyen de prise en
   charge des alertes.
8. Rapport PDF hebdomadaire automatique au responsable HSE.
