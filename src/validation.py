"""
validation.py - Logique de la validation humaine (sans interface).

Sert a l'ecran Streamlit (etape b12b), pour TOUS les documents traites (ranges ou
A_Valider) :
    - lister_documents()      : documents de Folder_Sortie (categorie, statut, raison, alertes) ;
    - charger_document()      : champs, texte, original, lignes OCR avec confiance ;
    - enregistrer_corrections(): renormalisation (normalize.py), renommage si un champ du
      nom change (doublons geres), deplacement si la categorie change, historique des
      corrections dans le .json, necessite_validation_humaine -> false ;
    - proposer_mots_cles() / creer_categorie() : nouvelle categorie (nom confirme par
      l'humain, 3 a 5 mots-cles proposes par le LLM puis confirmes), verifiee par
      config.py avant d'etre ecrite dans config/categories.json ;
    - rejeter_document()      : le document part dans Autres/.
Chaque action est notee dans data/logs/validation_<date>.jsonl (noms MASQUES, jamais
de valeur).

Les fichiers de sortie, eux, ne sont jamais masques : l'historique contient les
anciennes et nouvelles valeurs.
"""

# --- Imports ---------------------------------------------------------------
import copy
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.config import (CHEMIN_REGISTRE, DOSSIER_A_VALIDER, DOSSIER_AUTRES,
                        DOSSIER_DATA, DOSSIER_SORTIE, ErreurRegistre, champs_attendus,
                        choisir_sous_dossier, charger_registre, normaliser,
                        registre_par_defaut, trouver_categorie, verifier_registre)
from src.extractor import charger_champs_libres, charger_config
from src.filer import _preparer_base, construire_base
from src.masking import nom_affichable
from src.normalize import normaliser_date, normaliser_montant, verifier_totaux
from src.schemas import DocumentSortie

EXTENSIONS_PRODUITES = {".json", ".txt"}
CLES_A_VALIDER = ("raison", "alertes", "categorie_proposee")   # retirees apres validation


class ErreurValidation(ValueError):
    """Action impossible ; le message ne contient que des noms de champs."""


# --- 1. Journal ------------------------------------------------------------
def journaliser(action: str, dossier_logs: Path = None, **infos) -> None:
    """data/logs/validation_<AAAAMMJJ>.jsonl : une ligne par action (noms masques)."""
    dossier = Path(dossier_logs or DOSSIER_DATA / "logs")
    dossier.mkdir(parents=True, exist_ok=True)
    ligne = {"horodatage": datetime.now().isoformat(timespec="seconds"),
             "action": action, **infos}
    with open(dossier / f"validation_{datetime.now():%Y%m%d}.jsonl", "a",
              encoding="utf-8") as f:
        f.write(json.dumps(ligne, ensure_ascii=False) + "\n")


# --- 2. Lister et charger ------------------------------------------------------
@dataclass
class FicheDocument:
    chemin_json: Path
    dossier: str                     # relatif a Folder_Sortie (ex. "Factures", "A_Valider")
    categorie: str
    statut: str                      # "a_valider", "a_verifier", "range", "valide", "autres"
    raison: str = None
    alertes: list = field(default_factory=list)
    confiance: float = None
    source: str = field(default=None, repr=False)     # nom d'origine (peut contenir un nom)


def _statut(dossier: str, info: dict) -> str:
    racine = dossier.split("/")[0]
    if racine == DOSSIER_A_VALIDER:
        return "a_valider"
    if racine == DOSSIER_AUTRES:
        return "autres"
    if info.get("historique_corrections"):
        return "valide"
    return "a_verifier" if info.get("necessite_validation_humaine") else "range"


