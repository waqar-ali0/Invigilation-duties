<#
.SYNOPSIS
    Installs the Invigilation Duty System on a Windows machine as two Windows services
    (the web app and Caddy for HTTPS) plus scheduled jobs for email, reminders and backups.

.DESCRIPTION
    Run from PowerShell opened with "Run as administrator", inside the project folder.
    Safe to run again: it updates the configuration and reinstalls the services, keeping data.
    See deploy\windows\DEPLOY.md for the full guide.

.EXAMPLE
    .\deploy\windows\install.ps1 -Domain duties.university.edu -AdminEmail it@university.edu
    Production install with a real HTTPS certificate from Let's Encrypt and a PostgreSQL database.

.EXAMPLE
    .\deploy\windows\install.ps1 -Local -UseSqlite
    Trial install on this computer only, at https://localhost, with a self-signed certificate.
#>
[CmdletBinding()]
param(
    # Public web address, e.g. duties.university.edu. Its DNS record must point to this machine.
    [string]$Domain,
    # Contact address for Let's Encrypt certificate notices.
    [string]$AdminEmail,
    # Trial install at https://localhost with Caddy's own certificate (no domain needed).
    [switch]$Local,
    # Use a single SQLite file instead of PostgreSQL (fine for a trial, not for the real rollout).
    [switch]$UseSqlite,
    # Use an existing database instead of creating one, e.g. postgres://user:pass@host:5432/name
    [string]$DatabaseUrl,
    # PostgreSQL's bin folder; found automatically under C:\Program Files\PostgreSQL when omitted.
    [string]$PgBin,
    # Outgoing mail (SMTP). Without these, emails are written to files and nobody receives them.
    [string]$SmtpHost,
    [int]$SmtpPort = 587,
    [string]$SmtpUser,
    [string]$FromEmail,
    # Offline install: paths to caddy.exe and WinSW-x64.exe downloaded on another machine.
    [string]$CaddyExe,
    [string]$WinSWExe,
    # The command that runs Python 3.12 or newer.
    [string]$PythonCommand = "py -3",
    [int]$WebPort = 8000
)

. "$PSScriptRoot\common.ps1"

$CaddyVersion = "2.8.4"
$CaddyUrl = "https://github.com/caddyserver/caddy/releases/download/v$CaddyVersion/caddy_${CaddyVersion}_windows_amd64.zip"
$WinSWUrl = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"

if (-not $Local -and -not $Domain) { throw "Give -Domain (e.g. -Domain duties.university.edu), or -Local for a trial on this computer." }
if ($Local) { $Domain = "localhost" }
Assert-Admin
foreach ($dir in $BinDir, $ServiceDir, $LogDir, $BackupDir) { New-Item -ItemType Directory -Force $dir | Out-Null }

# ---------------------------------------------------------------- .env helpers
function Set-DotEnv([hashtable]$Values) {
    $path = Join-Path $Root ".env"
    $lines = [System.Collections.Generic.List[string]]::new()
    if (Test-Path $path) { foreach ($l in Get-Content $path) { $lines.Add($l) } }
    foreach ($key in $Values.Keys) {
        $line = "$key=$($Values[$key])"
        $index = -1
        for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^\s*#?\s*$key\s*=") { $index = $i; break } }
        if ($index -ge 0) { $lines[$index] = $line } else { $lines.Add($line) }
    }
    [IO.File]::WriteAllLines($path, $lines, [Text.UTF8Encoding]::new($false))
}

