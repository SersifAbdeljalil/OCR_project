"""
test_interface.py - Tests de l'interface Streamlit (app/streamlit_app.py) avec
l'outil de test de Streamlit (AppTest), sans navigateur.

Documents FICTIFS uniquement ; dossiers et registre TEMPORAIRES (variables
TRI_DOSSIER_ENTREE / SORTIE / DATA et TRI_REGISTRE) : l'interface n'est jamais
lancee sur les documents reels. Aucun tri ni LLM reel n'est lance.
"""

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import validation
from src.config import CHEMIN_REGISTRE, charger_registre
from src.filer import ranger_document
from src.schemas import DocumentSortie

RACINE = Path(__file__).resolve().parent.parent
APP = RACINE / "app" / "streamlit_app.py"
SYNTH = Path(__file__).parent / "docs_synthetiques"
REGISTRE = charger_registre()
TEXTE = ("Société Exemple SARL\nFACTURE\nN° : FA-2026-0007\nDate : 15/09/2026\n"
         "Total HT : 1 000,00 DH\nTVA 20 % : 200,00 DH\nTotal TTC : 1 200,00 DH")
CHAMPS = {"fournisseur": "Société Exemple SARL", "date_facture": "2026-09-15",
          "numero": "FA-2026-0007", "montant_ht": Decimal("1000.00"),
          "tva": Decimal("200.00"), "montant_ttc": Decimal("1200.00")}


@pytest.fixture
def espace(tmp_path, monkeypatch):
    e, s, d = tmp_path / "Folder_Entree", tmp_path / "Folder_Sortie", tmp_path / "data"
    e.mkdir()
    registre = tmp_path / "categories.json"
    shutil.copy2(CHEMIN_REGISTRE, registre)
    for nom, valeur in (("TRI_DOSSIER_ENTREE", e), ("TRI_DOSSIER_SORTIE", s),
                        ("TRI_DOSSIER_DATA", d), ("TRI_REGISTRE", registre)):
        monkeypatch.setenv(nom, str(valeur))
    return e, s, d, registre


def deposer(espace, type_="factures", champs=None, confiance=0.95, validation_=False,
            texte=TEXTE, nom="scan_001.pdf"):
    e, s, _, _ = espace
    doc = DocumentSortie.model_validate({
        "type": type_, "source": nom, "date_traitement": "2026-09-25T10:00:00",
        "confiance_classification": confiance, "champs": CHAMPS if champs is None else champs,
        "necessite_validation_humaine": validation_, "texte_brut": texte},
        context={"registre": REGISTRE})
    original = e / nom
    shutil.copy2(SYNTH / "f01_fr_standard_natif.pdf", original)       # vrai PDF FICTIF
    r = ranger_document(original, doc, ["alerte fictive"], REGISTRE, s, e)
    return next(p for p in r.fichiers if p.suffix == ".json")


def demarrer(ecran=None):
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    if ecran:
        at.sidebar.radio(key="ecran").set_value(ecran).run()
    assert not at.exception, [x.value for x in at.exception]
    return at


def champ(at, nom):
    return next(t for t in at.text_input if t.label == nom)


def textes(elements):
    return " ".join(str(x.value) for x in elements)


# --- 1. Ecran « Deposer et trier » ------------------------------------------------------
def test_ecran_deposer_sans_document(espace):
    at = demarrer()
    assert "Documents en attente dans le dossier d'entrée : **0**" in textes(at.markdown)
    assert at.button(key="lancer_tri").disabled


def test_ecran_deposer_avec_documents_en_attente(espace):
    e = espace[0]
    shutil.copy2(SYNTH / "f01_fr_standard_natif.pdf", e / "a.pdf")
    at = demarrer()
    assert "**1**" in textes(at.markdown)
    assert not at.button(key="lancer_tri").disabled


def test_tri_en_cours_progression_et_bouton_bloque(espace, monkeypatch):
    monkeypatch.setattr(validation, "etat_tri", lambda d: {
        "en_cours": True, "progression": "[3/10] lecture", "resume": None})
    at = demarrer()
    assert "Tri en cours… [3/10] lecture" in textes(at.info)
    assert at.button(key="lancer_tri").disabled


def test_resume_du_dernier_tri(espace, monkeypatch):
    monkeypatch.setattr(validation, "etat_tri", lambda d: {
        "en_cours": False, "progression": None,
        "resume": {"ranges": 7, "a_valider": 2, "erreurs": 1, "duree_s": 95}})
    at = demarrer()
    valeurs = {m.label: m.value for m in at.metric}
    assert valeurs == {"Rangés": "7", "À valider": "2", "Erreurs": "1", "Durée (s)": "95"}


