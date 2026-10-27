<#
.SYNOPSIS
    Restores the database from a backup file. ALL CURRENT DATA IS REPLACED.

.DESCRIPTION
    Run from PowerShell opened with "Run as administrator", inside the project folder.
    Without -File it lists the available backups. The current data is backed up once more
    before the restore, so a mistaken restore can itself be undone.

.EXAMPLE
    .\deploy\windows\restore.ps1
    Lists the backups.

.EXAMPLE
    .\deploy\windows\restore.ps1 -File backups\invigilation-20261024-020000.dump
#>
[CmdletBinding()]
param([string]$File, [switch]$Force)

. "$PSScriptRoot\common.ps1"
$folder = (Read-DotEnv)["BACKUP_DIR"]; if (-not $folder) { $folder = $BackupDir }

if (-not $File) {
    Write-Host "Backups in ${folder} (newest first):"
    Get-ChildItem $folder -Filter "invigilation-*" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending |
        Format-Table Name, @{ n = "Size (KB)"; e = { [int]($_.Length / 1KB) } }, LastWriteTime -AutoSize
    Write-Host "Restore one with: .\deploy\windows\restore.ps1 -File <path>"
    return
}

Assert-Admin
$path = (Resolve-Path $File).Path
if (-not $Force) {
    Write-Host "This replaces ALL current data with the backup $path" -ForegroundColor Yellow
    if ((Read-Host "Type RESTORE to continue") -cne "RESTORE") { Write-Host "Cancelled."; return }
}

Write-Step "Backing up the current data (in case you need it back)"
Invoke-Manage backup_db

Write-Step "Stopping the web app"
Stop-AppService $WebService

Write-Step "Restoring"
try { Invoke-Manage restore_db $path --yes; Invoke-Manage migrate --noinput }
finally {
    if (Test-ServiceExists $WebService) { Start-Service $WebService }
}
$port = (Read-DotEnv)["WEB_PORT"]; if (-not $port) { $port = 8000 }
if ((Test-ServiceExists $WebService) -and -not (Wait-Healthy -Port $port)) {
    throw "The web app did not come back after the restore. Look in $LogDir."
}
Write-Host "`nRestored from $path." -ForegroundColor Green
