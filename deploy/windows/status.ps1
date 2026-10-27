<#
.SYNOPSIS
    Shows whether everything is running: services, health check, scheduled jobs and the latest backup.
#>
. "$PSScriptRoot\common.ps1"
$envValues = Read-DotEnv
$port = $envValues["WEB_PORT"]; if (-not $port) { $port = 8000 }

Write-Step "Services"
foreach ($name in $WebService, $CaddyService) {
    $service = Get-Service $name -ErrorAction SilentlyContinue
    if (-not $service) { Write-Note "$name is not installed" }
    elseif ($service.Status -eq "Running") { Write-Ok "$name is running" }
    else { Write-Note "$name is $($service.Status)" }
}

Write-Step "Web app health"
try {
    $r = Invoke-WebRequest "http://127.0.0.1:$port/healthz/" -UseBasicParsing -TimeoutSec 5
    Write-Ok "$($r.StatusCode) $($r.Content)"
} catch { Write-Note "Not answering: $($_.Exception.Message)" }

Write-Step "Scheduled jobs"
$tasks = Get-ScheduledTask -TaskPath $TaskFolder -ErrorAction SilentlyContinue
if (-not $tasks) { Write-Note "None installed" }
foreach ($t in $tasks) {
    $info = $t | Get-ScheduledTaskInfo
    $result = if ($info.LastTaskResult -eq 0) { "OK" } else { "last result $($info.LastTaskResult)" }
    Write-Host ("    {0,-16} last run {1}  ({2})  next {3}" -f $t.TaskName, $info.LastRunTime, $result, $info.NextRunTime)
}

Write-Step "Latest backup"
$folder = $envValues["BACKUP_DIR"]; if (-not $folder) { $folder = $BackupDir }
$latest = Get-ChildItem $folder -Filter "invigilation-*" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
if ($latest) {
    $age = (Get-Date) - $latest.LastWriteTime
    $line = "{0}  ({1:N0} KB, {2:N0} hours ago)" -f $latest.Name, ($latest.Length / 1KB), $age.TotalHours
    if ($age.TotalHours -gt 26) { Write-Note "$line - older than a day, check the Nightly backup job" } else { Write-Ok $line }
} else { Write-Note "No backups yet in $folder" }
