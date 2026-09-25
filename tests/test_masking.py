"""
test_masking.py - Tests du masquage pour l'affichage (src/masking.py).

Lancement (venv active, depuis OCR_PROJECT) :  python -m pytest -v
Toutes les valeurs sont INVENTEES (numeros, emails, noms).
"""

import pytest

from src.config import charger_registre
from src.masking import MASQUE, masquer_texte, nom_affichable, resume_champs

REGISTRE = charger_registre()


def verifier_masque(texte, secret):
    """Le secret a disparu et [MASQUÉ] est present."""
    resultat = masquer_texte(texte)
    assert secret not in resultat
    assert MASQUE in resultat
    return resultat


# --- 1. Un test par motif (phrases inventees) ------------------------------
@pytest.mark.parametrize("texte, secret", [
    ("Titulaire de la CIN AB123456 delivree a Rabat", "AB123456"),
    ("C.I.N. : J 54321", "54321"),
    ("cnie n° xy998877", "998877"),                        # minuscules (OCR)
    ("CIN : BK 1234567", "1234567"),                       # 7 chiffres : masque aussi
])
def test_cin(texte, secret):
    verifier_masque(texte, secret)


@pytest.mark.parametrize("texte, secret, cle", [
    # Apres un mot-cle CIN : toujours masque, meme en minuscules avec un espace
    ("cin ab 123456", "123456", "cin"),
    ("CIN : ab 123456 delivree a Rabat", "ab 123456", "CIN"),
    ("CIN:AB123456", "AB123456", "CIN"),
    ("C.I.N. n° j 54321", "54321", "C.I.N."),
    ("c.i.n n°xy998877", "998877", "c.i.n"),
    ("CNIE : bk 1234567", "1234567", "CNIE"),
    ("cnie numéro ab 12345", "12345", "cnie"),
    ("Carte nationale d'identité n° ab 123456", "123456", "Carte nationale d'identité"),
    ("carte nationale : x 99999", "99999", "carte nationale"),
    ("CARTE NATIONALE D’IDENTITÉ ÉLECTRONIQUE N° AB123456", "AB123456", "CARTE NATIONALE"),
])
def test_valeur_apres_mot_cle_cin(texte, secret, cle):
    resultat = verifier_masque(texte, secret)
    assert cle in resultat          # le mot-cle reste visible, seule la valeur disparait


def test_mot_cle_cin_masque_meme_une_valeur_inhabituelle():
    """Masquer trop plutot que pas assez : le mot qui suit est masque."""
    assert masquer_texte("CIN abcdef") == f"CIN {MASQUE}"


def test_mots_proches_de_cin_non_masques():
    """'cinq', 'vaccin', 'cinema' ne sont pas le mot-cle CIN."""
    texte = "Cinq places au cinema apres le vaccin"
    assert masquer_texte(texte) == texte


@pytest.mark.parametrize("texte, secret", [
    ("RIB : 011780000012345678901234", "011780000012345678901234"),
    ("RIB : 011 780 0000123456789012 34", "0000123456789012"),
])
def test_rib(texte, secret):
    verifier_masque(texte, secret)


@pytest.mark.parametrize("texte, secret", [
    ("IBAN : MA64 0117 8000 0012 3456 7890 1234", "0117 8000"),
    ("iban ma640117800000123456789012", "640117800000123456789012"),
])
def test_iban(texte, secret):
    resultat = verifier_masque(texte, secret)
    assert "MA64" not in resultat.upper()


def test_suite_de_huit_chiffres_ou_plus():
    verifier_masque("Numero de dossier 12345678", "12345678")
    verifier_masque("Reference 1234 5678 9012", "5678")


@pytest.mark.parametrize("texte, secret", [
    ("Contact : prenom.nom@exemple.ma", "prenom.nom@exemple.ma"),
    ("Ecrire a service-rh@societe-exemple.co.ma svp", "service-rh"),
])
def test_email(texte, secret):
    verifier_masque(texte, secret)


@pytest.mark.parametrize("texte, secret", [
    ("Tel : 0612345678", "0612345678"),
    ("Tel : 06 12 34 56 78", "34 56"),
    ("Fixe : 05.22.12.34.56", "12.34"),
    ("Portable : 07-00-11-22-33", "11-22"),
    ("Tel : +212 6 12 34 56 78", "34 56"),
    ("Tel : +212612345678", "612345678"),
    ("Tel : 00212 5 22 12 34 56", "12 34"),
    ("Tel : +212 (0)6 12 34 56 78", "34 56"),
])
def test_telephone(texte, secret):
    verifier_masque(texte, secret)


