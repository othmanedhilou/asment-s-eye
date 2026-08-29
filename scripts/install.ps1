# Installation de SmokeWatch en services Windows (demarrage automatique au boot,
# redemarrage automatique en cas de crash).
#
# A executer EN TANT QU'ADMINISTRATEUR sur le serveur de supervision :
#     powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# Prerequis : NSSM (https://nssm.cc/download), decompresse et accessible dans le
# PATH. NSSM transforme un programme ordinaire en service Windows ; Python n'en
# est pas un nativement.
#
# Le script est rejouable : relance-le apres une mise a jour du code, il
# reinstalle proprement les deux services.

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$LogsDir = Join-Path $ProjectRoot "logs"
$Port = 8000

# --- Verifications prealables -------------------------------------------------
# Chacune de ces erreurs produirait sinon un echec obscur au milieu de
# l'installation, avec des services a moitie crees.

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Ce script doit etre lance en tant qu'administrateur (creation de services Windows)."
}

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    throw @"
NSSM introuvable dans le PATH.
Telecharger sur https://nssm.cc/download, decompresser, puis ajouter le dossier
contenant nssm.exe au PATH systeme (ou copier nssm.exe dans C:\Windows\System32).
"@
}

if (-not (Test-Path $Python)) {
    throw @"
Environnement virtuel introuvable : $Python
Creer l'environnement d'abord :
    python -m venv venv
    .\venv\Scripts\python.exe -m pip install -r requirements.txt
"@
}

# Verifie que les dependances sont reellement installees : un venv vide
# produirait un service qui redemarre en boucle sans explication.
& $Python -c "import fastapi, cv2, ultralytics" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Dependances manquantes dans le venv. Lancer : .\venv\Scripts\python.exe -m pip install -r requirements.txt"
}

if (-not (Test-Path (Join-Path $ProjectRoot "models"))) {
    Write-Warning "Dossier models/ absent : le pipeline demarrera mais ne detectera rien."
}

$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Warning "Le port $Port est deja utilise (PID $($busy[0].OwningProcess)). Le service web ne pourra pas demarrer tant qu'il n'est pas libere."
}

New-Item -ItemType Directory -Force $LogsDir | Out-Null

Write-Host "Installation des services SmokeWatch depuis $ProjectRoot"

# --- Installation d'un service ------------------------------------------------

function Install-SmokeWatchService {
    param(
        [string]$Name,
        [string]$Arguments,
        [string]$DisplayName,
        [string]$Description,
        [string]$LogPrefix
    )

    # Rejouabilite : on retire le service existant avant de le recreer, sinon
    # `nssm install` echoue et le service garde son ancienne configuration.
    if (Get-Service $Name -ErrorAction SilentlyContinue) {
        Write-Host "  service $Name deja present : reinstallation"
        nssm stop $Name confirm 2>$null | Out-Null
        nssm remove $Name confirm | Out-Null
        Start-Sleep -Seconds 2
    }

    nssm install $Name $Python $Arguments | Out-Null
    nssm set $Name AppDirectory $ProjectRoot | Out-Null
    nssm set $Name DisplayName $DisplayName | Out-Null
    nssm set $Name Description $Description | Out-Null
    nssm set $Name Start SERVICE_AUTO_START | Out-Null
    nssm set $Name AppStdout (Join-Path $LogsDir "$LogPrefix.log") | Out-Null
    nssm set $Name AppStderr (Join-Path $LogsDir "$LogPrefix.err.log") | Out-Null
    nssm set $Name AppRotateFiles 1 | Out-Null
    nssm set $Name AppRotateBytes 10485760 | Out-Null   # rotation a 10 Mo
    nssm set $Name AppExit Default Restart | Out-Null   # redemarre apres un crash
    nssm set $Name AppRestartDelay 5000 | Out-Null
    Write-Host "  $Name installe"
}

Install-SmokeWatchService -Name "SmokeWatchPipeline" `
    -Arguments "-u -m app.pipeline" `
    -DisplayName "SmokeWatch - Pipeline de detection" `
    -Description "Detection IA multi-modeles sur les flux cameras" `
    -LogPrefix "pipeline"

Install-SmokeWatchService -Name "SmokeWatchWeb" `
    -Arguments "-m uvicorn app.api:app --host 0.0.0.0 --port $Port" `
    -DisplayName "SmokeWatch - Interface VMS" `
    -Description "Interface web de supervision (port $Port)" `
    -LogPrefix "web"

# --- Demarrage et controle ----------------------------------------------------

Start-Service SmokeWatchPipeline
Start-Service SmokeWatchWeb
Start-Sleep -Seconds 5

$states = Get-Service SmokeWatchPipeline, SmokeWatchWeb
$states | Format-Table Name, Status, StartType -AutoSize

$failed = $states | Where-Object { $_.Status -ne "Running" }
if ($failed) {
    Write-Warning "Service(s) non demarre(s) : $($failed.Name -join ', ')"
    Write-Warning "Consulter $LogsDir pour la cause."
} else {
    Write-Host ""
    Write-Host "Services installes et demarres."
    Write-Host "Interface : http://localhost:$Port"
    Write-Host "Depuis un autre poste : http://<ip-du-serveur>:$Port"
}

Write-Host ""
Write-Host "Commandes utiles :"
Write-Host "  Get-Service SmokeWatch*             etat des services"
Write-Host "  Restart-Service SmokeWatchWeb       redemarrage apres mise a jour du code"
Write-Host "  Get-Content logs\pipeline.log -Tail 50 -Wait   suivre la detection en direct"
Write-Host "  .\scripts\uninstall.ps1             desinstallation complete"
