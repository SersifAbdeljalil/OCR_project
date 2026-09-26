"""
pipeline.py - Traitement complet de Folder_Entree/ (1 a 50 documents ou plus).

Ordre (jamais OCR et LLM en meme temps sur une machine modeste) :
    0. nettoyage de data/ocr/ (plus de 7 jours) ; documents deja traites ecartes ;
    1. LECTURE de tous les documents (texte natif, pages a OCR) ; illisible -> A_Valider ;
    2. OCR de toutes les pages en un lot (sous-process) ;
    3. tous les appels LLM, modele garde charge : classification, extraction, rangement ;
    4. dechargement du modele ; suppression du dossier OCR du lot.

Portabilite : tous les reglages de la machine viennent de config/machine.json
(profil, dossiers, Ollama). Aucun chemin propre a ce PC dans le code.

Gros lots : progression « [12/50] » (compteurs seulement), journal dans data/logs/,
reprise d'un lot interrompu : chaque document range est note (empreinte SHA-256)
dans data/etat/traites.json et n'est jamais retraite.

CONFIDENTIALITE : ni l'affichage ni le journal ne contiennent de texte ou de valeur ;
les noms de fichiers passent par masking.nom_affichable.
"""

# --- Imports ---------------------------------------------------------------
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from src.classifier import MoteurPhi4, classer
from src.config import DOSSIER_DATA, charger_profil, registre_par_defaut
from src.extract_text import extraire
from src.extractor import charger_config, extraire_document, lignes_du_document
from src.filer import deplacer_vers_traites, envoyer_a_valider, ranger_document
from src.llm import ClientOllama
from src.masking import nom_affichable
from src.ocr_worker import STATUT_OK, fusionner_texte, lancer_ocr, taches_depuis_extraction
from src.schemas import DocumentSortie

JOURS_CONSERVATION_OCR = 7        # decision CLAUDE.md : data/ocr/ purge apres 7 jours
FICHIERS_IGNORES = {"desktop.ini", "thumbs.db", ".ds_store"}


class ErreurPipeline(RuntimeError):
    """Le lot ne peut pas demarrer (message sans donnee de document)."""


# --- 1. Resultats ------------------------------------------------------------
@dataclass
class ResultatDocument:
    fichier: str                     # nom AFFICHABLE (masque)
    decision: str                    # "range", "a_valider", "deja_traite" ou "erreur"
    categorie: str = None
    confiance: float = None
    destination: str = None          # chemin AFFICHABLE (masque)
    nb_alertes: int = 0
    duree_s: float = 0.0


@dataclass
class BilanLot:
    total: int = 0
    ranges: int = 0
    a_valider: int = 0
    erreurs: int = 0
    deja_traites: int = 0
    duree_s: float = 0.0
    duree_ocr_s: float = 0.0
    duree_llm_s: float = 0.0
    pages_ocr: int = 0
    pic_ram_ocr_mo: float = 0.0
    ocr_purges: int = 0
    documents: list = field(default_factory=list)
    alertes: list = field(default_factory=list)
    journal: Path = None

    def compter(self, r: ResultatDocument):
        self.documents.append(r)
        if r.decision == "range":
            self.ranges += 1
        elif r.decision == "a_valider":
            self.a_valider += 1
        elif r.decision == "deja_traite":
            self.deja_traites += 1
        else:
            self.erreurs += 1


# --- 2. Outils : journal, etat, nettoyage ----------------------------------------
class Journal:
    """data/logs/pipeline_<horodatage>.jsonl : une ligne JSON par evenement.
    Seulement des compteurs, des categories, des decisions et des noms MASQUES."""

    def __init__(self, dossier: Path):
        dossier.mkdir(parents=True, exist_ok=True)
        self.chemin = dossier / f"pipeline_{datetime.now():%Y%m%d_%H%M%S_%f}.jsonl"

    def ecrire(self, evenement: str, **infos):
        ligne = {"horodatage": datetime.now().isoformat(timespec="seconds"),
                 "evenement": evenement, **infos}
        with open(self.chemin, "a", encoding="utf-8") as f:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")


