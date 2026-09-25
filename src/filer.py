"""
filer.py - Rangement des documents traites.

Pour un document classe et extrait, filer.py :
    1. construit le nom de fichier (registre : prefixe + parties, ex. facture_<fournisseur>_...) ;
    2. choisit le dossier : categorie + sous-dossier (cree seulement s'il n'existe pas) ;
    3. evite les doublons (_1, _2...), sans tenir compte des majuscules (Windows) ;
    4. ecrit le .txt et le .json en UTF-8 (montants a 2 decimales) ;
    5. copie l'original, VERIFIE la copie, puis seulement deplace l'original
       vers Folder_Entree/Traites/.

REGLE D'OR : l'original n'est JAMAIS perdu. En cas d'erreur a n'importe quelle
etape, il reste dans Folder_Entree/, les fichiers deja ecrits sont retires, et
une alerte est produite.

Les alertes ne contiennent ni chemin complet ni message d'erreur systeme
(ils peuvent contenir des noms de personnes) : seulement le type de probleme.
"""

# --- Imports ---------------------------------------------------------------
import hashlib       # empreinte SHA-256 pour verifier la copie
import json
import re
import shutil        # copie et deplacement de fichiers
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.config import (DOSSIER_A_VALIDER, DOSSIER_AUTRES, DOSSIER_ENTREE,
                        DOSSIER_SORTIE, DOSSIER_TRAITES, PARTIE_SOUS_DOSSIER,
                        choisir_sous_dossier, modele_nom_fichier, normaliser,
                        registre_par_defaut, trouver_categorie)
from src.schemas import DocumentSortie

# --- Limites Windows -------------------------------------------------------
MAX_CHEMIN = 259          # 260 caracteres, dont 1 reserve par Windows
MAX_COMPOSANT = 255       # longueur maximale d'un nom de fichier
RESERVE_DOUBLON = 4       # place gardee pour "_999"
LONGUEUR_MIN_BASE = 8     # en dessous, le nom n'a plus de sens : on refuse
NOMS_RESERVES_WINDOWS = ({"con", "prn", "aux", "nul"}
                         | {f"com{i}" for i in range(1, 10)}
                         | {f"lpt{i}" for i in range(1, 10)})


class ErreurRangement(Exception):
    """Probleme de rangement ; le message ne contient aucune donnee personnelle."""


@dataclass
class ResultatRangement:
    """Ce qui s'est passe pour un document."""
    ok: bool                                   # True : fichiers ecrits et copie verifiee
    a_valider: bool = False                    # True : depose dans A_Valider/
    dossier: Path = None                       # dossier de destination
    fichiers: list = field(default_factory=list)   # fichiers crees
    original_deplace: bool = False             # True : original dans Folder_Entree/Traites/
    alertes: list = field(default_factory=list)


# --- 1. Construction du nom ------------------------------------------------
def nettoyer_partie(texte) -> str:
    """Une partie de nom : minuscules, sans accents, sans espaces ; seuls a-z, 0-9
    et '-' sont gardes, tout le reste devient '_' (donc aucun caractere interdit
    par Windows : < > : " / \\ | ? *)."""
    t = normaliser(str(texte))
    t = re.sub(r"[^a-z0-9-]+", "_", t)
    t = re.sub(r"_{2,}", "_", t)
    return t.strip("_-")


def securiser_base(base: str) -> str:
    """Nom vide -> 'document' ; nom reserve par Windows (CON, NUL, COM1...) -> suffixe."""
    if not base:
        return "document"
    if base in NOMS_RESERVES_WINDOWS:
        return base + "_doc"
    return base


def construire_base(doc: DocumentSortie, registre: dict, sous_dossier) -> str:
    """Nom sans extension, d'apres le modele du registre. Une partie absente est omise.
    Ex. : facture_societe_exemple_2026-09-15_fa-2026-00042"""
    modele = modele_nom_fichier(registre, doc.type)
    morceaux = [modele["prefixe"]]
    for partie in modele["parties"]:
        valeur = sous_dossier if partie == PARTIE_SOUS_DOSSIER else doc.champs.get(partie)
        morceau = nettoyer_partie(valeur) if valeur is not None else ""
        if morceau:
            morceaux.append(morceau)
    return securiser_base("_".join(morceaux))