def lister_documents(sortie: Path = DOSSIER_SORTIE) -> list:
    """Tous les documents traites (un .json par document), A_Valider compris."""
    sortie = Path(sortie)
    fiches = []
    for chemin in sorted(sortie.rglob("*.json")):
        try:
            info = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(info, dict) or "champs" not in info:
            continue                                     # pas un document de sortie
        dossier = chemin.parent.relative_to(sortie).as_posix()
        fiches.append(FicheDocument(chemin, dossier, info.get("type"), _statut(dossier, info),
                                    info.get("raison"), list(info.get("alertes") or []),
                                    info.get("confiance_classification"), info.get("source")))
    return fiches


@dataclass
class DocumentCharge:
    chemin_json: Path
    info: dict = field(repr=False)                   # tout le .json
    champs: dict = field(repr=False)
    texte: str = field(repr=False)
    original: Path = None                            # copie de l'original (image source)
    pages_ocr: list = field(default_factory=list, repr=False)   # lignes OCR + confiance + cadre
    historique: list = field(default_factory=list, repr=False)


def _fichiers_du_document(chemin_json: Path) -> list:
    """Le .json, le .txt et la copie de l'original partagent la meme base."""
    base = chemin_json.stem
    return [p for p in chemin_json.parent.iterdir()
            if p.is_file() and (p.stem == base or p.stem == f"{base}_original")]


def _original(chemin_json: Path):
    for p in _fichiers_du_document(chemin_json):
        if p.suffix.lower() not in EXTENSIONS_PRODUITES or p.stem.endswith("_original"):
            return p
    return None


def charger_document(chemin_json) -> DocumentCharge:
    chemin_json = Path(chemin_json)
    info = json.loads(chemin_json.read_text(encoding="utf-8"))
    txt = chemin_json.with_suffix(".txt")
    texte = txt.read_text(encoding="utf-8") if txt.exists() else info.get("texte_brut", "")
    return DocumentCharge(chemin_json, info, dict(info.get("champs") or {}), texte,
                          _original(chemin_json), list(info.get("pages") or []),
                          list(info.get("historique_corrections") or []))


# --- 3. Normalisation d'une valeur saisie par l'humain -----------------------------
def normaliser_valeur(champ: str, valeur, config: dict = None, libres: list = None):
    """Valeur saisie -> valeur normalisee (date ISO, Decimal, chiffres...).
    Chaine vide -> None (champ vide). Leve ErreurValidation si la valeur est invalide
    (message : nom du champ seulement)."""
    if valeur is None or (isinstance(valeur, str) and not valeur.strip()):
        return None
    config = config if config is not None else charger_config()
    conf = config.get(champ)
    if conf is None:                                  # champ libre (fournisseur, adresse...)
        return re.sub(r"\s+", " ", str(valeur)).strip()
    t = conf["type"]
    if t == "date":
        iso, _ = normaliser_date(str(valeur))
        if iso is None:
            raise ErreurValidation(f"{champ} : date invalide")
        return iso
    if t == "montant":
        montant, _ = normaliser_montant(valeur if isinstance(valeur, (int, float, Decimal))
                                        else str(valeur))
        if montant is None:
            raise ErreurValidation(f"{champ} : montant invalide")
        return montant
    if t == "chiffres":
        chiffres = re.sub(r"\D", "", str(valeur))
        if len(chiffres) != conf["longueur"]:
            raise ErreurValidation(f"{champ} : {conf['longueur']} chiffres attendus")
        return chiffres
    texte = re.sub(r"\s+", " ", str(valeur)).strip()
    if conf.get("sans_espaces"):
        texte = re.sub(r"\s", "", texte).upper()
    return texte


def _en_decimal_si_montant(champ, valeur, config):
    """Les montants relus du JSON sont des float : Decimal pour les ecrire a 2 decimales."""
    conf = config.get(champ)
    if conf and conf["type"] == "montant" and isinstance(valeur, (int, float)) \
            and not isinstance(valeur, bool):
        return Decimal(str(valeur))
    return valeur


