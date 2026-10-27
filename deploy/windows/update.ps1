<#
.SYNOPSIS
    Updates an installed system to the latest code: backup, pull, install packages, migrate, restart.

.DESCRIPTION
    Run from PowerShell opened with "Run as administrator", inside the project folder.
    The site is down for about a minute while the web service restarts.

.EXAMPLE
    .\deploy\windows\update.ps1
    Pulls the latest code with git and applies it.

.EXAMPLE
    .\deploy\windows\update.ps1 -NoPull
    Applies code you copied into the folder yourself.
#>
[CmdletBinding()]
param([switch]$NoPull)

. "$PSScriptRoot\common.ps1"
Assert-Admin
$port = (Read-DotEnv)["WEB_PORT"]; if (-not $port) { $port = 8000 }

Write-Step "Backing up the database first"
Invoke-Manage backup_db

if (-not $NoPull) {
    Write-Step "Getting the latest code"
    Push-Location $Root
    try { Invoke-Native "git" @("pull", "--ff-only") } finally { Pop-Location }
}

Write-Step "Installing Python packages"
Invoke-Native $Python @("-m", "pip", "install", "--quiet", "-r", (Join-Path $Root "requirements.txt"))

Write-Step "Stopping the web app"
Stop-AppService $WebService

Write-Step "Updating the database tables and static files"
Invoke-Manage migrate --noinput
Invoke-Manage collectstatic --noinput --clear --verbosity=0
Invoke-Manage check --deploy

Write-Step "Starting the web app"
Start-Service $WebService
if (-not (Wait-Healthy -Port $port)) {
    throw "The web app did not come back. Look in $LogDir. To go back: restore the backup above with restore.ps1."
}
if ((Test-ServiceExists $CaddyService) -and (Get-Service $CaddyService).Status -ne "Running") { Start-Service $CaddyService }
Write-Host "`nUpdated and running." -ForegroundColor Green
