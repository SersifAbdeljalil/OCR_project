"""
test_jev.py - Premier appel a JEV (TypeSafe) via l'API Decisions d'OpenRouter.

Ce que fait ce script :
  1. lit la cle API dans le fichier .env (sans jamais l'afficher en entier)
  2. envoie a JEV un texte de facture FICTIVE
  3. pose 2 questions : type de document (Choice) + besoin d'un humain (Noul)
  4. affiche les reponses, le cout de l'appel, et la decision de routage

Lancement (venv active, depuis C:\\Users\\abdul\\Desktop\\OCR_PROJECT) :
    python test_jev.py        -> resultat resume
    python test_jev.py -v     -> resultat resume + reponse brute complete
"""

# --- Imports ---------------------------------------------------------------
import json          # pour afficher la reponse brute de facon lisible
import os            # pour lire les variables d'environnement (la cle)
import sys           # pour arreter le script proprement en cas d'erreur

import requests                  # envoie la requete HTTP vers OpenRouter
from dotenv import load_dotenv   # copie le contenu de .env dans l'environnement

# --- Reglages --------------------------------------------------------------
URL = "https://openrouter.ai/api/alpha/decisions"  # endpoint officiel de JEV
MODEL = "typesafe/jev-1.13"  # version figee : nos seuils resteront valables
SEUIL_CONFIANCE = 0.9        # regle validee : >= 0.9 -> rangement automatique
SEUIL_HUMAIN = 0.5           # provisoire : au-dessus -> validation humaine
TIMEOUT_S = 30               # au-dela de 30 s sans reponse, on abandonne

# --- 1. Charger la cle API -------------------------------------------------
load_dotenv()                            # lit le fichier .env du dossier courant
cle = os.getenv("OPENROUTER_API_KEY")    # recupere la valeur de la variable

if not cle or not cle.startswith("sk-or-"):
    # Cle absente ou mal formee : inutile d'appeler l'API.
    sys.exit("[ERREUR] OPENROUTER_API_KEY introuvable ou invalide dans .env "
             "(lance le script depuis le dossier OCR_PROJECT).")

# On n'affiche que le debut et la fin : preuve que la cle est lue, sans la divulguer.
print(f"Cle chargee      : {cle[:9]}...{cle[-4:]}")

# --- 2. Le document a tester -----------------------------------------------
# Facture 100 % INVENTEE : aucune donnee reelle ne doit partir vers OpenRouter
# tant que le masquage (CIN, RIB, ICE) n'est pas developpe et teste.
TEXTE_TEST = """FACTURE N° FA-2026-00042
Date : 15/09/2026
Fournisseur : Societe Exemple SARL, Casablanca
Client : Cabinet Test
Designation : Maintenance informatique - septembre 2026
Montant HT : 1 000,00 DH
TVA 20 % : 200,00 DH
Total TTC : 1 200,00 DH"""

# --- 3. La requete envoyee a JEV -------------------------------------------
payload = {
    "model": MODEL,
    # "state" = les informations que JEV doit juger. On y place le texte
    # sous le nom "document", qu'on cite ensuite entre `backticks`.
    "state": {"document": TEXTE_TEST},
    # Les questions sont posees en parallele sur le meme state.
    "questions": {
        # Question Choice : JEV choisit UNE option parmi les "criteria".
        # Chaque option = une cle (ce que notre code recevra) + une description
        # (ce qui aide JEV a decider). L'option "autre" evite de forcer un
        # mauvais choix quand rien ne correspond.
        "type_document": {
            "type": "choice",
            "instructions": "Quel est le type du document `document` ?",
            "criteria": {
                "facture": "Facture ou note d'honoraires : fournisseur, numero, "
                           "montants HT, TVA et TTC.",
                "diplome": "Diplome scolaire ou universitaire : DEUG, Licence, "
                           "Master, Doctorat, Baccalaureat.",
                "attestation": "Attestation delivree par un organisme : travail, "
                               "scolarite, salaire ou autre.",
                "contrat": "Contrat entre des parties : objet, obligations, duree, "
                           "signatures.",
                "autre": "Aucun des types ci-dessus.",
            },
        },
        # Question Noul : JEV renvoie la probabilite que la reponse soit OUI.
        # "true" / "false" decrivent concretement le oui et le non.
        "validation_humaine": {
            "type": "noul",
            "instructions": "Le document `document` necessite-t-il une "
                            "verification par un humain ?",
            "criteria": {
                "true": "Texte illisible ou incoherent, plusieurs documents "
                        "melanges, type ambigu, ou texte principalement en arabe.",
                "false": "Texte lisible, un seul document, type clairement "
                         "identifiable, redige en francais.",
            },
        },
    },
}