def test_lancer_le_tri_appelle_le_sous_process(espace, monkeypatch):
    e = espace[0]
    shutil.copy2(SYNTH / "f01_fr_standard_natif.pdf", e / "a.pdf")
    appels = []
    monkeypatch.setattr(validation, "lancer_tri",
                        lambda d, commande: appels.append(commande) or (True, "tri lance"))
    at = demarrer()
    at.button(key="lancer_tri").click().run()
    assert appels and appels[0][1].endswith("run_pipeline.py")
    assert appels[0][2:] == ["--entree", str(e), "--sortie", str(espace[1])]


# --- 2. Ecran « Documents » --------------------------------------------------------------
def test_aucun_document(espace):
    at = demarrer("Documents")
    assert "Aucun document traité" in textes(at.info)


def test_liste_a_valider_en_premier_et_champs(espace):
    deposer(espace)
    deposer(espace, confiance=0.60, validation_=True, nom="scan_002.pdf")
    at = demarrer("Documents")
    doc = at.selectbox(key="document")
    assert len(doc.options) == 2 and "A_Valider" in doc.value           # a valider d'abord
    assert champ(at, "montant_ttc").value == "1200.00"
    assert champ(at, "fournisseur").value == "Société Exemple SARL"
    codes = textes(at.code)
    assert "FA-2026-0007" in codes and "1200.00" in codes               # icones de copie
    assert "Raison : validation humaine necessaire" in textes(at.warning)


def test_filtre_par_statut(espace):
    deposer(espace)
    deposer(espace, confiance=0.60, validation_=True, nom="scan_002.pdf")
    at = demarrer("Documents")
    at.selectbox(key="filtre_statut").set_value("range").run()
    assert len(at.selectbox(key="document").options) == 1


def test_valider_avec_correction(espace):
    chemin = deposer(espace, confiance=0.60, validation_=True)
    at = demarrer("Documents")
    champ(at, "tva").set_value("200,00")
    champ(at, "numero").set_value("FA-2026-0099").run()
    at.button(key="valider").click().run()
    assert not at.exception
    assert "Document validé (1 correction(s))." in textes(at.success)
    nouveau = next((espace[1] / "Factures").glob("*.json"))
    info = json.loads(nouveau.read_text(encoding="utf-8"))
    assert info["champs"]["numero"] == "FA-2026-0099" and not info["necessite_validation_humaine"]
    assert not chemin.exists()


def test_valeur_invalide_message_d_erreur(espace):
    chemin = deposer(espace)
    at = demarrer("Documents")
    champ(at, "date_facture").set_value("31/02/2026").run()
    at.button(key="valider").click().run()
    assert "date_facture : date invalide" in textes(at.error)
    assert chemin.exists()


def test_rejeter(espace):
    deposer(espace, confiance=0.60, validation_=True)
    at = demarrer("Documents")
    at.button(key="rejeter").click().run()
    assert "Document rejeté" in textes(at.success)
    assert list((espace[1] / "Autres").glob("*.json"))


def test_changer_de_categorie(espace):
    deposer(espace)
    at = demarrer("Documents")
    at.selectbox(key=next(s.key for s in at.selectbox if s.key.startswith("categorie_"))) \
        .set_value("contrats").run()
    assert [t.label for t in at.text_input if t.label in ("parties", "duree")] == \
        ["parties", "duree"]
    at.button(key="valider").click().run()
    assert list((espace[1] / "Contrats").glob("*.json"))


def test_creer_une_categorie(espace, monkeypatch):
    deposer(espace, "bulletin_de_paie", {"titre": "Bulletin de paie"}, 0.60, True,
            texte="Bulletin de paie\nSalaire net\nCotisations")
    monkeypatch.setattr(validation, "proposer_mots_cles",
                        lambda nom, texte, client: ["Bulletin de paie", "Salaire net",
                                                    "Cotisations"])
    at = demarrer("Documents")
    assert "absente du registre" in textes(at.warning)
    at.button(key="proposer").click().run()
    assert at.text_area[0].value == "Bulletin de paie\nSalaire net\nCotisations"
    at.button(key="creer").click().run()
    assert not at.exception
    assert "Catégorie « bulletin_de_paie » créée" in textes(at.success)
    registre = charger_registre(espace[3])
    assert any(c["nom"] == "bulletin_de_paie" for c in registre["categories"])
    assert list((espace[1] / "Bulletin_de_paie").glob("*.json"))
    assert charger_registre(CHEMIN_REGISTRE) == REGISTRE                 # vrai registre intact


def test_texte_complet_et_historique(espace):
    deposer(espace)
    at = demarrer("Documents")
    assert TEXTE in textes(at.code)                                       # texte copiable
    assert "Aucune correction." in textes(at.markdown)
