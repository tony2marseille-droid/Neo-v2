# restaurer_taches.ps1 - remet les taches planifiees sauvegardees par taches_silencieuses.ps1.
#
# Usage (console administrateur) :
#   powershell -ExecutionPolicy Bypass -File E:\neo-repo\code\scripts\restaurer_taches.ps1 -Sauvegarde E:\ia\_sauvegardes\taches_AAAAMMJJ_HHMMSS

param([Parameter(Mandatory = $true)][string]$Sauvegarde)

$ErrorActionPreference = 'Stop'
foreach ($f in Get-ChildItem $Sauvegarde -Filter '*.xml') {
    $nom = [IO.Path]::GetFileNameWithoutExtension($f.Name)
    $t = Get-ScheduledTask -TaskName $nom -ErrorAction SilentlyContinue
    $chemin = if ($t) { $t.TaskPath } else { '\' }
    Register-ScheduledTask -TaskName $nom -TaskPath $chemin -Xml (Get-Content $f.FullName -Raw) -Force | Out-Null
    Write-Host "  restauree : $nom"
}
