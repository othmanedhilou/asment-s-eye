# Brief de design — Ciment's Eye

*À donner tel quel à un designer. Tout ce qui est écrit ici est la réalité du
logiciel, pas un souhait : les données, les états et les contraintes sont ceux
du produit qui tourne aujourd'hui.*

---

## 1. Ce qu'est le produit

**Ciment's Eye** est un logiciel de vidéosurveillance intelligente (un *VMS*,
video management system) installé dans une **cimenterie** au Maroc — site
industriel de Ciments du Maroc.

Il ne se contente pas d'afficher des caméras : huit modèles de vision par
ordinateur analysent les images en continu et lèvent une **alerte** quand ils
voient quelque chose d'anormal — un ouvrier sans casque, un départ de feu, un
camion qui sort sans bâche, une personne au sol.

Le logiciel est une **application web servie en local** par le serveur du site.
Personne ne l'ouvre depuis Internet. Il tourne dans un navigateur, en plein
écran, sur le poste du gardien.

---

## 2. Qui s'en sert, et dans quelles conditions

**L'agent de sécurité** — c'est l'utilisateur principal, 95 % du temps d'usage.

- Il est dans un **poste de garde**, une pièce souvent peu éclairée.
- Il n'est pas informaticien. Il ne lira aucune documentation.
- Il regarde l'écran par intermittence pendant des postes de 8 heures, parfois
  **debout, à un ou deux mètres de l'écran**.
- Ce qu'il fait concrètement : surveiller les images, réagir quand une alerte
  arrive, retrouver un événement passé quand un chef d'équipe le lui demande.

**L'installateur / l'ingénieur HSE** — quelques heures au démarrage du site,
puis rarement. C'est lui qui ajoute les caméras, trace les zones de détection,
règle les seuils.

> Conséquence : **l'écran de l'agent et l'écran de réglage ne doivent pas se
> ressembler ni se mélanger.** Un agent ne doit jamais tomber par accident sur
> un formulaire de configuration.

**Écrans réels** : postes bureautiques anciens, souvent **1366 × 768**. Le
design doit tenir à cette résolution sans défilement horizontal. Un écran
1920 × 1080 est le cas favorable, pas le cas de référence.

---

## 3. Les quatre écrans

### 3.1 Direct — le mur d'images

C'est l'écran par défaut, celui qui reste ouvert toute la journée.

- De **1 à 16 caméras** affichées simultanément en grille (1×1, 2×2, 3×3, 4×4).
- Chaque vignette est une **image fixe rafraîchie environ une fois par
  seconde** (pas une vidéo fluide — le serveur est trop faible). Format 16/9.
- Sur chaque vignette : le nom de la caméra, un état (`DIRECT` / `PAUSE` /
  `HORS LIGNE`), et des informations techniques discrètes (nombre de modèles
  actifs, temps de calcul en millisecondes).
- **Comportement demandé par le client** : quand on clique une caméra, elle
  **s'agrandit sans faire disparaître les autres**. L'agent doit garder le site
  entier sous les yeux tout en regardant un détail.
- Quand une alerte critique tombe, la vignette concernée doit **se signaler**
  immédiatement (c'est le moment le plus important de toute l'interface).

### 3.2 Historique — retrouver un événement

- Une **frise chronologique** horizontale : chaque alerte est un trait vertical
  placé à son heure, coloré par gravité. On clique un trait pour ouvrir
  l'alerte. Sert à répondre à « qu'est-ce qui s'est passé cette nuit ? ».
- En dessous, un **tableau d'alertes** paginé.
- Colonnes disponibles : heure, caméra, modèle, ce qui a été vu, gravité,
  confiance (%), zone, plaque d'immatriculation, **vignette de la capture**,
  état traité / non traité, marquage « fausse alerte ».
- Filtres : période, caméra, modèle, gravité, zone, traité, fausses,
  tranche horaire (matin / après-midi / nuit), classe, plaque.
- Deux actions sur une ligne : **accuser réception** et **déclarer fausse
  alerte** (ce second geste alimente la mesure de fiabilité).
- Exports **PDF** (rapport HSE) et **CSV**.

### 3.3 Analyse — les chiffres

Destiné au responsable HSE plus qu'à l'agent.

- Nombres clés : alertes du jour, à traiter, taux de fausses alertes.
- Histogramme des alertes par heure sur 24 h.
- Classement des caméras par nombre de fausses alertes (objectif : moins de 2
  par jour et par caméra).
- Fiabilité sur 30 jours.

### 3.4 Paramètres — six sections

Une seule visible à la fois, choisie dans un menu latéral.

1. **Caméras** — liste et formulaire. Une caméra = un nom, une source, des
   modèles actifs, et une dizaine d'options avancées (voir §4).
2. **Zones d'intérêt** — on dessine des polygones **à la souris sur une image
   figée de la caméra** pour limiter la détection à une zone (« le quai », « le
   portail ») ou au contraire en exclure une (« la route publique au fond »).
   Chaque zone a un horaire d'activité et un seuil propre.