class EtatTraites:
    """data/etat/traites.json : empreintes SHA-256 des documents deja ranges
    (ou mis en A_Valider). Permet de reprendre un lot interrompu sans rien retraiter."""

    def __init__(self, chemin: Path):
        self.chemin = chemin
        try:
            self.donnees = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.donnees = {}

    def __contains__(self, empreinte: str) -> bool:
        return empreinte in self.donnees

    def ajouter(self, empreinte: str, decision: str):
        self.donnees[empreinte] = {"date": datetime.now().isoformat(timespec="seconds"),
                                   "decision": decision}
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        provisoire = self.chemin.with_suffix(".tmp")      # ecriture sure : fichier provisoire
        provisoire.write_text(json.dumps(self.donnees, indent=1), encoding="utf-8")
        os.replace(provisoire, self.chemin)                # puis remplacement en un coup


def empreinte(chemin: Path) -> str:
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloc)
    return h.hexdigest()


def nettoyer_ocr_anciens(dossier_ocr: Path, jours: int = JOURS_CONSERVATION_OCR) -> int:
    """Supprime les dossiers de lot OCR de plus de `jours` jours. Renvoie leur nombre."""
    if not dossier_ocr.is_dir():
        return 0
    limite = datetime.now() - timedelta(days=jours)
    supprimes = 0
    for d in dossier_ocr.iterdir():
        if d.is_dir() and datetime.fromtimestamp(d.stat().st_mtime) < limite:
            shutil.rmtree(d, ignore_errors=True)
            supprimes += 1
    return supprimes


def lister_documents(entree: Path) -> list:
    """Fichiers a traiter : ceux directement dans Folder_Entree/ (pas Traites/),
    sans les fichiers systeme ni les fichiers temporaires d'Office (~$...)."""
    if not entree.is_dir():
        return []
    return sorted(p for p in entree.iterdir()
                  if p.is_file() and p.name.lower() not in FICHIERS_IGNORES
                  and not p.name.startswith(("~$", ".")))


def lignes_ocr_pour_json(pages: dict) -> dict:
    """Lignes OCR (texte, confiance, cadre) par page, pour le .json de A_Valider
    (decision CLAUDE.md : copiees dans le .json, puis data/ocr/ est supprime)."""
    return {"pages": [
        {"page": n, "statut": p.statut, "largeur": p.largeur, "hauteur": p.hauteur,
         "dpi": p.dpi, "orientation": p.orientation, "reduction": p.reduction,
         "lignes": [{"texte": l.texte, "confiance": l.confiance, "cadre": l.cadre}
                    for l in p.lignes]}
        for n, p in sorted(pages.items())]}


# --- 3. Un document -----------------------------------------------------------------
def traiter_document(chemin: Path, extraction, pages: dict, moteur, client, registre,
                     config, entree: Path, sortie: Path):
    """Classification, extraction, normalisation, rangement d'UN document.
    Renvoie (ResultatRangement, type retenu, confiance)."""
    lignes = lignes_du_document(extraction, pages)
    texte, alertes_fusion = fusionner_texte(extraction, pages)
    confiances = [l.confiance for p in pages.values() if p.statut == STATUT_OK
                  for l in p.lignes]
    cls = classer(texte, registre, moteur=moteur, texte_natif=extraction.texte,
                  confiances_ocr=confiances)
    type_doc = cls.categorie or cls.categorie_proposee or "autres"
    ext = extraire_document(lignes, type_doc, client, registre, config)

    alertes = cls.raisons + cls.alertes + extraction.alertes + alertes_fusion + ext.alertes
    doc = DocumentSortie.model_validate({
        "type": type_doc, "source": chemin.name,
        "date_traitement": datetime.now().isoformat(timespec="seconds"),
        "confiance_classification": cls.confiance,
        "champs": ext.valeurs(),
        "necessite_validation_humaine": (cls.necessite_validation_humaine
                                         or ext.necessite_validation_humaine
                                         or bool(alertes_fusion)),
        "texte_brut": texte,
    }, context={"registre": registre})
    supplement = lignes_ocr_pour_json(pages) if pages else None
    r = ranger_document(chemin, doc, alertes, registre, sortie, entree,
                        supplement_a_valider=supplement)
    return r, type_doc, cls.confiance


