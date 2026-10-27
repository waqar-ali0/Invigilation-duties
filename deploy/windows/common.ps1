# Shared helpers for the Windows deployment scripts. Dot-source this file: . "$PSScriptRoot\common.ps1"

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Windows PowerShell's progress bar makes downloads very slow

$Root = (Resolve-Path "$PSScriptRoot\..\..").Path          # the project folder
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$BinDir = Join-Path $PSScriptRoot "bin"                     # caddy.exe, winsw.exe
$ServiceDir = Join-Path $PSScriptRoot "services"            # generated service wrappers and Caddyfile
$LogDir = Join-Path $Root "logs"
$BackupDir = Join-Path $Root "backups"
$WebService = "InvigilationWeb"
$CaddyService = "InvigilationCaddy"
$TaskFolder = "\Invigilation\"

function Write-Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Write-Ok($message) { Write-Host "    $message" -ForegroundColor Green }
function Write-Note($message) { Write-Host "    $message" -ForegroundColor Yellow }

function Assert-Admin {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script from PowerShell opened with 'Run as administrator'."
    }
}

# Runs a native program and stops the script if it fails. Judged by exit code only: Windows
# PowerShell turns anything a program writes to stderr (e.g. warnings) into errors when the
# output is redirected to a log, which must not abort the install.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE." }
}

# Runs a native program and returns its output; the caller checks $LASTEXITCODE.
function Get-NativeOutput {
    param([string]$Exe, [string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return (& $Exe @Arguments 2>$null) } finally { $ErrorActionPreference = $previous }
}

function Invoke-Manage {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    Push-Location $Root
    try { Invoke-Native $Python (@("manage.py") + $Arguments) } finally { Pop-Location }
}

# Reads KEY=VALUE lines from the project's .env file into a hashtable.
function Read-DotEnv {
    $values = @{}
    $path = Join-Path $Root ".env"
    if (Test-Path $path) {
        foreach ($line in Get-Content $path) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() }
        }
    }
    return $values
}

function Test-ServiceExists($name) { return [bool](Get-Service -Name $name -ErrorAction SilentlyContinue) }

function Stop-AppService($name) {
    if (Test-ServiceExists $name) {
        Stop-Service -Name $name -ErrorAction SilentlyContinue
        (Get-Service -Name $name).WaitForStatus("Stopped", [TimeSpan]::FromSeconds(60))
    }
}

# Waits until the app answers on /healthz/ (directly, bypassing Caddy).
function Wait-Healthy {
    param([int]$Port = 8000, [int]$Seconds = 60)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/healthz/" -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Seconds 2 }
    }
    return $false
}
