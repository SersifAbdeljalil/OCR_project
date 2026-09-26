"""
extractor.py - Extraction des champs d'un document.

    - champs STRUCTURES par regex (etape b10a, sections 1 a 4) ;
    - champs LIBRES par LLM avec garde-fou anti-invention, puis FUSION (etape b10b,
      section 5 : extraire_champs_libres, extraire_document).

Partie regex :

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

# Un montant tel qu'ecrit : 1 988,00 / 5.455,00 / 1234.56 / -150,00. Il ne commence
# jamais au milieu d'un mot ou d'un nombre (« 2O0,00 » ne donne pas « 0,00 »).
MOTIF_MONTANT = re.compile(
    r"(?<![\w.,])(?:-?\d{1,3}(?:[   .,]\d{3})+(?:[.,]\d{1,2})?|-?\d+(?:[.,]\d{1,2})?)"
    r"(?!\d)")
DEVISE_SEULE = r"\s*(?:dh|dhs|mad|dirhams?|eur|euros?|€)?\.?\s*"


class ErreurConfigExtraction(ValueError):
    """config/extraction.json invalide."""


# Tolerance OCR sur les ETIQUETTES seulement (jamais sur les valeurs) : l'OCR confond
# souvent 0/O, 1/I/l et 5/S (ex. « T0tal HT », « 1CE »). Chaque lettre litterale d'une
# etiquette (deja en minuscules) est remplacee par la classe de ses sosies.
SOSIES_OCR = {"o": "[o0]", "i": "[i1l]", "l": "[l1i]", "s": "[s5]"}


def tolerer_ocr(motif: str) -> str:
    """Elargit les lettres litterales d'un motif d'etiquette. Ne touche ni aux codes
    (\\s, \\b, \\d...), ni aux classes [...], ni aux groupes speciaux (?:, (?<!...)."""
    sortie, i, dans_classe = [], 0, False
    while i < len(motif):
        c = motif[i]
        if c == "\\":                               # code regex : recopie tel quel
            sortie.append(motif[i:i + 2])
            i += 2
            continue
        if c == "[" and not dans_classe:
            dans_classe = True
        elif c == "]" and dans_classe:
            dans_classe = False
        elif c == "(" and motif[i + 1:i + 2] == "?":  # (?:  (?!  (?<!  : recopie le prefixe
            j = i + 2
            while j < len(motif) and motif[j] in "<!=:":
                j += 1
            sortie.append(motif[i:j])
            i = j
            continue
        sortie.append(c if dans_classe else SOSIES_OCR.get(c, c))
        i += 1
    return "".join(sortie)


# --- 1. Configuration --------------------------------------------------------
def charger_config(chemin: Path = CHEMIN_CONFIG) -> dict:
    """Lit et verifie la configuration ; compile les motifs."""
    try:
        brut = json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise ErreurConfigExtraction(f"configuration illisible ({type(err).__name__})") from None
    erreurs, champs = [], {}
    tolerance = brut.get("tolerance_ocr_etiquettes", True)
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
                "etiquettes_re": [re.compile(tolerer_ocr(e) if tolerance else e)
                                  for e in c.get("etiquettes", [])],
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
            reste = ligne.texte[m.end():]
            valeur, al = _valeur(conf, reste, ligne_seule=False)
            j = i
            if valeur is None and conf.get("lignes_suivantes"):
                # Valeur en plusieurs morceaux sur les lignes suivantes (ex. RIB en
                # 4 groupes) : seulement des lignes faites de chiffres et separateurs
                morceaux, k = [reste], i + 1
                while (k < len(lignes) and k <= i + conf["lignes_suivantes"]
                       and re.fullmatch(r"[\d\s|/.\-]+", lignes[k].texte)):
                    morceaux.append(lignes[k].texte)
                    k += 1
                if k > i + 1:
                    valeur, al2 = _valeur(conf, " ".join(morceaux), ligne_seule=False)
                    j, al = k - 1, al + al2
            elif valeur is None and i + 1 < len(lignes):        # valeur a la ligne suivante
                valeur, al2 = _valeur(conf, lignes[i + 1].texte, ligne_seule=True)
                j, al = i + 1, al + al2
            if valeur is not None:
                confs = [l.confiance for l in lignes[i:j + 1] if l.confiance is not None]
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
    etiquette: int = 0                     # indice de l'etiquette (None pour le LLM)
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
    duree_llm_s: float = 0.0                         # temps de l'appel LLM (champs libres)

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


# --- 5. Champs LIBRES par LLM (etape b10b) ---------------------------------------------
def charger_champs_libres(chemin: Path = CHEMIN_CONFIG):
    """(liste des champs libres autorises au LLM, descriptions pour le prompt,
    controles par champ). Les etiquettes de rejet recoivent la tolerance OCR."""
    brut = json.loads(Path(chemin).read_text(encoding="utf-8"))
    tolerance = brut.get("tolerance_ocr_etiquettes", True)
    controles = {}
    for nom, c in brut.get("controles_champs_libres", {}).items():
        controles[nom] = {
            "rejet_re": [re.compile(tolerer_ocr(e) if tolerance else e)
                         for e in c.get("rejet_si_etiquette", [])],
            "zone_titre": bool(c.get("zone_titre_obligatoire")),
        }
    return (brut.get("champs_libres_llm", []), brut.get("descriptions_champs_libres", {}),
            controles)


def cle_comparaison(texte: str) -> str:
    """Forme de comparaison : minuscules, sans accents, espaces reduits a un seul."""
    t = "".join(c for c in unicodedata.normalize("NFKD", texte or "")
                if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", t).strip()


def localiser_toutes(valeur: str, lignes: list) -> list:
    """Toutes les apparitions de la valeur dans le texte source, comparaison tolerante
    aux accents, a la casse et aux espaces (y compris a cheval sur plusieurs lignes).
    Renvoie une liste de (indices des lignes couvertes, confiance OCR minimale)."""
    cible = cle_comparaison(valeur)
    if not cible:
        return []
    morceaux, debuts, position = [], [], 0
    for l in lignes:
        cle = cle_comparaison(l.texte)
        debuts.append(position)
        morceaux.append(cle)
        position += len(cle) + 1                    # +1 : l'espace qui separe les lignes
    joint = " ".join(morceaux)
    apparitions, depart = [], 0
    while (trouve := joint.find(cible, depart)) >= 0:
        fin = trouve + len(cible)
        couvertes = [i for i, d in enumerate(debuts)
                     if d < fin and d + len(morceaux[i]) >= trouve]
        confs = [lignes[i].confiance for i in couvertes if lignes[i].confiance is not None]
        apparitions.append((couvertes, min(confs) if confs else None))
        depart = trouve + 1
    return apparitions


def localiser(valeur: str, lignes: list):
    """Garde-fou anti-invention : la valeur se retrouve-t-elle dans le texte source ?
    Renvoie (trouvee, indice de ligne, confiance OCR minimale) de la 1re apparition."""
    apparitions = localiser_toutes(valeur, lignes)
    if not apparitions:
        return False, None, None
    couvertes, confiance = apparitions[0]
    return True, couvertes[0], confiance


def _sur_ligne_destinataire(couvertes: list, lignes: list, motifs: list) -> bool:
    """La valeur est-elle sur une ligne qui porte une etiquette de destinataire
    (« Client : ... »), ou juste sous une ligne qui ne contient QUE cette etiquette
    (« Facture a : » puis le nom a la ligne suivante) ?"""
    for i in couvertes:
        if any(m.search(normaliser_ligne(lignes[i].texte)) for m in motifs):
            return True
    precedente = couvertes[0] - 1
    if precedente >= 0:
        texte = normaliser_ligne(lignes[precedente].texte)
        for m in motifs:
            trouve = m.search(texte)
            if trouve:
                reste = (texte[:trouve.start()] + texte[trouve.end():]).replace(":", "")
                if not re.search(r"\w", reste):     # la ligne ne contient QUE l'etiquette
                    return True
    return False


def _dans_zone_titre(valeur: str, lignes: list) -> bool:
    """La valeur est-elle dans la zone titre (memes 15 lignes que rules.py) ?"""
    from src.config import zone_titre
    zone = zone_titre("\n".join(l.texte for l in lignes))
    return cle_comparaison(valeur) in cle_comparaison(zone)


def extraire_champs_libres(lignes: list, categorie: str, client, registre: dict = None,
                           chemin_config: Path = CHEMIN_CONFIG,
                           nom_prompt: str = "extraction") -> ResultatChamps:
    """Demande au LLM les SEULS champs libres de la categorie. Chaque valeur doit se
    retrouver dans le texte source, sinon elle est rejetee (alerte + validation).
    Ne leve jamais d'exception."""
    registre = registre or registre_par_defaut()
    res = ResultatChamps()
    libres, descriptions, controles = charger_champs_libres(chemin_config)
    a_demander = [c for c in champs_attendus(registre, categorie) if c in libres]
    if not a_demander:
        return res
    schema = {"type": "object",
              "properties": {c: {"type": ["string", "null"]} for c in a_demander},
              "required": a_demander}
    variables = {"categorie": categorie,
                 "champs": "\n".join(f"- {c} : {descriptions.get(c, c)}" for c in a_demander)}
    texte = "\n".join(l.texte for l in lignes)
    rep = client.appeler(nom_prompt, texte, schema, variables=variables)
    res.duree_llm_s = rep.duree_s
    res.alertes += rep.alertes
    if not rep.ok:
        res.alertes.append(f"champs libres : moteur indisponible ({rep.erreur})")
        res.necessite_validation_humaine = True
        return res

    for nom in a_demander:
        valeur = rep.donnees.get(nom)
        if valeur is None or not str(valeur).strip():
            continue                                  # absent du document : reste absent
        valeur = re.sub(r"\s+", " ", str(valeur)).strip()
        apparitions = localiser_toutes(valeur, lignes)
        if not apparitions:
            res.alertes.append(f"{nom} : valeur du moteur absente du texte source, rejetee")
            res.necessite_validation_humaine = True
            continue
        controle = controles.get(nom, {})
        if controle.get("rejet_re"):
            # On garde seulement les apparitions qui ne sont pas sur une ligne de
            # destinataire (ex. le moteur a rendu le CLIENT au lieu du fournisseur)
            apparitions = [a for a in apparitions
                           if not _sur_ligne_destinataire(a[0], lignes, controle["rejet_re"])]
            if not apparitions:
                res.alertes.append(f"{nom} : valeur trouvee seulement sur une ligne de "
                                   "destinataire (client...), rejetee")
                res.necessite_validation_humaine = True
                continue
        couvertes, confiance = apparitions[0]
        ligne = couvertes[0]
        if controle.get("zone_titre") and not _dans_zone_titre(valeur, lignes):
            res.alertes.append(f"{nom} : valeur hors de la zone titre, a verifier")
            res.necessite_validation_humaine = True
        res.champs[nom] = ChampExtrait(valeur, None, ligne, lignes[ligne].page, confiance,
                                       1, methode="llm")
        if confiance is not None and confiance < SEUIL_CONFIANCE_LIGNE_OCR:
            res.alertes.append(f"{nom} : ligne OCR peu sure (confiance {confiance:.2f})")
            res.necessite_validation_humaine = True
    return res


def extraire_document(lignes: list, categorie: str, client=None, registre: dict = None,
                      config: dict = None) -> ResultatChamps:
    """FUSION : champs structures par regex + champs libres par LLM.
    Sans client (LLM), seuls les champs regex sont extraits. Les deux ensembles de
    champs sont disjoints : le LLM ne remplace jamais un champ structure."""
    registre = registre or registre_par_defaut()
    res = extraire_champs(lignes, categorie, registre, config)
    if client is None:
        return res
    libres = extraire_champs_libres(lignes, categorie, client, registre)
    for nom, champ in libres.champs.items():
        if nom not in res.champs:                 # par securite : la regex a priorite
            res.champs[nom] = champ
    res.alertes += libres.alertes
    res.necessite_validation_humaine |= libres.necessite_validation_humaine
    res.duree_llm_s = libres.duree_llm_s
    return res
