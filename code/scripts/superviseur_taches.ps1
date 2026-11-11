# superviseur_taches.ps1 - bascule vers le superviseur unique (localai-watch).
#
# Usage (console PowerShell ADMINISTRATEUR, APRES un deploiement qui contient le superviseur) :
#   powershell -ExecutionPolicy Bypass -File E:\neo-repo\code\scripts\superviseur_taches.ps1            # simulation
#   powershell -ExecutionPolicy Bypass -File E:\neo-repo\code\scripts\superviseur_taches.ps1 -Executer  # application
#
# Les services sont desormais lances et relances par localai-watch d'apres
# E:\ia\config\services.json. Ce script :
#   1. sauvegarde le XML des taches concernees ;
#   2. DESACTIVE (sans les supprimer) les taches de lancement et les anciens
#      chiens de garde, qui feraient double emploi avec le superviseur ;
#   3. regle la tache du superviseur pour qu'elle se relance si elle s'arrete.
# Retour arriere : restaurer_taches.ps1 -Sauvegarde <dossier affiche a la fin>
# (reimporte les XML : taches reactivees, reglages d'origine).

param([switch]$Executer)

$ErrorActionPreference = 'Stop'
$sauvegarde = "E:\ia\_sauvegardes\taches_superviseur_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'console administrateur requise (certaines taches appartiennent a SYSTEM).' }

# Remplacees par le superviseur. Ollama_superviseur (application Ollama a
# l'ouverture de session) reste active : le superviseur l'adopte.
$remplacees = [ordered]@{
    'LocalAI_server_boot'   = 'lancement du serveur (service "serveur")'
    'LocalAI_ollama_boot'   = 'relance d''Ollama au demarrage (service "ollama")'
    'Neo_Toolshim'          = 'lancement du toolshim (service "toolshim")'
    'NeoVideoAPI'           = 'lancement de l''API video (service "video")'
    'Neo_Rerank'            = 'lancement du rerank (service "rerank")'
    'Neo_GardeProd'         = 'ancien chien de garde toutes les 2 min'
    'Neo_Toolshim_Watchdog' = 'ancien chien de garde toutes les 5 min'
}

$watch = Get-ScheduledTask -TaskName 'LocalAI_watch_rs' -ErrorAction SilentlyContinue
if (-not $watch) { throw 'tache LocalAI_watch_rs absente : le superviseur ne demarrerait pas au boot.' }
$etat = 'E:\ia\logs\services_etat.json'
if (-not (Test-Path $etat) -or ((Get-Date) - (Get-Item $etat).LastWriteTime).TotalSeconds -gt 90) {
    throw "le superviseur ne tourne pas ($etat absent ou ancien) : deployer d'abord la version qui le contient."
}

if ($Executer) { New-Item -ItemType Directory -Force -Path $sauvegarde | Out-Null }
Write-Host ''
Write-Host '=== Taches remplacees par le superviseur ==='
foreach ($nom in $remplacees.Keys) {
    $t = Get-ScheduledTask -TaskName $nom -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host ("  {0,-22} absente" -f $nom); continue }
    if ($t.State -eq 'Disabled') { Write-Host ("  {0,-22} deja desactivee" -f $nom); continue }
    Write-Host ("  {0,-22} -> desactivee ({1})" -f $nom, $remplacees[$nom])
    if ($Executer) {
        Export-ScheduledTask -TaskName $nom -TaskPath $t.TaskPath | Out-File "$sauvegarde\$nom.xml" -Encoding unicode
        # Une tache en cours (ex. Neo_Rerank) : on arrete la TACHE, le processus
        # du service continue et le superviseur l'adopte.
        Disable-ScheduledTask -TaskName $nom -TaskPath $t.TaskPath | Out-Null
    }
}

Write-Host ''
Write-Host '=== Superviseur (LocalAI_watch_rs) ==='
Write-Host '  relance automatique : toutes les minutes en cas d''arret, sans limite de duree'
if ($Executer) {
    Export-ScheduledTask -TaskName 'LocalAI_watch_rs' -TaskPath $watch.TaskPath | Out-File "$sauvegarde\LocalAI_watch_rs.xml" -Encoding unicode
    $s = $watch.Settings
    $s.RestartCount = 999
    $s.RestartInterval = 'PT1M'
    $s.ExecutionTimeLimit = 'PT0S'
    Set-ScheduledTask -TaskName 'LocalAI_watch_rs' -TaskPath $watch.TaskPath -Settings $s | Out-Null
}

Write-Host ''
if ($Executer) {
    Write-Host "Termine. Sauvegardes XML : $sauvegarde"
    Write-Host "Retour arriere : restaurer_taches.ps1 -Sauvegarde $sauvegarde"
} else {
    Write-Host 'Simulation : relancer avec -Executer pour appliquer.'
}
