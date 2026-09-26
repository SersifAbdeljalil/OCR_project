"""
streamlit_app.py - Interface locale de tri et de validation.

Lancement : double-clic sur lancer_interface.bat (ou
    .\\.venv\\Scripts\\python.exe -m streamlit run app\\streamlit_app.py )
L'interface n'ecoute QUE sur 127.0.0.1 et n'envoie aucune statistique
(.streamlit/config.toml).

Ecran 1 « Deposer et trier » : depot de fichiers dans le dossier d'entree, tri lance
dans un sous-process (un seul a la fois), progression, resume.
Ecran 2 « Documents » : tous les documents traites ; pour chacun : image avec les
lignes OCR peu sures surlignees, champs modifiables avec icone de copie, categorie et
sous-dossier, texte complet copiable, historique ; Valider / Rejeter / Creer une categorie.

TOUTE la logique est dans src/validation.py : ce fichier ne fait que l'affichage.
Pour les tests seulement, des variables d'environnement remplacent les dossiers :
TRI_DOSSIER_ENTREE, TRI_DOSSIER_SORTIE, TRI_DOSSIER_DATA, TRI_REGISTRE.
"""

import html
import os
import sys
from decimal import Decimal
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

import streamlit as st                                                   # noqa: E402

from src import validation                                               # noqa: E402
from src.config import (CHEMIN_REGISTRE, DOSSIER_AUTRES, DOSSIER_DATA,   # noqa: E402
                        charger_profil, charger_registre, champs_attendus,
                        trouver_categorie)
from src.extractor import charger_config                                 # noqa: E402
from src.llm import ClientOllama                                         # noqa: E402
from src.pipeline import lister_documents as documents_en_attente       # noqa: E402

ECRANS = ["Déposer et trier", "Documents"]

# --- Style ---------------------------------------------------------------------
# Complete le theme de .streamlit/config.toml (couleurs et polices LOCALES).
# Contrastes verifies (WCAG AA >= 4,5:1) : ambre #8A5A12 sur cream 5,1 ; cocoa sur
# ambre doux #EFD9A8 11,1 ; terracotta #9C3F28 sur cream 5,7 ; cocoa sur terracotta
# doux #EBC6B6 9,7 ; olive #3F5A36 sur cream 6,6 ; cocoa sur sand 7,9.
STYLE = """
<style>
/* Etiquettes de section : petites capitales espacees (Inter n'a pas de vraies
   petites capitales : majuscules reduites et espacees) */
.etiquette {
  font-family: "Inter", sans-serif; font-weight: 600; font-size: 0.72rem;
  text-transform: uppercase; letter-spacing: 0.14em; color: #4A3A30;
  border-bottom: 1px solid #C8B8A2; padding-bottom: 0.25rem; margin: 1.1rem 0 0.5rem 0;
}
/* Accents des titres : Fraunces italique */
h1 em, h2 em, h3 em { font-family: "Fraunces", serif; font-style: italic; font-weight: 400; }
/* Montants, dates, numeros : sans-serif a chiffres de largeur fixe */
[data-testid="stCode"] pre, [data-testid="stCode"] code, input,
[data-testid="stMetricValue"], .chiffres {
  font-family: "Inter", sans-serif !important;
  font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1;
}
/* Pastilles de statut */
.pastille {
  display: inline-block; padding: 0.12rem 0.65rem; border-radius: 999px;
  font-family: "Inter", sans-serif; font-size: 0.72rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.08em; border: 1px solid;
}
.p-a_valider, .p-a_verifier { background: #EFD9A8; color: #2A2420; border-color: #8A5A12; }
.p-erreur                   { background: #EBC6B6; color: #2A2420; border-color: #9C3F28; }
.p-range, .p-valide         { background: #F2EDE4; color: #3F5A36; border-color: #3F5A36; }
.p-autres                   { background: #C8B8A2; color: #2A2420; border-color: #C8B8A2; }
</style>
"""


def etiquette(texte: str):
    """Etiquette de section en petites capitales espacees."""
    st.html(f'<div class="etiquette">{html.escape(texte)}</div>')


def pastille(statut: str, libelle: str = None):
    """Pastille coloree : ambre (a valider / a verifier), terracotta (erreur),
    olive (range / valide), sand (autres)."""
    texte = libelle or LIBELLES_STATUTS.get(statut, statut)
    st.html(f'<span class="pastille p-{html.escape(statut)}">{html.escape(texte)}</span>')
