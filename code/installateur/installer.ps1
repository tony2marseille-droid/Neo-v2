<#
.SYNOPSIS
    Installe Neo sur cette machine, a partir du dossier ou l'archive a ete extraite.

.DESCRIPTION
    Relancable sans risque : chaque etape verifie ce qui est deja fait.
    Aucun droit administrateur n'est necessaire. Journal : logs\installation.log.

    Etapes :
      1. verification de Windows, de PowerShell et de l'espace disque ;
      2. detection du materiel (carte NVIDIA, memoire) et choix des modules ;
      3. Python 3.11+ et Ollama (proposes via winget s'ils manquent) ;
      4. environnement Python (.venv) et dependances des modules choisis ;
      5. reglages de la machine (neo.toml) et liste des services ;
      6. modeles adaptes au materiel (sauf -SansModeles) ;
      7. raccourcis "Neo" (Bureau et menu Demarrer), puis demarrage.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer.ps1 -Oui -SansModeles -SansDemarrage
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer.ps1 -Desinstaller
#>
[CmdletBinding()]
param(
    [switch]$Oui,                 # repondre oui a toutes les questions (installation silencieuse)
    [switch]$SansModeles,         # ne pas telecharger les modeles (connexion lente, essais)
    [switch]$SansOllama,          # ne pas installer ni utiliser Ollama (essais automatiques)
    [switch]$SansDemarrage,       # ne pas lancer Neo a la fin
    [switch]$SansRaccourcis,      # ne pas creer de raccourcis
    [switch]$Reseau,              # ouvrir Neo aux autres appareils du reseau local
    [switch]$DemarrageAuto,       # lancer Neo a l'ouverture de session
    [string]$DossierModeles = '', # dossier des modeles d'Ollama (autre disque par exemple)
    [string[]]$Modules = @(),     # imposer les modules : voix, trois_d, rerank, finetune
    [switch]$Desinstaller,        # retirer raccourcis, demarrage auto et environnement Python
    [switch]$Tout                 # avec -Desinstaller : supprimer aussi donnees et journaux
)

$ErrorActionPreference = 'Stop'
$racine = $PSScriptRoot
$journaux = Join-Path $racine 'logs'
New-Item -ItemType Directory -Force -Path $journaux | Out-Null
$journal = Join-Path $journaux 'installation.log'

function Ecrire([string]$texte, [string]$couleur = 'Gray') {
    $ligne = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $texte"
    Add-Content -LiteralPath $journal -Value $ligne -Encoding UTF8
    Write-Host $texte -ForegroundColor $couleur
}
function Etape([string]$titre) { Ecrire ''; Ecrire "== $titre" 'Green' }
function Demander([string]$question) {
    if ($Oui) { return $true }
    $r = Read-Host "$question [O/n]"
    return ($r -eq '' -or $r -match '^[oOyY]')
}
function Pause-SiFenetre {
    # Lance par « Executer avec PowerShell », la fenetre se fermerait aussitot :
    # on laisse le temps de lire (sauf installation silencieuse).
    if (-not $Oui) { Read-Host 'Appuyez sur Entree pour fermer cette fenetre' | Out-Null }
}
function Arreter([string]$message) {
    Ecrire "ERREUR : $message" 'Red'
    Ecrire "Journal complet : $journal" 'Red'
    Pause-SiFenetre
    exit 1
}
# Commande externe (pip, python, winget, ollama) : ses messages sur la sortie
# d'erreur (avertissements, barres de progression) ne doivent jamais arreter le
# script. Sous Windows PowerShell 5.1, avec ErrorActionPreference = Stop, la
# premiere ligne d'avertissement redirigee par 2>&1 devenait une erreur fatale.
function Natif([scriptblock]$commande) {
    $avant = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $commande 2>&1 | ForEach-Object { "$_" } }
    finally { $ErrorActionPreference = $avant }
}
# Toute erreur imprevue : message clair, journal, fenetre maintenue ouverte.
trap {
    Ecrire "ERREUR inattendue : $($_.Exception.Message)" 'Red'
    Ecrire "  a la ligne $($_.InvocationInfo.ScriptLineNumber) : $($_.InvocationInfo.Line.Trim())" 'Red'
    Ecrire "Journal complet : $journal" 'Red'
    Pause-SiFenetre
    exit 1
}

