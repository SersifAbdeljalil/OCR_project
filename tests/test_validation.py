"""
test_validation.py - Tests de la logique de validation humaine (src/validation.py).

Documents FICTIFS uniquement, dans des dossiers temporaires (tmp_path). Le vrai
Folder_Sortie et le vrai config/categories.json ne sont jamais touches.
"""

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from src.config import CHEMIN_REGISTRE, charger_registre
from src.filer import envoyer_a_valider, ranger_document
from src.llm import ClientOllama
from src.schemas import DocumentSortie
from src.validation import (ErreurValidation, apercu_page, charger_document,
                            creer_categorie, deposer_fichiers, enregistrer_corrections,
                            etat_tri, lancer_tri, lister_documents, mot_cle_vers_regex,
                            nom_de_categorie, proposer_mots_cles, rejeter_document,
                            sous_dossier_actuel)

REGISTRE = charger_registre()
TEXTE = ("Société Exemple SARL\nFACTURE\nN° : FA-2026-0007\nDate : 15/09/2026\n"
         "Total HT : 1 000,00 DH\nTVA 20 % : 200,00 DH\nTotal TTC : 1 200,00 DH")
CHAMPS = {"fournisseur": "Société Exemple SARL", "date_facture": "2026-09-15",
          "numero": "FA-2026-0007", "montant_ht": Decimal("1000.00"),
          "tva": Decimal("200.00"), "montant_ttc": Decimal("1200.00")}


# --- Outils ---------------------------------------------------------------------------
@pytest.fixture
def espace(tmp_path):
    e, s, logs = tmp_path / "Folder_Entree", tmp_path / "Folder_Sortie", tmp_path / "logs"
    e.mkdir()
    return e, s, logs


def document(type_="factures", champs=None, confiance=0.95, validation=False, texte=TEXTE,
             source="scan_001.pdf"):
    return DocumentSortie.model_validate({
        "type": type_, "source": source, "date_traitement": "2026-09-25T10:00:00",
        "confiance_classification": confiance,
        "champs": CHAMPS if champs is None else champs,
        "necessite_validation_humaine": validation, "texte_brut": texte},
        context={"registre": REGISTRE})


def deposer(espace, doc, nom="scan_001.pdf", supplement=None, alertes=None):
    e, s, _ = espace
    original = e / nom
    original.write_bytes(b"%PDF fictif " + nom.encode())
    r = ranger_document(original, doc, alertes or ["alerte fictive"], REGISTRE, s, e,
                        supplement_a_valider=supplement)
    assert r.ok
    return next(p for p in r.fichiers if p.suffix == ".json")


def valider(chemin, espace, corrections=None, **kw):
    _, s, logs = espace
    return enregistrer_corrections(chemin, corrections, registre=kw.pop("registre", REGISTRE),
                                   sortie=s, dossier_logs=logs, **kw)


def lire(chemin):
    return json.loads(Path(chemin).read_text(encoding="utf-8"))


PAGES = {"pages": [{"page": 1, "largeur": 100, "hauteur": 200, "lignes": [
    {"texte": "FACTURE", "confiance": 0.7, "cadre": [[0, 0], [9, 0], [9, 9], [0, 9]]}]}]}


# --- 1. Lister et charger ------------------------------------------------------------------
def test_lister_tous_les_documents(espace):
    deposer(espace, document())
    deposer(espace, document(confiance=0.60, validation=True), "scan_002.pdf",
            alertes=["totaux : facture sans TVA"])
    fiches = {f.statut: f for f in lister_documents(espace[1])}
    assert set(fiches) == {"range", "a_valider"}
    assert fiches["range"].dossier == "Factures" and fiches["range"].categorie == "factures"
    av = fiches["a_valider"]
    assert av.dossier == "A_Valider" and av.raison == "validation humaine necessaire"
    assert av.alertes == ["totaux : facture sans TVA"] and av.confiance == 0.6


