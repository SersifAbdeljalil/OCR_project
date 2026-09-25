"""
ocr_worker.py - OCR des pages "OCR requis", avec PaddleOCR 2.10, dans un SOUS-PROCESS.

Deux cotes dans ce fichier :

    PARENT (appele par le pipeline) : lancer_ocr(taches)
        - ecrit la liste des pages dans data/ocr/<lot>/ ;
        - lance le WORKER dans un process separe (python -m src.ocr_worker ...) :
          a la fin, Windows recupere TOUTE la RAM de PaddleOCR ;
        - surveille l'avancement page par page : une page qui depasse le delai
          (60 s) ou qui fait planter le worker est notee en erreur, et le worker
          est relance pour les pages suivantes. Le lot ne plante jamais.

    WORKER (le sous-process) : charge le modele UNE fois (~4 s), puis pour chaque page :
        - PDF : rendu a 200 dpi avec PyMuPDF ; image : lue directement ;
        - PaddleOCR en francais, CPU, 2 threads, use_angle_cls=True (scans a l'envers) ;
        - ecrit une ligne JSON par page : texte, confiance et position (cadre) de
          chaque ligne reconnue.

CONFIDENTIALITE : les resultats intermediaires (texte reconnu) restent dans data/,
jamais affiches. Les alertes ne contiennent que des numeros de page et des types
d'erreur.

fusionner_texte() replace ensuite le texte OCR entre les pages natives, dans l'ordre.
"""

# --- Imports (communs) -----------------------------------------------------
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.config import DOSSIER_DATA, RACINE, SEUIL_RAM_WORKER_OCR_MO

# --- Reglages --------------------------------------------------------------
DPI = 200                    # rendu des pages PDF
MAX_COTE_PX = 2500           # plus grand cote de l'image envoyee a PaddleOCR (RAM)
THREADS = 2                  # 2 coeurs sur l'i3-1005G1
LANGUE = "fr"                # modele "latin" de PaddleOCR
DELAI_PAGE_S = 60            # au-dela, la page est abandonnee
DELAI_CHARGEMENT_S = 180     # chargement du modele (le 1er lancement le telecharge)
ATTENTE_S = 0.2              # frequence de surveillance du worker
STATUT_OK, STATUT_ERREUR = "ok", "erreur"


# ===========================================================================
# 1. Structures de donnees
# ===========================================================================
@dataclass
class LigneOCR:
    texte: str
    confiance: float          # 0 a 1 : servira a la regle de validation des champs
    cadre: list               # 4 points [[x, y], ...] en pixels de l'image analysee


@dataclass
class PageOCR:
    fichier: str
    page: int                 # numero a partir de 1
    statut: str               # "ok" ou "erreur"
    lignes: list = field(default_factory=list)
    duree_s: float = 0.0
    largeur: int = 0          # taille de l'image analysee (pour placer les cadres)
    hauteur: int = 0
    dpi: int = None           # 200 pour un PDF ; None pour une image lue telle quelle
    raison: str = None        # si erreur : type de probleme (jamais de texte)
    pic_ram_mo: float = 0.0   # pic de RAM du worker au moment de cette page
    orientation: int = None   # 0 ou 180 (page a l'envers) ; None si inconnue
    reduction: float = 1.0    # image reduite pour respecter MAX_COTE_PX (1 = taille d'origine)

    @property
    def texte(self) -> str:
        return "\n".join(l.texte for l in self.lignes)

    @property
    def confiance_moyenne(self):
        return (sum(l.confiance for l in self.lignes) / len(self.lignes)
                if self.lignes else None)


@dataclass
class ResultatLot:
    pages: dict = field(default_factory=dict)      # (fichier, page) -> PageOCR
    chargement_s: list = field(default_factory=list)   # un temps par lancement du worker
    pic_ram_mo: float = 0.0
    recyclages: int = 0                             # relances pour RAM trop haute
    dossier: Path = None                            # data/ocr/<lot>/
    alertes: list = field(default_factory=list)

    def pages_du_fichier(self, fichier) -> dict:
        """{numero de page: PageOCR} pour un fichier."""
        cle = str(Path(fichier))
        return {p: r for (f, p), r in self.pages.items() if f == cle}


def _page_depuis_json(d: dict) -> PageOCR:
    lignes = [LigneOCR(**l) for l in d.pop("lignes", [])]
    return PageOCR(lignes=lignes, **d)


