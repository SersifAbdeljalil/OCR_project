"""
masking.py - Masquage pour l'AFFICHAGE uniquement (terminal, logs, rapports de test).

Les fichiers de sortie (.txt / .json) ne sont JAMAIS masques : ce module ne sert
qu'a ce qu'un humain ou un log voit passer a l'ecran.

Principe : il vaut mieux masquer trop que pas assez. Un faux positif a l'affichage
n'a aucune consequence ; une donnee sensible qui passe est une fuite.
"""

# --- Imports ---------------------------------------------------------------
import re
from pathlib import Path

from src.config import (DOSSIER_A_VALIDER, DOSSIER_AUTRES, normaliser,
                        registre_par_defaut)

MASQUE = "[MASQUÉ]"
TROUVE, ABSENT = "trouvé", "absent"

# --- 1. Les motifs sensibles -----------------------------------------------
# L'ORDRE COMPTE : un email ou un IBAN contient des chiffres, on les masque
# d'abord en entier, avant que les regles "chiffres" n'en masquent un morceau.
MOTIFS_SENSIBLES = [
    # Email : texte@domaine.ext
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    # IBAN marocain : MA + chiffres (espaces, points ou tirets permis), casse ignoree
    ("iban", re.compile(r"\bMA[\s.-]?\d(?:[\s.-]?\d){9,}", re.IGNORECASE)),
    # Telephone international : +212 ou 00212, puis 5/6/7 et 8 chiffres
    ("telephone", re.compile(
        r"(?:\+|\b00)\s?212[\s.-]?(?:\(0\)|0)?[\s.-]?[5-7](?:[\s.-]?\d){8}(?!\d)")),
    # Telephone national : 05, 06, 07 puis 8 chiffres (espaces, points, tirets permis)
    ("telephone", re.compile(r"(?<!\d)0[5-7](?:[\s.-]?\d){8}(?!\d)")),
    # RIB : 24 chiffres, espaces permis entre les groupes
    ("rib", re.compile(r"(?<!\d)\d(?: ?\d){23}(?!\d)")),
    # Toute suite de 8 chiffres ou plus, collee ou separee par des espaces simples
    ("chiffres", re.compile(r"(?<!\d)\d(?: ?\d){7,}(?!\d)")),
    # CIN : 1 ou 2 lettres collees a 5 chiffres ou plus (casse ignoree, AB123456)...
    ("cin", re.compile(r"\b[A-Za-z]{1,2}\d{5,}\b")),
    # ... ou 1 ou 2 MAJUSCULES, un espace, puis 5 chiffres ou plus (AB 123456).
    # (Sans espace impose en majuscules, "de 15000" serait masque partout.)
    ("cin", re.compile(r"\b[A-Z]{1,2} \d{5,}\b")),
]


# Contexte CIN : tout ce qui suit "CIN", "C.I.N", "CNIE" ou "carte nationale"
# (casse ignoree, avec ou sans ":" / "n°") est TOUJOURS masque, meme en
# minuscules avec un espace ("cin ab 123456"). Le mot-cle, lui, reste visible.
MOTIF_APRES_CIN = re.compile(
    # Le mot-cle (garde a l'affichage)...
    r"(?P<cle>\b(?:c\.?\s?i\.?\s?n\b\.?|c\.?n\.?i\.?e\b\.?"
    r"|carte\s+nationale(?:\s+d['’]\s?identit[eé](?:\s+[eé]lectronique)?)?)"
    # ... suivi de separateurs facultatifs : ":", "n°", "no", "numero"...
    r"(?:\s*(?::|n\s?[°º]|no\.?|num[eé]ro)\s*)*\s*)"
    # ... puis la valeur masquee : lettres + chiffres (ab 123456), sinon le mot suivant.
    r"(?P<val>(?:[a-z]{1,3}[\s.-]?)?\d(?:\s?\d)*|[^\s,;]+)",
    re.IGNORECASE)


def masquer_texte(texte) -> str:
    """Remplace par [MASQUÉ] tout ce qui ressemble a une donnee sensible."""
    if texte is None:
        return ""
    resultat = str(texte)
    # 1) D'abord ce qui suit un mot-cle CIN (garde le mot-cle, masque la valeur)
    resultat = MOTIF_APRES_CIN.sub(lambda m: m.group("cle") + MASQUE, resultat)
    # 2) Puis les motifs generaux
    for _nom, motif in MOTIFS_SENSIBLES:
        resultat = motif.sub(MASQUE, resultat)
    return resultat


