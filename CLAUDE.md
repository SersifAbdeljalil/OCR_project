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
- Aucune question ouverte à ce jour.

## Prochaines étapes (une à la fois)
0. FAIT : lancer `python test_classification.py` puis `--forcer-llm` ; analyser temps et erreurs.
a) Inventaire du jeu de test : script tests/inventaire_docs_test.py (noms, formats, pages,
   natif ou scan), sans jamais afficher le contenu.
b) Ensuite seulement : découpage en modules : src/config.py, src/schemas.py, src/masking.py,
   src/normalize.py, src/filer.py, src/extract_text.py, src/ocr_worker.py,
   src/rules.py, src/llm.py, src/classifier.py, src/extractor.py, src/pipeline.py,
   app/streamlit_app.py, avec un test pour chacun (dossier tests/).
