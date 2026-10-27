<#
.SYNOPSIS
    Removes the Windows services, scheduled jobs and firewall rule. Keeps the database, backups,
    logs and .env, so running install.ps1 again brings the system back as it was.
#>
. "$PSScriptRoot\common.ps1"
Assert-Admin

foreach ($name in $CaddyService, $WebService) {
    if (Test-ServiceExists $name) {
        Stop-AppService $name
        Invoke-Native (Join-Path $ServiceDir "$name.exe") @("uninstall")
        Write-Ok "Removed service $name"
    }
}
Get-ScheduledTask -TaskPath $TaskFolder -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Write-Ok "Removed scheduled jobs"
Get-NetFirewallRule -DisplayName "Invigilation Duties (HTTP/HTTPS)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Ok "Removed firewall rule"
Write-Host "`nUninstalled. Data, backups and .env were kept in $Root." -ForegroundColor Green
