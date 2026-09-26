"""
test_config.py - Tests du registre des categories (src/config.py).

Lancement (venv active, depuis OCR_PROJECT) :  python -m pytest -v
Aucun document reel n'est utilise : seulement le registre et des phrases inventees.
"""

import copy
import json
import re
from pathlib import Path

import pytest

from src.config import (ErreurRegistre, champs_attendus, charger_registre,
                        choisir_sous_dossier, modele_nom_fichier, normaliser,
                        trouver_categorie, verifier_registre, zone_titre)


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


def noms_sous_dossiers(registre, nom_categorie):
    return [sd["nom"] for sd in trouver_categorie(registre, nom_categorie)["sous_dossiers"]]


def test_sous_dossiers_prevus(registre):
    assert noms_sous_dossiers(registre, "diplomes") == \
        ["DEUG", "Licence", "Master", "Doctorat", "Autres"]
    assert noms_sous_dossiers(registre, "attestations") == \
        ["Travail", "Scolarite", "Autres"]
    assert noms_sous_dossiers(registre, "factures") == []


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
    assert indices == 2        # releve de compte, solde ("agence" seul ne compte plus)


def test_rib_iban_agence_seuls_ne_comptent_plus(registre):
    """Une facture porte souvent RIB / IBAN / agence : ce n'est pas un document bancaire."""
    banque = trouver_categorie(registre, "banque")
    texte = normaliser("Règlement par virement : RIB, IBAN, agence Exemple")
    assert not any(re.search(m, texte) for m in banque["mots_cles"])


# --- Zone titre et sous-dossier (titre d'abord) ------------------------------
def test_zone_titre_premieres_lignes_non_vides():
    texte = "\n\n".join(f"ligne {i}" for i in range(1, 30))
    zone = zone_titre(texte)
    assert zone.splitlines() == [f"ligne {i}" for i in range(1, 16)]


def test_zone_titre_plafond_caracteres():
    assert len(zone_titre("x" * 5000)) == 1000


def test_sous_dossier_titre_prioritaire(registre):
    diplomes = trouver_categorie(registre, "diplomes")
    corps = "\n".join(["texte"] * 20)
    texte = f"DIPLÔME DE LICENCE\n{corps}\nOuvre l'accès au Master."
    assert choisir_sous_dossier(diplomes, texte) == "Licence"


def test_sous_dossier_corps_si_titre_muet(registre):
    diplomes = trouver_categorie(registre, "diplomes")
    corps = "\n".join(["texte"] * 20)
    assert choisir_sous_dossier(diplomes, f"UNIVERSITÉ EXEMPLE\n{corps}\nDEUG") == "DEUG"


@pytest.mark.parametrize("titre, attendu", [
    ("Diplôme d'Études Universitaires Générales", "DEUG"),          # sans le sigle
    ("DIPLOME D’ETUDES UNIVERSITAIRES GENERALES", "DEUG"),          # apostrophe typographique
    ("Licence Fondamentale en Économie", "Licence"),
    ("Licence Professionnelle Comptabilité", "Licence"),
    ("Licence d'Études Fondamentales", "Licence"),
    ("Master Spécialisé Audit", "Master"),
    ("Master de Recherche en Finance", "Master"),
])
def test_sous_dossier_noms_complets(registre, titre, attendu):
    diplomes = trouver_categorie(registre, "diplomes")
    assert choisir_sous_dossier(diplomes, titre) == attendu


def test_sous_dossier_plusieurs_dans_le_titre(registre):
    diplomes = trouver_categorie(registre, "diplomes")
    assert choisir_sous_dossier(diplomes, "Licence et Master\nDEUG") == "Autres"


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


def test_sous_dossier_autres_obligatoire(registre):
    diplomes = trouver_categorie(registre, "diplomes")
    diplomes["sous_dossiers"] = [sd for sd in diplomes["sous_dossiers"]
                                 if sd["nom"] != "Autres"]
    assert erreurs_contiennent(registre, "'Autres' obligatoire")


def test_sous_dossier_sans_mot_cle_refuse(registre):
    trouver_categorie(registre, "diplomes")["sous_dossiers"][0]["mots_cles"] = []
    assert erreurs_contiennent(registre, "sous-dossier 'DEUG' : aucun mot-cle")


def test_sous_dossier_regex_invalide_refusee(registre):
    trouver_categorie(registre, "diplomes")["sous_dossiers"][0]["mots_cles"] = [r"\b(deug"]
    assert erreurs_contiennent(registre, "regex invalide")


def test_sous_dossier_chiffre_en_clair_refuse(registre):
    trouver_categorie(registre, "diplomes")["sous_dossiers"][1]["mots_cles"] = [r"\blicence 3\b"]
    assert erreurs_contiennent(registre, "contient un chiffre")


def test_sous_dossier_nombre_en_lettres_refuse(registre):
    trouver_categorie(registre, "diplomes")["sous_dossiers"][2]["mots_cles"] = [r"\bmaster deux\b"]
    assert erreurs_contiennent(registre, "toutes lettres")


def test_sous_dossier_en_double_refuse(registre):
    sous = trouver_categorie(registre, "diplomes")["sous_dossiers"]
    sous.insert(0, {"nom": "deug", "mots_cles": [r"\bdeug\b"]})   # meme nom, autre casse
    assert erreurs_contiennent(registre, "sous-dossier en double")


def test_autres_avec_mots_cles_refuse(registre):
    trouver_categorie(registre, "diplomes")["sous_dossiers"][-1]["mots_cles"] = [r"\bbts\b"]
    assert erreurs_contiennent(registre, "Autres ne doit pas avoir de mots-cles")


