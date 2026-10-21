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

1. **Admin panel → Exam periods → Add**: enter name and dates. Default rules are created
   (max 8 duties per person, max 2 per day, 1 invigilator per 30 students with a minimum
   of 2, avoid back-to-back slots, avoid a teacher's own course). Change them on the same
   page; add *designation caps* (e.g. Professor = 4) if some ranks should do fewer.
2. **Import departments**, then **Import faculty** (once; re-import to update).
3. Open the period → **Import schedule**.
4. **Open availability** on the dashboard with a deadline. Faculty are emailed and
   request their unavailable dates; HODs approve or reject them. The dashboard warns
   how much capacity would drop if all pending requests were approved. (The exam office
   can also enter requests directly in **Admin panel → Unavailability**.)
5. **Run allocation**. Check the capacity warnings, fairness (min/max/average) and the
   unfilled-seats list. Re-run as often as you like: manual assignments are kept.
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
     later corrections are made by the exam office.
   - **Reports:** on the dashboard, *Attendance & swaps report* shows per person and per
     department how many duties were assigned, completed, absent, swapped and replaced,
     plus the full swap/replacement history (Excel download). *Completed* counts only
     duties marked present.

Every allocation run, edit, publish, swap, replacement and attendance mark is written to
the audit log (**Audit log** page, with filters), which nobody can edit or delete. Once a period is published,
its dates, rules, time slots, courses and rooms become read-only in the admin panel too.

## Spreadsheet formats

Upload `.xlsx` (first sheet) or `.csv`. The first row holds the column headings
(case and spacing don't matter). A file with any error saves nothing and lists every
problem by row number.

**Departments:** `name`

**Faculty:** `employee_id`, `name`, `email`, `department`, `designation`
— designation is one of Professor, Associate Professor, Assistant Professor, Lecturer, Visiting.
Existing faculty are matched by `employee_id` and updated.

**Exam schedule** (one row per room):
`date`, `start_time`, `end_time`, `course_code`, `course_name`, `department`, `room`,
`student_count`, and optionally `course_teacher_emails` (separate several with `;`).
Dates as `YYYY-MM-DD` or `DD/MM/YYYY` (or Excel date cells); times as `HH:MM`.
Re-importing **replaces** the period's whole schedule, so always upload the complete,
corrected file. It is only allowed while the period has no duties (use *Clear draft* first).
Exams on the same day whose times overlap (e.g. 09:00–12:00 and 10:00–13:00) count as a
clash: nobody is given both.

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

Linux (cron, `crontab -e`):

```
*/5 * * * * cd /srv/invigilation && .venv/bin/python manage.py send_emails
0 18 * * *  cd /srv/invigilation && .venv/bin/python manage.py send_reminders
```

Windows (Task Scheduler, from an administrator command prompt in the project folder):

```bat
schtasks /create /tn "Invigilation send emails" /sc minute /mo 5 /tr "\"%CD%\.venv\Scripts\python.exe\" \"%CD%\manage.py\" send_emails"
schtasks /create /tn "Invigilation reminders" /sc daily /st 18:00 /tr "\"%CD%\.venv\Scripts\python.exe\" \"%CD%\manage.py\" send_reminders"
```

To test reminders for a particular day: `manage.py send_reminders --date 2026-12-01`.

## Tests

```powershell
.venv\Scripts\python -m pytest
```

## Configuration

Set in `.env` (see `.env.example`): `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`,
`DATABASE_URL` (SQLite by default; PostgreSQL in production, e.g.
`postgres://user:pass@host:5432/invigilation`), `TIME_ZONE` (default `Asia/Karachi`).

## Coming next

- **Phase 4:** Docker deployment (PostgreSQL, Nginx, HTTPS), nightly backups, pilot run.
