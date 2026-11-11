<#
.SYNOPSIS
    Demarre Neo (superviseur et services) puis ouvre l'interface dans le navigateur.
    Sans droits administrateur. Si Neo tourne deja, ouvre simplement l'interface.
#>
param([switch]$SansNavigateur, [int]$AttenteMax = 180)

$racine = $PSScriptRoot
$module = Join-Path $racine 'scripts\NeoConfig.psm1'
if (Test-Path $module) { Import-Module $module -Force; $racine = Import-NeoConfig -Depart $racine }
$env:LOCALAI_ROOT = $racine
$port = if ($env:LOCALAI_PORT) { $env:LOCALAI_PORT } else { '8320' }
$sante = "http://127.0.0.1:$port/api/health"
$journal = Join-Path $racine 'logs\demarrage.log'
New-Item -ItemType Directory -Force -Path (Split-Path $journal) | Out-Null
function Noter([string]$t) { Add-Content -LiteralPath $journal -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $t" -Encoding UTF8; Write-Host $t }
function EnLigne { try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 $sante).StatusCode -eq 200 } catch { $false } }

if (-not (EnLigne)) {
    $watch = Join-Path $racine 'bin\localai-watch.exe'
    $deja = Get-Process localai-watch -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $watch }
    if (-not $deja) {
        if (-not (Test-Path $watch)) { Noter "superviseur absent : $watch"; exit 1 }
        Start-Process -FilePath $watch -WorkingDirectory $racine -WindowStyle Hidden
        Noter 'superviseur lance'
    }
    $debut = Get-Date
    while (-not (EnLigne)) {
        if (((Get-Date) - $debut).TotalSeconds -gt $AttenteMax) {
            Noter "Neo ne repond pas apres $AttenteMax s. Journaux : $racine\logs"
            exit 1
        }
        Start-Sleep -Seconds 2
    }
    Noter "Neo est pret sur le port $port"
}
if (-not $SansNavigateur) { Start-Process "http://127.0.0.1:$port/" }
exit 0
