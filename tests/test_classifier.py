"""
test_classifier.py - Tests de la classification (src/classifier.py).

Le moteur (LLM) est SIMULE : aucun besoin d'Ollama. Un test par ligne de la
regle A modifiee, plus la decouverte de categorie. Textes INVENTES.
Lancement (venv active, depuis OCR_PROJECT) :  python -m pytest -v
"""

import copy
import json

import pytest

import src.classifier as classifier
from src.classifier import (MOTEURS, MoteurClassification, MoteurPhi4,
                            ReponseMoteur, categorie_proche, classer, creer_moteur,
                            decrire_categories, normaliser_nom, schema_classification)
from src.config import charger_registre
from src.llm import ClientOllama

REGISTRE = charger_registre()

# Textes inventes, un par verdict des mots-cles
FACTURE_NETTE = ("FACTURE N° FA-2026-00042\nFournisseur : Société Exemple\n"
                 "Montant HT : 1 000,00 DH - TVA 20 % - Total TTC : 1 200,00 DH")
FACTURE_FAIBLE = "Facture liée au contrat, TVA incluse"          # factures 2, contrats 1
EGALITE = "Facture du contrat"                                  # 1 / 1
AUCUN_INDICE = "Reçu de la somme de cinq cents dirhams en espèces."
REUSSITE_DEUG = ("ATTESTATION DE RÉUSSITE\nLe doyen atteste que l'étudiant a obtenu le "
                 "Diplôme d'Études Universitaires Générales (DEUG), mention Bien.")


class MoteurSimule(MoteurClassification):
    """Renvoie une reponse fixee ; note chaque appel."""
    nom = "simule"

    def __init__(self, categorie=None, nom_propose=None, ok=True, erreur=None, leve=False):
        self.reponse = ReponseMoteur(ok=ok, categorie=categorie, nom_propose=nom_propose,
                                     erreur=erreur, duree_s=1.5)
        self.leve, self.appels = leve, 0

    def classer(self, texte, registre):
        self.appels += 1
        if self.leve:
            raise RuntimeError("bogue du moteur")
        return self.reponse


def classer_avec(texte, moteur, **kw):
    return classer(texte, REGISTRE, moteur=moteur, **kw)


# --- 1. Une ligne de la regle A par test ------------------------------------
def test_regle_metier_095_sans_moteur():
    m = MoteurSimule("attestations")
    r = classer_avec(REUSSITE_DEUG, m)
    assert (r.categorie, r.confiance, r.sous_dossier) == ("diplomes", 0.95, "DEUG")
    assert m.appels == 0 and not r.moteur_appele and not r.necessite_validation_humaine


def test_net_et_moteur_d_accord_095():
    m = MoteurSimule("factures")
    r = classer_avec(FACTURE_NETTE, m)
    assert r.verdict_regles == "net" and m.appels == 1       # le moteur confirme TOUJOURS
    assert (r.categorie, r.confiance, r.necessite_validation_humaine) == ("factures", 0.95, False)


def test_net_et_moteur_en_desaccord_060():
    r = classer_avec(FACTURE_NETTE, MoteurSimule("contrats"))
    assert (r.categorie, r.confiance, r.necessite_validation_humaine) == ("factures", 0.60, True)
    assert r.categorie_moteur == "contrats"
    assert "moteur : contrats (desaccord)" in r.raisons


def test_faible_et_moteur_d_accord_060():
    """Decision b9 : « faible » + accord ne suffit plus (ex. un CV qui cite un diplome)."""
    r = classer_avec(FACTURE_FAIBLE, MoteurSimule("factures"))
    assert r.verdict_regles == "faible"
    assert (r.categorie, r.confiance, r.necessite_validation_humaine) == ("factures", 0.60, True)
    assert "moteur : factures (accord)" in r.raisons


def test_faible_et_moteur_en_desaccord_060():
    r = classer_avec(FACTURE_FAIBLE, MoteurSimule("contrats"))
    assert (r.confiance, r.necessite_validation_humaine) == (0.60, True)


def test_egalite_060_meme_si_le_moteur_tranche():
    r = classer_avec(EGALITE, MoteurSimule("factures"))
    assert r.verdict_regles == "egalite"
    assert (r.categorie, r.confiance, r.necessite_validation_humaine) == ("factures", 0.60, True)


def test_aucun_indice_060():
    r = classer_avec(AUCUN_INDICE, MoteurSimule("banque"))
    assert r.verdict_regles == "aucun indice"
    assert (r.categorie, r.confiance) == ("banque", 0.60)


