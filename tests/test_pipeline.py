"""
test_pipeline.py - Tests du pipeline complet (src/pipeline.py, run_pipeline.py).

Tests rapides : Ollama et moteur SIMULES, factures FICTIVES natives (pas d'OCR),
dossiers temporaires (tmp_path). Le vrai Folder_Entree n'est jamais touche.

Test de bout en bout REEL (marque ollama_reel, lance a part, navigateur ferme) :
    python -m pytest -m ollama_reel -s -k bout_en_bout
"""

import json
import os
import re
import shutil
import time
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path

import pytest

import src.pipeline as pipeline
from src.classifier import MoteurClassification, ReponseMoteur
from src.config import charger_profil, charger_registre
from src.extractor import cle_comparaison
from src.llm import ClientOllama
from src.ocr_worker import LigneOCR, PageOCR, ResultatLot
from src.pipeline import lignes_ocr_pour_json, nettoyer_ocr_anciens, traiter_lot

SYNTH = Path(__file__).parent / "docs_synthetiques"
REGISTRE = charger_registre()
NATIFS = ["f01_fr_standard_natif.pdf", "f02_fr_multi_tva_natif.pdf",
          "f03_fr_sans_tva_natif.pdf", "f04_fr_ecart_totaux_natif.pdf"]
pytestmark = pytest.mark.skipif(not SYNTH.is_dir(), reason="jeu synthetique absent")


# --- Faux Ollama et moteur simule ---------------------------------------------------
class R:
    def __init__(self, donnees):
        self._d = donnees

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class FauxOllama:
    """Session simulee : /api/tags (modele present), /api/ps (modeles charges),
    /api/generate (JSON avec toutes les cles demandees a null)."""

    def __init__(self, charges=()):
        self.charges, self.posts = list(charges), []

    def get(self, url, timeout=None):
        if url.endswith("/api/ps"):
            return R({"models": [{"name": m} for m in self.charges]})
        return R({"models": [{"name": "phi4-mini:latest"}]})

    def post(self, url, json=None, timeout=None):
        self.posts.append(json)
        if "prompt" not in json:                       # chargement / dechargement
            return R({})
        cles = json["format"]["properties"]
        return R({"response": __import__("json").dumps({k: None for k in cles})})


class MoteurFixe(MoteurClassification):
    nom = "fixe"

    def __init__(self, categorie="factures", disponible=True):
        self.categorie, self.disponible, self.appels = categorie, disponible, 0

    def verifier(self):
        return (True, None) if self.disponible else (False, "Ollama ne repond pas")

    def lot(self):
        return nullcontext(self)

    def classer(self, texte, registre):
        self.appels += 1
        return ReponseMoteur(ok=True, categorie=self.categorie)


@pytest.fixture
def espace(tmp_path):
    """Folder_Entree, Folder_Sortie et data/ temporaires."""
    e, s, d = tmp_path / "Folder_Entree", tmp_path / "Folder_Sortie", tmp_path / "data"
    e.mkdir()
    return e, s, d


def deposer(entree, noms, renommer=None):
    for nom in noms:
        shutil.copy2(SYNTH / nom, entree / (renommer or nom))


def lancer(espace, moteur=None, session=None, messages=None, **kw):
    e, s, d = espace
    client = ClientOllama(session=session or FauxOllama())
    return traiter_lot(charger_profil(), e, s, d, client=client,
                       moteur=moteur or MoteurFixe(), registre=REGISTRE,
                       afficher=(messages.append if messages is not None else lambda m: None),
                       **kw)


# --- 1. Lot normal ------------------------------------------------------------------------
def test_lot_de_factures_natives(espace):
    e, s, d = espace
    deposer(e, NATIFS)
    bilan = lancer(espace)
    assert (bilan.total, bilan.ranges, bilan.a_valider, bilan.erreurs) == (4, 3, 1, 0)
    # Rangement : 3 factures dans Factures/, f04 (ecart de totaux) dans A_Valider/
    assert len(list((s / "Factures").glob("*.json"))) == 3
    assert len(list((s / "A_Valider").glob("*.json"))) == 1
    # Originaux deplaces vers Traites/, entree vide
    assert sorted(p.name for p in (e / "Traites").iterdir()) == sorted(NATIFS)
    assert [p for p in e.iterdir() if p.is_file()] == []
    # Valeurs extraites dans le JSON de sortie (non masque)
    # (le faux LLM ne donne pas de fournisseur : nom = facture_<date>_<numero>)
    sortie = json.loads((s / "Factures" / "facture_2026-09-15_fa-2026-0142.json")
                        .read_text(encoding="utf-8"))
    assert sortie["champs"]["numero"] == "FA-2026-0142"
    assert sortie["champs"]["montant_ttc"] == 2385.6
    assert sortie["confiance_classification"] == 0.95


