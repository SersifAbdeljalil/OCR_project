"""
rules.py - Classification deterministe par regles (regex du registre), SANS LLM.

Tout vient de config/categories.json : mots-cles des categories, regles metier,
sous-dossiers. Aucune regle n'est ecrite en dur ici.

analyser(texte) renvoie :
    - les scores (nombre de mots-cles trouves par categorie) ;
    - le verdict : "regle metier" (prioritaire), "net" (au moins 2 indices, un
      seul type, ET au moins un mot-cle de ce type dans la ZONE TITRE), "faible"
      (un type devant mais concurrence, ou rien dans le titre), "egalite" ou
      "aucun indice" ;
    - le sous-dossier (si une categorie est trouvee) ;
    - des SIGNAUX de qualite (texte arabe, confiance OCR), renvoyes SANS decider :
      c'est classifier.py qui appliquera la regle de confiance A.

Reprend la logique validee de test_classification.py.
"""

# --- Imports ---------------------------------------------------------------
import re
from dataclasses import dataclass, field

from src.config import (SEUIL_CONFIANCE_LIGNE_OCR, choisir_sous_dossier,
                        normaliser, registre_par_defaut, trouver_categorie,
                        zone_titre)

# --- Verdicts possibles ----------------------------------------------------
REGLE_METIER = "regle metier"
NET = "net"
FAIBLE = "faible"
EGALITE = "egalite"
AUCUN_INDICE = "aucun indice"
VERDICTS_SURS = (NET, REGLE_METIER)       # ceux qui valent 0.95 dans la regle A

# Lettres arabes : blocs de base (0600-06FF), supplement (0750-077F), etendu-A
# (08A0-08FF) et FORMES DE PRESENTATION A (FB50-FDFF) et B (FE70-FEFF). Les PDF arabes
# sont souvent extraits en formes de presentation. Le bloc FE00-FE6F, entre les deux,
# ne contient aucune lettre arabe (selecteurs de variantes, formes verticales...).
LETTRE_ARABE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")


# --- Resultats -------------------------------------------------------------
@dataclass
class SignauxQualite:
    """Indices de fiabilite du texte. Aucune decision ici."""
    part_arabe: float = 0.0                  # lettres arabes / toutes les lettres (texte natif)
    lignes_ocr: int = 0                      # nombre de lignes lues par OCR
    confiance_ocr_moyenne: float = None      # None si aucune ligne OCR
    lignes_ocr_sous_seuil: int = 0           # lignes OCR sous 0,90


@dataclass
class VerdictRegles:
    categorie: str = None                    # nom du registre, ou None
    verdict: str = AUCUN_INDICE
    scores: dict = field(default_factory=dict)
    scores_titre: dict = field(default_factory=dict)   # memes scores, zone titre seule
    regle_metier: str = None                 # description de la regle appliquee
    sous_dossier: str = None
    signaux: SignauxQualite = field(default_factory=SignauxQualite)


# --- 1. Scores et verdict --------------------------------------------------
def calculer_scores(texte: str, registre: dict) -> dict:
    """Pour chaque categorie : combien de ses mots-cles sont presents."""
    t = normaliser(texte)
    return {cat["nom"]: sum(1 for motif in cat["mots_cles"] if re.search(motif, t))
            for cat in registre["categories"]}


def appliquer_regles_metier(texte: str, registre: dict):
    """Premiere regle metier dont TOUS les motifs sont presents :
    (categorie, description), sinon None."""
    t = normaliser(texte)
    for regle in registre["regles_metier"]:
        if all(re.search(motif, t) for motif in regle["motifs"]):
            return regle["categorie"], regle.get("description", "")
    return None


def verdict_scores(scores: dict, scores_titre: dict = None):
    """(categorie ou None, verdict) d'apres les scores.
    scores_titre : scores de la zone titre ; si fourni, "net" exige au moins un
    mot-cle du type gagnant dans le titre (sinon "faible")."""
    classes = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if not classes or classes[0][1] == 0:
        return None, AUCUN_INDICE
    (premier, s1), s2 = classes[0], (classes[1][1] if len(classes) > 1 else 0)
    if s1 >= 2 and s2 == 0:
        if scores_titre is not None and scores_titre.get(premier, 0) == 0:
            return premier, FAIBLE        # indices seulement dans le corps du texte
        return premier, NET               # un seul type, au moins 2 indices, titre
    if s1 > s2:
        return premier, FAIBLE            # un type devant, mais concurrence
    return None, EGALITE                  # impossible de departager


# --- 2. Signaux de qualite -------------------------------------------------
def part_arabe(texte: str) -> float:
    """Proportion de lettres arabes parmi toutes les lettres (0 si aucune lettre)."""
    lettres = [c for c in texte if c.isalpha()]
    if not lettres:
        return 0.0
    return sum(1 for c in lettres if LETTRE_ARABE.match(c)) / len(lettres)


def signaux_qualite(texte_natif: str = "", confiances_ocr=()) -> SignauxQualite:
    """texte_natif : texte extrait SANS OCR (le modele OCR latin ne sait pas
    produire de lettres arabes) ; confiances_ocr : confiance de chaque ligne OCR."""
    confiances = list(confiances_ocr)
    return SignauxQualite(
        part_arabe=round(part_arabe(texte_natif or ""), 4),
        lignes_ocr=len(confiances),
        confiance_ocr_moyenne=(round(sum(confiances) / len(confiances), 4)
                               if confiances else None),
        lignes_ocr_sous_seuil=sum(1 for c in confiances if c < SEUIL_CONFIANCE_LIGNE_OCR),
    )


# --- 3. Point d'entree -----------------------------------------------------
def analyser(texte: str, registre: dict = None, texte_natif: str = None,
             confiances_ocr=()) -> VerdictRegles:
    """Classe un texte avec les regles du registre.
    texte        : texte complet (natif + OCR) sur lequel on cherche les mots-cles ;
    texte_natif  : partie native seule (pour la part d'arabe) ; par defaut = texte ;
    confiances_ocr : confiances des lignes OCR du document."""
    registre = registre or registre_par_defaut()
    texte = texte or ""
    scores = calculer_scores(texte, registre)
    scores_titre = calculer_scores(zone_titre(texte), registre)
    categorie, verdict = verdict_scores(scores, scores_titre)
    description = None

    metier = appliquer_regles_metier(texte, registre)
    if metier is not None:                         # la regle metier l'emporte
        categorie, description = metier
        verdict = REGLE_METIER

    sous_dossier = None
    if categorie is not None:
        sous_dossier = choisir_sous_dossier(trouver_categorie(registre, categorie), texte)

    return VerdictRegles(
        categorie=categorie, verdict=verdict, scores=scores, scores_titre=scores_titre,
        regle_metier=description, sous_dossier=sous_dossier,
        signaux=signaux_qualite(texte if texte_natif is None else texte_natif,
                                confiances_ocr))
