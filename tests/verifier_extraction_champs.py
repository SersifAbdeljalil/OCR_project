"""
verifier_extraction_champs.py - Extraction regex + champs libres LLM sur tests/docs_test/.

CONFIDENTIALITE : affiche SEULEMENT, par fichier : categorie, et pour chaque champ
« trouve / absent » + confiance OCR (ou « natif ») + methode (regex / llm), controle des
totaux (ok / ecart / manquant), validation necessaire, temps. JAMAIS de valeur.

Categorie : celle des MOTS-CLES seuls (rules.py). Sans categorie : champs generiques.
Ordre : Ollama verifie vide -> OCR -> tous les appels LLM, modele garde charge ->
dechargement.

Lancement (venv active, depuis OCR_PROJECT, navigateur ferme) :
    python tests/verifier_extraction_champs.py            regex + LLM
    python tests/verifier_extraction_champs.py --sans-llm regex seules
"""

import sys
import time
from collections import Counter
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from src.config import champs_attendus, registre_par_defaut          # noqa: E402
from src.extract_text import extraire                                  # noqa: E402
from src.extractor import (charger_champs_libres, charger_config,     # noqa: E402
                           extraire_document, lignes_du_document)
from src.llm import ClientOllama                                       # noqa: E402
from src.ocr_worker import fusionner_texte, lancer_ocr, taches_depuis_extraction  # noqa: E402
from src.rules import analyser                                         # noqa: E402

DOSSIER = RACINE / "tests" / "docs_test"
SANS_LLM = "--sans-llm" in sys.argv
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    client = None if SANS_LLM else ClientOllama()
    if client and client.modeles_charges():
        sys.exit("[ARRET] Un modele est charge dans Ollama : jamais OCR et LLM en meme temps.")
    registre, config = registre_par_defaut(), charger_config()
    libres, _ = charger_champs_libres()
    fichiers = sorted(p for p in DOSSIER.rglob("*") if p.is_file())
    extractions = {f: extraire(f) for f in fichiers}
    taches = [t for f, e in extractions.items() for t in taches_depuis_extraction(f, e)]
    lot = lancer_ocr(taches)

    # Tous les appels LLM, modele garde charge, puis decharge
    resultats, t_lot = {}, time.perf_counter()
    gestionnaire = client.modele_charge() if client else None
    if gestionnaire:
        gestionnaire.__enter__()
    try:
        for chemin in fichiers:
            e = extractions[chemin]
            if e.illisible:
                resultats[chemin] = None
                continue
            pages = lot.pages_du_fichier(chemin)
            texte, _ = fusionner_texte(e, pages)
            categorie = analyser(texte, registre).categorie or "autres"
            debut = time.perf_counter()
            r = extraire_document(lignes_du_document(e, pages), categorie, client,
                                  registre, config)
            resultats[chemin] = (categorie, r, time.perf_counter() - debut)
    finally:
        if gestionnaire:
            gestionnaire.__exit__(None, None, None)
    t_lot = time.perf_counter() - t_lot

    bilan = Counter()
    for chemin in fichiers:
        nom = str(chemin.relative_to(DOSSIER))
        if resultats[chemin] is None:
            print(f"\n{nom} : illisible")
            continue
        categorie, r, duree = resultats[chemin]
        print(f"\n{nom}   [categorie (mots-cles) : {categorie}]   temps : {duree:.1f} s")
        morceaux = []
        for champ in champs_attendus(registre, categorie):
            if champ not in config and champ not in libres:
                continue                               # extrait par personne
            c = r.champs.get(champ)
            if c is None:
                morceaux.append(f"{champ}: absent")
                bilan["absent"] += 1
            else:
                conf = "natif" if c.confiance is None else f"{c.confiance:.2f}"
                morceaux.append(f"{champ}: trouve ({c.methode}, {conf})")
                bilan[f"trouve ({c.methode})"] += 1
        for i in range(0, len(morceaux), 3):
            print("   " + " | ".join(morceaux[i:i + 3]))
        if r.statut_totaux:
            print(f"   totaux : {r.statut_totaux}")
            bilan[f"totaux {r.statut_totaux}"] += 1
        rejets = sum(1 for a in r.alertes if "absente du texte source" in a)
        if rejets:
            print(f"   valeurs du LLM rejetees (anti-invention) : {rejets}")
            bilan["rejets anti-invention"] += rejets
        print(f"   validation necessaire : {'OUI' if r.necessite_validation_humaine else 'non'}")
        bilan["validation" if r.necessite_validation_humaine else "sans validation"] += 1

    print("\n" + "=" * 60)
    for cle, nombre in sorted(bilan.items()):
        print(f"{cle:<24}: {nombre}")
    print(f"Pic RAM OCR             : {lot.pic_ram_mo:.0f} Mo ({lot.recyclages} relance(s))")
    print(f"Lot d'extraction        : {t_lot:.1f} s")
    if client:
        print(f"Modele decharge a la fin: {'oui' if not client.modeles_charges() else 'NON'}")


if __name__ == "__main__":
    main()
