"""
test_llm.py - Tests du client Ollama (src/llm.py).

Tests normaux : Ollama est SIMULE (aucun besoin du vrai serveur).
Test reel (marque ollama_reel, exclu par defaut) : texte INVENTE, mesure du
chargement et de la reponse. A lancer a part, OCR arrete :
    python -m pytest -m ollama_reel -s
"""

import json
import logging
import time

import pytest
import requests

from src.llm import (MARQUE_TRONQUE, NUM_CTX, ClientOllama, budget_caracteres,
                     compacter, conforme_au_schema, preparer_prompt, tronquer)

SCHEMA = {"type": "object",
          "properties": {"type_document": {"type": "string",
                                           "enum": ["facture", "diplome", "autre"]}},
          "required": ["type_document"]}
SECRET = "Titulaire Secret AB123456"


# --- Faux serveur Ollama -----------------------------------------------------
class FausseReponse:
    def __init__(self, donnees=None, statut=200):
        self._donnees, self.status_code = donnees or {}, statut

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def json(self):
        return self._donnees


class FausseSession:
    """Rejoue une liste de reponses (ou d'exceptions) et note chaque requete."""

    def __init__(self, reponses_post=(), reponse_get=None):
        self.reponses_post = list(reponses_post)
        self.reponse_get = reponse_get
        self.posts, self.gets = [], []

    def get(self, url, timeout=None):
        self.gets.append(url)
        if isinstance(self.reponse_get, Exception):
            raise self.reponse_get
        return self.reponse_get

    def post(self, url, json=None, timeout=None):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        suivante = self.reponses_post.pop(0) if self.reponses_post else FausseReponse({})
        if isinstance(suivante, Exception):
            raise suivante
        return suivante


def generation(reponse_texte, load_ns=0, lus=100, generes=10):
    return FausseReponse({"response": reponse_texte, "load_duration": load_ns,
                          "prompt_eval_count": lus, "eval_count": generes})


def client(*reponses, **kw):
    return ClientOllama(session=FausseSession(reponses, **kw))


# --- 1. Verification du serveur ----------------------------------------------
def test_verifier_ok():
    c = client(reponse_get=FausseReponse({"models": [{"name": "phi4-mini:latest"}]}))
    assert c.verifier() == (True, None)


def test_verifier_serveur_absent():
    c = client(reponse_get=requests.exceptions.ConnectionError("refus"))
    ok, raison = c.verifier()
    assert not ok and "ne repond pas" in raison


def test_verifier_modele_absent():
    c = client(reponse_get=FausseReponse({"models": [{"name": "phi4-mini-reasoning:latest"},
                                                     {"name": "llama3:8b"}]}))
    ok, raison = c.verifier()
    assert not ok and "absent" in raison


def test_modeles_charges():
    c = client(reponse_get=FausseReponse({"models": [{"name": "phi4-mini:latest"}]}))
    assert c.modeles_charges() == ["phi4-mini:latest"]
    assert client(reponse_get=requests.exceptions.ConnectionError()).modeles_charges() == []


# --- 2. Appel et reglages ------------------------------------------------------
def test_appel_reglages_imposes():
    c = client(generation('{"type_document": "facture"}', load_ns=2_500_000_000))
    rep = c.generer_json("consigne", SCHEMA)
    assert rep.ok and rep.donnees == {"type_document": "facture"} and rep.essais == 1
    assert rep.chargement_s == pytest.approx(2.5) and rep.tokens_lus == 100
    corps = c.session.posts[0]["json"]
    assert corps["options"] == {"num_ctx": 2048, "num_gpu": 0, "temperature": 0}
    assert corps["format"] == SCHEMA and corps["stream"] is False
    assert corps["keep_alive"] == 0                          # decharge par defaut
    assert c.session.posts[0]["timeout"] == c.delai_s        # delai maximum par appel


def test_json_invalide_puis_valide():
    c = client(generation("pas du json"), generation('{"type_document": "diplome"}'))
    rep = c.generer_json("consigne", SCHEMA)
    assert rep.ok and rep.essais == 2 and rep.donnees["type_document"] == "diplome"


