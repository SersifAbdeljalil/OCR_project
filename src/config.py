"""
config.py - Chargement et verification du registre des categories.

Le registre (config/categories.json) dit a l'agent :
    - quelles categories existent et dans quel dossier les ranger ;
    - quels mots-cles (regex) reconnaissent chaque categorie et chaque sous-dossier ;
    - quels champs extraire ;
    - quelles regles metier tranchent seules (ex. attestation de reussite -> diplome).

Au chargement, TOUT est verifie. Si le registre contient une erreur, l'agent
s'arrete avec la liste complete des problemes, plutot que de mal ranger.
"""

# --- Imports ---------------------------------------------------------------
import json          # lire le fichier du registre
import re            # verifier les noms et compiler les regex
import unicodedata   # retirer les accents
from functools import lru_cache   # ne charger le registre qu'une fois
from pathlib import Path          # chemins independants du dossier de lancement

# --- Constantes ------------------------------------------------------------
RACINE = Path(__file__).resolve().parent.parent          # dossier OCR_PROJECT
CHEMIN_REGISTRE = RACINE / "config" / "categories.json"

DOSSIER_ENTREE = RACINE / "Folder_Entree"   # documents a traiter
DOSSIER_SORTIE = RACINE / "Folder_Sortie"   # documents ranges
DOSSIER_TRAITES = "Traites"                 # sous-dossier de Folder_Entree
DOSSIER_DATA = RACINE / "data"              # resultats intermediaires (interdit en lecture a Claude)

SEUIL_CONFIANCE = 0.90          # >= 0.90 -> rangement automatique (regle A)
SEUIL_CARACTERES_PAGE = 50      # page PDF avec moins de caracteres visibles -> OCR requis
DOSSIER_A_VALIDER = "A_Valider"  # dossiers speciaux, hors registre
DOSSIER_AUTRES = "Autres"
NOMS_RESERVES = {"a_valider", "autres"}   # interdits comme nom de categorie

# Nom de categorie : minuscules sans accents, chiffres, "_" (ex. "bulletin_paie")
MOTIF_NOM = re.compile(r"^[a-z][a-z0-9_]*$")
# Nom de dossier : lettres sans accents (majuscules permises), chiffres, "_"
MOTIF_DOSSIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# Nom de champ : comme un nom de categorie (ex. "date_facture")
MOTIF_CHAMP = MOTIF_NOM

# Nombres ecrits en toutes lettres, interdits dans les mots-cles.
# ("un" / "une" sont exclus : ce sont aussi des articles.)
NOMBRES_EN_LETTRES = {
    "zero", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf",
    "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
    "vingt", "vingts", "trente", "quarante", "cinquante", "soixante",
    "cent", "cents", "mille", "million", "millions", "milliard", "milliards",
}


class ErreurRegistre(ValueError):
    """Le registre est invalide ; le message liste tous les problemes trouves."""


# --- 1. Normalisation ------------------------------------------------------
def normaliser(texte: str) -> str:
    """Minuscules + suppression des accents : "Diplôme" -> "diplome"."""
    decompose = unicodedata.normalize("NFKD", texte)          # é -> e + accent
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    return sans_accents.lower()


# --- 2. Verification d'un mot-cle (regex) ----------------------------------
def _texte_litteral(motif: str) -> str:
    r"""Retire les codes regex (\d, \b, {24}...) pour ne garder que le texte ecrit."""
    sans_codes = re.sub(r"\\[A-Za-z]", " ", motif)            # \d, \b, \s...
    return re.sub(r"\{\d+(,\d*)?\}", " ", sans_codes)         # {24}, {2,4}


def _verifier_motif(motif, ou: str, erreurs: list) -> None:
    """Ajoute a `erreurs` les problemes d'un mot-cle (regex)."""
    if not isinstance(motif, str) or not motif.strip():
        erreurs.append(f"{ou} : mot-cle vide ou non textuel")
        return
    try:
        re.compile(motif)
    except re.error as err:
        erreurs.append(f"{ou} : regex invalide {motif!r} ({err})")
        return
    litteral = _texte_litteral(motif)
    # Le texte est normalise avant la recherche : un motif avec majuscule
    # ou accent ne trouverait jamais rien.
    if litteral != normaliser(litteral):
        erreurs.append(f"{ou} : {motif!r} doit etre en minuscules sans accents")
    # Regle stricte : pas de numero ecrit en clair (\d+ reste autorise)
    if re.search(r"\d", litteral):
        erreurs.append(f"{ou} : {motif!r} contient un chiffre (utiliser \\d)")
    mots = set(re.findall(r"[a-z]+", litteral))
    if mots & NOMBRES_EN_LETTRES:
        erreurs.append(f"{ou} : {motif!r} contient un nombre en toutes lettres")


