"""
test_filer.py - Tests du rangement (src/filer.py).

Tout se passe dans un dossier temporaire (tmp_path de pytest), avec des
fichiers INVENTES : les vrais Folder_Entree / Folder_Sortie ne sont jamais touches.
Lancement (venv active, depuis OCR_PROJECT) :  python -m pytest -v
"""

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

import src.filer as filer
from src.config import charger_registre
from src.filer import (construire_base, envoyer_a_valider, nettoyer_partie,
                       ranger_document, securiser_base, tronquer_base)
from src.schemas import DocumentSortie

REGISTRE = charger_registre()
CONTENU = b"%PDF-1.4 contenu invente pour les tests\n" * 50


# --- Outils ----------------------------------------------------------------
@pytest.fixture
def dossiers(tmp_path):
    """Folder_Entree (existe) et Folder_Sortie (N'EXISTE PAS encore)."""
    entree = tmp_path / "Folder_Entree"
    entree.mkdir()
    return entree, tmp_path / "Folder_Sortie"


def creer_original(entree: Path, nom="scan_001.pdf", contenu=CONTENU) -> Path:
    chemin = entree / nom
    chemin.write_bytes(contenu)
    return chemin


def doc(type_="factures", champs=None, texte="Facture inventée", confiance=0.95,
        validation=False, source="scan_001.pdf") -> DocumentSortie:
    return DocumentSortie.model_validate({
        "type": type_, "source": source, "date_traitement": "2026-09-25T10:00:00",
        "confiance_classification": confiance, "champs": champs or {},
        "necessite_validation_humaine": validation, "texte_brut": texte,
    }, context={"registre": REGISTRE})


def facture(**champs):
    base = {"fournisseur": "Société Exemple", "date_facture": "2026-09-15",
            "numero": "FA-2026-00042", "montant_ht": Decimal("200.4"),
            "tva": Decimal("40.1"), "montant_ttc": Decimal("240.5")}
    base.update(champs)
    return doc(champs=base)


def ranger(original, document, dossiers, **kw):
    entree, sortie = dossiers
    return ranger_document(original, document, registre=REGISTRE,
                           dossier_sortie=sortie, dossier_entree=entree, **kw)


# --- 1. Noms de fichiers ---------------------------------------------------
@pytest.mark.parametrize("texte, attendu", [
    ("Société Exemple", "societe_exemple"),
    ("  Prénom   NÔM  ", "prenom_nom"),
    ('A<b>c:d"e/f\\g|h?i*j', "a_b_c_d_e_f_g_h_i_j"),   # caracteres interdits Windows
    ("FA-2026-00042", "fa-2026-00042"),
    ("L'Été à Fès", "l_ete_a_fes"),
    ("___", ""),
])
def test_nettoyer_partie(texte, attendu):
    assert nettoyer_partie(texte) == attendu


@pytest.mark.parametrize("base, attendu", [
    ("con", "con_doc"), ("NUL".lower(), "nul_doc"), ("com1", "com1_doc"),
    ("", "document"), ("facture_x", "facture_x"),
])
def test_securiser_base_noms_reserves_windows(base, attendu):
    assert securiser_base(base) == attendu


def test_nom_facture():
    assert construire_base(facture(), REGISTRE, None) == \
        "facture_societe_exemple_2026-09-15_fa-2026-00042"


def test_nom_partie_manquante_omise():
    d = facture(numero=None, date_facture="")
    assert construire_base(d, REGISTRE, None) == "facture_societe_exemple"


def test_nom_diplome_avec_sous_dossier():
    d = doc("diplomes", {"titulaire": "Prénom Nôm"}, texte="Diplôme DEUG")
    assert construire_base(d, REGISTRE, "DEUG") == "diplome_deug_prenom_nom"


def test_nom_categorie_decouverte():
    d = doc("bulletin_paie", {"titre": "Bulletin de paie", "personne": "Prénom Nom"},
            confiance=0.95)
    assert construire_base(d, REGISTRE, None) == "bulletin_paie_bulletin_de_paie_prenom_nom"


