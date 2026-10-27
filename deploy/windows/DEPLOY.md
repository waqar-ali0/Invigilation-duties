# Deploying on Windows

This guide is for whoever sets up the server (university IT or the system's maintainer).
Everything runs natively on Windows — no Linux, no Docker.

```
 Internet ──https──► Caddy (ports 80/443, automatic certificate)
                        │
                        ▼  127.0.0.1:8000 (not reachable from outside)
                     Waitress  ──►  Django app  ──►  PostgreSQL (local only)

 Task Scheduler:  send emails every 5 min · reminders 18:00 · backup 02:00
```

| Part | Runs as | Name |
|---|---|---|
| Web app (Waitress) | Windows service | `InvigilationWeb` |
| HTTPS (Caddy) | Windows service | `InvigilationCaddy` |
| Email, reminders, backup | Scheduled tasks | Task Scheduler → `Invigilation` folder |

## 1. What you need

- **A Windows machine that stays on**: Windows Server 2019/2022/2025, or Windows 10/11 Pro.
  2 CPU cores, 4 GB RAM and 20 GB free disk are plenty for 200 faculty.
- **A web address** such as `duties.university.edu`, with a DNS **A record pointing to this machine's public IP**.
- **Ports 80 and 443 reachable from the internet** (the university's network firewall must allow them to this machine).
  Port 80 is needed for Let's Encrypt to issue the certificate.
- **A mail account the system can send from** (SMTP host, port, user and password), e.g. `invigilation@university.edu`.

## 2. Install the prerequisites (once)

1. **Python 3.13** from <https://www.python.org/downloads/windows/>. In the installer tick
   *"Add python.exe to PATH"* and keep *"py launcher"* selected.
2. **PostgreSQL 16 or 17** from <https://www.postgresql.org/download/windows/> (the EDB installer).
   Note the password you set for the `postgres` user. Keep the default port 5432. Stack Builder is not needed.
3. **Git** from <https://git-scm.com/download/win> (used to get and update the code).

## 3. Get the code

Open **PowerShell as administrator** (right-click → *Run as administrator*):

```powershell
cd C:\
git clone <repository-url> InvigilationDuties
cd C:\InvigilationDuties
Set-ExecutionPolicy -Scope Process Bypass
```

> No git access on the server? Copy the project folder to `C:\InvigilationDuties` instead.

## 4. Run the installer

```powershell
.\deploy\windows\install.ps1 -Domain duties.university.edu -AdminEmail it@university.edu `
    -SmtpHost smtp.university.edu -SmtpUser invigilation@university.edu -FromEmail invigilation@university.edu
```

It asks for:
- the PostgreSQL `postgres` password (to create the `invigilation` database and its own user),
- the mail account's password,
- the first administrator's email and password (only the first time).

What it does, in order: creates the Python environment, creates the database, writes `.env`
(random secret key, HTTPS on; only administrators can read it), builds the database tables,
downloads Caddy and WinSW, installs and starts both services, schedules the three jobs,
opens ports 80/443 in Windows Firewall, and takes a first backup.

Then open `https://duties.university.edu`. The first visit may take up to a minute while the
certificate is issued. Log in with the administrator account and continue with the README
(*Users and roles* → create the exam office accounts, import departments and faculty).

**Running it again is safe.** It keeps the data, `.env` and secret key, and only refreshes the
configuration — use it to change the domain or mail settings.

### Options

| Option | Use |
|---|---|
| `-Local` | Trial on one PC at `https://localhost` with Caddy's own certificate. No domain or open ports needed. |
| `-UseSqlite` | Use a single file instead of PostgreSQL. Fine for a trial; use PostgreSQL for the real rollout. |
| `-DatabaseUrl postgres://user:pass@host:5432/name` | Use a database IT already runs elsewhere. |
| `-PgBin "C:\Program Files\PostgreSQL\17\bin"` | If PostgreSQL is installed somewhere unusual. |
| `-CaddyExe path -WinSWExe path` | Server has no internet access for downloads: download [Caddy 2.8.4 (windows_amd64.zip)](https://github.com/caddyserver/caddy/releases/tag/v2.8.4) and [WinSW-x64.exe 2.12.0](https://github.com/winsw/winsw/releases/tag/v2.12.0) on another machine and point to them. |
| `-WebPort 8000` | Internal port for the web app, if 8000 is taken. |

### Using the university's own certificate or IIS instead of Caddy

If the site is only reachable inside the campus network, Let's Encrypt cannot issue a
certificate. Either:
- put IT's certificate in Caddy: edit the `tls` line the installer generates in
  `deploy\windows\services\Caddyfile` to `tls C:\certs\duties.crt C:\certs\duties.key`, then
  `Restart-Service InvigilationCaddy` (note: re-running the installer rewrites this file), or
- stop and disable `InvigilationCaddy` and have IIS (URL Rewrite + Application Request Routing)
  forward to `http://127.0.0.1:8000`, setting the header `X-Forwarded-Proto: https`.

## 5. Day-to-day operation

| Task | Command (PowerShell as administrator, in `C:\InvigilationDuties`) |
|---|---|
| Is everything OK? | `.\deploy\windows\status.ps1` |
| Update to new code | `.\deploy\windows\update.ps1` (backs up first, ~1 minute downtime) |
| List backups | `.\deploy\windows\restore.ps1` |
| Restore a backup | `.\deploy\windows\restore.ps1 -File backups\invigilation-YYYYMMDD-HHMMSS.dump` |
| Restart the app | `Restart-Service InvigilationWeb` |
| Remove the services | `.\deploy\windows\uninstall.ps1` (keeps data, backups and `.env`) |

**Logs** are in `C:\InvigilationDuties\logs\`:
- `app.log`: errors from the application
- `InvigilationWeb.*.log`, `InvigilationCaddy.*.log`: service output
- `caddy-access.log`: every web request
- `jobs.log`: output of the scheduled jobs (emails sent, reminders, backups)

## 6. Backups

- Every night at 02:00 the database is saved to `C:\InvigilationDuties\backups\`
  (`.dump` files for PostgreSQL). Backups older than **30 days** are deleted automatically.
- **Copy the backups off the machine.** A backup on the same disk does not survive a disk failure.
  Point the university's file backup at the `backups` folder, or change `BACKUP_DIR` in `.env`
  to a network share (e.g. `\\fileserver\invigilation-backups`) that the machine's account can write.
- `restore.ps1` takes one more backup of the current data before restoring, so a mistaken restore can be undone.
- **Test a restore once after installing**, and again each semester:
  run `restore.ps1`, pick last night's file, then log in and check the data is there.
- Also keep a copy of `.env` somewhere safe: it holds the secret key and passwords.

## 7. Pilot run checklist

Before the first real exam period:

- [ ] `status.ps1` shows both services running, all three jobs `OK`, and a backup from last night.
- [ ] `https://` works from a phone on mobile data (not only on campus Wi-Fi).
- [ ] Send yourself an activation email from *Faculty accounts*; it arrives within 5 minutes and the link opens the site.
- [ ] A restore of last night's backup was tested.
- [ ] Run one small period end to end with a few departments: import, open availability,
      HOD approvals, allocate, publish, download PDFs, mark attendance.
- [ ] The machine reboots cleanly: after a restart the site comes back on its own.
