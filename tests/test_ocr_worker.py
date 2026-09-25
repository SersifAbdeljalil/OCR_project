"""
test_ocr_worker.py - Tests de l'OCR en sous-process (src/ocr_worker.py).

Toutes les images sont INVENTEES et generees dans tmp_path.
Les tests "vrai OCR" partagent UN seul lot (le modele n'est charge qu'une fois).
Avant de lancer : Ollama sans modele charge (ollama ps).
Lancement (venv active, depuis OCR_PROJECT) :  python -m pytest -v
"""

import difflib

import pymupdf
import pytest

from src.extract_text import extraire
from src.ocr_worker import (STATUT_ERREUR, STATUT_OK, LigneOCR, PageOCR,
                            charger_page, fusionner_texte, lancer_ocr,
                            taches_depuis_extraction)

LIGNES = ["FACTURE N° FA-2026-00042", "Date : 15/09/2026",
          "Fournisseur : Société Exemple SARL", "Montant HT : 1 000,00 DH",
          "TVA 20 % : 200,00 DH", "Total TTC : 1 200,00 DH"]
ATTENDU = "\n".join(LIGNES)


# --- Fabrication d'images inventees ----------------------------------------
def page_texte(doc, angle=0, retourne=False):
    """Ajoute une page avec les lignes inventees, penchee de `angle` degres
    ou a l'envers (rotation 180)."""
    page = doc.new_page()
    for i, ligne in enumerate(LIGNES):
        point = pymupdf.Point(90, 120 + i * 30)
        if retourne:
            point = pymupdf.Point(500, 700 - i * 30)
            page.insert_text(point, ligne, fontsize=13, rotate=180)
        elif angle:
            page.insert_text(point, ligne, fontsize=13,
                             morph=(pymupdf.Point(90, 120), pymupdf.Matrix(angle)))
        else:
            page.insert_text(point, ligne, fontsize=13)
    return page


def image_png(chemin, **options):
    """PNG a 200 dpi d'une page inventee (comme un scan)."""
    doc = pymupdf.open()
    page = page_texte(doc, **options)
    page.get_pixmap(dpi=200).save(chemin)
    doc.close()
    return chemin


def image_vide(chemin):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1654, 2339), False)
    pix.clear_with(255)
    pix.save(chemin)
    return chemin


