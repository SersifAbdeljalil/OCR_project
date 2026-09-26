"""
test_extractor.py - Tests de l'extraction par regex (src/extractor.py).

Documents INVENTES, un par categorie, avec des pieges (montant mal formate,
deux dates, ligne OCR peu sure, valeur collee a de l'arabe, TVA non applicable...).
Lancement (venv active, depuis OCR_PROJECT) :  python -m pytest -v
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.config import charger_registre
from src.extract_text import extraire
from src.extractor import (ErreurConfigExtraction, LigneSource, charger_config,
                           extraire_champs, extraire_champs_libres, extraire_document,
                           lignes_du_document, localiser, normaliser_ligne, tolerer_ocr)
from src.ocr_worker import LigneOCR, PageOCR

REGISTRE = charger_registre()
CONFIG = charger_config()
SYNTH = Path(__file__).parent / "docs_synthetiques"


def natif(texte: str) -> list:
    """Lignes d'un texte natif (sans confiance OCR)."""
    return [LigneSource(l) for l in texte.strip().splitlines() if l.strip()]


def extraire_texte(texte, categorie="factures", lignes=None):
    return extraire_champs(lignes or natif(texte), categorie, REGISTRE, CONFIG)


FACTURE = """Société Exemple SARL
ICE : 001 234 567 000 089
FACTURE
N° : FA-2026-0007
Date : 15/09/2026
Total HT :
1 000,00 DH
TVA 20 % :
200,00 DH
Total TTC :
1 200,00 DH
Paiement par virement : RIB 999 780 0000123456789012 34"""


# --- 1. Facture de reference -------------------------------------------------
def test_facture_complete():
    r = extraire_texte(FACTURE)
    v = r.valeurs()
    assert v == {"date_facture": "2026-09-15", "numero": "FA-2026-0007",
                 "montant_ht": Decimal("1000.00"), "tva": Decimal("200.00"),
                 "montant_ttc": Decimal("1200.00"), "ice": "001234567000089",
                 "rib": "999780000012345678901234"}
    assert r.statut_totaux == "ok" and not r.necessite_validation_humaine
    assert r.alertes == []


def test_provenance_de_chaque_champ():
    r = extraire_texte(FACTURE)
    c = r.champs["montant_ttc"]
    assert c.methode == "regex" and c.confiance is None           # texte natif
    assert c.ligne == 10 and c.etiquette == 0 and c.nb_candidats == 1


def test_etiquette_et_valeur_sur_la_meme_ligne():
    r = extraire_texte("Total HT : 1 000,00 DH TVA 20 % : 200,00 DH\nTotal TTC : 1 200,00 DH")
    assert r.valeurs()["montant_ht"] == Decimal("1000.00")
    assert r.valeurs()["tva"] == Decimal("200.00")                # le taux 20 % est ignore


def test_tableau_excel_avec_separateurs():
    r = extraire_texte("Total HT | 1 988,00\nTVA 20 % | 397,60\nTotal TTC | 2 385,60")
    assert r.statut_totaux == "ok"


# --- 2. Pieges sur les montants ----------------------------------------------
def test_tva_non_applicable_ne_donne_pas_91():
    r = extraire_texte("Total HT :\n1 500,00 DH\nTVA :\nNon applicable (art. 91 CGI)\n"
                       "Net à payer :\n1 500,00 DH")
    assert "tva" not in r.champs
    assert r.valeurs()["montant_ttc"] == Decimal("1500.00")      # « net a payer »
    assert r.statut_totaux == "ok" and "totaux : facture sans TVA" in r.alertes


def test_plusieurs_taux_de_tva_additionnes():
    r = extraire_texte("Total HT : 2 900,00\nTVA 20 % : 400,00\nTVA 14 % : 42,00\n"
                       "TVA 10 % : 50,00\nTVA 7 % : 7,00\nTotal TTC : 3 399,00")
    assert r.valeurs()["tva"] == Decimal("499.00")
    assert r.champs["tva"].detail == [Decimal("400.00"), Decimal("42.00"),
                                      Decimal("50.00"), Decimal("7.00")]
    assert r.statut_totaux == "ok"