def test_charger_un_document(espace):
    chemin = deposer(espace, document(confiance=0.60, validation=True), supplement=PAGES)
    doc = charger_document(chemin)
    assert doc.champs["numero"] == "FA-2026-0007" and doc.texte == TEXTE
    assert doc.original.suffix == ".pdf" and doc.original.exists()
    assert doc.pages_ocr[0]["lignes"][0]["confiance"] == 0.7
    assert doc.historique == []


# --- 2. Corrections -------------------------------------------------------------------------
def test_correction_renormalisee_et_historique(espace):
    chemin = deposer(espace, document())
    r = valider(chemin, espace, {"tva": "200,00 DH", "montant_ttc": "1 200,00"})
    info = lire(r.chemin_json)
    assert r.chemin_json == chemin and not r.renomme and not r.deplace
    assert r.nb_corrections == 0                     # memes valeurs : pas de correction
    assert info["necessite_validation_humaine"] is False and info["valide_le"]
    assert '"montant_ttc": 1200.00' in r.chemin_json.read_text(encoding="utf-8")
    assert info["historique_corrections"][-1]["action"] == "validation"


def test_correction_d_une_valeur(espace):
    chemin = deposer(espace, document())
    r = valider(chemin, espace, {"montant_ht": "1.000,00", "tva": "200"})
    info = lire(r.chemin_json)
    h = [x for x in info["historique_corrections"] if "champ" in x]
    assert r.nb_corrections == 0 and h == []        # 1000.00 et 200 identiques
    r = valider(r.chemin_json, espace, {"tva": "210,00"})
    info = lire(r.chemin_json)
    h = [x for x in info["historique_corrections"] if x.get("champ") == "tva"]
    assert h[0]["ancienne_valeur"] == 200.0 and h[0]["nouvelle_valeur"] == "210.00"
    assert r.nb_corrections == 1
    assert any("ecart de 10.00" in a for a in r.alertes)   # controle des totaux


def test_champ_du_nom_change_renommage(espace):
    chemin = deposer(espace, document())
    assert chemin.name == "facture_societe_exemple_sarl_2026-09-15_fa-2026-0007.json"
    r = valider(chemin, espace, {"date_facture": "16 septembre 2026"})
    assert r.renomme and not r.deplace
    assert r.chemin_json.name == "facture_societe_exemple_sarl_2026-09-16_fa-2026-0007.json"
    noms = {p.name for p in r.chemin_json.parent.iterdir()}
    assert noms == {f"facture_societe_exemple_sarl_2026-09-16_fa-2026-0007{x}"
                    for x in (".json", ".txt", ".pdf")}          # anciens fichiers retires
    assert lire(r.chemin_json)["champs"]["date_facture"] == "2026-09-16"


def test_renommage_avec_doublon(espace):
    deposer(espace, document())
    autre = deposer(espace, document(champs={**CHAMPS, "numero": "FA-2026-0008"}),
                    "scan_002.pdf")
    r = valider(autre, espace, {"numero": "FA-2026-0007"})     # meme nom que le premier
    assert r.chemin_json.name == "facture_societe_exemple_sarl_2026-09-15_fa-2026-0007_1.json"
    assert r.chemin_json.with_suffix(".pdf").exists()


def test_a_valider_vers_factures(espace):
    chemin = deposer(espace, document(confiance=0.60, validation=True), supplement=PAGES)
    assert chemin.parent.name == "A_Valider"
    r = valider(chemin, espace, {"fournisseur": "Société Exemple SARL"})
    assert r.deplace and r.chemin_json.parent.name == "Factures"
    info = lire(r.chemin_json)
    assert info["necessite_validation_humaine"] is False
    assert info["confiance_classification"] == 0.6                  # information gardee
    assert "raison" not in info and "categorie_proposee" not in info
    assert info["pages"][0]["lignes"][0]["texte"] == "FACTURE"       # lignes OCR gardees
    assert info["historique_corrections"][-1]["alertes_avant"] == ["alerte fictive"]
    assert not any(chemin.parent.iterdir())                           # A_Valider vide
    assert [f.statut for f in lister_documents(espace[1])] == ["valide"]


