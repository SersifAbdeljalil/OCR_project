"""
inventaire_docs_test.py - Inventaire du jeu de test (tests/docs_test/).

Pour chaque fichier : nom, extension, taille, et la facon dont il sera lu.
Pour les PDF : nombre de pages, natif (couche texte) ou scan (OCR necessaire).

CONFIDENTIALITE : le script lit les PDF en interne pour COMPTER des caracteres,
mais n'affiche JAMAIS de texte extrait. Seuls des noms de fichiers et des chiffres
sortent a l'ecran.

Lancement (venv active, depuis OCR_PROJECT) :
    python tests/inventaire_docs_test.py
"""

# --- Imports ---------------------------------------------------------------
import sys                        # reglage de l'affichage console
from collections import Counter   # compter les fichiers par format
from pathlib import Path          # manipuler les chemins proprement

import pymupdf                    # lecture des PDF (nom moderne de "fitz")

# --- Reglages --------------------------------------------------------------
DOSSIER = Path(__file__).parent / "docs_test"   # tests/docs_test/, ou que l'on lance
SEUIL_CARACTERES_PAGE = 50   # en dessous, la page est consideree sans couche texte

# Categorie de lecture prevue pour chaque extension (hors PDF, traite a part)
LECTURE_PAR_EXTENSION = {
    ".jpg": "image : OCR", ".jpeg": "image : OCR", ".png": "image : OCR",
    ".tif": "image : OCR", ".tiff": "image : OCR", ".bmp": "image : OCR",
    ".webp": "image : OCR",
    ".docx": "python-docx", ".dotx": "python-docx",
    ".xls": "xlrd",
    ".xlsx": "openpyxl",
}
INCONNU = "inconnu : A_Valider"

# Les noms de fichiers peuvent contenir des caracteres speciaux : on force
# l'UTF-8 pour que la console Windows ne plante pas a l'affichage.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# --- 1. Analyse d'un PDF ---------------------------------------------------
def analyser_pdf(chemin: Path) -> dict:
    """Compte les pages et les pages sans texte. Ne renvoie QUE des chiffres."""
    try:
        with pymupdf.open(chemin) as doc:
            if doc.needs_pass:                      # PDF protege par mot de passe
                return {"pages": 0, "pages_scan": 0, "lecture": "protege : A_Valider"}
            pages = doc.page_count
            pages_scan = 0
            for page in doc:
                # On compte les caracteres visibles (espaces exclus). Le texte
                # lui-meme n'est ni garde ni affiche : seule la longueur compte.
                nb = sum(1 for c in page.get_text() if not c.isspace())
                if nb < SEUIL_CARACTERES_PAGE:
                    pages_scan += 1
    except Exception as err:                        # fichier corrompu, etc.
        # On affiche seulement le TYPE d'erreur, jamais son message detaille.
        return {"pages": 0, "pages_scan": 0,
                "lecture": f"illisible ({type(err).__name__}) : A_Valider"}

    if pages_scan == 0:
        lecture = "natif"
    elif pages_scan == pages:
        lecture = "scan : OCR necessaire"
    else:                                           # certaines pages seulement
        lecture = f"mixte : OCR sur {pages_scan} page(s)"
    return {"pages": pages, "pages_scan": pages_scan, "lecture": lecture}


# --- 2. Analyse d'un fichier quelconque ------------------------------------
def analyser(chemin: Path) -> dict:
    """Renvoie les infos d'inventaire d'un fichier (aucun contenu)."""
    ext = chemin.suffix.lower()
    infos = {"nom": str(chemin.relative_to(DOSSIER)), "ext": ext or "(aucune)",
             "ko": chemin.stat().st_size / 1024, "pages": None, "pages_ocr": 0}
    if ext == ".pdf":
        pdf = analyser_pdf(chemin)
        infos.update(pages=pdf["pages"], pages_ocr=pdf["pages_scan"],
                     lecture=pdf["lecture"])
    else:
        infos["lecture"] = LECTURE_PAR_EXTENSION.get(ext, INCONNU)
        if infos["lecture"] == "image : OCR":
            infos["pages_ocr"] = 1                  # une image = une page a lire en OCR
    return infos


# --- 3. Execution et bilan -------------------------------------------------
def main():
    if not DOSSIER.is_dir():
        sys.exit(f"[ERREUR] Dossier introuvable : {DOSSIER}")

    fichiers = sorted(p for p in DOSSIER.rglob("*") if p.is_file())
    if not fichiers:
        sys.exit("Aucun fichier dans tests/docs_test/.")

    print(f"{'Fichier':<45} {'Ext':<7} {'Ko':>8} {'Pages':>6}  Lecture prevue")
    print("-" * 95)
    resultats = []
    for chemin in fichiers:
        r = analyser(chemin)
        resultats.append(r)
        pages = "" if r["pages"] is None else r["pages"]
        print(f"{r['nom']:<45} {r['ext']:<7} {r['ko']:>8.1f} {pages:>6}  {r['lecture']}")

    # Bilan : uniquement des comptes
    par_format = Counter(r["ext"] for r in resultats)
    pdfs = [r for r in resultats if r["ext"] == ".pdf"]
    print("\n" + "=" * 60)
    print(f"Fichiers au total           : {len(resultats)}")
    for ext, nb in sorted(par_format.items()):
        print(f"  {ext:<10}                : {nb}")
    print(f"PDF natifs                  : {sum(r['lecture'] == 'natif' for r in pdfs)}")
    print(f"PDF scans                   : "
          f"{sum(r['lecture'] == 'scan : OCR necessaire' for r in pdfs)}")
    print(f"PDF mixtes                  : "
          f"{sum(r['lecture'].startswith('mixte') for r in pdfs)}")
    print(f"PDF illisibles / proteges   : "
          f"{sum(r['lecture'].endswith('A_Valider') for r in pdfs)}")
    print(f"Fichiers inconnus (A_Valider): "
          f"{sum(r['lecture'] == INCONNU for r in resultats)}")
    print(f"Pages a passer en OCR       : {sum(r['pages_ocr'] for r in resultats)}")


if __name__ == "__main__":
    main()
