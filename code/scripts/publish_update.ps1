param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Notes = "",
    [string]$BaseUrl = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "== 1/4 Build frontend ==" -ForegroundColor Cyan
Push-Location frontend
npm run build
Pop-Location

Write-Host "== 2/4 Build exe PyInstaller ==" -ForegroundColor Cyan
& .\scripts\build_exe.ps1

Write-Host "== 3/4 Archive ==" -ForegroundColor Cyan
$outDir = Join-Path (Get-Location) "updates"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$zipName = "LocalAI-$Version-win64.zip"
$zipPath = Join-Path $outDir $zipName
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path ".\dist\LocalAI\*" -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host "== 4/4 Manifeste ==" -ForegroundColor Cyan
$hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item $zipPath).Length
$url = if ($BaseUrl) { "$BaseUrl/$zipName" } else { $zipName }
$manifest = [ordered]@{
    version    = $Version
    url        = $url
    sha256     = $hash
    size_bytes = $size
    notes      = $Notes
    published_at = (Get-Date).ToUniversalTime().ToString("o")
}
$manifest | ConvertTo-Json | Set-Content (Join-Path $outDir "update_manifest.json") -Encoding UTF8

Write-Host ""
Write-Host "Pret :" -ForegroundColor Green
Write-Host "  $zipPath ($([math]::Round($size/1MB,1)) Mo)"
Write-Host "  $(Join-Path $outDir 'update_manifest.json')"
Write-Host ""
Write-Host "Publiez le dossier 'updates\' sur n'importe quel hebergeur statique"
Write-Host "(GitHub Pages, S3, share LAN...). Puis sur chaque poste client,"
Write-Host "renseignez l'URL du manifeste dans LocalAI > Reglages > Mises a jour :"
if ($BaseUrl) { Write-Host "  $BaseUrl/update_manifest.json" -ForegroundColor Yellow }
else { Write-Host "  <votre-hebergeur>/update_manifest.json" -ForegroundColor Yellow }