def test_troncature_chemin_windows(tmp_path):
    dossier = tmp_path / "Factures"
    base = tronquer_base("facture_" + "x" * 400, dossier, [".txt", ".json", ".pdf"])
    chemin_max = dossier / f"{base}_999.json"
    assert len(str(chemin_max.absolute())) <= 259
    assert not base.endswith("_")


def test_troncature_impossible_si_dossier_trop_long(tmp_path):
    with pytest.raises(filer.ErreurRangement, match="trop long"):
        tronquer_base("facture_x", tmp_path / ("d" * 300), [".json"])


# --- 2. Rangement normal ---------------------------------------------------
def test_rangement_facture_complet(dossiers):
    entree, sortie = dossiers
    original = creer_original(entree)
    r = ranger(original, facture(), dossiers)

    assert r.ok and r.original_deplace and not r.a_valider and r.alertes == []
    dossier = sortie / "Factures"
    base = "facture_societe_exemple_2026-09-15_fa-2026-00042"
    assert {p.name for p in dossier.iterdir()} == {f"{base}.txt", f"{base}.json", f"{base}.pdf"}
    # Copie identique, original deplace dans Traites/
    assert (dossier / f"{base}.pdf").read_bytes() == CONTENU
    assert not original.exists()
    assert (entree / "Traites" / "scan_001.pdf").read_bytes() == CONTENU
    # .txt et .json en UTF-8, montants a 2 decimales
    assert (dossier / f"{base}.txt").read_text(encoding="utf-8") == "Facture inventée"
    texte_json = (dossier / f"{base}.json").read_text(encoding="utf-8")
    assert '"montant_ttc": 240.50' in texte_json
    assert json.loads(texte_json)["champs"]["fournisseur"] == "Société Exemple"


def test_dossiers_crees_a_la_demande_seulement(dossiers):
    entree, sortie = dossiers
    assert not sortie.exists()                            # rien de cree a l'avance
    ranger(creer_original(entree), facture(), dossiers)
    assert [p.name for p in sortie.iterdir()] == ["Factures"]   # pas de Diplomes/, etc.


def test_sous_dossier_diplome(dossiers):
    entree, sortie = dossiers
    d = doc("diplomes", {"titulaire": "Prénom Nôm"},
            texte="Diplôme d'Études Universitaires Générales (DEUG)")
    r = ranger(creer_original(entree, "deug.pdf"), d, dossiers)
    assert r.ok
    assert (sortie / "Diplomes" / "DEUG" / "diplome_deug_prenom_nom.pdf").exists()


def test_sous_dossier_autres(dossiers):
    entree, sortie = dossiers
    d = doc("attestations", {"beneficiaire": "Prénom Nom"}, texte="Attestation de salaire")
    r = ranger(creer_original(entree, "a.pdf"), d, dossiers)
    assert r.ok and (sortie / "Attestations" / "Autres" /
                     "attestation_autres_prenom_nom.json").exists()


def test_type_autres(dossiers):
    entree, sortie = dossiers
    d = doc("autres", {"titre": "Reçu de paiement"}, texte="Reçu inventé")
    r = ranger(creer_original(entree, "recu.jpg"), d, dossiers)
    assert r.ok and (sortie / "Autres" / "autres_recu_de_paiement.jpg").exists()


# --- 3. Doublons -----------------------------------------------------------
def test_doublons_1_2(dossiers):
    entree, sortie = dossiers
    for i in range(3):
        r = ranger(creer_original(entree, f"scan_{i}.pdf"), facture(), dossiers)
        assert r.ok
    noms = {p.name for p in (sortie / "Factures").iterdir()}
    base = "facture_societe_exemple_2026-09-15_fa-2026-00042"
    for suffixe in ("", "_1", "_2"):
        assert {f"{base}{suffixe}{e}" for e in (".txt", ".json", ".pdf")} <= noms
    assert len(noms) == 9