# --- 4. Enregistrer des corrections -------------------------------------------------
@dataclass
class ResultatValidation:
    chemin_json: Path                 # emplacement FINAL du .json
    renomme: bool = False
    deplace: bool = False
    nb_corrections: int = 0
    alertes: list = field(default_factory=list)


def _destination(type_doc: str, texte: str, registre: dict, sortie: Path,
                 sous_dossier: str = None):
    """Dossier de destination. sous_dossier : choisi par l'humain (doit exister dans
    la categorie) ; sinon choisi automatiquement (choisir_sous_dossier)."""
    cat = trouver_categorie(registre, type_doc)
    if cat is None:
        if type_doc != DOSSIER_AUTRES.lower():
            raise ErreurValidation(f"categorie absente du registre : {type_doc} "
                                   "(la creer d'abord)")
        return Path(sortie) / DOSSIER_AUTRES, None
    if sous_dossier:
        noms = [sd["nom"] for sd in cat.get("sous_dossiers", [])]
        if sous_dossier not in noms:
            raise ErreurValidation(f"sous-dossier inconnu pour {type_doc} : {sous_dossier}")
        sous = sous_dossier
    else:
        sous = choisir_sous_dossier(cat, texte)
    dossier = Path(sortie) / cat["dossier"]
    return (dossier / sous if sous else dossier), sous


def sous_dossier_actuel(chemin_json, registre: dict = None):
    """Sous-dossier ou se trouve le document (None s'il est a la racine de sa categorie)."""
    registre = registre or registre_par_defaut()
    chemin_json = Path(chemin_json)
    for cat in registre["categories"]:
        if chemin_json.parent.parent.name == cat["dossier"]:
            if chemin_json.parent.name in [sd["nom"] for sd in cat.get("sous_dossiers", [])]:
                return chemin_json.parent.name
    return None