def tronquer_base(base: str, dossier: Path, suffixes: list) -> str:
    """Raccourcit le nom pour que le chemin complet reste sous 260 caracteres
    (en gardant la place de l'extension et d'un eventuel _999)."""
    plus_long = max(len(s) for s in suffixes)
    place = min(MAX_CHEMIN - len(str(Path(dossier).absolute())) - 1,
                MAX_COMPOSANT) - plus_long - RESERVE_DOUBLON
    if place < LONGUEUR_MIN_BASE:
        raise ErreurRangement("chemin de destination trop long")
    return base if len(base) <= place else base[:place].rstrip("_-")


def base_libre(dossier: Path, base: str, suffixes: list) -> str:
    """Ajoute _1, _2... tant qu'un des fichiers (base + suffixe) existe deja.
    Comparaison en minuscules : pour Windows, FACTURE.pdf et facture.pdf sont
    le meme fichier."""
    existants = ({p.name.lower() for p in dossier.iterdir()}
                 if dossier.is_dir() else set())
    candidat, n = base, 0
    while any(f"{candidat}{s}".lower() in existants for s in suffixes):
        n += 1
        candidat = f"{base}_{n}"
    return candidat


def _suffixe_copie(original: Path, contenus: dict) -> str:
    """Extension de la copie de l'original. Si elle est deja prise par un fichier
    produit (.txt ou .json), la copie devient <base>_original.<ext>."""
    ext = original.suffix.lower()
    return f"_original{ext}" if ext in contenus else ext


# --- 2. Depot securise -----------------------------------------------------
def _empreinte(chemin: Path) -> str:
    """Empreinte SHA-256 du fichier (lu par blocs : peu de RAM)."""
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloc)
    return h.hexdigest()


def _raison(err: Exception) -> str:
    """Raison affichable : jamais le message systeme (il contient des chemins)."""
    return str(err) if isinstance(err, ErreurRangement) else type(err).__name__


def _deposer(original: Path, dossier: Path, base: str, contenus: dict,
             dossier_entree: Path, resultat: ResultatRangement) -> ResultatRangement:
    """Ecrit les fichiers produits, copie et verifie l'original, puis le deplace.
    En cas d'erreur avant la fin de la verification : tout ce qui a ete ecrit
    est retire et l'original reste en place."""
    ecrits = []
    try:
        dossier.mkdir(parents=True, exist_ok=True)      # cree seulement si absent
        for suffixe, texte in contenus.items():
            chemin = dossier / f"{base}{suffixe}"
            ecrits.append(chemin)
            chemin.write_text(texte, encoding="utf-8", newline="\n")
        copie = dossier / f"{base}{_suffixe_copie(original, contenus)}"
        ecrits.append(copie)                            # avant : une copie partielle sera retiree
        shutil.copy2(original, copie)
        if (copie.stat().st_size != original.stat().st_size
                or _empreinte(copie) != _empreinte(original)):
            raise ErreurRangement("copie de l'original non conforme")
    except Exception as err:
        for chemin in ecrits:                           # on ne laisse rien a moitie fait
            try:
                chemin.unlink(missing_ok=True)
            except OSError:
                pass
        resultat.ok = False
        resultat.alertes.append(
            f"rangement annule ({_raison(err)}) : l'original reste dans Folder_Entree")
        return resultat

    resultat.ok, resultat.dossier, resultat.fichiers = True, dossier, ecrits

    # La copie est verifiee : on peut deplacer l'original vers Traites/
    try:
        traites = Path(dossier_entree) / DOSSIER_TRAITES
        traites.mkdir(parents=True, exist_ok=True)
        nom = base_libre(traites, original.stem, [original.suffix]) + original.suffix
        shutil.move(str(original), str(traites / nom))
        resultat.original_deplace = True
    except Exception as err:
        resultat.alertes.append(
            f"original non deplace vers Traites ({_raison(err)}) : il reste dans Folder_Entree")
    return resultat