def test_total_tva_prioritaire_pas_de_double_compte():
    r = extraire_texte("Total HT : 1 000,00\nTVA 20 % : 150,00\nTVA 10 % : 50,00\n"
                       "Total TVA : 200,00\nTotal TTC : 1 200,00")
    assert r.valeurs()["tva"] == Decimal("200.00")
    assert r.statut_totaux == "ok"


def test_montant_mal_formate_ambigu():
    r = extraire_texte("Total HT : 1.000 DH\nTotal TTC : 1 200,00 DH")
    assert "montant_ht" not in r.champs
    assert "montant_ht : ambigu (separateur suivi de 3 chiffres)" in r.alertes
    assert r.statut_totaux == "manquant" and r.necessite_validation_humaine


def test_chiffre_isole_dans_du_bruit_ignore():
    """Bruit OCR apres l'etiquette : un « 9 » sans decimales ni devise n'est pas un montant."""
    r = extraire_texte("TVA 20 %/ &9LaIl &osll :\nTotal TTC / pgw 2o &ga :")
    assert "tva" not in r.champs and "montant_ttc" not in r.champs


def test_ecart_de_totaux():
    r = extraire_texte("Total HT : 2 400,00\nTVA 20 % : 480,00\nTotal TTC : 2 980,00")
    assert r.statut_totaux == "ecart" and r.necessite_validation_humaine
    assert r.controle_totaux.ecart == Decimal("-100.00")


def test_montants_format_europeen():
    r = extraire_texte("Total HT :\n5.455,00 DH\nTVA 20 % :\n1.091,00 DH\n"
                       "Net à payer TTC :\n6.546,00 DH")
    assert r.valeurs()["montant_ttc"] == Decimal("6546.00") and r.statut_totaux == "ok"


# --- 3. Pieges sur les dates et numeros ---------------------------------------
def test_deux_dates_premiere_retenue_avec_alerte():
    r = extraire_texte("Date : 15/09/2026\nClient : Exemple\nDate d'échéance : 15/10/2026")
    assert r.valeurs()["date_facture"] == "2026-09-15"
    assert "date_facture : 2 valeurs candidates differentes, retenue selon la regle " \
           "« premier »" in r.alertes


def test_date_de_facture_prioritaire_sur_date():
    r = extraire_texte("Date : 01/01/2026\nDate de facture : 15/09/2026")
    assert r.valeurs()["date_facture"] == "2026-09-15"


def test_date_en_lettres_et_annee_sur_deux_chiffres():
    assert extraire_texte("Date : 1er août 2026").valeurs()["date_facture"] == "2026-08-01"
    assert extraire_texte("Date : 18/09/26").valeurs()["date_facture"] == "2026-09-18"


def test_numero_colle_a_de_l_arabe():
    r = extraire_texte("N° Facture : RFD-26-889201رقم الفاتورة")
    assert r.valeurs()["numero"] == "RFD-26-889201"


@pytest.mark.parametrize("ligne, attendu", [
    ("N° : MI/2026/0387", "MI/2026/0387"),
    ("N° : TC 2026 / 12", "TC 2026 / 12"),
    ("Facture n° 2026-017", "2026-017"),
    ("N° : 118 Date : 15/09/2026", "118"),              # s'arrete avant « Date »
])
def test_formats_de_numero(ligne, attendu):
    assert extraire_texte(ligne).valeurs()["numero"] == attendu


def test_numero_sans_chiffre_refuse():
    assert "numero" not in extraire_texte("N° : ABC").champs


# --- 4. Lignes OCR peu sures -----------------------------------------------------
def test_ligne_ocr_sous_090_impose_la_validation():
    lignes = [LigneSource("Total HT :", 0.99), LigneSource("1 000,00 DH", 0.99),
              LigneSource("TVA 20 % :", 0.98), LigneSource("200,00 DH", 0.97),
              LigneSource("Total TTC :", 0.99), LigneSource("1 200,00 DH", 0.75)]
    r = extraire_texte("", lignes=lignes)
    assert r.statut_totaux == "ok"
    assert r.champs["montant_ttc"].confiance == 0.75       # min(etiquette, valeur)
    assert "montant_ttc : ligne OCR peu sure (confiance 0.75)" in r.alertes
    assert r.necessite_validation_humaine


