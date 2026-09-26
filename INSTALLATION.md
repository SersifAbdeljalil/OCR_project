# Installation sur un nouveau PC Windows

Guide pour installer l'agent de tri documentaire sur un PC Windows 10 ou 11 (64 bits).
Tout fonctionne en LOCAL : aucun document ne quitte le PC.

Compter environ 30 à 45 minutes (téléchargements compris).

---

## 1. Ce qu'il faut

| Élément | Version | Pourquoi |
|---|---|---|
| Windows | 10 ou 11, 64 bits | |
| Python | **3.11** (pas 3.12 ni plus récent) | paddlepaddle 2.6.2 n'existe que jusqu'à Python 3.12 ; le projet est testé en 3.11.9 |
| Runtime Visual C++ 2015-2022 (x64) | dernière | nécessaire à paddlepaddle et opencv (erreurs « DLL load failed » sans lui) |
| Ollama | récente | fait tourner le modèle de langue Phi-4-mini en local |
| Espace disque | environ 6 Go | environnement Python (~2 Go), modèle Phi-4-mini (~2,5 Go), modèles OCR (~16 Mo) |
| RAM | 8 Go minimum | profil « modeste » ; 16 Go conseillés pour le profil « performant » |

---

## 2. Installer Python 3.11

1. Télécharger l'installateur **Windows installer (64-bit)** de Python 3.11 sur python.org.
2. Pendant l'installation, cocher **« Add python.exe to PATH »**.
3. Vérifier dans PowerShell :
   ```powershell
   py -3.11 --version
   ```
   Doit afficher `Python 3.11.x`.

> Piège : si une autre version de Python est installée (ex. 3.14), `python` peut lancer
> la mauvaise. Utiliser `py -3.11` pour créer l'environnement, puis TOUJOURS le Python
> de l'environnement `.venv` (voir plus bas).

---

## 3. Installer le runtime Visual C++

Télécharger et installer **« Microsoft Visual C++ Redistributable 2015-2022 (x64) »**
(fichier `vc_redist.x64.exe`, site de Microsoft). Redémarrer si demandé.

---

## 4. Copier le projet et créer l'environnement

1. Copier le dossier du projet (ex. `C:\Tri\OCR_PROJECT`), ou le cloner avec git.
2. Ouvrir PowerShell **dans ce dossier**, puis :
   ```powershell
   py -3.11 -m venv .venv
   ```
3. Autoriser les scripts d'activation (une seule fois par utilisateur) :
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```
   > Piège : sans ce réglage, `.\.venv\Scripts\Activate.ps1` échoue avec « l'exécution de
   > scripts est désactivée sur ce système ».
4. Activer l'environnement :
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
   L'invite commence alors par `(.venv)`.

---

## 5. Installer les bibliothèques Python

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` contient les versions EXACTES testées. Points importants, déjà
réglés dans ce fichier :

- **paddlepaddle 2.6.2** (CPU) et **paddleocr 2.10.0** : dernières versions 2.x.
- **numpy 1.26.4** (`numpy<2`) : paddlepaddle 2.6.2 plante avec numpy 2.
- **opencv 4.10.0.84** pour les TROIS paquets opencv (`opencv-python`,
  `opencv-contrib-python`, `opencv-python-headless`) : ils partagent le même module `cv2`,
  des versions différentes se marchent dessus.
- **protobuf 3.20.2** (exigé par paddlepaddle) et **streamlit 1.60.0** (interface) :
  compatibles, vérifié par `pip check`.

Vérifier :
```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import paddle, paddleocr, pymupdf, numpy; print('OK', numpy.__version__)"
```
Doit afficher `No broken requirements found.` puis `OK 1.26.4`.

---

## 6. Installer Ollama et le modèle