$raccourcis = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Neo.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Neo.lnk')
)
$cleRun = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

# ------------------------------------------------------------ desinstallation
if ($Desinstaller) {
    Etape 'Desinstallation de Neo'
    $arret = Join-Path $racine 'Arreter-Neo.ps1'
    if (Test-Path $arret) { & powershell -NoProfile -ExecutionPolicy Bypass -File $arret }
    foreach ($r in $raccourcis) { if (Test-Path $r) { Remove-Item -LiteralPath $r -Force; Ecrire "raccourci retire : $r" } }
    if (Get-ItemProperty -Path $cleRun -Name 'Neo' -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path $cleRun -Name 'Neo'; Ecrire 'demarrage automatique retire'
    }
    $venv = Join-Path $racine '.venv'
    if (Test-Path $venv) { Remove-Item -LiteralPath $venv -Recurse -Force; Ecrire 'environnement Python retire' }
    if ($Tout) {
        foreach ($d in 'data', 'workspace') {
            $p = Join-Path $racine $d
            if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force; Ecrire "supprime : $p" }
        }
    } else {
        Ecrire 'Vos donnees (data, workspace) et les modeles d''Ollama sont conserves.'
    }
    Ecrire "Vous pouvez maintenant supprimer le dossier $racine." 'Green'
    exit 0
}

Ecrire "Installation de Neo dans $racine" 'Cyan'

# --------------------------------------------------------------- 1. systeme
Etape '1/7 Verification du systeme'
if ($PSVersionTable.PSVersion.Major -lt 5) { Arreter 'PowerShell 5.1 ou plus recent est necessaire.' }
$os = Get-CimInstance Win32_OperatingSystem
if ([int]($os.BuildNumber) -lt 17763) { Arreter "Windows 10 (1809) ou plus recent est necessaire (build $($os.BuildNumber))." }
if (-not [Environment]::Is64BitOperatingSystem) { Arreter 'Un Windows 64 bits est necessaire.' }
$lecteur = (Split-Path -Qualifier $racine).TrimEnd(':')
$libreGo = [math]::Round((Get-PSDrive $lecteur).Free / 1GB, 1)
Ecrire "Windows $($os.Caption) (build $($os.BuildNumber)), $libreGo Go libres sur $lecteur`:"
# Modeles compris : 15 Go ; sans modeles (Python et dependances) : 4 Go.
$minGo = if ($SansModeles -or $SansOllama) { 4 } else { 15 }
if ($libreGo -lt $minGo) { Arreter "Au moins $minGo Go libres sont necessaires sur $lecteur`: ($libreGo Go)." }
foreach ($f in 'bin\localai-server.exe', 'bin\localai-watch.exe', 'backend', 'frontend\dist\index.html', 'config\neo.exemple.toml') {
    if (-not (Test-Path (Join-Path $racine $f))) { Arreter "fichier manquant : $f (archive incomplete ?)" }
}

# -------------------------------------------------------------- 2. materiel
Etape '2/7 Detection du materiel'
$ramGo = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
$vramGo = 0.0; $carte = 'aucune carte NVIDIA'
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    try {
        $l = (& $smi.Source --query-gpu=name,memory.total --format=csv,noheader,nounits | Select-Object -First 1)
        if ($l) { $p = $l.Split(','); $carte = $p[0].Trim(); $vramGo = [math]::Round([double]$p[1].Trim() / 1024, 1) }
    } catch { }
}
Ecrire "Memoire : $ramGo Go ; carte graphique : $carte ($vramGo Go)"
$palier = if ($vramGo -ge 22) { 'T3' } elseif ($vramGo -ge 10) { 'T2' } else { 'T1' }
if ($Modules.Count -eq 0) {
    $Modules = @('voix', 'trois_d')
    if ($ramGo -ge 16) { $Modules += 'rerank' }
    if ($vramGo -ge 16) { $Modules += 'finetune' }
}
Ecrire "Palier de modeles : $palier ; modules : base, $($Modules -join ', ')"
if ($vramGo -lt 8) {
    Ecrire 'Sans carte NVIDIA de 8 Go, Neo fonctionne avec de petits modeles : la conversation sera plus lente, le studio image et la video ne seront pas proposes.' 'Yellow'
}