def test_ligne_ocr_sure_pas_de_validation():
    lignes = [LigneSource("Date : 10/09/2026", 0.99)]
    r = extraire_texte("", "attestations", lignes=lignes)
    assert r.champs["date"].confiance == 0.99 and not r.necessite_validation_humaine


def test_facture_sans_montants_impose_la_validation():
    r = extraire_texte("", lignes=[LigneSource("Date : 10/09/2026", 0.99)])
    assert r.statut_totaux == "manquant" and r.necessite_validation_humaine


# --- 5. Autres categories ----------------------------------------------------------
def test_diplome_date_naissance_cin_et_obtention():
    texte = """DIPLÔME DE LICENCE
Titulaire : Prénom Nom, né le 03/04/1990 à Rabat
CIN : AB 123456
Fait à Rabat, le 12 juillet 2020"""
    v = extraire_texte(texte, "diplomes").valeurs()
    assert v == {"date_obtention": "2020-07-12", "cin": "AB123456",
                 "date_naissance": "1990-04-03"}


def test_nee_le_au_feminin():
    v = extraire_texte("Titulaire : Prénom Nom, née le 5 mars 1995", "diplomes").valeurs()
    assert v["date_naissance"] == "1995-03-05"


def test_contrat_date_de_signature():
    texte = "CONTRAT DE BAIL\nArticle 1 : Objet\nFait à Casablanca, le 05/01/2026"
    assert extraire_texte(texte, "contrats").valeurs() == {"date_signature": "2026-01-05"}


def test_attestation_date_generique_et_cin():
    texte = "ATTESTATION DE TRAVAIL\nC.I.N. n° J 54321\nFait à Fès, le 20/09/2026"
    v = extraire_texte(texte, "attestations").valeurs()
    assert v == {"date": "2026-09-20", "cin": "J54321"}


def test_banque_rib_et_iban():
    texte = ("RELEVÉ D'IDENTITÉ BANCAIRE\nRIB : 999 780 0000123456789012 34\n"
             "IBAN : MA64 9997 8000 0012 3456 7890 1234")
    v = extraire_texte(texte, "banque").valeurs()
    assert v == {"rib": "999780000012345678901234", "iban": "MA64999780000012345678901234"}


def test_rib_de_mauvaise_longueur_refuse():
    assert "rib" not in extraire_texte("RIB : 999 780 0000123456", "banque").champs


def test_categorie_decouverte_champs_generiques():
    v = extraire_texte("Bulletin\nDate : 30/09/2026", "bulletin_paie").valeurs()
    assert v == {"date": "2026-09-30"}


def test_champs_libres_laisses_au_llm():
    """fournisseur, titulaire, objet... n'ont pas de motif : ils ne sont pas extraits ici."""
    r = extraire_texte(FACTURE)
    assert "fournisseur" not in r.champs


# --- 5 bis. Tolerance OCR (etiquettes seulement) et RIB en 4 groupes ----------------
@pytest.mark.parametrize("motif, attendu", [
    (r"\btotal\s*ht", r"\bt[o0]ta[l1i]\s*ht"),
    (r"\bi\.?c\.?e\.?(?![a-z])", r"\b[i1l]\.?c\.?e\.?(?![a-z])"),
    (r"(?<![a-z])n\s?[°º]", r"(?<![a-z])n\s?[°º]"),             # classes et \s intacts
    (r"\bdate\s+de\s+naissance", r"\bdate\s+de\s+na[i1l][s5][s5]ance"),
])
def test_tolerer_ocr(motif, attendu):
    assert tolerer_ocr(motif) == attendu


@pytest.mark.parametrize("ligne", ["T0tal HT : 980,00 DH", "TotaI HT : 980,00 DH",
                                   "Tota1 HT : 980,00 DH"])
def test_etiquette_mal_lue_par_l_ocr(ligne):
    assert extraire_texte(ligne).valeurs()["montant_ht"] == Decimal("980.00")


def test_ice_mal_lu_par_l_ocr():
    assert extraire_texte("1CE000666777000088").valeurs()["ice"] == "000666777000088"