def test_changement_de_categorie(espace):
    chemin = deposer(espace, document())
    r = valider(chemin, espace, {"objet": "Bail commercial"}, categorie="contrats")
    assert r.deplace and r.chemin_json.parent.name == "Contrats"
    info = lire(r.chemin_json)
    assert info["type"] == "contrats" and "montant_ttc" not in info["champs"]
    h = info["historique_corrections"]
    assert {"champ": "type", "ancienne_valeur": "factures", "nouvelle_valeur": "contrats"} \
        .items() <= h[0].items()
    assert any(x.get("champ") == "numero" and x["nouvelle_valeur"] is None for x in h)


@pytest.mark.parametrize("corrections, message", [
    ({"date_facture": "31/02/2026"}, "date_facture : date invalide"),
    ({"montant_ttc": "1.234"}, "montant_ttc : montant invalide"),
    ({"ice": "123"}, "ice : 15 chiffres attendus"),
    ({"titulaire": "X"}, "champs non prevus"),
])
def test_valeur_invalide_rien_n_est_modifie(espace, corrections, message):
    chemin = deposer(espace, document())
    avant = chemin.read_bytes()
    with pytest.raises(ErreurValidation, match=message):
        valider(chemin, espace, corrections)
    assert chemin.read_bytes() == avant


def test_valeur_videe(espace):
    chemin = deposer(espace, document())
    r = valider(chemin, espace, {"tva": ""})
    assert lire(r.chemin_json)["champs"]["tva"] is None


def test_categorie_proposee_inconnue_doit_etre_creee(espace):
    chemin = deposer(espace, document("bulletin_paie", {"titre": "Bulletin"}, 0.60, True))
    with pytest.raises(ErreurValidation, match="la creer d'abord"):
        valider(chemin, espace)


# --- 3. Rejet ------------------------------------------------------------------------------------
def test_rejeter_vers_autres(espace):
    chemin = deposer(espace, document(confiance=0.60, validation=True))
    _, s, logs = espace
    r = rejeter_document(chemin, REGISTRE, s, logs)
    assert r.chemin_json.parent.name == "Autres" and lire(r.chemin_json)["type"] == "autres"
    assert lire(r.chemin_json)["historique_corrections"][-1]["action"] == "rejet"
    assert [f.statut for f in lister_documents(s)] == ["autres"]


# --- 4. Nouvelle categorie -------------------------------------------------------------------------
class FausseSession:
    def __init__(self, mots):
        self.mots, self.posts = mots, []

    def post(self, url, json=None, timeout=None):
        self.posts.append(json)

        class R:
            def raise_for_status(self_):
                pass

            def json(self_):
                return {"response": __import__("json").dumps({"mots_cles": self.mots})}
        return R()


@pytest.fixture
def registre_temp(tmp_path):
    chemin = tmp_path / "categories.json"
    shutil.copy2(CHEMIN_REGISTRE, chemin)
    return chemin


def test_proposer_mots_cles_filtre_les_chiffres_et_doublons():
    client = ClientOllama(session=FausseSession(
        ["Bulletin de paie", "Salaire net", "salaire net", "Matricule 1234", "Cotisations",
         "Net à payer", "Congés"]))
    mots = proposer_mots_cles("bulletin_paie", "texte fictif", client)
    assert mots == ["Bulletin de paie", "Salaire net", "Cotisations", "Net à payer", "Congés"]
    assert "bulletin paie" in client.session.posts[0]["prompt"]


def test_proposer_mots_cles_moteur_en_panne():
    class Panne:
        def post(self, *a, **k):
            raise ConnectionError("coupe")
    assert proposer_mots_cles("x", "texte", ClientOllama(session=Panne())) == []


@pytest.mark.parametrize("mot, regex", [
    ("Bulletin de paie", r"\bbulletin\s+de\s+paie\b"),
    ("Net à payer", r"\bnet\s+a\s+payer\b"),
    ("Congés payés", r"\bconges\s+payes\b"),
    ("d'imposition", r"\bd.imposition\b"),
])
def test_mot_cle_vers_regex(mot, regex):
    assert mot_cle_vers_regex(mot) == regex