def _preparer_base(base: str, dossier: Path, original: Path, contenus: dict):
    """Nom final : tronque puis rendu unique."""
    suffixes = list(contenus) + [_suffixe_copie(original, contenus)]
    base = tronquer_base(base, dossier, suffixes)
    return base_libre(dossier, base, suffixes)


# --- 3. Fonctions publiques ------------------------------------------------
def envoyer_a_valider(original, raison: str, doc: DocumentSortie = None,
                      alertes: list = None, registre: dict = None,
                      dossier_sortie: Path = DOSSIER_SORTIE,
                      dossier_entree: Path = DOSSIER_ENTREE) -> ResultatRangement:
    """Depose l'original dans A_Valider/ (nom d'origine nettoye) avec :
        - le .txt (texte brut) ;
        - UN SEUL .json complet : meme schema que les documents ranges, plus
          raison, alertes et categorie_proposee (pour l'ecran de validation).
    Sert aussi aux fichiers illisibles (doc=None) : pas de .txt, et un .json aux
    memes cles, mais vides."""
    original = Path(original)
    alertes = list(alertes or [])
    resultat = ResultatRangement(ok=False, a_valider=True, alertes=alertes)
    dossier = Path(dossier_sortie) / DOSSIER_A_VALIDER
    supplement = {"raison": raison, "alertes": alertes,
                  "categorie_proposee": doc.type if doc else None}
    if doc is not None:
        contenus = {".txt": doc.texte_brut, ".json": doc.vers_json(supplement) + "\n"}
    else:
        vide = {"type": None, "source": original.name,
                "date_traitement": datetime.now().isoformat(timespec="seconds"),
                "confiance_classification": None, "champs": {},
                "necessite_validation_humaine": True, "texte_brut": ""}
        contenus = {".json": json.dumps({**vide, **supplement},
                                        ensure_ascii=False, indent=2) + "\n"}
    try:
        if not original.is_file():
            raise ErreurRangement("original introuvable")
        base = securiser_base(nettoyer_partie(original.stem))
        base = _preparer_base(base, dossier, original, contenus)
    except Exception as err:
        resultat.alertes.append(f"rangement annule ({_raison(err)}) : l'original reste en place")
        return resultat
    return _deposer(original, dossier, base, contenus, dossier_entree, resultat)


def ranger_document(original, doc: DocumentSortie, alertes: list = None,
                    registre: dict = None,
                    dossier_sortie: Path = DOSSIER_SORTIE,
                    dossier_entree: Path = DOSSIER_ENTREE) -> ResultatRangement:
    """Range un document traite. Il part dans A_Valider/ si une validation humaine
    est necessaire, ou si sa categorie n'est pas dans le registre."""
    registre = registre or registre_par_defaut()
    original = Path(original)
    alertes = list(alertes or [])
    options = dict(alertes=alertes, registre=registre,
                   dossier_sortie=dossier_sortie, dossier_entree=dossier_entree)

    if doc.necessite_validation_humaine:
        return envoyer_a_valider(original, "validation humaine necessaire", doc=doc, **options)
    categorie = trouver_categorie(registre, doc.type)
    if categorie is None and doc.type != DOSSIER_AUTRES.lower():
        return envoyer_a_valider(original, "categorie absente du registre", doc=doc, **options)

    # Dossier : categorie (+ sous-dossier), ou Autres/
    if categorie is None:
        dossier, sous_dossier = Path(dossier_sortie) / DOSSIER_AUTRES, None
    else:
        sous_dossier = choisir_sous_dossier(categorie, doc.texte_brut)
        dossier = Path(dossier_sortie) / categorie["dossier"]
        if sous_dossier:
            dossier = dossier / sous_dossier

    resultat = ResultatRangement(ok=False, alertes=alertes)
    contenus = {".txt": doc.texte_brut, ".json": doc.vers_json() + "\n"}
    try:
        if not original.is_file():
            raise ErreurRangement("original introuvable")
        base = construire_base(doc, registre, sous_dossier)
        base = _preparer_base(base, dossier, original, contenus)
    except Exception as err:
        resultat.alertes.append(f"rangement annule ({_raison(err)}) : l'original reste en place")
        return resultat
    return _deposer(original, dossier, base, contenus, dossier_entree, resultat)
