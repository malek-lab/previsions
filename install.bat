@echo off
echo Installation de l'environnement...
python -m venv venv
call venv\Scripts\activate
pip install -r requirements.txt
echo Installation terminée. Lancez run.bat pour démarrer.
pause