def enregistrer_corrections(chemin_json, corrections: dict = None, categorie: str = None,
                            registre: dict = None, sortie: Path = DOSSIER_SORTIE,
                            dossier_logs: Path = None, action: str = "validation",
                            sous_dossier: str = None) -> ResultatValidation:
    """Valide un document (avec ou sans corrections). Voir l'en-tete du module.
    Si une valeur est invalide, RIEN n'est modifie (ErreurValidation)."""
    registre = registre or registre_par_defaut()
    config = charger_config()
    libres, _, _ = charger_champs_libres()
    doc = charger_document(chemin_json)
    ancien_json = doc.chemin_json
    info, corrections = doc.info, dict(corrections or {})
    maintenant = datetime.now().isoformat(timespec="seconds")
    historique = list(doc.historique)

    # Categorie : celle demandee, sinon l'actuelle
    type_ancien = info.get("type")
    type_doc = categorie or type_ancien
    if type_doc is None:
        raise ErreurValidation("categorie inconnue : en choisir une")
    dossier_cible, sous_dossier = _destination(type_doc, doc.texte, registre, sortie,
                                               sous_dossier)
    autorises = champs_attendus(registre, type_doc)
    if type_doc != type_ancien:
        historique.append({"champ": "type", "ancienne_valeur": type_ancien,
                           "nouvelle_valeur": type_doc, "date": maintenant})

    inconnus = sorted(set(corrections) - set(autorises))
    if inconnus:
        raise ErreurValidation(f"champs non prevus pour {type_doc} : {inconnus}")

    # Champs : ceux qui existent dans la (nouvelle) categorie, puis corrections
    champs = {}
    for nom in autorises:
        ancienne = doc.champs.get(nom)
        if nom in corrections:
            nouvelle = normaliser_valeur(nom, corrections[nom], config, libres)
        else:
            nouvelle = _en_decimal_si_montant(nom, ancienne, config)
        champs[nom] = nouvelle
        if nom in corrections and _differents(ancienne, nouvelle):
            historique.append({"champ": nom, "ancienne_valeur": _json(ancienne),
                               "nouvelle_valeur": _json(nouvelle), "date": maintenant})
    for nom in set(doc.champs) - set(autorises):       # champs perdus au changement de type
        if doc.champs[nom] is not None:
            historique.append({"champ": nom, "ancienne_valeur": _json(doc.champs[nom]),
                               "nouvelle_valeur": None, "date": maintenant})

    res = ResultatValidation(ancien_json)
    res.nb_corrections = sum(1 for h in historique[len(doc.historique):] if h["champ"] != "type")
    if all(c in autorises for c in ("montant_ht", "tva", "montant_ttc")):
        _, alertes = verifier_totaux(champs.get("montant_ht"), champs.get("tva"),
                                     champs.get("montant_ttc"))
        res.alertes += alertes

    historique.append({"action": action, "alertes_avant": list(info.get("alertes") or []),
                       "date": maintenant})
    nouveau = DocumentSortie.model_validate({
        "type": type_doc, "source": info.get("source") or ancien_json.name,
        "date_traitement": info.get("date_traitement") or maintenant,
        "confiance_classification": info.get("confiance_classification") or 0.0,
        "champs": champs, "necessite_validation_humaine": False,
        "texte_brut": doc.texte}, context={"registre": registre, "valide_par_humain": True})
    supplement = {k: v for k, v in info.items()
                  if k not in DocumentSortie.model_fields and k not in CLES_A_VALIDER
                  and k != "historique_corrections"}
    supplement.update(historique_corrections=historique, valide_le=maintenant)
    contenus = {".txt": doc.texte, ".json": nouveau.vers_json(supplement) + "\n"}

    # Nom et dossier : si rien ne change, reecriture sur place
    base = construire_base(nouveau, registre, sous_dossier)
    meme_place = (dossier_cible.resolve() == ancien_json.parent.resolve()
                  and base == ancien_json.stem)
    if meme_place:
        for suffixe, texte in contenus.items():
            _ecrire_sur(ancien_json.with_suffix(suffixe), texte)
        res.chemin_json = ancien_json
    else:
        res.chemin_json = _deplacer(doc, dossier_cible, base, contenus)
        res.deplace = dossier_cible.resolve() != ancien_json.parent.resolve()
        res.renomme = res.chemin_json.stem != ancien_json.stem

    journaliser(action, dossier_logs, fichier=nom_affichable(res.chemin_json, registre),
                categorie=type_doc, nb_corrections=res.nb_corrections,
                deplace=res.deplace, renomme=res.renomme, nb_alertes=len(res.alertes))
    return res


def _differents(a, b) -> bool:
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
        return Decimal(str(a)) != Decimal(str(b))
    return a != b


def _json(valeur):
    """Valeur pour l'historique (JSON) : Decimal -> texte a 2 decimales."""
    return f"{valeur:.2f}" if isinstance(valeur, Decimal) else valeur


def _ecrire_sur(chemin: Path, texte: str):
    """Ecriture sure : fichier provisoire puis remplacement."""
    provisoire = chemin.with_name(chemin.name + ".tmp")
    provisoire.write_text(texte, encoding="utf-8", newline="\n")
    os.replace(provisoire, chemin)


def _deplacer(doc: DocumentCharge, dossier: Path, base: str, contenus: dict) -> Path:
    """Ecrit les nouveaux .json/.txt, deplace l'original, puis retire les anciens.
    En cas d'erreur avant la fin, les nouveaux fichiers sont retires et l'ancien
    document reste intact."""
    original = doc.original
    ext = original.suffix.lower() if original else ""
    suffixe_orig = f"_original{ext}" if ext in contenus else ext
    dossier.mkdir(parents=True, exist_ok=True)
    base = _preparer_base(base, dossier, original or doc.chemin_json, contenus)
    ecrits = []
    try:
        for suffixe, texte in contenus.items():
            chemin = dossier / f"{base}{suffixe}"
            ecrits.append(chemin)
            _ecrire_sur(chemin, texte)
        if original:
            shutil.move(str(original), str(dossier / f"{base}{suffixe_orig}"))
    except Exception:
        for chemin in ecrits:
            chemin.unlink(missing_ok=True)
        raise
    for suffixe in contenus:                         # anciens .json / .txt
        ancien = doc.chemin_json.with_suffix(suffixe)
        if ancien.exists() and ancien not in ecrits:
            ancien.unlink()
    return dossier / f"{base}.json"


