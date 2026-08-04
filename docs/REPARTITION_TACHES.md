# Répartition des tâches — développement logiciel, 4 personnes

Projet SmokeWatch : logiciel de supervision vidéo intelligente pour site cimentier.

**Hypothèse de départ** : les modèles de détection (fumée, feu, EPI) sont déjà
entraînés et disponibles sous forme de poids. Le périmètre couvert ici est
uniquement la **réalisation du logiciel** qui les exploite.

Durée de référence : **10 semaines**, charge cible **≈ 50 jours-homme par
personne** (base 5 j/semaine, marge de 15 % incluse).

---

## 1. Principe de découpage

Le découpage suit les **couches techniques du logiciel**, pas les
fonctionnalités. Deux personnes sur une même couche se gênent dans Git ; deux
personnes sur des couches différentes n'ont qu'un contrat d'interface à
respecter.

| | Rôle | Périmètre | Charge propre |
|---|---|---|---|
| **A** | Acquisition vidéo & inférence | capture RTSP, exécution des modèles, performance | 38 j |
| **B** | Règles métier & traitement des alertes | ROI, filtres, clips, notifications | 38 j |
| **C** | Backend, données & API | configuration, base, REST, WebSocket, sécurité | 38 j |
| **D** | Interface & exploitation | dashboard, tracé de ROI, rapports, déploiement | 38 j |

Auxquels s'ajoutent **12 j de travail collectif** par personne :

| Tâche collective | Charge | Quand |
|---|---|---|
| Intégration hebdomadaire et revues croisées | 4 j | S2–S9 |
| Campagne de tests système | 3 j | S8 |
| Réglage et validation sur site | 2 j | S9 |
| Documentation, mémoire, soutenance | 3 j | S10 |

**Total par personne : 38 + 12 = 50 jours.**

---

## 2. Personne A — Acquisition vidéo et inférence

**Fichiers** : `app/capture.py`, `app/detectors.py`, `app/pipeline.py`

| Tâche | Charge | Semaine |
|---|---|---|
| Inventaire des caméras : codecs, résolutions, chemins RTSP, latence réseau | 3 j | S1 |
| Lecteur RTSP threadé : dernière image, reconnexion, buffer de pré-roll | 6 j | S2 |
| Registre de modèles : chargement unique, bascule CPU/GPU, FP16 | 4 j | S3 |
| Inférence par lot multi-caméras | 5 j | S3–S4 |
| Boucle d'orchestration : cadence, regroupement par modèle, distribution | 6 j | S4–S5 |
| Caractérisation des modèles fournis : latence, mémoire, classes réelles | 4 j | S5 |
| Optimisation : taille de batch, fréquence d'analyse, export TensorRT | 5 j | S6–S7 |
| Robustesse : caméra morte, GPU saturé, flux corrompu | 3 j | S7 |
| Documentation technique de la couche vidéo | 2 j | S8 |

**Livre à l'équipe** : objet `Detection` normalisé (classe, confiance, boîte en
pixels), courbe caméras/latence, dimensionnement matériel chiffré.

**Point d'attention** : les modèles étant fournis, la première tâche réelle est
de **mesurer ce qu'ils coûtent** avant de dimensionner la boucle. Un modèle à
6 ms et un modèle à 40 ms n'autorisent pas le même nombre de caméras.

---

## 3. Personne B — Règles métier et traitement des alertes

**Fichiers** : `app/rules.py`, `app/recorder.py`, `app/notifier.py`

| Tâche | Charge | Semaine |
|---|---|---|
| Spécification des règles avec le service HSE (par zone, par modèle) | 3 j | S1 |
| Filtre de zone d'intérêt : polygone normalisé, appartenance, tests | 4 j | S2 |
| Confirmation temporelle et cooldown : machine d'état par détecteur | 5 j | S3 |
| Tests unitaires du moteur de règles | 3 j | S3 |
| Snapshots annotés : boîtes, bandeau horodaté, gravité | 3 j | S4 |
| Clips vidéo : pré-roll depuis le tampon, post-roll, écriture asynchrone | 5 j | S4–S5 |
| Notifications e-mail et Telegram, file d'attente asynchrone | 5 j | S6 |
| **Banc de rejeu** : rejouer des séquences enregistrées à la place du flux | 5 j | S6–S7 |
| Campagne de réglage des seuils par zone, mesure des fausses alertes | 3 j | S8 |
| Documentation des règles et des paramètres | 2 j | S8 |

