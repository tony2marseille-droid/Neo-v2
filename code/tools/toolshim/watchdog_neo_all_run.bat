@echo off
setlocal
rem Racine : LOCALAI_ROOT, sinon deux niveaux au-dessus de ce fichier.
set "RACINE=%LOCALAI_ROOT%"
if not defined RACINE for %%I in ("%~dp0..\..") do set "RACINE=%%~fI"
powershell -ExecutionPolicy Bypass -File "%RACINE%\tools\toolshim\watchdog.ps1"