# ===========================================================================
# 2. Cote PARENT
# ===========================================================================
def taches_depuis_extraction(chemin, extraction) -> list:
    """Pages a OCR d'un fichier, d'apres le resultat de extract_text.extraire()."""
    if extraction.illisible:
        return []
    return [{"fichier": str(Path(chemin)), "page": n} for n in extraction.pages_ocr]


def _lire_nouvelles_lignes(chemin: Path, deja_lues: int) -> list:
    """Lignes JSON COMPLETES (terminees par un saut de ligne) non encore lues."""
    if not chemin.exists():
        return []
    contenu = chemin.read_text(encoding="utf-8")
    completes = contenu.split("\n")[:-1]          # la derniere peut etre en cours d'ecriture
    return [json.loads(l) for l in completes[deja_lues:] if l.strip()]


def _lancer_worker(taches: list, dossier: Path, numero: int, threads: int,
                   seuil_ram_mo: float):
    """Ecrit les taches et lance le sous-process. Renvoie (process, fichier resultats)."""
    fichier_taches = dossier / f"taches_{numero}.json"
    fichier_resultats = dossier / f"resultats_{numero}.jsonl"
    fichier_taches.write_text(json.dumps({"pages": taches}, ensure_ascii=False),
                              encoding="utf-8")
    env = dict(os.environ, OMP_NUM_THREADS=str(threads))
    journal = open(dossier / f"worker_{numero}.log", "w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "src.ocr_worker", str(fichier_taches),
         str(fichier_resultats), str(threads), str(seuil_ram_mo)],
        cwd=RACINE, env=env, stdout=journal, stderr=subprocess.STDOUT)
    process.journal = journal                      # ferme a la fin
    return process, fichier_resultats


def lancer_ocr(taches: list, dossier_travail: Path = None,
               delai_page_s: float = DELAI_PAGE_S,
               delai_chargement_s: float = DELAI_CHARGEMENT_S,
               threads: int = THREADS,
               seuil_ram_mo: float = SEUIL_RAM_WORKER_OCR_MO) -> ResultatLot:
    """OCR d'un lot de pages [{"fichier", "page"}] dans un sous-process.
    Ne leve pas d'exception pour une page : elle est notee en erreur.
    Le worker est relance des que sa RAM depasse seuil_ram_mo (apres une page)."""
    lot = ResultatLot()
    if not taches:
        return lot
    horodatage = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dossier = Path(dossier_travail or DOSSIER_DATA / "ocr") / horodatage
    dossier.mkdir(parents=True, exist_ok=True)
    lot.dossier = dossier

    restantes, numero = list(taches), 0
    while restantes:
        numero += 1
        process, fichier_resultats = _lancer_worker(restantes, dossier, numero, threads,
                                                    seuil_ram_mo)
        lues, pret, recycle, cause = 0, False, False, None
        lignes_lues = 0                              # toutes les lignes JSON deja traitees
        dernier_signe = time.monotonic()

        def traiter(nouvelles):
            """Met a jour le lot avec les nouvelles lignes du worker."""
            nonlocal lues, pret, recycle, lignes_lues
            for d in nouvelles:
                lignes_lues += 1
                if d.get("pret"):
                    pret = True
                    lot.chargement_s.append(d["chargement_s"])
                elif d.get("recycler"):
                    recycle = True                   # RAM trop haute : arret volontaire
                    lot.recyclages += 1
                else:
                    page = _page_depuis_json(d)
                    lot.pages[(page.fichier, page.page)] = page
                    lot.pic_ram_mo = max(lot.pic_ram_mo, page.pic_ram_mo)
                    lues += 1
            return bool(nouvelles)

        try:
            while True:
                if traiter(_lire_nouvelles_lignes(fichier_resultats, lignes_lues)):
                    dernier_signe = time.monotonic()
                if process.poll() is not None:
                    # Derniere lecture : le worker a pu ecrire juste avant de finir
                    traiter(_lire_nouvelles_lignes(fichier_resultats, lignes_lues))
                    if lues < len(restantes) and not recycle:
                        cause = f"worker arrete (code {process.returncode})"
                    break
                delai = delai_page_s if pret else delai_chargement_s
                if time.monotonic() - dernier_signe > delai:
                    process.kill()
                    process.wait()
                    cause = (f"delai depasse ({delai:g} s)" if pret
                             else f"chargement OCR trop long ({delai:g} s)")
                    break
                time.sleep(ATTENTE_S)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.journal.close()

        if lues >= len(restantes):
            break
        if recycle and cause is None:
            # Arret volontaire pour liberer la RAM : aucune page perdue, on relance
            restantes = restantes[lues:]
            continue
        if not pret:
            # Le modele n'a meme pas pu se charger : inutile d'insister
            for t in restantes[lues:]:
                lot.pages[(t["fichier"], t["page"])] = PageOCR(
                    fichier=t["fichier"], page=t["page"], statut=STATUT_ERREUR,
                    raison=cause)
            lot.alertes.append(f"OCR impossible : {cause}")
            break
        # La page en cours est abandonnee ; on relance pour les suivantes
        t = restantes[lues]
        lot.pages[(t["fichier"], t["page"])] = PageOCR(
            fichier=t["fichier"], page=t["page"], statut=STATUT_ERREUR, raison=cause)
        lot.alertes.append(f"page {t['page']} abandonnee : {cause}")
        restantes = restantes[lues + 1:]
    return lot


