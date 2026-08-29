"""Banc de test : mesurer la detection sur des videos de reference.

    python scripts/benchmark.py run                 lance la mesure
    python scripts/benchmark.py list                liste les rapports passes
    python scripts/benchmark.py compare A B         compare deux rapports
    python scripts/benchmark.py compare --last      compare les deux derniers

A lancer avant et apres tout changement de seuil ou de modele : sans point de
comparaison, on ne sait pas si l'on a progresse ou recule.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.benchmark import compare, list_results, load_result, run_benchmark  # noqa: E402


def cmd_run(args):
    rapport = run_benchmark(models=args.models, stride=args.stride)
    synthese = rapport["synthese"]

    print()
    print("=" * 66)
    print("TAUX DE DETECTION  (part des images ou la classe attendue est vue)")
    print("=" * 66)
    if not synthese["taux_detection"]:
        print("  aucune classe attendue declaree dans le jeu de test")
    for classe, taux in sorted(synthese["taux_detection"].items(),
                               key=lambda kv: kv[1]):
        barre = "#" * int(taux * 30)
        print(f"  {classe:34} {taux * 100:5.1f}%  {barre}")

    print()
    print("=" * 66)
    print("FAUSSES DETECTIONS  (par minute, sur les clips ou c'est absent)")
    print("=" * 66)
    if not synthese["fausses_par_minute"]:
        print("  aucune : le jeu de test ne produit aucun bruit")
    for classe, par_min in synthese["fausses_par_minute"].items():
        print(f"  {classe:34} {par_min:6.1f}/min")

    print()
    for clip in rapport["clips"]:
        if "erreur" in clip:
            print(f"  ! {clip['file']} : {clip['erreur']}")
    print(f"Rapport : {rapport.get('fichier')}")


def cmd_list(args):
    rapports = list_results()
    if not rapports:
        print("Aucun rapport. Lancez d'abord : python scripts/benchmark.py run")
        return
    for path in rapports:
        data = load_result(path)
        classes = len(data["synthese"]["taux_detection"])
        bruit = sum(data["synthese"]["fausses_par_minute"].values())
        print(f"  {path.name:24} {data['date']}  {classes} classe(s), bruit {bruit:.1f}/min")


def cmd_compare(args):
    rapports = list_results()
    if args.last:
        if len(rapports) < 2:
            print("Il faut au moins deux rapports pour comparer.")
            return
        avant, apres = rapports[-2], rapports[-1]
    else:
        avant, apres = Path(args.avant), Path(args.apres)

    resultat = compare(load_result(avant), load_result(apres))
    print(f"Avant : {resultat['avant']}")
    print(f"Apres : {resultat['apres']}")
    print()
    for ligne in resultat["lignes"]:
        marque = "OK " if ligne["amelioration"] else "-- "
        if ligne["ecart"] == 0:
            marque = "=  "
        print(f"  {marque} {ligne['classe']:30} {ligne['mesure']:22} "
              f"{ligne['avant']} -> {ligne['apres']}  ({ligne['ecart']:+})")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="commande", required=True)

    p_run = sub.add_parser("run", help="lance la mesure")
    p_run.add_argument("--models", nargs="*", help="limiter a certains modeles")
    p_run.add_argument("--stride", type=int, help="analyser une image sur N")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="liste les rapports")
    p_list.set_defaults(func=cmd_list)

    p_cmp = sub.add_parser("compare", help="compare deux rapports")
    p_cmp.add_argument("avant", nargs="?")
    p_cmp.add_argument("apres", nargs="?")
    p_cmp.add_argument("--last", action="store_true", help="les deux derniers")
    p_cmp.set_defaults(func=cmd_compare)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