# --- 5. Rejeter -------------------------------------------------------------------------
def rejeter_document(chemin_json, registre: dict = None, sortie: Path = DOSSIER_SORTIE,
                     dossier_logs: Path = None) -> ResultatValidation:
    """Le document part dans Autres/ (type « autres », champs generiques)."""
    return enregistrer_corrections(chemin_json, {}, categorie=DOSSIER_AUTRES.lower(),
                                   registre=registre, sortie=sortie,
                                   dossier_logs=dossier_logs, action="rejet")


# --- 5 bis. Pour l'interface : apercu, depot, tri en arriere-plan ---------------------------
EXTENSIONS_ACCEPTEES = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp",
                        ".docx", ".dotx", ".xls", ".xlsx"}


def apercu_page(original, page_ocr: dict = None, numero: int = 1,
                seuil: float = None, dpi_defaut: int = 100):
    """Image de la page (PIL), avec les lignes OCR de confiance < seuil SURLIGNEES.
    page_ocr : entree de « pages » du .json (largeur, hauteur, dpi, lignes avec cadre) ;
    l'image est remise a la taille analysee par l'OCR pour que les cadres tombent juste.
    Renvoie (image, nombre de lignes surlignees, nombre de pages du fichier)."""
    import io
    import pymupdf
    from PIL import Image, ImageDraw
    from src.config import SEUIL_CONFIANCE_LIGNE_OCR
    seuil = SEUIL_CONFIANCE_LIGNE_OCR if seuil is None else seuil
    with pymupdf.open(str(original)) as doc:
        nb_pages = doc.page_count
        page = doc[max(0, min(numero, nb_pages) - 1)]
        dpi = (page_ocr or {}).get("dpi") or dpi_defaut
        pix = page.get_pixmap(dpi=dpi) if (doc.is_pdf or nb_pages > 1) \
            else pymupdf.Pixmap(str(original))
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    if not page_ocr:
        return image, 0, nb_pages
    taille = (page_ocr.get("largeur"), page_ocr.get("hauteur"))
    if all(taille) and image.size != taille:
        image = image.resize(taille)                 # meme repere que les cadres OCR
    calque = Image.new("RGBA", image.size, (0, 0, 0, 0))
    dessin = ImageDraw.Draw(calque)
    surlignees = 0
    for ligne in page_ocr.get("lignes", []):
        if ligne.get("confiance", 1) < seuil and ligne.get("cadre"):
            points = [tuple(p) for p in ligne["cadre"]]
            dessin.polygon(points, fill=(255, 200, 0, 90), outline=(220, 0, 0, 255), width=3)
            surlignees += 1
    image = Image.alpha_composite(image.convert("RGBA"), calque).convert("RGB")
    return image, surlignees, nb_pages


def deposer_fichiers(fichiers: list, entree: Path) -> list:
    """Copie les fichiers deposes [(nom, contenu en octets)] dans Folder_Entree.
    Nom nettoye (pas de chemin), extension verifiee, jamais d'ecrasement (_1, _2...).
    Renvoie (noms deposes, noms refuses)."""
    entree = Path(entree)
    entree.mkdir(parents=True, exist_ok=True)
    deposes, refuses = [], []
    for nom, contenu in fichiers:
        nom = Path(str(nom)).name                    # jamais de dossier dans le nom
        if Path(nom).suffix.lower() not in EXTENSIONS_ACCEPTEES:
            refuses.append(nom)
            continue
        cible, n = entree / nom, 0
        while cible.exists():
            n += 1
            cible = entree / f"{Path(nom).stem}_{n}{Path(nom).suffix}"
        cible.write_bytes(contenu)
        deposes.append(cible.name)
    return deposes, refuses


