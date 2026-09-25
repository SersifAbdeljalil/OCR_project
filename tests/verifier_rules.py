"""
verifier_rules.py - Classification par regles sur tests/docs_test/ (texte natif + OCR).

CONFIDENTIALITE : affiche SEULEMENT, par fichier : categorie trouvee, verdict,
scores, sous-dossier et signaux de qualite (part d'arabe, confiance OCR, lignes
sous 0,90). JAMAIS de texte. Les noms de categories viennent du registre (pas des
documents). Le texte OCR intermediaire reste dans data/ocr/.

AVANT de lancer : Ollama sans modele charge (ollama ps) ; l'OCR tourne.
Lancement (venv active, depuis OCR_PROJECT) :  python tests/verifier_rules.py
"""

import sys
from collections import Counter
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from src.extract_text import extraire                                 # noqa: E402
from src.ocr_worker import (STATUT_OK, fusionner_texte, lancer_ocr,   # noqa: E402
                            taches_depuis_extraction)
from src.rules import analyser                                        # noqa: E402

DOSSIER = RACINE / "tests" / "docs_test"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def scores_courts(scores: dict) -> str:
    """Seulement les categories qui ont au moins un indice : 'factures:2 contrats:1'."""
    non_nuls = [f"{c}:{s}" for c, s in scores.items() if s]
    return " ".join(non_nuls) or "-"


def main():
    fichiers = sorted(p for p in DOSSIER.rglob("*") if p.is_file())
    extractions = {f: extraire(f) for f in fichiers}
    taches = [t for f, e in extractions.items() for t in taches_depuis_extraction(f, e)]
    lot = lancer_ocr(taches)                        # un seul lot pour tout le jeu de test

    print(f"{'Fichier':<40} {'Categorie':<13} {'Verdict':<13} {'Sous-dossier':<12} "
          f"{'Scores':<34} {'Titre':<24} {'Arabe':>6} {'Conf.OCR':>8} {'<0,90':>7}")
    print("-" * 165)
    bilan = Counter()
    for chemin in fichiers:
        e = extractions[chemin]
        nom = str(chemin.relative_to(DOSSIER))[:40]
        if e.illisible:
            print(f"{nom:<40} illisible")
            bilan["illisible"] += 1
            continue
        pages = lot.pages_du_fichier(chemin)
        texte, _alertes = fusionner_texte(e, pages)
        confiances = [l.confiance for p in pages.values() if p.statut == STATUT_OK
                      for l in p.lignes]
        r = analyser(texte, texte_natif=e.texte, confiances_ocr=confiances)
        s = r.signaux
        conf = f"{s.confiance_ocr_moyenne:.3f}" if s.confiance_ocr_moyenne is not None else "-"
        sous_seuil = f"{s.lignes_ocr_sous_seuil}/{s.lignes_ocr}" if s.lignes_ocr else "-"
        print(f"{nom:<40} {r.categorie or '-':<13} {r.verdict:<13} {r.sous_dossier or '-':<12} "
              f"{scores_courts(r.scores):<34} {scores_courts(r.scores_titre):<24} "
              f"{s.part_arabe:>6.1%} {conf:>8} {sous_seuil:>7}")
        bilan[r.verdict] += 1

    print("=" * 60)
    for verdict, nombre in bilan.most_common():
        print(f"{verdict:<28}: {nombre}")
    print(f"Alertes OCR                 : {' ; '.join(lot.alertes) or '-'}")


if __name__ == "__main__":
    main()
