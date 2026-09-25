"""
verifier_ocr.py - Lance l'OCR sur les pages "OCR requis" de tests/docs_test/.

CONFIDENTIALITE : affiche SEULEMENT, par fichier : nombre de pages OCR, temps par
page, nombre de lignes, confiance moyenne, nombre de lignes sous 0,90, pic de RAM.
JAMAIS de texte. Les resultats intermediaires (texte reconnu) restent dans
data/ocr/ (interdit en lecture a Claude).

AVANT de lancer : Ollama sans modele charge (ollama ps).
Lancement (venv active, depuis OCR_PROJECT) :  python tests/verifier_ocr.py
"""

import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from src.extract_text import extraire                               # noqa: E402
from src.ocr_worker import STATUT_OK, lancer_ocr, taches_depuis_extraction  # noqa: E402

DOSSIER = RACINE / "tests" / "docs_test"
SEUIL_LIGNE = 0.90
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    fichiers = sorted(p for p in DOSSIER.rglob("*") if p.is_file())
    taches = []
    for chemin in fichiers:
        taches += taches_depuis_extraction(chemin, extraire(chemin))
    if not taches:
        sys.exit("Aucune page a OCR dans tests/docs_test/.")

    debut = time.perf_counter()
    lot = lancer_ocr(taches)                     # UN lot : modele charge une fois
    total = time.perf_counter() - debut

    print(f"{'Fichier':<45} {'Pages':>5} {'s/page':>7} {'Lignes':>7} "
          f"{'Conf.moy':>9} {'<0,90':>6} {'Pic RAM':>9}  Erreurs")
    print("-" * 105)
    toutes_lignes = []
    for chemin in fichiers:
        pages = lot.pages_du_fichier(chemin)
        if not pages:
            continue
        ok = [p for p in pages.values() if p.statut == STATUT_OK]
        lignes = [l for p in ok for l in p.lignes]          # on ne garde que les CHIFFRES
        toutes_lignes += lignes
        conf = sum(l.confiance for l in lignes) / len(lignes) if lignes else 0
        sous_seuil = sum(1 for l in lignes if l.confiance < SEUIL_LIGNE)
        s_page = sum(p.duree_s for p in ok) / len(ok) if ok else 0
        pic = max(p.pic_ram_mo for p in pages.values())
        erreurs = [f"p{n}:{p.raison}" for n, p in sorted(pages.items())
                   if p.statut != STATUT_OK]
        nom = str(chemin.relative_to(DOSSIER))[:45]
        print(f"{nom:<45} {len(pages):>5} {s_page:>7.1f} {len(lignes):>7} "
              f"{conf:>9.3f} {sous_seuil:>6} {pic:>7.0f} Mo  {' '.join(erreurs) or '-'}")

    conf_globale = (sum(l.confiance for l in toutes_lignes) / len(toutes_lignes)
                    if toutes_lignes else 0)
    print("=" * 60)
    print(f"Pages OCR                   : {len(lot.pages)}")
    print(f"Chargement(s) du modele     : {', '.join(f'{c:.1f} s' for c in lot.chargement_s)}")
    print(f"Temps total du lot          : {total:.1f} s")
    print(f"Lignes reconnues            : {len(toutes_lignes)}")
    print(f"Confiance moyenne globale   : {conf_globale:.3f}")
    print(f"Lignes sous {SEUIL_LIGNE:.2f}             : "
          f"{sum(1 for l in toutes_lignes if l.confiance < SEUIL_LIGNE)}")
    print(f"Pic de RAM du worker        : {lot.pic_ram_mo:.0f} Mo")
    print(f"Alertes                     : {' ; '.join(lot.alertes) or '-'}")


if __name__ == "__main__":
    main()