@pytest.mark.parametrize("moteur", [MoteurSimule(ok=False, erreur="appel echoue (Timeout)"),
                                    MoteurSimule(leve=True)])
def test_moteur_en_panne_060_sans_exception(moteur):
    r = classer_avec(FACTURE_NETTE, moteur)
    assert (r.categorie, r.confiance, r.necessite_validation_humaine) == ("factures", 0.60, True)
    assert any(x.startswith("moteur indisponible") for x in r.raisons)


def test_ocr_douteux_impose_la_validation():
    r = classer_avec(FACTURE_NETTE, MoteurSimule("factures"), confiances_ocr=[0.9, 0.6])
    assert r.confiance == 0.95                     # la confiance n'est pas modifiee...
    assert r.necessite_validation_humaine          # ... mais la validation est imposee
    assert any("confiance OCR moyenne 0.75" in x for x in r.raisons)


def test_ocr_a_080_pile_ne_bloque_pas():
    r = classer_avec(FACTURE_NETTE, MoteurSimule("factures"), confiances_ocr=[0.8, 0.8])
    assert not r.necessite_validation_humaine


def test_texte_arabe_au_dela_de_30_pourcent():
    natif = "شهادة الإجازة في الاقتصاد Licence"          # surtout des lettres arabes
    r = classer_avec(FACTURE_NETTE, MoteurSimule("factures"), texte_natif=natif)
    assert r.signaux.part_arabe > 0.30
    assert r.confiance == 0.95 and r.necessite_validation_humaine
    assert any(x.startswith("lettres arabes") for x in r.raisons)


def test_texte_arabe_sous_30_pourcent():
    natif = "Facture " * 20 + "شهادة"                     # 5 lettres arabes sur 145
    r = classer_avec(FACTURE_NETTE, MoteurSimule("factures"), texte_natif=natif)
    assert r.signaux.part_arabe < 0.30 and not r.necessite_validation_humaine


def test_seuls_net_accord_et_regle_metier_rangent():
    """Toutes les combinaisons verdict x reponse : seules 2 donnent >= 0.90."""
    ranges = []
    for texte in (FACTURE_NETTE, FACTURE_FAIBLE, EGALITE, AUCUN_INDICE, REUSSITE_DEUG):
        for reponse in ("factures", "contrats", "autre"):
            r = classer_avec(texte, MoteurSimule(reponse, "Nom Nouveau"))
            if not r.necessite_validation_humaine:
                ranges.append((r.verdict_regles, reponse))
    assert set(ranges) == {("net", "factures"), ("regle metier", "factures"),
                           ("regle metier", "contrats"), ("regle metier", "autre")}


def test_regle_metier_mais_ocr_douteux():
    r = classer_avec(REUSSITE_DEUG, MoteurSimule(), confiances_ocr=[0.5])
    assert r.confiance == 0.95 and r.necessite_validation_humaine


# --- 2. Categorie decouverte -------------------------------------------------
def test_autre_nom_vraiment_nouveau():
    r = classer_avec(AUCUN_INDICE, MoteurSimule("autre", "Bulletin de Paie"))
    assert r.categorie_proposee == "bulletin_de_paie"
    assert r.categorie is None and r.confiance == 0.60 and r.necessite_validation_humaine


def test_autre_nom_trop_proche_garde_l_existante():
    r = classer_avec(AUCUN_INDICE, MoteurSimule("autre", "Facture d'avoir"))
    assert r.categorie == "factures" and r.categorie_proposee is None
    assert r.confiance == 0.60 and r.necessite_validation_humaine
    assert "moteur : autre ; nom propose proche de factures" in r.raisons


def test_autre_alors_que_les_mots_cles_sont_nets():
    r = classer_avec(FACTURE_NETTE, MoteurSimule("autre", "Bon de commande"))
    assert r.categorie == "factures" and r.categorie_proposee == "bon_de_commande"
    assert r.confiance == 0.60


@pytest.mark.parametrize("nom", [None, "", "  ", "Autre", "autres", "A_Valider", "123"])
def test_autre_sans_nom_exploitable(nom):
    r = classer_avec(AUCUN_INDICE, MoteurSimule("autre", nom))
    assert r.categorie_proposee is None and r.confiance == 0.60


@pytest.mark.parametrize("brut, attendu", [
    ("Bulletin de Paie", "bulletin_de_paie"),
    ("Avis d'imposition", "avis_d_imposition"),
    ("  Relevé  Épargne ", "releve_epargne"),
    ("2024 Rapport", "rapport"),
])
def test_normaliser_nom(brut, attendu):
    assert normaliser_nom(brut) == attendu


