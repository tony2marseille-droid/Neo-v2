# deployer.ps1 - deploie le depot (code/) vers la production. Le depot est la SOURCE
# UNIQUE : on ne modifie jamais la production a la main (voir verifier_ecart.ps1).
#
# Usage :
#   powershell -ExecutionPolicy Bypass -File E:\neo-repo\deployer.ps1            # simulation + liste des fichiers qui changeront
#   powershell -ExecutionPolicy Bypass -File E:\neo-repo\deployer.ps1 -Executer  # deploiement reel
#   ... -Executer -BasculeSeule   # reprise : etapes 4-6 seulement (sources deja
#                                 # copiees et binaires deja construits)
#
# Ce qui va ou : correspondances.psd1. Etapes :
#   1. sauvegarde de tout ce qui sera remplace (restaurable par annuler_deploiement.ps1)
#   2. sources Rust + build dans un dossier cible SEPARE (le serveur continue de tourner)
#   3. sources Python / frontend / scripts, build du frontend
#   4. bascule des binaires ; le superviseur relance serveur et services, puis
#      les anciennes taches de lancement sont desactivees (superviseur_taches.ps1)
#   5. verifications (sante, outil, statut SD, API video, ecart nul)
#   6. trace du commit deploye dans E:\ia\DEPLOIEMENT.json
# Rien n'est supprime sans avoir ete sauvegarde au prealable.

param([switch]$Executer, [switch]$BasculeSeule)

$ErrorActionPreference = 'Stop'
$depot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $depot 'outils_deploiement.ps1')
$RUST = 'E:\localai-rs'
$PY = 'E:\ia'
$horodatage = Get-Date -Format 'yyyyMMdd_HHmmss'
$sauvegarde = "$PY\_sauvegardes\deploiement_$horodatage"
$cibleBuild = "$RUST\target-deploiement"
$serveur = "$RUST\target\release\localai-server.exe"
$watch = "$RUST\target\release\localai-watch.exe"
$silencieux = "$PY\tools\neo-silencieux.exe"

function Etape([string]$t) { Write-Host ''; Write-Host "=== $t ===" }
function Faire([string]$quoi, [scriptblock]$action) {
    if ($Executer) { Write-Host "  -> $quoi"; & $action }
    else { Write-Host "  (simulation) $quoi" }
}
# Arret par CHEMIN d'executable (la recherche par port ne voit pas toujours
# un processus lance par une tache SYSTEM), puis attente que le fichier soit
# reellement deverrouille avant de le remplacer.
function Arreter-Exe([string]$chemin) {
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and ($_.Path -ieq $chemin) } |
        ForEach-Object { Write-Host "     arret pid $($_.Id) ($chemin)"; Stop-Process -Id $_.Id -Force }
}
function Remplacer([string]$source, [string]$cible) {
    for ($i = 0; $i -lt 30; $i++) {
        try { Copy-Item $source $cible -Force -ErrorAction Stop; return }
        catch { Start-Sleep -Seconds 1 }
    }
    throw "fichier toujours verrouille apres 30 s : $cible"
}
function Arreter-Script([string]$motif) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*$motif*" } |
        ForEach-Object { Write-Host "     arret pid $($_.ProcessId) ($motif)"; Stop-Process -Id $_.ProcessId -Force }
}

