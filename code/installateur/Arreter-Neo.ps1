<#
.SYNOPSIS
    Arrete Neo : le superviseur, le serveur et les services Python lances depuis ce dossier.
    Ollama n'est pas arrete (il peut servir a d'autres applications).
#>
$racine = $PSScriptRoot
$bin = Join-Path $racine 'bin'
foreach ($nom in 'localai-watch', 'localai-server') {
    Get-Process $nom -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($bin, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Stop-Process -Id $_.Id -Force; Write-Host "arrete : $nom ($($_.Id))" }
}
$venv = Join-Path $racine '.venv'
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($venv, [StringComparison]::OrdinalIgnoreCase)) -or
        ($_.CommandLine -and $_.CommandLine.IndexOf($racine, [StringComparison]::OrdinalIgnoreCase) -ge 0)
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "arrete : python ($($_.ProcessId))" }
