# Invigilation Duty Management System — Design Spec

**Date:** 2026-10-20
**Status:** Draft, awaiting review
**Builder:** single developer
**Deployment target:** undecided (university server or small VPS); design is hosting-neutral

---

## 1. Purpose

A web system the university will use in production to:

1. Divide exam invigilation duties among faculty members fairly and without clashes.
2. Keep a permanent, auditable record of who was assigned, swapped, replaced, present, or absent.

### Success criteria

- The exam office produces a complete, clash-free roster for an exam period from imported data in under an hour of human effort (vs. manual Excel work today).
- No faculty member exceeds the duty cap set by the manager for that period.
- Duty counts within a period are as even as constraints allow (max − min ≤ 1 among unconstrained faculty).
- Every change to a duty after publishing is traceable to a person and a time.

### Scale

- ~200 faculty, 20+ departments.
- One exam period ≈ a few hundred sessions/rooms, ≈ 1,000–2,000 duty slots.

### Out of scope (v1)

- Remuneration / payment calculation.
- Carrying duty loads across semesters (each exam period starts fresh).
- Invigilator roles (chief vs. regular) — all invigilators are equal.
- Self check-in / QR attendance.
- Mobile app (the web UI must be usable on phones, but no native app).
- SSO (Google/Microsoft) — possible later addition.

---

## 2. Architecture

| Concern | Choice |
|---|---|
| Backend | Django (Python) |
| Database | PostgreSQL |
| UI | Server-rendered Django templates + HTMX for small interactive parts; no separate SPA |
| Back-office screens | Django Admin (customized) for data management |
| Exports | PDF (rosters), Excel (rosters, reports), .ics (personal calendar) |
| Email | SMTP — university server or transactional provider (Brevo/SendGrid free tier) |
| Background jobs | Django management commands run by cron (reminder emails, nightly backup). No task queue in v1. |
| Packaging | Docker Compose: `web` (Django + Gunicorn), `db` (PostgreSQL), `nginx` |
| TLS | Let's Encrypt |

Rationale: one codebase, one language, built-in auth/admin, minimal moving parts for a solo maintainer.

### Code organization (Django apps)

Each app has one clear responsibility:

- `accounts` — users, roles, login.
- `faculty` — departments, faculty profiles, designations.
- `exams` — exam periods, sessions, room allocations, period settings, imports.
- `allocation` — the allocation engine (pure Python, no Django views), draft review, publishing.
- `duties` — duties, unavailability, swaps, replacements, attendance.
- `notifications` — email composition and sending, reminders.
- `reports` — report queries and PDF/Excel/ICS exports.
- `audit` — append-only audit log.

The allocation engine is a plain-Python module that takes input data structures and returns assignments + unfilled seats. It does not touch the database directly; a thin service layer loads data, calls it, and saves the result. This keeps it fast to test.

---

## 3. Roles and permissions

| Role | Can do |
|---|---|
| **Admin** (exam office manager) | Everything for exam operations: periods, settings, imports, allocation, manual edits, publish, approve swaps, replacements, reports. |
| **Exam Staff** | View rosters; mark attendance (completed / absent). Nothing else. |
| **HOD** | View own department's faculty and duties; approve/reject unavailability requests from own department; view department reports. |
| **Faculty** | View own duties; download own PDF/.ics; submit unavailability; request and accept swaps. |
| **Superuser** | Developer only; system maintenance. |

A user has exactly one role. An HOD is also a faculty member, so HODs additionally have all Faculty abilities for their own duties.

Rules:

- Faculty never see other faculty's unavailability reasons.
- HODs see only their own department.
- All permission checks are enforced server-side on every view, not only by hiding UI.

---

## 4. Data model

### Department
- `name` (unique)
- `hod` → Faculty (nullable)

### Faculty
- `user` → User (one-to-one; nullable until account is activated)
- `name`, `email` (unique), `employee_id` (unique)
- `department` → Department
- `designation` (e.g. Professor, Associate Professor, Assistant Professor, Lecturer, Visiting)
- `is_active` (bool) — inactive faculty are never allocated
- `is_exempt` (bool) — exempt from invigilation entirely

### ExamPeriod
- `name` (e.g. "Fall 2026 Finals"), `start_date`, `end_date`
- `status`: `draft` → `availability_open` → `allocating` → `published` → `closed`
- `availability_deadline` (datetime)

### PeriodSettings (one-to-one with ExamPeriod)
- `max_duties_per_faculty` (int, default 8)
- `max_duties_per_day` (int, default 2)
- `students_per_invigilator` (int, default 30)
- `min_invigilators_per_room` (int, default 2)
- `avoid_back_to_back` (bool, default true) — soft rule
- `exclude_course_teacher` (bool, default true) — soft rule

### DesignationCap
- `period` → ExamPeriod, `designation`, `max_duties` (int)
- Optional overrides: for that period, faculty with that designation use this cap instead of `max_duties_per_faculty`.

### TimeSlot
- `period` → ExamPeriod, `date`, `start_time`, `end_time`
- Two TimeSlots are "back-to-back" if they are on the same date and consecutive in start-time order.

