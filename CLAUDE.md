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
1. Extraction texte : PyMuPDF si PDF natif, DOCX via python-docx, PaddleOCR 2.x si scan/image
   (PaddleOCR lancé dans un sous-process pour libérer la RAM à la fin).
2. Masquage par regex du CIN et du RIB AVANT tout traitement (seulement près des mots-clés
   CIN / C.I.N / CNIE ; RIB = 24 chiffres). Ne pas masquer les n° de facture type FA123456.
3. Classification LOCALE : regex d'abord, Phi-4-mini seulement si les regex hésitent.
   JEV / OpenRouter ABANDONNÉ pour le MVP (pas de crédits, loi 09-08 / CNDP). Possible en v2.
4. Extraction des champs : Phi-4-mini, prompt par type, sortie JSON schema.
   Réglages Ollama : num_ctx=2048, num_gpu=0, keep_alive=0, temperature=0.
5. Normalisation déterministe Python (pas de LLM) : dates ISO, montants, HT+TVA=TTC.
6. Rangement + doublons (_1, _2). Original copié vers Folder_Sortie, puis déplacé
   vers Folder_Entree/Traites/.
7. Interface Streamlit : image + tableau éditable, pour les documents de A_Valider/.

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
- Diplomes/ (DEUG, Licence, Master, Doctorat) : titulaire, intitule, etablissement,
  date_obtention, mention. Une « attestation de réussite » d'un diplôme va dans Diplomes/.
- Attestations/ (Travail, Scolarite, Autres) : emetteur, beneficiaire, objet, date
- Contrats/ : parties, objet, date_signature, duree
- A_Valider/ : confiance < 0.90, texte arabe, scan groupé, illisible
- Autres/ : ce qui ne rentre nulle part
- Jamais de CIN ni de RIB dans les champs extraits.
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
- EN COURS : `test_classification.py` (regex + Phi-4-mini + règle de confiance).
  Testé hors ligne (regex seules et LLM simulé) : 4 documents rangés, 2 en A_Valider,
  0 mal rangé. RESTE À FAIRE : le lancer sur cette machine avec le vrai Ollama.

## Questions encore ouvertes (à poser avant l'étape concernée)
- B : diplômes hors DEUG/Licence/Master/Doctorat (Bac, BTS, DUT...) -> Diplomes/Autres/ ?
- C : la copie du PDF original dans Folder_Sortie garde le CIN/RIB visibles
  (seuls .txt/.json sont masqués). Acceptable pour le MVP ?

## Prochaines étapes (une à la fois)
1. Lancer `python test_classification.py` puis `--forcer-llm` ; analyser temps et erreurs.
2. Transformer le test en modules : src/config.py, src/schemas.py, src/masking.py,
   src/normalize.py, src/filer.py, src/extract_text.py, src/ocr_worker.py,
   src/rules.py, src/llm.py, src/classifier.py, src/extractor.py, src/pipeline.py,
   app/streamlit_app.py, avec un test pour chacun (dossier tests/).
