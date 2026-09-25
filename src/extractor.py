"""
extractor.py - Extraction des champs STRUCTURES par regex (etape b10a, SANS LLM).

Champs : numero, dates (facture, generique, signature, obtention, naissance),
montants (HT, TVA, TTC / net a payer), CIN, RIB, IBAN, ICE.
Les motifs sont dans config/extraction.json (rien en dur ici).

Methode, LIGNE PAR LIGNE (texte natif et texte OCR, dans l'ordre des pages) :
    1. l'etiquette est cherchee sur la ligne normalisee (minuscules, sans accents) ;
    2. la valeur est cherchee juste apres, sur la meme ligne ; sinon sur la ligne
       suivante si elle ne contient QUE la valeur (ex. "Total HT :" puis "1 988,00 DH") ;
    3. chaque valeur garde sa provenance : champ, etiquette, ligne, page, confiance OCR ;
    4. valeurs normalisees par normalize.py (dates ISO, montants Decimal) ;
    5. plusieurs candidats : etiquette la plus prioritaire, puis regle "premier",
       "dernier" ou "somme" du fichier de configuration ; alerte si les valeurs different ;
    6. ligne OCR sous 0,90 -> alerte + validation humaine obligatoire ;
    7. factures : controle HT + TVA = TTC (normalize.verifier_totaux).

CONFIDENTIALITE : les alertes ne contiennent que des noms de champs et des chiffres de
confiance, JAMAIS de valeur. repr() masque les valeurs.
"""

# --- Imports ---------------------------------------------------------------
import json
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from src.config import (RACINE, SEUIL_CONFIANCE_LIGNE_OCR, champs_attendus,
                        registre_par_defaut)
from src.normalize import (ControleTotaux, normaliser_date, normaliser_montant,
                           verifier_totaux)

CHEMIN_CONFIG = RACINE / "config" / "extraction.json"
TYPES = {"date", "montant", "texte", "chiffres"}
CHOIX = {"premier", "dernier", "somme"}
CHAMPS_TOTAUX = ("montant_ht", "tva", "montant_ttc")

# Un montant tel qu'ecrit : 1 988,00 / 5.455,00 / 1234.56 / -150,00
MOTIF_MONTANT = re.compile(
    r"-?\d{1,3}(?:[   .,]\d{3})+(?:[.,]\d{1,2})?|-?\d+(?:[.,]\d{1,2})?")
DEVISE_SEULE = r"\s*(?:dh|dhs|mad|dirhams?|eur|euros?|€)?\.?\s*"


class ErreurConfigExtraction(ValueError):
    """config/extraction.json invalide."""


# --- 1. Configuration --------------------------------------------------------
def charger_config(chemin: Path = CHEMIN_CONFIG) -> dict:
    """Lit et verifie la configuration ; compile les motifs."""
    try:
        brut = json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise ErreurConfigExtraction(f"configuration illisible ({type(err).__name__})") from None
    erreurs, champs = [], {}
    for nom, c in brut.get("champs", {}).items():
        if c.get("type") not in TYPES:
            erreurs.append(f"{nom} : type inconnu {c.get('type')!r}")
            continue
        if c.get("choix", "premier") not in CHOIX:
            erreurs.append(f"{nom} : choix inconnu {c.get('choix')!r}")
        if c["type"] in ("texte", "chiffres") and not c.get("valeur"):
            erreurs.append(f"{nom} : motif 'valeur' obligatoire pour le type {c['type']}")
        if not c.get("etiquettes"):
            erreurs.append(f"{nom} : aucune etiquette")
        try:
            champs[nom] = {
                **c,
                "etiquettes_re": [re.compile(e) for e in c.get("etiquettes", [])],
                "valeur_re": re.compile(c["valeur"]) if c.get("valeur") else None,
                "ignorer_re": re.compile(c["ignorer"]) if c.get("ignorer") else None,
                "choix": c.get("choix", "premier"),
            }
        except re.error as err:
            erreurs.append(f"{nom} : regex invalide ({err})")
    if erreurs:
        raise ErreurConfigExtraction("configuration invalide :\n  - " + "\n  - ".join(erreurs))
    return champs


# --- 2. Lignes du document ----------------------------------------------------
@dataclass
class LigneSource:
    texte: str = field(repr=False)
    confiance: float = None        # None : texte natif (pas d'OCR)
    page: int = None