def test_journal_et_etat_sans_donnee_de_document(espace):
    e, s, d = espace
    deposer(e, NATIFS)
    bilan = lancer(espace)
    journal = bilan.journal.read_text(encoding="utf-8")
    for secret in ("Atlas", "atlas", "FA-2026-0142", "000999888000011", "2385"):
        assert secret not in journal
    lignes = [json.loads(l) for l in journal.splitlines()]
    assert lignes[0]["evenement"] == "debut" and lignes[-1]["evenement"] == "fin"
    assert sum(1 for l in lignes if l["evenement"] == "document") == 4
    etat = json.loads((d / "etat" / "traites.json").read_text(encoding="utf-8"))
    assert len(etat) == 4


def test_progression_compteurs_seulement(espace):
    e, _, _ = espace
    deposer(e, NATIFS)
    messages = []
    lancer(espace, messages=messages)
    assert any(m.startswith("[4/4]") for m in messages)
    for m in messages:
        assert re.fullmatch(r"\d+ document\(s\) dans le dossier d'entree \(profil \w+\)"
                            r"|\[\d+/\d+\] [a-z ,]+|OCR : \d+ page\(s\)|  OCR \[\d+/\d+\]", m), m


# --- 2. Reprise et doublons -----------------------------------------------------------------
def test_reprise_sans_retraiter(espace):
    e, s, d = espace
    deposer(e, NATIFS)
    lancer(espace)
    deposer(e, ["f01_fr_standard_natif.pdf"])            # meme fichier redepose
    moteur = MoteurFixe()
    bilan = lancer(espace, moteur=moteur)
    assert bilan.deja_traites == 1 and bilan.ranges == 0 and moteur.appels == 0
    assert len(list((s / "Factures").glob("*.json"))) == 3        # pas de doublon _1
    assert (e / "Traites" / "f01_fr_standard_natif_1.pdf").exists()


def test_meme_fichier_deux_fois_dans_un_lot(espace):
    e, s, _ = espace
    deposer(e, ["f01_fr_standard_natif.pdf"])
    deposer(e, ["f01_fr_standard_natif.pdf"], renommer="copie.pdf")
    bilan = lancer(espace)
    assert bilan.ranges == 1 and bilan.deja_traites == 1


# --- 3. Documents a probleme -----------------------------------------------------------------
def test_licence_de_logiciel_rend_la_facture_faible(espace):
    """FAIBLESSE CONNUE du registre : f10 contient « Licence antivirus » ; le mot-cle
    « licence » (diplomes) donne 1 point diplomes -> verdict « faible » -> 0.60 ->
    A_Valider, meme si le moteur est d'accord."""
    e, s, _ = espace
    deposer(e, ["f10_fr_montants_europeens_natif.pdf"])
    bilan = lancer(espace)
    assert bilan.a_valider == 1
    info = json.loads(next((s / "A_Valider").glob("*.json")).read_text(encoding="utf-8"))
    assert "mots-cles : faible (factures)" in info["alertes"]


def test_fichier_illisible_en_a_valider(espace):
    e, s, _ = espace
    (e / "abime.pdf").write_bytes(b"pas un pdf")
    moteur = MoteurFixe()
    bilan = lancer(espace, moteur=moteur)
    assert bilan.a_valider == 1 and moteur.appels == 0
    info = json.loads((s / "A_Valider" / "abime.json").read_text(encoding="utf-8"))
    assert info["raison"] == "fichier illisible"


