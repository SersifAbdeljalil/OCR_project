"""
verifier_extraction_champs.py - Extraction regex (b10a) sur tests/docs_test/.

CONFIDENTIALITE : affiche SEULEMENT, par fichier : categorie, et pour chaque champ
« trouve / absent » + confiance OCR, controle des totaux (ok / ecart / manquant) et
validation necessaire. JAMAIS de valeur.

Categorie : celle des MOTS-CLES seuls (rules.py), sans LLM a cette etape.
Sans categorie : champs generiques ("autres").
AVANT de lancer : Ollama sans modele charge (l'OCR tourne), navigateur ferme.

Lancement (venv active, depuis OCR_PROJECT) :  python tests/verifier_extraction_champs.py
"""

import sys
from collections import Counter
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from src.config import champs_attendus, registre_par_defaut          # noqa: E402
from src.extract_text import extraire                                  # noqa: E402
from src.extractor import charger_config, extraire_champs, lignes_du_document  # noqa: E402
from src.ocr_worker import fusionner_texte, lancer_ocr, taches_depuis_extraction  # noqa: E402
from src.rules import analyser                                         # noqa: E402

DOSSIER = RACINE / "tests" / "docs_test"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    registre, config = registre_par_defaut(), charger_config()
    fichiers = sorted(p for p in DOSSIER.rglob("*") if p.is_file())
    extractions = {f: extraire(f) for f in fichiers}
    taches = [t for f, e in extractions.items() for t in taches_depuis_extraction(f, e)]
    lot = lancer_ocr(taches)

    bilan = Counter()
    for chemin in fichiers:
        e = extractions[chemin]
        nom = str(chemin.relative_to(DOSSIER))
        if e.illisible:
            print(f"\n{nom} : illisible")
            continue
        pages = lot.pages_du_fichier(chemin)
        texte, _ = fusionner_texte(e, pages)
        categorie = analyser(texte, registre).categorie or "autres"
        r = extraire_champs(lignes_du_document(e, pages), categorie, registre, config)

        print(f"\n{nom}   [categorie (mots-cles) : {categorie}]")
        morceaux = []
        for champ in champs_attendus(registre, categorie):
            if champ not in config:
                continue                                    # champ libre : pour le LLM
            c = r.champs.get(champ)
            if c is None:
                morceaux.append(f"{champ}: absent")
            else:
                conf = "natif" if c.confiance is None else f"{c.confiance:.2f}"
                morceaux.append(f"{champ}: trouve ({conf})")
            bilan["trouve" if c else "absent"] += 1
        for i in range(0, len(morceaux), 4):
            print("   " + " | ".join(morceaux[i:i + 4]))
        if r.statut_totaux:
            print(f"   totaux : {r.statut_totaux}")
            bilan[f"totaux {r.statut_totaux}"] += 1
        print(f"   validation necessaire : {'OUI' if r.necessite_validation_humaine else 'non'}")
        bilan["validation" if r.necessite_validation_humaine else "sans validation"] += 1

    print("\n" + "=" * 60)
    for cle, nombre in sorted(bilan.items()):
        print(f"{cle:<22}: {nombre}")
    print(f"Pic RAM OCR           : {lot.pic_ram_mo:.0f} Mo ({lot.recyclages} relance(s))")


if __name__ == "__main__":
    main()