@pytest.mark.parametrize("nom, attendu", [
    ("facture", "factures"), ("factures", "factures"), ("facture_d_avoir", "factures"),
    ("diplome", "diplomes"), ("contrat", "contrats"), ("banques", "banque"),
    ("bulletin_de_paie", None), ("avis_d_imposition", None),
])
def test_categorie_proche(nom, attendu):
    assert categorie_proche(nom, REGISTRE) == attendu


# --- 3. Moteur interchangeable ------------------------------------------------
def test_creer_moteur_par_nom():
    assert isinstance(creer_moteur("phi4-mini"), MoteurPhi4)
    with pytest.raises(ValueError, match="inconnu"):
        creer_moteur("jev")


def test_brancher_un_autre_moteur_sans_toucher_classifier(monkeypatch):
    """Un futur moteur (ex. JEV) : une classe + une entree dans MOTEURS + le reglage."""
    class MoteurFutur(MoteurSimule):
        nom = "futur"

        def __init__(self):
            super().__init__("factures")
    monkeypatch.setitem(MOTEURS, "futur", MoteurFutur)
    moteur = creer_moteur("futur")
    r = classer_avec(FACTURE_NETTE, moteur)
    assert r.confiance == 0.95 and moteur.appels == 1


# --- 4. Le moteur Phi-4-mini, avec Ollama simule -------------------------------
class FausseReponse:
    def __init__(self, donnees):
        self._d = donnees

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class FausseSession:
    def __init__(self, reponse_texte):
        self.reponse_texte, self.posts = reponse_texte, []

    def post(self, url, json=None, timeout=None):
        self.posts.append(json)
        return FausseReponse({"response": self.reponse_texte})


def test_moteur_phi4_prompt_et_schema_depuis_le_registre():
    registre = copy.deepcopy(REGISTRE)
    registre["categories"].append({
        "nom": "bulletin_paie", "dossier": "Bulletins", "sous_dossiers": [],
        "mots_cles": [r"\bbulletin de paie\b"], "champs": ["titre"],
        "nom_fichier": {"prefixe": "bulletin", "parties": ["titre"]}})
    session = FausseSession(json.dumps({"type_document": "bulletin_paie", "nom_propose": None}))
    moteur = MoteurPhi4(client=ClientOllama(session=session))
    rep = moteur.classer("texte inventé", registre)
    assert rep.ok and rep.categorie == "bulletin_paie"
    corps = session.posts[0]
    assert "- bulletin_paie (informations : titre)" in corps["prompt"]      # vient du registre
    assert "sous-types : DEUG, Licence, Master, Doctorat" in corps["prompt"]
    assert "attestation de reussite" in corps["prompt"]                    # regle metier
    assert corps["format"]["properties"]["type_document"]["enum"][-2:] == ["bulletin_paie", "autre"]
    assert "$" not in corps["prompt"]                                      # tout est rempli


def test_prompt_sans_exemple_de_categorie_inventee_et_avec_consigne():
    session = FausseSession(json.dumps({"type_document": "autre", "nom_propose": "x"}))
    MoteurPhi4(client=ClientOllama(session=session)).classer("texte", REGISTRE)
    prompt = session.posts[0]["prompt"]
    assert "par exemple" not in prompt.lower()
    assert "bulletin de paie" not in prompt and "avis d'imposition" not in prompt
    assert "MENTIONNE un diplôme, une facture ou un contrat" in prompt


def test_moteur_phi4_reponse_invalide():
    moteur = MoteurPhi4(client=ClientOllama(session=FausseSession("pas du json")))
    rep = moteur.classer("texte", REGISTRE)
    assert not rep.ok and rep.erreur


def test_description_des_categories_sans_autres():
    texte = decrire_categories(REGISTRE)
    assert "Autres" not in texte and texte.count("\n") == len(REGISTRE["categories"]) - 1


def test_schema_classification():
    enum = schema_classification(REGISTRE)["properties"]["type_document"]["enum"]
    assert enum == [c["nom"] for c in REGISTRE["categories"]] + ["autre"]


# --- 5. Confidentialite --------------------------------------------------------
def test_raisons_sans_texte():
    secret = "Titulaire Secret AB123456"
    r = classer_avec(FACTURE_NETTE + "\n" + secret, MoteurSimule("factures"))
    assert not any(secret in x or "AB123456" in x for x in r.raisons + r.alertes)