3. **Fichiers de test** — dépôt de vidéos ou d'images par glisser-déposer, pour
   essayer un modèle sans caméra réelle.
4. **Modèles** — activer/désactiver chacun des 8 modèles, régler son seuil de
   confiance et son délai anti-répétition.
5. **Cas d'usage** — tableau d'avancement des 12 cas du cahier des charges.
6. **État du système** — santé du pipeline, charge processeur, mémoire, disque.

---

## 4. Les données réelles

**Les huit modèles** (leur nom apparaît tel quel dans l'interface) :

| Modèle | Ce qu'il détecte |
|---|---|
| `epi` | casque, gilet, chaussures — équipements de protection |
| `gloves_glasses` | gants et lunettes de sécurité |
| `fire_smoke` | feu et fumée |
| `person_animal` | présence humaine, intrusion animale |
| `vehicles` | voitures, camions, engins |
| `load_control` | camion bâché ou non, surcharge |
| `arc` | arcs électriques de soudure |
| `conveyor` | anomalies sur les convoyeurs à bande |
| `fall` | personne au sol *(en cours d'entraînement)* |

**Une alerte** porte : identifiant, caméra, modèle, libellé de ce qui a été vu,
confiance (0 à 1), message, horodatage, chemin de la capture, **gravité**,
accusé de réception (par qui, quand), clip vidéo éventuel, zone, marquage
fausse alerte, cadre de détection, plaque lue.

**Quatre gravités**, dans cet ordre : `critique`, `haute`, `moyenne`,
`technique`. C'est la distinction visuelle la plus importante de tout le
produit — un agent doit trier d'un coup d'œil, de loin.

**Trois états de caméra** : en ligne, en pause, hors ligne.

**Volumétrie attendue** : 4 à 16 caméras, quelques dizaines à quelques
centaines d'alertes par jour.

---

## 5. Contraintes techniques — non négociables

- **Trois fichiers, pas de compilation** : un `index.html` (gabarit Jinja2), un
  `dashboard.css`, un `dashboard.js`. Pas de React, pas de Vue, pas de Tailwind,
  pas de npm, pas d'étape de build.
- **Aucune ressource distante.** Le site industriel peut être coupé
  d'Internet. Pas de Google Fonts, pas de CDN, pas d'icônes chargées en ligne.
  Les polices doivent être celles du système ; les icônes, du SVG écrit dans la
  page.
- **Interface en français.**
- **Thème sombre par défaut, thème clair disponible**, choix mémorisé sur le
  poste.
- Palette demandée par le client : **noir et or**, texte clair, beaucoup
  d'espace, rien de tassé.
- Machine faible (2 cœurs, processeur de 2015) : pas d'animation coûteuse, pas
  d'ombres portées animées, pas de flou d'arrière-plan.

---

## 6. Ce que le client a explicitement demandé

Ses mots, traduits en exigences :

1. **« Pas chargé, reposant pour l'œil. »** Peu d'éléments par écran, peu de
   traits, peu de couleurs. La couleur vive est réservée aux gravités.
2. **« Rien de petit, tout se voit clairement. »** Texte grand, cibles de clic
   généreuses. Lisible debout à deux mètres.
3. **« Toutes les fonctions restent. »** Aucun retrait de fonctionnalité :
   l'objectif est de mieux ranger, pas de simplifier en enlevant.
4. **« Que ça ne fasse pas généré par une IA. »** Concrètement, ce qui trahit
   une interface générée : de longs paragraphes explicatifs sous chaque titre,
   des cartes arrondies partout, des dégradés, des emoji en guise d'icônes, des
   libellés vagues. À éviter absolument.
5. **« Comme un VMS payant, professionnel. »** Les repères du métier :
   Milestone XProtect, Genetec Security Center, Avigilon, Verkada.
6. **« Bien organisé. »** La navigation actuelle ne le satisfait pas : à
   repenser librement.

---

## 7. Ce qui a déjà été essayé et rejeté

Utile pour ne pas refaire les mêmes erreurs :

- **Un tableau de bord de statistiques comme page d'accueil** — rejeté : un
  agent veut voir des images, pas des compteurs.
- **Sept onglets côte à côte** — rejeté : trop de bascules.
- **Texte à 11–12 px, interface dense** — rejeté : illisible et fatigant.
- **Un paragraphe d'explication sous chaque titre et chaque champ** — rejeté :
  c'est ce qui donnait le plus l'impression d'une interface générée.
- **Un accent orange saturé** — rejeté : il attirait l'œil sans rien signaler,
  et se confondait avec la couleur des alertes.

---

## 8. Ce qu'on attend en retour

Une proposition de **`dashboard.css`** complet, et les modifications de
structure HTML nécessaires (les identifiants et classes actuels peuvent être
changés librement — le JavaScript sera adapté).

Ce qui compte le plus, dans l'ordre :

1. Qu'un agent de sécurité comprenne l'écran Direct **sans qu'on le lui
   explique**.
2. Qu'une alerte critique se remarque **immédiatement**, sans lecture.
3. Que l'écran soit reposant sur huit heures de poste.
4. Que ça tienne en 1366 × 768.