def test_devise_collee_au_montant():
    assert extraire_texte("Total TTC : 1 200,00DH").valeurs()["montant_ttc"] == Decimal("1200.00")


def test_valeurs_restent_strictes():
    """La tolerance ne s'applique JAMAIS aux valeurs : « 1 2O0,00 » n'est pas un montant."""
    r = extraire_texte("Total HT : 1 2O0,00 DH\nICE : 0OO666777000088")
    assert "montant_ht" not in r.champs and "ice" not in r.champs


@pytest.mark.parametrize("texte", [
    "RIB : 999 780 0000123456789012 34",
    "RIB :\t999\t780\t0000123456789012\t34",
    "RIB : 999 | 780 | 0000123456789012 | 34",
    "RIB : 999/780/0000123456789012/34",
    "RIB :\n999\n780\n0000123456789012\n34",
    "RIB\n999 780\n0000123456789012 34",
])
def test_rib_en_quatre_groupes(texte):
    assert extraire_texte(texte, "banque").valeurs()["rib"] == "999780000012345678901234"


def test_rib_ne_prend_pas_une_ligne_de_texte():
    """Les lignes suivantes ne completent la valeur que si elles ne contiennent que des chiffres."""
    r = extraire_texte("RIB :\n999 780\nCompte n° 0000123456789012 34", "banque")
    assert "rib" not in r.champs


def test_rib_groupes_de_mauvaise_taille_refuse():
    assert "rib" not in extraire_texte("RIB : 99 780 0000123456789012 34", "banque").champs


# --- 6. Lignes du document et normalisation ----------------------------------------
def test_normaliser_ligne_garde_la_longueur():
    for texte in ["Désignation Qté", "ÉTÉ à Fès", "ﻓﺎﺗﻮرة ﻻ", "N° : FA-1"]:
        assert len(normaliser_ligne(texte)) == len(texte)
    assert normaliser_ligne("Désignation ÉTÉ") == "designation ete"


def test_lignes_du_document_ordre_des_pages(tmp_path):
    class Extraction:
        textes_pages = ["Page un\nDate : 01/01/2026", "", "Page trois"]
        statut_pages = ["texte", "OCR requis", "texte"]
        texte = ""
    pages = {2: PageOCR(fichier="x", page=2, statut="ok",
                        lignes=[LigneOCR("Ligne OCR", 0.8, [[0, 0]] * 4)])}
    lignes = lignes_du_document(Extraction(), pages)
    assert [(l.texte, l.confiance, l.page) for l in lignes] == [
        ("Page un", None, 1), ("Date : 01/01/2026", None, 1),
        ("Ligne OCR", 0.8, 2), ("Page trois", None, 3)]


# --- 7. Configuration -----------------------------------------------------------------
def test_config_du_projet_valide():
    assert {"numero", "date_facture", "montant_ht", "tva", "montant_ttc",
            "cin", "rib", "iban", "ice", "date_naissance"} <= set(CONFIG)


@pytest.mark.parametrize("champ, message", [
    ({"type": "inconnu", "etiquettes": ["x"]}, "type inconnu"),
    ({"type": "date", "etiquettes": ["(x"]}, "regex invalide"),
    ({"type": "texte", "etiquettes": ["x"]}, "motif 'valeur' obligatoire"),
    ({"type": "date", "etiquettes": []}, "aucune etiquette"),
    ({"type": "date", "etiquettes": ["x"], "choix": "hasard"}, "choix inconnu"),
])
def test_config_invalide(tmp_path, champ, message):
    chemin = tmp_path / "extraction.json"
    chemin.write_text(json.dumps({"champs": {"essai": champ}}), encoding="utf-8")
    with pytest.raises(ErreurConfigExtraction, match=message):
        charger_config(chemin)


# --- 8. Confidentialite ---------------------------------------------------------------
def test_ni_valeur_dans_les_alertes_ni_dans_repr():
    texte = FACTURE.replace("Total TTC :\n1 200,00 DH", "Total TTC :\n1.200 DH") + \
        "\nCIN : XY 999888"
    r = extraire_texte(texte)
    tout = repr(r) + " ".join(r.alertes)
    for secret in ("999888", "FA-2026-0007", "001234567000089", "99978000"):
        assert secret not in tout


