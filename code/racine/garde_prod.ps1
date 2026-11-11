$ErrorActionPreference = 'SilentlyContinue'
$log = 'E:\ia\logs\garde_prod.log'
$etat = 'E:\ia\logs\garde_prod_etat.json'

function Log([string]$m) {
    $l = "{0} garde_prod: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $log -Value $l -Encoding UTF8
}

function OK($url) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

$sante = @{ neo = (OK 'http://127.0.0.1:8320/api/health'); ollama = (OK 'http://127.0.0.1:11434/api/version'); rerank = (OK 'http://127.0.0.1:8322/') }

# 1. Serveur Neo (8320) : relance via la tache de boot officielle
if (-not $sante.neo) {
    Log 'serveur Neo KO -> relance LocalAI_server_boot'
    schtasks /run /tn LocalAI_server_boot | Out-Null
    Start-Sleep -Seconds 10
    if (OK 'http://127.0.0.1:8320/api/health') { Log 'serveur Neo retabli'; $sante.neo = $true }
    else { Log 'serveur Neo toujours KO apres relance' }
}

# 2. Ollama (11434) : relance via le script de boot officiel
if (-not $sante.ollama) {
    Log 'ollama KO -> relance run_ollama.ps1'
    Start-Process -FilePath 'powershell' -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','E:\ia\run_ollama.ps1' -WindowStyle Hidden
    Start-Sleep -Seconds 15
    if (OK 'http://127.0.0.1:11434/api/version') { Log 'ollama retabli'; $sante.ollama = $true }
    else { Log 'ollama toujours KO apres relance' }
}

# 3. Rerank (8322) : relance via la tache dediee
if (-not $sante.rerank) {
    Log 'rerank KO -> relance Neo_Rerank'
    schtasks /run /tn Neo_Rerank | Out-Null
    Start-Sleep -Seconds 5
}

# 4. Entrainements : alerter si un statut 'training' ne bouge plus (>20 min)
foreach ($st in (Get-ChildItem 'E:\ia\logs\train_v*_status.json' | Sort-Object LastWriteTime -Descending)) {
    try {
        $j = Get-Content $st.FullName -Raw | ConvertFrom-Json
        if ($j.phase -eq 'training') {
            $age = (Get-Date) - (Get-Date $j.updated)
            if ($age.TotalMinutes -gt 20) {
                Log ("ALERTE train gele (" + $st.Name + ") : step " + $j.step + ", statut fige depuis " + [math]::Round($age.TotalMinutes) + " min")
            }
        }
    } catch {}
}

# 5. Heartbeat : une ligne par heure maximum quand tout va bien (preuve de vie)
$dernier = $null
if (Test-Path $etat) {
    try { $dernier = (Get-Content $etat -Raw | ConvertFrom-Json).derniere_ok } catch {}
}
$maintenant = Get-Date
$ko = @($sante.Keys | Where-Object { -not $sante[$_] })
if ($ko.Count -eq 0) {
    $ecrit = $false
    if (-not $dernier -or ($maintenant - [datetime]$dernier).TotalMinutes -ge 60) {
        Log 'heartbeat: 3/3 services OK'
        $ecrit = $true
    }
    @{ derniere_ok = $maintenant.ToString('o'); sante = $sante } | ConvertTo-Json | Set-Content -Path $etat -Encoding UTF8
} else {
    Log ("services KO : " + ($ko -join ', '))
    @{ derniere_ok = $dernier; sante = $sante } | ConvertTo-Json | Set-Content -Path $etat -Encoding UTF8
}