ORDRE_STATUTS = {"a_valider": 0, "a_verifier": 1, "range": 2, "valide": 3, "autres": 4}
LIBELLES_STATUTS = {"a_valider": "à valider", "a_verifier": "à vérifier", "range": "rangé",
                    "valide": "validé", "autres": "autres"}
AUTOMATIQUE = "(automatique)"


# --- Reglages ------------------------------------------------------------------
def reglages() -> dict:
    profil = charger_profil()
    return {
        "profil": profil,
        "entree": Path(os.environ.get("TRI_DOSSIER_ENTREE") or profil["dossier_entree"]),
        "sortie": Path(os.environ.get("TRI_DOSSIER_SORTIE") or profil["dossier_sortie"]),
        "data": Path(os.environ.get("TRI_DOSSIER_DATA") or DOSSIER_DATA),
        "registre": Path(os.environ.get("TRI_REGISTRE") or CHEMIN_REGISTRE),
    }


def texte_valeur(champ: str, valeur, types: dict) -> str:
    """Valeur du JSON -> texte affiche (montants a 2 decimales)."""
    if valeur is None:
        return ""
    if types.get(champ) == "montant" and isinstance(valeur, (int, float, Decimal)):
        return f"{Decimal(str(valeur)):.2f}"
    return str(valeur)


def message(nature: str, texte: str):
    """Message garde pour l'affichage apres st.rerun()."""
    st.session_state["message"] = (nature, texte)


def afficher_message():
    if "message" in st.session_state:
        nature, texte = st.session_state.pop("message")
        getattr(st, nature)(texte)


# --- Ecran 1 : deposer et trier ---------------------------------------------------------
def ecran_deposer(R: dict):
    st.header("Déposer et *trier*")
    afficher_message()

    fichiers = st.file_uploader(
        "Glisser-déposer un ou plusieurs documents (PDF, images, Word, Excel)",
        accept_multiple_files=True,
        type=sorted(e.lstrip(".") for e in validation.EXTENSIONS_ACCEPTEES), key="depot")
    deja = st.session_state.setdefault("deja_copies", set())
    nouveaux = [f for f in (fichiers or []) if f.file_id not in deja]
    if nouveaux:
        deposes, refuses = validation.deposer_fichiers(
            [(f.name, f.getvalue()) for f in nouveaux], R["entree"])
        deja.update(f.file_id for f in nouveaux)
        st.success(f"{len(deposes)} fichier(s) copié(s) dans le dossier d'entrée.")
        if refuses:
            st.warning(f"{len(refuses)} fichier(s) refusé(s) : format non pris en charge.")

    en_attente = len(documents_en_attente(R["entree"]))
    st.write(f"Documents en attente dans le dossier d'entrée : **{en_attente}**")
    suivi_du_tri(R, en_attente)


@st.fragment(run_every="3s")
def suivi_du_tri(R: dict, en_attente: int):
    """Se rafraichit toutes les 3 s pendant un tri ; le reste de l'interface reste
    utilisable."""
    etat = validation.etat_tri(R["data"])
    if etat["en_cours"]:
        st.info(f"Tri en cours… {etat['progression'] or 'démarrage'}")
        st.button("Lancer le tri", disabled=True, key="lancer_tri",
                  help="Un tri est déjà en cours.")
        return
    if st.button("Lancer le tri", disabled=en_attente == 0, key="lancer_tri", type="primary"):
        commande = [sys.executable, str(RACINE / "run_pipeline.py"),
                    "--entree", str(R["entree"]), "--sortie", str(R["sortie"])]
        lance, texte = validation.lancer_tri(R["data"], commande)
        (st.success if lance else st.warning)(texte)
        st.rerun()
    resume = etat["resume"]
    if resume:
        etiquette("Dernier tri")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Rangés", resume.get("ranges") or 0)
        c2.metric("À valider", resume.get("a_valider") or 0)
        c3.metric("Erreurs", resume.get("erreurs") or 0)
        c4.metric("Durée (s)", resume.get("duree_s") or 0)
        if resume.get("erreurs"):
            pastille("erreur", f"{resume['erreurs']} erreur(s) : documents restés en entrée")
        elif resume.get("a_valider"):
            pastille("a_valider", f"{resume['a_valider']} document(s) à valider")


