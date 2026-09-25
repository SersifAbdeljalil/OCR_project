# Projet : agent local de tri documentaire (cabinet comptable)

Ce fichier est lu automatiquement par Claude Code au démarrage. Il résume
tout ce qui a été décidé dans la conversation claude.ai précédente.

## Règles de collaboration (à respecter strictement)
1. Réponds en français, de façon pédagogique : l'utilisateur apprend.
2. Une seule étape à la fois. Après chaque étape : arrêt, attente de validation.
3. Poser des questions avant de coder si quelque chose n'est pas clair. Ne jamais deviner.
4. Écrire les fichiers directement dans le projet (pas de gros blocs de code dans le chat).
5. Vérifier que chaque fichier fonctionne (le lancer) avant de passer au suivant.
6. Expliquer ce que fait chaque bloc de code ; commenter le code en français.
7. Ne pas parler de Docker, WSL, Linux, GPU, ni de Sage 100 (hors périmètre).
8. Ne jamais lire, afficher ni résumer le contenu des documents réels ou des fichiers de sortie. Les scripts de test n'affichent que des métriques et des noms de fichiers.
## Objectif
Documents en vrac (PDF, DOCX, images) -> lire, classer, extraire les champs,
ranger en `.txt` + `.json` dans des sous-dossiers. Validation humaine dans Streamlit.
MVP pragmatique, 100 % LOCAL, budget 0 €.

## Machine (contraintes fortes)
- Windows 64-bit, Intel i3-1005G1 (2 cœurs), 8 Go RAM, pas de GPU utilisable.
- Python 3.11.9 dans `.venv` (Python 3.14 existe aussi sur le PC : toujours utiliser le .venv).
  Activation : `.\.venv\Scripts\Activate.ps1` (ExecutionPolicy RemoteSigned déjà réglée).
- Ollama 0.34.4 installé, modèle `phi4-mini` (3.8B, Q4_K_M) téléchargé.
  Mesuré : 16 s pour une réponse courte (chargement inclus), 3,1 Go RAM (ctx 4096), 100 % CPU.
- Piège connu : le terminal d'Antigravity ne trouve pas `ollama` (PATH).
  Contournement : `Set-Alias ollama "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"`.
  Les scripts Python n'en ont pas besoin : ils appellent http://localhost:11434.
- PaddleOCR et Ollama ne doivent JAMAIS tourner en même temps (RAM).

## Architecture validée
1. Extraction texte selon le format :
   - PDF natif : PyMuPDF ; PDF scanné et images : PaddleOCR 2.x
     (PaddleOCR lancé dans un sous-process pour libérer la RAM à la fin) ;
   - DOCX et DOTX : python-docx ; XLS : xlrd ; XLSX : openpyxl.
   A_Valider/ uniquement pour les fichiers illisibles (corrompus, protégés, format inconnu).
   L'agent ne plante jamais sur un document : toute erreur de lecture ou de traitement
   envoie le document dans A_Valider/ avec la raison, et le traitement continue avec
   le document suivant.
