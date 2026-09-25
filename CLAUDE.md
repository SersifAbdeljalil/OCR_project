# Projet : agent local de tri documentaire (cabinet comptable)

Ce fichier est lu automatiquement par Claude Code au dÃ©marrage. Il rÃ©sume
tout ce qui a Ã©tÃ© dÃ©cidÃ© dans la conversation claude.ai prÃ©cÃ©dente.

## RÃ¨gles de collaboration (Ã  respecter strictement)
1. RÃ©ponds en franÃ§ais, de faÃ§on pÃ©dagogique : l'utilisateur apprend.
2. Une seule Ã©tape Ã  la fois. AprÃ¨s chaque Ã©tape : arrÃªt, attente de validation.
3. Poser des questions avant de coder si quelque chose n'est pas clair. Ne jamais deviner.
4. Ã‰crire les fichiers directement dans le projet (pas de gros blocs de code dans le chat).
5. VÃ©rifier que chaque fichier fonctionne (le lancer) avant de passer au suivant.
6. Expliquer ce que fait chaque bloc de code ; commenter le code en franÃ§ais.
7. Ne pas parler de Docker, WSL, Linux, GPU, ni de Sage 100 (hors pÃ©rimÃ¨tre).
8. Ne jamais lire, afficher ni rÃ©sumer le contenu des documents rÃ©els ou des fichiers de sortie. Les scripts de test n'affichent que des mÃ©triques et des noms de fichiers.
## Objectif
Documents en vrac (PDF, DOCX, images) -> lire, classer, extraire les champs,
ranger en `.txt` + `.json` dans des sous-dossiers. Validation humaine dans Streamlit.
MVP pragmatique, 100 % LOCAL, budget 0 â‚¬.

## Machine (contraintes fortes)
- Windows 64-bit, Intel i3-1005G1 (2 cÅ“urs), 8 Go RAM, pas de GPU utilisable.
- Python 3.11.9 dans `.venv` (Python 3.14 existe aussi sur le PC : toujours utiliser le .venv).
  Activation : `.\.venv\Scripts\Activate.ps1` (ExecutionPolicy RemoteSigned dÃ©jÃ  rÃ©glÃ©e).
- Ollama 0.34.4 installÃ©, modÃ¨le `phi4-mini` (3.8B, Q4_K_M) tÃ©lÃ©chargÃ©.
  MesurÃ© : 16 s pour une rÃ©ponse courte (chargement inclus), 3,1 Go RAM (ctx 4096), 100 % CPU.
- PiÃ¨ge connu : le terminal d'Antigravity ne trouve pas `ollama` (PATH).
  Contournement : `Set-Alias ollama "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"`.
  Les scripts Python n'en ont pas besoin : ils appellent http://localhost:11434.
- PaddleOCR et Ollama ne doivent JAMAIS tourner en mÃªme temps (RAM).

## Architecture validÃ©e
1. Extraction texte selon le format :
   - PDF natif : PyMuPDF ; PDF scannÃ© et images : PaddleOCR 2.x
     (PaddleOCR lancÃ© dans un sous-process pour libÃ©rer la RAM Ã  la fin) ;
   - DOCX et DOTX : python-docx ; XLS : xlrd ; XLSX : openpyxl.
   A_Valider/ uniquement pour les fichiers illisibles (corrompus, protÃ©gÃ©s, format inconnu).
   L'agent ne plante jamais sur un document : toute erreur de lecture ou de traitement
   envoie le document dans A_Valider/ avec la raison, et le traitement continue avec
   le document suivant.
