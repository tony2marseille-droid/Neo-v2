$ErrorActionPreference = 'Continue'
# Reglages : neo.toml (scripts\NeoConfig.psm1), sinon LOCALAI_ROOT, sinon le dossier du script.
$racine = $PSScriptRoot
$module = Join-Path $PSScriptRoot 'scripts\NeoConfig.psm1'
if (Test-Path $module) {
    Import-Module $module -Force
    $racine = Import-NeoConfig -Depart $racine
} elseif ($env:LOCALAI_ROOT) { $racine = $env:LOCALAI_ROOT }
$OllamaLog = Join-Path $racine 'logs\ollama_boot.log'
# Executable d'Ollama : OLLAMA_BIN (neo.toml [ollama] executable) s'il est defini.
$bin = $null
if ($env:OLLAMA_BIN) {
    $bin = (Get-Command $env:OLLAMA_BIN -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1).Path
}
# Sinon : Ollama est installe dans le profil de l'utilisateur ; ce script peut
# tourner sous un autre compte : on cherche dans tous les profils.
if (-not $bin) {
    $bin = (Get-ChildItem "$env:SystemDrive\Users\*\AppData\Local\Programs\Ollama\ollama.exe" -ErrorAction SilentlyContinue |
        Select-Object -First 1).FullName
}
# Dernier recours : "ollama" trouve par le PATH.
if (-not $bin) {
    $bin = (Get-Command 'ollama' -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1).Path
}

function Log([string]$msg) {
  $line = "{0} ollama_boot: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  try { Add-Content -Path $OllamaLog -Value $line -Encoding UTF8 } catch {}
  Write-Output $line
}

function Test-Api() {
  try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:11434/api/version' -UseBasicParsing -TimeoutSec 3
    return ($r.StatusCode -eq 200)
  } catch { return $false }
}

Log 'demarrage; attendre fin des updates Ollama (60s)'
Start-Sleep -Seconds 60

if (Test-Api) { Log 'API deja operationnelle - rien a faire'; exit 0 }

$procs = Get-Process ollama, 'ollama app' -ErrorAction SilentlyContinue
foreach ($pr in $procs) {
  Log ("process present mais non servant -> kill: {0} (pid {1})" -f $pr.ProcessName, $pr.Id)
  Stop-Process -Id $pr.Id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 3

if (-not $bin -or -not (Test-Path $bin)) { Log 'skip: bin introuvable'; exit 0 }
Log ('lancement serve: {0}' -f $bin)
try {
  $p = Start-Process -FilePath $bin -ArgumentList 'serve' -WindowStyle Hidden -PassThru
  Log ("serve lance (pid={0}), attente reponse..." -f $p.Id)
} catch {
  Log ('echec: {0}' -f $_.Exception.Message)
  exit 1
}

for ($i = 0; $i -lt 24; $i++) {
  Start-Sleep -Seconds 5
  if (Test-Api) { Log 'API OK - ollama operationnel'; exit 0 }
}
Log 'API pas encore up apres 120s (verification au check suivant)'
exit 0