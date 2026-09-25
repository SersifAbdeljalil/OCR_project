"""
verifier_extraction.py - Lance l'extraction (sans OCR) sur tests/docs_test/.

CONFIDENTIALITE : affiche SEULEMENT, pour chaque fichier, son nom, la methode,
le nombre de caracteres extraits, le nombre de pages a passer en OCR et les
alertes (qui ne contiennent jamais de texte du document). JAMAIS de texte.

Lancement (venv active, depuis OCR_PROJECT) :
    python tests/verifier_extraction.py
"""

import sys
import time
from pathlib import Path

# Racine du projet dans le chemin de recherche, pour importer "src"
RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from src.extract_text import extraire   # noqa: E402

DOSSIER = RACINE / "tests" / "docs_test"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # noms accentues


def main():
    fichiers = sorted(p for p in DOSSIER.rglob("*") if p.is_file())
    if not fichiers:
        sys.exit("Aucun fichier dans tests/docs_test/.")

    print(f"{'Fichier':<50} {'Methode':<21} {'Caract.':>8} {'Pages OCR':>10}  Alertes")
    print("-" * 110)
    total_car, total_ocr, illisibles, debut = 0, 0, 0, time.perf_counter()
    for chemin in fichiers:
        r = extraire(chemin)
        nb_car = len(r.texte)          # seulement la LONGUEUR, jamais le texte
        total_car += nb_car
        total_ocr += len(r.pages_ocr)
        illisibles += r.illisible
        nom = str(chemin.relative_to(DOSSIER))
        alertes = " ; ".join(r.alertes) if r.alertes else "-"
        print(f"{nom[:50]:<50} {r.methode:<21} {nb_car:>8} {len(r.pages_ocr):>10}  {alertes}")

    print("=" * 60)
    print(f"Fichiers                    : {len(fichiers)}")
    print(f"Illisibles                  : {illisibles}")
    print(f"Caracteres extraits (total) : {total_car}")
    print(f"Pages a passer en OCR       : {total_ocr}")
    print(f"Temps                       : {time.perf_counter() - debut:.1f} s")


if __name__ == "__main__":
    main()