def test_json_invalide_deux_fois_echec_propre():
    c = client(generation("{"), generation("toujours pas"))
    rep = c.generer_json("consigne", SCHEMA)
    assert not rep.ok and rep.essais == 2 and rep.donnees is None
    assert rep.erreur == "json invalide ou non conforme au schema"
    assert len(c.session.posts) == 2                          # UN seul nouvel essai


@pytest.mark.parametrize("reponse", ['{"autre_cle": 1}', '{"type_document": "contrat"}',
                                     '["facture"]'])
def test_json_non_conforme_compte_comme_invalide(reponse):
    rep = client(generation(reponse), generation(reponse)).generer_json("c", SCHEMA)
    assert not rep.ok and rep.essais == 2


@pytest.mark.parametrize("panne", [requests.exceptions.Timeout("trop long"),
                                   requests.exceptions.ConnectionError("coupe"),
                                   FausseReponse({}, statut=500)])
def test_panne_sans_nouvel_essai_ni_exception(panne):
    c = client(panne)
    rep = c.generer_json("consigne", SCHEMA)
    assert not rep.ok and rep.essais == 1 and rep.erreur.startswith("appel echoue")
    assert len(c.session.posts) == 1


def test_alerte_contexte_presque_plein():
    rep = client(generation('{"type_document": "autre"}', lus=NUM_CTX)).generer_json("c", SCHEMA)
    assert rep.ok and any("contexte presque plein" in a for a in rep.alertes)


def test_client_depuis_le_profil_performant():
    from src.config import charger_profil
    session = FausseSession([generation('{"type_document": "facture"}')])
    c = ClientOllama.depuis_profil(charger_profil("performant"), session=session)
    c.generer_json("c", SCHEMA)
    assert session.posts[0]["json"]["options"] == {"num_ctx": 4096, "num_gpu": 99,
                                                   "temperature": 0}
    assert session.posts[0]["timeout"] == 120


# --- 3. keep_alive et chargement pour un lot ---------------------------------
def test_modele_charge_pendant_le_lot_puis_decharge():
    c = client(FausseReponse({"load_duration": 4_000_000_000}),     # prechargement
               generation('{"type_document": "facture"}'),
               generation('{"type_document": "diplome"}'),
               FausseReponse({}))                                   # dechargement
    with c.modele_charge():
        assert c.generer_json("a", SCHEMA).ok and c.generer_json("b", SCHEMA).ok
    alive = [p["json"]["keep_alive"] for p in c.session.posts]
    assert alive == ["30m", "30m", "30m", 0]          # charge, 2 appels, decharge
    assert "prompt" not in c.session.posts[0]["json"]  # prechargement sans prompt
    assert c.keep_alive == 0                           # retour au reglage par defaut


def test_dechargement_meme_si_erreur_dans_le_lot():
    c = client(FausseReponse({}), FausseReponse({}))
    with pytest.raises(RuntimeError):
        with c.modele_charge():
            raise RuntimeError("panne pendant le lot")
    assert c.session.posts[-1]["json"]["keep_alive"] == 0


def test_dechargement_ne_leve_jamais():
    assert client(requests.exceptions.ConnectionError()).decharger() is False


# --- 4. Troncature du texte ----------------------------------------------------
def test_compacter():
    assert compacter("a   b\t\tc\n\n\n\n d  \n") == "a b c\n\nd"


def test_texte_court_intact():
    assert tronquer("FACTURE\nligne", 1000) == ("FACTURE\nligne", False)


def test_texte_long_garde_le_debut():
    texte = "FACTURE N° FA-001\n" + "\n".join(f"ligne {i} " + "x" * 40 for i in range(500))
    coupe, tronque = tronquer(texte, 2000)
    assert tronque and len(coupe) <= 2000
    assert coupe.startswith("FACTURE N° FA-001")      # zone titre gardee
    assert coupe.endswith(MARQUE_TRONQUE)


def test_budget_tient_dans_le_contexte():
    consigne = "x" * 1000
    budget = budget_caracteres(consigne)
    tokens_estimes = (len(consigne) + budget) / 2.5
    assert tokens_estimes <= NUM_CTX - 400