# --- 2. Resume des champs extraits -----------------------------------------
# Champs qui contiennent des noms de PERSONNES : aucune regex ne reconnait un nom,
# donc on n'affiche jamais leur valeur (question H, decidee le 2026-09-25).
# Les champs d'ORGANISMES (fournisseur, banque, etablissement, emetteur, organisme)
# restent affiches, via masquer_texte.
CHAMPS_PERSONNES = {"titulaire", "beneficiaire", "personne", "parties"}


def resume_champs(champs: dict, registre: dict = None) -> dict:
    """Version affichable des champs d'un document :
        - champ sensible du registre ou champ de personne
                                    -> "trouvé" ou "absent" (jamais la valeur) ;
        - autre champ               -> valeur passee dans masquer_texte,
                                       ou "absent" si elle est vide."""
    registre = registre or registre_par_defaut()
    sensibles = set(registre["champs_sensibles"]) | CHAMPS_PERSONNES
    resume = {}
    for nom, valeur in champs.items():
        vide = valeur is None or (isinstance(valeur, str) and not valeur.strip())
        if nom in sensibles:
            resume[nom] = ABSENT if vide else TROUVE
        else:
            resume[nom] = ABSENT if vide else masquer_texte(valeur)
    return resume


# --- 3. Nom de fichier affichable ------------------------------------------
# Dossiers techniques du projet, surs a afficher en plus de ceux du registre
DOSSIERS_TECHNIQUES = {"folder_sortie", "folder_entree", "traites", "tests", "docs_test",
                       DOSSIER_A_VALIDER.lower(), DOSSIER_AUTRES.lower()}
ETOILES = "***"


def _vocabulaire_sur(registre: dict):
    """Mots connus du registre (surs) : noms de categories, singuliers, dossiers,
    sous-dossiers. Tout le reste d'un nom de fichier est suppose personnel."""
    mots, dossiers = set(), set(DOSSIERS_TECHNIQUES)
    for cat in registre["categories"]:
        dossiers.add(cat["dossier"].lower())
        mots.update({cat["nom"], cat["nom"].rstrip("s"), cat["dossier"].lower()})
        for sd in cat.get("sous_dossiers", []):
            dossiers.add(sd["nom"].lower())
            mots.add(normaliser(sd["nom"]))
    return mots, dossiers


def nom_affichable(chemin, registre: dict = None) -> str:
    """Garde la categorie, le sous-dossier et le type ; remplace par *** tout le
    reste (nom de personne, dossier inconnu comme C:/Users/<nom>).
    Exemple : Diplomes/DEUG/diplome_deug_nom_prenom.json -> Diplomes/DEUG/diplome_deug_***.json"""
    registre = registre or registre_par_defaut()
    mots_surs, dossiers_surs = _vocabulaire_sur(registre)
    chemin = Path(chemin)

    # Les dossiers : on garde seulement ceux que l'on connait
    morceaux = []
    for partie in chemin.parent.parts:
        if partie == chemin.anchor:                      # "C:\" -> "C:"
            morceau = partie.rstrip("\\/")
        elif partie.lower() in dossiers_surs:
            morceau = partie
        else:
            morceau = ETOILES
        if morceau and not (morceau == ETOILES and morceaux and morceaux[-1] == ETOILES):
            morceaux.append(morceau)                     # pas de "***/***"

    # Le nom du fichier : mots connus gardes, petits numeros de doublon (_1, _2) gardes,
    # tout le reste devient *** (une seule fois pour une suite de mots inconnus).
    jetons = []
    for jeton in re.split(r"[_\-\s.]+", chemin.stem):
        if not jeton:
            continue
        if normaliser(jeton) in mots_surs or re.fullmatch(r"\d{1,2}", jeton):
            jetons.append(jeton)
        elif not jetons or jetons[-1] != ETOILES:
            jetons.append(ETOILES)
    nom_fichier = "_".join(jetons or [ETOILES]) + chemin.suffix.lower()
    return "/".join(morceaux + [nom_fichier])
