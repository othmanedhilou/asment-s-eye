# Demarre Ciment's Eye : l'interface et la detection.
#
# Deux processus, et l'ordre compte peu, mais leurs roles sont distincts :
#
#   l'interface (uvicorn)   sert la page et l'API. Sans elle, rien a regarder
#   la detection (pipeline) ouvre les cameras et fait tourner les modeles.
#                           Sans elle, l'interface affiche des vignettes figees
#
# Le pipeline refuse de demarrer si un autre tourne deja (verrou sur le port
# 8791). C'est voulu : deux pipelines sur la meme machine, c'est deux fois
# l'inference sur deux coeurs et deux ecritures concurrentes de la meme image.
# Le symptome visible serait une interface lente, et la cause invisible.
#
# Usage :
#   .\demarrer.ps1              lance les deux dans des fenetres separees
#   .\demarrer.ps1 -Arreter     arrete tout
#   .\demarrer.ps1 -Etat        dit ce qui tourne

param(
    [switch]$Arreter,
    [switch]$Etat
)

$ErrorActionPreference = "Stop"
$Racine = $PSScriptRoot
$Python = Join-Path $Racine "venv\Scripts\python.exe"

function Processus-CimentsEye {
    Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
        Where-Object { $_.CommandLine -match "app\.pipeline|uvicorn app\.api" }
}

if ($Etat) {
    $procs = Processus-CimentsEye
    if (-not $procs) {
        "Rien ne tourne."
    } else {
        foreach ($p in $procs) {
            $role = if ($p.CommandLine -match "app\.pipeline") { "detection " } else { "interface " }
            "$role PID $($p.ProcessId)  memoire $([int]($p.WorkingSetSize / 1MB)) Mo"
        }
        try {
            $r = Invoke-WebRequest "http://localhost:8000/api/health" -UseBasicParsing -TimeoutSec 5
            $sante = $r.Content | ConvertFrom-Json
            "pipeline actif : $($sante.pipeline.running)"
            foreach ($nom in $sante.cameras.PSObject.Properties.Name) {
                $c = $sante.cameras.$nom
                "  $nom : $($c.state)  $($c.cycle_ms) ms  $($c.modeles_actifs) modele(s)"
            }
        } catch {
            "L'interface ne repond pas encore sur le port 8000."
        }
    }
    return
}

if ($Arreter) {
    $procs = Processus-CimentsEye
    if (-not $procs) { "Rien a arreter."; return }
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force
        "arrete PID $($p.ProcessId)"
    }
    return
}

if (-not (Test-Path $Python)) {
    throw "Environnement Python introuvable : $Python`nLancez d'abord : python -m venv venv ; .\venv\Scripts\pip install -r requirements.txt"
}

# Un demarrage par-dessus un autre laisserait deux interfaces sur le meme port
# et un pipeline refuse par son verrou. On fait le menage d'abord.
$existants = Processus-CimentsEye
if ($existants) {
    "Arret des processus deja en cours..."
    foreach ($p in $existants) { Stop-Process -Id $p.ProcessId -Force }
    Start-Sleep -Seconds 3
}

"Demarrage de l'interface..."
Start-Process -FilePath $Python `
    -ArgumentList "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000" `
    -WorkingDirectory $Racine -WindowStyle Minimized

Start-Sleep -Seconds 4

"Demarrage de la detection..."
Start-Process -FilePath $Python `
    -ArgumentList "-u", "-m", "app.pipeline" `
    -WorkingDirectory $Racine -WindowStyle Minimized

""
"  Sur ce poste        http://localhost:8000"
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.PrefixOrigin -ne 'WellKnown' } |
       Select-Object -First 1).IPAddress
if ($ip) { "  Sur le reseau local http://${ip}:8000" }
""
"Le chargement des modeles prend une trentaine de secondes au premier demarrage."
"Etat : .\demarrer.ps1 -Etat      Arret : .\demarrer.ps1 -Arreter"
