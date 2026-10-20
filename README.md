# Invigilation Duty System

A web system for the exam office to divide exam invigilation duties among faculty
fairly and without clashes, and to keep a permanent record of every assignment.

This is **Phase 1 (core)**: the exam office imports departments, faculty and the exam
schedule, runs the automatic allocation, reviews and edits the draft, publishes it,
and downloads rosters. The design is in
[`docs/superpowers/specs/2026-10-20-invigilation-duty-system-design.md`](docs/superpowers/specs/2026-10-20-invigilation-duty-system-design.md).

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

| Role | Phase 1 access |
|---|---|
| **Admin** (exam office) | Everything below, plus the Django admin panel for faculty, departments, exam periods and settings. |
| Exam Staff, HOD, Faculty | No screens yet; their pages arrive in Phases 2–3. |

To create an exam office user: open **Admin panel → Users → Add user**, enter the
email and password, and set **Role** to *Admin (exam office)*. That user automatically
becomes staff and joins the *Exam Office* group.

## How a period works

1. **Admin panel → Exam periods → Add**: enter name and dates. Default rules are created
   (max 8 duties per person, max 2 per day, 1 invigilator per 30 students with a minimum
   of 2, avoid back-to-back slots, avoid a teacher's own course). Change them on the same
   page; add *designation caps* (e.g. Professor = 4) if some ranks should do fewer.
2. **Import departments**, then **Import faculty** (once; re-import to update).
3. Open the period → **Import schedule**.
4. Enter approved unavailability in **Admin panel → Unavailability** (status *Approved*).
5. **Run allocation**. Check the capacity warnings, fairness (min/max/average) and the
   unfilled-seats list. Re-run as often as you like: manual assignments are kept.
6. Click a room to **add or remove** invigilators. Hard rules (same slot, unavailable,
   caps) are always enforced; soft rules ask for confirmation.
7. **Review and publish**. After publishing the roster is locked.
8. Download the **roster PDF / Excel** and the **faculty load** sheet.

Every allocation run, manual edit and publish is written to the audit log
(Admin panel → Audit logs), which nobody can edit or delete.

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
The schedule can only be re-imported while the period has no duties (use *Clear draft* first).

## Tests

```powershell
.venv\Scripts\python -m pytest
```

## Configuration

Set in `.env` (see `.env.example`): `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`,
`DATABASE_URL` (SQLite by default; PostgreSQL in production, e.g.
`postgres://user:pass@host:5432/invigilation`), `TIME_ZONE` (default `Asia/Karachi`).

## Coming next

- **Phase 2:** faculty accounts and "My Duties" page, calendar download, unavailability
  requests with HOD approval, email notifications and reminders.
- **Phase 3:** swaps, emergency replacements, attendance by exam staff, reports, audit viewer.
- **Phase 4:** Docker deployment (PostgreSQL, Nginx, HTTPS), nightly backups, pilot run.