1. Télécharger et installer Ollama pour Windows (ollama.com). Il démarre tout seul
   (icône près de l'horloge).
2. Télécharger le modèle (≈ 2,5 Go) :
   ```powershell
   ollama pull phi4-mini
   ```
3. Vérifier :
   ```powershell
   ollama list
   ollama ps
   ```
   `phi4-mini` doit apparaître dans `ollama list` ; `ollama ps` doit être vide (aucun
   modèle chargé en mémoire au repos).

> Piège PATH : dans certains terminaux (ex. celui d'un éditeur), `ollama` est
> « introuvable ». Contournement pour la session :
> ```powershell
> Set-Alias ollama "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
> ```
> Le projet, lui, n'a pas besoin de la commande : il parle à Ollama par
> http://localhost:11434.

---

## 7. Premier lancement de l'OCR (téléchargement des modèles)

Au tout premier OCR, PaddleOCR télécharge ses modèles (≈ 16 Mo) dans
`%USERPROFILE%\.paddleocr\whl\`. Une connexion Internet est nécessaire UNE fois.
Ensuite, tout fonctionne hors ligne.

Test rapide sur une image inventée :
```powershell
.\.venv\Scripts\python.exe tests\essai_ocr.py
```

---

## 8. Régler la machine : `config/machine.json`

- `profil_actif` : `"modeste"` (2 cœurs, 8 Go, sans GPU) ou `"performant"`
  (8 cœurs ou plus, 16 Go ou plus, GPU facultatif). On peut ajouter un profil.
- `dossiers.entree` / `dossiers.sortie` : chemins relatifs au projet (par défaut
  `Folder_Entree` et `Folder_Sortie`) ou absolus (ex. `D:\\Scans`, avec des `\\` doublés
  dans le JSON).
- Profil : `threads_ocr`, `seuil_ram_worker_ocr_mo`, `modele_llm`, `num_gpu`
  (0 = processeur ; ex. 99 = carte graphique), `num_ctx`, `delai_llm_s`,
  `ocr_et_llm_simultanes` (false : Ollama doit être vide pendant l'OCR).

Aucun chemin propre à un PC n'est écrit dans le code.

---

## 9. Vérifier l'installation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
Tous les tests doivent passer (ils n'ont besoin ni d'Ollama ni des vrais documents).

Tests qui utilisent le VRAI Ollama (plus lents, OCR arrêté, navigateur fermé) :
```powershell
.\.venv\Scripts\python.exe -m pytest -m ollama_reel -s
```

---

## 10. Utilisation

1. Déposer les documents (PDF, images, DOCX, DOTX, XLS, XLSX) dans le dossier d'entrée.
2. Fermer les applications gourmandes (navigateur) sur une machine modeste.
3. Lancer :
   ```powershell
   .\.venv\Scripts\python.exe run_pipeline.py
   ```
   Options : `--profil performant`, `--entree D:\Scans`, `--sortie D:\Classement`.
4. Résultat : documents rangés dans le dossier de sortie (un `.txt` + un `.json` + la
   copie de l'original), documents douteux dans `A_Valider/`, originaux déplacés dans
   `Folder_Entree\Traites\`. Journal dans `data\logs\`.

Un lot interrompu peut être relancé : les documents déjà rangés ne sont jamais
retraités. Pour retraiter quand même un document déjà vu : `run_pipeline.py --forcer`.

---

## 10 bis. Interface de validation (Streamlit)

Lancement : **double-clic sur `lancer_interface.bat`** (à la racine du projet). Le fichier
active le `.venv` et ouvre http://127.0.0.1:8501 dans le navigateur. Fermer la fenêtre
noire arrête l'interface.

Commande équivalente :
```powershell
.\.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py
```

Confidentialité (réglée dans `.streamlit\config.toml`, et rappelée dans le `.bat`) :
- `server.address = "127.0.0.1"` : l'interface n'est accessible QUE depuis ce PC,
  jamais depuis le réseau ;
- `browser.gatherUsageStats = false` : aucune statistique n'est envoyée.

Écrans :
1. **Déposer et trier** : glisser-déposer des documents (copiés dans le dossier
   d'entrée), bouton « Lancer le tri » (le tri tourne en arrière-plan, un seul à la fois,
   progression affichée), résumé du dernier tri.
2. **Documents** : tous les documents traités, filtres par catégorie et par statut
   (« à valider » en premier). Pour chaque document : l'image de la page avec les lignes
   OCR peu sûres surlignées, les champs modifiables avec une icône de copie, la
   catégorie et le sous-dossier, le texte complet (copiable), l'historique des
   corrections ; boutons « Valider », « Rejeter (vers Autres) », « Créer une catégorie ».

Sur une machine modeste, ne pas lancer « Proposer des mots-clés » pendant un tri
(le bouton est désactivé pendant un tri).

---

## 11. Pièges connus

| Symptôme | Cause | Solution |
|---|---|---|
| `Activate.ps1 ... l'exécution de scripts est désactivée` | ExecutionPolicy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `ollama` introuvable | PATH du terminal | `Set-Alias ollama "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"` |
| `DLL load failed` à l'import de paddle ou cv2 | runtime Visual C++ absent | installer `vc_redist.x64.exe` |
| `A module that was compiled using NumPy 1.x cannot be run in NumPy 2.x` | numpy 2 installé | `pip install -r requirements.txt` (numpy 1.26.4) |
| Accents cassés (`Ã©`) dans un fichier réécrit | PowerShell 5.1 relit l'UTF-8 comme de l'ANSI | ne pas réécrire de fichier avec `Get-Content` / `Set-Content` ; si besoin, `-Encoding utf8` |
| Fichier écrit en UTF-16 par `>` | redirection de PowerShell 5.1 | utiliser `Out-File -Encoding utf8` ou Python |
| Mauvais Python utilisé | plusieurs versions installées | toujours `.\.venv\Scripts\python.exe` |
| Lot arrêté : « Ollama a un modèle chargé » | un modèle est resté en mémoire | attendre, ou `ollama stop phi4-mini`, puis relancer |
| Interface : « port 8501 déjà utilisé » | une interface tourne déjà | utiliser l'onglet déjà ouvert, ou fermer l'autre fenêtre |
| `lancer_interface.bat` ne trouve pas `.venv` | environnement non créé ou projet déplacé | refaire les étapes 4 et 5 |
| PC très lent, RAM saturée | OCR et modèle en même temps, ou navigateur ouvert | profil « modeste », fermer le navigateur |
