"""
verifier_classifier.py - Classification complete de tests/docs_test/.

Ordre (decision de CLAUDE.md, jamais OCR et Ollama en meme temps) :
    1. verification qu'Ollama n'a aucun modele charge ;
    2. OCR de toutes les pages "OCR requis" (sous-process, RAM rendue a la fin) ;
    3. TOUS les appels au moteur, modele garde charge pendant le lot ;
    4. dechargement du modele, et verification.

CONFIDENTIALITE : affiche SEULEMENT, par fichier : verdict des mots-cles, reponse
du moteur, categorie proposee eventuelle, confiance, decision, temps. Jamais de texte.

Lancement (venv active, depuis OCR_PROJECT, navigateur ferme pour la RAM) :
    python tests/verifier_classifier.py
"""

import sys
import time
from collections import Counter
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from src.classifier import classer, creer_moteur                      # noqa: E402
from src.extract_text import extraire                                 # noqa: E402
from src.llm import ClientOllama                                      # noqa: E402
from src.masking import masquer_texte                                 # noqa: E402
from src.ocr_worker import (STATUT_OK, fusionner_texte, lancer_ocr,   # noqa: E402
                            taches_depuis_extraction)

DOSSIER = RACINE / "tests" / "docs_test"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ollama = ClientOllama()
    if ollama.modeles_charges():
        sys.exit("[ARRET] Un modele est charge dans Ollama : jamais OCR et LLM en meme temps.")

    # 1. Extraction + OCR (sous-process)
    fichiers = sorted(p for p in DOSSIER.rglob("*") if p.is_file())
    t0 = time.perf_counter()
    extractions = {f: extraire(f) for f in fichiers}
    taches = [t for f, e in extractions.items() for t in taches_depuis_extraction(f, e)]
    lot_ocr = lancer_ocr(taches)
    t_ocr = time.perf_counter() - t0

    # 2. Tous les appels au moteur, modele garde charge, puis decharge
    moteur = creer_moteur()
    ok, raison = moteur.verifier()
    if not ok:
        sys.exit(f"[ARRET] Moteur indisponible : {raison}")
    resultats = {}
    t1 = time.perf_counter()
    with moteur.lot():
        t_chargement = time.perf_counter() - t1
        for chemin, e in extractions.items():
            if e.illisible:
                resultats[chemin] = None
                continue
            pages = lot_ocr.pages_du_fichier(chemin)
            texte, _ = fusionner_texte(e, pages)
            confiances = [l.confiance for p in pages.values() if p.statut == STATUT_OK
                          for l in p.lignes]
            debut = time.perf_counter()
            r = classer(texte, moteur=moteur, texte_natif=e.texte, confiances_ocr=confiances)
            resultats[chemin] = (r, time.perf_counter() - debut)
    t_llm = time.perf_counter() - t1
    time.sleep(2)
    decharge = not ollama.modeles_charges()

    # 3. Affichage : metriques seulement
    print(f"{'Fichier':<38} {'Mots-cles':<25} {'Moteur':<13} {'Proposee':<18} "
          f"{'Conf.':>5}  {'Decision':<30} {'Temps':>6}")
    print("-" * 145)
    decisions = Counter()
    for chemin in fichiers:
        nom = str(chemin.relative_to(DOSSIER))[:38]
        if resultats[chemin] is None:
            print(f"{nom:<38} illisible -> A_Valider")
            decisions["A_Valider"] += 1
            continue
        r, duree = resultats[chemin]
        mots = r.verdict_regles + (f" ({r.categorie_regles})" if r.categorie_regles else "")
        if not r.moteur_appele:
            reponse = "(non appele)"
        else:
            reponse = r.categorie_moteur or "erreur"
        proposee = masquer_texte(r.categorie_proposee)[:18] if r.categorie_proposee else "-"
        if r.necessite_validation_humaine:
            decision = "A_Valider"
            if r.signaux.confiance_ocr_moyenne is not None and r.signaux.confiance_ocr_moyenne < 0.80:
                decision += " (OCR < 0,80)"
        else:
            decision = f"range : {r.categorie}" + (f"/{r.sous_dossier}" if r.sous_dossier else "")
        decisions["A_Valider" if r.necessite_validation_humaine else "range"] += 1
        print(f"{nom:<38} {mots:<25} {reponse:<13} {proposee:<18} {r.confiance:>5.2f}  "
              f"{decision:<30} {duree:>5.1f}s")

    print("=" * 60)
    print(f"Ranges automatiquement      : {decisions['range']}")
    print(f"Envoyes en A_Valider        : {decisions['A_Valider']}")
    print(f"Temps extraction + OCR      : {t_ocr:.1f} s (pic RAM OCR {lot_ocr.pic_ram_mo:.0f} Mo)")
    print(f"Chargement du modele        : {t_chargement:.1f} s")
    print(f"Temps total moteur (lot)    : {t_llm:.1f} s")
    print(f"Modele decharge a la fin    : {'oui' if decharge else 'NON'}")


if __name__ == "__main__":
    main()