# --- Ecran 2 : documents -----------------------------------------------------------------
def ecran_documents(R: dict, registre: dict):
    st.header("Documents *traités*")
    afficher_message()
    fiches = validation.lister_documents(R["sortie"])
    if not fiches:
        st.info("Aucun document traité pour l'instant.")
        return

    c1, c2 = st.columns(2)
    categories = sorted({f.categorie or "-" for f in fiches})
    filtre_cat = c1.selectbox("Filtrer par catégorie", ["Toutes"] + categories,
                              key="filtre_categorie")
    statuts = sorted({f.statut for f in fiches}, key=lambda s: ORDRE_STATUTS.get(s, 9))
    filtre_statut = c2.selectbox("Filtrer par statut", ["Tous"] + statuts, key="filtre_statut",
                                 format_func=lambda s: LIBELLES_STATUTS.get(s, s))
    choisies = [f for f in fiches
                if (filtre_cat == "Toutes" or (f.categorie or "-") == filtre_cat)
                and (filtre_statut == "Tous" or f.statut == filtre_statut)]
    choisies.sort(key=lambda f: (ORDRE_STATUTS.get(f.statut, 9), str(f.chemin_json)))
    if not choisies:
        st.info("Aucun document pour ces filtres.")
        return

    options = [str(f.chemin_json) for f in choisies]
    fiche_par_chemin = {str(f.chemin_json): f for f in choisies}
    # Document a reselectionner apres Valider / Rejeter / Creer (il a pu changer de nom) :
    # Streamlit interdit de modifier la cle d'une liste deja affichee, d'ou cette etape.
    if "document_suivant" in st.session_state:
        st.session_state["document"] = st.session_state.pop("document_suivant")
    if st.session_state.get("document") not in options:
        st.session_state["document"] = options[0]
    chemin = st.selectbox(
        f"Document ({len(options)})", options, key="document",
        format_func=lambda c: (f"{LIBELLES_STATUTS.get(fiche_par_chemin[c].statut)} · "
                               f"{fiche_par_chemin[c].categorie or '-'} · "
                               f"{Path(c).relative_to(R['sortie']).as_posix()}"))
    detail_document(R, registre, Path(chemin), fiche_par_chemin[chemin])


def detail_document(R: dict, registre: dict, chemin: Path, fiche):
    doc = validation.charger_document(chemin)
    cle = str(abs(hash(str(chemin))))
    pastille(fiche.statut)
    if fiche.raison:
        st.warning(f"Raison : {fiche.raison}")
    if fiche.alertes:
        with st.expander(f"Alertes ({len(fiche.alertes)})"):
            for a in fiche.alertes:
                st.write(f"- {a}")

    gauche, droite = st.columns([1, 1])
    with gauche:
        etiquette("Aperçu")
        image_du_document(doc, cle)
    with droite:
        etiquette("Classement")
        saisies, categorie, sous = champs_du_document(registre, doc, cle)
        boutons(R, registre, doc, chemin, saisies, categorie, sous)

    with st.expander("Texte complet"):
        st.code(doc.texte or " ", language=None)          # icone de copie integree
    with st.expander(f"Historique des corrections ({len(doc.historique)})"):
        if doc.historique:
            st.dataframe(doc.historique, width="stretch")
        else:
            st.write("Aucune correction.")
    creer_categorie(R, doc, chemin, cle)


def image_du_document(doc, cle: str):
    if not doc.original or not doc.original.exists():
        st.info("Original introuvable.")
        return
    pages = {p.get("page"): p for p in doc.pages_ocr}
    try:
        _, _, nb_pages = validation.apercu_page(doc.original, None, 1)
        numero = 1
        if nb_pages > 1:
            numero = st.number_input("Page", 1, nb_pages, 1, key=f"page_{cle}")
        image, surlignees, _ = validation.apercu_page(doc.original, pages.get(numero), numero)
        st.image(image, width="stretch")
        if pages.get(numero):
            st.caption(f"{surlignees} ligne(s) OCR de confiance < 0,90 surlignée(s).")
    except Exception as err:                        # fichier illisible : pas d'apercu
        st.info(f"Aperçu impossible ({type(err).__name__}).")