def test_erreur_sur_un_document_ne_bloque_pas_le_lot(espace, monkeypatch):
    e, s, _ = espace
    deposer(e, ["f01_fr_standard_natif.pdf", "f02_fr_multi_tva_natif.pdf"])
    vrai = pipeline.extraire_document

    def extraire_en_panne(lignes, categorie, *a, **k):
        if any("Atlas" in l.texte for l in lignes):
            raise RuntimeError("panne simulee")
        return vrai(lignes, categorie, *a, **k)
    monkeypatch.setattr(pipeline, "extraire_document", extraire_en_panne)
    bilan = lancer(espace)
    assert bilan.a_valider == 1 and bilan.ranges == 1 and bilan.erreurs == 0
    info = json.loads(next((s / "A_Valider").glob("*.json")).read_text(encoding="utf-8"))
    assert info["raison"] == "erreur de traitement (RuntimeError)"


def test_moteur_indisponible_rien_n_est_touche(espace):
    e, s, _ = espace
    deposer(e, NATIFS)
    bilan = lancer(espace, moteur=MoteurFixe(disponible=False))
    assert bilan.ranges == bilan.a_valider == 0
    assert len([p for p in e.iterdir() if p.is_file()]) == 4        # restes en entree
    assert any("moteur indisponible" in a for a in bilan.alertes)


def test_modele_deja_charge_lot_arrete_avant_l_ocr(espace):
    """Profil modeste : si Ollama garde un modele charge malgre le dechargement,
    l'OCR ne demarre pas."""
    e, s, _ = espace
    deposer(e, ["f01_fr_standard_natif.pdf"])
    bilan = lancer(espace, session=FauxOllama(charges=["phi4-mini:latest"]))
    assert bilan.ranges == 0 and (e / "f01_fr_standard_natif.pdf").exists()
    assert any("Ollama a un modele charge" in a for a in bilan.alertes)


# --- 4. OCR : lignes copiees dans le JSON de A_Valider, dossier OCR supprime -------------------
def test_a_valider_contient_les_lignes_ocr(espace, monkeypatch):
    e, s, d = espace
    deposer(e, ["f07_fr_scan_propre.jpg"])
    dossier_lot = d / "ocr" / "lot_simule"

    def ocr_simule(taches, dossier_travail=None, **kw):
        dossier_lot.mkdir(parents=True)
        lot = ResultatLot(dossier=dossier_lot)
        for t in taches:
            lot.pages[(t["fichier"], t["page"])] = PageOCR(
                fichier=t["fichier"], page=t["page"], statut="ok", largeur=100, hauteur=200,
                lignes=[LigneOCR("FACTURE", 0.70, [[0, 0], [9, 0], [9, 9], [0, 9]]),
                        LigneOCR("Total TTC : 658,80 DH", 0.60, [[0, 20]] * 4)])
        return lot
    monkeypatch.setattr(pipeline, "lancer_ocr", ocr_simule)
    bilan = lancer(espace)
    assert bilan.a_valider == 1                       # confiance OCR < 0,80
    info = json.loads(next((s / "A_Valider").glob("*.json")).read_text(encoding="utf-8"))
    page = info["pages"][0]
    assert page["largeur"] == 100 and page["lignes"][0] == {
        "texte": "FACTURE", "confiance": 0.7, "cadre": [[0, 0], [9, 0], [9, 9], [0, 9]]}
    assert not dossier_lot.exists()                   # data/ocr du lot supprime


def test_lignes_ocr_pour_json():
    pages = {1: PageOCR(fichier="x", page=1, statut="ok",
                        lignes=[LigneOCR("a", 0.9, [[1, 2]] * 4)])}
    assert lignes_ocr_pour_json(pages)["pages"][0]["lignes"][0]["confiance"] == 0.9


def test_nettoyage_des_lots_ocr_de_plus_de_7_jours(tmp_path):
    vieux, recent = tmp_path / "ocr" / "vieux", tmp_path / "ocr" / "recent"
    vieux.mkdir(parents=True)
    recent.mkdir()
    il_y_a_8_jours = time.time() - 8 * 86400
    os.utime(vieux, (il_y_a_8_jours, il_y_a_8_jours))
    assert nettoyer_ocr_anciens(tmp_path / "ocr") == 1
    assert not vieux.exists() and recent.exists()


def test_fichiers_systeme_ignores(espace):
    e, _, _ = espace
    (e / "desktop.ini").write_text("x")
    (e / "~$brouillon.docx").write_text("x")
    assert pipeline.lister_documents(e) == []


