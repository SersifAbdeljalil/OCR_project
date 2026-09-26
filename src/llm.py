"""
llm.py - Client UNIQUE pour Phi-4-mini via Ollama (http://localhost:11434).

    - sortie JSON imposee par un schema (option "format" d'Ollama) ;
    - reglages : num_ctx 2048, num_gpu 0 (CPU), temperature 0 ;
    - keep_alive = 0 par defaut (modele decharge apres chaque appel) ; pour un LOT,
      `with client.modele_charge():` garde le modele en memoire puis le decharge
      explicitement a la fin (ordre du pipeline : OCR, puis tous les appels LLM,
      puis dechargement) ;
    - le texte du document est tronque pour tenir dans le contexte, en gardant
      le DEBUT (la zone titre en fait partie) ;
    - robustesse : verification du serveur et du modele, delai maximum par appel,
      JSON invalide -> UN nouvel essai, puis echec propre. Aucune exception ne
      remonte : le lot continue ;
    - prompts : fichiers texte dans prompts/ (emplacement $texte).

CONFIDENTIALITE : aucun texte de document ni aucune reponse n'est affiche ni
journalise. Les erreurs ne contiennent que le type de probleme.
"""

# --- Imports ---------------------------------------------------------------
import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

import requests

from src.config import RACINE

# --- Reglages --------------------------------------------------------------
OLLAMA_URL = "http://localhost:11434"
MODELE = "phi4-mini"
NUM_CTX = 2048
OPTIONS = {"num_ctx": NUM_CTX, "num_gpu": 0, "temperature": 0}
DELAI_APPEL_S = 300          # i3 sur CPU : jusqu'a 5 min avant d'abandonner un appel
DELAI_VERIFICATION_S = 5
KEEP_ALIVE_LOT = "30m"       # pendant un lot ; le dechargement final est explicite
DOSSIER_PROMPTS = RACINE / "prompts"

# Budget du texte : estimation PRUDENTE de 2,5 caracteres par token. Un texte
# francais propre fait plutot 3,5 a 4 caracteres par token, mais les chiffres,
# les accents et les erreurs d'OCR en consomment davantage.
CARACTERES_PAR_TOKEN = 2.5
RESERVE_REPONSE_TOKENS = 400  # place laissee a la reponse JSON
MARQUE_TRONQUE = "\n[... texte tronque ...]"


# --- Resultat d'un appel ---------------------------------------------------
@dataclass
class ReponseLLM:
    ok: bool
    donnees: dict = field(default=None, repr=False)  # JAMAIS affiche (valeurs extraites)
    erreur: str = None           # type de probleme, sans texte du document
    essais: int = 0
    duree_s: float = 0.0         # duree totale mesuree cote Python
    chargement_s: float = 0.0    # chargement du modele (0 s'il etait deja en memoire)
    tokens_lus: int = 0          # tokens du prompt reellement lus par le modele
    tokens_generes: int = 0
    texte_tronque: bool = False
    alertes: list = field(default_factory=list)


# --- 1. Prompts et texte ---------------------------------------------------
def charger_prompt(nom: str, dossier: Path = DOSSIER_PROMPTS) -> Template:
    """prompts/<nom>.txt -> modele de texte avec l'emplacement $texte."""
    return Template((Path(dossier) / f"{nom}.txt").read_text(encoding="utf-8"))


def compacter(texte: str) -> str:
    """Espaces multiples -> un espace ; 3 sauts de ligne ou plus -> 2 (economise des tokens)."""
    texte = re.sub(r"[ \t  ]+", " ", texte or "")
    texte = re.sub(r" *\n *", "\n", texte)
    return re.sub(r"\n{3,}", "\n\n", texte).strip()


def budget_caracteres(consigne: str, num_ctx: int = NUM_CTX) -> int:
    """Caracteres disponibles pour le document, une fois la consigne et la
    reponse comptees (estimation prudente)."""
    tokens_consigne = len(consigne) / CARACTERES_PAR_TOKEN
    tokens_libres = num_ctx - tokens_consigne - RESERVE_REPONSE_TOKENS
    return max(0, int(tokens_libres * CARACTERES_PAR_TOKEN))


