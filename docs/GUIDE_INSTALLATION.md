# Guide d'installation

Pour le technicien qui installe SmokeWatch sur le serveur du site. Comptez une
heure la première fois, dont une bonne partie en téléchargements.

---

## 1. La machine

SmokeWatch tourne **sans carte graphique** : tout se fait sur le processeur.
C'est le nombre de cœurs qui détermine combien de caméras peuvent être traitées.

| | Minimum | Recommandé pour 7 caméras |
|---|---|---|
| Processeur | 4 cœurs | **8 cœurs** |
| Mémoire | 8 Go | 16 Go |
| Disque | 100 Go | 250 Go SSD |
| Système | Windows 10/11 ou Linux | idem |

**Règle de dimensionnement**, mesurée sur le matériel de développement :

```
cœurs nécessaires ≈ caméras × modèles par caméra × images/s × 0,19
```

7 caméras avec 3 modèles ciblés chacune, à 1 image par seconde, demandent donc
environ 4 cœurs. Affecter à chaque caméra uniquement les modèles pertinents —
EPI dans l'atelier, véhicules au quai — divise la charge par deux ou trois sans
rien perdre de la couverture réelle.

La machine doit rester **allumée en permanence** et être joignable sur le réseau
interne. Ce n'est pas un poste de travail.

---

## 2. Installation

### Prérequis