# --- 4. Le lot ------------------------------------------------------------------------
def traiter_lot(profil: dict = None, dossier_entree: Path = None, dossier_sortie: Path = None,
                dossier_data: Path = DOSSIER_DATA, client=None, moteur=None,
                registre: dict = None, afficher=print) -> BilanLot:
    """Traite tout Folder_Entree/. Ne leve pas d'exception pour un document :
    il part en A_Valider, ou reste en place (erreur) pour le prochain lancement."""
    debut = time.perf_counter()
    profil = profil or charger_profil()
    entree = Path(dossier_entree or profil["dossier_entree"])
    sortie = Path(dossier_sortie or profil["dossier_sortie"])
    dossier_data = Path(dossier_data)
    registre = registre or registre_par_defaut()
    config = charger_config()
    client = client or ClientOllama.depuis_profil(profil)
    moteur = moteur or MoteurPhi4(client=client)

    bilan = BilanLot()
    journal = Journal(dossier_data / "logs")
    bilan.journal = journal.chemin
    bilan.ocr_purges = nettoyer_ocr_anciens(dossier_data / "ocr")
    etat = EtatTraites(dossier_data / "etat" / "traites.json")
    journal.ecrire("debut", profil=profil["nom"], ocr_purges=bilan.ocr_purges)

    fichiers = lister_documents(entree)
    bilan.total = len(fichiers)
    n = len(fichiers)
    afficher(f"{n} document(s) dans le dossier d'entree (profil {profil['nom']})")

    # 0. Documents deja traites (lot interrompu, ou meme fichier depose deux fois)
    a_traiter, vus = [], set()
    for chemin in fichiers:
        cle = empreinte(chemin)
        if cle in etat or cle in vus:
            deplace, alerte = deplacer_vers_traites(chemin, entree)
            r = ResultatDocument(nom_affichable(chemin), "deja_traite",
                                 nb_alertes=0 if deplace else 1)
            bilan.compter(r)
            journal.ecrire("document", **r.__dict__)
            continue
        vus.add(cle)
        a_traiter.append((chemin, cle))

    # 1. Lecture
    extractions = {}
    for i, (chemin, cle) in enumerate(a_traiter, start=1):
        afficher(f"[{i}/{len(a_traiter)}] lecture")
        e = extraire(chemin)
        if e.illisible:
            r = envoyer_a_valider(chemin, "fichier illisible", alertes=e.alertes,
                                  registre=registre, dossier_sortie=sortie, dossier_entree=entree)
            _enregistrer(bilan, journal, etat, chemin, cle, r, None, None, 0.0)
            continue
        extractions[chemin] = (e, cle)

    if not extractions:
        return _terminer(bilan, journal, debut, afficher)

    # Le moteur doit etre disponible AVANT l'OCR (sinon l'OCR tournerait pour rien).
    # verifier() ne charge pas le modele : la RAM reste libre pour l'OCR.
    ok, raison = moteur.verifier()
    if not ok:
        bilan.alertes.append(f"moteur indisponible ({raison}) : documents laisses dans le "
                             "dossier d'entree")
        journal.ecrire("arret", raison="moteur indisponible")
        return _terminer(bilan, journal, debut, afficher)

    # 2. OCR (sur une machine modeste, Ollama doit etre vide)
    if not profil["ocr_et_llm_simultanes"] and client.modeles_charges():
        client.decharger()
        time.sleep(2)
        if client.modeles_charges():
            bilan.alertes.append("Ollama a un modele charge : lot arrete avant l'OCR "
                                 "(documents laisses dans le dossier d'entree)")
            journal.ecrire("arret", raison="modele charge dans Ollama")
            return _terminer(bilan, journal, debut, afficher)
    taches = [t for c, (e, _) in extractions.items() for t in taches_depuis_extraction(c, e)]
    bilan.pages_ocr = len(taches)
    t_ocr = time.perf_counter()
    if taches:
        afficher(f"OCR : {len(taches)} page(s)")
    lot = lancer_ocr(taches, dossier_travail=dossier_data / "ocr",
                     threads=profil["threads_ocr"],
                     seuil_ram_mo=profil["seuil_ram_worker_ocr_mo"],
                     progression=lambda f, t: afficher(f"  OCR [{f}/{t}]"))
    bilan.duree_ocr_s = time.perf_counter() - t_ocr
    bilan.pic_ram_ocr_mo = lot.pic_ram_mo
    bilan.alertes += lot.alertes
    journal.ecrire("ocr", pages=len(taches), duree_s=round(bilan.duree_ocr_s, 1),
                   pic_ram_mo=lot.pic_ram_mo, relances=lot.recyclages)

    # 3. Tous les appels LLM, modele garde charge, puis decharge
    t_llm = time.perf_counter()
    en_erreur = 0
    with moteur.lot():
        for i, (chemin, (e, cle)) in enumerate(extractions.items(), start=1):
            afficher(f"[{i}/{len(extractions)}] classement, extraction, rangement")
            t_doc = time.perf_counter()
            pages = lot.pages_du_fichier(chemin)
            try:
                r, type_doc, confiance = traiter_document(
                    chemin, e, pages, moteur, client, registre, config, entree, sortie)
            except Exception as err:                    # jamais d'arret du lot
                r = envoyer_a_valider(chemin, f"erreur de traitement ({type(err).__name__})",
                                      registre=registre, dossier_sortie=sortie,
                                      dossier_entree=entree,
                                      supplement=lignes_ocr_pour_json(pages) if pages else None)
                type_doc, confiance = None, None
            en_erreur += not r.ok
            _enregistrer(bilan, journal, etat, chemin, cle, r, type_doc, confiance,
                         time.perf_counter() - t_doc)
    bilan.duree_llm_s = time.perf_counter() - t_llm

    # 4. Verification du dechargement, suppression du dossier OCR du lot
    if not profil["ocr_et_llm_simultanes"]:
        time.sleep(1)
        if client.modeles_charges():
            bilan.alertes.append("le modele n'a pas ete decharge d'Ollama")
    if lot.dossier and en_erreur == 0:
        shutil.rmtree(lot.dossier, ignore_errors=True)   # lignes OCR deja copiees si besoin
    return _terminer(bilan, journal, debut, afficher)


def _enregistrer(bilan, journal, etat, chemin, cle, r, type_doc, confiance, duree):
    """Compte le resultat d'un document, le journalise et, s'il est depose, le note
    comme traite (il ne sera jamais retraite)."""
    if not r.ok:
        decision = "erreur"                          # l'original reste dans Folder_Entree
    else:
        decision = "a_valider" if r.a_valider else "range"
        etat.ajouter(cle, decision)
    destination = None
    if r.fichiers:
        destination = nom_affichable(r.fichiers[-1])
    res = ResultatDocument(nom_affichable(chemin), decision, type_doc,
                           round(confiance, 2) if confiance is not None else None,
                           destination, len(r.alertes), round(duree, 1))
    bilan.compter(res)
    journal.ecrire("document", **res.__dict__)


def _terminer(bilan, journal, debut, afficher):
    bilan.duree_s = time.perf_counter() - debut
    journal.ecrire("fin", total=bilan.total, ranges=bilan.ranges, a_valider=bilan.a_valider,
                   erreurs=bilan.erreurs, deja_traites=bilan.deja_traites,
                   duree_s=round(bilan.duree_s, 1), nb_alertes=len(bilan.alertes))
    return bilan
