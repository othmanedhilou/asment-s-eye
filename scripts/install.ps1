# Installation SmokeWatch en services Windows (démarrage automatique).
# À exécuter en tant qu'administrateur sur le serveur de supervision.
#
# Prérequis : NSSM (https://nssm.cc/download) accessible dans le PATH,
#             Docker Desktop configuré pour démarrer avec Windows.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot "venv\Scripts\python.exe"

Write-Host "Installation des services SmokeWatch depuis $ProjectRoot"

if (-not (Test-Path $Python)) {
    throw "Environnement virtuel introuvable : $Python"
}

# --- Service 1 : pipeline de detection ---
nssm install SmokeWatchPipeline $Python "-u -m app.pipeline"
nssm set SmokeWatchPipeline AppDirectory $ProjectRoot
nssm set SmokeWatchPipeline DisplayName "SmokeWatch - Pipeline de detection"
nssm set SmokeWatchPipeline Description "Detection IA multi-modeles sur les flux cameras"
nssm set SmokeWatchPipeline Start SERVICE_AUTO_START
nssm set SmokeWatchPipeline AppStdout (Join-Path $ProjectRoot "logs\pipeline.log")
nssm set SmokeWatchPipeline AppStderr (Join-Path $ProjectRoot "logs\pipeline.err.log")
nssm set SmokeWatchPipeline AppRotateFiles 1
# redemarrage automatique en cas de crash
nssm set SmokeWatchPipeline AppExit Default Restart
nssm set SmokeWatchPipeline AppRestartDelay 5000

# --- Service 2 : interface web / API ---
nssm install SmokeWatchWeb $Python "-m uvicorn app.api:app --host 0.0.0.0 --port 8000"
nssm set SmokeWatchWeb AppDirectory $ProjectRoot
nssm set SmokeWatchWeb DisplayName "SmokeWatch - Interface VMS"
nssm set SmokeWatchWeb Description "Interface web de supervision (port 8000)"
nssm set SmokeWatchWeb Start SERVICE_AUTO_START
nssm set SmokeWatchWeb AppStdout (Join-Path $ProjectRoot "logs\web.log")
nssm set SmokeWatchWeb AppStderr (Join-Path $ProjectRoot "logs\web.err.log")
nssm set SmokeWatchWeb AppRotateFiles 1
nssm set SmokeWatchWeb AppExit Default Restart
nssm set SmokeWatchWeb AppRestartDelay 5000

New-Item -ItemType Directory -Force (Join-Path $ProjectRoot "logs") | Out-Null

Start-Service SmokeWatchPipeline
Start-Service SmokeWatchWeb

Write-Host ""
Write-Host "Services installes et demarres."
Write-Host "Interface : http://localhost:8000"
Write-Host ""
Write-Host "Commandes utiles :"
Write-Host "  Get-Service SmokeWatch*        etat des services"
Write-Host "  Restart-Service SmokeWatchWeb  redemarrage apres mise a jour"
Write-Host "  nssm remove SmokeWatchWeb confirm   desinstallation"