def test_preparer_prompt_depuis_un_fichier(tmp_path):
    (tmp_path / "essai.txt").write_text("Consigne.\n<<<\n$texte\n>>>", encoding="utf-8")
    prompt, tronque = preparer_prompt("essai", "FACTURE inventée", dossier=tmp_path)
    assert prompt == "Consigne.\n<<<\nFACTURE inventée\n>>>" and not tronque


def test_appeler_prompt_absent_echec_propre(tmp_path):
    rep = client().appeler("inexistant", "texte", SCHEMA, dossier_prompts=tmp_path)
    assert not rep.ok and rep.erreur == "prompt invalide (FileNotFoundError)"


def test_appeler_texte_long_signale_la_troncature(tmp_path):
    (tmp_path / "p.txt").write_text("C : $texte", encoding="utf-8")
    c = client(generation('{"type_document": "autre"}'))
    rep = c.appeler("p", "mot " * 5000, SCHEMA, dossier_prompts=tmp_path)
    assert rep.ok and rep.texte_tronque
    assert "texte tronque pour tenir dans le contexte" in rep.alertes
    assert len(c.session.posts[0]["json"]["prompt"]) < 5000


def test_prompts_du_projet_sont_des_fichiers():
    prompt, _ = preparer_prompt("essai_llm", "texte")
    assert "DOCUMENT" in prompt and "$texte" not in prompt


# --- 5. Confidentialite --------------------------------------------------------
def test_aucun_texte_affiche_ni_journalise(tmp_path, caplog, capsys):
    (tmp_path / "p.txt").write_text("$texte", encoding="utf-8")
    c = client(generation(json.dumps({"type_document": "autre"})))
    with caplog.at_level(logging.DEBUG):
        rep = c.appeler("p", SECRET, SCHEMA, dossier_prompts=tmp_path)
    sortie = capsys.readouterr()
    assert SECRET not in caplog.text and SECRET not in sortie.out + sortie.err
    assert SECRET not in repr(rep)


def test_repr_ne_montre_pas_les_donnees():
    rep = client(generation(json.dumps({"type_document": "facture"}))).generer_json("c", SCHEMA)
    assert "facture" not in repr(rep)


# --- 6. Test REEL (a lancer a part) ---------------------------------------------
TEXTE_INVENTE = """FACTURE N° FA-2026-00042
Date : 15/09/2026
Fournisseur : Société Exemple SARL, Casablanca
Désignation : Maintenance informatique septembre 2026
Montant HT : 1 000,00 DH - TVA 20 % : 200,00 DH - Total TTC : 1 200,00 DH"""

SCHEMA_ESSAI = {"type": "object",
                "properties": {"fournisseur": {"type": ["string", "null"]},
                               "montant_ttc": {"type": ["string", "null"]}},
                "required": ["fournisseur", "montant_ttc"]}


@pytest.mark.ollama_reel
def test_reel_chargement_et_reponse():
    """Vrai Phi-4-mini, texte INVENTE (affichable). Mesure chargement et reponse."""
    c = ClientOllama()
    ok, raison = c.verifier()
    assert ok, raison
    assert c.modeles_charges() == [], "un modele est deja charge : resultats faux"

    debut = time.perf_counter()
    rep = c.appeler("essai_llm", TEXTE_INVENTE, SCHEMA_ESSAI)
    total = time.perf_counter() - debut
    print(f"\n  ok={rep.ok} essais={rep.essais} erreur={rep.erreur}")
    print(f"  chargement du modele : {rep.chargement_s:.1f} s")
    print(f"  duree totale         : {total:.1f} s (reponse hors chargement : "
          f"{total - rep.chargement_s:.1f} s)")
    print(f"  tokens lus / generes : {rep.tokens_lus} / {rep.tokens_generes}")
    print(f"  caracteres/token     : {len(preparer_prompt('essai_llm', TEXTE_INVENTE)[0]) / max(rep.tokens_lus, 1):.2f}")
    print(f"  reponse (inventee)   : {rep.donnees}")
    assert rep.ok
    assert "Exemple" in (rep.donnees["fournisseur"] or "")
    time.sleep(2)
    assert c.modeles_charges() == [], "keep_alive=0 : le modele devrait etre decharge"