2. Données sensibles (décision du 2026-09-25, remplace l'ancien masquage) : on extrait TOUT,
   y compris cin, rib, iban, date_naissance, adresse. Plus aucun masquage dans les fichiers
   de sortie (.txt / .json). masking.py sert UNIQUEMENT aux affichages (terminal, logs,
   rapports de test) : aucun script n'affiche jamais une valeur sensible, seulement
   « trouvé / absent ».
3. Classification LOCALE : regex d'abord, Phi-4-mini seulement si les regex hésitent.
   JEV / OpenRouter ABANDONNÉ pour le MVP (pas de crédits, loi 09-08 / CNDP). Possible en v2.
4. Extraction des champs : Phi-4-mini, prompt par type, sortie JSON schema.
   Réglages Ollama : num_ctx=2048, num_gpu=0, keep_alive=0, temperature=0.
5. Normalisation déterministe Python (pas de LLM) : dates ISO, montants, HT+TVA=TTC.
6. Rangement + doublons (_1, _2). Original copié vers Folder_Sortie, puis déplacé
   vers Folder_Entree/Traites/.
   Nommage (décision du 2026-09-25) : modèle « nom_fichier » par catégorie dans le registre
   (préfixe + parties ; minuscules, sans accents, sans espaces, parties séparées par _,
   partie absente omise) :
     factures     : facture_<fournisseur>_<date_facture>_<numero>
     diplomes     : diplome_<sous-dossier>_<titulaire>
     attestations : attestation_<sous-dossier>_<beneficiaire>
     contrats     : contrat_<objet>_<date_signature>
     banque       : banque_<banque>_<periode>
     découverte / autres : <categorie>_<titre>_<personne>
   L'original, le .txt et le .json partagent le même nom de base (même suffixe _1).
   A_Valider/ : nom d'origine nettoyé + <nom>.json qui donne la raison.
   Folder_Entree/ et Folder_Sortie/ : à la racine du projet (constantes de config.py,
   passées en paramètre à filer.py).
7. Interface Streamlit : image + tableau éditable, pour les documents de A_Valider/.
8. Dossiers de sortie : les dossiers de sortie ne sont jamais créés à l'avance. L'agent
   (filer.py) les crée à la demande, quand le premier document d'un type arrive, et
   uniquement avec les noms exacts du registre config/categories.json (point 9).
   Un type absent du registre ne crée jamais de dossier tout seul : il passe par
   A_Valider/ et la validation humaine.
9. Catégories découvertes (décision du 2026-09-25, remplace la « liste fixe ») :
   - Registre config/categories.json (versionné dans git, lisible par Claude), initialisé avec :
     factures, diplomes (DEUG, Licence, Master, Doctorat, Autres),
     attestations (Travail, Scolarite, Autres), contrats, banque.
   - Chaque catégorie du registre a une liste de mots-clés (motifs regex). Les catégories
     de départ reçoivent les regex actuelles de test_classification.py.
   - Règle stricte : un mot-clé ne contient JAMAIS de nom de personne ni de numéro.
   - L'agent consulte le registre avant chaque rangement.
   - Type connu : rangement normal (règle de confiance A).
   - Type nouveau : Phi-4-mini propose un nom ; le code le normalise (minuscules,
     sans accents) puis le compare aux catégories existantes (similarité) pour éviter
     les doublons. S'il est vraiment nouveau : A_Valider/ avec la proposition.
     L'humain décide une seule fois dans Streamlit (créer / ranger dans Autres /
     renommer). À ce moment, Phi-4-mini propose 3 à 5 mots-clés, l'humain les confirme
     ou les corrige, puis la catégorie ET ses mots-clés sont ajoutés au registre.
   - La règle de confiance A s'applique ensuite à toutes les catégories de la même façon.
   - Champs génériques d'une catégorie découverte : titre, personne, organisme, date.
10. Évaluation : pas d'attendus rempli à l'avance. L'agent traite le jeu de test, puis
    l'utilisateur marque chaque résultat « correct » ou « faux » dans un fichier de revue.
    On calcule ensuite le taux de réussite.

## Règle de confiance (décision A, validée)
| Situation | Confiance |
|---|---|
| Regex nettes (1 seul type, >= 2 indices) | 0.95, sans LLM |
| Regex et Phi-4-mini d'accord | 0.90 |
| Désaccord, égalité ou aucun indice regex | 0.60 |
Confiance >= 0.90 -> rangement automatique, sinon -> A_Valider/.
On ne demande JAMAIS au LLM son propre chiffre de confiance (non calibré).

## Catégories et champs
- Factures/ : fournisseur, date_facture, numero, montant_ht, tva, montant_ttc
- Diplomes/ (DEUG, Licence, Master, Doctorat, Autres) : titulaire, intitule, etablissement,
  date_obtention, mention. Une « attestation de réussite » d'un diplôme va dans Diplomes/.
