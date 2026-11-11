# Repare Ollama apres une mise a jour automatique ratee (a lancer en ADMINISTRATEUR).
#
# Cause (constatee le 09/11/2026) : l'application Ollama de la barre des taches
# telecharge une nouvelle version et lance son installeur SANS droits admin ;
# l'installeur ne peut pas remplacer ollama.exe, tenu par le `ollama serve` que
# lance le superviseur localai-watch (eleve), annule... et laisse
# lib\ollama vide. Le serveur repond encore mais ne charge plus aucun modele
# (« llama-server binary not found »).
#
# Ce script : met le service « ollama » du superviseur en pause, arrete Ollama,
# relance l'installeur deja telecharge par Ollama, verifie les moteurs, puis
# rend la main au superviseur qui relance `ollama serve`.
#
#   powershell -ExecutionPolicy Bypass -File <depot>\code\scripts\reparer_ollama.ps1
#   ... -SansMiseAJourAuto   : desactive en plus les deux taches planifiees qui
#                              lancent l'application Ollama et met de cote son
#                              raccourci de demarrage (sources des mises a jour
#                              ratees ; le superviseur suffit a servir).
param([switch]$SansMiseAJourAuto)
$ErrorActionPreference = 'Stop'

$estAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $estAdmin) { throw "A lancer dans une console PowerShell ADMINISTRATEUR." }

# Reglages : neo.toml (NeoConfig.psm1), sinon LOCALAI_ROOT, sinon le dossier parent du script.
$racine = Split-Path -Parent $PSScriptRoot
$module = Join-Path $PSScriptRoot 'NeoConfig.psm1'
if (Test-Path $module) {
    Import-Module $module -Force
    $racine = Import-NeoConfig -Depart $racine
} elseif ($env:LOCALAI_ROOT) { $racine = $env:LOCALAI_ROOT }

# Ollama vit dans le profil de l'utilisateur qui l'a installe (pas forcement
# celui de la console administrateur) : on le cherche dans tous les profils.
$exe = Get-ChildItem "$env:SystemDrive\Users\*\AppData\Local\Programs\Ollama\ollama.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $exe) { throw "Ollama introuvable dans les profils de $env:SystemDrive\Users (l'installer depuis ollama.com)" }
$dossier = $exe.DirectoryName                                   # ...\AppData\Local\Programs\Ollama
$local = Split-Path (Split-Path $dossier)                       # ...\AppData\Local
$profil = Split-Path (Split-Path $local)                        # <lecteur systeme>\Users\<nom>
$installeur = Join-Path $local 'Ollama\OllamaSetup.exe'
$journal = Join-Path $local 'Ollama\reparation.log'
$config = Join-Path $racine 'config\services.json'
if (-not (Test-Path $installeur)) { throw "Installeur introuvable : $installeur (le telecharger depuis ollama.com)" }

function Ecrire-Actif([bool]$actif) {
    $j = Get-Content $config -Raw -Encoding UTF8 | ConvertFrom-Json
    $liste = if ($j.services) { $j.services } else { $j }
    foreach ($s in $liste) {
        if ($s.nom -eq 'ollama') {
            if ($s.PSObject.Properties.Name -contains 'actif') { $s.actif = $actif }
            else { $s | Add-Member -NotePropertyName actif -NotePropertyValue $actif }
        }
    }
    $j | ConvertTo-Json -Depth 20 | Set-Content $config -Encoding UTF8
}

$sauvegarde = "$config.avant_reparation_ollama"
Copy-Item $config $sauvegarde -Force
Write-Host "1/5 Pause du service ollama dans le superviseur (sauvegarde : $sauvegarde)"
Ecrire-Actif $false
Start-Sleep -Seconds 20

try {
    Write-Host "2/5 Arret d'Ollama"
    Get-Process 'ollama', 'ollama app' -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 3
    if (Get-Process 'ollama' -ErrorAction SilentlyContinue) { throw "ollama.exe tourne encore" }

    $v = (Get-Item $installeur).VersionInfo.ProductVersion
    Write-Host "3/5 Installation d'Ollama $v (silencieuse, 1 a 3 min)"
    # Pas de « -Wait » : il attend aussi tout ce que l'installeur lance, donc
    # l'application de la barre des taches, qui ne se ferme jamais. Le script
    # restait fige ici, supervision d'Ollama en pause (11/11/2026, 48 minutes).
    # WaitForExit n'attend que l'installeur lui-meme, 10 minutes au plus.
    $p = Start-Process $installeur -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/LOG=$journal" -PassThru
    if (-not $p.WaitForExit(600000)) { throw "installeur toujours en cours apres 10 minutes (voir $journal)" }
    if ($p.ExitCode -ne 0) { throw "installeur : code $($p.ExitCode) (voir $journal)" }
    # L'installeur lance l'application de la barre des taches : on la referme,
    # c'est le superviseur qui sert l'API.
    Start-Sleep -Seconds 5
    Get-Process 'ollama', 'ollama app' -ErrorAction SilentlyContinue | Stop-Process -Force

    Write-Host "4/5 Verification des moteurs"
    $moteurs = Get-ChildItem "$dossier\lib\ollama" -Recurse -Include 'llama-server.exe', 'ggml-cuda*.dll' -ErrorAction SilentlyContinue
    if (-not $moteurs) { throw "lib\ollama toujours vide apres installation" }
    $moteurs | ForEach-Object { Write-Host "   $($_.FullName)" }
}
finally {
    Write-Host "5/5 Le superviseur reprend la main sur ollama (configuration d'origine restauree)"
    Copy-Item $sauvegarde $config -Force
}

if ($SansMiseAJourAuto) {
    foreach ($t in 'Ollama_now2', 'Ollama_superviseur') {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Disable-ScheduledTask -TaskName $t | Out-Null
            Write-Host "Tache $t desactivee (reactivable : Enable-ScheduledTask -TaskName $t)"
        }
    }
    # L'application se lance aussi a l'ouverture de session par un raccourci du
    # dossier Demarrage : c'est lui qui a relance la mise a jour ratee au
    # redemarrage du 11/11/2026 (0.40.16, lib\ollama de nouveau videe). On le
    # met de cote ; le superviseur de Neo lance `ollama serve` sans lui.
    $raccourci = Join-Path $profil 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\Ollama.lnk'
    if (Test-Path $raccourci) {
        $cote = "$racine\_sauvegardes\ollama_demarrage_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        New-Item -ItemType Directory -Force -Path $cote | Out-Null
        Move-Item $raccourci $cote
        Write-Host "Raccourci de demarrage mis de cote : $cote\Ollama.lnk (le remettre dans le dossier Demarrage pour revenir en arriere)"
    }
}

Write-Host "Attente de l'API (jusqu'a 3 min)..."
for ($i = 0; $i -lt 36; $i++) {
    Start-Sleep -Seconds 5
    try {
        $r = Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 3
        Write-Host "OK : Ollama $($r.version) repond."
        exit 0
    } catch {}
}
Write-Warning "Ollama ne repond pas encore ; voir $racine\logs\ollama_superviseur.log"
