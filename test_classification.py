"""
test_classification.py - Test de la classification locale : regex d'abord, Phi-4-mini si besoin.

Regle de confiance validee (point A) :
    regex nettes (1 seul type, >= 2 indices)     -> 0.95, pas d'appel au LLM
    regex et Phi-4-mini d'accord                 -> 0.90
    desaccord, egalite, ou aucun indice regex    -> 0.60
    confiance >= 0.90 -> rangement automatique, sinon -> A_Valider/

Lancement (venv active, Ollama demarre, depuis OCR_PROJECT) :
    python test_classification.py              regex, puis LLM seulement si necessaire
    python test_classification.py --sans-llm   regex seulement (instantane, sans Ollama)
    python test_classification.py --forcer-llm LLM sur chaque document (lent : evalue le LLM)

Tous les documents de test sont INVENTES : aucune donnee reelle.
"""

# --- Imports ---------------------------------------------------------------
import json          # lire la reponse JSON du modele
import re            # expressions regulieres (les "regles")
import sys           # options de la ligne de commande, arret propre
import time          # chronometrer chaque document
import unicodedata   # retirer les accents avant d'appliquer les regles

import requests      # parler au serveur Ollama (http://localhost:11434)

# --- Reglages --------------------------------------------------------------
OLLAMA_URL = "http://localhost:11434"   # adresse locale : rien ne sort du PC
MODELE = "phi4-mini"
TYPES = ["facture", "diplome", "attestation", "contrat", "autre"]
SEUIL = 0.90            # >= 0.90 -> rangement automatique
MAX_CARACTERES = 3000   # ~900 tokens : tient dans num_ctx=2048 avec la consigne
TIMEOUT_LLM_S = 300     # sur un i3, on laisse jusqu'a 5 min avant d'abandonner

SANS_LLM = "--sans-llm" in sys.argv
FORCER_LLM = "--forcer-llm" in sys.argv

# --- 1. Les regles regex ---------------------------------------------------
# Chaque motif trouve dans le texte = 1 indice pour ce type.
# Les motifs sont ecrits SANS accents et en minuscules, car le texte est
# normalise avant (voir normaliser()) : "Diplôme", "DIPLOME", "diplome" -> "diplome".
REGLES = {
    "facture": [
        r"\bfacture\b",
        r"\bnote d.honoraires\b",        # le "." accepte ' et ’
        r"\btva\b",
        r"\bttc\b",
        r"\b(montant|total) ht\b",
    ],
    "diplome": [
        r"\bdiplome\b",
        r"\b(deug|licence|master|doctorat|baccalaureat)\b",
        r"\bmention\b",
        r"\b(confere|decerne)\b",
        r"\bgrade\b",
    ],
    "attestation": [
        r"\battestation\b",
        r"\b(atteste|certifie)\b",
        r"\bpour servir et valoir\b",
        r"\bde (travail|scolarite|salaire)\b",
    ],
    "contrat": [
        r"\bcontrat\b",
        r"\bentre les soussignes\b",
        r"\barticle \d+\b",
        r"\bles parties\b",
    ],
}


# Regles metier : elles passent AVANT le comptage des indices et tranchent seules.
# Chaque regle = (liste de motifs qui doivent TOUS etre presents, type impose).
# "attestation de reussite" seule ne suffit pas : on exige aussi un nom de diplome,
# pour ne pas envoyer dans Diplomes/ une attestation de reussite a une formation.
REGLES_METIER = [
    ([r"\battestation de reussite\b",
      r"\b(diplome|deug|licence|master|doctorat|baccalaureat)\b"], "diplome"),
]
QUALITES_SURES = ("net", "regle metier")   # verdicts qui valent 0.95


def normaliser(texte: str) -> str:
    """Minuscules + suppression des accents : les regles restent simples."""
    decompose = unicodedata.normalize("NFKD", texte)          # é -> e + accent
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    return sans_accents.lower()


def scores_regex(texte: str) -> dict:
    """Compte, pour chaque type, combien de motifs sont presents dans le texte."""
    t = normaliser(texte)
    return {typ: sum(1 for motif in motifs if re.search(motif, t))
            for typ, motifs in REGLES.items()}


def regle_metier(texte: str):
    """Renvoie le type impose par une regle metier, ou None si aucune ne s'applique."""
    t = normaliser(texte)
    for motifs, typ in REGLES_METIER:
        if all(re.search(motif, t) for motif in motifs):
            return typ
    return None