# --- 8 bis. Champs LIBRES par LLM (simule) et fusion ----------------------------------------
class FausseReponse:
    def __init__(self, donnees):
        self._d = donnees

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class FausseSession:
    """Faux Ollama : renvoie la reponse JSON donnee et garde les requetes."""

    def __init__(self, reponse, panne=None):
        self.reponse, self.panne, self.posts = reponse, panne, []

    def post(self, url, json=None, timeout=None):
        self.posts.append(json)
        if self.panne:
            raise self.panne
        if isinstance(self.reponse, str):
            return FausseReponse({"response": self.reponse})
        # Comme Ollama avec un schema : toutes les cles demandees sont presentes
        # (null si le test ne donne pas de valeur)
        complete = {cle: None for cle in json["format"]["properties"]}
        complete.update(self.reponse)
        return FausseReponse({"response": __import__("json").dumps(complete)})


def client_simule(reponse, panne=None):
    from src.llm import ClientOllama
    return ClientOllama(session=FausseSession(reponse, panne))


def test_llm_ne_remplit_que_les_champs_libres():
    client = client_simule({"fournisseur": "Société Exemple SARL"})
    r = extraire_document(natif(FACTURE), "factures", client, REGISTRE, CONFIG)
    assert r.valeurs()["fournisseur"] == "Société Exemple SARL"
    assert r.champs["fournisseur"].methode == "llm"
    assert r.champs["montant_ttc"].methode == "regex"
    schema = client.session.posts[0]["format"]
    assert set(schema["properties"]) == {"fournisseur", "adresse"}   # facture : champs libres
    assert "- fournisseur : nom de l'entreprise" in client.session.posts[0]["prompt"]


def test_champs_libres_selon_la_categorie():
    client = client_simule({"titulaire": "Prénom Nom", "intitule": "Licence",
                            "etablissement": "Faculté Exemple", "mention": "Bien"})
    texte = ("DIPLÔME DE LICENCE\nFaculté Exemple\nTitulaire : Prénom Nom\nmention Bien\n"
             "Fait à Rabat, le 12 juillet 2020")
    r = extraire_document(natif(texte), "diplomes", client, REGISTRE, CONFIG)
    assert set(client.session.posts[0]["format"]["properties"]) == \
        {"titulaire", "intitule", "etablissement", "mention", "adresse"}
    assert r.valeurs()["titulaire"] == "Prénom Nom"
    assert r.valeurs()["date_obtention"] == "2020-07-12"          # regex


def test_structure_absent_reste_absent():
    """Meme si le LLM renvoyait une date, elle n'est pas demandee ni lue."""
    client = client_simule({"fournisseur": "Société Exemple SARL",
                            "date_facture": "2026-01-01", "montant_ttc": "999"})
    r = extraire_document(natif("Société Exemple SARL\nFACTURE"), "factures", client,
                          REGISTRE, CONFIG)
    assert "date_facture" not in r.champs and "montant_ttc" not in r.champs


def test_anti_invention_valeur_rejetee():
    client = client_simule({"fournisseur": "Maroc Telecom SA"})     # pas dans le texte
    r = extraire_document(natif(FACTURE), "factures", client, REGISTRE, CONFIG)
    assert "fournisseur" not in r.champs
    assert "fournisseur : valeur du moteur absente du texte source, rejetee" in r.alertes
    assert r.necessite_validation_humaine


@pytest.mark.parametrize("valeur", ["SOCIETE EXEMPLE SARL", "societe  exemple   sarl",
                                    "Société\nExemple SARL"])
def test_anti_invention_tolerant_accents_casse_espaces(valeur):
    client = client_simule({"fournisseur": valeur})
    r = extraire_document(natif(FACTURE), "factures", client, REGISTRE, CONFIG)
    assert "fournisseur" in r.champs


def test_valeur_sur_deux_lignes_retrouvee():
    lignes = [LigneSource("Atlas Fournitures", 0.99), LigneSource("Bureau SARL", 0.85)]
    trouvee, ligne, confiance = localiser("Atlas Fournitures Bureau SARL", lignes)
    assert trouvee and ligne == 0 and confiance == 0.85