# ------------------------------------------------------- 3. Python et Ollama
Etape '3/7 Python et Ollama'
function Trouver-Python {
    foreach ($c in @(@('py', '-3.12'), @('py', '-3.11'), @('py', '-3.13'), @('python'))) {
        $cmd = Get-Command $c[0] -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $args2 = @($c | Select-Object -Skip 1) + @('-c', 'import sys; print(sys.executable); print(sys.version_info[:2] >= (3, 11))')
            $out = @(Natif { & $cmd.Source @args2 })
            if ($LASTEXITCODE -eq 0 -and $out[-1] -eq 'True') { return $out[0] }
        } catch { }
    }
    return $null
}
$python = Trouver-Python
if (-not $python) {
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Demander 'Python 3.11+ est introuvable. L''installer avec winget (Python 3.12) ?')) {
        Natif { & winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements } | Add-Content -LiteralPath $journal -Encoding UTF8
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $python = Trouver-Python
    }
    if (-not $python) { Arreter 'Python 3.11 ou plus recent est necessaire : https://www.python.org/downloads/ puis relancez l''installateur.' }
}
Ecrire "Python : $python"

$ollama = $null
if (-not $SansOllama) {
    $o = Get-Command ollama -ErrorAction SilentlyContinue
    if ($o) { $ollama = $o.Source }
    elseif (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe") { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
    if (-not $ollama) {
        if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Demander 'Ollama (moteur des modeles de langage) est introuvable. L''installer avec winget ?')) {
            Natif { & winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements } | Add-Content -LiteralPath $journal -Encoding UTF8
            if (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe") { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
        }
        if (-not $ollama) { Ecrire 'Ollama absent : installez-le depuis https://ollama.com puis relancez l''installateur.' 'Yellow' }
    }
    if ($ollama) { Ecrire "Ollama : $ollama" }
}

# ------------------------------------------------- 4. environnement Python
Etape '4/7 Environnement Python et dependances'
$venvPy = Join-Path $racine '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Ecrire 'Creation de l''environnement Python (.venv)...'
    Natif { & $python -m venv (Join-Path $racine '.venv') } | Add-Content -LiteralPath $journal -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { Arreter 'creation de .venv impossible.' }
}
Natif { & $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check } | Add-Content -LiteralPath $journal -Encoding UTF8
$listes = @('requirements\base.txt') + ($Modules | ForEach-Object { "requirements\$_.txt" })
$toolshim = 'tools\toolshim\requirements.txt'
if (Test-Path (Join-Path $racine $toolshim)) { $listes += $toolshim }
foreach ($liste in $listes) {
    $chemin = Join-Path $racine $liste
    if (-not (Test-Path $chemin)) { Ecrire "liste absente, ignoree : $liste" 'Yellow'; continue }
    Ecrire "Dependances : $liste (quelques minutes)..."
    Natif { & $venvPy -m pip install -r $chemin --quiet --disable-pip-version-check } | Add-Content -LiteralPath $journal -Encoding UTF8
    if ($LASTEXITCODE -ne 0) {
        if ($liste -eq 'requirements\base.txt') { Arreter "installation de $liste impossible." }
        Ecrire "Module ignore (installation impossible) : $liste. Voir le journal." 'Yellow'
        $Modules = @($Modules | Where-Object { "requirements\$_.txt" -ne $liste })
    }
}