def verdict_regex(scores: dict):
    """Renvoie (type candidat ou None, qualite du verdict)."""
    classes = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (type1, s1), (_, s2) = classes[0], classes[1]
    if s1 == 0:
        return None, "aucun indice"
    if s1 >= 2 and s2 == 0:
        return type1, "net"          # un seul type, au moins 2 indices
    if s1 > s2:
        return type1, "faible"       # un type devant, mais concurrence
    return None, "egalite"           # impossible de departager


# --- 2. L'appel a Phi-4-mini -----------------------------------------------
CONSIGNE = """Tu classes des documents administratifs en francais pour un cabinet comptable.
Types possibles :
- facture : facture ou note d'honoraires (fournisseur, montants HT, TVA, TTC)
- diplome : diplome ou attestation de reussite d'un diplome (DEUG, Licence, Master, Doctorat, Baccalaureat)
- attestation : attestation delivree par un organisme (travail, scolarite, salaire...)
- contrat : contrat entre des parties (objet, articles, signatures)
- autre : aucun de ces types
Donne uniquement le type du document ci-dessous.

DOCUMENT :
<<<
{texte}
>>>"""


def verifier_ollama():
    """Avant tout, on verifie que le serveur tourne et que le modele est la."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
    except requests.exceptions.ConnectionError:
        sys.exit("[ERREUR] Ollama ne repond pas. Ouvre l'application Ollama "
                 "(menu Demarrer), puis relance.")
    noms = [m.get("name", "") for m in r.json().get("models", [])]
    if not any(n.startswith(MODELE) for n in noms):
        sys.exit(f"[ERREUR] Modele {MODELE} absent. Lance : ollama pull {MODELE}")


def classer_llm(texte: str):
    """Demande le type a Phi-4-mini. Renvoie (type, statistiques de temps)."""
    corps = {
        "model": MODELE,
        "prompt": CONSIGNE.format(texte=texte[:MAX_CARACTERES]),
        # Sortie structuree : Ollama FORCE une reponse JSON conforme a ce schema,
        # et le type ne peut etre qu'une des 5 valeurs de TYPES.
        "format": {
            "type": "object",
            "properties": {"type_document": {"type": "string", "enum": TYPES}},
            "required": ["type_document"],
        },
        "stream": False,       # on attend la reponse complete
        "keep_alive": 0,       # decharge le modele tout de suite : RAM liberee
        "options": {
            "num_ctx": 2048,   # contexte limite -> moins de RAM
            "num_gpu": 0,      # CPU uniquement
            "temperature": 0,  # meme texte -> meme reponse
        },
    }
    r = requests.post(f"{OLLAMA_URL}/api/generate", json=corps, timeout=TIMEOUT_LLM_S)
    r.raise_for_status()                       # erreur HTTP -> exception
    d = r.json()
    typ = json.loads(d["response"]).get("type_document")
    if typ not in TYPES:
        raise ValueError(f"reponse hors liste : {typ!r}")
    ns = 1e9  # Ollama compte en nanosecondes
    stats = {
        "chargement_s": round(d.get("load_duration", 0) / ns, 1),
        "lecture_s": round(d.get("prompt_eval_duration", 0) / ns, 1),
        "tokens_lus": d.get("prompt_eval_count"),
    }
    return typ, stats


# --- 3. La decision (regle A) ----------------------------------------------
def classer(texte: str) -> dict:
    scores = scores_regex(texte)
    candidat, qualite = verdict_regex(scores)
    # Une regle metier l'emporte sur le simple comptage des indices
    type_metier = regle_metier(texte)
    if type_metier is not None:
        candidat, qualite = type_metier, "regle metier"
    res = {"scores": scores, "regex": candidat, "qualite": qualite,
           "llm": None, "stats": None, "erreur": None}

    # Cas 1 : regex nettes ou regle metier -> 0.95 sans LLM
    # (sauf si on force le LLM pour l'evaluer)
    if qualite in QUALITES_SURES and not FORCER_LLM:
        res.update(type=candidat, confiance=0.95)
        return res

    # Cas 2 : LLM desactive -> impossible de confirmer
    if SANS_LLM:
        res.update(type=candidat, confiance=0.60)
        return res

    # Cas 3 : on demande a Phi-4-mini
    try:
        res["llm"], res["stats"] = classer_llm(texte)
    except Exception as err:          # panne, delai, JSON invalide...
        res["erreur"] = str(err)
        res.update(type=candidat, confiance=0.60)
        return res

    if candidat is not None and res["llm"] == candidat:
        confiance = 0.95 if qualite in QUALITES_SURES else 0.90
    else:
        confiance = 0.60              # desaccord, egalite ou aucun indice
    res.update(type=res["llm"], confiance=confiance)
    return res


# --- 4. Documents de test (100 % inventes) ---------------------------------
DOCUMENTS = [
    {"nom": "facture_nette", "attendu": "facture", "texte": """FACTURE N° FA-2026-00042