def test_creer_categorie_puis_ranger(espace, registre_temp):
    _, s, logs = espace
    chemin = deposer(espace, document("bulletin_de_paie",
                                      {"titre": "Bulletin de paie", "personne": "Prénom Nom"},
                                      0.60, True, texte="Bulletin de paie\nSalaire net"))
    nom, r = creer_categorie("Bulletin de Paie", ["Bulletin de paie", "Salaire net",
                                                   "Cotisations"],
                             chemin, registre_temp, s, logs)
    assert nom == "bulletin_de_paie"
    nouveau = charger_registre(registre_temp)
    cat = next(c for c in nouveau["categories"] if c["nom"] == nom)
    assert cat["dossier"] == "Bulletin_de_paie" and cat["mots_cles"][0] == r"\bbulletin\s+de\s+paie\b"
    assert cat["champs"] == ["titre", "personne", "organisme", "date"]
    assert r.chemin_json.parent.name == "Bulletin_de_paie"
    assert r.chemin_json.name == "bulletin_de_paie_bulletin_de_paie_prenom_nom.json"
    assert json.loads(Path(CHEMIN_REGISTRE).read_text(encoding="utf-8")) != nouveau  # vrai registre intact


@pytest.mark.parametrize("nom, mots, message", [
    ("Bulletin", ["un", "deux mois", "trois"], "refusee par la verification"),   # nombre en lettres
    ("Factures", ["a", "b", "c"], "existe deja"),
    ("Bulletin", ["a", "b"], "de 3 a 5 mots-cles"),
    ("123", ["a", "b", "c"], "nom de categorie invalide"),
])
def test_creer_categorie_refusee_registre_intact(registre_temp, nom, mots, message):
    avant = registre_temp.read_bytes()
    with pytest.raises(ErreurValidation, match=message):
        creer_categorie(nom, mots, chemin_registre=registre_temp)
    assert registre_temp.read_bytes() == avant


def test_nom_de_categorie():
    assert nom_de_categorie("Avis d'imposition") == "avis_d_imposition"


# --- 4 bis. Pour l'interface : sous-dossier, apercu, depot, tri --------------------------------------
DIPLOME = ("DIPLÔME DE LICENCE\nUniversité Exemple\nTitulaire : Prénom Nom\n"
           "Fait à Rabat, le 12 juillet 2020")


def test_sous_dossier_choisi_par_l_humain(espace):
    doc = document("diplomes", {"titulaire": "Prénom Nom", "date_obtention": "2020-07-12"},
                   texte=DIPLOME)
    chemin = deposer(espace, doc)
    assert chemin.parent.name == "Licence"
    assert sous_dossier_actuel(chemin, REGISTRE) == "Licence"
    r = valider(chemin, espace, sous_dossier="Master")
    assert r.deplace and r.chemin_json.parent.name == "Master"
    assert r.chemin_json.name == "diplome_master_prenom_nom.json"      # nom suit le sous-dossier
    with pytest.raises(ErreurValidation, match="sous-dossier inconnu"):
        valider(r.chemin_json, espace, sous_dossier="BTS")


def test_apercu_page_lignes_peu_sures_surlignees(tmp_path):
    import pymupdf
    chemin = tmp_path / "scan.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), False)
    pix.clear_with(255)
    pix.save(chemin)
    page = {"page": 1, "largeur": 200, "hauteur": 100, "dpi": None, "lignes": [
        {"texte": "sure", "confiance": 0.99, "cadre": [[10, 10], [90, 10], [90, 30], [10, 30]]},
        {"texte": "douteuse", "confiance": 0.50, "cadre": [[10, 60], [90, 60], [90, 80], [10, 80]]}]}
    image, surlignees, nb_pages = apercu_page(chemin, page)
    assert (surlignees, nb_pages, image.size) == (1, 1, (200, 100))
    assert image.getpixel((50, 20)) == (255, 255, 255)                 # ligne sure : intacte
    assert image.getpixel((50, 70)) != (255, 255, 255)                 # ligne douteuse : coloree
    assert image.getpixel((10, 70))[0] > 200 and image.getpixel((10, 70))[1] < 50   # bord rouge


