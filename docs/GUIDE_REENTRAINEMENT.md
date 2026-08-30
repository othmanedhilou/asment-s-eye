# Ré-entraîner les modèles

Guide pratique pour corriger les modèles défaillants. Le carnet prêt à l'emploi
est [`notebooks/reentrainement.ipynb`](../notebooks/reentrainement.ipynb) —
ouvrez-le dans Google Colab, il fait le reste.

---

## Par quoi commencer, et pourquoi

| Ordre | Modèle | Défaut | Pourquoi cette place |
|---|---|---|---|
| **1** | `epi` | rappel ~54 % sur `NO-Hardhat` | Un ouvrier sans casque sur deux passe inaperçu. C'est le défaut le plus grave **et le plus sournois** : une fausse alerte se voit, un cas manqué non. L'interface reste calme, et tout le monde croit que le système fonctionne. |
| **2** | `load_control` | affirme `empty` à 0,89 sur un bureau | Il est *sûr de lui et faux* : aucun seuil ne corrige cela. Il n'a jamais appris à répondre « rien ici ». |
| **3** | `gloves_glasses` | chute peu fiable | Aujourd'hui masquée par un seuil relevé à 0,80 — un pansement, pas un correctif. |
| **4** | `plate` | modèle inexistant | La localisation par vision classique fonctionne sur une vue frontale nette. Un modèle dédié améliorerait nettement le taux de lecture. |
| **5** | `conveyor` | jamais éprouvé | Aucun jeu public. Demande des images de vos bandes transporteuses. |

Traitez-les **un à la fois**. Deux modèles entraînés en parallèle, et vous ne
saurez plus lequel a amélioré ou dégradé quoi.

---

## Les jeux de données

Vérifiés en août 2026.

### EPI — abondant

| Jeu | Volume | Remarque |
|---|---|---|
| [HardHat & SafetyVest](https://universe.roboflow.com/ppe-kit-detection/hardhat-safetyvest) | 22 068 images | Le plus gros, ciblé casque et gilet |
| [Construction-PPE (Ultralytics)](https://docs.ultralytics.com/datasets/detect/construction-ppe) | 1 416 images, 11 classes | Curé, avec cas conformes *et* non conformes |
| [Recherche sur les EPI](https://www.sciencedirect.com/science/article/pii/S2352340925007127) | — | Jeu académique, utile en complément |

### Chute — exactement ce qui manque

[Fall Detection](https://universe.roboflow.com/roboflow-universe-projects/fall-detection-ca3o8) —
4 497 images, avec les classes *debout*, *penché*, *en train de tomber*,
*tombé*. Cette distinction entre « penché » et « tombé » est précisément ce que
votre modèle actuel ne sait pas faire : il alerte sur quelqu'un d'assis.

### Plaques — format local

[Plaques marocaines](https://universe.roboflow.com/naima-el-menani/moroccan-license-plate-detection) —
2 588 images. Le format local compte : une plaque marocaine n'a ni la géométrie
ni les caractères d'une plaque européenne.

### Contrôle de chargement et convoyeur — rien de public

Recherche faite : **aucun jeu de données exploitable n'existe** pour ces deux
cas. Le convoyeur ne fait l'objet que de
[publications](https://www.nature.com/articles/s41598-024-83619-6) dont les
données ne sont pas diffusées.

Ce n'est pas bloquant, parce que la meilleure source est déjà chez vous.

---

## La source qui vaut plus que tout jeu public

```powershell
.\venv\Scripts\python.exe scripts\export_dataset.py --model load_control --days 90
```

Ce script produit un jeu de données issu de **votre exploitation réelle** :

- les alertes **marquées fausses** par les opérateurs deviennent des **images de
  fond**, sans annotation. C'est ainsi qu'on apprend à un modèle à répondre
  « rien ici » — exactement ce qui manque à `load_control` ;
- les alertes justes fournissent des images **pré-annotées** à partir des
  positions enregistrées : il reste à vérifier les boîtes, pas à tout tracer.

Vos images montrent vos caméras, votre lumière, vos ouvriers, vos angles de vue.
Trois cents images du site valent mieux que cinq mille images génériques.

**Conséquence pratique :** plus les opérateurs marquent les fausses alertes,
meilleur devient le jeu de données. Ce bouton n'est pas un détail de confort,
c'est la matière première du ré-entraînement.

---

## Les trois règles à ne pas enfreindre

**Rester en 640 pixels.** Les modèles OpenVINO du serveur ont cette taille
figée. Entraîner en 416 ou 832 obligerait à tout réexporter, et l'inférence
planterait. Ce piège a déjà coûté une soirée sur ce projet.

**Sauvegarder sur Drive.** Colab coupe sans prévenir. Le carnet écrit
directement dans `MonDrive/SmokeWatch/runs/` et reprend automatiquement.

**Mesurer avant et après.** C'est la règle la plus importante. Sans point de
comparaison, on ne sait pas si l'on a progressé — et un recul ne se voit nulle
part, puisqu'un cas manqué ne s'affiche pas.

---

## Le chiffre à surveiller

Le **rappel**, pas la précision.

- La *précision* dit : quand le modèle alerte, a-t-il raison ?
- Le *rappel* dit : sur tous les cas réels, combien en voit-il ?

Un modèle qui n'alerte jamais a une précision parfaite. C'est le piège : en
relevant un seuil, on fait chuter les fausses alertes, on est satisfait — et le
modèle rate désormais un ouvrier sans casque sur trois au lieu d'un sur deux.

Le carnet affiche explicitement un avertissement si le rappel a baissé. **Dans
ce cas, ne déployez pas.**

---

## Déployer le résultat

```powershell
# 1. Sauvegarder l'ancien modèle — il faut pouvoir revenir en arrière
copy models\smokewatch_epi_best.pt models\smokewatch_epi_best_ancien.pt

# 2. Installer le nouveau
copy <telechargé>\smokewatch_epi_best_v2.pt models\smokewatch_epi_best.pt

# 3. Supprimer l'export OpenVINO périmé — sinon le pipeline garde l'ancien modèle
rmdir /s models\smokewatch_epi_best_openvino_model

# 4. Reconvertir
.\venv\Scripts\python.exe scripts\export_openvino.py

# 5. Mesurer sur VOS vidéos
.\venv\Scripts\python.exe scripts\benchmark.py run
.\venv\Scripts\python.exe scripts\benchmark.py compare --last

# 6. Redémarrer
Restart-Service SmokeWatchPipeline
```

L'étape 3 n'est pas facultative : sans elle, le pipeline continue d'utiliser
l'ancien modèle **sans rien signaler**.

---

## Les deux mesures se complètent

| Mesure | Ce qu'elle dit | Où |
|---|---|---|
| mAP et rappel | ce que le modèle a appris | carnet Colab |
| Banc de test | ce qu'il fait sur vos scènes | serveur |

Si les deux progressent, le travail est fait. Si la mAP monte mais que le banc
recule, le modèle s'est spécialisé sur le jeu public au détriment de votre site :
ajoutez davantage d'images de production et recommencez.

---

## Ce qu'il faut en retenir pour le mémoire

« J'ai amélioré le rappel de NO-Hardhat de 54 % à 71 % » est une phrase de
soutenance. « Ça marche mieux » n'en est pas une.

Conservez les rapports du carnet et ceux du banc de test : ce sont vos preuves,
et elles ont plus de valeur qu'une capture d'écran de l'interface.
