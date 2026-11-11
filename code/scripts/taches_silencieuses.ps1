# taches_silencieuses.ps1 - les taches planifiees de Neo tournent sans aucune fenetre.
#
# Usage (console PowerShell ADMINISTRATEUR : certaines taches appartiennent a SYSTEM) :
#   powershell -ExecutionPolicy Bypass -File E:\neo-repo\code\scripts\taches_silencieuses.ps1            # simulation
#   powershell -ExecutionPolicy Bypass -File E:\neo-repo\code\scripts\taches_silencieuses.ps1 -Executer  # application
#
# Probleme : les taches « LocalAI_* » et « Neo_* » lancent powershell.exe, des .bat
# ou python.exe dans la session ouverte : une console s'affiche (toutes les 2 min
# pour Neo_GardeProd, toutes les 5 min pour le chien de garde du toolshim...).
# Solution : chaque action passe par neo-silencieux.exe (application sans
# console, enfant lance avec CREATE_NO_WINDOW). Le XML de chaque tache est
# sauvegarde avant modification ; retour arriere : restaurer_taches.ps1.
# Idempotent : une tache deja convertie est ignoree.

param([switch]$Executer)

$ErrorActionPreference = 'Stop'
$source = 'E:\neo-repo\code\localai-rs\target\release\neo-silencieux.exe'
$lanceur = 'E:\ia\tools\neo-silencieux.exe'
$sauvegarde = "E:\ia\_sauvegardes\taches_$(Get-Date -Format 'yyyyMMdd_HHmmss')"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Executer -and -not $admin) { throw 'console administrateur requise (taches SYSTEM).' }
if ($Executer) {
    if (-not (Test-Path $source)) { throw "lanceur absent : $source (cargo build --release -p neo-silencieux)" }
    Copy-Item $source $lanceur -Force
    New-Item -ItemType Directory -Force -Path $sauvegarde | Out-Null
}

function Quote([string]$a) {
    if ($a -match '[\s"]') { '"' + ($a -replace '"', '\"') + '"' } else { $a }
}

# Les taches du compte SYSTEM tournent en session 0 : jamais visibles, inutile
# (et genant pour l'arret du chien de garde) de les envelopper.
$taches = Get-ScheduledTask | Where-Object {
    $_.TaskName -match '^(LocalAI|Neo)' -and $_.Principal.LogonType -ne 'ServiceAccount'
}
foreach ($t in $taches) {
    $nouvelles = @()
    $aConvertir = $false
    foreach ($a in $t.Actions) {
        $exe = [Environment]::ExpandEnvironmentVariables($a.Execute.Trim('"'))
        if ($exe -ieq $lanceur -or $exe -like '*neo-silencieux.exe') { $nouvelles += $a; continue }
        $aConvertir = $true
        # Un .bat/.cmd s'execute via cmd.exe /c ; le reste est lance tel quel.
        $prefixe = if ($exe -match '\.(bat|cmd)$') { "cmd.exe /c $(Quote $exe)" } else { Quote $exe }
        $args = ("$prefixe $($a.Arguments)").Trim()
        $p = @{ Execute = $lanceur; Argument = $args }
        if ($a.WorkingDirectory) { $p.WorkingDirectory = $a.WorkingDirectory }
        $nouvelles += New-ScheduledTaskAction @p
    }
    if (-not $aConvertir) { Write-Host "  deja silencieuse : $($t.TaskName)"; continue }
    Write-Host ("  {0,-26} -> {1} {2}" -f $t.TaskName, (Split-Path $lanceur -Leaf), $nouvelles[0].Arguments)
    if ($Executer) {
        Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Out-File "$sauvegarde\$($t.TaskName).xml" -Encoding unicode
        Set-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Action $nouvelles | Out-Null
    }
}
if ($Executer) {
    Write-Host ''
    Write-Host "Termine. Sauvegardes XML : $sauvegarde"
    Write-Host "Retour arriere : restaurer_taches.ps1 -Sauvegarde $sauvegarde"
} else {
    Write-Host ''
    Write-Host 'Simulation : relancer avec -Executer (console administrateur) pour appliquer.'
}
