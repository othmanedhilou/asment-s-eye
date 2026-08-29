# Guide de l'opérateur

Ce guide s'adresse à l'équipe sécurité. Aucune connaissance technique n'est
nécessaire, et il n'y a **rien à installer** : un navigateur suffit.

---

## Se connecter

Ouvrez votre navigateur et allez à l'adresse fournie par le service technique :

```
http://<adresse-du-serveur>:8000
```

Ajoutez-la à vos favoris. Il n'y a pas de mot de passe.

L'application peut rester ouverte en permanence sur un écran du poste de garde :
elle se met à jour toute seule.

---

## Comprendre l'écran

Le menu de gauche donne accès à six écrans. Les deux qui servent tous les jours
sont **Caméras** et **Alertes**.

En bas à gauche, un voyant indique si la détection tourne :

| Voyant | Signification |
|---|---|
| 🟢 Détection active | Tout fonctionne |
| 🔴 Détection arrêtée | **Plus rien n'est surveillé** — prévenir le service technique |

Ce voyant compte autant que les alertes elles-mêmes : un système arrêté
n'affiche aucune alerte, et l'absence d'alerte ressemble au calme.

---

## Le mur de caméras

L'écran **Caméras** affiche toutes les caméras en direct. Les boîtes rouges sur
l'image signalent ce que le système vient de détecter ; les contours orange
délimitent les zones surveillées.

Trois réglages en haut à droite :

- **La disposition** (1, 2 ou 3 colonnes) selon la taille de votre écran
- **Plein écran** pour un affichage permanent, sans menu
- **Suivi des alertes** : quand une alerte grave arrive, la caméra concernée est
  automatiquement mise en évidence. Laissez cette case cochée.

Un bandeau rouge apparaît en bas de l'écran à chaque alerte critique. Inutile de
surveiller votre téléphone : l'information vient à vous.

---

## Traiter une alerte

L'écran **Alertes** liste ce qui a été détecté, du plus récent au plus ancien.
Chaque ligne montre une photo, ce qui a été vu, la caméra, la zone et l'heure.

Une pastille de couleur indique la gravité :

| Gravité | Ce que c'est | Ce que vous faites |
|---|---|---|
| 🔴 **Critique** | Feu, fumée, arc électrique, personne au sol | Intervenir immédiatement |
| 🟠 **Haute** | EPI manquant, convoyeur déchiré | Rappeler la consigne, faire cesser |
| 🔵 **Moyenne** | Véhicule, présence, chargement | Vérifier si le contexte l'exige |
| ⚪ **Technique** | Caméra hors ligne, disque plein | Prévenir le service technique |

### Deux boutons, deux sens différents

**« Prendre en charge »** — vous avez vu l'alerte et vous vous en occupez. Votre
nom et l'heure sont enregistrés. C'est ce qui permet de savoir, plus tard, que
l'événement a bien été traité et par qui.

**« Fausse alerte »** — le système s'est trompé : il n'y avait rien.

Ce second bouton mérite une explication, parce que son intérêt n'est pas
évident. **Il ne sert pas seulement à faire disparaître la ligne.** Chaque
signalement remplit deux fonctions :

1. il mesure la fiabilité réelle du système, caméra par caméra et détection par
   détection — sans lui, personne ne peut dire si le système est fiable ;
2. il constitue une bibliothèque d'exemples d'erreurs, qui sert ensuite à
   corriger le modèle concerné.

Autrement dit : **plus vous signalez les erreurs, moins il y en aura.** C'est le
geste le plus utile que vous puissiez faire pour ce système, et il prend une
seconde.

Si vous vous êtes trompé, le bouton « ↩ Vraie alerte » revient en arrière.

---

## Retrouver un événement passé

Les menus déroulants en haut de l'écran **Alertes** se combinent :

- par caméra, par zone, par type de détection
- par gravité
- à traiter / déjà traitées
- vraies alertes / fausses alertes
- par période : dernière heure, 24 h, 7 jours, 30 jours
- **par poste de travail** : matin, après-midi, ou nuit

Exemple : « toutes les alertes EPI du poste de nuit sur les 7 derniers jours »
se compose en trois clics.

Le champ de recherche filtre sur le type de détection (`casque`, `Fire`…).

Quand une vidéo a été enregistrée, un lien **🎬 clip vidéo** apparaît sous
l'alerte : il montre les 5 secondes qui précèdent et les 10 secondes qui suivent.
C'est souvent là que se trouve l'explication de ce qui s'est passé.

---

## Les alertes sur téléphone

Les alertes partent aussi sur **Telegram**, avec la photo. Rien à installer de
plus si vous avez déjà l'application.

- **Alertes critiques** : envoyées à toute l'équipe
- **Autres alertes** : envoyées au superviseur seulement

Demandez au service technique d'ajouter votre compte à la liste.

---

## Les autres écrans

**Tableau de bord** — la situation d'un coup d'œil : nombre d'alertes, délai
moyen de prise en charge, fiabilité de chaque détection.

**Zones** — délimite ce qui est surveillé sur chaque caméra. À utiliser avec le
service technique : mal réglé, cet écran peut arrêter une surveillance.

**Rapports** — export Excel de l'historique pour le service HSE.

**Système** — état des caméras et du serveur.

---

## Quand quelque chose ne va pas

| Ce que vous constatez | Ce que ça veut dire | Ce que vous faites |
|---|---|---|
| Voyant rouge « Détection arrêtée » | Plus rien n'est surveillé | Prévenir le service technique **tout de suite** |
| Une caméra affiche « HORS LIGNE » | Caméra débranchée, en panne, ou réseau coupé | Vérifier sur place, puis prévenir |
| Image figée | Le flux ne se met plus à jour | Rafraîchir la page ; si ça persiste, prévenir |
| Trop d'alertes inutiles | Zones mal réglées ou modèle imprécis | **Marquer chacune en « fausse alerte »**, puis signaler |
| Aucune alerte depuis longtemps | Peut être normal… ou pas | Vérifier le voyant vert et l'écran Système |

La dernière ligne est la plus importante. Un système de surveillance qui tombe
en panne devient **silencieux**, et le silence se confond avec le calme. Prenez
l'habitude de vérifier le voyant vert en début de poste.
