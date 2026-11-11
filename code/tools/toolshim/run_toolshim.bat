@echo off
setlocal
rem Racine : LOCALAI_ROOT, sinon deux niveaux au-dessus de ce fichier. Python : NEO_PYTHON, sinon le .venv de la racine.
set "RACINE=%LOCALAI_ROOT%"
if not defined RACINE for %%I in ("%~dp0..\..") do set "RACINE=%%~fI"
set "PY=%NEO_PYTHON%"
if not defined PY set "PY=%RACINE%\.venv\Scripts\python.exe"
cd /d "%RACINE%\tools\toolshim"
"%PY%" -m toolshim --config config.json >> "%RACINE%\tools\toolshim\toolshim.log" 2>&1