2. DonnÃ©es sensibles (dÃ©cision du 2026-09-25, remplace l'ancien masquage) : on extrait TOUT,
   y compris cin, rib, iban, date_naissance, adresse. Plus aucun masquage dans les fichiers
   de sortie (.txt / .json). masking.py sert UNIQUEMENT aux affichages (terminal, logs,
   rapports de test) : aucun script n'affiche jamais une valeur sensible, seulement
   Â« trouvÃ© / absent Â».
3. Classification LOCALE : regex d'abord, Phi-4-mini seulement si les regex hÃ©sitent.
   JEV / OpenRouter ABANDONNÃ‰ pour le MVP (pas de crÃ©dits, loi 09-08 / CNDP). Possible en v2.
4. Extraction des champs : Phi-4-mini, prompt par type, sortie JSON schema.
   RÃ©glages Ollama : num_ctx=2048, num_gpu=0, keep_alive=0, temperature=0.
5. Normalisation dÃ©terministe Python (pas de LLM) : dates ISO, montants, HT+TVA=TTC.
6. Rangement + doublons (_1, _2). Original copiÃ© vers Folder_Sortie, puis dÃ©placÃ©
   vers Folder_Entree/Traites/.
7. Interface Streamlit : image + tableau Ã©ditable, pour les documents de A_Valider/.
8. Dossiers de sortie : les dossiers de sortie ne sont jamais crÃ©Ã©s Ã  l'avance. L'agent
   (filer.py) les crÃ©e Ã  la demande, quand le premier document d'un type arrive, et
   uniquement avec les noms exacts du registre config/categories.json (point 9).
   Un type absent du registre ne crÃ©e jamais de dossier tout seul : il passe par
   A_Valider/ et la validation humaine.
9. CatÃ©gories dÃ©couvertes (dÃ©cision du 2026-09-25, remplace la Â« liste fixe Â») :
   - Registre config/categories.json (versionnÃ© dans git, lisible par Claude), initialisÃ© avec :
     factures, diplomes (DEUG, Licence, Master, Doctorat, Autres),
     attestations (Travail, Scolarite, Autres), contrats, banque.
   - Chaque catÃ©gorie du registre a une liste de mots-clÃ©s (motifs regex). Les catÃ©gories
     de dÃ©part reÃ§oivent les regex actuelles de test_classification.py.
   - RÃ¨gle stricte : un mot-clÃ© ne contient JAMAIS de nom de personne ni de numÃ©ro.
   - L'agent consulte le registre avant chaque rangement.
   - Type connu : rangement normal (rÃ¨gle de confiance A).
   - Type nouveau : Phi-4-mini propose un nom ; le code le normalise (minuscules,
     sans accents) puis le compare aux catÃ©gories existantes (similaritÃ©) pour Ã©viter
     les doublons. S'il est vraiment nouveau : A_Valider/ avec la proposition.
     L'humain dÃ©cide une seule fois dans Streamlit (crÃ©er / ranger dans Autres /
     renommer). Ã€ ce moment, Phi-4-mini propose 3 Ã  5 mots-clÃ©s, l'humain les confirme
     ou les corrige, puis la catÃ©gorie ET ses mots-clÃ©s sont ajoutÃ©s au registre.
   - La rÃ¨gle de confiance A s'applique ensuite Ã  toutes les catÃ©gories de la mÃªme faÃ§on.
   - Champs gÃ©nÃ©riques d'une catÃ©gorie dÃ©couverte : titre, personne, organisme, date.
10. Ã‰valuation : pas d'attendus rempli Ã  l'avance. L'agent traite le jeu de test, puis
    l'utilisateur marque chaque rÃ©sultat Â« correct Â» ou Â« faux Â» dans un fichier de revue.
    On calcule ensuite le taux de rÃ©ussite.

## RÃ¨gle de confiance (dÃ©cision A, validÃ©e)
| Situation | Confiance |
|---|---|
| Regex nettes (1 seul type, >= 2 indices) | 0.95, sans LLM |
| Regex et Phi-4-mini d'accord | 0.90 |
| DÃ©saccord, Ã©galitÃ© ou aucun indice regex | 0.60 |
Confiance >= 0.90 -> rangement automatique, sinon -> A_Valider/.
On ne demande JAMAIS au LLM son propre chiffre de confiance (non calibrÃ©).

## CatÃ©gories et champs
- Factures/ : fournisseur, date_facture, numero, montant_ht, tva, montant_ttc
- Diplomes/ (DEUG, Licence, Master, Doctorat, Autres) : titulaire, intitule, etablissement,
  date_obtention, mention. Une Â« attestation de rÃ©ussite Â» d'un diplÃ´me va dans Diplomes/.