def tronquer(texte: str, budget: int):
    """Garde le DEBUT du texte (zone titre comprise), coupe a une fin de ligne.
    Renvoie (texte, tronque)."""
    texte = compacter(texte)
    if len(texte) <= budget:
        return texte, False
    place = max(0, budget - len(MARQUE_TRONQUE))
    coupe = texte[:place]
    fin_de_ligne = coupe.rfind("\n")
    if fin_de_ligne > place * 0.8:                 # evite de couper au milieu d'une ligne
        coupe = coupe[:fin_de_ligne]
    return coupe + MARQUE_TRONQUE, True


def preparer_prompt(nom: str, texte_document: str, dossier: Path = DOSSIER_PROMPTS,
                    variables: dict = None, num_ctx: int = NUM_CTX):
    """Prompt complet : consigne du fichier (avec ses autres emplacements, ex.
    $categories) + texte tronque. Renvoie (prompt, tronque)."""
    modele = charger_prompt(nom, dossier)
    variables = dict(variables or {})
    consigne = modele.substitute(texte="", **variables)   # budget calcule sans le texte
    texte, tronque = tronquer(texte_document, budget_caracteres(consigne, num_ctx))
    return modele.substitute(texte=texte, **variables), tronque


# --- 2. Verification de la reponse ------------------------------------------
def conforme_au_schema(donnees, schema: dict) -> bool:
    """Controle simple : objet JSON, cles obligatoires presentes, valeurs
    d'enumeration respectees (niveau 1)."""
    if not isinstance(donnees, dict):
        return False
    if any(cle not in donnees for cle in schema.get("required", [])):
        return False
    for cle, regle in schema.get("properties", {}).items():
        if "enum" in regle and cle in donnees and donnees[cle] not in regle["enum"]:
            return False
    return True


