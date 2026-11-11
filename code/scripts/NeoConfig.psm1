# NeoConfig.psm1 - lecture de neo.toml pour les scripts PowerShell de Neo.
#
# Meme regles que localai-core/src/neoconf.rs et backend/core/reglages.py :
# une variable d'environnement deja definie n'est jamais ecrasee ; sinon la
# valeur de neo.toml s'applique ; sinon le script utilise un defaut relatif a
# la racine. Recherche : NEO_CONFIG, puis <LOCALAI_ROOT>\neo.toml, puis le
# dossier de depart et ses parents.
#
# Usage :
#   Import-Module "$PSScriptRoot\scripts\NeoConfig.psm1" -Force
#   $racine = Import-NeoConfig -Depart $PSScriptRoot

$Correspondances = @(
    @('chemins', 'racine', @('LOCALAI_ROOT')),
    @('chemins', 'donnees', @('LOCALAI_DATA', 'LOCALAI_DATA_DIR')),
    @('chemins', 'journal', @('LOCALAI_LOG')),
    @('chemins', 'espace_travail', @('NEO_ESPACE_TRAVAIL')),
    @('chemins', 'sd', @('LOCALAI_SD_DIR')),
    @('chemins', 'retouche', @('NEO_SD_RETOUCHE')),
    @('chemins', 'esrgan', @('LOCALAI_ESRGAN_DIR')),
    @('chemins', 'voix', @('NEO_VOIX_DIR')),
    @('chemins', 'parakeet', @('NEO_PARAKEET_DIR')),
    @('chemins', 'hunyuan', @('LOCALAI_HUNYUAN21_DIR')),
    @('chemins', 'modele_visages', @('NEO_YUNET')),
    @('chemins', 'cache_hf', @('HF_HOME')),
    @('reseau', 'hote', @('LOCALAI_HOST')),
    @('reseau', 'port', @('LOCALAI_PORT')),
    @('ollama', 'url', @('OLLAMA_URL')),
    @('ollama', 'executable', @('OLLAMA_BIN')),
    @('ollama', 'modeles', @('OLLAMA_MODELS')),
    @('outils', 'python', @('NEO_PYTHON')),
    @('outils', 'ffmpeg', @('NEO_FFMPEG')),
    @('outils', 'blender', @('LOCALAI_BLENDER')),
    @('outils', 'openscad', @('LOCALAI_OPENSCAD')),
    @('outils', 'trancheur', @('LOCALAI_SLICER')),
    @('materiel', 'palier_force', @('LOCALAI_FORCE_TIER'))
)

function Find-NeoToml {
    param([string]$Depart)
    if ($env:NEO_CONFIG) {
        if (Test-Path -LiteralPath $env:NEO_CONFIG -PathType Leaf) { return $env:NEO_CONFIG }
        return $null
    }
    if ($env:LOCALAI_ROOT) {
        $p = Join-Path $env:LOCALAI_ROOT 'neo.toml'
        if (Test-Path -LiteralPath $p -PathType Leaf) { return $p }
    }
    $d = $Depart
    while ($d) {
        $p = Join-Path $d 'neo.toml'
        if (Test-Path -LiteralPath $p -PathType Leaf) { return $p }
        $parent = Split-Path -Parent $d
        if ($parent -eq $d) { break }
        $d = $parent
    }
    return $null
}

# Lecteur TOML minimal : [section], cle = "texte" | 'texte' | nombre | booleen.
function Read-NeoToml {
    param([string]$Chemin)
    $doc = @{}
    $section = ''
    foreach ($brute in Get-Content -LiteralPath $Chemin -Encoding UTF8) {
        $ligne = $brute.Trim()
        if (-not $ligne -or $ligne.StartsWith('#')) { continue }
        if ($ligne -match '^\[([A-Za-z0-9_]+)\]$') { $section = $Matches[1]; $doc[$section] = @{}; continue }
        if ($ligne -match '^([A-Za-z0-9_]+)\s*=\s*(.+)$') {
            $cle = $Matches[1]; $v = $Matches[2].Trim()
            if ($v -match '^"((?:[^"\\]|\\.)*)"') { $v = $Matches[1] -replace '\\\\', '\' -replace '\\"', '"' }
            elseif ($v -match "^'([^']*)'") { $v = $Matches[1] }
            else { $v = ($v -split '#')[0].Trim() }
            if (-not $doc.ContainsKey($section)) { $doc[$section] = @{} }
            $doc[$section][$cle] = $v
        }
    }
    return $doc
}

function Import-NeoConfig {
    param([string]$Depart = $PWD.Path)
    $fichier = Find-NeoToml -Depart $Depart
    if ($fichier) {
        $doc = Read-NeoToml -Chemin $fichier
        $racine = $null
        if ($doc.ContainsKey('chemins')) { $racine = $doc['chemins']['racine'] }
        if (-not $racine) { $racine = Split-Path -Parent $fichier }
        if (-not $env:LOCALAI_ROOT) { $env:LOCALAI_ROOT = $racine }
        foreach ($c in $Correspondances) {
            if (-not $doc.ContainsKey($c[0]) -or $c[1] -eq 'racine') { continue }
            $v = $doc[$c[0]][$c[1]]
            if (-not $v) { continue }
            $v = $v.Replace('{racine}', $racine)
            foreach ($nom in $c[2]) {
                if (-not [Environment]::GetEnvironmentVariable($nom, 'Process')) {
                    [Environment]::SetEnvironmentVariable($nom, $v, 'Process')
                }
            }
        }
        if ($doc.ContainsKey('variables')) {
            foreach ($nom in $doc['variables'].Keys) {
                if (-not [Environment]::GetEnvironmentVariable($nom, 'Process')) {
                    [Environment]::SetEnvironmentVariable($nom, $doc['variables'][$nom].Replace('{racine}', $racine), 'Process')
                }
            }
        }
    }
    if ($env:LOCALAI_ROOT) { return $env:LOCALAI_ROOT }
    return $Depart
}

Export-ModuleMember -Function Import-NeoConfig, Find-NeoToml, Read-NeoToml