- Attestations/ (Travail, Scolarite, Autres) : emetteur, beneficiaire, objet, date
- Contrats/ : parties, objet, date_signature, duree
- Banque/ : banque, titulaire, periode, objet, rib, iban
- CatÃ©gorie dÃ©couverte (validÃ©e par l'humain) : titre, personne, organisme, date
- A_Valider/ : confiance < 0.90, type nouveau Ã  valider, texte arabe, scan groupÃ©, illisible
- Autres/ : ce qui ne rentre nulle part
- DonnÃ©es sensibles EXTRAITES quand elles sont prÃ©sentes : cin, rib, iban, date_naissance,
  adresse (voir architecture, point 2). Jamais affichÃ©es par les scripts.
- FranÃ§ais uniquement (arabe en v2 -> A_Valider/). Un fichier = un document.
- Volume : 20-30 documents/jour.

## Format JSON de sortie (schÃ©ma unique)
{"type", "source", "date_traitement", "confiance_classification",
 "champs": {...selon le type...}, "necessite_validation_humaine", "texte_brut"}

## Ã‰tat d'avancement
- FAIT : Python 3.11 + .venv, requests + python-dotenv installÃ©s.
- FAIT : Ollama + phi4-mini installÃ©s et testÃ©s (voir mesures ci-dessus).
- FAIT : `test_jev.py` (JEV via OpenRouter) : fonctionne jusqu'Ã  l'erreur 402
  (compte sans crÃ©dits). Mis de cÃ´tÃ© pour la v2. `.env` contient OPENROUTER_API_KEY :
  ne jamais l'afficher ni le lire Ã  voix haute.
- FAIT : `test_classification.py` (regex + rÃ¨gle mÃ©tier + Phi-4-mini + rÃ¨gle de confiance),
  lancÃ© avec le vrai Ollama.
  - Mode normal : 5 rangÃ©s, 1 en A_Valider (recu_paiement), 0 mal rangÃ©, 22,8 s.
  - Mode `--forcer-llm` : 4 rangÃ©s, 2 en A_Valider, 0 mal rangÃ©, ~19 s/document
    (dont 6-7 s de chargement Ã  cause de keep_alive=0).
    Erreurs de Phi-4-mini : attestation_reussite_deug -> Â« attestation Â» (attendu diplome),
    recu_paiement -> Â« facture Â» (attendu autre). La rÃ¨gle de confiance les a bloquÃ©es.
  - RÃ¨gle mÃ©tier regex : Â« attestation de rÃ©ussite Â» + nom de diplÃ´me -> diplome (0.95, sans LLM).
- FAIT : `.claude/settings.json` interdit Ã  Claude Code la lecture de `.env`,
  `tests/docs_test/`, `Folder_Entree/`, `Folder_Sortie/` et `data/`.
- FAIT : dossier `tests/docs_test/` crÃ©Ã© (documents d'exemple, ignorÃ© par git).
  Les dossiers de sortie ne sont PAS crÃ©Ã©s (voir architecture, point 8).
- FAIT : PyMuPDF 1.28.2 installÃ© dans le .venv (utiliser `import pymupdf`, `fitz` est obsolÃ¨te).
- FAIT : `tests/inventaire_docs_test.py` (Ã©tape a) : noms, formats, taille, pages, natif/scan
  (seuil : 50 caractÃ¨res visibles par page, jamais affichÃ©s). RÃ©sultat du jeu de test :
  17 fichiers (13 .pdf, 2 .xls, 1 .dotx, 1 .jpg) ; PDF : 4 natifs, 9 scans, 0 mixte,
  0 illisible ; 12 pages Ã  passer en OCR. Aucun .docx ni .xlsx dans le jeu de test.
- FAIT (Ã©tape b1) : pydantic 2.13.5 + pytest 9.1.1 installÃ©s. `pytest.ini` (seuls test_*.py,
  docs_test/ jamais explorÃ©). Lancer les tests : `python -m pytest -v`.
  - `config/categories.json` : registre initial (5 catÃ©gories, dossier, sous-dossiers,
    mots-clÃ©s repris de test_classification.py + banque, champs, rÃ¨gle mÃ©tier).
    ClÃ©s globales : champs_generiques, champs_sensibles (cin, rib, iban, date_naissance,
    adresse : ajoutÃ©s aux champs de TOUTES les catÃ©gories).
  - `src/config.py` : charge et vÃ©rifie le registre (noms normalisÃ©s, pas de doublons,
    dossiers comparÃ©s sans tenir compte de la casse, regex valides, pas de chiffre en clair
    ni de nombre en lettres dans les mots-clÃ©s ; \d et {24} autorisÃ©s), liste toutes les
    erreurs d'un coup. Constantes : SEUIL_CONFIANCE, A_Valider, Autres (noms rÃ©servÃ©s).
  - `src/schemas.py` : modÃ¨le Pydantic `DocumentSortie` (schÃ©ma unique). Champs autorisÃ©s
    selon la catÃ©gorie (gÃ©nÃ©riques si type dÃ©couvert), champs absents -> None,
    confiance < 0.90 impose necessite_validation_humaine=true. Les erreurs ne recopient
    jamais les valeurs, et repr() masque champs et texte_brut.
  - Limite : le contrÃ´le Â« pas de nom de personne Â» dans les mots-clÃ©s ne peut pas Ãªtre
    automatique ; il reste une vÃ©rification humaine.
  - ComplÃ©ment b1 (sous-dossiers) : chaque sous-dossier a ses mots-clÃ©s
    ({"nom", "mots_cles"}). Autres est obligatoire dÃ¨s qu'il y a des sous-dossiers et n'a
    pas de mots-clÃ©s (choix par dÃ©faut). `choisir_sous_dossier(categorie, texte)` dans
    src/config.py, SANS LLM : exactement un reconnu -> ce sous-dossier ; aucun ou
    plusieurs -> Autres ; catÃ©gorie sans sous-dossiers -> None. 58 tests OK.
- FAIT : blocage vÃ©rifiÃ© : l'outil Read de Claude Code refuse tests/docs_test/essai_blocage.txt.
  Limite : la rÃ¨gle Â« deny Read Â» vise l'outil Read, pas les commandes shell
  (Get-Content, cat...). Claude ne doit donc jamais utiliser le shell pour lire ces dossiers.

## Questions encore ouvertes (Ã  poser avant l'Ã©tape concernÃ©e)
- B : RÃ‰SOLUE (2026-09-25) : Diplomes/ a un sous-dossier Autres/ (Bac, BTS, DUT...).
- C : SANS OBJET (2026-09-25) : plus de masquage dans les sorties (architecture, point 2).
- D : RÃ‰SOLUE : champs Banque/ = banque, titulaire, periode, objet, rib, iban.
- E : RÃ‰SOLUE : mots-clÃ©s par catÃ©gorie dans le registre (architecture, point 9).
- F : RÃ‰SOLUE : registre dans config/categories.json (lisible, versionnÃ©) ;
  data/ reste interdit en lecture (logs, sorties).
- G : RÃ‰SOLUE : l'agent ne plante jamais (architecture, point 1).
- Aucune question ouverte Ã  ce jour.

## Prochaines Ã©tapes (une Ã  la fois)
0. FAIT : lancer `python test_classification.py` puis `--forcer-llm` ; analyser temps et erreurs.
a) FAIT : Inventaire du jeu de test : script tests/inventaire_docs_test.py (noms, formats, pages,
   natif ou scan), sans jamais afficher le contenu.
b) DÃ©coupage en modules, UN MODULE (ou une petite paire) PAR Ã‰TAPE, avec son test :
   b1) FAIT : config/categories.json + src/config.py + src/schemas.py.
   Suite : src/masking.py,
   src/normalize.py, src/filer.py, src/extract_text.py, src/ocr_worker.py,
   src/rules.py, src/llm.py, src/classifier.py, src/extractor.py, src/pipeline.py,
   app/streamlit_app.py, avec un test pour chacun (dossier tests/).