def test_valeur_llm_sur_ligne_ocr_peu_sure():
    lignes = [LigneSource("Société Exemple SARL", 0.70), LigneSource("FACTURE", 0.99)]
    r = extraire_champs_libres(lignes, "factures",
                               client_simule({"fournisseur": "Société Exemple SARL"}), REGISTRE)
    assert r.champs["fournisseur"].confiance == 0.70
    assert "fournisseur : ligne OCR peu sure (confiance 0.70)" in r.alertes
    assert r.necessite_validation_humaine


def test_null_reste_absent_sans_alerte():
    r = extraire_champs_libres(natif(FACTURE), "factures", client_simule({"fournisseur": None}),
                               REGISTRE)
    assert r.champs == {} and r.alertes == [] and not r.necessite_validation_humaine


def test_moteur_en_panne_echec_propre():
    import requests
    client = client_simule({}, panne=requests.exceptions.ConnectionError("coupe"))
    r = extraire_document(natif(FACTURE), "factures", client, REGISTRE, CONFIG)
    assert "fournisseur" not in r.champs and r.valeurs()["montant_ttc"] == Decimal("1200.00")
    assert any(a.startswith("champs libres : moteur indisponible") for a in r.alertes)
    assert r.necessite_validation_humaine


def test_sans_client_regex_seulement():
    r = extraire_document(natif(FACTURE), "factures", None, REGISTRE, CONFIG)
    assert "fournisseur" not in r.champs and "numero" in r.champs


def test_categorie_decouverte_champs_generiques_llm():
    client = client_simule({"titre": "Bulletin de paie", "personne": "Prénom Nom",
                            "organisme": "Société Exemple"})
    texte = "Bulletin de paie\nSociété Exemple\nSalarié : Prénom Nom\nDate : 30/09/2026"
    r = extraire_document(natif(texte), "bulletin_paie", client, REGISTRE, CONFIG)
    assert set(client.session.posts[0]["format"]["properties"]) == {"titre", "personne",
                                                                     "organisme", "adresse"}
    assert r.valeurs() == {"date": "2026-09-30", "titre": "Bulletin de paie",
                           "personne": "Prénom Nom", "organisme": "Société Exemple"}


# --- 8 ter. Controles du fournisseur, duree / periode / adresse --------------------------
FACTURE_AVEC_CLIENT = """Société Exemple SARL
ICE : 001 234 567 000 089
FACTURE
N° : FA-2026-0007
Client : Cabinet Fictif Destinataire, Casablanca
Total TTC : 1 200,00 DH"""


@pytest.mark.parametrize("ligne_client", [
    "Client : Cabinet Fictif Destinataire, Casablanca",
    "Destinataire : Cabinet Fictif Destinataire",
    "Facturé à : Cabinet Fictif Destinataire",
    "Doit : Cabinet Fictif Destinataire",
    "À l'attention de Cabinet Fictif Destinataire",
    "Adressé à Cabinet Fictif Destinataire",
    "C1ient : Cabinet Fictif Destinataire",                  # etiquette mal lue par l'OCR
])
def test_fournisseur_sur_ligne_destinataire_rejete(ligne_client):
    texte = FACTURE_AVEC_CLIENT.replace(
        "Client : Cabinet Fictif Destinataire, Casablanca", ligne_client)
    client = client_simule({"fournisseur": "Cabinet Fictif Destinataire"})
    r = extraire_document(natif(texte), "factures", client, REGISTRE, CONFIG)
    assert "fournisseur" not in r.champs
    assert "fournisseur : valeur trouvee seulement sur une ligne de destinataire " \
           "(client...), rejetee" in r.alertes
    assert r.necessite_validation_humaine


def test_fournisseur_sous_une_etiquette_seule_rejete():
    texte = "Société Exemple SARL\nFACTURE\nFacturé à :\nCabinet Fictif Destinataire"
    client = client_simule({"fournisseur": "Cabinet Fictif Destinataire"})
    r = extraire_document(natif(texte), "factures", client, REGISTRE, CONFIG)
    assert "fournisseur" not in r.champs


