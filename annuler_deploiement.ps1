# annuler_deploiement.ps1 - restaure l'etat de production sauvegarde par deployer.ps1.
#
# Usage :
#   powershell -ExecutionPolicy Bypass -File E:\neo-repo\annuler_deploiement.ps1 -Sauvegarde E:\ia\_sauvegardes\deploiement_AAAAMMJJ_HHMMSS -Executer
#
# Sans -Executer : simulation (affiche ce qui serait restaure).
# Lit les deux formats de sauvegarde :
#   - actuel : correspondances.psd1 + fichiers\<chemin de production> + bin\ ;
#   - ancien (avant le 09/11/2026) : dossiers ia\ et rs\.

param(
    [Parameter(Mandatory = $true)][string]$Sauvegarde,
    [switch]$Executer
)

$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'outils_deploiement.ps1')
$RUST = 'E:\localai-rs'
$PY = 'E:\ia'
$serveur = "$RUST\target\release\localai-server.exe"
$watch = "$RUST\target\release\localai-watch.exe"
$silencieux = "$PY\tools\neo-silencieux.exe"
$format = if (Test-Path "$Sauvegarde\correspondances.psd1") { 'actuel' }
          elseif ((Test-Path "$Sauvegarde\ia") -and (Test-Path "$Sauvegarde\rs")) { 'ancien' }
          else { throw "sauvegarde incomplete ou inconnue : $Sauvegarde" }
$bin = if ($format -eq 'actuel') { "$Sauvegarde\bin" } else { "$Sauvegarde\rs\bin" }
Write-Host "Sauvegarde $Sauvegarde (format $format)"

function Faire([string]$quoi, [scriptblock]$action) {
    if ($Executer) { Write-Host "  -> $quoi"; & $action } else { Write-Host "  (simulation) $quoi" }
}
function Arreter-Exe([string]$chemin) {
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and ($_.Path -ieq $chemin) } |
        ForEach-Object { Stop-Process -Id $_.Id -Force }
}
function Remplacer([string]$source, [string]$cible) {
    for ($i = 0; $i -lt 30; $i++) {
        try { Copy-Item $source $cible -Force -ErrorAction Stop; return }
        catch { Start-Sleep -Seconds 1 }
    }
    throw "fichier toujours verrouille apres 30 s : $cible"
}

if ($format -eq 'actuel') {
    $table = Import-PowerShellDataFile "$Sauvegarde\correspondances.psd1"
    $chemins = @($table.Miroirs.Vivant) + @($table.Fichiers.Vivant) + @("$PY\frontend\dist", "$PY\DEPLOIEMENT.json")
    Faire "sources, scripts et frontend\dist ($($chemins.Count) entrees)" {
        foreach ($v in $chemins) {
            $copie = Chemin-Sauvegarde $Sauvegarde $v
            if (Test-Path -LiteralPath $copie -PathType Container) { Copier-Dossier $copie $v }
            elseif (Test-Path -LiteralPath $copie) { Copier-Fichier $copie $v }
            elseif ($v -like '*DEPLOIEMENT.json') { Remove-Item -LiteralPath $v -Force -ErrorAction SilentlyContinue }
            # Absent de la sauvegarde = cree par ce deploiement : laisse en place (il vient du depot).
            elseif (Test-Path -LiteralPath $v) { Write-Host "     cree par le deploiement, laisse en place : $v" }
        }
    }
} else {
    Faire 'sources Python, tests, frontend (src + dist), toolshim, serve_video.py' {
        foreach ($d in @('backend', 'tests_unit', 'frontend\src', 'frontend\dist', 'tools\toolshim')) {
            Copier-Dossier "$Sauvegarde\ia\$d" "$PY\$d"
        }
        Get-ChildItem "$Sauvegarde\ia\frontend" -File | ForEach-Object { Copy-Item $_.FullName "$PY\frontend\$($_.Name)" -Force }
        Copy-Item "$Sauvegarde\ia\serve_video.py" "$PY\serve_video.py" -Force
    }
    Faire 'sources Rust' {
        foreach ($c in Get-ChildItem "$Sauvegarde\rs" -Directory | Where-Object { Test-Path "$($_.FullName)\src" }) {
            Copier-Dossier "$($c.FullName)\src" "$RUST\$($c.Name)\src"
            Copy-Item "$($c.FullName)\Cargo.toml" "$RUST\$($c.Name)\Cargo.toml" -Force
        }
        Copy-Item "$Sauvegarde\rs\Cargo.toml" "$RUST\Cargo.toml" -Force
        Copy-Item "$Sauvegarde\rs\Cargo.lock" "$RUST\Cargo.lock" -Force
    }
}
Faire 'binaire serveur precedent + redemarrage' {
    & schtasks /end /tn LocalAI_watch_rs 2>$null | Out-Null
    Arreter-Exe $watch
    Arreter-Exe $serveur
    Remplacer "$bin\localai-server.exe" $serveur
    & schtasks /run /tn LocalAI_server_boot 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { & powershell -NoProfile -ExecutionPolicy Bypass -File "$PY\run_server_boot.ps1" }
}
Faire 'service video precedent' {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*serve_video.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 2
    & schtasks /run /tn NeoVideoAPI 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Start-Process -FilePath "$PY\.venv\Scripts\python.exe" -ArgumentList @('-u', "$PY\serve_video.py") -WorkingDirectory $PY -WindowStyle Hidden
    }
}
Faire 'chien de garde et lanceur des taches precedents (administrateur requis)' {
    try {
        Remplacer "$bin\localai-watch.exe" $watch
        if (Test-Path "$bin\neo-silencieux.exe") { Remplacer "$bin\neo-silencieux.exe" $silencieux }
        & schtasks /run /tn LocalAI_watch_rs 2>$null | Out-Null
    } catch { Write-Host "  !! binaires non restaures ($_)" }
}
if ($Executer) {
    Start-Sleep -Seconds 8
    try { Write-Host ("sante : " + (Invoke-RestMethod http://127.0.0.1:8320/api/health -TimeoutSec 15).status) }
    catch { Write-Host "sante KO : $_" }
}
$taches = Get-ChildItem "$PY\_sauvegardes" -Directory -Filter 'taches_superviseur_*' -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
if ($taches) {
    Write-Host ''
    Write-Host "Si le binaire restaure precede le superviseur unique, reactiver aussi les anciennes taches :"
    Write-Host "  restaurer_taches.ps1 -Sauvegarde $($taches.FullName)"
}