def test_apercu_sans_lignes_ocr(tmp_path):
    import pymupdf
    d = pymupdf.open()
    d.new_page()
    d.new_page()
    d.save(tmp_path / "x.pdf")
    image, surlignees, nb_pages = apercu_page(tmp_path / "x.pdf", None, 2)
    assert surlignees == 0 and nb_pages == 2 and image.size[0] > 500


def test_deposer_fichiers(tmp_path):
    e = tmp_path / "Folder_Entree"
    (e).mkdir()
    (e / "facture.pdf").write_bytes(b"deja la")
    deposes, refuses = deposer_fichiers(
        [("facture.pdf", b"nouveau"), ("..\\..\\piege.pdf", b"x"), ("notes.txt", b"x"),
         ("photo.JPG", b"x")], e)
    assert deposes == ["facture_1.pdf", "piege.pdf", "photo.JPG"] and refuses == ["notes.txt"]
    assert (e / "facture.pdf").read_bytes() == b"deja la"             # jamais d'ecrasement
    assert not (tmp_path / "piege.pdf").exists()                        # pas de sortie du dossier


def test_tri_en_arriere_plan_un_seul_a_la_fois(tmp_path):
    import sys
    import time
    d = tmp_path / "data"
    commande = [sys.executable, "-c",
                "import time; print('3 document(s) dans le dossier d entree', flush=True); "
                "print('[2/3] lecture', flush=True); time.sleep(4)"]
    lance, _ = lancer_tri(d, commande)
    assert lance
    assert lancer_tri(d, commande) == (False, "un tri est deja en cours")
    time.sleep(1.5)
    etat = etat_tri(d)
    assert etat["en_cours"] and etat["progression"] == "[2/3] lecture"
    for _ in range(40):
        if not etat_tri(d)["en_cours"]:
            break
        time.sleep(0.25)
    assert not etat_tri(d)["en_cours"] and not (d / "etat" / "tri.lock").exists()


def test_etat_tri_resume_du_dernier_lot(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "pipeline_20260926_100000_000000.jsonl").write_text(
        '{"evenement": "debut"}\n{"evenement": "fin", "total": 3, "ranges": 2, '
        '"a_valider": 1, "erreurs": 0, "deja_traites": 0, "duree_s": 12.5}\n', encoding="utf-8")
    etat = etat_tri(tmp_path)
    assert not etat["en_cours"] and etat["resume"]["ranges"] == 2
    assert etat["resume"]["a_valider"] == 1


def test_verrou_perime_retire(tmp_path):
    (tmp_path / "etat").mkdir()
    (tmp_path / "etat" / "tri.lock").write_text('{"pid": 999999}', encoding="utf-8")
    assert etat_tri(tmp_path)["en_cours"] is False
    assert not (tmp_path / "etat" / "tri.lock").exists()


def test_configuration_streamlit_confidentialite():
    import tomllib
    racine = Path(__file__).resolve().parent.parent
    conf = tomllib.loads((racine / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert conf["server"]["address"] == "127.0.0.1"
    assert conf["browser"]["gatherUsageStats"] is False
    bat = (racine / "lancer_interface.bat").read_text(encoding="utf-8")
    assert "--server.address 127.0.0.1" in bat and "--browser.gatherUsageStats false" in bat


# --- 5. Journal ------------------------------------------------------------------------------------
def test_journal_sans_valeur(espace):
    chemin = deposer(espace, document())
    _, s, logs = espace
    r = valider(chemin, espace, {"numero": "FA-2026-9999"})
    rejeter_document(r.chemin_json, REGISTRE, s, logs)
    journal = next(logs.glob("validation_*.jsonl")).read_text(encoding="utf-8")
    for secret in ("FA-2026", "fa-2026", "Exemple", "exemple", "1200", "9999"):
        assert secret not in journal
    actions = [json.loads(l)["action"] for l in journal.splitlines()]
    assert actions == ["validation", "rejet"]