def test_vrai_fournisseur_accepte_malgre_la_ligne_client():
    client = client_simule({"fournisseur": "Société Exemple SARL"})
    r = extraire_document(natif(FACTURE_AVEC_CLIENT), "factures", client, REGISTRE, CONFIG)
    assert r.valeurs()["fournisseur"] == "Société Exemple SARL"
    assert not any("destinataire" in a or "zone titre" in a for a in r.alertes)


def test_nom_present_en_entete_et_sur_ligne_client_accepte():
    """Une apparition hors ligne de destinataire suffit (ex. auto-facturation)."""
    texte = "Société Exemple SARL\nFACTURE\nClient : Société Exemple SARL"
    client = client_simule({"fournisseur": "Société Exemple SARL"})
    r = extraire_document(natif(texte), "factures", client, REGISTRE, CONFIG)
    assert "fournisseur" in r.champs and r.champs["fournisseur"].ligne == 0


def test_fournisseur_hors_zone_titre_alerte_et_validation():
    lignes = natif(FACTURE) + [LigneSource(f"ligne {i}") for i in range(20)] + \
        [LigneSource("Émis par : Autre Société Fictive")]
    client = client_simule({"fournisseur": "Autre Société Fictive"})
    r = extraire_document(lignes, "factures", client, REGISTRE, CONFIG)
    assert r.valeurs()["fournisseur"] == "Autre Société Fictive"      # garde, mais...
    assert "fournisseur : valeur hors de la zone titre, a verifier" in r.alertes
    assert r.necessite_validation_humaine


@pytest.mark.parametrize("categorie, attendus", [
    ("contrats", {"parties", "objet", "duree", "adresse"}),
    ("banque", {"banque", "titulaire", "objet", "periode", "adresse"}),
    ("attestations", {"emetteur", "beneficiaire", "objet", "adresse"}),
])
def test_duree_periode_adresse_demandes(categorie, attendus):
    client = client_simule({})
    extraire_champs_libres(natif("texte"), categorie, client, REGISTRE)
    assert set(client.session.posts[0]["format"]["properties"]) == attendus


def test_duree_et_adresse_anti_invention():
    texte = "CONTRAT DE BAIL\nConclu pour une durée de douze mois\n12, rue Fictive, Rabat"
    client = client_simule({"duree": "douze mois", "adresse": "5, avenue Inventée, Fès"})
    r = extraire_champs_libres(natif(texte), "contrats", client, REGISTRE)
    assert r.valeurs() == {"duree": "douze mois"}
    assert "adresse : valeur du moteur absente du texte source, rejetee" in r.alertes


def test_aucune_valeur_llm_dans_alertes_ni_repr():
    client = client_simule({"fournisseur": "Invention Secrète SA"})
    r = extraire_document(natif(FACTURE), "factures", client, REGISTRE, CONFIG)
    assert "Invention" not in repr(r) + " ".join(r.alertes)


# --- 9. Regression sur les factures fictives (PDF natifs, sans OCR) ---------------------
@pytest.mark.skipif(not SYNTH.is_dir(), reason="jeu synthetique absent")
@pytest.mark.parametrize("nom", ["f01_fr_standard_natif.pdf", "f02_fr_multi_tva_natif.pdf",
                                 "f03_fr_sans_tva_natif.pdf", "f04_fr_ecart_totaux_natif.pdf",
                                 "f05_bilingue_natif.pdf",
                                 "f10_fr_montants_europeens_natif.pdf"])
def test_factures_fictives_natives_exactes(nom):
    attendu = json.loads((SYNTH / "verite_terrain.json").read_text(encoding="utf-8"))[nom]
    r = extraire_champs(lignes_du_document(extraire(SYNTH / nom)), "factures", REGISTRE, CONFIG)
    v = r.valeurs()
    for champ in ("date_facture", "numero", "ice"):
        assert v.get(champ) == attendu[champ], champ
    for champ in ("montant_ht", "tva", "montant_ttc"):
        assert v.get(champ) == (Decimal(attendu[champ]) if attendu[champ] else None), champ
