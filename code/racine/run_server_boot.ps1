$ErrorActionPreference = 'SilentlyContinue'
# Lancement MANUEL de secours du serveur (idempotent). En production, c'est le
# superviseur (localai-watch, config/services.json) qui lance et relance le serveur.
# CUTOVER RUST 2026-10-14 : lance le binaire Rust (release) au lieu de Python.
# Repli : restaurer run_server_boot.ps1.bak_py et relancer (Python intact sur <racine>).
# Reglages : neo.toml (scripts\NeoConfig.psm1), sinon LOCALAI_ROOT, sinon le dossier du script.
$racine = $PSScriptRoot
$module = Join-Path $PSScriptRoot 'scripts\NeoConfig.psm1'
if (Test-Path $module) {
    Import-Module $module -Force
    $racine = Import-NeoConfig -Depart $racine
} elseif ($env:LOCALAI_ROOT) { $racine = $env:LOCALAI_ROOT }
$journaux = Join-Path $racine 'logs'
$log = "$journaux\server_boot.log"
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') server_boot: demarrage (rust)" | Out-File $log -Append -Encoding utf8
$c = Get-NetTCPConnection -LocalPort 8320 -State Listen -ErrorAction SilentlyContinue
if ($c) {
    "deja en ecoute sur 8320 (PID $($c.OwningProcess)) - rien a faire" | Out-File $log -Append -Encoding utf8
    exit 0
}
# Executable du serveur : NEO_SERVEUR_EXE (neo.toml), sinon <racine>\bin\localai-server.exe.
$bin = if ($env:NEO_SERVEUR_EXE) { $env:NEO_SERVEUR_EXE } else { Join-Path $racine 'bin\localai-server.exe' }
# Dossier de travail : celui de l'executable ; pour un binaire compile sur place
# (<espace Cargo>\target\release), la racine de l'espace de travail Cargo.
$dossierServeur = Split-Path -Parent $bin
if ($dossierServeur -match '[\\/]target[\\/]release$') { $dossierServeur = Split-Path -Parent (Split-Path -Parent $dossierServeur) }
if (-not (Test-Path $bin)) {
    "binaire rust absent: $bin" | Out-File $log -Append -Encoding utf8
    exit 1
}
$env:LOCALAI_PORT = '8320'
$env:LOCALAI_ROOT = $racine
# toolshim (proxy d'emulation tool-calling) : les modeles locaux sans
# tool calling natif passent par 8330 ; toolshim relaie le reste vers 11434.
# Retirer cette ligne pour revenir a Ollama direct.
$env:OLLAMA_URL = 'http://127.0.0.1:8330'
$p = Start-Process -FilePath $bin -WorkingDirectory $dossierServeur -WindowStyle Hidden -RedirectStandardOutput "$journaux\server_rs.out.log" -RedirectStandardError "$journaux\server_rs.err.log" -PassThru
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') server_boot: rust lance PID $($p.Id)" | Out-File $log -Append -Encoding utf8