def _process_actif(pid: int) -> bool:
    """Le process existe-t-il encore ? (API Windows, sans envoyer de signal)."""
    import ctypes
    import ctypes.wintypes as wt
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wt.HANDLE
    poignee = k32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not poignee:
        return False
    try:
        code = wt.DWORD()
        k32.GetExitCodeProcess(poignee, ctypes.byref(code))
        return code.value == 259                              # STILL_ACTIVE
    finally:
        k32.CloseHandle(poignee)


def etat_tri(dossier_data: Path = DOSSIER_DATA) -> dict:
    """{"en_cours", "progression" (derniere ligne « [i/N] ... »), "resume" (dernier lot)}.
    Un verrou dont le process n'existe plus est retire."""
    dossier_data = Path(dossier_data)
    verrou = dossier_data / "etat" / "tri.lock"
    en_cours = False
    if verrou.exists():
        try:
            pid = json.loads(verrou.read_text(encoding="utf-8"))["pid"]
            en_cours = _process_actif(pid)
        except (OSError, ValueError, KeyError):
            en_cours = False
        if not en_cours:
            verrou.unlink(missing_ok=True)
    progression = None
    sortie = dossier_data / "logs" / "tri_interface.log"
    if sortie.exists():
        lignes = [l for l in sortie.read_text(encoding="utf-8", errors="replace").splitlines()
                  if re.match(r"\s*(\[\d+/\d+\]|OCR)", l) or "document(s) dans" in l]
        progression = lignes[-1].strip() if lignes else None
    resume = None
    journaux = sorted((dossier_data / "logs").glob("pipeline_*.jsonl"))
    if journaux:
        for ligne in reversed(journaux[-1].read_text(encoding="utf-8").splitlines()):
            evt = json.loads(ligne)
            if evt.get("evenement") == "fin":
                resume = {k: evt.get(k) for k in ("total", "ranges", "a_valider", "erreurs",
                                                   "deja_traites", "duree_s", "horodatage")}
                break
    return {"en_cours": en_cours, "progression": progression, "resume": resume}


def lancer_tri(dossier_data: Path = DOSSIER_DATA, commande: list = None):
    """Lance le pipeline dans un SOUS-PROCESS detache (l'interface reste utilisable).
    Refuse si un tri est deja en cours (verrou data/etat/tri.lock).
    Renvoie (lance, message)."""
    import subprocess
    import sys
    from src.config import RACINE
    dossier_data = Path(dossier_data)
    if etat_tri(dossier_data)["en_cours"]:
        return False, "un tri est deja en cours"
    (dossier_data / "etat").mkdir(parents=True, exist_ok=True)
    (dossier_data / "logs").mkdir(parents=True, exist_ok=True)
    commande = commande or [sys.executable, str(RACINE / "run_pipeline.py")]
    sortie = open(dossier_data / "logs" / "tri_interface.log", "w", encoding="utf-8")
    drapeaux = 0x08000000 | 0x00000200            # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(commande, cwd=RACINE, stdout=sortie, stderr=subprocess.STDOUT,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                               creationflags=drapeaux)
    sortie.close()                                   # le sous-process garde sa propre copie
    (dossier_data / "etat" / "tri.lock").write_text(
        json.dumps({"pid": process.pid, "debut": datetime.now().isoformat(timespec="seconds")}),
        encoding="utf-8")
    journaliser("lancement_tri", dossier_data / "logs")
    return True, "tri lance"