def test_nom_fichier_absent_refuse(registre):
    del registre["categories"][0]["nom_fichier"]
    assert erreurs_contiennent(registre, "nom_fichier absent")


def test_nom_fichier_partie_inconnue_refusee(registre):
    registre["categories"][0]["nom_fichier"]["parties"].append("titulaire")  # pas un champ de facture
    assert erreurs_contiennent(registre, "partie de nom_fichier inconnue 'titulaire'")


def test_nom_fichier_sous_dossier_sans_sous_dossiers_refuse(registre):
    registre["categories"][0]["nom_fichier"]["parties"].insert(0, "sous_dossier")
    assert erreurs_contiennent(registre, "inconnue 'sous_dossier'")


def test_nom_fichier_prefixe_invalide(registre):
    registre["categories"][0]["nom_fichier"]["prefixe"] = "Facturé"
    assert erreurs_contiennent(registre, "prefixe de nom_fichier invalide")


def test_modele_nom_fichier(registre):
    assert modele_nom_fichier(registre, "diplomes") == \
        {"prefixe": "diplome", "parties": ["sous_dossier", "titulaire"]}
    assert modele_nom_fichier(registre, "bulletin_paie") == \
        {"prefixe": "bulletin_paie", "parties": ["titre", "personne"]}


def test_toutes_les_erreurs_sont_listees(registre):
    """On ne s'arrete pas a la premiere erreur : l'humain voit tout d'un coup."""
    registre["categories"][0]["nom"] = "Factures"
    registre["categories"][1]["mots_cles"].append(r"\b(")
    assert len(verifier_registre(registre)) >= 2


# --- 3 bis. Profil de machine (config/machine.json) ---------------------------
def test_profils_du_projet():
    from src.config import RACINE, charger_profil
    modeste = charger_profil()
    assert modeste["nom"] == "modeste" and modeste["threads_ocr"] == 2
    assert modeste["seuil_ram_worker_ocr_mo"] == 1536 and modeste["num_gpu"] == 0
    assert modeste["ocr_et_llm_simultanes"] is False
    assert modeste["dossier_entree"] == RACINE / "Folder_Entree"     # relatif -> projet
    assert charger_profil("performant")["ocr_et_llm_simultanes"] is True


def _machine(tmp_path, **modifs):
    base = json.loads((Path(__file__).parent.parent / "config" / "machine.json")
                      .read_text(encoding="utf-8"))
    for cle, valeur in modifs.items():
        if cle == "dossiers":
            base["dossiers"] = valeur
        else:
            base["profils"]["modeste"][cle] = valeur
    chemin = tmp_path / "machine.json"
    chemin.write_text(json.dumps(base), encoding="utf-8")
    return chemin


def test_profil_inconnu(tmp_path):
    from src.config import charger_profil
    with pytest.raises(ErreurRegistre, match="profil de machine inconnu"):
        charger_profil("superordinateur")


@pytest.mark.parametrize("cle, valeur", [("threads_ocr", "2"), ("num_gpu", True),
                                         ("ocr_et_llm_simultanes", "non"),
                                         ("modele_llm", None)])
def test_profil_invalide(tmp_path, cle, valeur):
    from src.config import charger_profil
    with pytest.raises(ErreurRegistre, match=f"'{cle}'"):
        charger_profil(chemin=_machine(tmp_path, **{cle: valeur}))


def test_dossiers_absolus_acceptes(tmp_path):
    from src.config import charger_profil
    chemin = _machine(tmp_path, dossiers={"entree": str(tmp_path / "E"), "sortie": "S"})
    p = charger_profil(chemin=chemin)
    assert p["dossier_entree"] == tmp_path / "E"


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


# --- 6. Choix du sous-dossier (phrases inventees) --------------------------
@pytest.mark.parametrize("texte, attendu", [
    # Exactement un sous-dossier reconnu -> ce sous-dossier
    ("Diplôme d'Études Universitaires Générales (DEUG), mention Bien", "DEUG"),
    ("DIPLÔME DE LICENCE EN SCIENCES ÉCONOMIQUES", "Licence"),
    ("Le grade de Master est conféré à Titulaire Exemple", "Master"),
    ("Diplôme national de Doctorat en droit", "Doctorat"),
    # Aucun -> Autres
    ("Diplôme de Technicien Supérieur (BTS), mention Assez Bien", "Autres"),
    ("Diplôme du Baccalauréat, série Sciences", "Autres"),
    # Plusieurs -> Autres (impossible de trancher sans risque)
    ("Titulaire d'une Licence, admis en première année de Master", "Autres"),
])
def test_choisir_sous_dossier_diplomes(registre, texte, attendu):
    diplomes = trouver_categorie(registre, "diplomes")
    assert choisir_sous_dossier(diplomes, texte) == attendu


@pytest.mark.parametrize("texte, attendu", [
    ("ATTESTATION DE TRAVAIL - la société Exemple atteste...", "Travail"),
    ("Attestation de scolarité pour l'année en cours", "Scolarite"),
    ("CERTIFICAT DE SCOLARITÉ", "Scolarite"),
    ("Attestation de salaire", "Autres"),                          # aucun
    ("Attestation de travail et certificat de scolarité", "Autres"),  # plusieurs
])
def test_choisir_sous_dossier_attestations(registre, texte, attendu):
    attestations = trouver_categorie(registre, "attestations")
    assert choisir_sous_dossier(attestations, texte) == attendu


def test_choisir_sous_dossier_categorie_sans_sous_dossier(registre):
    factures = trouver_categorie(registre, "factures")
    assert choisir_sous_dossier(factures, "Facture de Licence logicielle") is None
