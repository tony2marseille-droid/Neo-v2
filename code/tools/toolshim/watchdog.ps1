# Watchdog Néo complet : verifie les 3 ports critiques et relance la tache
# correspondante si le port est ferme.
#   8320 -> LocalAI_server_boot  (serveur Rust, frontend + API)
#   8321 -> NeoVideoAPI          (API video + liste des fichiers Neo + vignettes)
#   8330 -> Neo_Toolshim         (proxy tool-calling du chat)
# Reglages : neo.toml (scripts\NeoConfig.psm1), sinon LOCALAI_ROOT, sinon <racine> deduite
# de l'emplacement du script (<racine>\tools\toolshim).
$racine = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$module = Join-Path $PSScriptRoot '..\..\scripts\NeoConfig.psm1'
if (Test-Path $module) {
    Import-Module $module -Force
    $racine = Import-NeoConfig -Depart $racine
} elseif ($env:LOCALAI_ROOT) { $racine = $env:LOCALAI_ROOT }
$log = Join-Path $racine 'tools\toolshim\watchdog_all.log'

function Test-Port($port) {
    try {
        $s = New-Object System.Net.Sockets.TcpClient
        $r = $s.BeginConnect("127.0.0.1", $port, $null, $null)
        # WaitOne rend vrai aussi quand la connexion est REFUSEE : exiger Connected.
        $ok = ($r.AsyncWaitHandle.WaitOne(3000) -and $s.Connected)
        $s.Close()
        return $ok
    } catch { return $false }
}

$ports = @(
    @{ port = 8320; task = "LocalAI_server_boot"; nom = "Serveur Rust" },
    @{ port = 8321; task = "NeoVideoAPI";         nom = "API Video" },
    @{ port = 8330; task = "Neo_Toolshim";        nom = "Toolshim" }
)

$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
foreach ($p in $ports) {
    if (Test-Port $p.port) {
        "$ts PORT $($p.port) ($($p.nom)) OK" | Out-File -FilePath $log -Append
    } else {
        "$ts PORT $($p.port) ($($p.nom)) FERME -> redemarrage $($p.task)" | Out-File -FilePath $log -Append
        schtasks /run /tn $p.task | Out-Null
        Write-Output "REDEMARRE $($p.task)"
    }
}
Write-Output "WATCHDOG_TERMINE"
