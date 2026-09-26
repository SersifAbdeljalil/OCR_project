"""
test_schemas.py - Tests du modele de sortie JSON (src/schemas.py).

Lancement (venv active, depuis OCR_PROJECT) :  python -m pytest -v
Toutes les valeurs sont INVENTEES.
"""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.config import charger_registre
from src.schemas import DocumentSortie

REGISTRE = charger_registre()
CONTEXTE = {"registre": REGISTRE}


def document(**modifs) -> dict:
    """Un document de sortie valide (facture inventee), modifiable par test."""
    base = {
        "type": "factures",
        "source": "facture_exemple.pdf",
        "date_traitement": "2026-09-25T10:00:00",
        "confiance_classification": 0.95,
        "champs": {"fournisseur": "Societe Exemple", "montant_ttc": 1200.0},
        "necessite_validation_humaine": False,
        "texte_brut": "texte invente",
    }
    base.update(modifs)
    return base


def valider(donnees: dict) -> DocumentSortie:
    return DocumentSortie.model_validate(donnees, context=CONTEXTE)


# --- 1. Cas valides --------------------------------------------------------
def test_document_valide_et_champs_completes():
    doc = valider(document())
    # Les champs absents sont ajoutes avec None, dans l'ordre du registre
    assert list(doc.champs)[:6] == ["fournisseur", "date_facture", "numero",
                                    "montant_ht", "tva", "montant_ttc"]
    assert doc.champs["numero"] is None
    assert doc.champs["montant_ttc"] == 1200.0


def test_json_produit_respecte_le_schema_unique():
    sortie = json.loads(valider(document()).model_dump_json())
    assert set(sortie) == {"type", "source", "date_traitement",
                           "confiance_classification", "champs",
                           "necessite_validation_humaine", "texte_brut"}


def test_champs_sensibles_acceptes():
    doc = valider(document(champs={"cin": "XX000000", "iban": "MA00 0000"}))
    assert doc.champs["cin"] == "XX000000"


def test_categorie_decouverte_champs_generiques():
    doc = valider(document(type="bulletin_paie", confiance_classification=0.60,
                           necessite_validation_humaine=True,
                           champs={"titre": "Bulletin", "personne": "Personne Exemple"}))
    assert list(doc.champs)[:4] == ["titre", "personne", "organisme", "date"]


def test_banque():
    doc = valider(document(type="banque", champs={"banque": "Banque Exemple"}))
    assert set(doc.champs) >= {"banque", "titulaire", "periode", "objet", "rib", "iban"}


def test_decimal_accepte_et_garde_intact():
    doc = valider(document(champs={"montant_ht": Decimal("200.4"),
                                   "montant_ttc": Decimal("240.5")}))
    assert isinstance(doc.champs["montant_ttc"], Decimal)


def test_vers_json_montants_a_deux_decimales():
    doc = valider(document(champs={"montant_ht": Decimal("200.4"),
                                   "tva": Decimal("40.1"),
                                   "montant_ttc": Decimal("240.5"),
                                   "fournisseur": "Société Exemple"}))
    texte = doc.vers_json()
    assert '"montant_ttc": 240.50' in texte          # nombre, pas texte
    assert '"montant_ht": 200.40' in texte
    assert "Société Exemple" in texte                # accents lisibles
    relu = json.loads(texte)                         # JSON valide
    assert relu["champs"]["montant_ttc"] == 240.5
    assert relu["champs"]["numero"] is None
    assert set(relu) == {"type", "source", "date_traitement",
                         "confiance_classification", "champs",
                         "necessite_validation_humaine", "texte_brut"}


def test_vers_json_arrondi_au_centime():
    doc = valider(document(champs={"montant_ttc": Decimal("10.005")}))
    assert '"montant_ttc": 10.01' in doc.vers_json()


# --- 2. Cas refuses --------------------------------------------------------
def test_champ_non_prevu_refuse():
    with pytest.raises(ValidationError, match="champs non prevus"):
        valider(document(champs={"titulaire": "X"}))   # champ de diplome, pas de facture


def test_categorie_decouverte_refuse_champ_de_facture():
    with pytest.raises(ValidationError, match="champs non prevus"):
        valider(document(type="bulletin_paie", champs={"fournisseur": "X"}))


def test_cle_hors_schema_refusee():
    with pytest.raises(ValidationError):
        valider(document(commentaire="en trop"))


def test_cle_manquante_refusee():
    donnees = document()
    del donnees["source"]
    with pytest.raises(ValidationError):
        valider(donnees)


def test_type_non_normalise_refuse():
    with pytest.raises(ValidationError, match="minuscules sans accents"):
        valider(document(type="Factures"))


@pytest.mark.parametrize("confiance", [-0.1, 1.5])
def test_confiance_hors_limites_refusee(confiance):
    with pytest.raises(ValidationError):
        valider(document(confiance_classification=confiance))


def test_confiance_basse_impose_validation_humaine():
    with pytest.raises(ValidationError, match="necessite_validation_humaine"):
        valider(document(confiance_classification=0.60,
                         necessite_validation_humaine=False))


def test_confiance_basse_acceptee_apres_validation_humaine():
    """validation.py : l'humain a valide, la confiance d'origine reste une information."""
    doc = DocumentSortie.model_validate(
        document(confiance_classification=0.60, necessite_validation_humaine=False),
        context={**CONTEXTE, "valide_par_humain": True})
    assert doc.confiance_classification == 0.60 and not doc.necessite_validation_humaine


# --- 3. Confidentialite ----------------------------------------------------
def test_erreur_ne_recopie_jamais_les_valeurs():
    """Une erreur de validation ne doit pas faire fuiter une valeur sensible."""
    secret = "XX999999"
    with pytest.raises(ValidationError) as err:
        valider(document(champs={"cin": secret, "champ_inconnu": secret},
                         confiance_classification="pas un nombre"))
    assert secret not in str(err.value)


def test_affichage_masque_champs_et_texte():
    doc = valider(document(champs={"cin": "XX999999"}, texte_brut="TEXTE SECRET"))
    assert "XX999999" not in repr(doc)
    assert "TEXTE SECRET" not in repr(doc)