function New-Secret([int]$Length) {
    $chars = [char[]]"abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    $bytes = New-Object byte[] $Length
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

function ConvertFrom-Secure([Security.SecureString]$Value) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

# ---------------------------------------------------------------- 1. Python environment
Write-Step "Preparing Python"
if (-not (Test-Path $Python)) {
    $parts = $PythonCommand -split " "
    Invoke-Native $parts[0] (@($parts | Select-Object -Skip 1) + @("-m", "venv", (Join-Path $Root ".venv")))
}
Invoke-Native $Python @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
Invoke-Native $Python @("-m", "pip", "install", "--quiet", "-r", (Join-Path $Root "requirements.txt"))
Write-Ok (Get-NativeOutput $Python @("--version"))

# ---------------------------------------------------------------- 2. Database
Write-Step "Setting up the database"
$existing = Read-DotEnv
if (-not $PgBin) {
    $found = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Directory.Parent.Name -replace '\D', '') } | Select-Object -Last 1
    if ($found) { $PgBin = $found.DirectoryName }
}
if ($UseSqlite) {
    $DatabaseUrl = "sqlite:///" + ((Join-Path $Root "db.sqlite3") -replace '\\', '/')
    Write-Note "Using SQLite. Switch to PostgreSQL before the real rollout."
} elseif ($DatabaseUrl) {
    Write-Ok "Using the database you gave."
} elseif ($existing["DATABASE_URL"] -like "postgres*") {
    $DatabaseUrl = $existing["DATABASE_URL"]
    Write-Ok "Keeping the PostgreSQL database already in .env."
} else {
    if (-not $PgBin) { throw "PostgreSQL was not found. Install it (see DEPLOY.md), pass -PgBin, or use -UseSqlite for a trial." }
    $psql = Join-Path $PgBin "psql.exe"
    $pgAdmin = Read-Host "Password of the PostgreSQL 'postgres' user (set when PostgreSQL was installed)" -AsSecureString
    $env:PGPASSWORD = ConvertFrom-Secure $pgAdmin
    try {
        $dbPassword = New-Secret 32
        $roleExists = (Get-NativeOutput $psql @("-U", "postgres", "-h", "localhost", "-tAc", "SELECT 1 FROM pg_roles WHERE rolname='invigilation'")) -eq "1"
        if ($LASTEXITCODE -ne 0) { throw "Could not connect to PostgreSQL as 'postgres'. Check the password." }
        $verb = if ($roleExists) { "ALTER" } else { "CREATE" }
        Invoke-Native $psql @("-U", "postgres", "-h", "localhost", "-v", "ON_ERROR_STOP=1", "-qc", "$verb ROLE invigilation LOGIN PASSWORD '$dbPassword'")
        $dbExists = (Get-NativeOutput $psql @("-U", "postgres", "-h", "localhost", "-tAc", "SELECT 1 FROM pg_database WHERE datname='invigilation'")) -eq "1"
        if (-not $dbExists) {
            Invoke-Native $psql @("-U", "postgres", "-h", "localhost", "-v", "ON_ERROR_STOP=1", "-qc", "CREATE DATABASE invigilation OWNER invigilation ENCODING 'UTF8'")
        }
    } finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    $DatabaseUrl = "postgres://invigilation:$dbPassword@localhost:5432/invigilation"
    Write-Ok "Database 'invigilation' is ready."
}

# ---------------------------------------------------------------- 3. Configuration (.env)
Write-Step "Writing configuration (.env)"
$values = [ordered]@{
    DEBUG                = "False"
    ALLOWED_HOSTS        = "$Domain,localhost,127.0.0.1"
    SITE_URL             = "https://$Domain"
    HTTPS                = "True"
    CSRF_TRUSTED_ORIGINS = "https://$Domain"
    DATABASE_URL         = $DatabaseUrl
    LOG_DIR              = $LogDir
    BACKUP_DIR           = $BackupDir
    WEB_PORT             = "$WebPort"
}
if ($PgBin) { $values["PG_BIN"] = $PgBin }
if (-not $existing["SECRET_KEY"] -or $existing["SECRET_KEY"] -like "change-me*") { $values["SECRET_KEY"] = New-Secret 64 }
if ($SmtpHost) {
    $values["EMAIL_BACKEND"] = "django.core.mail.backends.smtp.EmailBackend"
    $values["EMAIL_HOST"] = $SmtpHost
    $values["EMAIL_PORT"] = "$SmtpPort"
    $values["EMAIL_USE_TLS"] = "True"
    if ($SmtpUser) {
        $values["EMAIL_HOST_USER"] = $SmtpUser
        $values["EMAIL_HOST_PASSWORD"] = ConvertFrom-Secure (Read-Host "Password for mail account $SmtpUser" -AsSecureString)
    }
}
if ($FromEmail) { $values["DEFAULT_FROM_EMAIL"] = $FromEmail }
Set-DotEnv $values
# Only administrators and the services (SYSTEM) may read .env: it holds passwords.
Invoke-Native "icacls.exe" @((Join-Path $Root ".env"), "/inheritance:r", "/grant:r", "*S-1-5-32-544:F", "*S-1-5-18:F") | Out-Null
$smtpConfigured = (Read-DotEnv)["EMAIL_BACKEND"] -like "*smtp*"
Write-Ok "Saved. Only administrators can read it."
if (-not $smtpConfigured) { Write-Note "Email is not set up yet: messages are saved to files only. Re-run with -SmtpHost, or edit .env." }