def _verifier_champs(champs, ou: str, erreurs: list) -> None:
    """Liste de champs non vide, noms valides, sans doublon."""
    if not isinstance(champs, list) or not champs:
        erreurs.append(f"{ou} : liste de champs vide ou absente")
        return
    for champ in champs:
        if not isinstance(champ, str) or not MOTIF_CHAMP.match(champ):
            erreurs.append(f"{ou} : nom de champ invalide {champ!r}")
    if len(set(champs)) != len(champs):
        erreurs.append(f"{ou} : champ en double")


def _verifier_sous_dossiers(sous_dossiers, ou: str, erreurs: list) -> None:
    """Sous-dossiers : noms valides sans doublon, mots-cles valides, et un
    sous-dossier Autres obligatoire (sans mots-cles : c'est le choix par defaut)."""
    if not isinstance(sous_dossiers, list):
        erreurs.append(f"{ou} : sous_dossiers doit etre une liste")
        return
    if not sous_dossiers:
        return                                  # categorie sans sous-dossier : permis
    noms_vus = set()
    for sd in sous_dossiers:
        nom = sd.get("nom", "") if isinstance(sd, dict) else ""
        ou_sd = f"{ou}, sous-dossier {nom!r}"
        if not isinstance(nom, str) or not MOTIF_DOSSIER.match(nom):
            erreurs.append(f"{ou_sd} : nom de sous-dossier invalide")
            continue
        if nom.lower() in noms_vus:
            erreurs.append(f"{ou_sd} : sous-dossier en double")
        noms_vus.add(nom.lower())
        mots_cles = sd.get("mots_cles", [])
        if nom == DOSSIER_AUTRES:
            if mots_cles:
                erreurs.append(f"{ou_sd} : Autres ne doit pas avoir de mots-cles")
            continue
        if not mots_cles:
            erreurs.append(f"{ou_sd} : aucun mot-cle")
        for motif in mots_cles:
            _verifier_motif(motif, ou_sd, erreurs)
    if DOSSIER_AUTRES not in {sd.get("nom") for sd in sous_dossiers if isinstance(sd, dict)}:
        erreurs.append(f"{ou} : sous-dossier {DOSSIER_AUTRES!r} obligatoire")


PARTIE_SOUS_DOSSIER = "sous_dossier"   # dans nom_fichier : le nom du sous-dossier choisi


def _verifier_nom_fichier(cat: dict, ou: str, erreurs: list) -> None:
    """nom_fichier = {"prefixe": "facture", "parties": ["fournisseur", ...]} :
    prefixe normalise ; chaque partie est un champ de la categorie, ou
    "sous_dossier" (seulement si la categorie a des sous-dossiers)."""
    modele = cat.get("nom_fichier")
    if not isinstance(modele, dict):
        erreurs.append(f"{ou} : nom_fichier absent")
        return
    if not isinstance(modele.get("prefixe"), str) or not MOTIF_NOM.match(modele["prefixe"]):
        erreurs.append(f"{ou} : prefixe de nom_fichier invalide")
    autorisees = set(cat.get("champs") or [])
    if cat.get("sous_dossiers"):
        autorisees.add(PARTIE_SOUS_DOSSIER)
    parties = modele.get("parties")
    if not isinstance(parties, list) or not parties:
        erreurs.append(f"{ou} : nom_fichier sans parties")
        return
    for partie in parties:
        if partie not in autorisees:
            erreurs.append(f"{ou} : partie de nom_fichier inconnue {partie!r}")


