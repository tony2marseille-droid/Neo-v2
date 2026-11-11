@echo off
setlocal
rem Espace de travail Cargo = dossier de ce fichier ; journal rebuild.log a cote.
cd /d "%~dp0"
set "JOURNAL=%~dp0rebuild.log"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8320 ^| findstr LISTENING') do taskkill /f /pid %%a >> "%JOURNAL%" 2>&1
timeout /t 2 /nobreak >nul
cargo build --release >> "%JOURNAL%" 2>&1
if errorlevel 1 (echo BUILD_ECHEC >> "%JOURNAL%" & exit /b 1)
echo BUILD_OK >> "%JOURNAL%"
schtasks /run /tn LocalAI_server_boot >> "%JOURNAL%" 2>&1
timeout /t 12 /nobreak >nul
curl -s http://localhost:8320/api/health >> "%JOURNAL%" 2>&1
echo. >> "%JOURNAL%"
curl -s -m 30 -X POST http://localhost:8320/api/outil/execute -H "Content-Type: application/json" -d "{\"tool\":\"get_datetime\",\"args\":{}}" >> "%JOURNAL%" 2>&1
echo. >> "%JOURNAL%"
echo FINI >> "%JOURNAL%"
