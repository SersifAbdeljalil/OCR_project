"""
mesurer_extraction_synthetique.py - Exactitude de l'extraction CHAMP PAR CHAMP sur
les 10 factures FICTIVES de tests/docs_synthetiques/ (verite_terrain.json).

Donnees INVENTEES : les valeurs peuvent etre affichees.
Categorie imposee a "factures" (on mesure l'extraction, pas la classification).
AVANT de lancer : Ollama sans modele charge (l'OCR tourne pour f07 a f09).

Lancement (venv active, depuis OCR_PROJECT) :
    python tests/mesurer_extraction_synthetique.py
"""

import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from src.extract_text import extraire                                   # noqa: E402
from src.extractor import extraire_champs, lignes_du_document           # noqa: E402
from src.ocr_worker import lancer_ocr, taches_depuis_extraction         # noqa: E402

DOSSIER = RACINE / "tests" / "docs_synthetiques"
CHAMPS = ["date_facture", "numero", "montant_ht", "tva", "montant_ttc", "ice"]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def egal(attendu, trouve) -> bool:
    if attendu is None:
        return trouve is None
    if trouve is None:
        return False
    if isinstance(trouve, Decimal):
        return trouve == Decimal(attendu)
    return str(trouve) == str(attendu)


def main():
    verite = json.loads((DOSSIER / "verite_terrain.json").read_text(encoding="utf-8"))
    extractions = {nom: extraire(DOSSIER / nom) for nom in verite}
    taches = [t for nom, e in extractions.items()
              for t in taches_depuis_extraction(DOSSIER / nom, e)]
    lot = lancer_ocr(taches)

    score = Counter()
    for nom, attendu in verite.items():
        e = extractions[nom]
        lignes = lignes_du_document(e, lot.pages_du_fichier(DOSSIER / nom))
        r = extraire_champs(lignes, "factures")
        v = r.valeurs()
        print(f"\n=== {nom}   totaux : {r.statut_totaux}   validation : "
              f"{'OUI' if r.necessite_validation_humaine else 'non'}")
        for champ in CHAMPS:
            ok = egal(attendu.get(champ), v.get(champ))
            score[(champ, ok)] += 1
            c = r.champs.get(champ)
            conf = f"{c.confiance:.2f}" if c and c.confiance is not None else "natif" if c else "-"
            print(f"  {'OK ' if ok else 'XX '} {champ:<13} attendu={str(attendu.get(champ)):<22} "
                  f"trouve={str(v.get(champ)):<22} conf={conf}")
        for a in r.alertes:
            print(f"      alerte : {a}")

    print("\n" + "=" * 60)
    total_ok = 0
    for champ in CHAMPS:
        ok = score[(champ, True)]
        total_ok += ok
        print(f"{champ:<13} : {ok}/{len(verite)} exacts")
    print(f"{'TOTAL':<13} : {total_ok}/{len(verite) * len(CHAMPS)} "
          f"({total_ok / (len(verite) * len(CHAMPS)):.0%})")


if __name__ == "__main__":
    main()