def fusionner_texte(extraction, pages_ocr: dict):
    """Texte complet du document : texte natif et texte OCR, dans l'ordre des pages.
    pages_ocr : {numero de page: PageOCR}. Renvoie (texte, alertes)."""
    from src.extract_text import SEPARATEUR_PAGES, STATUT_TEXTE
    if not extraction.textes_pages:                 # Word, Excel : pas de pages
        return extraction.texte, []
    morceaux, alertes = [], []
    for numero, (natif, statut) in enumerate(
            zip(extraction.textes_pages, extraction.statut_pages), start=1):
        if statut == STATUT_TEXTE:
            morceaux.append(natif)
            continue
        page = pages_ocr.get(numero)
        if page is None or page.statut != STATUT_OK:
            raison = page.raison if page else "page non traitee"
            alertes.append(f"page {numero} : OCR indisponible ({raison})")
            continue
        morceaux.append(page.texte)
    return SEPARATEUR_PAGES.join(m for m in morceaux if m.strip()), alertes


# ===========================================================================
# 3. Cote WORKER (execute dans le sous-process)
# ===========================================================================
def pic_ram_mo() -> float:
    """Pic de RAM (working set) du process courant, en Mo (API Windows)."""
    return _memoire_mo()[1]


def ram_actuelle_mo() -> float:
    """RAM (working set) utilisee MAINTENANT par le process courant, en Mo."""
    return _memoire_mo()[0]


def _memoire_mo():
    """(RAM actuelle, pic de RAM) du process courant, en Mo (API Windows)."""
    import ctypes
    import ctypes.wintypes as wt

    class Compteurs(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.GetCurrentProcess.restype = wt.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(Compteurs), wt.DWORD]
    c = Compteurs()
    c.cb = ctypes.sizeof(c)
    psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return round(c.WorkingSetSize / 2**20, 1), round(c.PeakWorkingSetSize / 2**20, 1)


def charger_page(fichier: str, page: int, max_cote: int = MAX_COTE_PX):
    """Image d'une page, au format attendu par PaddleOCR (tableau BGR).
    PDF : rendu a 200 dpi, sauf si le plus grand cote depasse max_cote (grand
    format) : le dpi est alors baisse. Image simple : lue directement puis, si
    besoin, reduite proportionnellement a max_cote.
    Renvoie (image, largeur, hauteur, dpi, reduction).
    Passer par PyMuPDF evite aussi le bug d'OpenCV avec les chemins accentues."""
    import cv2
    import numpy as np
    import pymupdf
    with pymupdf.open(fichier) as doc:
        if not 1 <= page <= doc.page_count:
            raise IndexError("numero de page hors du document")
        if doc.is_pdf or doc.page_count > 1:        # PDF, ou TIFF multi-pages
            p = doc[page - 1]
            echelle = min(DPI / 72, max_cote / max(p.rect.width, p.rect.height))
            pix = p.get_pixmap(matrix=pymupdf.Matrix(echelle, echelle))
            dpi = round(echelle * 72)
        else:
            pix, dpi = pymupdf.Pixmap(fichier), None
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)                 # retire la transparence
    if pix.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)     # gris / CMJN -> RGB
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    bgr = rgb[:, :, ::-1].copy()                     # RGB -> BGR

    # Reduction proportionnelle si l'image depasse encore max_cote (grande photo)
    reduction = 1.0
    hauteur, largeur = bgr.shape[:2]
    if max(largeur, hauteur) > max_cote:
        reduction = max_cote / max(largeur, hauteur)
        largeur, hauteur = round(largeur * reduction), round(hauteur * reduction)
        bgr = cv2.resize(bgr, (largeur, hauteur), interpolation=cv2.INTER_AREA)
    return bgr, largeur, hauteur, dpi, round(reduction, 4)


