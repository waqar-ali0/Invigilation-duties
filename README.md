# Invigilation Duty System

A web system for the exam office to divide exam invigilation duties among faculty
fairly and without clashes, and to keep a permanent record of every assignment.

**Phase 1 (core):** the exam office imports departments, faculty and the exam
schedule, runs the automatic allocation, reviews and edits the draft, publishes it,
and downloads rosters.

**Phase 2 (faculty-facing):** faculty activate their accounts from an email, see and
download their own duties (PDF or calendar file), and request dates they can't
invigilate; HODs approve their department's requests; the system emails people when
availability opens, when a request is decided, when the roster is published, and the
evening before each duty.

**Phase 3 (operations):** after publishing, faculty swap duties with colleagues (the
exam office approves), the exam office replaces people in emergencies, exam staff mark
who was present or absent, and the exam office gets attendance/swap reports and an
audit-log viewer.

Designs: [`Phase 1 spec`](docs/superpowers/specs/2026-10-20-invigilation-duty-system-design.md),
[`Phase 2 spec`](docs/superpowers/specs/2026-10-20-phase-2-faculty-facing-design.md),
[`Phase 3 spec`](docs/superpowers/specs/2026-10-21-phase-3-operations-design.md).

## Setup (Windows)

Requires Python 3.13.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env          # then put a long random value in SECRET_KEY
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py runserver
```

Open <http://127.0.0.1:8000/> and log in with the email and password you just created.

To try it with realistic data (20 departments, 200 faculty, a 10-day exam period):

```powershell
.venv\Scripts\python manage.py load_demo
```

## Users and roles

Every user logs in with their email address. Roles:

| Role | Access |
|---|---|
| **Admin** (exam office) | Exam periods, imports, allocation, publishing, unavailability requests, swaps, replacements, attendance, reports, audit log, faculty accounts, emails, and the Django admin panel. |
| **HOD** | Their own duties, requests and swaps, plus *Department requests* (approve/reject) and *Department duties* (with an attendance/swap summary). |
| **Faculty** | *My duties* (PDF and calendar download, *Ask a colleague* to swap), *My unavailability*, *Swap requests*. |
| **Exam Staff** | *Attendance*: mark invigilators present or absent, room by room. |

To create an exam office user: open **Admin panel → Users → Add user**, enter the
email and password, and set **Role** to *Admin (exam office)*. That user automatically
becomes staff and joins the *Exam Office* group.

**Faculty accounts:** open **Accounts** and click **Send activation emails**. Each
person gets a link (valid 7 days) to set their password; use **Resend** if a link
expires. Logins use the email address, in any letter case.

**HODs:** set a department's *HOD* in **Admin panel → Departments**. That person's
account becomes an HOD account automatically (and goes back to Faculty if replaced).
HODs never decide their own requests; those appear on the exam office's **Requests** page.

## How a period works

1. **Admin panel → Exam periods → Add**: enter name and dates (and, if needed, the per-day limit
   and the student/invigilator ratio). No duty numbers are built in: set them on the period's
   **Workload rules** page (see *Workload rules* below) and **confirm** them.
2. **Import departments**, then **Faculty → Import from Excel** and **Rooms → Import rooms**
   (once; re-import any time to update — see *Imports* below).
3. Open the period → **Import schedule**.
4. **Open availability** on the dashboard with a deadline. Faculty are emailed and
   request their unavailable dates; HODs approve or reject them. The dashboard warns
   how much capacity would drop if all pending requests were approved. (The exam office
   can also enter requests directly in **Admin panel → Unavailability**.)
5. **Run allocation**. Check the capacity warnings, fairness (min/max/average) and the
   unfilled-seats list. Re-run as often as you like: manual assignments are kept.
   If a request is approved, or someone is made exempt or inactive, *after* allocation, the
   dashboard and the publish page list the **duty conflicts** so nobody is published on a day
   they were approved off; re-run the allocation (or use Replace once published) to fix them.
6. Click a room to **add or remove** invigilators. Hard rules (same slot, unavailable,
   caps) are always enforced; soft rules ask for confirmation.
7. **Review and publish**. After publishing the roster is locked, and everyone with
   duties is emailed their list with a calendar file attached.
8. Download the **roster PDF / Excel** and the **faculty load** sheet.

9. **After publishing:**
   - **Swaps:** a faculty member uses *Ask a colleague* on My duties (until the period's
     *swap cut-off*, default 24 hours before the exam; change it in the period settings).
     The colleague accepts on *Swap requests*, then the exam office approves on **Swaps**.
     Approval re-checks every rule; both people are emailed.
   - **Emergencies:** on a room page, **Replace** gives a duty to someone else (a reason
     is required); both people are emailed and any open swap on that duty is cancelled.
   - **Attendance:** exam staff (or the exam office) open **Attendance**, pick the time
     slot once it has started, and mark each invigilator *Present* or *Absent*
     (or *Mark all present* for a room). Staff can correct their own marks the same day;
     later corrections are made by the exam office. If someone was absent and a colleague
     covered, use **Record stand-in** on the absent person's row: the absence stays on
     record and the stand-in is credited with a completed duty.
   - **Reports:** on the dashboard, *Attendance & swaps report* shows per person and per
     department how many duties were assigned, completed, absent, swapped and replaced,
     plus the full swap/replacement history (Excel download). *Completed* counts only
     duties marked present.

Every allocation run, edit, publish, swap, replacement and attendance mark is written to
the audit log (**Audit log** page, with filters), which nobody can edit or delete. Once a period is published,
its dates, rules, time slots, courses and rooms become read-only in the admin panel too.

## Workload rules

Every duty number belongs to one exam period and is set by the exam office on the period's
**Workload rules** page (dashboard → *Rules* card). Nothing is hard-coded; a target that is
not set is shown as *Target not set*, never as 0.

- **Defaults:** the maximum per person (empty = none) and per day.
- **By designation:** a target and how it applies — *Exactly*, *At least*, *At most*, *About*
  (preferred) — plus optional minimum and maximum. Visiting faculty can instead get **duties
  per course** they teach in this period.
- **Personal rules** override the designation for one person: another target, *exempt in this
  period*, or an *exception* allowing duties for someone marked inactive (reason required).
- **Counting:** whether excused / unexcused absences, make-up duties and stand-ins count toward
  the target, and which other periods' duties count together (e.g. midterms + finals).
- **Copy from another period** to start from last semester; the other period never changes.
- **Confirm** the rules before publishing (publishing asks for it). Every confirmation, and
  every change after publishing, is kept as a numbered **version** you can open later.

The allocation engine fills people below their minimum first, then whoever is furthest below
their own target (or the equal share when they have none), never above a maximum. **Workload
per person** shows target, assigned, completed, pending, missed, handed over, counted and
remaining for everyone, with filters and highlights (below / at target, near / over limit,
no target set).

## Faculty, rooms and imports

**Faculty** (sidebar → *Faculty*) lists everyone with filters (department, designation, status,
missing or invalid phone numbers), a profile page with the full duty history across periods,
and add/edit forms. Every faculty member has a **Faculty ID**: given by you, or generated as
`FAC-0001`, `FAC-0002`… It never changes afterwards. Email is optional (needed only for an
account); phone numbers are stored as typed and in international form (`+923001234567`,
default country code 92, set `PHONE_COUNTRY_CODE` in `.env` to change it).

**Designations** (*Faculty → Designations*) are editable: add, rename, or take out of use.
Lecturer, Assistant/Associate Professor, Professor, Lab Engineer, Visiting Faculty and Backup
Faculty exist from the start. No designation carries a fixed number of duties.

**Rooms** (sidebar → *Rooms*) is the permanent room list: building, campus, capacity, and
optionally a fixed number of invigilators (otherwise worked out from the student numbers).
Rooms are never deleted — take them out of use instead. Schedules link to it by room name.

**Imports** (sidebar → *Imports*) bring in faculty, rooms and courses from **your own
spreadsheet** (`.xlsx`, `.xls` or `.csv`, first sheet, headings in the first row):

1. Upload. 2. Choose which column holds what — guesses are made from headings such as
"Teacher Name", "Rank", "Cell No." — plus options (department for rows without one, add
designations/departments that are new). 3. Check every row before anything is saved:
*new*, *update* (with each change shown old → new), *no change*, *needs your decision*,
*needs correction* (with the reason). Untick rows to leave them out; download the check as
Excel; choose *update existing and add new* or *only add new*. Then confirm.

Faculty are matched by **faculty ID, phone number or email — never by name alone**: someone
with only the same name is shown for you to decide (skip, add as new, or update that person).
Blank cells never erase saved values, and duty history is never touched. Every import is
kept in the import history (file, who, when, result). Templates can be downloaded.

**Departments:** `name` (simple upload on *Exam periods → Import departments*).

**Exam schedule** — through the same wizard from the period's *Import schedule*; every row
must be correct before it can replace the schedule. Columns:
`date`, `start_time`, `end_time`, `course_code`, `course_name`, `department`, `room`,
`student_count`, and optionally `building` and course teachers (emails or faculty IDs,
separate several with `;`). Rooms are matched to the room list (missing rooms can be added
automatically); a room's fixed invigilator number, if set, is used for its exams.
Dates as `YYYY-MM-DD` or `DD/MM/YYYY` (or Excel date cells); times as `HH:MM`.
Re-importing **replaces** the period's whole schedule, so always upload the complete,
corrected file. It is only allowed while the period has no duties (use *Clear draft* first).
Exams on the same day whose times overlap (e.g. 09:00–12:00 and 10:00–13:00) count as a
clash: nobody is given both.

**Shared halls** are handled automatically: when several courses sit in the same room in the
same time slot (room names compared ignoring case and spaces), the hall gets **one** team of
invigilators sized from all its students together (e.g. 40 + 35 students → 3 invigilators,
not 2 + 2). The team is listed under the hall's first course; the other courses' room pages
point to it, and the roster shows the same team on every course in the hall. This is applied
on import and again at every allocation run.

## Email and scheduled jobs

The system never sends email while a page loads: emails are queued, then sent by a job.
On the **Emails** page the exam office sees queued, sent and failed emails, can
**Resend** failed ones, and can **Send queued emails now**. Failed sends are retried
automatically up to 3 times.

During development emails are written as files to `sent_emails/` instead of being
sent. For real sending, set the SMTP values and `SITE_URL` in `.env` (see `.env.example`).

Two commands must run on a schedule on the server:

| Command | When | What |
|---|---|---|
| `manage.py send_emails` | every 5 minutes | sends queued emails |
| `manage.py send_reminders` | daily at 18:00 | queues reminders for tomorrow's duties (never twice) |

On the server, `deploy\windows\install.ps1` schedules both (plus the nightly backup) in
Task Scheduler. To test reminders for a particular day: `manage.py send_reminders --date 2026-12-01`.

## Deployment (Windows server)

The full guide for IT is **[deploy/windows/DEPLOY.md](deploy/windows/DEPLOY.md)**. In short, from
PowerShell opened as administrator in the project folder:

```powershell
.\deploy\windows\install.ps1 -Domain duties.university.edu -AdminEmail it@university.edu
```

That installs two Windows services — the app on Waitress (`manage.py serve`) and Caddy for
automatic HTTPS — schedules email, reminders and a nightly backup (kept 30 days), and turns on
production security (`HTTPS=True`: secure cookies, http→https redirect, HSTS).
`status.ps1`, `update.ps1`, `restore.ps1` and `uninstall.ps1` in the same folder cover daily operation.

Backups by hand: `manage.py backup_db` and `manage.py restore_db <file> --yes`.
Monitoring can poll `/healthz/` (answers `ok` when the app and database are up).

## Tests

Unit and integration tests:

```powershell
.venv\Scripts\python -m pytest
```

Full-system audit — a fresh throw-away database, every workflow through the real pages:

```powershell
.venv\Scripts\python scripts\audit_e2e.py
```

Page crawl — opens every page each role can reach in your local database (read-only).
By default it logs in as the demo accounts; pass other emails to check real accounts:

```powershell
.venv\Scripts\python scripts\audit_crawl.py
```

Both print what they checked and exit with an error status if anything failed.

## Configuration

Set in `.env` (see `.env.example`): `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`,
`DATABASE_URL` (SQLite by default; PostgreSQL in production, e.g.
`postgres://user:pass@host:5432/invigilation`), `TIME_ZONE` (default `Asia/Karachi`).
