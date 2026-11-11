# Watchdog Néo complet : verifie les 3 ports critiques et relance la tache
# correspondante si le port est ferme.
#   8320 -> LocalAI_server_boot  (serveur Rust, frontend + API)
#   8321 -> NeoVideoAPI          (API video + liste des fichiers Neo + vignettes)
#   8330 -> Neo_Toolshim         (proxy tool-calling du chat)
$log = "E:\ia\tools\toolshim\watchdog_all.log"

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
