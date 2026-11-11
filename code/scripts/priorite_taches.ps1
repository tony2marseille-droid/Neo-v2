# priorite_taches.ps1 - les services de Neo et Ollama tournent en priorite NORMALE.
#
# Usage (console PowerShell ADMINISTRATEUR : une tache appartient a SYSTEM) :
#   powershell -ExecutionPolicy Bypass -File <depot>\code\scripts\priorite_taches.ps1            # simulation
#   powershell -ExecutionPolicy Bypass -File <depot>\code\scripts\priorite_taches.ps1 -Executer  # application
#
# Probleme : une tache planifiee recoit par defaut la priorite 7. Windows lance
# alors le programme, et tout ce qu'il lance a son tour, en classe « inferieure
# a la normale », avec une priorite de lecture disque BASSE et une priorite
# memoire de 2 sur 5 (mesure sur Ollama le 11/11/2026). Le serveur de Neo
# (LocalAI_watch_rs) et Ollama (Ollama_superviseur) sont lances ainsi : chaque
# chargement de modele, chaque lecture de gros fichier et chaque copie memoire
# vive -> carte graphique en patissent. Mesures : un clip video de 3 s en
# 410 a 700 s par le service contre 114 s depuis une console ; fichier de
# Gemma lu en 22 a 55 s contre 4 s.
# Solution : priorite 4 (classe normale, disque et memoire normaux) pour les
# taches qui font tourner un service. Les taches d'entrainement ou de
# maintenance gardent leur priorite basse, voulue.
# Le XML de chaque tache est sauvegarde avant modification (retour arriere :
# restaurer_taches.ps1). Idempotent. Le nouveau reglage vaut au PROCHAIN
# demarrage de la tache : redemarrer le PC, ou relancer les taches.

param(
    [switch]$Executer,
    [string[]]$Taches = @('LocalAI_watch_rs', 'LocalAI_server_boot', 'NeoVideoAPI', 'Ollama_superviseur', 'Ollama_now2'),
    [ValidateRange(4, 6)][int]$Priorite = 4
)

$ErrorActionPreference = 'Stop'
# Reglages : neo.toml (NeoConfig.psm1), sinon LOCALAI_ROOT, sinon le dossier parent du script.
$racine = Split-Path -Parent $PSScriptRoot
$module = Join-Path $PSScriptRoot 'NeoConfig.psm1'
if (Test-Path $module) {
    Import-Module $module -Force
    $racine = Import-NeoConfig -Depart $racine
} elseif ($env:LOCALAI_ROOT) { $racine = $env:LOCALAI_ROOT }
$sauvegarde = "$racine\_sauvegardes\taches_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Executer -and -not $admin) { throw 'console administrateur requise (tache SYSTEM).' }
if ($Executer) { New-Item -ItemType Directory -Force -Path $sauvegarde | Out-Null }

$modifiees = 0
foreach ($nom in $Taches) {
    $t = Get-ScheduledTask -TaskName $nom -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host ("  {0,-22} absente" -f $nom); continue }
    $actuelle = $t.Settings.Priority
    if ($actuelle -ge 4 -and $actuelle -le 6) {
        Write-Host ("  {0,-22} deja en priorite normale ({1})" -f $nom, $actuelle)
        continue
    }
    Write-Host ("  {0,-22} priorite {1} -> {2}   (etat : {3})" -f $nom, $actuelle, $Priorite, $t.State)
    if (-not $Executer) { continue }
    try {
        Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath |
            Out-File "$sauvegarde\$($t.TaskName).xml" -Encoding unicode
        $t.Settings.Priority = $Priorite
        Set-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Settings $t.Settings | Out-Null
        $modifiees++
    } catch {
        Write-Host ("     echec : {0}" -f $_.Exception.Message) -ForegroundColor Yellow
    }
}

if ($Executer) {
    Write-Host ""
    Write-Host "$modifiees tache(s) modifiee(s). Sauvegarde : $sauvegarde"
    Write-Host "Le reglage s'applique au prochain demarrage de chaque tache :"
    Write-Host "  - le plus simple : redemarrer le PC ;"
    Write-Host "  - sinon : relancer deployer.ps1 -Executer (Neo), puis quitter et relancer Ollama."
} else {
    Write-Host ""
    Write-Host "Simulation : rien n'a ete modifie. Ajouter -Executer pour appliquer."
}