def pdf_mixte(chemin, png_scan):
    """Page 1 : texte natif ; page 2 : image seule (a OCR) ; page 3 : texte natif."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Page une native : " + ATTENDU.replace("\n", " "),
                               fontsize=8)
    doc.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), filename=str(png_scan))
    doc.new_page().insert_text((72, 72), "Page trois native : " + ATTENDU.replace("\n", " "),
                               fontsize=8)
    doc.save(chemin)
    doc.close()
    return chemin


def ressemblance(texte):
    return difflib.SequenceMatcher(None, ATTENDU, texte).ratio()


# --- Le lot commun (vrai PaddleOCR, lance une seule fois) ------------------
@pytest.fixture(scope="module")
def lot(tmp_path_factory):
    d = tmp_path_factory.mktemp("ocr")
    fichiers = {
        "droite": image_png(d / "droite.png"),
        "penchee": image_png(d / "penchée 5 degrés.png", angle=5),   # nom accentue
        "envers": image_png(d / "envers.png", retourne=True),
        "vide": image_vide(d / "vide.png"),
        "corrompue": d / "corrompue.jpg",
    }
    fichiers["corrompue"].write_bytes(b"pas une image")
    fichiers["pdf"] = pdf_mixte(d / "mixte.pdf", fichiers["droite"])
    taches = [{"fichier": str(fichiers[n]), "page": 1}
              for n in ("droite", "penchee", "envers", "vide", "corrompue")]
    extraction_pdf = extraire(fichiers["pdf"])
    taches += taches_depuis_extraction(fichiers["pdf"], extraction_pdf)
    resultat = lancer_ocr(taches, dossier_travail=d / "travail")
    return resultat, fichiers, extraction_pdf


def page_de(lot, nom, numero=1):
    resultat, fichiers, _ = lot
    return resultat.pages_du_fichier(fichiers[nom])[numero]


def test_lot_complet_sans_plantage(lot):
    resultat, _, _ = lot
    assert len(resultat.pages) == 6
    assert len(resultat.chargement_s) == 1           # modele charge UNE seule fois
    assert resultat.alertes == []
    assert 0 < resultat.pic_ram_mo < 2048            # critere : moins de 2 Go


def test_page_droite(lot):
    p = page_de(lot, "droite")
    assert p.statut == STATUT_OK and len(p.lignes) == len(LIGNES)
    assert ressemblance(p.texte) > 0.9
    assert p.dpi is None and p.largeur > 1000        # image lue telle quelle


def test_page_penchee(lot):
    p = page_de(lot, "penchee")
    assert p.statut == STATUT_OK and ressemblance(p.texte) > 0.8


def test_page_a_l_envers(lot):
    """use_angle_cls=True : une page retournee a 180 degres est quand meme lue."""
    p = page_de(lot, "envers")
    assert p.statut == STATUT_OK and ressemblance(p.texte) > 0.8
    assert p.orientation == 180
    assert p.lignes[0].texte.startswith("FACTURE")   # ordre de lecture retabli


def test_orientation_normale(lot):
    assert page_de(lot, "droite").orientation == 0
    assert page_de(lot, "penchee").orientation == 0


def test_page_vide(lot):
    p = page_de(lot, "vide")
    assert p.statut == STATUT_OK and p.lignes == [] and p.confiance_moyenne is None


def test_image_corrompue_ne_bloque_pas_le_lot(lot):
    p = page_de(lot, "corrompue")
    assert p.statut == STATUT_ERREUR and p.raison      # type d'erreur seulement
    assert page_de(lot, "pdf", 2).statut == STATUT_OK  # la page suivante est traitee


def test_lignes_avec_confiance_et_cadre(lot):
    p = page_de(lot, "droite")
    for ligne in p.lignes:
        assert isinstance(ligne, LigneOCR)
        assert 0 <= ligne.confiance <= 1
        assert len(ligne.cadre) == 4
        assert all(0 <= x <= p.largeur and 0 <= y <= p.hauteur for x, y in ligne.cadre)


def test_pdf_rendu_a_200_dpi_et_fusion_dans_l_ordre(lot):
    resultat, fichiers, extraction = lot
    assert extraction.pages_ocr == [2]
    p2 = page_de(lot, "pdf", 2)
    assert p2.dpi == 200 and p2.statut == STATUT_OK
    texte, alertes = fusionner_texte(extraction, resultat.pages_du_fichier(fichiers["pdf"]))
    assert alertes == []
    i1, i3 = texte.index("Page une native"), texte.index("Page trois native")
    i2 = texte.index("200,00 DH", i1 + 100)           # texte OCR de la page 2
    assert i1 < i2 < i3


def test_resultats_intermediaires_dans_le_dossier_de_travail(lot):
    resultat, _, _ = lot
    assert resultat.dossier.is_dir()
    assert (resultat.dossier / "resultats_1.jsonl").exists()


# --- Delais : une page trop lente ne fait pas planter le lot ---------------
def test_delai_depasse_page_suivante_quand_meme(tmp_path):
    a = image_png(tmp_path / "a.png")
    b = image_png(tmp_path / "b.png")
    taches = [{"fichier": str(a), "page": 1}, {"fichier": str(b), "page": 1}]
    r = lancer_ocr(taches, dossier_travail=tmp_path / "t", delai_page_s=0.01)
    assert len(r.pages) == 2
    assert all(p.statut == STATUT_ERREUR and "delai depasse" in p.raison
               for p in r.pages.values())
    assert len(r.chargement_s) == 2                  # worker relance pour la page b
    assert r.alertes == ["page 1 abandonnee : delai depasse (0.01 s)"] * 2


def test_chargement_trop_long(tmp_path):
    a = image_png(tmp_path / "a.png")
    r = lancer_ocr([{"fichier": str(a), "page": 1}], dossier_travail=tmp_path / "t",
                   delai_chargement_s=0.01)
    assert r.pages[(str(a), 1)].statut == STATUT_ERREUR
    assert r.alertes == ["OCR impossible : chargement OCR trop long (0.01 s)"]


def test_lot_vide():
    r = lancer_ocr([])
    assert r.pages == {} and r.dossier is None


# --- Fonctions sans OCR (rapides) ------------------------------------------
def test_charger_page_image_transparente_et_chemin_accentue(tmp_path):
    chemin = tmp_path / "reçu été.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 20), True)   # avec alpha
    pix.save(chemin)
    image, largeur, hauteur, dpi, reduction = charger_page(str(chemin), 1)
    assert image.shape == (20, 30, 3) and (largeur, hauteur, dpi) == (30, 20, None)
    assert reduction == 1.0


def test_grande_photo_reduite_proportionnellement(tmp_path):
    chemin = tmp_path / "photo.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4000, 3000), False)
    pix.clear_with(255)
    pix.save(chemin)
    image, largeur, hauteur, dpi, reduction = charger_page(str(chemin), 1)
    assert (largeur, hauteur) == (2500, 1875) and image.shape == (1875, 2500, 3)
    assert reduction == 0.625 and dpi is None


def test_pdf_grand_format_dpi_abaisse(tmp_path):
    """Page A2 (1191 x 1684 pt) : a 200 dpi, 4678 px ; limite a 2500 px."""
    doc = pymupdf.open()
    doc.new_page(width=1191, height=1684)
    doc.save(tmp_path / "a2.pdf")
    image, largeur, hauteur, dpi, reduction = charger_page(str(tmp_path / "a2.pdf"), 1)
    assert max(largeur, hauteur) <= 2500 and dpi < 200
    assert abs(largeur / hauteur - 1191 / 1684) < 0.01      # proportions gardees


def test_pdf_a4_inchange(tmp_path):
    doc = pymupdf.open()
    doc.new_page()                                          # A4
    doc.save(tmp_path / "a4.pdf")
    _, largeur, hauteur, dpi, reduction = charger_page(str(tmp_path / "a4.pdf"), 1)
    assert dpi == 200 and reduction == 1.0 and hauteur == 2339


def test_charger_page_hors_document(tmp_path):
    with pytest.raises(IndexError):
        charger_page(str(image_png(tmp_path / "a.png")), 2)


def test_fusion_page_ocr_en_erreur_signalee(tmp_path):
    png = image_png(tmp_path / "s.png")
    extraction = extraire(pdf_mixte(tmp_path / "m.pdf", png))
    pages = {2: PageOCR(fichier="m.pdf", page=2, statut=STATUT_ERREUR, raison="TypeErreur")}
    texte, alertes = fusionner_texte(extraction, pages)
    assert "Page une native" in texte and "Page trois native" in texte
    assert alertes == ["page 2 : OCR indisponible (TypeErreur)"]


def test_fusion_word_inchange():
    class ExtractionWord:
        texte, textes_pages, statut_pages = "texte Word", [], []
    assert fusionner_texte(ExtractionWord(), {}) == ("texte Word", [])


def test_taches_fichier_illisible_ignore(tmp_path):
    chemin = tmp_path / "x.pdf"
    chemin.write_bytes(b"abime")
    assert taches_depuis_extraction(chemin, extraire(chemin)) == []
