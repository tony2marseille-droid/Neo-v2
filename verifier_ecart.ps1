# verifier_ecart.ps1 - signale toute difference entre le depot et la production.
#
# Usage :
#   powershell -ExecutionPolicy Bypass -File E:\neo-repo\verifier_ecart.ps1
#   ... -Rapatrier   # recopie dans code/ les fichiers modifies ou crees en production
#
# Le depot est la source unique : un ecart veut dire qu'un fichier a ete modifie
# directement en production (ou que le dernier commit n'est pas encore deploye).
#   - modification voulue  -> -Rapatrier, relire "git diff", commiter, redeployer ;
#   - modification parasite -> redeployer (deployer.ps1 -Executer) l'ecrase.
# -Rapatrier ne supprime rien et ne commite rien.
# Code de sortie : 0 si aucun ecart, 1 sinon.

param([switch]$Rapatrier)

$ErrorActionPreference = 'Stop'
$depot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $depot 'outils_deploiement.ps1')

$trace = 'E:\ia\DEPLOIEMENT.json'
$head = (& git -C $depot rev-parse --short HEAD).Trim()
if (Test-Path $trace) {
    $d = Get-Content $trace -Raw | ConvertFrom-Json
    $note = if ($d.commit -eq $head) { 'identique a HEAD' } else { "HEAD = $head : commit non deploye" }
    Write-Host "Deploye : commit $($d.commit) le $($d.date) ($note)"
} else {
    Write-Host "Deploye : inconnu ($trace absent : aucun deploiement depuis la mise en place du suivi)"
}

$ecarts = @(Comparer-Ecart (Lire-Correspondances $depot))
if ($ecarts.Count -eq 0) {
    Write-Host 'Aucun ecart : la production correspond exactement au depot.'
    exit 0
}

$libelles = @{ DIFFERENT = 'modifie  '; PROD_SEULE = 'prod seule'; DEPOT_SEUL = 'depot seul' }
foreach ($e in $ecarts) { Write-Host ("  {0}  {1}" -f $libelles[$e.Etat], $e.Vivant) }
Write-Host ''
Write-Host "$($ecarts.Count) ecart(s)."

if ($Rapatrier) {
    foreach ($e in $ecarts | Where-Object { $_.Etat -ne 'DEPOT_SEUL' }) {
        Copier-Fichier $e.Vivant $e.Depot
        Write-Host "  rapatrie : $($e.Depot.Substring($depot.Length + 1))"
    }
    Write-Host ''
    Write-Host 'Relire avec "git diff" (et "git status" pour les nouveaux fichiers), puis commiter et redeployer.'
} else {
    Write-Host 'Modification voulue : relancer avec -Rapatrier. Sinon : deployer.ps1 -Executer la remplace.'
}
exit 1
