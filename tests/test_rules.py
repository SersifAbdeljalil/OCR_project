"""
test_rules.py - Tests de la classification par regles (src/rules.py).

Les 6 documents INVENTES de test_classification.py sont repris, plus des cas
pour chaque categorie du registre. Aucun document reel.
Lancement (venv active, depuis OCR_PROJECT) :  python -m pytest -v
"""

import copy

import pytest

from src.config import charger_registre
from src.rules import (AUCUN_INDICE, EGALITE, FAIBLE, NET, REGLE_METIER,
                       analyser, part_arabe, signaux_qualite, verdict_scores)

REGISTRE = charger_registre()

# --- 1. Les 6 documents inventes de test_classification.py ------------------
FACTURE_NETTE = """FACTURE N° FA-2026-00042
Date : 15/09/2026 - Fournisseur : Société Exemple SARL, Casablanca
Désignation : Maintenance informatique septembre 2026
Montant HT : 1 000,00 DH - TVA 20 % : 200,00 DH - Total TTC : 1 200,00 DH"""

ATTESTATION_TRAVAIL = """ATTESTATION DE TRAVAIL
Je soussigné, directeur de la société Exemple SARL, atteste que M. Titulaire Exemple
est employé en qualité de comptable depuis le 01/03/2022.
Cette attestation est délivrée pour servir et valoir ce que de droit."""

CONTRAT_BAIL = """CONTRAT DE BAIL COMMERCIAL
Entre les soussignés : Société Bailleur Exemple et Société Preneur Exemple.
Article 1 : Objet - location d'un local commercial à Casablanca.
Article 2 : Les parties conviennent d'un loyer mensuel payable d'avance."""

DIPLOME_LICENCE = """ROYAUME DU MAROC - UNIVERSITÉ EXEMPLE
DIPLÔME DE LICENCE EN SCIENCES ÉCONOMIQUES
Le président de l'université confère à Titulaire Exemple
le diplôme de Licence, mention Assez Bien, session de juin 2020."""

ATTESTATION_REUSSITE_DEUG = """ATTESTATION DE RÉUSSITE
Le doyen de la Faculté Exemple atteste que l'étudiant Titulaire Exemple
a obtenu le Diplôme d'Études Universitaires Générales (DEUG), mention Bien.
Délivrée pour servir et valoir ce que de droit."""

RECU_PAIEMENT = """REÇU N° 118
Reçu de Monsieur Client Exemple la somme de cinq cents dirhams (500 DH)
en espèces, pour la réservation d'une salle le 20/09/2026.
Fait à Casablanca, le 18/09/2026."""


@pytest.mark.parametrize("texte, categorie, verdict, score, sous_dossier", [
    (FACTURE_NETTE, "factures", NET, 4, None),
    (ATTESTATION_TRAVAIL, "attestations", NET, 4, "Travail"),
    (CONTRAT_BAIL, "contrats", NET, 4, None),
    (DIPLOME_LICENCE, "diplomes", NET, 4, "Licence"),
    (ATTESTATION_REUSSITE_DEUG, "diplomes", REGLE_METIER, 3, "DEUG"),
])
def test_documents_de_test_classification(texte, categorie, verdict, score, sous_dossier):
    r = analyser(texte, REGISTRE)
    assert (r.categorie, r.verdict, r.sous_dossier) == (categorie, verdict, sous_dossier)
    assert r.scores[categorie] == score


def test_scores_identiques_a_test_classification():
    """Memes scores que ceux mesures avec test_classification.py."""
    assert analyser(ATTESTATION_REUSSITE_DEUG, REGISTRE).scores == \
        {"factures": 0, "diplomes": 3, "attestations": 3, "contrats": 0, "banque": 0}
    assert analyser(FACTURE_NETTE, REGISTRE).scores == \
        {"factures": 4, "diplomes": 0, "attestations": 0, "contrats": 0, "banque": 0}


def test_regle_metier_decrite():
    r = analyser(ATTESTATION_REUSSITE_DEUG, REGISTRE)
    assert r.regle_metier == "Une attestation de reussite d'un diplome est un diplome"


def test_recu_aucun_indice():
    r = analyser(RECU_PAIEMENT, REGISTRE)
    assert r.categorie is None and r.verdict == AUCUN_INDICE and r.sous_dossier is None
    assert set(r.scores.values()) == {0}