def champs_du_document(registre: dict, doc, cle: str):
    types = {nom: c["type"] for nom, c in charger_config().items()}
    noms = [c["nom"] for c in registre["categories"]] + [DOSSIER_AUTRES.lower()]
    type_actuel = doc.info.get("type")
    if type_actuel and type_actuel not in noms:
        st.warning(f"Catégorie proposée, absente du registre : « {type_actuel} ». "
                   "La créer (en bas), ou choisir une catégorie existante.")
    index = noms.index(type_actuel) if type_actuel in noms else len(noms) - 1
    categorie = st.selectbox("Catégorie", noms, index=index, key=f"categorie_{cle}")
    cat = trouver_categorie(registre, categorie)
    sous_noms = [sd["nom"] for sd in (cat or {}).get("sous_dossiers", [])]
    sous = None
    if sous_noms:
        actuel = validation.sous_dossier_actuel(doc.chemin_json, registre)
        options = [AUTOMATIQUE] + sous_noms
        choix = st.selectbox("Sous-dossier", options,
                             index=options.index(actuel) if actuel in options else 0,
                             key=f"sous_{cle}_{categorie}")
        sous = None if choix == AUTOMATIQUE else choix

    etiquette("Champs extraits")
    saisies = {}
    for champ in champs_attendus(registre, categorie):
        initial = texte_valeur(champ, doc.champs.get(champ), types)
        c1, c2 = st.columns([3, 2])
        saisie = c1.text_input(champ, value=initial, key=f"champ_{cle}_{champ}")
        with c2:
            st.caption("copier")
            st.code(initial or " ", language=None)            # icone de copie integree
        saisies[champ] = (initial, saisie)
    return saisies, categorie, sous


def boutons(R: dict, registre: dict, doc, chemin: Path, saisies: dict, categorie: str, sous):
    c1, c2 = st.columns(2)
    logs = R["data"] / "logs"
    if c1.button("Valider", type="primary", key="valider"):
        corrections = {c: s for c, (initial, s) in saisies.items() if s != initial}
        try:
            res = validation.enregistrer_corrections(
                chemin, corrections,
                categorie=categorie if categorie != doc.info.get("type") else None,
                registre=registre, sortie=R["sortie"], dossier_logs=logs, sous_dossier=sous)
        except (validation.ErreurValidation, ValueError) as err:
            st.error(f"Validation impossible : {err}")
            return
        texte = f"Document validé ({res.nb_corrections} correction(s))."
        if res.alertes:
            texte += " Alertes : " + " ; ".join(res.alertes)
        message("success", texte)
        st.session_state["document_suivant"] = str(res.chemin_json)
        st.rerun()
    if c2.button("Rejeter (vers Autres)", key="rejeter"):
        res = validation.rejeter_document(chemin, registre, R["sortie"], logs)
        message("success", "Document rejeté : déplacé dans Autres/.")
        st.session_state["document_suivant"] = str(res.chemin_json)
        st.rerun()


def creer_categorie(R: dict, doc, chemin: Path, cle: str):
    with st.expander("Créer une catégorie"):
        nom = st.text_input("Nom de la nouvelle catégorie",
                            value=doc.info.get("categorie_proposee") or "",
                            key=f"nouvelle_categorie_{cle}")
        tri = validation.etat_tri(R["data"])["en_cours"]
        if st.button("Proposer des mots-clés (LLM)", key="proposer", disabled=tri or not nom,
                     help="Indisponible pendant un tri (mémoire)." if tri else None):
            with st.spinner("Le modèle propose des mots-clés…"):
                client = ClientOllama.depuis_profil(R["profil"])
                mots = validation.proposer_mots_cles(nom, doc.texte, client)
            st.session_state[f"mots_{cle}"] = "\n".join(mots)
            if not mots:
                st.warning("Aucune proposition (moteur indisponible ?) : saisir 3 à 5 mots-clés.")
        mots = st.text_area("Mots-clés (un par ligne, 3 à 5) : à confirmer ou corriger",
                            key=f"mots_{cle}")
        if st.button("Créer la catégorie et ranger le document", key="creer",
                     disabled=not nom or not mots.strip()):
            liste = [m.strip() for m in mots.splitlines() if m.strip()]
            try:
                nom_norm, res = validation.creer_categorie(
                    nom, liste, chemin, R["registre"], R["sortie"], R["data"] / "logs")
            except (validation.ErreurValidation, ValueError) as err:
                st.error(f"Création impossible : {err}")
                return
            message("success", f"Catégorie « {nom_norm} » créée ; document rangé.")
            st.session_state["document_suivant"] = str(res.chemin_json)
            st.rerun()


# --- Page ----------------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Tri documentaire", layout="wide")
    st.html(STYLE)
    R = reglages()
    registre = charger_registre(R["registre"])
    ecran = st.sidebar.radio("Écran", ECRANS, key="ecran")
    st.sidebar.caption("Interface locale (127.0.0.1) : rien ne quitte ce PC.")
    if ecran == ECRANS[0]:
        ecran_deposer(R)
    else:
        ecran_documents(R, registre)


main()
