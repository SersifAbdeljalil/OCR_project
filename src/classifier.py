"""
classifier.py - Classification d'un document : regles (rules.py) + moteur (LLM).

Regle de confiance A (MODIFIEE, voir CLAUDE.md) :
    regle metier                          -> 0.95, sans moteur
    verdict "net"    + moteur d'accord    -> 0.95
    tout le reste (net + desaccord, faible meme avec accord, egalite,
                   aucun indice, moteur en panne)             -> 0.60
    confiance OCR moyenne < 0.80          -> validation humaine OBLIGATOIRE
    plus de 30 % de lettres arabes (texte natif) -> validation humaine OBLIGATOIRE
    confiance < 0.90                      -> validation humaine (A_Valider/)

Categorie decouverte : si le moteur repond "autre", il propose un nom. Le nom est
normalise puis compare aux categories existantes :
    trop proche d'une categorie existante -> on garde celle-ci, mais confiance 0.60 ;
    vraiment nouveau                      -> A_Valider/ avec categorie_proposee.

MOTEUR INTERCHANGEABLE : classifier.py ne connait que l'interface MoteurClassification
(verifier, lot, classer). Le moteur utilise est choisi par son nom
(config.MOTEUR_CLASSIFICATION) dans le dictionnaire MOTEURS. Brancher un autre
moteur (ex. JEV en v2) = ecrire une classe + une ligne dans MOTEURS.

CONFIDENTIALITE : les raisons ne contiennent que des noms de categories, des
verdicts et des chiffres, jamais de texte du document.
"""

# --- Imports ---------------------------------------------------------------
import difflib
import re
import time
from contextlib import nullcontext
from dataclasses import dataclass, field

from src.config import (MOTEUR_CLASSIFICATION, NOMS_RESERVES, SEUIL_CONFIANCE,
                        SEUIL_CONFIANCE_OCR_DOCUMENT, SEUIL_PART_ARABE, DOSSIER_AUTRES,
                        choisir_sous_dossier, normaliser, registre_par_defaut,
                        trouver_categorie)
from src.rules import NET, REGLE_METIER, SignauxQualite, analyser

# --- Valeurs de la regle A ---------------------------------------------------
CONFIANCE_SURE = 0.95
CONFIANCE_DOUTE = 0.60
AUTRE = "autre"                     # reponse du moteur hors registre
SEUIL_SIMILARITE = 0.80             # nom propose "trop proche" au-dela


# ===========================================================================
# 1. Interface des moteurs
# ===========================================================================
@dataclass
class ReponseMoteur:
    ok: bool
    categorie: str = None           # un nom du registre, ou "autre"
    nom_propose: str = None         # si "autre" : nom brut propose par le moteur
    erreur: str = None              # type de probleme, jamais de texte
    duree_s: float = 0.0
    alertes: list = field(default_factory=list)


class MoteurClassification:
    """Interface commune. Un moteur doit savoir :
        verifier() -> (ok, raison)       : est-il disponible ?
        lot()      -> gestionnaire with : preparer / liberer pour un lot de documents
        classer(texte, registre) -> ReponseMoteur (ne leve jamais d'exception)."""
    nom = "abstrait"

    def verifier(self):
        return True, None

    def lot(self):
        return nullcontext(self)

    def classer(self, texte: str, registre: dict) -> ReponseMoteur:
        raise NotImplementedError


def decrire_categories(registre: dict) -> str:
    """Lignes du prompt, construites depuis le registre (jamais ecrites en dur)."""
    lignes = []
    for cat in registre["categories"]:
        sous = [sd["nom"] for sd in cat.get("sous_dossiers", []) if sd["nom"] != DOSSIER_AUTRES]
        details = []
        if sous:
            details.append("sous-types : " + ", ".join(sous))
        details.append("informations : " + ", ".join(cat["champs"]))
        lignes.append(f"- {cat['nom']} ({' ; '.join(details)})")
    return "\n".join(lignes)


def decrire_regles(registre: dict) -> str:
    regles = [f"- {r['description']}" for r in registre["regles_metier"] if r.get("description")]
    return "\n".join(regles) or "- (aucune)"