# --- 2. Un texte sans rien de sensible reste lisible -----------------------
@pytest.mark.parametrize("texte", [
    "Facture FA-2026-00042 du 15/09/2026 : montant HT 1 000,00 DH, TVA 20 %, "
    "total TTC 1 200,00 DH.",
    "Traitement du 2026-09-25 : 17 fichiers, 12 pages OCR, 22,8 s.",
    "Diplome de Licence, mention Assez Bien, session de juin 2020.",
    "Article 12 : le loyer mensuel est de 15000 DH.",
    "",
])
def test_texte_non_sensible_inchange(texte):
    assert masquer_texte(texte) == texte


def test_none_donne_texte_vide():
    assert masquer_texte(None) == ""


# --- 3. Resume des champs --------------------------------------------------
def test_resume_champs():
    champs = {
        "fournisseur": "Societe Exemple",
        "montant_ttc": 1200.0,
        "numero": "Ref 123456789",          # champ non sensible, mais contient 9 chiffres
        "date_facture": None,
        "cin": "AB123456",
        "rib": "",
        "iban": "MA64 0117 8000 0012 3456 7890 1234",
        "adresse": "12 rue Exemple, Casablanca",
        "date_naissance": None,
    }
    r = resume_champs(champs, REGISTRE)
    # Champs sensibles : jamais la valeur, seulement trouve / absent
    assert r["cin"] == "trouvé" and r["iban"] == "trouvé" and r["adresse"] == "trouvé"
    assert r["rib"] == "absent" and r["date_naissance"] == "absent"
    # Autres champs : lisibles, mais passes au masquage
    assert r["fournisseur"] == "Societe Exemple"
    assert r["montant_ttc"] == "1200.0"
    assert r["numero"] == f"Ref {MASQUE}"
    assert r["date_facture"] == "absent"
    # Aucune valeur sensible nulle part dans le resume
    tout = " ".join(r.values())
    for secret in ("AB123456", "0117", "rue Exemple"):
        assert secret not in tout


def test_resume_champs_personnes_jamais_affichees():
    champs = {"titulaire": "Prenom Nom", "beneficiaire": "Autre Personne",
              "personne": "", "parties": "Societe A et Monsieur B"}
    r = resume_champs(champs, REGISTRE)
    assert r == {"titulaire": "trouvé", "beneficiaire": "trouvé",
                 "personne": "absent", "parties": "trouvé"}


def test_resume_champs_organismes_affiches_et_masques():
    champs = {"fournisseur": "Societe Exemple", "banque": "Banque Exemple",
              "etablissement": "Faculte Exemple", "emetteur": "Societe Exemple, tel 0612345678",
              "organisme": "Caisse Exemple"}
    r = resume_champs(champs, REGISTRE)
    assert r["fournisseur"] == "Societe Exemple"
    assert r["banque"] == "Banque Exemple"
    assert r["etablissement"] == "Faculte Exemple"
    assert r["organisme"] == "Caisse Exemple"
    assert "0612345678" not in r["emetteur"] and MASQUE in r["emetteur"]


def test_resume_champs_montant_decimal():
    from decimal import Decimal
    assert resume_champs({"montant_ttc": Decimal("240.50")}, REGISTRE)["montant_ttc"] == "240.50"


# --- 4. Nom de fichier affichable ------------------------------------------
@pytest.mark.parametrize("chemin, attendu", [
    ("Folder_Sortie/Diplomes/DEUG/diplome_deug_nom_prenom.json",
     "Folder_Sortie/Diplomes/DEUG/diplome_deug_***.json"),
    ("Folder_Sortie/Attestations/Travail/attestation_travail_Nom_Prenom_1.txt",
     "Folder_Sortie/Attestations/Travail/attestation_travail_***_1.txt"),
    ("Folder_Sortie/Factures/facture_societe_exemple.json",
     "Folder_Sortie/Factures/facture_***.json"),
    ("Folder_Sortie/A_Valider/cvnomprenom.pdf",
     "Folder_Sortie/A_Valider/***.pdf"),
    # Dossier inconnu (ex. nom d'utilisateur Windows) : masque aussi
    ("C:/Users/nomutilisateur/Documents/Folder_Entree/scan nom prenom.PDF",
     "C:/***/Folder_Entree/***.pdf"),
])
def test_nom_affichable(chemin, attendu):
    assert nom_affichable(chemin, REGISTRE) == attendu
