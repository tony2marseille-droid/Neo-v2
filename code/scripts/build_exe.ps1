$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Push-Location "$root\frontend"
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Build frontend echoue" }
Pop-Location

Push-Location $root
& ".\.venv\Scripts\pyinstaller.exe" packaging\localai.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Packaging PyInstaller echoue" }
Pop-Location

Write-Host ""
Write-Host "Executable pret : dist\LocalAI\LocalAI.exe" -ForegroundColor Green