def schema_classification(registre: dict) -> dict:
    """Schema JSON impose au moteur : le type est une categorie du registre ou "autre"."""
    return {"type": "object",
            "properties": {
                "type_document": {"type": "string",
                                  "enum": [c["nom"] for c in registre["categories"]] + [AUTRE]},
                "nom_propose": {"type": ["string", "null"]}},
            "required": ["type_document", "nom_propose"]}


class MoteurPhi4(MoteurClassification):
    """Phi-4-mini en local via Ollama (src/llm.py) et prompts/classification.txt."""
    nom = "phi4-mini"

    def __init__(self, client=None, nom_prompt: str = "classification"):
        from src.llm import ClientOllama
        self.client = client or ClientOllama()
        self.nom_prompt = nom_prompt

    def verifier(self):
        return self.client.verifier()

    def lot(self):
        return self.client.modele_charge()     # garde le modele charge, puis le decharge

    def classer(self, texte: str, registre: dict) -> ReponseMoteur:
        rep = self.client.appeler(
            self.nom_prompt, texte, schema_classification(registre),
            variables={"categories": decrire_categories(registre),
                       "regles": decrire_regles(registre)})
        if not rep.ok:
            return ReponseMoteur(ok=False, erreur=rep.erreur, duree_s=rep.duree_s,
                                 alertes=rep.alertes)
        return ReponseMoteur(ok=True, categorie=rep.donnees["type_document"],
                             nom_propose=rep.donnees.get("nom_propose"),
                             duree_s=rep.duree_s, alertes=rep.alertes)


# Moteurs disponibles, par nom. Ajouter un moteur = ajouter une ligne ici.
MOTEURS = {"phi4-mini": MoteurPhi4}


def creer_moteur(nom: str = MOTEUR_CLASSIFICATION, **options) -> MoteurClassification:
    if nom not in MOTEURS:
        raise ValueError(f"moteur de classification inconnu : {nom!r} "
                         f"(disponibles : {', '.join(MOTEURS)})")
    return MOTEURS[nom](**options)


# ===========================================================================
# 2. Categorie decouverte
# ===========================================================================
def normaliser_nom(nom) -> str:
    """'Bulletin de Paie' -> 'bulletin_de_paie' (format des noms du registre).
    Vide, reserve (autre, autres, a_valider) ou inutilisable -> ''."""
    if not isinstance(nom, str):
        return ""
    t = re.sub(r"[^a-z0-9]+", "_", normaliser(nom)).strip("_")
    t = re.sub(r"^[0-9_]+", "", t)                  # un nom commence par une lettre
    if t in NOMS_RESERVES | {AUTRE}:
        return ""
    return t[:40]


def categorie_proche(nom: str, registre: dict):
    """Categorie existante trop proche du nom propose, ou None.
    Proche = forte ressemblance avec le nom, son singulier ou le nom du dossier,
    ou meme premier mot que le singulier (ex. 'facture_d_avoir' -> factures)."""
    meilleure, score_max = None, 0.0
    premier_mot = nom.split("_")[0]
    for cat in registre["categories"]:
        formes = {cat["nom"], cat["nom"].rstrip("s"), cat["dossier"].lower()}
        score = max(difflib.SequenceMatcher(None, nom, f).ratio() for f in formes)
        if premier_mot == cat["nom"].rstrip("s"):
            score = max(score, 1.0)
        if score > score_max:
            meilleure, score_max = cat["nom"], score
    return meilleure if score_max >= SEUIL_SIMILARITE else None


# ===========================================================================
# 3. Classification
# ===========================================================================
@dataclass
class ResultatClassification:
    categorie: str = None                 # categorie retenue (registre), ou None
    sous_dossier: str = None
    confiance: float = CONFIANCE_DOUTE
    necessite_validation_humaine: bool = True
    categorie_proposee: str = None        # nom NOUVEAU propose (categorie decouverte)
    raisons: list = field(default_factory=list)
    signaux: SignauxQualite = field(default_factory=SignauxQualite)
    verdict_regles: str = None
    categorie_regles: str = None
    categorie_moteur: str = None          # reponse du moteur (ou None s'il n'a pas repondu)
    moteur_appele: bool = False
    duree_moteur_s: float = 0.0
    alertes: list = field(default_factory=list)


