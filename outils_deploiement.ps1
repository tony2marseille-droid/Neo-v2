# outils_deploiement.ps1 - fonctions communes a deployer.ps1, verifier_ecart.ps1
# et annuler_deploiement.ps1. A charger par dot-sourcing : . "$PSScriptRoot\outils_deploiement.ps1"
#
# Les trois scripts lisent la meme table (correspondances.psd1) et appliquent
# les memes exclusions : ce qui est copie est exactement ce qui est compare.

$script:DossiersExclus = @('__pycache__', '.venv', 'node_modules', '.pytest_cache')
$script:FichiersExclus = @('*.pyc', '*.log', '*.bak', '*.bak_*', '*.bak-*', '*.orig', '*.rej')

# Entrees de correspondances.psd1 avec chemins absolus.
function Lire-Correspondances([string]$depot) {
    $table = Import-PowerShellDataFile (Join-Path $depot 'correspondances.psd1')
    $code = Join-Path $depot 'code'
    foreach ($m in $table.Miroirs) {
        [pscustomobject]@{ Type = 'miroir'; Depot = (Join-Path $code $m.Depot); Vivant = $m.Vivant }
    }
    foreach ($f in $table.Fichiers) {
        [pscustomobject]@{ Type = 'fichier'; Depot = (Join-Path $code $f.Depot); Vivant = $f.Vivant }
    }
}

function Est-Exclu([string]$relatif) {
    $parties = $relatif -split '[\\/]'
    foreach ($p in $parties[0..($parties.Count - 2)]) { if ($script:DossiersExclus -contains $p) { return $true } }
    foreach ($motif in $script:FichiersExclus) { if ($parties[-1] -like $motif) { return $true } }
    return $false
}

# Fichiers d'un dossier (hors exclusions), indexes par chemin relatif.
function Lister-Fichiers([string]$dossier) {
    $index = @{}
    if (-not (Test-Path -LiteralPath $dossier)) { return $index }
    $base = (Resolve-Path -LiteralPath $dossier).Path.TrimEnd('\').Length + 1
    Get-ChildItem -LiteralPath $dossier -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $rel = $_.FullName.Substring($base)
        if (-not (Est-Exclu $rel)) { $index[$rel] = $_.FullName }
    }
    return $index
}

function Empreinte([string]$chemin) { (Get-FileHash -LiteralPath $chemin -Algorithm SHA256).Hash }

# Differences depot <-> production. Etat : DIFFERENT, DEPOT_SEUL, PROD_SEULE.
function Comparer-Ecart($entrees) {
    foreach ($e in $entrees) {
        if ($e.Type -eq 'fichier') {
            $a = Test-Path -LiteralPath $e.Depot; $b = Test-Path -LiteralPath $e.Vivant
            if ($a -and -not $b) { [pscustomobject]@{ Etat = 'DEPOT_SEUL'; Depot = $e.Depot; Vivant = $e.Vivant } }
            elseif ($b -and -not $a) { [pscustomobject]@{ Etat = 'PROD_SEULE'; Depot = $e.Depot; Vivant = $e.Vivant } }
            elseif ($a -and (Empreinte $e.Depot) -ne (Empreinte $e.Vivant)) {
                [pscustomobject]@{ Etat = 'DIFFERENT'; Depot = $e.Depot; Vivant = $e.Vivant }
            }
            continue
        }
        $d = Lister-Fichiers $e.Depot; $v = Lister-Fichiers $e.Vivant
        foreach ($rel in (@($d.Keys) + @($v.Keys) | Sort-Object -Unique)) {
            $cd = Join-Path $e.Depot $rel; $cv = Join-Path $e.Vivant $rel
            if (-not $v.ContainsKey($rel)) { [pscustomobject]@{ Etat = 'DEPOT_SEUL'; Depot = $cd; Vivant = $cv } }
            elseif (-not $d.ContainsKey($rel)) { [pscustomobject]@{ Etat = 'PROD_SEULE'; Depot = $cd; Vivant = $cv } }
            elseif ((Empreinte $d[$rel]) -ne (Empreinte $v[$rel])) { [pscustomobject]@{ Etat = 'DIFFERENT'; Depot = $cd; Vivant = $cv } }
        }
    }
}

function Copier-Dossier([string]$src, [string]$dst) {
    $opts = @($src, $dst, '/MIR', '/NFL', '/NDL', '/NJH', '/NJS', '/R:1', '/W:1', '/XD') + $script:DossiersExclus + @('/XF') + $script:FichiersExclus
    & robocopy @opts | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy a echoue ($LASTEXITCODE) : $src -> $dst" }
}

function Copier-Fichier([string]$src, [string]$dst) {
    $d = Split-Path -Parent $dst
    if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    Copy-Item -LiteralPath $src -Destination $dst -Force
}

# Copie une entree du depot vers la production.
function Copier-Entree($e) {
    if ($e.Type -eq 'miroir') { Copier-Dossier $e.Depot $e.Vivant } else { Copier-Fichier $e.Depot $e.Vivant }
}

# Emplacement d'un chemin de production dans une sauvegarde : E:\ia\x -> <sauvegarde>\fichiers\E\ia\x
function Chemin-Sauvegarde([string]$sauvegarde, [string]$vivant) {
    Join-Path (Join-Path $sauvegarde 'fichiers') ($vivant -replace ':', '')
}

function Sauvegarder-Chemin([string]$sauvegarde, [string]$vivant) {
    if (-not (Test-Path -LiteralPath $vivant)) { return }
    $cible = Chemin-Sauvegarde $sauvegarde $vivant
    if (Test-Path -LiteralPath $vivant -PathType Container) { Copier-Dossier $vivant $cible } else { Copier-Fichier $vivant $cible }
}
