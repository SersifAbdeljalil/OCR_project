"""
test_config.py - Tests du registre des categories (src/config.py).

Lancement (venv active, depuis OCR_PROJECT) :  python -m pytest -v
Aucun document reel n'est utilise : seulement le registre et des phrases inventees.
"""

import copy
import json
import re

import pytest

from src.config import (ErreurRegistre, champs_attendus, charger_registre,
                        normaliser, trouver_categorie, verifier_registre)


# --- Outils ----------------------------------------------------------------
@pytest.fixture
def registre():
    """Une copie fraiche du vrai registre, que chaque test peut abimer."""
    return copy.deepcopy(charger_registre())


def erreurs_contiennent(registre, fragment):
    """Vrai si l'un des messages d'erreur contient `fragment`."""
    return any(fragment in e for e in verifier_registre(registre))


# --- 1. Le vrai registre ---------------------------------------------------
def test_registre_du_projet_valide(registre):
    assert verifier_registre(registre) == []
    noms = [c["nom"] for c in registre["categories"]]
    assert noms == ["factures", "diplomes", "attestations", "contrats", "banque"]


def test_sous_dossiers_prevus(registre):
    assert trouver_categorie(registre, "diplomes")["sous_dossiers"] == \
        ["DEUG", "Licence", "Master", "Doctorat", "Autres"]
    assert trouver_categorie(registre, "attestations")["sous_dossiers"] == \
        ["Travail", "Scolarite", "Autres"]


def test_regle_metier_attestation_de_reussite(registre):
    """La regle metier reconnait une attestation de reussite de DEUG (phrase inventee)."""
    regle = registre["regles_metier"][0]
    texte = normaliser("ATTESTATION DE RÉUSSITE au Diplôme (DEUG)")
    assert regle["categorie"] == "diplomes"
    assert all(re.search(m, texte) for m in regle["motifs"])
    # Sans nom de diplome, la regle ne s'applique pas
    texte2 = normaliser("Attestation de réussite à la formation Excel")
    assert not all(re.search(m, texte2) for m in regle["motifs"])


def test_mots_cles_banque(registre):
    banque = trouver_categorie(registre, "banque")
    texte = normaliser("Relevé de compte - Agence Centre - Solde créditeur")
    indices = sum(1 for m in banque["mots_cles"] if re.search(m, texte))
    assert indices == 3        # releve de compte, agence, solde


# --- 2. Normalisation ------------------------------------------------------
def test_normaliser():
    assert normaliser("Diplôme de Licence ÉCONOMIE") == "diplome de licence economie"


# --- 3. Les erreurs sont detectees -----------------------------------------
def test_nom_avec_majuscule_ou_accent_refuse(registre):
    registre["categories"][0]["nom"] = "Facturés"
    assert erreurs_contiennent(registre, "nom invalide")


def test_categorie_en_double_refusee(registre):
    registre["categories"].append(copy.deepcopy(registre["categories"][0]))
    assert erreurs_contiennent(registre, "categorie en double")


def test_dossier_en_double_malgre_la_casse(registre):
    registre["categories"][1]["dossier"] = "FACTURES"   # Windows : meme dossier
    assert erreurs_contiennent(registre, "deja utilise")


def test_nom_reserve_refuse(registre):
    registre["categories"][0]["nom"] = "autres"
    assert erreurs_contiennent(registre, "nom reserve")


def test_regex_invalide_refusee(registre):
    registre["categories"][0]["mots_cles"].append(r"\b(facture\b")   # parenthese ouverte
    assert erreurs_contiennent(registre, "regex invalide")


def test_mot_cle_avec_majuscule_refuse(registre):
    registre["categories"][0]["mots_cles"].append(r"\bFacture\b")
    assert erreurs_contiennent(registre, "minuscules sans accents")


def test_mot_cle_avec_chiffre_en_clair_refuse(registre):
    registre["categories"][0]["mots_cles"].append(r"\bfa2026\b")
    assert erreurs_contiennent(registre, "contient un chiffre")


def test_mot_cle_avec_nombre_en_lettres_refuse(registre):
    registre["categories"][0]["mots_cles"].append(r"\bdeux cents\b")
    assert erreurs_contiennent(registre, "toutes lettres")


def test_codes_regex_numeriques_autorises(registre):
    """\\d+ et les quantites {24} ne sont pas des numeros ecrits en clair."""
    registre["categories"][4]["mots_cles"] += [r"\bcompte \d+\b", r"\b\d{24}\b"]
    assert verifier_registre(registre) == []


def test_regle_metier_categorie_inconnue(registre):
    registre["regles_metier"][0]["categorie"] = "diplome"   # sans le "s"
    assert erreurs_contiennent(registre, "categorie inconnue")


def test_champ_en_double_refuse(registre):
    registre["categories"][0]["champs"].append("tva")
    assert erreurs_contiennent(registre, "champ en double")


def test_toutes_les_erreurs_sont_listees(registre):
    """On ne s'arrete pas a la premiere erreur : l'humain voit tout d'un coup."""
    registre["categories"][0]["nom"] = "Factures"
    registre["categories"][1]["mots_cles"].append(r"\b(")
    assert len(verifier_registre(registre)) >= 2


# --- 4. Chargement depuis un fichier ---------------------------------------
def test_fichier_json_invalide(tmp_path):
    chemin = tmp_path / "categories.json"
    chemin.write_text("{ pas du json", encoding="utf-8")
    with pytest.raises(ErreurRegistre, match="JSON invalide"):
        charger_registre(chemin)


def test_fichier_absent(tmp_path):
    with pytest.raises(ErreurRegistre, match="introuvable"):
        charger_registre(tmp_path / "absent.json")


def test_fichier_avec_erreur_leve_erreur_registre(tmp_path, registre):
    registre["categories"][0]["nom"] = "Factures"
    chemin = tmp_path / "categories.json"
    chemin.write_text(json.dumps(registre), encoding="utf-8")
    with pytest.raises(ErreurRegistre, match="nom invalide"):
        charger_registre(chemin)


# --- 5. Champs attendus ----------------------------------------------------
def test_champs_categorie_connue(registre):
    champs = champs_attendus(registre, "factures")
    assert champs[:6] == ["fournisseur", "date_facture", "numero",
                          "montant_ht", "tva", "montant_ttc"]
    assert "cin" in champs and "iban" in champs      # champs sensibles ajoutes


def test_champs_categorie_decouverte(registre):
    champs = champs_attendus(registre, "bulletin_paie")   # absent du registre
    assert champs[:4] == ["titre", "personne", "organisme", "date"]


def test_champs_sans_doublon_pour_banque(registre):
    champs = champs_attendus(registre, "banque")
    assert champs.count("rib") == 1 and champs.count("iban") == 1