class _EspionAngles:
    """Enveloppe le classifieur d'angle de PaddleOCR (attribut interne
    `text_classifier` de la version 2.10) pour recuperer l'angle de chaque ligne,
    que PaddleOCR ne renvoie pas.
    Pourquoi : sur une page A L'ENVERS, chaque ligne est bien retournee et lue, mais
    les lignes sortent dans l'ordre inverse (tri de haut en bas sur l'image retournee)."""

    def __init__(self, classifieur):
        self.classifieur, self.angles = classifieur, []

    def __call__(self, images_lignes):
        images_lignes, angles, duree = self.classifieur(images_lignes)
        self.angles = angles                       # [["0" ou "180", score], ...]
        return images_lignes, angles, duree


def main_worker(fichier_taches: str, fichier_resultats: str, threads: int,
                seuil_ram_mo: float = SEUIL_RAM_WORKER_OCR_MO) -> None:
    """Charge PaddleOCR une fois, puis traite les pages une par une. Chaque page
    produit une ligne JSON dans fichier_resultats (ecrite des qu'elle est finie).
    Apres chaque page, si la RAM du process depasse seuil_ram_mo et qu'il reste des
    pages, le worker ecrit {"recycler": true} et s'arrete : le parent le relance
    (PaddleOCR accumule de la memoire au fil des pages)."""
    taches = json.loads(Path(fichier_taches).read_text(encoding="utf-8"))["pages"]
    with open(fichier_resultats, "a", encoding="utf-8") as sortie:
        def ecrire(objet):
            sortie.write(json.dumps(objet, ensure_ascii=False) + "\n")
            sortie.flush()

        t0 = time.perf_counter()
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(lang=LANGUE, use_angle_cls=True, use_gpu=False,
                        cpu_threads=threads, show_log=False)
        espion = None
        if hasattr(ocr, "text_classifier"):          # sinon : orientation inconnue
            espion = ocr.text_classifier = _EspionAngles(ocr.text_classifier)
        ecrire({"pret": True, "chargement_s": round(time.perf_counter() - t0, 2)})

        for i, t in enumerate(taches):
            if i > 0 and ram_actuelle_mo() > seuil_ram_mo:
                ecrire({"recycler": True, "ram_mo": ram_actuelle_mo()})
                return                              # le parent relance pour la suite
            debut = time.perf_counter()
            base = {"fichier": t["fichier"], "page": t["page"]}
            try:
                image, largeur, hauteur, dpi, reduction = charger_page(t["fichier"], t["page"])
                if espion:
                    espion.angles = []
                resultat = ocr.ocr(image, cls=True)
                lignes = [{"texte": texte, "confiance": round(float(conf), 4),
                           "cadre": [[round(float(x)), round(float(y))] for x, y in cadre]}
                          for page in (resultat or []) if page
                          for cadre, (texte, conf) in page]
                # Page a l'envers : la majorite des lignes a ete retournee a 180 degres.
                # On remet les lignes dans l'ordre de lecture (les cadres restent
                # ceux de l'image d'origine).
                orientation = None
                if espion is not None:
                    retournees = sum(1 for angle, _score in espion.angles if angle == "180")
                    orientation = 180 if retournees * 2 > len(espion.angles) else 0
                    if orientation == 180:
                        lignes.reverse()
                ecrire({**base, "statut": STATUT_OK, "lignes": lignes,
                        "duree_s": round(time.perf_counter() - debut, 2),
                        "largeur": largeur, "hauteur": hauteur, "dpi": dpi,
                        "pic_ram_mo": pic_ram_mo(), "orientation": orientation,
                        "reduction": reduction})
            except Exception as err:
                ecrire({**base, "statut": STATUT_ERREUR, "raison": type(err).__name__,
                        "duree_s": round(time.perf_counter() - debut, 2),
                        "pic_ram_mo": pic_ram_mo()})


if __name__ == "__main__":
    main_worker(sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]))
