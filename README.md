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

## WhatsApp reminders

Through the **official WhatsApp Business Platform (Cloud API)** only — no WhatsApp Web
automation. Sidebar → **WhatsApp**.

- **Set up** (once, see the page's checklist): Meta business account and number, then put
  `WHATSAPP_TOKEN`, `WHATSAPP_APP_SECRET` and `WHATSAPP_VERIFY_TOKEN` in the server's `.env`
  (never in the database or pages), enter the phone number ID, create the message templates
  in WhatsApp Manager and enter their approved names, point Meta's webhook at
  `https://<site>/whatsapp/webhook/`. *Test the connection* and *Send test* check it. Until all
  of this is done the page says **not set up** and nothing is sent or shown as sent.
- **Consent:** faculty opt in or out on their own WhatsApp page (or the exam office records a
  consent given on paper, with its source); numbers come from the faculty phone numbers and
  can be overridden; a number WhatsApp rejects is marked invalid.
- **Reminders:** every day at the dispatch time (default 18:00, Asia/Karachi) reminders are
  queued for duties *days ahead* (default tomorrow) and sent by the `whatsapp_tick` job
  (every 5 minutes; the installer schedules it). Each duty gets one reminder per day —
  running the job again, or twice at once, never sends a second one. Cancelled or handed-over
  duties are not reminded. Optional notices when a duty is given, moved, replaced or cancelled.
- **Tomorrow's duties** shows who will be reminded and why not when someone can't be (no
  number, not opted in, opted out, email only, invalid number) — the reminder preview.
- **Send a message:** pick people, a department, everyone with duties in a period or on a
  date; write the announcement; preview recipients and text; confirm.
- **Message log:** scheduled → queued → submitted → sent → delivered → read, or failed /
  cancelled. *Submitted* only means WhatsApp accepted it; the rest comes from WhatsApp's signed
  status reports. Temporary errors are retried (3 tries); permanent ones are not. Retry by
  hand, cancel, or export CSV.

## Dashboard, reports and timetable

- **Dashboard** (the exam office's home page): pick the period in the top bar. Cards for
  faculty, rooms, coverage, duties (completed / pending / missed / not yet confirmed), missed
  duties and make-ups owed, backups, below target, over limit and no target — each opens the
  records behind it. Charts: duty completion, average duties by designation, how many people
  have how many duties, and seats filled per time slot (hover any bar for its numbers). Every
  number is counted live from the records with the same calculator as the workload pages.
- **Reports** (sidebar): faculty-, room- and slot-wise duty lists, department and designation
  workload, visiting faculty per course, assigned vs completed, missed duties, make-up
  obligations (all / outstanding), backup use, unfilled seats, fairness, preference
  satisfaction, period comparison, overrides and exceptions, over-limit, and workload per
  person. Filter by period, dates, department, designation, person, room, building and status;
  click a column heading to sort; download as **CSV, Excel or PDF**.
- **Timetable** (dashboard): dates × time slots with courses, rooms and seats filled.
- The sidebar can be collapsed to icons on a desktop (button next to the menu).

## Missed duties, make-ups, moves and roster versions

- **Missed duties** (sidebar): every duty marked absent. Record the reason, *excused* or
  *unexcused*, and whether a **make-up duty** is owed. The absence is never erased; whoever
  covered keeps their own stand-in duty. A make-up is scheduled later into any upcoming exam
  (the system lists the rooms where the person is free; their maximum does not block a make-up)
  and is tracked: owed → scheduled → done (or missed again, or waived). Workload counting
  follows the period's rules (e.g. excused absences count, unexcused don't).
- **Move** a duty to another room or slot from its room page. In a draft the duty itself moves;
  once published the original stays (handed over) and a new one is created, a reason is
  required, and the person is emailed with a new calendar file.
- **Roster versions** (dashboard, after publishing): the roster at publishing and after every
  swap, replacement, stand-in, make-up or move. Compare any two versions (or a version with now)
  to see which people, rooms and time slots changed.
- **Confirm receipt:** faculty see *new* on unconfirmed duties and press *I have seen these duties*.
- **Notifications:** every email to someone with an account also appears under the bell in the
  top bar (account and password links stay email-only). Faculty without an email get no email
  — add one on their faculty page.
- **Duty slips:** a PDF of anyone's duties from their faculty page.

## Preferences, locking, backups and checks

- **Preferences** (faculty: sidebar → *Preferences*, while availability is open; exam office:
  dashboard → *Preferences*): preferred dates, time slots or buildings, or a slot someone would
  rather avoid. They are wishes: the engine follows them among people with the same workload,
  never at the cost of fairness, and never leaves a seat empty for them. Each run reports how
  many were met. Assigning someone by hand against a preference asks for a reason (audit log).
- **Locking:** on a room page, *Lock* keeps a duty when the allocation is run again.
  **Re-run this slot** on the dashboard re-generates one time slot only; everything else stays.
- **Backups** (sidebar → *Backups*): the backup pool, with priority, *regular + backup* or
  *backup only* (kept in reserve, not given regular duties), optional building limits and a
  maximum; *Add a whole designation* puts e.g. all Lab Engineers in. Per period (dashboard →
  *Backups*) choose how many backups to keep free per time slot — reserved after every run,
  released automatically if they become busy — and whether backup-only members may fill seats
  nobody else can take. Replacement suggestions list reserved backups first, then the pool,
  never anyone busy at that time; the reservation then shows *called*, the duty they took and
  whether it was performed. The absent person's original duty always stays on record.
- **Check** (dashboard): before allocating and publishing, a list of everything missing or
  wrong — schedule, rooms not in the room list or over capacity, unset targets, unconfirmed
  rules, capacity and per-slot shortages, pending requests, conflicts, people over their limit
  or below their minimum, unfilled seats with their reasons — each with what to do about it.

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