def classer(texte: str, registre: dict = None, moteur: MoteurClassification = None,
            texte_natif: str = None, confiances_ocr=()) -> ResultatClassification:
    """Classe un document. Ne leve pas d'exception pour une panne du moteur."""
    registre = registre or registre_par_defaut()
    v = analyser(texte, registre, texte_natif=texte_natif, confiances_ocr=confiances_ocr)
    r = ResultatClassification(signaux=v.signaux, verdict_regles=v.verdict,
                               categorie_regles=v.categorie)
    r.raisons.append(f"mots-cles : {v.verdict}" + (f" ({v.categorie})" if v.categorie else ""))

    if v.verdict == REGLE_METIER:
        # Seule la regle metier suffit sans moteur
        r.categorie, r.confiance = v.categorie, CONFIANCE_SURE
        r.raisons.append("regle metier : suffisante sans moteur")
    else:
        moteur = moteur or creer_moteur()
        debut = time.perf_counter()
        try:
            rep = moteur.classer(texte, registre)
        except Exception as err:                     # un moteur mal ecrit ne bloque pas le lot
            rep = ReponseMoteur(ok=False, erreur=f"moteur en erreur ({type(err).__name__})")
        r.moteur_appele = True
        r.duree_moteur_s = round(rep.duree_s or time.perf_counter() - debut, 2)
        r.alertes += rep.alertes
        _appliquer_regle_a(r, v, rep, registre)

    # Signal de qualite : OCR peu fiable -> validation humaine obligatoire
    s = v.signaux
    ocr_douteux = (s.confiance_ocr_moyenne is not None
                   and s.confiance_ocr_moyenne < SEUIL_CONFIANCE_OCR_DOCUMENT)
    if ocr_douteux:
        r.raisons.append(f"confiance OCR moyenne {s.confiance_ocr_moyenne:.2f} "
                         f"< {SEUIL_CONFIANCE_OCR_DOCUMENT} : validation obligatoire")
    # Signal de qualite : document en arabe (francais uniquement pour le MVP)
    arabe = s.part_arabe > SEUIL_PART_ARABE
    if arabe:
        r.raisons.append(f"lettres arabes {s.part_arabe:.0%} > {SEUIL_PART_ARABE:.0%} : "
                         "validation obligatoire")

    cat = trouver_categorie(registre, r.categorie) if r.categorie else None
    r.sous_dossier = choisir_sous_dossier(cat, texte) if cat else None
    r.necessite_validation_humaine = (r.confiance < SEUIL_CONFIANCE or ocr_douteux or arabe
                                      or r.categorie is None
                                      or r.categorie_proposee is not None)
    return r


def _appliquer_regle_a(r: ResultatClassification, v, rep: ReponseMoteur, registre: dict):
    """Confiance selon le verdict des mots-cles et la reponse du moteur."""
    if not rep.ok:
        r.categorie, r.confiance = v.categorie, CONFIANCE_DOUTE
        r.raisons.append(f"moteur indisponible : {rep.erreur}")
        return
    r.categorie_moteur = rep.categorie

    if rep.categorie == AUTRE:
        r.categorie, r.confiance = v.categorie, CONFIANCE_DOUTE
        nom = normaliser_nom(rep.nom_propose)
        if not nom:
            r.raisons.append("moteur : autre, sans nom exploitable")
            return
        proche = categorie_proche(nom, registre)
        if proche:
            r.categorie = proche                  # on garde l'existante, mais on doute
            r.raisons.append(f"moteur : autre ; nom propose proche de {proche}")
        else:
            r.categorie_proposee = nom
            r.raisons.append("moteur : autre ; nouvelle categorie proposee")
        return

    accord = rep.categorie == v.categorie
    # Seul "net" + accord range automatiquement. "faible" + accord reste a 0.60 :
    # un document qui MENTIONNE un type (ex. un CV qui cite un diplome) ne doit pas
    # etre range dans ce type sans validation humaine.
    r.confiance = CONFIANCE_SURE if (v.verdict == NET and accord) else CONFIANCE_DOUTE
    # En desaccord, on garde la categorie des mots-cles quand elle existe (deterministe) ;
    # sinon celle du moteur. L'humain voit les deux dans les raisons.
    r.categorie = v.categorie if v.categorie else rep.categorie
    r.raisons.append(f"moteur : {rep.categorie} ({'accord' if accord else 'desaccord'})")