# --- 4. Envoi de la requete ------------------------------------------------
try:
    reponse = requests.post(
        URL,
        headers={
            "Authorization": f"Bearer {cle}",    # authentification OpenRouter
            "Content-Type": "application/json",  # on envoie du JSON
        },
        json=payload,       # requests convertit le dictionnaire en JSON UTF-8
        timeout=TIMEOUT_S,
    )
except requests.exceptions.RequestException as err:
    # Pas d'Internet, pare-feu, delai depasse...
    sys.exit(f"[ERREUR RESEAU] {err}")

# --- 5. Gestion des erreurs HTTP -------------------------------------------
ERREURS = {
    400: "Requete mal formee (verifier le contenu de 'payload').",
    401: "Cle API absente ou invalide (verifier .env).",
    402: "Credits insuffisants : ajouter des credits sur openrouter.ai (menu Credits).",
    404: "Modele introuvable (verifier MODEL).",
    413: "Texte trop long (limite : 32 000 tokens).",
    429: "Trop de requetes : attendre un peu et relancer.",
}
if reponse.status_code != 200:
    explication = ERREURS.get(reponse.status_code, "Erreur inattendue.")
    print(f"[ERREUR HTTP {reponse.status_code}] {explication}")
    print("Detail renvoye par OpenRouter :", reponse.text[:500])
    sys.exit(1)

# --- 6. Lecture des reponses -----------------------------------------------
donnees = reponse.json()                 # texte JSON -> dictionnaire Python
reponses = donnees["answers"]            # une entree par question posee
choix = reponses["type_document"]        # resultat de la question Choice
humain = reponses["validation_humaine"]  # resultat de la question Noul

print(f"Modele utilise   : {donnees.get('model')}")
print("-" * 60)
print(f"Type detecte     : {choix['choice']}")
print(f"Confiance        : {choix['confidence']:.2f}")
print("Probabilites     :")
# On trie les options de la plus probable a la moins probable.
for option, proba in sorted(choix["probabilities"].items(),
                            key=lambda item: item[1], reverse=True):
    print(f"   {option:<12} {proba:.2f}")
print(f"Besoin humain    : {humain['noul']:.2f} (probabilite du OUI)")

# --- 7. Decision de routage (apercu de la future logique du pipeline) ------
if choix["confidence"] >= SEUIL_CONFIANCE and humain["noul"] < SEUIL_HUMAIN:
    decision = f"RANGEMENT AUTOMATIQUE -> dossier {choix['choice']}"
else:
    decision = "A_Valider/ (validation humaine)"
print(f"Decision         : {decision}")

# --- 8. Cout de l'appel ----------------------------------------------------
usage = donnees.get("usage", {})
print("-" * 60)
print(f"Tokens envoyes   : {usage.get('input_tokens')}")
print(f"Cout de l'appel  : {usage.get('cost', 0):.6f} $")

# Option -v : afficher la reponse complete (utile pour me la renvoyer).
if "-v" in sys.argv:
    print("-" * 60)
    print(json.dumps(donnees, indent=2, ensure_ascii=False))