**Livre à l'équipe** : tableau des seuils recommandés par zone, banc de rejeu
utilisable par tout le monde.

**Point d'attention** : le banc de rejeu est ce qui rend les résultats
**reproductibles**. Sans lui, chaque mesure de fausses alertes dépend de ce qui
s'est passé sur le site ce jour-là et n'est pas défendable dans le mémoire.

---

## 4. Personne C — Backend, données et API

**Fichiers** : `app/config.py`, `app/storage.py`, `app/api.py`

| Tâche | Charge | Semaine |
|---|---|---|
| Schéma de configuration YAML, chargement typé, validation des entrées | 4 j | S2 |
| Schéma de base et couche de persistance des événements | 5 j | S3 |
| API REST : caméras, événements filtrables, acquittement | 6 j | S4–S5 |
| Diffusion des flux live vers le navigateur (MJPEG) | 3 j | S5 |
| WebSocket d'alertes temps réel, passage thread → boucle asynchrone | 4 j | S6 |
| Agrégats statistiques : par jour, par zone, par type de détection | 4 j | S6 |
| Modification à chaud des seuils, ROI et activation des détecteurs | 4 j | S7 |
| Authentification et rôles opérateur / administrateur | 4 j | S7–S8 |
| Rétention, purge planifiée, sauvegarde de la base | 2 j | S8 |
| Documentation de l'API | 2 j | S8 |

**Livre à l'équipe** : contrat d'API documenté (schémas JSON REST et WebSocket),
structure de `config.yaml` figée.

**Point d'attention** : la remontée d'alerte traverse une frontière délicate —
le moteur d'inférence tourne dans un thread, l'API dans une boucle asynchrone.
C'est l'endroit typique où les alertes se perdent silencieusement ; à tester
explicitement.

---

## 5. Personne D — Interface et exploitation

**Fichiers** : `web/`, scripts de déploiement, documentation utilisateur

| Tâche | Charge | Semaine |
|---|---|---|
| Entretiens opérateurs et maquettes validées par le HSE | 4 j | S1–S2 |
| Vue live multi-caméras : grille, état de connexion, cadence | 5 j | S3–S4 |
| Flux d'alertes temps réel, mise en évidence de la caméra concernée | 4 j | S5 |
| Historique filtrable, lecture des clips, acquittement opérateur | 5 j | S5–S6 |
| **Outil de tracé de ROI à la souris sur image figée** | 6 j | S6–S7 |
| Page de configuration : seuils, activation des modèles par caméra | 4 j | S7 |
| Rapports : agrégats, graphiques, export CSV et PDF | 4 j | S8 |
| Packaging : service système, script d'installation, redémarrage auto | 3 j | S9 |
| Manuel d'utilisation opérateur | 3 j | S9 |

**Livre à l'équipe** : dashboard déployable, manuel opérateur.

**Point d'attention** : l'outil de tracé de ROI est la fonctionnalité la plus
sous-estimée du lot. Sans lui, chaque ajustement de zone passe par une
modification manuelle du fichier de configuration — inutilisable par un
opérateur, et c'est précisément le réglage qui demande le plus d'itérations.

---

## 6. Contrats d'interface

Seuls points de synchronisation entre les rôles. À **figer en fin de semaine 2**,
avant tout développement parallèle.