def test_doublon_sans_tenir_compte_des_majuscules(dossiers):
    entree, sortie = dossiers
    dossier = sortie / "Factures"
    dossier.mkdir(parents=True)
    (dossier / "FACTURE_SOCIETE_EXEMPLE_2026-09-15_FA-2026-00042.PDF").write_bytes(b"x")
    r = ranger(creer_original(entree), facture(), dossiers)
    assert r.ok
    assert (dossier / "facture_societe_exemple_2026-09-15_fa-2026-00042_1.json").exists()


def test_doublon_dans_traites(dossiers):
    entree, _ = dossiers
    ranger(creer_original(entree, "scan.pdf"), facture(), dossiers)
    ranger(creer_original(entree, "scan.pdf"), facture(), dossiers)   # meme nom d'origine
    assert {p.name for p in (entree / "Traites").iterdir()} == {"scan.pdf", "scan_1.pdf"}


def test_nom_tres_long_reste_sous_260(dossiers):
    entree, _ = dossiers
    r = ranger(creer_original(entree), facture(fournisseur="Société " * 80), dossiers)
    assert r.ok
    assert all(len(str(p.absolute())) <= 259 for p in r.fichiers)


def test_caracteres_interdits_dans_les_champs(dossiers):
    entree, sortie = dossiers
    r = ranger(creer_original(entree), facture(fournisseur='Société <A>:"B"/C|D?*',
                                               numero=None), dossiers)
    assert r.ok
    assert (sortie / "Factures" / "facture_societe_a_b_c_d_2026-09-15.pdf").exists()


# --- 4. A_Valider ----------------------------------------------------------
def test_a_valider_si_validation_humaine(dossiers):
    entree, sortie = dossiers
    d = doc(confiance=0.60, validation=True)
    r = ranger(creer_original(entree, "Scan Été 15.09.PDF"), d, dossiers,
               alertes=["totaux : facture sans TVA"])
    assert r.ok and r.a_valider and r.original_deplace
    dossier = sortie / "A_Valider"
    assert {p.name for p in dossier.iterdir()} == {"scan_ete_15_09.pdf", "scan_ete_15_09.json"}
    raison = json.loads((dossier / "scan_ete_15_09.json").read_text(encoding="utf-8"))
    assert raison["raison"] == "validation humaine necessaire"
    assert raison["alertes"] == ["totaux : facture sans TVA"]
    assert raison["type_propose"] == "factures" and raison["confiance_classification"] == 0.6
    assert raison["source"] == "Scan Été 15.09.PDF"


def test_a_valider_si_categorie_absente_du_registre(dossiers):
    entree, sortie = dossiers
    d = doc("bulletin_paie", {"titre": "Bulletin"}, confiance=0.95)
    r = ranger(creer_original(entree), d, dossiers)
    assert r.a_valider
    raison = json.loads((sortie / "A_Valider" / "scan_001.json").read_text(encoding="utf-8"))
    assert raison["raison"] == "categorie absente du registre"


def test_a_valider_fichier_illisible_sans_document(dossiers):
    entree, sortie = dossiers
    original = creer_original(entree, "corrompu.pdf", b"pas un pdf")
    r = envoyer_a_valider(original, "fichier illisible (corrompu)",
                          dossier_sortie=sortie, dossier_entree=entree)
    assert r.ok and r.a_valider
    raison = json.loads((sortie / "A_Valider" / "corrompu.json").read_text(encoding="utf-8"))
    assert raison["type_propose"] is None


def test_a_valider_original_json_ne_se_melange_pas(dossiers):
    """Un original .json ne doit pas ecraser le .json de la raison."""
    entree, sortie = dossiers
    original = creer_original(entree, "export.json", b'{"a": 1}')
    r = envoyer_a_valider(original, "format inconnu", dossier_sortie=sortie,
                          dossier_entree=entree)
    assert r.ok
    noms = {p.name for p in (sortie / "A_Valider").iterdir()}
    assert noms == {"export.json", "export_original.json"}