# ---------------------------------------------------------------- 4. Application
Write-Step "Updating the database tables and static files"
Stop-AppService $WebService
Invoke-Manage migrate --noinput
Invoke-Manage collectstatic --noinput --clear --verbosity=0
Invoke-Manage check --deploy
Push-Location $Root
Get-NativeOutput $Python @("manage.py", "shell", "-c", "import sys; from accounts.models import User; sys.exit(0 if User.objects.filter(is_superuser=True).exists() else 3)") | Out-Null
$hasSuperuser = $LASTEXITCODE -eq 0
Pop-Location
if (-not $hasSuperuser) {
    Write-Note "Create the first administrator account (you log in with this email):"
    Invoke-Manage createsuperuser
}

# ---------------------------------------------------------------- 5. Caddy and WinSW
Write-Step "Getting Caddy and WinSW"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$caddy = Join-Path $BinDir "caddy.exe"
$winsw = Join-Path $BinDir "winsw.exe"
if ($CaddyExe) { Copy-Item $CaddyExe $caddy -Force }
elseif (-not (Test-Path $caddy)) {
    $zip = Join-Path $env:TEMP "caddy-$CaddyVersion.zip"
    Invoke-WebRequest $CaddyUrl -OutFile $zip -UseBasicParsing
    $unpacked = Join-Path $env:TEMP "caddy-$CaddyVersion"
    Expand-Archive $zip $unpacked -Force
    Copy-Item (Join-Path $unpacked "caddy.exe") $caddy -Force
    Remove-Item $zip, $unpacked -Recurse -Force
}
if ($WinSWExe) { Copy-Item $WinSWExe $winsw -Force }
elseif (-not (Test-Path $winsw)) { Invoke-WebRequest $WinSWUrl -OutFile $winsw -UseBasicParsing }
Write-Ok (Get-NativeOutput $caddy @("version"))

# ---------------------------------------------------------------- 6. Caddyfile
Write-Step "Writing the Caddy configuration"
$caddyLog = (Join-Path $LogDir "caddy-access.log") -replace '\\', '/'
$tls = if ($Local) { "tls internal" } elseif ($AdminEmail) { "tls $AdminEmail" } else { "" }
$caddyfile = @"
# Generated by deploy\windows\install.ps1. Re-run the installer instead of editing.
$Domain {
	$tls
	encode zstd gzip
	reverse_proxy 127.0.0.1:$WebPort
	header {
		-Server
	}
	log {
		output file "$caddyLog" {
			roll_size 10MiB
			roll_keep 10
		}
	}
}
"@
$caddyfilePath = Join-Path $ServiceDir "Caddyfile"
[IO.File]::WriteAllText($caddyfilePath, $caddyfile, [Text.UTF8Encoding]::new($false))
Invoke-Native $caddy @("validate", "--config", $caddyfilePath, "--adapter", "caddyfile")

