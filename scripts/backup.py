"""Sauvegarde et restauration de la configuration et de l'historique.

    python scripts/backup.py create                 cree une sauvegarde
    python scripts/backup.py list                   liste les sauvegardes
    python scripts/backup.py restore <fichier.zip>  restaure

Ce qui est sauvegarde
---------------------
La base d'alertes, les cameras, les zones, les reglages et la configuration.
Autrement dit : tout ce qui a ete construit a l'usage et qui ne se retrouve pas
dans le depot Git. Un an d'historique d'alertes est une donnee HSE — elle ne
doit pas disparaitre avec un disque.

Ce qui n'est PAS sauvegarde, et pourquoi
----------------------------------------
Les modeles et l'environnement Python : volumineux, et reconstructibles.
Les snapshots et clips video : volumineux, purges a 30 jours de toute facon.
Le fichier .env : il contient le jeton du bot Telegram. Une archive de
sauvegarde circule (copie sur une cle, envoyee par messagerie) ; un secret ne
doit pas voyager avec elle. Recopiez-le a la main, il fait deux lignes.

Une sauvegarde qui n'a jamais ete restauree n'est pas une sauvegarde : testez
la restauration sur une machine de developpement avant d'en avoir besoin.
"""

import argparse
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = PROJECT_ROOT / "backups"

# Chemins relatifs a la racine du projet.
A_SAUVEGARDER = [
    "data/smokewatch.db",
    "data/settings.json",
    "data/health.json",
    "config/cameras.json",
    "config/zones.json",
    "config/config.yaml",
    "config/benchmark.yaml",
]


def cmd_create(args):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    archive = BACKUP_DIR / f"smokewatch_{datetime.now():%Y%m%d_%H%M%S}.zip"

    presents, absents = [], []
    for relatif in A_SAUVEGARDER:
        (presents if (PROJECT_ROOT / relatif).exists() else absents).append(relatif)

    if not presents:
        print("Rien a sauvegarder : aucun fichier de donnees trouve.")
        return

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for relatif in presents:
            zf.write(PROJECT_ROOT / relatif, relatif)

    taille = archive.stat().st_size / 1024
    print(f"Sauvegarde creee : {archive}  ({taille:.0f} Ko)")
    for relatif in presents:
        print(f"  + {relatif}")
    for relatif in absents:
        print(f"  - {relatif} (absent)")
    print()
    print("Le fichier .env n'est pas inclus : il contient le jeton Telegram.")


def cmd_list(args):
    if not BACKUP_DIR.exists() or not any(BACKUP_DIR.glob("*.zip")):
        print("Aucune sauvegarde. Lancez : python scripts/backup.py create")
        return
    for archive in sorted(BACKUP_DIR.glob("*.zip")):
        taille = archive.stat().st_size / 1024
        date = datetime.fromtimestamp(archive.stat().st_mtime)
        print(f"  {archive.name:34} {date:%Y-%m-%d %H:%M}  {taille:7.0f} Ko")


def cmd_restore(args):
    archive = Path(args.archive)
    if not archive.exists():
        archive = BACKUP_DIR / args.archive
    if not archive.exists():
        print(f"Archive introuvable : {args.archive}")
        return

    with zipfile.ZipFile(archive) as zf:
        contenu = zf.namelist()

    print(f"Archive : {archive}")
    for nom in contenu:
        print(f"  {nom}")
    print()
    print("ARRETEZ LE PIPELINE ET L'INTERFACE avant de restaurer : ils ecrivent")
    print("dans ces fichiers, et une restauration a chaud serait ecrasee.")
    print()

    if not args.yes:
        reponse = input("Restaurer et ecraser les fichiers actuels ? [oui/non] ").strip().lower()
        if reponse not in ("oui", "o", "yes", "y"):
            print("Annule.")
            return

    # Les fichiers actuels sont mis de cote plutot que supprimes : si la
    # restauration se revele etre une erreur, rien n'est perdu.
    secours = PROJECT_ROOT / "backups" / f"avant_restauration_{datetime.now():%Y%m%d_%H%M%S}"
    for nom in contenu:
        actuel = PROJECT_ROOT / nom
        if actuel.exists():
            destination = secours / nom
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(actuel, destination)

    with zipfile.ZipFile(archive) as zf:
        zf.extractall(PROJECT_ROOT)

    print(f"Restauration terminee. Etat precedent conserve dans : {secours}")
    print("Relancez le pipeline et l'interface.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="commande", required=True)

    sub.add_parser("create", help="cree une sauvegarde").set_defaults(func=cmd_create)
    sub.add_parser("list", help="liste les sauvegardes").set_defaults(func=cmd_list)

    p_restore = sub.add_parser("restore", help="restaure une sauvegarde")
    p_restore.add_argument("archive")
    p_restore.add_argument("--yes", action="store_true", help="sans demander confirmation")
    p_restore.set_defaults(func=cmd_restore)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