# --- 6. Nouvelle categorie ------------------------------------------------------------------
def mot_cle_vers_regex(mot: str) -> str:
    """'Bulletin de paie' -> r'\\bbulletin\\s+de\\s+paie\\b' (minuscules, sans accents ;
    apostrophes et ponctuation -> '.' ; espaces -> \\s+)."""
    t = normaliser(mot)
    t = re.sub(r"[^a-z0-9 ]", ".", t)
    mots = [m for m in t.split() if m.strip(".")]
    if not mots:
        raise ErreurValidation("mot-cle vide")
    return r"\b" + r"\s+".join(mots) + r"\b"


def proposer_mots_cles(nom_categorie: str, texte: str, client) -> list:
    """Le LLM propose 3 a 5 mots-cles (a CONFIRMER par l'humain). Ne leve jamais :
    liste vide si le moteur echoue. Les propositions avec un chiffre sont retirees."""
    schema = {"type": "object", "properties": {"mots_cles": {"type": "array",
                                                             "items": {"type": "string"}}},
              "required": ["mots_cles"]}
    rep = client.appeler("mots_cles", texte, schema,
                         variables={"categorie": nom_categorie.replace("_", " ")})
    if not rep.ok or not isinstance(rep.donnees.get("mots_cles"), list):
        return []
    propositions = []
    for mot in rep.donnees["mots_cles"]:
        mot = re.sub(r"\s+", " ", str(mot)).strip()
        if mot and not re.search(r"\d", mot) and mot.lower() not in (p.lower() for p in propositions):
            propositions.append(mot)
    return propositions[:5]


def nom_de_categorie(nom: str) -> str:
    """Nom confirme par l'humain -> nom normalise du registre ('Bulletin de paie' ->
    'bulletin_de_paie')."""
    t = re.sub(r"[^a-z0-9]+", "_", normaliser(nom or "")).strip("_")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", t):
        raise ErreurValidation("nom de categorie invalide")
    return t


def creer_categorie(nom: str, mots_cles: list, chemin_json=None,
                    chemin_registre: Path = CHEMIN_REGISTRE, sortie: Path = DOSSIER_SORTIE,
                    dossier_logs: Path = None):
    """Ajoute la categorie (nom et mots-cles CONFIRMES par l'humain) au registre,
    apres verification complete par config.py ; puis range le document s'il est donne.
    Renvoie (nom normalise, ResultatValidation ou None)."""
    if not 3 <= len(mots_cles) <= 5:
        raise ErreurValidation("il faut de 3 a 5 mots-cles")
    nom_norm = nom_de_categorie(nom)
    chemin_registre = Path(chemin_registre)
    registre = charger_registre(chemin_registre)
    if trouver_categorie(registre, nom_norm):
        raise ErreurValidation(f"la categorie {nom_norm} existe deja")
    candidat = copy.deepcopy(registre)
    candidat["categories"].append({
        "nom": nom_norm,
        "dossier": nom_norm[0].upper() + nom_norm[1:],
        "sous_dossiers": [],
        "mots_cles": [mot_cle_vers_regex(m) for m in mots_cles],
        "champs": list(registre["champs_generiques"]),
        "nom_fichier": {"prefixe": nom_norm, "parties": ["titre", "personne"]},
    })
    erreurs = verifier_registre(candidat)
    if erreurs:
        raise ErreurValidation("categorie refusee par la verification du registre :\n  - "
                               + "\n  - ".join(erreurs))
    _ecrire_sur(chemin_registre, json.dumps(candidat, ensure_ascii=False, indent=2) + "\n")
    if chemin_registre.resolve() == Path(CHEMIN_REGISTRE).resolve():
        registre_par_defaut.cache_clear()             # le registre en memoire est perime
    journaliser("nouvelle_categorie", dossier_logs, categorie=nom_norm,
                nb_mots_cles=len(mots_cles))
    resultat = None
    if chemin_json is not None:
        resultat = enregistrer_corrections(chemin_json, {}, categorie=nom_norm,
                                           registre=candidat, sortie=sortie,
                                           dossier_logs=dossier_logs)
    return nom_norm, resultat