# ---------------------------------------------------------------- 7. Windows services
function Install-WinSWService {
    param([string]$Id, [string]$Title, [string]$Description, [string]$Executable, [string]$Arguments,
          [string[]]$DependsOn = @(), [hashtable]$Environment = @{})
    $exe = Join-Path $ServiceDir "$Id.exe"
    if (Test-ServiceExists $Id) {
        Stop-AppService $Id
        Invoke-Native $exe @("uninstall")
        for ($i = 0; $i -lt 30 -and (Test-ServiceExists $Id); $i++) { Start-Sleep -Seconds 1 }
    }
    Copy-Item $winsw $exe -Force
    $e = { param($s) [Security.SecurityElement]::Escape($s) }
    $depends = ($DependsOn | ForEach-Object { "  <depend>$(& $e $_)</depend>" }) -join "`n"
    $envs = ($Environment.Keys | ForEach-Object { "  <env name=`"$_`" value=`"$(& $e $Environment[$_])`"/>" }) -join "`n"
    $xml = @"
<service>
  <id>$Id</id>
  <name>$(& $e $Title)</name>
  <description>$(& $e $Description)</description>
  <executable>$(& $e $Executable)</executable>
  <arguments>$(& $e $Arguments)</arguments>
  <workingdirectory>$(& $e $Root)</workingdirectory>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <stoptimeout>20 sec</stoptimeout>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <onfailure action="restart" delay="60 sec"/>
  <resetfailure>1 hour</resetfailure>
  <logpath>$(& $e $LogDir)</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
$depends
$envs
</service>
"@
    [IO.File]::WriteAllText((Join-Path $ServiceDir "$Id.xml"), $xml, [Text.UTF8Encoding]::new($false))
    Invoke-Native $exe @("install")
}

Write-Step "Installing the Windows services"
$postgres = @(Get-Service "postgresql*" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
$webDepends = if ($DatabaseUrl -like "postgres*localhost*" -and $postgres) { $postgres } else { @() }
Install-WinSWService -Id $WebService -Title "Invigilation Duties - web app" `
    -Description "Runs the Invigilation Duty System (Waitress) on 127.0.0.1:$WebPort." `
    -Executable $Python -Arguments "manage.py serve --port $WebPort" -DependsOn $webDepends
$caddyData = Join-Path $PSScriptRoot "caddy-data"
Install-WinSWService -Id $CaddyService -Title "Invigilation Duties - HTTPS (Caddy)" `
    -Description "Serves https://$Domain and forwards requests to the web app." `
    -Executable $caddy -Arguments "run --config `"$caddyfilePath`" --adapter caddyfile" `
    -DependsOn @($WebService) -Environment @{ XDG_DATA_HOME = $caddyData; XDG_CONFIG_HOME = $caddyData }
Start-Service $WebService
if (-not (Wait-Healthy -Port $WebPort)) { throw "The web app did not start. Look in $LogDir for $WebService.err.log and app.log." }
Write-Ok "Web app is running."
Start-Service $CaddyService
Write-Ok "Caddy is running."

# ---------------------------------------------------------------- 8. Scheduled jobs
Write-Step "Scheduling email, reminders and backups"
function Register-Job([string]$Name, [string]$Command, $Trigger) {
    $jobsLog = Join-Path $LogDir "jobs.log"
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -WorkingDirectory $Root `
        -Argument "/c `"`"$Python`" manage.py $Command >> `"$jobsLog`" 2>&1`""
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskPath $TaskFolder -TaskName $Name -Action $action -Trigger $Trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Ok "$Name"
}
$every5 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-Job "Send emails" "send_emails" $every5
Register-Job "Duty reminders" "send_reminders" (New-ScheduledTaskTrigger -Daily -At "18:00")
Register-Job "Nightly backup" "backup_db" (New-ScheduledTaskTrigger -Daily -At "02:00")

# ---------------------------------------------------------------- 9. Firewall
if (-not $Local) {
    Write-Step "Opening ports 80 and 443 in Windows Firewall"
    if (-not (Get-NetFirewallRule -DisplayName "Invigilation Duties (HTTP/HTTPS)" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "Invigilation Duties (HTTP/HTTPS)" -Direction Inbound -Protocol TCP `
            -LocalPort 80, 443 -Action Allow | Out-Null
    }
    Write-Ok "Done."
}

# ---------------------------------------------------------------- 10. First backup
Write-Step "Taking a first backup"
Invoke-Manage backup_db

Write-Host "`nInstalled. Open https://$Domain in a browser." -ForegroundColor Green
if ($Local) { Write-Note "This trial uses Caddy's own certificate; the browser may warn the first time." }
else { Write-Note "The HTTPS certificate is fetched on the first visit; it can take up to a minute." }
Write-Note "Check health any time with: .\deploy\windows\status.ps1"