# --- 2. Un cas par categorie du registre (textes inventes) ------------------
@pytest.mark.parametrize("texte, categorie, sous_dossier", [
    ("NOTE D'HONORAIRES - Total HT : 3 000 DH - TVA 20 % - TTC", "factures", None),
    ("Diplôme de Master, grade conféré avec mention Très Bien", "diplomes", "Master"),
    ("Diplôme national de Doctorat décerné à Titulaire Exemple", "diplomes", "Doctorat"),
    ("Diplôme de Technicien Spécialisé, mention Bien", "diplomes", "Autres"),
    ("Certificat de scolarité : le directeur certifie que l'élève est inscrit. "
     "Attestation de scolarité délivrée pour servir et valoir.", "attestations", "Scolarite"),
    ("Attestation de salaire : l'employeur atteste le montant perçu", "attestations", "Autres"),
    ("CONTRAT DE PRESTATION entre les soussignés, Article 3 : durée", "contrats", None),
    ("RELEVÉ DE COMPTE - Agence Centre - Solde au 30/09/2026", "banque", None),
    ("Relevé d'identité bancaire : RIB, IBAN, agence Exemple", "banque", None),
])
def test_chaque_categorie_du_registre(texte, categorie, sous_dossier):
    r = analyser(texte, REGISTRE)
    assert r.categorie == categorie and r.verdict == NET
    assert r.sous_dossier == sous_dossier


# --- 3. Verdicts faible et egalite -----------------------------------------
def test_verdict_faible():
    # facture (facture, tva) = 2 ; contrat (contrat) = 1
    r = analyser("Facture liée au contrat, TVA incluse", REGISTRE)
    assert (r.categorie, r.verdict) == ("factures", FAIBLE)


def test_verdict_egalite():
    r = analyser("Facture du contrat", REGISTRE)          # 1 facture, 1 contrat
    assert (r.categorie, r.verdict, r.sous_dossier) == (None, EGALITE, None)


def test_contrat_de_travail_seulement_faible():
    """FAIBLESSE CONNUE du registre (a trancher avec l'utilisateur) : le mot-cle
    d'attestation « de travail » compte aussi dans un CONTRAT de travail."""
    r = analyser("CONTRAT DE TRAVAIL entre les soussignés, Article 3 : durée", REGISTRE)
    assert r.scores["contrats"] == 3 and r.scores["attestations"] == 1
    assert (r.categorie, r.verdict) == ("contrats", FAIBLE)


def test_un_seul_indice_est_faible():
    assert verdict_scores({"factures": 1, "contrats": 0}) == ("factures", FAIBLE)


@pytest.mark.parametrize("scores, attendu", [
    ({}, (None, AUCUN_INDICE)),
    ({"a": 0, "b": 0}, (None, AUCUN_INDICE)),
    ({"a": 2}, ("a", NET)),                              # une seule categorie
    ({"a": 3, "b": 3}, (None, EGALITE)),
])
def test_verdict_scores_cas_limites(scores, attendu):
    assert verdict_scores(scores) == attendu


# --- 4. Aucune regle en dur : tout vient du registre ------------------------
def test_regles_lues_dans_le_registre():
    registre = copy.deepcopy(REGISTRE)
    registre["categories"].append({
        "nom": "bulletin_paie", "dossier": "Bulletins", "sous_dossiers": [],
        "mots_cles": [r"\bbulletin de paie\b", r"\bsalaire net\b"],
        "champs": ["titre"], "nom_fichier": {"prefixe": "bulletin", "parties": ["titre"]}})
    r = analyser("Bulletin de paie - salaire net à payer", registre)
    assert (r.categorie, r.verdict) == ("bulletin_paie", NET)


def test_sans_regle_metier_dans_le_registre():
    registre = copy.deepcopy(REGISTRE)
    registre["regles_metier"] = []
    r = analyser(ATTESTATION_REUSSITE_DEUG, registre)
    assert (r.categorie, r.verdict) == (None, EGALITE)   # 3 diplome, 3 attestation


# --- 5. Signaux de qualite ---------------------------------------------------
def test_part_arabe():
    assert part_arabe("Facture Exemple") == 0.0
    assert part_arabe("شهادة") == 1.0
    assert part_arabe("abcd شهادة") == pytest.approx(5 / 9)
    assert part_arabe("123 !") == 0.0                      # aucune lettre


def test_signaux_texte_natif_seul_pour_l_arabe():
    r = analyser("Diplôme de Licence mention Bien", REGISTRE, texte_natif="شهادة الإجازة")
    assert r.signaux.part_arabe == 1.0


def test_signaux_ocr():
    s = signaux_qualite("", [0.99, 0.95, 0.80, 0.50])
    assert s.lignes_ocr == 4 and s.lignes_ocr_sous_seuil == 2
    assert s.confiance_ocr_moyenne == pytest.approx(0.81)


def test_signaux_sans_ocr():
    s = analyser(FACTURE_NETTE, REGISTRE).signaux
    assert s.lignes_ocr == 0 and s.confiance_ocr_moyenne is None
    assert s.lignes_ocr_sous_seuil == 0 and s.part_arabe == 0.0


def test_les_signaux_ne_changent_pas_le_verdict():
    """Les signaux sont renvoyes SANS decider : c'est classifier.py qui tranchera."""
    r = analyser(FACTURE_NETTE, REGISTRE, confiances_ocr=[0.3, 0.4])
    assert (r.categorie, r.verdict) == ("factures", NET)


def test_texte_vide():
    r = analyser("", REGISTRE)
    assert r.verdict == AUCUN_INDICE and r.signaux.part_arabe == 0.0
