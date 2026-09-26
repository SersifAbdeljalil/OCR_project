"""
run_pipeline.py - Traite tout le dossier d'entree en une commande.

Lancement (depuis le dossier du projet) :
    .\\.venv\\Scripts\\python.exe run_pipeline.py
    .\\.venv\\Scripts\\python.exe run_pipeline.py --profil performant
    .\\.venv\\Scripts\\python.exe run_pipeline.py --entree D:\\Scans --sortie D:\\Classement

Reglages de la machine (profil, dossiers, Ollama) : config/machine.json.
Le resume n'affiche aucun nom de personne : les noms de fichiers sont masques
(masking.nom_affichable) et aucune valeur extraite n'est montree.
"""

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE))
from src.config import ErreurRegistre, charger_profil   # noqa: E402
from src.pipeline import traiter_lot                     # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def minutes(secondes: float) -> str:
    m, s = divmod(int(round(secondes)), 60)
    return f"{m} min {s:02d} s" if m else f"{s} s"


def afficher_resume(bilan) -> None:
    print("\n" + "=" * 60)
    print("RÉSUMÉ DU LOT")
    print("=" * 60)
    print(f"Documents trouvés        : {bilan.total}")
    print(f"Rangés automatiquement   : {bilan.ranges}")
    print(f"Envoyés en A_Valider     : {bilan.a_valider}")
    print(f"Déjà traités (écartés)   : {bilan.deja_traites}")
    print(f"Erreurs (restés en entrée): {bilan.erreurs}")
    print(f"Pages OCR                : {bilan.pages_ocr} (pic RAM {bilan.pic_ram_ocr_mo:.0f} Mo)")
    print(f"Temps OCR / LLM / total  : {minutes(bilan.duree_ocr_s)} / "
          f"{minutes(bilan.duree_llm_s)} / {minutes(bilan.duree_s)}")
    a_voir = [d for d in bilan.documents if d.decision in ("a_valider", "erreur")]
    if a_voir:
        print("\nÀ vérifier (noms masqués) :")
        for d in a_voir:
            categorie = d.categorie or "-"
            print(f"  - {d.decision:<9} {categorie:<14} {d.destination or d.fichier}")
    for alerte in bilan.alertes:
        print(f"ALERTE : {alerte}")
    print(f"\nJournal : {bilan.journal}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Tri documentaire local")
    parser.add_argument("--profil", help="profil de machine (config/machine.json)")
    parser.add_argument("--entree", help="dossier d'entree (sinon config/machine.json)")
    parser.add_argument("--sortie", help="dossier de sortie (sinon config/machine.json)")
    args = parser.parse_args()
    try:
        profil = charger_profil(args.profil)
    except ErreurRegistre as err:
        print(f"[ERREUR] {err}")
        return 2
    bilan = traiter_lot(profil, dossier_entree=args.entree, dossier_sortie=args.sortie)
    afficher_resume(bilan)
    return 1 if bilan.erreurs or bilan.alertes else 0


if __name__ == "__main__":
    sys.exit(main())
