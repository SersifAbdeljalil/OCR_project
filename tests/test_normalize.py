"""
test_normalize.py - Tests de la normalisation (src/normalize.py).

Lancement (venv active, depuis OCR_PROJECT) :  python -m pytest -v
Toutes les valeurs sont INVENTEES.
"""

from decimal import Decimal

import pytest

from src.normalize import normaliser_date, normaliser_montant, verifier_totaux

ANNEE_REF = 2026      # annee de reference fixe : les tests ne dependent pas du jour


# --- 1. Dates --------------------------------------------------------------
@pytest.mark.parametrize("texte, attendu", [
    ("15/09/2026", "2026-09-15"),
    ("15-09-2026", "2026-09-15"),
    ("15.09.2026", "2026-09-15"),
    ("2026-09-15", "2026-09-15"),
    ("15 septembre 2026", "2026-09-15"),
    ("15 Septembre 2026", "2026-09-15"),
    ("15 sept. 2026", "2026-09-15"),
    ("1er octobre 2026", "2026-10-01"),
    ("3 février 2025", "2025-02-03"),            # accent
    ("12 août 2024", "2024-08-12"),
    ("5/3/2026", "2026-03-05"),                  # jour/mois sur 1 chiffre
    ("Casablanca, le 15/09/2026", "2026-09-15"), # date au milieu d'une phrase
    ("15/09/26", "2026-09-15"),                  # annee sur 2 chiffres
    ("15/09/85", "1985-09-15"),                  # 85 -> 1985 (pas 2085)
])
def test_dates_valides(texte, attendu):
    iso, _ = normaliser_date(texte, annee_reference=ANNEE_REF)
    assert iso == attendu


def test_ordre_jour_mois_francais():
    """Piege : 03/04/2026 est le 3 avril (convention francaise), pas le 4 mars."""
    assert normaliser_date("03/04/2026", ANNEE_REF)[0] == "2026-04-03"


@pytest.mark.parametrize("texte, alerte", [
    ("31/02/2026", "date impossible"),           # 31 fevrier
    ("29/02/2025", "date impossible"),           # 2025 n'est pas bissextile
    ("15/13/2026", "date impossible"),           # mois 13
    ("09/15/2026", "date impossible"),           # format americain : refuse
    ("hier", "format non reconnu"),
    ("15 truc 2026", "format non reconnu"),      # mois inconnu
    ("du 01/09/2026 au 30/09/2026", "plusieurs dates"),
    ("15/09/1850", "hors limites"),
])
def test_dates_refusees(texte, alerte):
    iso, alertes = normaliser_date(texte, annee_reference=ANNEE_REF)
    assert iso is None
    assert any(alerte in a for a in alertes)


def test_29_fevrier_bissextile_accepte():
    assert normaliser_date("29/02/2028", ANNEE_REF)[0] == "2028-02-29"


def test_date_absente_sans_alerte():
    assert normaliser_date(None) == (None, [])
    assert normaliser_date("  ") == (None, [])


def test_annee_deux_chiffres_signalee():
    _, alertes = normaliser_date("15/09/26", ANNEE_REF)
    assert any("2 chiffres" in a for a in alertes)


def test_alerte_ne_recopie_pas_la_date():
    """Une date peut etre une date de naissance : jamais recopiee dans l'alerte."""
    _, alertes = normaliser_date("31/02/1990", ANNEE_REF)
    assert alertes and not any("1990" in a or "31" in a for a in alertes)


# --- 2. Montants -----------------------------------------------------------
@pytest.mark.parametrize("texte, attendu", [
    ("1 234,56", "1234.56"),
    ("1.234,56", "1234.56"),
    ("1,234.56", "1234.56"),
    ("1234.56", "1234.56"),
    ("1234,5", "1234.5"),
    ("1234", "1234"),
    ("1 234,56 DH", "1234.56"),
    ("1234,56 MAD", "1234.56"),
    ("1.234,56 Dhs", "1234.56"),
    ("1 234,56 €", "1234.56"),
    ("1 234,56 DH", "1234.56"),        # espaces insecables
    ("1 234,56", "1234.56"),               # espace fine insecable
    ("1.234.567,89", "1234567.89"),
    ("1,234,567", "1234567"),
    ("1 234 567", "1234567"),
    ("-150,00 DH", "-150.00"),                  # avoir
    ("0,5", "0.5"),
])
def test_montants_valides(texte, attendu):
    montant, _ = normaliser_montant(texte)
    assert montant == Decimal(attendu)


def test_montant_deja_numerique():
    assert normaliser_montant(1200.5)[0] == Decimal("1200.5")
    assert normaliser_montant(Decimal("12.30"))[0] == Decimal("12.30")


@pytest.mark.parametrize("texte, alerte", [
    ("1.234", "ambigu"),        # mille deux cent trente-quatre ou 1,234 ?
    ("1,234", "ambigu"),
    ("12,3,4", "incoherents"),
    ("1.23.456,00", "incoherents"),
    ("1,234,56", "incoherents"),
    ("douze", "format non reconnu"),
    ("12,", "format non reconnu"),
    ("12 DH 50", "format non reconnu"),         # piege : ne pas lire 1250
    ("12 50", "format non reconnu"),            # espace qui n'est pas un millier
    ("1 23,00", "format non reconnu"),
])
def test_montants_refuses(texte, alerte):
    montant, alertes = normaliser_montant(texte)
    assert montant is None
    assert any(alerte in a for a in alertes)


def test_montant_absent_sans_alerte():
    assert normaliser_montant(None) == (None, [])
    assert normaliser_montant("") == (None, [])


# --- 3. Totaux -------------------------------------------------------------
def test_totaux_ok():
    controle, alertes = verifier_totaux("1 000,00", "200,00", "1 200,00 DH")
    assert controle.ok and not controle.necessite_validation_humaine and alertes == []


def test_totaux_tolerance_un_centime():
    controle, _ = verifier_totaux("1000.00", "200.00", "1200.01")
    assert controle.ok


def test_totaux_plusieurs_taux_tva():
    """Piege : 4 taux marocains sur une meme facture (inventee).
    HT : 1000 a 20 %, 500 a 14 %, 300 a 10 %, 200 a 7 % = 2000
    TVA : 200 + 70 + 30 + 14 = 314 ; TTC = 2314."""
    controle, alertes = verifier_totaux("2 000,00", ["200,00", "70,00", "30,00", "14,00"],
                                        "2 314,00")
    assert controle.ok and alertes == []


def test_totaux_plusieurs_taux_avec_ecart():
    controle, alertes = verifier_totaux(2000, [200, 70, 30], 2314)   # ligne 7 % oubliee
    assert not controle.ok
    assert controle.ecart == Decimal("-14")
    assert controle.necessite_validation_humaine
    assert any("ecart de -14.00" in a for a in alertes)


def test_totaux_ecart_impose_validation():
    controle, alertes = verifier_totaux("1000", "200", "1250")
    assert not controle.ok and controle.necessite_validation_humaine
    assert controle.ecart == Decimal("-50")


def test_totaux_montant_manquant():
    controle, alertes = verifier_totaux(None, "200", "1200")
    assert not controle.ok and controle.ecart is None
    assert any("verification impossible" in a for a in alertes)


def test_totaux_montant_ambigu_signale_le_champ():
    controle, alertes = verifier_totaux("1.000", "200", "1200")
    assert not controle.ok
    assert any(a.startswith("montant_ht : ambigu") for a in alertes)