def test_a_valider_doublons(dossiers):
    entree, sortie = dossiers
    for _ in range(2):
        envoyer_a_valider(creer_original(entree, "scan.pdf"), "illisible",
                          dossier_sortie=sortie, dossier_entree=entree)
    assert {p.name for p in (sortie / "A_Valider").iterdir()} == \
        {"scan.pdf", "scan.json", "scan_1.pdf", "scan_1.json"}


# --- 5. L'original n'est JAMAIS perdu ---------------------------------------
def verifier_original_intact(original: Path, sortie: Path):
    assert original.exists() and original.read_bytes() == CONTENU
    # Aucun fichier produit ne traine a moitie
    assert not sortie.exists() or not any(p.is_file() for p in sortie.rglob("*"))


def test_copie_qui_echoue(dossiers, monkeypatch):
    entree, sortie = dossiers
    original = creer_original(entree, "Prenom_Nom_facture.pdf")

    def copie_en_panne(src, dst, *a, **k):
        raise OSError(f"disque plein en ecrivant {dst}")      # message avec chemin
    monkeypatch.setattr(filer.shutil, "copy2", copie_en_panne)

    r = ranger(original, facture(), dossiers)
    assert not r.ok and not r.original_deplace
    verifier_original_intact(original, sortie)
    assert r.alertes == ["rangement annule (OSError) : l'original reste dans Folder_Entree"]
    assert not any("Prenom" in a or "Nom" in a for a in r.alertes)   # pas de chemin


def test_copie_corrompue_detectee(dossiers, monkeypatch):
    entree, sortie = dossiers
    original = creer_original(entree)

    def copie_tronquee(src, dst, *a, **k):
        Path(dst).write_bytes(Path(src).read_bytes()[:10])     # copie incomplete
    monkeypatch.setattr(filer.shutil, "copy2", copie_tronquee)

    r = ranger(original, facture(), dossiers)
    assert not r.ok
    verifier_original_intact(original, sortie)
    assert "copie de l'original non conforme" in r.alertes[0]


def test_copie_meme_taille_mais_contenu_different(dossiers, monkeypatch):
    entree, sortie = dossiers
    original = creer_original(entree)

    def copie_alteree(src, dst, *a, **k):
        donnees = bytearray(Path(src).read_bytes())
        donnees[-1] ^= 0xFF                                    # 1 octet modifie
        Path(dst).write_bytes(bytes(donnees))
    monkeypatch.setattr(filer.shutil, "copy2", copie_alteree)

    r = ranger(original, facture(), dossiers)
    assert not r.ok
    verifier_original_intact(original, sortie)


def test_ecriture_json_qui_echoue(dossiers, monkeypatch):
    entree, sortie = dossiers
    original = creer_original(entree)
    ecrire = Path.write_text

    def ecriture_en_panne(self, *a, **k):
        if self.suffix == ".json":
            raise PermissionError("acces refuse")
        return ecrire(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", ecriture_en_panne)

    r = ranger(original, facture(), dossiers)
    assert not r.ok
    verifier_original_intact(original, sortie)                 # le .txt deja ecrit est retire


def test_deplacement_qui_echoue(dossiers, monkeypatch):
    """La copie est verifiee mais le deplacement echoue : les fichiers ranges sont
    gardes, l'original reste dans Folder_Entree, avec une alerte."""
    entree, sortie = dossiers
    original = creer_original(entree)
    monkeypatch.setattr(filer.shutil, "move",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("verrouille")))
    r = ranger(original, facture(), dossiers)
    assert r.ok and not r.original_deplace
    assert original.read_bytes() == CONTENU
    assert len(list((sortie / "Factures").iterdir())) == 3
    assert "original non deplace vers Traites (OSError)" in r.alertes[0]


def test_original_introuvable(dossiers):
    entree, sortie = dossiers
    r = ranger(entree / "absent.pdf", facture(), dossiers)
    assert not r.ok and "original introuvable" in r.alertes[0]
    assert not sortie.exists()