### ExamSession
- `slot` → TimeSlot
- `course_code`, `course_name`
- `department` → Department
- `course_teachers` → many Faculty (optional; used by `exclude_course_teacher`)

### RoomAllocation
- `session` → ExamSession
- `room` (string, e.g. "B-204")
- `student_count` (int)
- `invigilators_required` (int) — computed on import as `max(min_invigilators_per_room, ceil(student_count / students_per_invigilator))`; manager may edit.

### Unavailability
- `faculty` → Faculty, `period` → ExamPeriod
- `date` (required), `slot` → TimeSlot (nullable; null = whole day)
- `reason` (text)
- `status`: `pending` / `approved` / `rejected`
- `reviewed_by` → User, `reviewed_at`
- Only `approved` records block allocation.

### Duty (core record)
- `faculty` → Faculty
- `room_allocation` → RoomAllocation
- `status`: `assigned` / `completed` / `absent` / `cancelled`
- `source`: `auto` / `manual` / `swap` / `replacement`
- `created_at`, `updated_at`
- Constraint: unique (`faculty`, `room_allocation`) among non-cancelled duties.

Duties are never hard-deleted after publishing; they are marked `cancelled` and a new Duty is created (e.g. on swap/replacement), so history is preserved.

### SwapRequest
- `duty` → Duty (requester's duty)
- `requested_by` → Faculty, `requested_with` → Faculty
- `status`: `pending_peer` → `pending_admin` → `approved` / `rejected` / `cancelled`
- Timestamps for each transition.
- v1 swaps are **one-way handovers**: the peer takes over the requester's duty. (The peer does not give a duty back.)

### AuditLog (append-only)
- `actor` → User, `timestamp`
- `action` (e.g. `duty.assign`, `duty.edit`, `duty.swap`, `duty.replace`, `duty.attendance`, `period.publish`, `settings.change`)
- `object_type`, `object_id`
- `before` / `after` (JSON)
- No update or delete permission for any role, including Admin, through the application.

---

## 5. Allocation algorithm

### Inputs
- All RoomAllocations in the period (each needing `invigilators_required` seats).
- Eligible faculty: `is_active = true`, `is_exempt = false`.
- Approved Unavailability.
- PeriodSettings and DesignationCaps.
- Any existing manual duties in the draft (kept as fixed; algorithm fills the rest).

### Step 1 — Capacity check (before allocating)
- `seats_needed` = Σ `invigilators_required` − existing fixed duties.
- `capacity` = Σ over eligible faculty of their effective cap (designation cap if set, else `max_duties_per_faculty`), minus fixed duties already counted.
- If `seats_needed > capacity`, show a warning with both numbers and let the manager either adjust settings or run anyway (shortages will be reported).
- Also warn per slot: if seats needed in a slot > faculty available in that slot.

### Step 2 — Greedy fill
1. Build the list of all empty seats, grouped by TimeSlot.
2. Order slots **hardest first**: ratio of seats needed to faculty available in that slot, highest first.
3. For each seat, the candidate set is faculty who pass all **hard rules**:
   - Not already assigned in the same TimeSlot.
   - Not approved-unavailable for that date/slot.
   - Below their effective period cap.
   - Below `max_duties_per_day` on that date.
4. Rank candidates by:
   1. Soft-rule violations (0 first): back-to-back slot (if `avoid_back_to_back`), is a course teacher of that session (if `exclude_course_teacher`).
   2. Fewest duties so far in this period.
   3. Department spread: prefer a department not already in that room.
   4. Random tie-break, using a stored seed so a run can be reproduced.
5. Assign the top candidate. If no candidate exists, record the seat as **unfilled** with the reason (e.g. "all eligible faculty at cap", "all remaining faculty unavailable").

### Step 3 — Draft output
- Draft duties with `source = auto`.
- Unfilled seat list with reasons.
- Fairness summary: min / max / average duties among eligible faculty; count of soft-rule violations.

### Manager actions on draft
- **Re-run:** discards all `auto` draft duties, keeps `manual` ones, reruns with a new seed.
- **Manual edit:** add/remove/move a duty. Hard rules are checked; violations are blocked with the reason shown. Soft-rule violations are allowed with a warning.
- **Publish:** only allowed when the manager confirms (unfilled seats may remain, but must be acknowledged). Sets period to `published`, triggers notifications, writes AuditLog.

### Determinism and testability
- The engine is a pure function: `allocate(seats, faculty, constraints, seed) -> (assignments, unfilled, stats)`.
- Same inputs + same seed → identical output.

---

## 6. Workflows

### 6.1 Setup (Admin)
1. One-time: import faculty and departments from Excel/CSV.
2. Create ExamPeriod; set PeriodSettings and any DesignationCaps.
3. Import TimeSlots, ExamSessions and RoomAllocations from Excel/CSV (one template file, documented columns).
4. Imports are **all-or-nothing**: every row is validated first; if any row fails, nothing is saved and an error list is shown (e.g. "Row 14: unknown department 'CSE2'").

### 6.2 Availability window
1. Admin opens availability (status → `availability_open`) with a deadline.
2. Faculty receive an email; they submit unavailable dates/slots with reasons.
3. HOD approves/rejects each request for their department.
4. After the deadline, faculty can no longer submit; Admin can still add/approve on their behalf.

### 6.3 Allocation and publishing
As in section 5. On publish, each faculty member is emailed their duty list.

### 6.4 After publishing
- **My Duties page:** list of the faculty member's duties, PDF download, .ics calendar download.
- **Reminder:** email the evening before each duty (cron job, daily at 18:00).
- **Swap:** Faculty A requests handover of one duty to Faculty B → B accepts → Admin approves → system re-checks hard rules for B at approval time → old Duty `cancelled`, new Duty for B with `source = swap`. Both are notified. Any step can reject.
- **Emergency replacement:** Admin clicks "Replace" on a duty → system lists eligible faculty (passing hard rules, sorted by fewest duties) → Admin picks one → old Duty `cancelled`, new Duty with `source = replacement`. Both are notified.

### 6.5 Exam day — attendance
- Exam Staff opens a room-wise checklist for a TimeSlot and marks each duty `completed` or `absent`.
- Attendance can be corrected later by Admin; every change is audited.

### 6.6 Close
- Admin closes the period (status → `closed`); duties become read-only.

---

## 7. Reports

All filterable by exam period; exportable to Excel, rosters also to PDF.

- **Faculty load:** duties per faculty member, with min/max/average.
- **Absence & swap history:** per faculty member.
- **Room-wise roster:** per TimeSlot, rooms and assigned invigilators (printable).
- **Day-wise roster:** per date.
- **Department summary:** for HODs — their faculty's duty counts and absences.
- **Unfilled seats:** current list.

---

## 8. Notifications

| Trigger | Recipient |
|---|---|
| Availability window opened | All eligible faculty |
| Unavailability approved/rejected | Requesting faculty |
| Roster published | Each faculty member with duties |
| Swap requested | Peer |
| Swap accepted (awaiting admin) | Admin |
| Swap approved/rejected | Both faculty |
| Replacement made | Removed and new faculty |
| Evening-before reminder | Faculty with a duty the next day |

Email failures are logged and shown to Admin on a "failed notifications" list with a resend button; they never block the underlying action.

---

## 9. Security and operations

- Login with email + password; Django's password hashing; password reset by email.
- Accounts are created by import; faculty set their password via an activation email.
- Server-side permission checks on every view; CSRF protection (Django default).
- HTTPS only.
- AuditLog as in section 4.
- Nightly `pg_dump` backup via cron, kept for 30 days; documented restore procedure, tested once before go-live.
- Environment-based configuration (`.env`) so the same image runs on any host.

---

## 10. Error handling

- Imports: validate all rows, report all errors, save nothing on failure.
- Allocation: never crashes on shortages; reports unfilled seats with reasons.
- Manual edits / swaps / replacements: hard-rule violations blocked with the reason shown.
- Concurrent edits: duty changes use database transactions with row locking so two admins cannot double-assign the same seat.
- Email: failures logged and retryable, non-blocking.

---

## 11. Testing

- **Allocation engine (unit tests, highest priority):**
  - No faculty member in two rooms in the same slot.
  - Period cap and per-day cap never exceeded; designation caps respected.
  - Approved unavailability honored; pending/rejected ignored.
  - Exempt/inactive faculty never assigned.
  - Even distribution (max − min ≤ 1 when unconstrained).
  - Shortages reported with reasons, not raised as errors.
  - Same seed → same output.
  - Manual duties preserved across re-runs.
- **Permissions:** each role can only reach what section 3 allows (faculty can't see others' duties; HOD limited to own department; Exam Staff limited to attendance).
- **Imports:** valid file saves all rows; one bad row saves nothing and is reported.
- **Workflows:** swap and replacement state transitions, including hard-rule re-check at approval.
- **End-to-end:** import → availability → allocate → publish → swap → attendance → reports.
- **Performance check:** allocation for 200 faculty / 2,000 seats completes in under 10 seconds.

---

## 12. Build phases

| Phase | Scope | Rough effort (solo) |
|---|---|---|
| **1 — Core** | Models, Django Admin, Excel imports, PeriodSettings, allocation engine + draft review + manual edit, publish, PDF/Excel rosters | 3–4 weeks |
| **2 — Faculty-facing** | Faculty accounts & activation, My Duties page, .ics, unavailability + HOD approval, email notifications & reminders | 2–3 weeks |
| **3 — Operations** | Swaps, emergency replacement, attendance (Exam Staff), reports, audit log viewer | 2 weeks |
| **4 — Go-live** | Docker deployment on chosen host, HTTPS, backups + restore test, pilot in one exam period | 1–2 weeks |

Phase 1 alone is usable: the exam office can generate and print a fair roster.

---

## 13. Open questions

- **Hosting:** university server vs. VPS — to be decided before Phase 4. No design impact.
- **Email sender:** university SMTP vs. external provider — needed before Phase 2.
- **Import templates:** exact column layout of the university's existing exam schedule spreadsheet — needed early in Phase 1 to match their real files.