| Frontière | Contrat à figer | Responsable |
|---|---|---|
| A → B | objet `Detection` : classe, confiance, boîte en pixels | A |
| B → C | objet `Alert` et champs persistés en base | B |
| C → D | schémas JSON de l'API REST et du WebSocket | C |
| C → tous | structure de `config.yaml` | C |
| A → tous | liste des classes réelles de chaque modèle fourni | A |

**Règle pratique** : tant qu'un contrat n'est pas livré, chacun développe contre
un **bouchon** — détecteur factice renvoyant des boîtes aléatoires, API simulée
avec des données figées, générateur d'alertes de test. Personne n'attend
personne, et les bouchons servent ensuite aux tests automatisés.

---

## 7. Planning consolidé

| Sem. | A — Vidéo & inférence | B — Règles & alertes | C — Backend & API | D — Interface |
|---|---|---|---|---|
| S1 | Inventaire caméras | Spécification des règles | Cadrage technique | Entretiens, maquettes |
| S2 | Lecteur RTSP | Filtre ROI | Configuration YAML | Maquettes validées |
| S3 | Registre + lot | Confirmation, cooldown | Persistance | Vue live |
| S4 | Orchestrateur | Snapshots, clips | API REST | Vue live |
| S5 | Caractérisation modèles | Clips | MJPEG | Flux d'alertes, historique |
| S6 | Optimisation | Notifications, banc de rejeu | WebSocket, stats | Historique, outil ROI |
| S7 | Optimisation, robustesse | Banc de rejeu | Config à chaud, auth | Outil ROI, config |
| S8 | *Campagne de tests système — les 4* + doc | Réglage des seuils | Purge, doc API | Rapports |
| S9 | *Validation sur site — les 4* | | | Packaging, manuel |
| S10 | *Documentation, mémoire, soutenance — les 4* | | | |

---

## 8. Organisation de travail

- **Git** : une branche par rôle (`feat/video`, `feat/rules`, `feat/backend`,
  `feat/web`), fusion dans `main` en fin de semaine, revue croisée obligatoire
  par une personne d'un autre rôle.
- **Définition de « terminé »** : le code est fusionné, testé, documenté, **et
  démontré à l'équipe**. Sans démonstration, une tâche reste ouverte — c'est ce
  qui évite les modules « finis à 90 % » pendant trois semaines.
- **Point d'équipe** : 30 min en début de semaine — livré, bloqué, contrats
  d'interface modifiés.
- **Journal de projet** : chacun consigne ses décisions et ses impasses
  techniques. Elles constituent une part importante du mémoire et sont
  impossibles à reconstituer deux mois plus tard.
- **Soutenance** : chacun présente sa couche ; une personne assure
  l'introduction et la démonstration finale.

---

## 9. Vérification de l'équilibrage

| | Développement | Tests & mesures | Doc & analyse | Collectif | **Total** |
|---|---|---|---|---|---|
| A | 24 j | 9 j | 5 j | 12 j | **50 j** |
| B | 25 j | 8 j | 5 j | 12 j | **50 j** |
| C | 30 j | 4 j | 4 j | 12 j | **50 j** |
| D | 26 j | 4 j | 8 j | 12 j | **50 j** |

Les charges sont équivalentes, mais la **nature** du travail diffère : A et B
passent plus de temps sur la mesure et le réglage, C sur le développement pur,
D sur l'interface et la documentation utilisateur. Autant l'expliciter au jury :
un poste qui produit moins de lignes de code n'est pas un poste plus léger.

---

## 10. Points de blocage possibles

| Risque | Qui est bloqué | Parade |
|---|---|---|
| Accès réseau aux caméras retardé | A, puis tous | banc de test sur fichiers vidéo dès S1 |
| Contrats d'interface non figés en S2 | tous | réunion dédiée en fin de S2, non reportable |
| Modèles trop lourds pour le matériel | A, B | mesurer dès S5, réduire la cadence ou la résolution |
| Serveur GPU indisponible | A | développer en mode CPU sur 2 caméras, basculer plus tard |
| Fausses alertes non maîtrisées | B, D | resserrer les ROI, allonger la confirmation temporelle |