$entrees = @(Lire-Correspondances $depot)
$entreesRust = @($entrees | Where-Object { $_.Vivant.StartsWith("$RUST\") })
$entreesAutres = @($entrees | Where-Object { -not $_.Vivant.StartsWith("$RUST\") })

Etape "Verifications prealables"
foreach ($p in @("$PY\.venv\Scripts\python.exe", "$PY\frontend\node_modules")) {
    if (-not (Test-Path $p)) { throw "absent : $p" }
}
$manquants = @($entrees | Where-Object { -not (Test-Path -LiteralPath $_.Depot) })
if ($manquants) { throw "entrees de correspondances.psd1 absentes du depot : $($manquants.Depot -join ', ')" }
$etat = & git -C $depot status --porcelain
if ($etat) { throw "le depot a des modifications non commitees : commitez d'abord (on ne deploie que des commits)." }
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Executer -and -not $admin) {
    throw "console administrateur requise : le serveur, le service video et le chien de garde tournent avec des droits eleves."
}
$commit = (& git -C $depot rev-parse --short HEAD).Trim()
Write-Host "  depot propre, commit $commit"

if ($BasculeSeule) {
    $derniere = Get-ChildItem "$PY\_sauvegardes" -Directory -Filter 'deploiement_*' | Sort-Object Name | Select-Object -Last 1
    if (-not $derniere) { throw "aucune sauvegarde trouvee : lancer le deploiement complet." }
    $sauvegarde = $derniere.FullName
    foreach ($exe in 'localai-server.exe', 'localai-watch.exe', 'neo-silencieux.exe') {
        if (-not (Test-Path "$cibleBuild\release\$exe")) { throw "binaire absent : $cibleBuild\release\$exe (lancer le deploiement complet)." }
    }
    Write-Host "  reprise : bascule seule, sauvegarde de reference $sauvegarde"
} else {

$ecarts = @(Comparer-Ecart $entrees)
Write-Host "  fichiers qui changeront en production : $($ecarts.Count)"
foreach ($e in $ecarts | Select-Object -First 40) { Write-Host ("     {0,-10} {1}" -f $e.Etat, $e.Vivant) }
if ($ecarts.Count -gt 40) { Write-Host "     ... et $($ecarts.Count - 40) autre(s)" }
$prodSeule = @($ecarts | Where-Object { $_.Etat -ne 'DEPOT_SEUL' })
if ($prodSeule) {
    Write-Host "  !! $($prodSeule.Count) fichier(s) modifie(s) ou cree(s) en production seront remplaces ou supprimes."
    Write-Host "     Ils sont sauvegardes ; s'ils etaient voulus : verifier_ecart.ps1 -Rapatrier AVANT de deployer."
}

Etape "1. Sauvegarde de ce qui sera remplace -> $sauvegarde"
Faire "sources et scripts de production (correspondances.psd1), frontend\dist, binaires" {
    foreach ($e in $entrees) { Sauvegarder-Chemin $sauvegarde $e.Vivant }
    Sauvegarder-Chemin $sauvegarde "$PY\frontend\dist"
    Sauvegarder-Chemin $sauvegarde "$PY\DEPLOIEMENT.json"
    Copier-Fichier (Join-Path $depot 'correspondances.psd1') "$sauvegarde\correspondances.psd1"
    foreach ($b in @($serveur, $watch, $silencieux) | Where-Object { Test-Path $_ }) {
        Copier-Fichier $b "$sauvegarde\bin\$(Split-Path -Leaf $b)"
    }
}

Etape "2. Sources Rust + build separe (le serveur continue de tourner)"
Faire "copie des sources Rust vers $RUST" {
    foreach ($e in $entreesRust) { Copier-Entree $e }
    Set-Content -Path "$RUST\COMMIT" -Value $commit -Encoding ASCII
}
Faire "cargo build --release (cible $cibleBuild)" {
    # Identite gravee dans /api/version (build.rs relit ces variables a chaque changement).
    $env:NEO_GIT_COMMIT = $commit
    $env:NEO_BUILD_DATE = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    Push-Location $RUST
    & cargo build --release --target-dir $cibleBuild
    $ok = $LASTEXITCODE -eq 0
    Pop-Location
    if (-not $ok) { throw "build Rust en echec : production intacte (binaire non remplace). Restaurer les sources avec annuler_deploiement.ps1." }
}

Etape "3. Python, frontend, scripts"
Faire "copie de $($entreesAutres.Count) entree(s) (backend, tests, frontend, toolshim, config, scripts)" {
    foreach ($e in $entreesAutres) { Copier-Entree $e }
}
Faire "build du frontend (vite -> $PY\frontend\dist)" {
    Push-Location "$PY\frontend"
    & npx --no-install tsc -b
    if ($LASTEXITCODE -eq 0) { & npx --no-install vite build }
    $ok = $LASTEXITCODE -eq 0
    Pop-Location
    if (-not $ok) { throw "build frontend en echec : l'ancien dist reste servi. Lancer annuler_deploiement.ps1." }
}
}

Etape "4. Bascule"
Faire "superviseur arrete EN PREMIER (sinon il relance l'ancien serveur pendant la bascule)" {
    & schtasks /end /tn LocalAI_watch_rs 2>$null | Out-Null
    Arreter-Exe $watch
}
Faire "serveur Rust : arret et remplacement du binaire" {
    Arreter-Exe $serveur
    Remplacer "$cibleBuild\release\localai-server.exe" $serveur
}
Faire "services Python (toolshim, rerank, video) : arret pour recharger le code deploye" {
    foreach ($motif in '-m toolshim', 'rerank_server.py', 'serve_video.py') { Arreter-Script $motif }
}
Faire "superviseur : nouveau binaire et redemarrage (il relance le serveur et les services)" {
    Remplacer "$cibleBuild\release\localai-watch.exe" $watch
    & schtasks /run /tn LocalAI_watch_rs 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  !! tache LocalAI_watch_rs non relancee : lancement direct."
        Start-Process -FilePath $watch -WindowStyle Hidden
    }
}
Faire "attente des services (config\services.json, 4 min max)" {
    $prets = $false
    for ($i = 0; $i -lt 48 -and -not $prets; $i++) {
        Start-Sleep -Seconds 5
        try {
            $e = Invoke-RestMethod http://127.0.0.1:8320/api/system/services -TimeoutSec 5
            $attente = @($e.services | Where-Object { $_.etat -ne 'ok' -and $_.etat -ne 'desactive' })
            $prets = $e.superviseur_actif -and $attente.Count -eq 0
            if (-not $prets -and $i % 6 -eq 5) { Write-Host "     en attente : $(($attente | ForEach-Object { "$($_.nom)=$($_.etat)" }) -join ', ')" }
        } catch { }
    }
    if (-not $prets) { Write-Host "  !! tous les services ne sont pas prets : voir E:\ia\logs\watch_rs.log" }
}
Faire "taches planifiees remplacees par le superviseur : desactivation (sauvegarde XML)" {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $depot 'code\scripts\superviseur_taches.ps1') -Executer
}
Faire "lanceur des taches planifiees ($silencieux)" {
    # Toujours verrouille : il heberge des services permanents (rerank, toolshim...).
    # Windows autorise a RENOMMER un executable en cours : l'ancien continue de
    # servir les taches deja lancees, les prochaines prennent le nouveau. Les
    # anciennes copies liberees sont supprimees au deploiement suivant.
    Get-ChildItem "$silencieux.ancien_*" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
    try { Copy-Item "$cibleBuild\release\neo-silencieux.exe" $silencieux -Force -ErrorAction Stop }
    catch {
        Rename-Item $silencieux "$(Split-Path -Leaf $silencieux).ancien_$horodatage"
        Copy-Item "$cibleBuild\release\neo-silencieux.exe" $silencieux -Force
        Write-Host "     en cours d'utilisation : ancien renomme en neo-silencieux.exe.ancien_$horodatage"
    }
}

Etape "5. Verifications"
if ($Executer) {
    $echecs = 0
    function Sonde([string]$nom, [scriptblock]$test) {
        try { $r = & $test; Write-Host "  OK   $nom : $r" }
        catch { Write-Host "  KO   $nom : $_"; $script:echecs++ }
    }
    Sonde 'sante :8320' { (Invoke-RestMethod http://127.0.0.1:8320/api/health -TimeoutSec 15).status }
    Sonde 'version (commit grave)' {
        $v = Invoke-RestMethod http://127.0.0.1:8320/api/version -TimeoutSec 15
        $c = ($v | ConvertTo-Json -Compress -Depth 3)
        if ($BasculeSeule -or $c -match [regex]::Escape($commit)) { $c.Substring(0, [Math]::Min(100, $c.Length)) } else { throw "commit $commit absent de /api/version : $c" }
    }
    Sonde 'outil get_datetime' { (Invoke-RestMethod http://127.0.0.1:8320/api/outil/execute -Method Post -ContentType 'application/json' -Body '{"tool":"get_datetime","args":{}}' -TimeoutSec 60).job }
    Sonde 'statut SD (pont Python)' { (Invoke-RestMethod http://127.0.0.1:8320/api/sd/status -TimeoutSec 60) | ConvertTo-Json -Compress -Depth 2 | ForEach-Object { $_.Substring(0, [Math]::Min(80, $_.Length)) } }
    Sonde 'statut video (pont Python)' { (Invoke-RestMethod http://127.0.0.1:8320/api/video/status -TimeoutSec 60).ok }
    Sonde 'API video :8321' { (Invoke-WebRequest http://127.0.0.1:8321/api/video/liste -UseBasicParsing -TimeoutSec 30).StatusCode }
    Sonde 'interface (index.html)' { (Invoke-WebRequest http://127.0.0.1:8320/ -UseBasicParsing -TimeoutSec 15).StatusCode }
    Sonde 'superviseur et services' {
        $e = Invoke-RestMethod http://127.0.0.1:8320/api/system/services -TimeoutSec 15
        if (-not $e.superviseur_actif) { throw 'superviseur muet (logs\watch_rs.log)' }
        $ko = @($e.services | Where-Object { $_.etat -ne 'ok' -and $_.etat -ne 'desactive' })
        if ($ko) { throw (($ko | ForEach-Object { "$($_.nom)=$($_.etat)" }) -join ', ') }
        ($e.services | Where-Object { $_.etat -eq 'ok' } | ForEach-Object { $_.nom }) -join ' '
    }
    Sonde 'ecart depot/production' {
        $reste = @(Comparer-Ecart $entrees)
        if ($reste) { throw "$($reste.Count) ecart(s) apres deploiement, ex. $($reste[0].Vivant)" } else { 'nul' }
    }
    Write-Host ''
    if ($echecs -gt 0) {
        Write-Host "$echecs verification(s) en echec. Retour arriere : annuler_deploiement.ps1 -Sauvegarde $sauvegarde -Executer"
        exit 1
    }

    Etape "6. Trace du deploiement"
    $trace = [ordered]@{ commit = $commit; date = (Get-Date).ToString('s'); sauvegarde = $sauvegarde }
    $trace | ConvertTo-Json | Set-Content -Path "$PY\DEPLOIEMENT.json" -Encoding UTF8
    Write-Host "  $PY\DEPLOIEMENT.json : commit $commit"
    Write-Host ''
    Write-Host "Deploiement termine. Sauvegarde : $sauvegarde"
} else {
    Write-Host '  (simulation) sante, version, outil, statut SD, statut video, API :8321, interface, ecart nul'
    Write-Host ''
    Write-Host 'Simulation terminee : relancer avec -Executer pour deployer.'
}
