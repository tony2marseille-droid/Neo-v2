<#
.SYNOPSIS
    Assemble l'archive d'installation de Neo (Windows x64) a partir du depot.

.DESCRIPTION
    Prerequis : binaires Rust compiles (cargo build --release) et interface
    construite (npm run build dans code\frontend). Produit :
      <Sortie>\Neo-<Version>\            dossier pret a installer
      <Sortie>\Neo-<Version>-windows-x64.zip
    Aucun fichier propre a une machine n'est embarque (neo.toml, services.json,
    donnees, journaux) : seulement les gabarits.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File code\installateur\assembler.ps1 -Version 2.0.0
#>
param(
    [string]$Version = 'dev',
    [string]$Depot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$Sortie = ''
)
$ErrorActionPreference = 'Stop'
$code = Join-Path $Depot 'code'
if (-not $Sortie) { $Sortie = Join-Path $Depot 'dist' }
$nom = "Neo-$Version"
$cible = Join-Path $Sortie $nom
$release = Join-Path $code 'localai-rs\target\release'

foreach ($f in "$release\localai-server.exe", "$release\localai-watch.exe", "$release\neo-silencieux.exe", "$code\frontend\dist\index.html") {
    if (-not (Test-Path $f)) { throw "absent : $f (compiler d'abord le Rust et l'interface)" }
}
if (Test-Path $cible) { Remove-Item -LiteralPath $cible -Recurse -Force }
New-Item -ItemType Directory -Force -Path $cible | Out-Null

function Copier-Dossier([string]$source, [string]$dest) {
    # robocopy : codes 0 a 7 = succes. Exclusions : caches, environnements, journaux.
    & robocopy $source $dest /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ .pytest_cache .ruff_cache .venv node_modules logs /XF *.pyc *.log | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy $source -> $dest : code $LASTEXITCODE" }
}

# Executables
New-Item -ItemType Directory -Force -Path "$cible\bin" | Out-Null
foreach ($exe in 'localai-server.exe', 'localai-watch.exe', 'neo-silencieux.exe') { Copy-Item "$release\$exe" "$cible\bin\$exe" }
# Code Python, interface, proxy d'outils
Copier-Dossier "$code\backend" "$cible\backend"
Copier-Dossier "$code\frontend\dist" "$cible\frontend\dist"
Copier-Dossier "$code\tools\toolshim" "$cible\tools\toolshim"
Copier-Dossier "$code\requirements" "$cible\requirements"
# Scripts de la racine (services Python, demarrage)
Get-ChildItem "$code\racine" -File | ForEach-Object { Copy-Item $_.FullName "$cible\$($_.Name)" }
# Configuration : gabarits uniquement
New-Item -ItemType Directory -Force -Path "$cible\config", "$cible\scripts" | Out-Null
foreach ($f in 'models.json', 'neo_persona.md', 'neo.exemple.toml', 'services.exemple.json') { Copy-Item "$code\config\$f" "$cible\config\$f" }
Copy-Item "$code\scripts\NeoConfig.psm1" "$cible\scripts\NeoConfig.psm1"
# Installateur et lanceurs a la racine
foreach ($f in 'installer.ps1', 'Demarrer-Neo.ps1', 'Arreter-Neo.ps1') { Copy-Item "$code\installateur\$f" "$cible\$f" }
Copy-Item "$code\installateur\LISEZMOI.txt" "$cible\LISEZMOI.txt"
foreach ($f in 'LICENSE', 'README.md') { Copy-Item (Join-Path $Depot $f) "$cible\$f" }
Copier-Dossier (Join-Path $Depot 'docs') "$cible\docs"

# Controle : aucun fichier propre a une machine
foreach ($interdit in 'neo.toml', 'config\services.json', 'data', 'logs') {
    if (Test-Path (Join-Path $cible $interdit)) { throw "fichier de machine dans l'archive : $interdit" }
}

$zip = Join-Path $Sortie "$nom-windows-x64.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path $cible -DestinationPath $zip -CompressionLevel Optimal
$taille = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "Archive : $zip ($taille Mo)"
