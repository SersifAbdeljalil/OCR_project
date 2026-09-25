"""
normalize.py - Normalisation deterministe (Python pur, SANS LLM).

Le LLM extrait des valeurs "telles qu'ecrites" ("15 sept. 2026", "1 234,56 DH").
Ce module les transforme en valeurs propres et comparables :
    - dates   -> texte ISO AAAA-MM-JJ ;
    - montants -> Decimal (nombre decimal exact : pas d'erreur d'arrondi) ;
    - totaux  -> verification HT + TVA = TTC.

Chaque fonction renvoie (valeur, alertes). Les alertes sont des phrases lisibles
qui ne recopient JAMAIS la valeur analysee (une date peut etre une date de
naissance) : elles decrivent seulement le probleme.
"""

# --- Imports ---------------------------------------------------------------
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from src.config import normaliser   # minuscules + sans accents

# --- 1. Dates --------------------------------------------------------------
# Mois en toutes lettres et abreviations courantes (sans accents : le texte
# est normalise avant). "sept." -> le point est retire par la regex.
MOIS = {
    "janvier": 1, "janv": 1, "jan": 1,
    "fevrier": 2, "fevr": 2, "fev": 2,
    "mars": 3, "mar": 3,
    "avril": 4, "avr": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7, "juil": 7,
    "aout": 8,
    "septembre": 9, "sept": 9, "sep": 9,
    "octobre": 10, "oct": 10,
    "novembre": 11, "nov": 11,
    "decembre": 12, "dec": 12,
}

# 15/09/2026, 15-09-2026, 15.09.2026, 15/09/26 : toujours JOUR puis MOIS
DATE_CHIFFRES = re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?!\d)")
# 2026-09-15 (ISO) : l'annee sur 4 chiffres vient en premier
DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
# 15 septembre 2026, 15 sept. 2026, 1er octobre 2026
DATE_LETTRES = re.compile(r"(?<!\d)(\d{1,2})(?:er)?\s+([a-z]+)\.?\s+(\d{4}|\d{2})(?!\d)")


def _annee_complete(annee: str, alertes: list, annee_reference: int) -> int:
    """'26' -> 2026 ; '85' -> 1985. Pivot : pas plus d'un an dans le futur."""
    if len(annee) == 4:
        return int(annee)
    alertes.append("date : annee sur 2 chiffres, siecle deduit")
    yy = int(annee)
    return 2000 + yy if 2000 + yy <= annee_reference + 1 else 1900 + yy


def normaliser_date(texte, annee_reference: int = None):
    """Renvoie (date ISO 'AAAA-MM-JJ' ou None, alertes)."""
    alertes = []
    if texte is None or not str(texte).strip():
        return None, alertes                       # absent : pas une erreur
    annee_reference = annee_reference or date.today().year
    t = normaliser(str(texte))

    # On cherche les trois formes ; ISO d'abord pour que "2026-09-15" ne soit
    # pas lu de travers par le motif jour-mois-annee.
    candidats = []                                 # (jour, mois, annee texte)
    for m in DATE_ISO.finditer(t):
        candidats.append((m.group(3), m.group(2), m.group(1)))
    if not candidats:
        for m in DATE_CHIFFRES.finditer(t):
            candidats.append((m.group(1), m.group(2), m.group(3)))
        for m in DATE_LETTRES.finditer(t):
            mois = MOIS.get(m.group(2))
            if mois is not None:
                candidats.append((m.group(1), str(mois), m.group(3)))

    if not candidats:
        alertes.append("date : format non reconnu")
        return None, alertes
    if len(set(candidats)) > 1:
        alertes.append("date : plusieurs dates differentes dans la meme valeur")
        return None, alertes

    jour, mois, annee = candidats[0]
    annee = _annee_complete(annee, alertes, annee_reference)
    if not 1900 <= annee <= annee_reference + 50:
        alertes.append("date : annee hors limites")
        return None, alertes
    try:
        return date(annee, int(mois), int(jour)).isoformat(), alertes
    except ValueError:                             # 31/02, mois 13...
        alertes.append("date : date impossible (jour ou mois hors calendrier)")
        return None, alertes


# --- 2. Montants -----------------------------------------------------------
# Devises retirees avant l'analyse, SEULEMENT au debut ou a la fin (casse ignoree) :
# "12 DH 50" ne doit pas devenir 1250.
_DEVISE = r"(?:dirhams?|dhs?|mad|eur(?:os?)?|€)"
DEVISES = re.compile(rf"^\s*{_DEVISE}\s*|\s*{_DEVISE}\s*$", re.IGNORECASE)
# Tous les espaces, y compris insecables ( ) et fins insecables ( )
ESPACES = re.compile(r"[\s  ]+")
# Un espace n'est permis que comme separateur de milliers : suivi de 3 chiffres
ESPACE_HORS_MILLIERS = re.compile(r" (?!\d{3}(?!\d))")