def lignes_du_document(extraction, pages_ocr: dict = None) -> list:
    """Lignes dans l'ordre des pages : texte natif (confiance None) ou lignes OCR
    (avec leur confiance). Word / Excel : toutes les lignes du texte."""
    from src.extract_text import STATUT_TEXTE
    pages_ocr = pages_ocr or {}
    if not extraction.textes_pages:
        return [LigneSource(l) for l in extraction.texte.splitlines() if l.strip()]
    lignes = []
    for numero, (natif, statut) in enumerate(
            zip(extraction.textes_pages, extraction.statut_pages), start=1):
        if statut == STATUT_TEXTE:
            lignes += [LigneSource(l, None, numero) for l in natif.splitlines() if l.strip()]
        elif numero in pages_ocr and pages_ocr[numero].statut == "ok":
            lignes += [LigneSource(l.texte, l.confiance, numero)
                       for l in pages_ocr[numero].lignes if l.texte.strip()]
    return lignes


def normaliser_ligne(texte: str) -> str:
    """Minuscules sans accents, EN GARDANT LA LONGUEUR (caractere par caractere) :
    une position trouvee dans la ligne normalisee vaut dans la ligne d'origine."""
    sortie = []
    for c in texte:
        d = "".join(x for x in unicodedata.normalize("NFKD", c)
                    if not unicodedata.combining(x)).lower()
        sortie.append(d if len(d) == 1 else c.lower() if len(c.lower()) == 1 else c)
    return "".join(sortie)


# --- 3. Valeurs ---------------------------------------------------------------
def _valeur(conf: dict, texte: str, ligne_seule: bool):
    """Cherche la valeur du champ dans `texte`. ligne_seule=True : le texte ne doit
    contenir QUE la valeur (ligne suivant l'etiquette). Renvoie (valeur, alertes)
    ou (None, alertes)."""
    if conf["ignorer_re"] is not None:
        texte = conf["ignorer_re"].sub(" ", texte)
    t = conf["type"]
    if t == "date":
        if ligne_seule and len(texte.strip()) > 40:
            return None, []
        return normaliser_date(texte)
    if t == "montant":
        if ligne_seule:
            m = re.fullmatch(rf"\s*({MOTIF_MONTANT.pattern}){DEVISE_SEULE}", texte, re.IGNORECASE)
            return normaliser_montant(m.group(1)) if m else (None, [])
        # Meme ligne que l'etiquette : le premier nombre qui A L'AIR d'un montant
        # (decimales, ou devise juste apres). Evite de lire un "9" isole dans du
        # bruit OCR ou un numero d'article.
        for m in MOTIF_MONTANT.finditer(texte):
            a_decimales = re.search(r"[.,]\d{1,2}$", m.group(0))
            devise = re.match(r"\s*(?:dh|dhs|mad|dirhams?|eur|euros?|€)\b", texte[m.end():],
                              re.IGNORECASE)
            if a_decimales or devise:
                return normaliser_montant(m.group(0))
        return None, []
    # texte / chiffres
    m = (re.fullmatch(rf"\s*[:\-]?\s*({conf['valeur']})\s*", texte) if ligne_seule
         else conf["valeur_re"].search(texte))
    if not m:
        return None, []
    brut = (m.group(1) if ligne_seule else m.group(0)).strip()
    if t == "chiffres":
        chiffres = re.sub(r"\D", "", brut)
        return (chiffres, []) if len(chiffres) == conf["longueur"] else (None, [])
    if conf.get("sans_espaces"):
        brut = re.sub(r"\s", "", brut).upper()
    if not re.search(r"\d", brut):                  # un numero contient au moins un chiffre
        return None, []
    return brut, []


@dataclass
class Candidat:
    valeur: object = field(repr=False)
    priorite: int = 0              # indice de l'etiquette (0 = la plus prioritaire)
    ligne: int = 0                 # indice de la ligne de l'etiquette
    ligne_valeur: int = 0          # indice de la ligne de la valeur (meme ou suivante)
    page: int = None
    confiance: float = None        # min des confiances OCR des lignes utilisees


def chercher_candidats(nom: str, conf: dict, lignes: list):
    """Tous les candidats d'un champ dans le document. Renvoie (candidats, alertes)."""
    candidats, alertes = [], []
    normalisees = [normaliser_ligne(l.texte) for l in lignes]
    for i, ligne in enumerate(lignes):
        for priorite, etiquette in enumerate(conf["etiquettes_re"]):
            m = etiquette.search(normalisees[i])
            if not m:
                continue
            valeur, al = _valeur(conf, ligne.texte[m.end():], ligne_seule=False)
            j = i
            if valeur is None and i + 1 < len(lignes):          # valeur a la ligne suivante
                valeur, al2 = _valeur(conf, lignes[i + 1].texte, ligne_seule=True)
                j, al = i + 1, al + al2
            if valeur is not None:
                confs = [c for c in (ligne.confiance, lignes[j].confiance) if c is not None]
                candidats.append(Candidat(valeur, priorite, i, j, ligne.page,
                                          min(confs) if confs else None))
            else:
                alertes += [f"{nom} : {a.split(' : ', 1)[-1]}" for a in al]
            break                           # une seule etiquette (la plus prioritaire) par ligne
    return candidats, alertes


