"""
essai_ocr.py - Test de faisabilite de PaddleOCR 2.x sur cette machine (CPU, 2 threads).

Le script :
    1. genere une image INVENTEE (facture fictive rendue par PyMuPDF a 200 dpi) ;
    2. charge PaddleOCR en francais, sur CPU, 2 threads ;
    3. affiche : temps de chargement, temps d'OCR, pic de RAM du process, nombre de
       lignes, confiance moyenne, texte reconnu (c'est de l'invente : on peut l'afficher)
       et un taux de ressemblance avec le texte attendu ;
    4. indique ou sont stockes les modeles et leur taille.

AVANT de lancer : Ollama ne doit pas avoir de modele charge (ollama ps).
Lancement (venv active, depuis OCR_PROJECT) :  python tests/essai_ocr.py
"""

import os

# A regler AVANT d'importer paddle : nombre de threads de calcul (2 coeurs sur l'i3)
os.environ["OMP_NUM_THREADS"] = "2"

import ctypes                      # lire le pic de RAM via l'API Windows
import ctypes.wintypes
import difflib                     # comparer le texte reconnu au texte attendu
import sys
import tempfile
import time
from pathlib import Path

import pymupdf

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- Texte INVENTE de la facture fictive -----------------------------------
LIGNES_ATTENDUES = [
    "FACTURE N° FA-2026-00042",
    "Date : 15/09/2026",
    "Fournisseur : Société Exemple SARL, Casablanca",
    "Client : Cabinet Comptable Fictif",
    "Désignation : Maintenance informatique septembre 2026",
    "Montant HT : 1 000,00 DH",
    "TVA 20 % : 200,00 DH",
    "Total TTC : 1 200,00 DH",
    "Arrêtée la présente facture à la somme de mille deux cents dirhams.",
]
DPI = 200


# --- 1. Pic de RAM du process (Windows, sans bibliotheque en plus) ----------
class _CompteursMemoire(ctypes.Structure):
    _fields_ = [("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


# Types des fonctions Windows declares explicitement : sans cela, ctypes tronque
# le "pseudo-handle" du process (valeur -1 sur 64 bits) et l'appel echoue.
_GetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
_GetCurrentProcess.restype = ctypes.wintypes.HANDLE
_GetProcessMemoryInfo = ctypes.windll.psapi.GetProcessMemoryInfo
_GetProcessMemoryInfo.argtypes = [ctypes.wintypes.HANDLE,
                                  ctypes.POINTER(_CompteursMemoire), ctypes.wintypes.DWORD]
_GetProcessMemoryInfo.restype = ctypes.wintypes.BOOL


def ram_mo():
    """(RAM actuelle, pic de RAM) du process en Mo."""
    c = _CompteursMemoire()
    c.cb = ctypes.sizeof(c)
    if not _GetProcessMemoryInfo(_GetCurrentProcess(), ctypes.byref(c), c.cb):
        raise OSError("lecture de la memoire du process impossible")
    return c.WorkingSetSize / 2**20, c.PeakWorkingSetSize / 2**20


# --- 2. Image inventee -----------------------------------------------------
def generer_image(dossier: Path) -> Path:
    """Page A4 avec le texte fictif, rendue en PNG a 200 dpi (comme un scan)."""
    doc = pymupdf.open()
    page = doc.new_page()                                  # A4 par defaut
    for i, ligne in enumerate(LIGNES_ATTENDUES):
        taille = 16 if i == 0 else 11
        page.insert_text((60, 80 + i * 28), ligne, fontsize=taille)
    chemin = dossier / "facture_fictive.png"
    page.get_pixmap(dpi=DPI).save(chemin)
    doc.close()
    return chemin


# --- 3. Taille des modeles -------------------------------------------------
def taille_dossier_mo(dossier: Path) -> float:
    return sum(p.stat().st_size for p in dossier.rglob("*") if p.is_file()) / 2**20


def main():
    ram0, _ = ram_mo()
    print(f"RAM au depart                : {ram0:.0f} Mo")

    with tempfile.TemporaryDirectory() as tmp:
        image = generer_image(Path(tmp))
        largeur, hauteur = pymupdf.Pixmap(str(image)).width, pymupdf.Pixmap(str(image)).height
        print(f"Image inventee               : {largeur} x {hauteur} px ({DPI} dpi)")

        # Chargement (import + modeles ; le 1er lancement telecharge les modeles)
        t0 = time.perf_counter()
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(lang="fr", use_angle_cls=True, use_gpu=False,
                        cpu_threads=2, show_log=False)
        t_chargement = time.perf_counter() - t0
        ram1, _ = ram_mo()

        # OCR
        t0 = time.perf_counter()
        resultat = ocr.ocr(str(image), cls=True)
        t_ocr = time.perf_counter() - t0
        ram2, pic = ram_mo()

    # resultat : une liste par page ; chaque ligne = [cadre, (texte, confiance)]
    lignes = [(texte, conf) for page in resultat if page for _cadre, (texte, conf) in page]
    confiance_moy = sum(c for _, c in lignes) / len(lignes) if lignes else 0.0
    texte_reconnu = "\n".join(t for t, _ in lignes)
    ressemblance = difflib.SequenceMatcher(
        None, "\n".join(LIGNES_ATTENDUES), texte_reconnu).ratio()

    print(f"Temps de chargement          : {t_chargement:.1f} s")
    print(f"Temps d'OCR (1 page)         : {t_ocr:.1f} s")
    print(f"RAM apres chargement / OCR   : {ram1:.0f} Mo / {ram2:.0f} Mo")
    print(f"Pic de RAM du process        : {pic:.0f} Mo")
    print(f"Lignes reconnues             : {len(lignes)} (attendues : {len(LIGNES_ATTENDUES)})")
    print(f"Confiance moyenne            : {confiance_moy:.3f}")
    print(f"Ressemblance avec l'attendu  : {ressemblance:.1%}")

    print("\n--- Texte reconnu (INVENTE) : confiance | texte ---")
    for texte, conf in lignes:
        print(f"  {conf:.3f} | {texte}")

    dossier_modeles = Path.home() / ".paddleocr"
    if dossier_modeles.is_dir():
        print(f"\nModeles : {dossier_modeles}  ({taille_dossier_mo(dossier_modeles):.1f} Mo)")
        for sous in sorted(p for p in dossier_modeles.rglob("*") if p.is_dir()
                           and any(f.is_file() for f in p.iterdir())):
            print(f"  {sous.relative_to(dossier_modeles)}  ({taille_dossier_mo(sous):.1f} Mo)")


if __name__ == "__main__":
    main()