def _milliers_valides(groupes: list) -> bool:
    """'1.234.567' -> ['1','234','567'] : 1 a 3 chiffres puis groupes de 3."""
    return (1 <= len(groupes[0]) <= 3 and groupes[0].isdigit()
            and all(len(g) == 3 and g.isdigit() for g in groupes[1:]))


def normaliser_montant(texte):
    """Renvoie (Decimal ou None, alertes).
    Regles : le DERNIER separateur (virgule ou point) est le separateur decimal
    quand les deux sont presents ; un separateur unique suivi d'exactement
    3 chiffres ('1.234', '1,234') est AMBIGU (mille ou un virgule deux) -> None."""
    alertes = []
    if texte is None or (isinstance(texte, str) and not texte.strip()):
        return None, alertes
    if isinstance(texte, (int, float, Decimal)) and not isinstance(texte, bool):
        return Decimal(str(texte)), alertes        # deja un nombre

    t = ESPACES.sub(" ", str(texte)).strip()       # espaces speciaux -> " "
    t = DEVISES.sub("", t).strip()
    if ESPACE_HORS_MILLIERS.search(t.lstrip("- ")):
        alertes.append("montant : format non reconnu")
        return None, alertes
    t = t.replace(" ", "")
    signe = ""
    if t.startswith("-"):                          # avoir : montant negatif
        signe, t = "-", t[1:]
    if not re.fullmatch(r"\d[\d.,]*", t) or t[-1] in ".,":
        alertes.append("montant : format non reconnu")
        return None, alertes

    virgules, points = t.count(","), t.count(".")
    if virgules and points:
        # Les deux : le dernier est le decimal, l'autre separe les milliers
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        mil = "." if dec == "," else ","
        entier, _, decimales = t.rpartition(dec)
        if t.count(dec) > 1 or not _milliers_valides(entier.split(mil)):
            alertes.append("montant : separateurs incoherents")
            return None, alertes
        nombre = entier.replace(mil, "") + "." + decimales
    elif virgules or points:
        sep = "," if virgules else "."
        morceaux = t.split(sep)
        if len(morceaux) > 2:                      # 1.234.567 : milliers seulement
            if not _milliers_valides(morceaux):
                alertes.append("montant : separateurs incoherents")
                return None, alertes
            nombre = "".join(morceaux)
        elif len(morceaux[1]) == 3:                # 1.234 / 1,234 : ambigu
            alertes.append("montant : ambigu (separateur suivi de 3 chiffres)")
            return None, alertes
        else:                                      # 1234,5 / 1234.56 : decimal
            nombre = morceaux[0] + "." + morceaux[1]
    else:
        nombre = t                                 # 1234

    try:
        return Decimal(signe + nombre), alertes
    except InvalidOperation:                       # par securite
        alertes.append("montant : format non reconnu")
        return None, alertes


# --- 3. Verification HT + TVA = TTC ----------------------------------------
TOLERANCE = Decimal("0.01")


@dataclass
class ControleTotaux:
    """Resultat de la verification des totaux d'une facture."""
    ok: bool                              # True si HT + TVA = TTC (a 0,01 pres)
    ecart: Decimal = None                 # HT + TVA - TTC, si calculable
    necessite_validation_humaine: bool = False


def _en_montant(valeur, nom: str, alertes: list):
    """Accepte un nombre ou un texte ; les alertes recoivent le nom du champ."""
    montant, alertes_montant = normaliser_montant(valeur)
    alertes += [f"{nom} : {a.split(' : ', 1)[1]}" for a in alertes_montant]
    return montant


def verifier_totaux(ht, tva, ttc):
    """Renvoie (ControleTotaux, alertes).
    `tva` peut etre un seul montant ou une liste (plusieurs taux : on additionne).
    Un ecart ne bloque rien : il ajoute une alerte et impose la validation humaine.
    Un montant manquant ou illisible : verification impossible, alerte seulement."""
    alertes = []
    lignes_tva = tva if isinstance(tva, (list, tuple)) else [tva]
    m_ht = _en_montant(ht, "montant_ht", alertes)
    m_ttc = _en_montant(ttc, "montant_ttc", alertes)
    m_tva = [_en_montant(v, "tva", alertes) for v in lignes_tva]

    if m_ht is None or m_ttc is None or not m_tva or any(v is None for v in m_tva):
        alertes.append("totaux : verification impossible (montant manquant ou illisible)")
        return ControleTotaux(ok=False), alertes

    ecart = m_ht + sum(m_tva) - m_ttc
    if abs(ecart) <= TOLERANCE:
        return ControleTotaux(ok=True, ecart=ecart), alertes
    alertes.append(f"totaux : HT + TVA different du TTC (ecart de {ecart:.2f})")
    return ControleTotaux(ok=False, ecart=ecart, necessite_validation_humaine=True), alertes