- **Python 3.13** ([python.org](https://www.python.org/downloads/)) — cocher
  « Add Python to PATH » à l'installation
- **Git** pour récupérer le code et les mises à jour
- **NSSM** ([nssm.cc](https://nssm.cc/download)) — décompressé, et son dossier
  ajouté au PATH système. Il transforme un programme en service Windows, ce que
  Python ne sait pas faire seul.

### Récupérer le code

```powershell
cd C:\
git clone <url-du-depot> SmokeWatch
cd SmokeWatch
```

### Environnement Python

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Compter 10 à 20 minutes : plusieurs Go sont téléchargés.

### Modèles de détection

Copiez les fichiers `.pt` dans `models\`, puis convertissez-les une fois pour
toutes au format accéléré :

```powershell
.\venv\Scripts\python.exe scripts\export_openvino.py
```

Cette conversion apporte un gain d'environ 2,5× sur processeur. Elle prend
quelques minutes et n'est à refaire qu'après un ré-entraînement.

Vérifiez que tout est en place :

```powershell
.\venv\Scripts\python.exe scripts\inspect_models.py
```

### Notifications Telegram

Créez le fichier `.env` à la racine :

```
TELEGRAM_BOT_TOKEN=le_jeton_du_bot
TELEGRAM_CHAT_IDS=id_superviseur,id_agent2,id_groupe
```

Le premier destinataire reçoit toutes les alertes, les suivants uniquement les
critiques.

Pour créer le bot : sur Telegram, écrire à `@BotFather`, envoyer `/newbot`,
suivre les instructions. Pour obtenir un `chat_id` : la personne écrit au bot,
puis ouvrir `https://api.telegram.org/bot<JETON>/getUpdates`.

**Ce fichier ne doit jamais être versionné ni transmis** : le jeton donne le
contrôle du bot, donc la possibilité d'envoyer de fausses alertes à l'équipe.

---

## 3. Vérifier avant de mettre en service

```powershell
.\venv\Scripts\python.exe -m pytest tests -q
```

Puis lancez les deux processus à la main, dans deux fenêtres :

```powershell
.\venv\Scripts\python.exe -u -m app.pipeline
.\venv\Scripts\python.exe -m uvicorn app.api:app --host 0.0.0.0 --port 8000
```

Ouvrez `http://localhost:8000`. Vous devez voir l'interface, et le voyant en bas
à gauche doit passer au vert.

Arrêtez les deux processus (Ctrl+C) avant de passer en service.

---

## 4. Déclarer les caméras

Tout se fait depuis l'interface, écran **Caméras** → « Ajouter une caméra ».

Le champ **Source** accepte quatre formes :

| Forme | Exemple | Usage |
|---|---|---|
| Numéro | `0` | webcam locale (essais) |
| Adresse RTSP | `rtsp://user:mdp@192.168.1.42:554/stream1` | caméra IP du site |
| Fichier vidéo | `videos/essai.mp4` | rejeu, mise au point |
| Dossier | `videos/sequence/` | suite d'images |

**Utilisez « Tester la connexion » avant d'enregistrer.** Le bouton affiche la
résolution si la caméra répond, et le motif de l'échec sinon. L'adresse RTSP
exacte figure dans la documentation de la caméra ou son interface web.

Cochez uniquement les modèles pertinents pour ce que voit la caméra : c'est le
principal levier de performance.

Le pipeline prend en compte la nouvelle caméra en quelques secondes, sans
redémarrage.

### Délimiter les zones

Écran **Zones**. Sans zone, la caméra est analysée en entier — le détecteur
d'EPI se déclenchera sur le parking, celui de véhicules sur la route derrière la
clôture. **C'est la première cause de fausses alertes.**

Deux types de zones :

- **Surveiller** : seul ce qui tombe dedans est pris en compte
- **Ignorer (masque)** : ce qui tombe dedans est écarté, même à l'intérieur
  d'une zone surveillée. Souvent plus rapide — masquer la route au fond du champ
  prend dix secondes ; contourner finement l'atelier en prend dix minutes.

Chaque zone peut avoir son horaire : le contrôle des EPI pendant les postes, la
détection de présence près des fours la nuit uniquement.

---

## 5. Mise en service permanente

En PowerShell **administrateur** :

```powershell
cd C:\SmokeWatch
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Le script vérifie ses prérequis (droits, NSSM, environnement, port libre), crée
deux services Windows démarrant automatiquement au boot et redémarrant après un
incident, puis les lance.

```powershell
Get-Service SmokeWatch*                          # état
Get-Content logs\pipeline.log -Tail 50 -Wait     # suivre la détection
Restart-Service SmokeWatchWeb                    # après une mise à jour
```

Le script est rejouable : relancez-le après chaque mise à jour du code.

### Ouvrir l'accès à l'équipe

```powershell
New-NetFirewallRule -DisplayName "SmokeWatch" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

L'équipe accède ensuite à `http://<adresse-du-serveur>:8000` depuis n'importe
quel poste ou tablette du réseau interne. Rien à installer de leur côté.

> **Attention** : l'interface n'a pas d'authentification. Elle ne doit pas être
> exposée sur Internet, seulement sur le réseau interne.

---

## 6. Exploitation courante

### Sauvegardes

```powershell
.\venv\Scripts\python.exe scripts\backup.py create
```

Sauvegarde la base d'alertes, les caméras, les zones et les réglages. À
planifier une fois par jour (Planificateur de tâches Windows).

Le `.env` n'est **pas** inclus, volontairement : une archive circule, un secret
ne doit pas voyager avec.

**Testez une restauration** sur une machine de développement avant d'en avoir
besoin : une sauvegarde jamais restaurée n'est pas une sauvegarde.

### Mettre à jour

```powershell
Stop-Service SmokeWatchPipeline, SmokeWatchWeb
.\venv\Scripts\python.exe scripts\backup.py create
git pull
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pytest tests -q
Start-Service SmokeWatchPipeline, SmokeWatchWeb
```

Si la nouvelle version pose problème : `git checkout <commit-précédent>`, puis
restaurez la sauvegarde et relancez.

### Ce qui se purge tout seul

Snapshots et clips au-delà de **30 jours**, alertes au-delà d'**un an**. La
purge a lieu au démarrage du pipeline. Les journaux sont conservés 30 jours.

---

## 7. En cas de problème

| Symptôme | Cause probable | Solution |
|---|---|---|
| Un service ne démarre pas | Dépendances ou modèles manquants | Consulter `logs\pipeline.err.log` |
| « Détection arrêtée » dans l'interface | Le pipeline ne tourne plus | `Restart-Service SmokeWatchPipeline` |
| Caméra « HORS LIGNE » | Adresse, identifiants ou réseau | Écran Caméras → Tester la connexion |
| Traitement très lent | Trop de modèles par caméra | Réduire les modèles, ou les images/s |
| Le pipeline ne démarre jamais | Machine trop chargée pendant la compilation initiale | Attendre : compter 10 s par modèle au premier lancement |
| Beaucoup de fausses alertes | Zones absentes ou trop larges | Écran Zones ; faire marquer les fausses alertes par les opérateurs |

Le détail technique — architecture, choix de conception, pièges déjà rencontrés
— se trouve dans [DOCUMENTATION.md](../DOCUMENTATION.md).
