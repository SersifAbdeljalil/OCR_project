@echo off
rem ============================================================
rem  Interface de tri documentaire (double-clic pour lancer)
rem  - utilise le Python du .venv du projet ;
rem  - l'interface n'ecoute que sur cet ordinateur (127.0.0.1) ;
rem  - aucune statistique n'est envoyee (.streamlit\config.toml).
rem  Fermer cette fenetre arrete l'interface.
rem ============================================================
chcp 65001 > nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERREUR] Environnement .venv introuvable. Voir INSTALLATION.md.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
echo Ouverture de l'interface sur http://127.0.0.1:8501 ...
start "" "http://127.0.0.1:8501"
python -m streamlit run app\streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
pause