- Attestations/ (Travail, Scolarite, Autres) : emetteur, beneficiaire, objet, date
- Contrats/ : parties, objet, date_signature, duree
- Banque/ : banque, titulaire, periode, objet, rib, iban
- Catégorie découverte (validée par l'humain) : titre, personne, organisme, date
- A_Valider/ : confiance < 0.90, type nouveau à valider, texte arabe, scan groupé, illisible
- Autres/ : ce qui ne rentre nulle part
- Données sensibles EXTRAITES quand elles sont présentes : cin, rib, iban, date_naissance,
  adresse (voir architecture, point 2). Jamais affichées par les scripts.
- Français uniquement (arabe en v2 -> A_Valider/). Un fichier = un document.
- Volume : 20-30 documents/jour.

## Format JSON de sortie (schéma unique)
{"type", "source", "date_traitement", "confiance_classification",
 "champs": {...selon le type...}, "necessite_validation_humaine", "texte_brut"}

## État d'avancement
- FAIT : Python 3.11 + .venv, requests + python-dotenv installés.
- FAIT : Ollama + phi4-mini installés et testés (voir mesures ci-dessus).
- FAIT : `test_jev.py` (JEV via OpenRouter) : fonctionne jusqu'à l'erreur 402
  (compte sans crédits). Mis de côté pour la v2. `.env` contient OPENROUTER_API_KEY :
  ne jamais l'afficher ni le lire à voix haute.
- FAIT : `test_classification.py` (regex + règle métier + Phi-4-mini + règle de confiance),
  lancé avec le vrai Ollama.
  - Mode normal : 5 rangés, 1 en A_Valider (recu_paiement), 0 mal rangé, 22,8 s.
  - Mode `--forcer-llm` : 4 rangés, 2 en A_Valider, 0 mal rangé, ~19 s/document
    (dont 6-7 s de chargement à cause de keep_alive=0).
    Erreurs de Phi-4-mini : attestation_reussite_deug -> « attestation » (attendu diplome),
    recu_paiement -> « facture » (attendu autre). La règle de confiance les a bloquées.
  - Règle métier regex : « attestation de réussite » + nom de diplôme -> diplome (0.95, sans LLM).
- FAIT : `.claude/settings.json` interdit à Claude Code la lecture de `.env`,
  `tests/docs_test/`, `Folder_Entree/`, `Folder_Sortie/` et `data/`.
- FAIT : dossier `tests/docs_test/` créé (documents d'exemple, ignoré par git).
  Les dossiers de sortie ne sont PAS créés (voir architecture, point 8).
- FAIT : PyMuPDF 1.28.2 installé dans le .venv (utiliser `import pymupdf`, `fitz` est obsolète).
- FAIT : `tests/inventaire_docs_test.py` (étape a) : noms, formats, taille, pages, natif/scan
  (seuil : 50 caractères visibles par page, jamais affichés). Résultat du jeu de test :
  17 fichiers (13 .pdf, 2 .xls, 1 .dotx, 1 .jpg) ; PDF : 4 natifs, 9 scans, 0 mixte,
  0 illisible ; 12 pages à passer en OCR. Aucun .docx ni .xlsx dans le jeu de test.
- FAIT (étape b1) : pydantic 2.13.5 + pytest 9.1.1 installés. `pytest.ini` (seuls test_*.py,
  docs_test/ jamais exploré). Lancer les tests : `python -m pytest -v`.
  - `config/categories.json` : registre initial (5 catégories, dossier, sous-dossiers,
    mots-clés repris de test_classification.py + banque, champs, règle métier).
    Clés globales : champs_generiques, champs_sensibles (cin, rib, iban, date_naissance,
    adresse : ajoutés aux champs de TOUTES les catégories).
  - `src/config.py` : charge et vérifie le registre (noms normalisés, pas de doublons,
    dossiers comparés sans tenir compte de la casse, regex valides, pas de chiffre en clair
    ni de nombre en lettres dans les mots-clés ; \d et {24} autorisés), liste toutes les
    erreurs d'un coup. Constantes : SEUIL_CONFIANCE, A_Valider, Autres (noms réservés).
  - `src/schemas.py` : modèle Pydantic `DocumentSortie` (schéma unique). Champs autorisés
    selon la catégorie (génériques si type découvert), champs absents -> None,
    confiance < 0.90 impose necessite_validation_humaine=true. Les erreurs ne recopient
    jamais les valeurs, et repr() masque champs et texte_brut.
  - Limite : le contrôle « pas de nom de personne » dans les mots-clés ne peut pas être
    automatique ; il reste une vérification humaine.
  - Complément b1 (sous-dossiers) : chaque sous-dossier a ses mots-clés
    ({"nom", "mots_cles"}). Autres est obligatoire dès qu'il y a des sous-dossiers et n'a
    pas de mots-clés (choix par défaut). `choisir_sous_dossier(categorie, texte)` dans
    src/config.py, SANS LLM : exactement un reconnu -> ce sous-dossier ; aucun ou
    plusieurs -> Autres ; catégorie sans sous-dossiers -> None. 58 tests OK.
- FAIT (étape b2) : `src/masking.py`, pour l'AFFICHAGE uniquement (sorties jamais masquées).
  Principe : masquer trop plutôt que pas assez.
  - `masquer_texte` : email, IBAN (MA + chiffres), téléphone (05/06/07, +212, 00212),
    RIB (24 chiffres, espaces permis), 8 chiffres ou plus, CIN (1-2 lettres + 5 chiffres
    ou plus ; avec espace seulement en majuscules, pour ne pas masquer « de 15000 »).
    Les dates (15/09/2026, 2026-09-25) et montants (1 000,00 DH) restent lisibles.
  - `resume_champs` : champs sensibles du registre -> « trouvé » / « absent » ; autres
    champs -> masquer_texte (vide -> « absent »).
  - `nom_affichable` : garde les dossiers connus (registre + dossiers techniques) et les
    mots connus du registre dans le nom de fichier (+ n° de doublon _1, _2) ; tout le
    reste devient *** (ex. C:/***/Folder_Entree/***.pdf).
  - `tests/test_masking.py` : 31 tests (89 au total).
  - Correction : tout ce qui suit CIN / C.I.N / CNIE / carte nationale (casse ignorée,
    avec ou sans « : » / « n° ») est toujours masqué ; le mot-clé reste visible.
  - Question H résolue : champs de PERSONNES (titulaire, beneficiaire, personne, parties)
    -> seulement « trouvé » / « absent » (CHAMPS_PERSONNES dans masking.py). Champs
    d'ORGANISMES (fournisseur, banque, etablissement, emetteur, organisme) -> affichés
    via masquer_texte.
- FAIT (étape b3) : `src/normalize.py`, Python pur sans LLM. Chaque fonction renvoie
  (valeur, alertes) ; les alertes ne recopient jamais la valeur analysée.
  - `normaliser_date` -> 'AAAA-MM-JJ' ou None. Formats : 15/09/2026, 15-09-2026,
    15.09.2026, 2026-09-15, 15 septembre 2026, 15 sept. 2026, 1er octobre 2026,
    15/09/26. Toujours jour/mois. Année sur 2 chiffres : 20xx sauf si > année courante + 1
    (alors 19xx), avec alerte. Date impossible, plusieurs dates, année < 1900 -> None.
  - `normaliser_montant` -> Decimal ou None. Devise (DH, MAD, Dhs, €...) retirée seulement
    au début ou à la fin ; espace permis seulement comme séparateur de milliers ;
    avec virgule ET point, le dernier est le décimal ; un séparateur unique suivi
    d'exactement 3 chiffres (1.234 / 1,234) est AMBIGU -> None.
  - `verifier_totaux(ht, tva, ttc)` -> (ControleTotaux(ok, ecart, necessite_validation_humaine),
    alertes). TVA : un montant ou une liste (additionnée). Tolérance 0,01. Écart -> alerte +
    validation humaine. Montants manquants (décision du 2026-09-25) : HT ou TTC manquant
    ou illisible -> validation humaine ; TVA manquante et HT = TTC -> ok + alerte
    « facture sans TVA » ; TVA manquante et HT différent du TTC -> validation humaine ;
    ligne de TVA illisible -> validation humaine.
  - `tests/test_normalize.py` : 63 tests à l'origine.
  - Decimal : schemas.py accepte Decimal ; `DocumentSortie.vers_json()` écrit les montants
    comme NOMBRES à 2 décimales (240.50), arrondi au centime supérieur à partir de 0,005.
- FAIT (étape b4) : `src/filer.py` + `nom_fichier` dans le registre (vérifié par config.py).
  - `ranger_document(original, doc, alertes)` : A_Valider/ si necessite_validation_humaine
    ou catégorie absente du registre ; type « autres » -> Folder_Sortie/Autres/ ;
    sinon catégorie + sous-dossier (choisir_sous_dossier). Dossiers créés seulement si absents.
  - `envoyer_a_valider(original, raison, doc=None)` : aussi pour les fichiers illisibles.
    Dans A_Valider/ (décision du 2026-09-25) : l'original + le .txt + UN SEUL .json complet
    (même schéma que les documents rangés, plus raison, alertes, categorie_proposee).
    Fichier illisible (doc=None) : pas de .txt, .json aux mêmes clés mais vides.
  - Les tirets sont gardés dans les noms (fa-2026-00042, 2026-09-15) : validé.
  - Noms : a-z 0-9 - seulement (aucun caractère interdit Windows), noms réservés
    (CON, NUL, COM1...) suffixés « _doc », chemin complet <= 259 caractères (troncature
    en gardant la place de « _999 »), doublons _1, _2 comparés en minuscules, y compris
    dans Traites/. Original .json/.txt : copie nommée <base>_original.<ext>.
  - Sécurité de l'original : écrire .txt/.json -> copier -> vérifier (taille + SHA-256)
    -> seulement alors déplacer vers Traites/. Toute erreur avant la vérification :
    fichiers produits retirés, original intact, alerte. Déplacement raté : fichiers rangés
    gardés, original laissé dans Folder_Entree, alerte. Les alertes ne contiennent que le
    type d'erreur (jamais le message système, qui contient des chemins).
  - `tests/test_filer.py` : 38 tests dans tmp_path (221 au total).
- FAIT (étape b5) : `src/extract_text.py` (sans OCR). python-docx 1.2.0, xlrd 2.0.2,
  openpyxl 3.1.5 installés. SEUIL_CARACTERES_PAGE (50) est dans config.py, partagé
  avec l'inventaire.
  - `extraire(chemin)` -> ResultatExtraction(texte, nb_pages, pages_ocr, statut_pages,
    methode, illisible, alertes). Ne lève JAMAIS d'exception ; illisible -> alerte
    « illisible : <raison> » (type d'erreur seulement, jamais de chemin ni de texte).
  - PDF : page par page, « texte » ou « OCR requis » ; PDF mixte -> texte des pages
    natives + liste des pages à OCR. Protégé / corrompu / 0 page -> illisible.
  - Images : aucune extraction, toutes les pages « OCR requis » ; chaque page est rendue
    à 10 % pour détecter une image corrompue (PyMuPDF ouvre un faux .jpg sans erreur).
  - DOCX/DOTX : en-têtes, corps (paragraphes + tableaux dans l'ordre, cellules
    « | »), zones de texte, pieds de page. DOTX : copie EN MÉMOIRE avec le type
    « document » (méthode « python-docx (modele) ») ; dernier recours : XML brut.
  - XLS/XLSX : toutes les feuilles (« [Feuille : nom] »), cellules non vides ligne par
    ligne, dates -> AAAA-MM-JJ, 1200.0 -> 1200. nb_pages = nombre de feuilles.
  - Document lisible mais vide -> alerte « aucun texte extrait ».
  - `tests/test_extract_text.py` : 23 tests sur fichiers générés dans tmp_path (245 au
    total). Pas de test unitaire .xls (impossible d'en générer sans bibliothèque de plus) :
    couvert par le jeu de test réel.
  - `tests/verifier_extraction.py` sur le jeu de test : 17 fichiers, 0 illisible,
    7125 caractères extraits, 12 pages à OCR (cohérent avec l'inventaire), 0,4 s.
    .xls lus par xlrd, .dotx par « python-docx (modele) ».
- FAIT (étape b6a) : faisabilité PaddleOCR (pas encore de module).
  - Installé : paddlepaddle 2.6.2 (CPU, DERNIÈRE 2.x disponible pour Python 3.11 Windows)
    + paddleocr 2.10.0 (DERNIÈRE 2.x ; branche plus maintenue, le projet est en 3.x).
    Épinglages OBLIGATOIRES : numpy<2 (paddle 2.6.2 compilé pour numpy 1.x ; sinon pip
    prend numpy 2.4) et opencv-python / opencv-contrib-python / opencv-python-headless
    <4.11 (les 3 alignés en 4.10.0.84 : ils partagent le module cv2). PyMuPDF 1.28.2
    conservé. `pip check` OK, 245 tests toujours OK.
  - `requirements.txt` (pip freeze, 60 paquets) : réinstallation à l'identique avec
    `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.
  - `tests/essai_ocr.py` (facture INVENTÉE rendue à 200 dpi, 1653x2339 px, lang="fr",
    CPU, OMP_NUM_THREADS=2, cpu_threads=2), Ollama sans modèle chargé :
    chargement 3,8 s (modèles déjà téléchargés), OCR 1,9 s/page, pic RAM 641 Mo,
    9/9 lignes, confiance moyenne 0,982, ressemblance 98,7 %.
    Erreurs : « FACTURE N° FA- » lu « EACTURE N° EA- » (F -> E sur le titre en gros
    caractères), « Arrêtée » -> « Arrétée », « à » -> « ä ».
    => Attention : la regex \bfacture\b ne reconnaîtrait pas « EACTURE », et le numéro
    de facture est faux : l'OCR impose la prudence (validation humaine).
  - Modèles (téléchargés au 1er lancement, ~1 min) : C:\Users\<utilisateur>\.paddleocr\whl\,
    15,6 Mo au total : det en_PP-OCRv3 (3,8 Mo), rec latin_PP-OCRv3 (9,7 Mo),
    cls ch_ppocr_mobile_v2.0 (2,1 Mo).
  - Avertissement paddle « OMP_NUM_THREADS set to 2, not 1 » sans conséquence (build MKL,
    pas OpenBlas) : l'OCR fonctionne.
  - Plan B si problème plus tard : RapidOCR (mêmes modèles, onnxruntime). PAS PaddleOCR 3.x.
- FAIT (étape b6b) : `src/ocr_worker.py`.
  - PARENT `lancer_ocr(taches)` : taches = [{"fichier", "page"}] (via
    `taches_depuis_extraction(chemin, extraction)`). Lance `python -m src.ocr_worker` dans
    un SOUS-PROCESS (RAM rendue à Windows à la fin), UN lot = modèle chargé une fois.
    Surveille l'avancement ligne par ligne (JSONL) : page > 60 s ou worker planté -> page
    en erreur + alerte « page N abandonnee : ... », worker relancé pour les pages
    suivantes ; chargement > 180 s -> toutes les pages en erreur « OCR impossible ».
  - WORKER : PDF rendu à 200 dpi (PyMuPDF), image lue telle quelle (dpi=None), via
    PyMuPDF (OpenCV plante sur les chemins accentués) ; lang="fr", CPU, 2 threads,
    use_angle_cls=True. Une page qui plante -> statut « erreur » + type d'erreur, page suivante.
  - Par ligne : texte, confiance, cadre (4 points en pixels de l'image analysée) ; par page :
    largeur, hauteur, dpi, durée, pic RAM, orientation.
  - À GARDER pour la future règle de validation des champs : la CONFIANCE PAR LIGNE
    (et le cadre pour l'écran Streamlit).
  - Page À L'ENVERS : PaddleOCR retourne chaque ligne mais les rend dans l'ordre inverse.
    Correctif : `_EspionAngles` enveloppe l'attribut interne `text_classifier` (PaddleOCR
    2.10) pour lire l'angle de chaque ligne ; majorité à 180° -> orientation=180 et lignes
    remises dans l'ordre de lecture. Dépend d'un attribut interne : à revérifier si on
    change de version.
  - `fusionner_texte(extraction, pages_ocr)` : texte natif + texte OCR dans l'ordre des
    pages (extract_text.py garde maintenant `textes_pages`) ; page OCR ratée -> alerte.
  - Résultats intermédiaires : data/ocr/<horodatage>/ (taches, resultats JSONL, journal
    du worker), jamais affichés, jamais lus par Claude.
  - DÉCISION data/ocr/ (2026-09-25, à appliquer dans pipeline.py) : résultat OCR supprimé
    dès que le document est rangé ; pour A_Valider/, les lignes OCR (texte, confiance,
    cadre) sont copiées dans son .json puis supprimées ; au lancement, tout ce qui a plus
    de 7 jours dans data/ocr/ est supprimé.
  - `tests/test_ocr_worker.py` : 18 tests (263 au total, ~24 s) : page droite, penchée
    5°, à l'envers, vide, image corrompue, PDF mixte + fusion, délai dépassé, chargement
    trop long.
  - `tests/verifier_ocr.py` sur le jeu de test (12 pages, Ollama sans modèle chargé) :
    0 erreur, chargement 5,6 s, lot 51,2 s (0,9 à 7,4 s par page), 392 lignes,
    confiance moyenne 0,870, 137 lignes sous 0,90, pic RAM du worker 1582 Mo (< 2 Go,
    mais bien plus que les 641 Mo de l'essai : vrais scans plus grands).
    Confiance faible sur bac-1-1, bac-2-1, bac (0,72 à 0,80) et demand eljadida (0,756) ;
    cause non vérifiable sans lire le contenu (hypothèse : texte arabe ou tampons).
- FAIT (étape b7) : `src/rules.py` (déterministe, sans LLM, TOUT vient du registre).
  - `analyser(texte, texte_natif=, confiances_ocr=)` -> VerdictRegles(categorie, verdict,
    scores, regle_metier, sous_dossier, signaux). Verdicts : « regle metier » (prioritaire),
    « net » (>= 2 indices, un seul type), « faible », « egalite », « aucun indice ».
    VERDICTS_SURS = net + regle metier (0.95 dans la règle A).
  - Signaux de qualité renvoyés SANS décider (classifier.py appliquera la règle A) :
    part_arabe (lettres arabes / lettres, texte NATIF), lignes_ocr, confiance_ocr_moyenne,
    lignes_ocr_sous_seuil (SEUIL_CONFIANCE_LIGNE_OCR = 0,90 dans config.py).
  - Mots-clés NON modifiés pour tolérer l'OCR (décision reportée après résultats réels).
  - `tests/test_rules.py` : 33 tests (296 au total) : les 6 documents de
    test_classification.py (mêmes scores), un cas par catégorie (banque compris),
    faible / égalité, règles lues dans le registre, signaux.
  - `tests/verifier_rules.py` sur le jeu de test (17 fichiers) : net 2, règle métier 1,
    faible 8, égalité 2, aucun indice 4. Donc seulement 3/17 rangés sans LLM.
    Constats (métriques et noms de fichiers seulement) :
    * les 4 modèles de facture (pdf, 2 xls, dotx) : factures 3-4 MAIS banque:1 -> « faible »
      (un mot-clé banque — rib, iban, agence ou solde — apparaît sur les factures) ;
    * « damand a monsieur le doyen.pdf » -> diplomes « net » (0.95 sans LLM) alors que le
      nom évoque une demande et non un diplôme : RISQUE de mauvais rangement automatique ;
    * deug.pdf -> diplomes par règle métier, mais sous-dossier Autres (aucun ou plusieurs
      mots-clés de sous-dossier trouvés) ;
    * « RIB CDG.pdf » -> attestations:2 banque:1 -> attestations « faible » ;
    * bac, bac-1-1 : égalité diplomes 2 / attestations 2 ; bac-2-1 : aucun indice
      (confiance OCR 0,72) ;
    * CV (3 fichiers) -> diplomes « faible » (une mention de diplôme dans un CV) ;
    * « contrat de travail » -> attestations:1 via le mot-clé « de travail » : jamais « net »
      (faiblesse connue du registre, testée) ;
    * part_arabe = 0 % partout : le signal ne voit que le texte NATIF ; sur un scan, le
      modèle OCR latin ne produit pas de lettres arabes -> la confiance OCR basse est le
      seul indice pour les scans bilingues.
  - Piège Windows : ne jamais réécrire un fichier avec Get-Content/Set-Content de
    PowerShell 5.1 (il relit l'UTF-8 comme de l'ANSI et casse les accents).
- FAIT : blocage vérifié : l'outil Read de Claude Code refuse tests/docs_test/essai_blocage.txt.
  Limite : la règle « deny Read » vise l'outil Read, pas les commandes shell
  (Get-Content, cat...). Claude ne doit donc jamais utiliser le shell pour lire ces dossiers.

## Questions encore ouvertes (à poser avant l'étape concernée)
- B : RÉSOLUE (2026-09-25) : Diplomes/ a un sous-dossier Autres/ (Bac, BTS, DUT...).
- C : SANS OBJET (2026-09-25) : plus de masquage dans les sorties (architecture, point 2).
- D : RÉSOLUE : champs Banque/ = banque, titulaire, periode, objet, rib, iban.
- E : RÉSOLUE : mots-clés par catégorie dans le registre (architecture, point 9).
- F : RÉSOLUE : registre dans config/categories.json (lisible, versionné) ;
  data/ reste interdit en lecture (logs, sorties).
- G : RÉSOLUE : l'agent ne plante jamais (architecture, point 1).
- H : RÉSOLUE : champs de personnes -> « trouvé / absent » à l'affichage.

## Prochaines étapes (une à la fois)
0. FAIT : lancer `python test_classification.py` puis `--forcer-llm` ; analyser temps et erreurs.
a) FAIT : Inventaire du jeu de test : script tests/inventaire_docs_test.py (noms, formats, pages,
   natif ou scan), sans jamais afficher le contenu.
b) Découpage en modules, UN MODULE (ou une petite paire) PAR ÉTAPE, avec son test :
   b1) FAIT : config/categories.json + src/config.py + src/schemas.py.
   b2) FAIT : src/masking.py.
   b3) FAIT : src/normalize.py.
   b4) FAIT : src/filer.py.
   b5) FAIT : src/extract_text.py (sans OCR).
   b6a) FAIT : faisabilité PaddleOCR (tests/essai_ocr.py) + requirements.txt.
   b6b) FAIT : src/ocr_worker.py.
   b7) FAIT : src/rules.py.
   Suite : src/llm.py, src/classifier.py, src/extractor.py, src/pipeline.py,
   app/streamlit_app.py, avec un test pour chacun (dossier tests/).
