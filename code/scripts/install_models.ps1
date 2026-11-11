# Installation des modeles recommandes pour ce PC.
# Usage : .\install_models.ps1            (palier auto-detecte)
#         .\install_models.ps1 -Tier T3   (forcer un palier)

param(
    [ValidateSet("T1", "T2", "T3", "auto")]
    [string]$Tier = "auto"
)

$ErrorActionPreference = "Stop"

if ($Tier -eq "auto") {
    $gpu = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null
    if (-not $gpu) {
        Write-Host "Aucun GPU NVIDIA detecte -> palier T1" -ForegroundColor Yellow
        $Tier = "T1"
    } else {
        $vramMb = ($gpu | ForEach-Object { [int]($_ -split ",")[1] } | Measure-Object -Maximum).Maximum
        if ($vramMb -ge 22000) { $Tier = "T3" }
        elseif ($vramMb -ge 10000) { $Tier = "T2" }
        else { $Tier = "T1" }
        Write-Host "GPU: $gpu  VRAM max: $($vramMb) Mo -> palier $Tier" -ForegroundColor Cyan
    }
}

$plans = @{
    T1 = @("qwen3:4b", "qwen2.5-coder:7b", "qwen3:8b", "qwen2.5vl:7b", "qwen3:14b", "nomic-embed-text")
    T2 = @("qwen3:14b", "qwen2.5-coder:14b", "qwen2.5vl:7b", "llama3.2-vision:11b", "qwen3:8b", "nomic-embed-text")
    T3 = @("qwen3:30b", "qwen2.5-coder:32b", "qwen2.5vl:7b", "qwen2.5vl:14b", "qwen3:8b", "mxbai-embed-large", "nomic-embed-text")
}

$deja = (& ollama list) -join "`n"
foreach ($model in $plans[$Tier]) {
    $nomCourt = ($model -split ":")[0]
    if ($deja -match [regex]::Escape($nomCourt)) {
        Write-Host "DEJA INSTALLE (ou variante) : $model" -ForegroundColor DarkGray
        continue
    }
    Write-Host "Telechargement : $model ..." -ForegroundColor Green
    & ollama pull $model
}

Write-Host ""
Write-Host "Termine. Demarre LocalAI : il detectera le palier $Tier automatiquement." -ForegroundColor Cyan