# ---------------------------------------------- 5. reglages et services
Etape '5/7 Reglages de la machine'
$neoToml = Join-Path $racine 'neo.toml'
if (Test-Path $neoToml) {
    Ecrire 'neo.toml existe deja : conserve tel quel.'
} else {
    $t = Get-Content -LiteralPath (Join-Path $racine 'config\neo.exemple.toml') -Raw -Encoding UTF8
    if ($Reseau) { $t = $t -replace 'hote = "127\.0\.0\.1"', 'hote = "0.0.0.0"' }
    if ($ollama -and -not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        $t = $t -replace '(?m)^executable = ""', ("executable = '" + $ollama + "'")
    }
    if ($DossierModeles) {
        New-Item -ItemType Directory -Force -Path $DossierModeles | Out-Null
        $t = $t -replace '(?m)^modeles = ""', ("modeles = '" + $DossierModeles + "'")
    }
    [IO.File]::WriteAllText($neoToml, $t, (New-Object System.Text.UTF8Encoding($false)))
    Ecrire 'neo.toml cree (modifiable a tout moment).'
}
$services = Join-Path $racine 'config\services.json'
if (Test-Path $services) {
    Ecrire 'config\services.json existe deja : conserve tel quel.'
} else {
    $s = Get-Content -LiteralPath (Join-Path $racine 'config\services.exemple.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($svc in $s.services) {
        $actif = $true
        if ($svc.nom -eq 'rerank') { $actif = $Modules -contains 'rerank' }
        if ($svc.nom -in @('video', 'comfyui')) { $actif = $false }
        if ($svc.nom -eq 'ollama' -and -not $ollama) { $actif = $false }
        $svc | Add-Member -NotePropertyName actif -NotePropertyValue $actif -Force
    }
    $json = $s | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($services, $json, (New-Object System.Text.UTF8Encoding($false)))
    Ecrire "config\services.json cree (services actifs : $(@($s.services | Where-Object actif | ForEach-Object nom) -join ', '))."
}
foreach ($d in 'data', 'workspace', 'logs') { New-Item -ItemType Directory -Force -Path (Join-Path $racine $d) | Out-Null }

# ---------------------------------------------------------------- 6. modeles
Etape '6/7 Modeles'
if ($SansModeles -or -not $ollama) {
    Ecrire 'Telechargement des modeles ignore. L''assistant de premier lancement de Neo les propose aussi.'
} else {
    $env:LOCALAI_ROOT = $racine
    $env:LOCALAI_FORCE_TIER = $palier
    $liste = @(Natif { & $venvPy -c "from backend.core.hardware import recommended_pulls; print('\n'.join(recommended_pulls('$palier')))" } |
        Where-Object { $_ -match '^[a-z0-9._-]+(:[A-Za-z0-9._-]+)?$' })
    if (-not $liste) { $liste = @('gemma3:4b', 'nomic-embed-text') }
    if ($DossierModeles) { $env:OLLAMA_MODELS = $DossierModeles }
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:11434/api/version' | Out-Null }
    catch { Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden; Start-Sleep -Seconds 5 }
    foreach ($m in $liste) {
        if (-not $m) { continue }
        Ecrire "Telechargement de $m (peut etre long)..."
        Natif { & $ollama pull $m } | Select-Object -Last 1 | ForEach-Object { Ecrire "  $_" }
    }
}

# -------------------------------------------------- 7. raccourcis, demarrage
Etape '7/7 Raccourcis et demarrage'
$demarrer = Join-Path $racine 'Demarrer-Neo.ps1'
$cible = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$demarrer`""
if (-not $SansRaccourcis) {
    $shell = New-Object -ComObject WScript.Shell
    foreach ($r in $raccourcis) {
        $lnk = $shell.CreateShortcut($r)
        $lnk.TargetPath = $cible; $lnk.Arguments = $arguments; $lnk.WorkingDirectory = $racine
        $icone = Join-Path $racine 'frontend\dist\favicon.ico'
        if (Test-Path $icone) { $lnk.IconLocation = $icone }
        $lnk.Description = 'Neo, assistant IA local'
        $lnk.Save()
        Ecrire "raccourci : $r"
    }
}
if ($DemarrageAuto) {
    Set-ItemProperty -Path $cleRun -Name 'Neo' -Value "`"$cible`" $arguments"
    Ecrire 'Neo demarrera a l''ouverture de session.'
}
if (-not $SansDemarrage) {
    & $cible -NoProfile -ExecutionPolicy Bypass -File $demarrer
}
Ecrire ''
Ecrire 'Installation terminee. Lancez Neo avec le raccourci "Neo" ; l''interface s''ouvre dans votre navigateur.' 'Green'
Ecrire "Reglages : $neoToml ; journal : $journal" 'Green'
Pause-SiFenetre