Date : 15/09/2026 - Fournisseur : Société Exemple SARL, Casablanca
Désignation : Maintenance informatique septembre 2026
Montant HT : 1 000,00 DH - TVA 20 % : 200,00 DH - Total TTC : 1 200,00 DH"""},

    {"nom": "attestation_travail", "attendu": "attestation", "texte": """ATTESTATION DE TRAVAIL
Je soussigné, directeur de la société Exemple SARL, atteste que M. Titulaire Exemple
est employé en qualité de comptable depuis le 01/03/2022.
Cette attestation est délivrée pour servir et valoir ce que de droit."""},

    {"nom": "contrat_bail", "attendu": "contrat", "texte": """CONTRAT DE BAIL COMMERCIAL
Entre les soussignés : Société Bailleur Exemple et Société Preneur Exemple.
Article 1 : Objet - location d'un local commercial à Casablanca.
Article 2 : Les parties conviennent d'un loyer mensuel payable d'avance."""},

    {"nom": "diplome_licence", "attendu": "diplome", "texte": """ROYAUME DU MAROC - UNIVERSITÉ EXEMPLE
DIPLÔME DE LICENCE EN SCIENCES ÉCONOMIQUES
Le président de l'université confère à Titulaire Exemple
le diplôme de Licence, mention Assez Bien, session de juin 2020."""},

    {"nom": "attestation_reussite_deug", "attendu": "diplome", "texte": """ATTESTATION DE RÉUSSITE
Le doyen de la Faculté Exemple atteste que l'étudiant Titulaire Exemple
a obtenu le Diplôme d'Études Universitaires Générales (DEUG), mention Bien.
Délivrée pour servir et valoir ce que de droit."""},

    {"nom": "recu_paiement", "attendu": "autre", "texte": """REÇU N° 118
Reçu de Monsieur Client Exemple la somme de cinq cents dirhams (500 DH)
en espèces, pour la réservation d'une salle le 20/09/2026.
Fait à Casablanca, le 18/09/2026."""},
]


# --- 5. Execution et rapport -----------------------------------------------
def main():
    mode = ("regex seulement" if SANS_LLM else
            "LLM force sur chaque document" if FORCER_LLM else
            "regex puis LLM si necessaire")
    print(f"Mode : {mode}\n")
    if not SANS_LLM:
        verifier_ollama()

    bilan = {"bien_ranges": 0, "a_valider": 0, "mal_ranges": 0}
    debut_total = time.perf_counter()

    for doc in DOCUMENTS:
        t0 = time.perf_counter()
        res = classer(doc["texte"])
        duree = time.perf_counter() - t0

        auto = res["confiance"] >= SEUIL
        decision = f"RANGE -> {res['type']}" if auto else "A_Valider/"
        if not auto:
            bilan["a_valider"] += 1
            verdict = "prudent (humain)"
        elif res["type"] == doc["attendu"]:
            bilan["bien_ranges"] += 1
            verdict = "OK"
        else:
            bilan["mal_ranges"] += 1
            verdict = "ERREUR : mal range !"

        print(f"=== {doc['nom']}  (attendu : {doc['attendu']})")
        print(f"  Indices regex : {res['scores']}  -> {res['regex']} ({res['qualite']})")
        if res["llm"] or res["erreur"]:
            print(f"  Phi-4-mini    : {res['llm']}  {res['stats'] or ''}")
        if res["erreur"]:
            print(f"  Erreur LLM    : {res['erreur']}")
        print(f"  Confiance     : {res['confiance']:.2f}  -> {decision}   [{verdict}]")
        print(f"  Temps         : {duree:.1f} s\n")

    print("=" * 60)
    print(f"Bien rangés automatiquement : {bilan['bien_ranges']}")
    print(f"Envoyés en validation       : {bilan['a_valider']}")
    print(f"MAL rangés (grave)          : {bilan['mal_ranges']}")
    print(f"Temps total                 : {time.perf_counter() - debut_total:.1f} s")


if __name__ == "__main__":
    main()