def choisir(nom: str, conf: dict, candidats: list):
    """Regle simple et documentee :
        1. on ne garde que l'etiquette la plus prioritaire trouvee ;
        2. 'premier' / 'dernier' : position dans le document ; 'somme' : on additionne
           (lignes distinctes).
    Renvoie (candidat retenu ou None, alertes)."""
    if not candidats:
        return None, []
    meilleure = min(c.priorite for c in candidats)
    groupe = [c for c in candidats if c.priorite == meilleure]
    alertes = []
    if conf["choix"] == "somme":
        uniques = {c.ligne_valeur: c for c in groupe}.values()
        total = sum((c.valeur for c in uniques), Decimal(0))
        confs = [c.confiance for c in uniques if c.confiance is not None]
        retenu = Candidat(total, meilleure, groupe[0].ligne, groupe[-1].ligne_valeur,
                          groupe[0].page, min(confs) if confs else None)
        retenu.detail = [c.valeur for c in uniques]
        return retenu, alertes
    retenu = groupe[0] if conf["choix"] == "premier" else groupe[-1]
    if len({str(c.valeur) for c in groupe}) > 1:
        alertes.append(f"{nom} : {len(groupe)} valeurs candidates differentes, "
                       f"retenue selon la regle « {conf['choix']} »")
    return retenu, alertes


# --- 4. Point d'entree -------------------------------------------------------------
@dataclass
class ChampExtrait:
    valeur: object = field(repr=False)     # JAMAIS affichee
    etiquette: int = 0                     # indice de l'etiquette (provenance)
    ligne: int = 0                         # indice de la ligne de la valeur
    page: int = None
    confiance: float = None                # confiance OCR (None si texte natif)
    nb_candidats: int = 1
    methode: str = "regex"


@dataclass
class ResultatChamps:
    champs: dict = field(default_factory=dict)       # nom -> ChampExtrait
    alertes: list = field(default_factory=list)
    necessite_validation_humaine: bool = False
    controle_totaux: ControleTotaux = None
    statut_totaux: str = None                        # "ok", "ecart", "manquant" ou None

    def valeurs(self) -> dict:
        """{nom: valeur} pour le JSON de sortie (Decimal, date ISO, texte)."""
        return {n: c.valeur for n, c in self.champs.items()}


def extraire_champs(lignes: list, categorie: str, registre: dict = None,
                    config: dict = None) -> ResultatChamps:
    """Extrait par regex les champs de la categorie qui ont un motif dans la
    configuration. Ne leve pas d'exception pour un champ introuvable."""
    registre = registre or registre_par_defaut()
    config = config or charger_config()
    res = ResultatChamps()
    for nom in champs_attendus(registre, categorie):
        if nom not in config:
            continue                               # champ libre : pour le LLM (b10b)
        conf = config[nom]
        candidats, alertes = chercher_candidats(nom, conf, lignes)
        retenu, al_choix = choisir(nom, conf, candidats)
        res.alertes += alertes + al_choix
        if retenu is None:
            continue
        res.champs[nom] = ChampExtrait(retenu.valeur, retenu.priorite, retenu.ligne_valeur,
                                       retenu.page, retenu.confiance, len(candidats))
        if getattr(retenu, "detail", None) and len(retenu.detail) > 1:
            res.champs[nom].detail = retenu.detail
        # Ligne OCR peu sure -> validation humaine obligatoire
        if retenu.confiance is not None and retenu.confiance < SEUIL_CONFIANCE_LIGNE_OCR:
            res.alertes.append(f"{nom} : ligne OCR peu sure (confiance {retenu.confiance:.2f})")
            res.necessite_validation_humaine = True

    # Controle des totaux (categories qui ont des montants)
    if all(c in champs_attendus(registre, categorie) for c in CHAMPS_TOTAUX):
        v = res.valeurs()
        controle, alertes = verifier_totaux(v.get("montant_ht"), v.get("tva"),
                                            v.get("montant_ttc"))
        res.controle_totaux = controle
        res.alertes += alertes
        if controle.ok:
            res.statut_totaux = "ok"
        elif controle.ecart is not None:
            res.statut_totaux = "ecart"
        else:
            res.statut_totaux = "manquant"
        res.necessite_validation_humaine |= controle.necessite_validation_humaine
    return res