# --- 3. Le client ------------------------------------------------------------
class ClientOllama:
    """Client Ollama. `session` permet aux tests de simuler le serveur."""

    def __init__(self, url: str = OLLAMA_URL, modele: str = MODELE,
                 delai_s: float = DELAI_APPEL_S, keep_alive=0, session=None,
                 num_gpu: int = OPTIONS["num_gpu"], num_ctx: int = NUM_CTX):
        self.url, self.modele, self.delai_s = url.rstrip("/"), modele, delai_s
        self.keep_alive = keep_alive
        self.session = session or requests.Session()
        self.options = {**OPTIONS, "num_gpu": num_gpu, "num_ctx": num_ctx}

    @classmethod
    def depuis_profil(cls, profil: dict, **autres):
        """Client regle selon le profil de machine (config/machine.json)."""
        return cls(url=profil["ollama_url"], modele=profil["modele_llm"],
                   delai_s=profil["delai_llm_s"], num_gpu=profil["num_gpu"],
                   num_ctx=profil["num_ctx"], **autres)

    # -- Etat du serveur --
    def verifier(self):
        """(True, None) si Ollama repond et que le modele est installe,
        sinon (False, raison)."""
        try:
            r = self.session.get(f"{self.url}/api/tags", timeout=DELAI_VERIFICATION_S)
            r.raise_for_status()
            noms = [m.get("name", "") for m in r.json().get("models", [])]
        except requests.exceptions.ConnectionError:
            return False, "Ollama ne repond pas (application arretee ?)"
        except Exception as err:
            return False, f"Ollama : reponse invalide ({type(err).__name__})"
        if not any(n == self.modele or n.startswith(f"{self.modele}:") for n in noms):
            return False, f"modele {self.modele} absent (ollama pull {self.modele})"
        return True, None

    def modeles_charges(self) -> list:
        """Noms des modeles actuellement en memoire (equivalent de `ollama ps`).
        Sert a verifier qu'Ollama est vide avant de lancer l'OCR."""
        try:
            r = self.session.get(f"{self.url}/api/ps", timeout=DELAI_VERIFICATION_S)
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]
        except Exception:
            return []

    # -- Chargement / dechargement --
    def precharger(self, keep_alive=KEEP_ALIVE_LOT) -> float:
        """Charge le modele sans rien lui demander ; renvoie le temps de chargement (s)."""
        r = self.session.post(f"{self.url}/api/generate",
                              json={"model": self.modele, "keep_alive": keep_alive},
                              timeout=self.delai_s)
        r.raise_for_status()
        return r.json().get("load_duration", 0) / 1e9

    def decharger(self) -> bool:
        """Retire le modele de la memoire (keep_alive = 0). Ne leve jamais."""
        try:
            r = self.session.post(f"{self.url}/api/generate",
                                  json={"model": self.modele, "keep_alive": 0},
                                  timeout=DELAI_VERIFICATION_S * 6)
            r.raise_for_status()
            return True
        except Exception:
            return False

    @contextmanager
    def modele_charge(self, keep_alive=KEEP_ALIVE_LOT):
        """Pendant le bloc `with`, le modele reste en memoire entre les appels ;
        a la sortie (meme en cas d'erreur), il est decharge et keep_alive revient a 0."""
        precedent = self.keep_alive
        self.keep_alive = keep_alive
        try:
            try:
                self.precharger(keep_alive)
            except Exception:
                pass                          # le premier appel chargera le modele
            yield self
        finally:
            self.keep_alive = precedent
            self.decharger()

    # -- Appels --
    def _un_appel(self, prompt: str, schema: dict) -> dict:
        corps = {"model": self.modele, "prompt": prompt, "format": schema,
                 "stream": False, "keep_alive": self.keep_alive, "options": dict(self.options)}
        r = self.session.post(f"{self.url}/api/generate", json=corps, timeout=self.delai_s)
        r.raise_for_status()
        return r.json()

    def generer_json(self, prompt: str, schema: dict) -> ReponseLLM:
        """Envoie le prompt ; JSON invalide ou non conforme -> un seul nouvel essai.
        Ne leve jamais d'exception."""
        rep = ReponseLLM(ok=False)
        debut = time.perf_counter()
        for essai in (1, 2):
            rep.essais = essai
            try:
                brut = self._un_appel(prompt, schema)
            except Exception as err:                # delai, connexion, HTTP...
                rep.erreur = f"appel echoue ({type(err).__name__})"
                break                               # pas de nouvel essai : inutile d'attendre encore
            rep.chargement_s += brut.get("load_duration", 0) / 1e9
            rep.tokens_lus = brut.get("prompt_eval_count", 0) or 0
            rep.tokens_generes = brut.get("eval_count", 0) or 0
            try:
                donnees = json.loads(brut.get("response", ""))
            except (TypeError, ValueError):
                donnees = None
            if conforme_au_schema(donnees, schema):
                rep.ok, rep.donnees, rep.erreur = True, donnees, None
                break
            rep.erreur = "json invalide ou non conforme au schema"
        rep.duree_s = round(time.perf_counter() - debut, 2)
        # Ollama tronque lui-meme un prompt trop long (sans le dire) : si le prompt
        # depasse la place prevue, notre estimation de 2,5 caracteres/token etait fausse.
        if rep.tokens_lus > self.options["num_ctx"] - RESERVE_REPONSE_TOKENS:
            rep.alertes.append("contexte presque plein : une partie du prompt a pu etre perdue")
        return rep

    def appeler(self, nom_prompt: str, texte_document: str, schema: dict,
                dossier_prompts: Path = DOSSIER_PROMPTS, variables: dict = None) -> ReponseLLM:
        """Prompt du fichier prompts/<nom_prompt>.txt + texte tronque -> JSON."""
        try:
            prompt, tronque = preparer_prompt(nom_prompt, texte_document, dossier_prompts,
                                              variables, self.options["num_ctx"])
        except Exception as err:                    # fichier absent, emplacement inconnu...
            return ReponseLLM(ok=False, erreur=f"prompt invalide ({type(err).__name__})")
        rep = self.generer_json(prompt, schema)
        rep.texte_tronque = tronque
        if tronque:
            rep.alertes.append("texte tronque pour tenir dans le contexte")
        return rep
