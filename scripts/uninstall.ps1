# Desinstallation des services Windows SmokeWatch.
# A executer en tant qu'administrateur.
#
# Ne touche ni au code, ni aux modeles, ni a la base de donnees : seuls les
# services sont retires. Les logs restent dans logs/.

$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Ce script doit etre lance en tant qu'administrateur."
}

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    throw "NSSM introuvable dans le PATH : impossible de retirer les services."
}

foreach ($name in @("SmokeWatchPipeline", "SmokeWatchWeb")) {
    if (Get-Service $name -ErrorAction SilentlyContinue) {
        Write-Host "Arret et suppression de $name"
        nssm stop $name confirm 2>$null | Out-Null
        nssm remove $name confirm | Out-Null
    } else {
        Write-Host "$name : absent, rien a faire"
    }
}

Write-Host ""
Write-Host "Services retires. Le code, les modeles et la base sont intacts."