# --- 5. Resume de run_pipeline : noms masques -------------------------------------------------
def test_resume_sans_nom_de_personne(espace, capsys):
    import run_pipeline
    e, s, _ = espace
    deposer(e, ["f04_fr_ecart_totaux_natif.pdf"])
    shutil.copy2(SYNTH / "f01_fr_standard_natif.pdf", e / "Facture Prenom Nom.pdf")
    bilan = lancer(espace)
    run_pipeline.afficher_resume(bilan)
    sortie = capsys.readouterr().out
    assert "A_Valider" in sortie and "Prenom" not in sortie and "Riad" not in sortie


# --- 6. Test de bout en bout REEL (OCR + Phi-4-mini), a lancer a part --------------------------
# Decision attendue d'apres verite_terrain.json (champ « attendu »)
ATTENDU_RANGE = {"f01_fr_standard_natif.pdf", "f02_fr_multi_tva_natif.pdf",
                 "f03_fr_sans_tva_natif.pdf", "f07_fr_scan_propre.jpg",
                 "f10_fr_montants_europeens_natif.pdf"}
CHAMPS = ["date_facture", "numero", "montant_ht", "tva", "montant_ttc", "ice", "fournisseur"]


@pytest.mark.ollama_reel
def test_bout_en_bout_reel(tmp_path):
    """Les 10 factures fictives traversent tout le pipeline (vrai OCR, vrai Phi-4-mini).
    EXIGE la securite : tout document RANGE a la bonne categorie, le bon dossier et des
    champs exacts ; rien ne reste en entree ; aucune erreur. Les ecarts de PRUDENCE
    (A_Valider alors que la verite terrain dit « range ») sont affiches, pas bloquants."""
    e, s, d = tmp_path / "Folder_Entree", tmp_path / "Folder_Sortie", tmp_path / "data"
    e.mkdir()
    verite = json.loads((SYNTH / "verite_terrain.json").read_text(encoding="utf-8"))
    for nom in verite:
        shutil.copy2(SYNTH / nom, e / nom)

    bilan = traiter_lot(charger_profil(), e, s, d, afficher=print)
    print(f"\nrangés {bilan.ranges}, A_Valider {bilan.a_valider}, erreurs {bilan.erreurs}, "
          f"temps {bilan.duree_s:.0f} s (OCR {bilan.duree_ocr_s:.0f} s, "
          f"LLM {bilan.duree_llm_s:.0f} s), pic OCR {bilan.pic_ram_ocr_mo:.0f} Mo")

    # Retrouver la sortie de chaque facture par sa source
    sorties = {}
    for chemin in s.rglob("*.json"):
        info = json.loads(chemin.read_text(encoding="utf-8"))
        sorties[info["source"]] = (chemin.parent.relative_to(s).as_posix(), info)

    prudence, dangereux = [], []
    for nom, attendu in verite.items():
        dossier, info = sorties[nom]
        decision = "a_valider" if dossier == "A_Valider" else "range"
        prevu = "range" if nom in ATTENDU_RANGE else "a_valider"
        champs_ok = []
        for c in CHAMPS:
            trouve, voulu = info["champs"].get(c), attendu.get(c)
            if c == "fournisseur":
                ok = trouve is not None and cle_comparaison(str(trouve)) == cle_comparaison(voulu or "")
            elif isinstance(trouve, (int, float)):
                ok = voulu is not None and Decimal(str(trouve)) == Decimal(voulu)
            else:
                ok = trouve == voulu
            champs_ok.append(ok)
        print(f"  {nom:<38} {info['type']:<9} {dossier:<10} {decision:<9} "
              f"(prevu {prevu:<9}) champs {sum(champs_ok)}/{len(CHAMPS)}")
        if decision == "a_valider" and prevu == "range":
            prudence.append(nom)
        if decision == "range" and prevu == "a_valider":
            dangereux.append(f"{nom} : range alors qu'il devait partir en A_Valider")
        if decision == "range" and (info["type"] != attendu["categorie_attendue"]
                                    or dossier != "Factures" or not all(champs_ok)):
            dangereux.append(f"{nom} : range avec categorie, dossier ou champ faux")
    print(f"  Prudence (A_Valider au lieu de range) : {prudence or 'aucune'}")
    print(f"  DANGER (range a tort)                 : {dangereux or 'aucun'}")
    assert not dangereux, dangereux
    assert bilan.erreurs == 0
    assert [p for p in e.iterdir() if p.is_file()] == []
    assert not any((d / "ocr").iterdir())             # dossier OCR du lot supprime
