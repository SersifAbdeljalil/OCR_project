"""
mesurer_extraction_synthetique.py - Exactitude de l'extraction CHAMP PAR CHAMP sur
les 10 factures FICTIVES de tests/docs_synthetiques/ (verite_terrain.json).

Donnees INVENTEES : les valeurs peuvent etre affichees.
Categorie imposee a "factures" (on mesure l'extraction, pas la classification).
Ordre : Ollama verifie vide -> OCR (f07 a f09) -> tous les appels LLM, modele garde
charge -> dechargement.

Lancement (venv active, depuis OCR_PROJECT, navigateur ferme) :
    python tests/mesurer_extraction_synthetique.py            regex + LLM (fournisseur)
    python tests/mesurer_extraction_synthetique.py --sans-llm regex seules
"""

import json
import sys
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from src.extract_text import extraire                                   # noqa: E402
from src.extractor import cle_comparaison, extraire_document, lignes_du_document  # noqa: E402
from src.llm import ClientOllama                                        # noqa: E402
from src.ocr_worker import lancer_ocr, taches_depuis_extraction         # noqa: E402

DOSSIER = RACINE / "tests" / "docs_synthetiques"
SANS_LLM = "--sans-llm" in sys.argv
CHAMPS = ["date_facture", "numero", "montant_ht", "tva", "montant_ttc", "ice"] + \
    ([] if SANS_LLM else ["fournisseur"])
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def egal(champ, attendu, trouve) -> bool:
    if attendu is None:
        return trouve is None
    if trouve is None:
        return False
    if isinstance(trouve, Decimal):
        return trouve == Decimal(attendu)
    if champ == "fournisseur":                 # texte libre : casse, accents, espaces
        return cle_comparaison(trouve) == cle_comparaison(attendu)
    return str(trouve) == str(attendu)


def main():
    client = None if SANS_LLM else ClientOllama()
    if client and client.modeles_charges():
        sys.exit("[ARRET] Un modele est charge dans Ollama : jamais OCR et LLM en meme temps.")
    verite = json.loads((DOSSIER / "verite_terrain.json").read_text(encoding="utf-8"))
    extractions = {nom: extraire(DOSSIER / nom) for nom in verite}
    taches = [t for nom, e in extractions.items()
              for t in taches_depuis_extraction(DOSSIER / nom, e)]
    lot = lancer_ocr(taches)

    resultats, t_lot = {}, time.perf_counter()
    gestionnaire = client.modele_charge() if client else None
    if gestionnaire:
        gestionnaire.__enter__()
    try:
        for nom in verite:
            lignes = lignes_du_document(extractions[nom], lot.pages_du_fichier(DOSSIER / nom))
            debut = time.perf_counter()
            resultats[nom] = (extraire_document(lignes, "factures", client),
                              time.perf_counter() - debut)
    finally:
        if gestionnaire:
            gestionnaire.__exit__(None, None, None)
    t_lot = time.perf_counter() - t_lot

    score, faux = Counter(), 0
    for nom, attendu in verite.items():
        r, duree = resultats[nom]
        v = r.valeurs()
        print(f"\n=== {nom}   totaux : {r.statut_totaux}   validation : "
              f"{'OUI' if r.necessite_validation_humaine else 'non'}   temps : {duree:.1f} s")
        for champ in CHAMPS:
            ok = egal(champ, attendu.get(champ), v.get(champ))
            score[(champ, ok)] += 1
            faux += (not ok) and v.get(champ) is not None
            c = r.champs.get(champ)
            conf = f"{c.confiance:.2f}" if c and c.confiance is not None else "natif" if c else "-"
            print(f"  {'OK ' if ok else 'XX '} {champ:<13} attendu={str(attendu.get(champ)):<30} "
                  f"trouve={str(v.get(champ)):<30} conf={conf}")
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
    print(f"Valeurs FAUSSES (trouvees mais differentes) : {faux}")
    print(f"OCR : {len(taches)} page(s), pic {lot.pic_ram_mo:.0f} Mo ; lot d'extraction : "
          f"{t_lot:.1f} s")
    if client:
        print(f"Modele decharge a la fin : {'oui' if not client.modeles_charges() else 'NON'}")


if __name__ == "__main__":
    main()