# --- 3. Verification du registre complet -----------------------------------
def verifier_registre(registre: dict) -> list:
    """Renvoie la liste des problemes du registre (liste vide = registre valide)."""
    erreurs = []
    for cle in ("champs_generiques", "champs_sensibles", "categories", "regles_metier"):
        if cle not in registre:
            erreurs.append(f"cle manquante : {cle}")
    if erreurs:
        return erreurs

    _verifier_champs(registre["champs_generiques"], "champs_generiques", erreurs)
    _verifier_champs(registre["champs_sensibles"], "champs_sensibles", erreurs)

    noms, dossiers = set(), set()
    for i, cat in enumerate(registre["categories"]):
        nom = cat.get("nom", "")
        ou = f"categorie {nom!r}" if nom else f"categorie n°{i + 1}"

        # Nom : normalise, pas reserve, pas en double
        if not isinstance(nom, str) or not MOTIF_NOM.match(nom):
            erreurs.append(f"{ou} : nom invalide (minuscules sans accents, a-z 0-9 _)")
        elif nom in NOMS_RESERVES:
            erreurs.append(f"{ou} : nom reserve (A_Valider / Autres)")
        elif nom in noms:
            erreurs.append(f"{ou} : categorie en double")
        noms.add(nom)

        # Dossier : sans accents ; Windows ne distingue pas "Factures" et "factures",
        # donc les doublons sont compares en minuscules.
        dossier = cat.get("dossier", "")
        if not isinstance(dossier, str) or not MOTIF_DOSSIER.match(dossier):
            erreurs.append(f"{ou} : nom de dossier invalide {dossier!r}")
        elif dossier.lower() in dossiers | NOMS_RESERVES:
            erreurs.append(f"{ou} : dossier {dossier!r} deja utilise ou reserve")
        else:
            dossiers.add(dossier.lower())

        _verifier_sous_dossiers(cat.get("sous_dossiers", []), ou, erreurs)

        # Mots-cles : au moins un, tous valides, sans doublon
        mots_cles = cat.get("mots_cles", [])
        if not mots_cles:
            erreurs.append(f"{ou} : aucun mot-cle")
        for motif in mots_cles:
            _verifier_motif(motif, ou, erreurs)
        if len(set(mots_cles)) != len(mots_cles):
            erreurs.append(f"{ou} : mot-cle en double")

        _verifier_champs(cat.get("champs"), ou, erreurs)
        _verifier_nom_fichier(cat, ou, erreurs)

    # Regles metier : categorie existante, motifs valides
    for j, regle in enumerate(registre["regles_metier"]):
        ou = f"regle metier n°{j + 1}"
        if regle.get("categorie") not in noms:
            erreurs.append(f"{ou} : categorie inconnue {regle.get('categorie')!r}")
        motifs = regle.get("motifs", [])
        if not motifs:
            erreurs.append(f"{ou} : aucun motif")
        for motif in motifs:
            _verifier_motif(motif, ou, erreurs)
    return erreurs


# --- 4. Chargement ---------------------------------------------------------
def charger_registre(chemin: Path = CHEMIN_REGISTRE) -> dict:
    """Lit et verifie le registre. Leve ErreurRegistre si quoi que ce soit cloche."""
    try:
        registre = json.loads(Path(chemin).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ErreurRegistre(f"registre introuvable : {chemin}") from None
    except json.JSONDecodeError as err:
        raise ErreurRegistre(f"registre illisible (JSON invalide) : {err}") from None
    if not isinstance(registre, dict):
        raise ErreurRegistre("le registre doit etre un objet JSON")

    erreurs = verifier_registre(registre)
    if erreurs:
        raise ErreurRegistre("registre invalide :\n  - " + "\n  - ".join(erreurs))
    return registre


@lru_cache(maxsize=1)
def registre_par_defaut() -> dict:
    """Le registre du projet, charge une seule fois par execution."""
    return charger_registre()


# --- 5. Petits outils de consultation --------------------------------------
def trouver_categorie(registre: dict, nom: str):
    """Renvoie la categorie portant ce nom, ou None si elle n'existe pas."""
    return next((c for c in registre["categories"] if c["nom"] == nom), None)


def champs_attendus(registre: dict, nom_type: str) -> list:
    """Champs a extraire pour un type : ceux de la categorie (ou les champs
    generiques si le type n'est pas dans le registre), puis les champs sensibles,
    sans doublon et dans un ordre stable."""
    cat = trouver_categorie(registre, nom_type)
    base = cat["champs"] if cat else registre["champs_generiques"]
    return list(dict.fromkeys(base + registre["champs_sensibles"]))


def modele_nom_fichier(registre: dict, nom_type: str) -> dict:
    """Modele de nom de fichier d'un type. Categorie absente du registre
    (decouverte, ou "autres") : <type>_<titre>_<personne>."""
    cat = trouver_categorie(registre, nom_type)
    if cat is not None:
        return cat["nom_fichier"]
    return {"prefixe": nom_type, "parties": ["titre", "personne"]}


def choisir_sous_dossier(categorie: dict, texte: str):
    """Choisit le sous-dossier d'un document, SANS LLM :
        - exactement un sous-dossier reconnu -> ce sous-dossier ;
        - aucun ou plusieurs                 -> "Autres" ;
        - categorie sans sous-dossiers       -> None (rangement a la racine).
    Renvoie seulement un NOM de dossier, jamais d'extrait du texte."""
    sous_dossiers = categorie.get("sous_dossiers", [])
    if not sous_dossiers:
        return None
    t = normaliser(texte)
    reconnus = [sd["nom"] for sd in sous_dossiers
                if sd["nom"] != DOSSIER_AUTRES
                and any(re.search(motif, t) for motif in sd["mots_cles"])]
    return reconnus[0] if len(reconnus) == 1 else DOSSIER_AUTRES
