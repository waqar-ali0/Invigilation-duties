# Phase 5 — Closing the gaps with the full "Smart Examination Duty Management" spec

Date: 2026-10-27. Source: the user's full specification (sections 1–29 plus room import and
automatic faculty IDs). Decision: **extend the existing Django system** (the spec allows this:
"If an existing project already exists … extend working components rather than unnecessarily
replacing the application"). The React/Node/MongoDB stack is a preference, not a requirement; a
rewrite would discard a tested engine, audit log, deployment and 451 tests.

Terminology: the spec's *examination session* is the existing **exam period**; *shift* is a
**time slot**; *Super Administrator* is a superuser, *Examination Coordinator* the exam office
(admin role), *Department Coordinator* the HOD role.

## Already covered (kept, extended where noted)

Roles enforced on the server · periods with their own rules · constraint-based engine (overlap,
unavailability, caps, daily limit; soft back-to-back / own course; explains unfilled seats) ·
manual edits kept on re-run · unavailability with HOD approval · publish checks and locking ·
swaps, emergency replacement keeping the original duty, attendance, stand-ins · audit log ·
PDF/Excel exports · email outbox with retries and reminders · search · mobile layout · Windows
deployment with HTTPS and nightly backups · shared halls staffed once.

## Gaps and design, by stage

### Stage A — Faculty and room data foundation
- **Faculty categories** become a table (`FacultyCategory`: name, active, order) instead of a
  fixed list; seeded with Professor, Associate Professor, Assistant Professor, Lecturer,
  Lab Engineer, Visiting Faculty, Backup Faculty. Admins add, rename, deactivate. Existing
  designations migrate. No category carries a duty count.
- **Faculty ID** generated on the server as `FAC-0001…` when none is given, from a locked counter
  row (safe under concurrent imports); never changes on edit. Existing IDs are kept.
- **Faculty profile** gains phone (as entered + normalised international form), employment
  status, remarks, courses taught. **Email becomes optional** (needed only for an account).
- **Courses** (`Course`: code, name, department) and who teaches them.
- **Import wizard** (faculty, rooms, courses): upload `.xlsx`/`.xls`/`.csv` → map the file's
  columns to fields (auto-guessed from synonyms such as "Teacher Name", "Mobile") → preview
  classifying each row (new · reliable match · possible duplicate · invalid · duplicate in
  file) → choose rows, mode (*add new only* / *update existing and add new*), and resolve
  possible duplicates → confirm. Matching: faculty ID, then phone, then email; **a name alone
  never matches** (shown as a possible duplicate for a decision). Error report download.
  Every import is recorded (file, who, when, counts). Nothing is saved before confirmation.
- **Room inventory**: `Building` (name, campus) and `Room` (building, name, capacity,
  invigilators needed by default, special requirements, active). Manual create/edit/deactivate
  pages and room import. Schedule rows link to the inventory room; unknown rooms are reported
  in the schedule import and can be added.

### Stage B — Dynamic workload configuration
- Period rules lose their built-in numbers: a new period's maximum per person is **unset**
  until the office sets it, and unset is shown as "Not set", never as 0.
- Per period, per category rule: target (exact / minimum / maximum / preferred), min, max,
  duties per course (visiting faculty). Per person override: target, kind, max, exemption for
  this period, reason. **Copy rules from another period.**
- Counting policy per period: whether missed (excused / unexcused), cancelled and make-up
  duties count toward the target.
- Workload calculator (one function used by dashboards, reports and the engine): target,
  assigned, completed, pending, missed, cancelled, make-up owed/done, remaining, history.
- Rule changes after publishing are versioned (snapshot with who/when); earlier periods are
  never touched.

### Stage C — Allocation extensions
- Engine: per-person min/target/max; fills people below their target first, never above max;
  exact targets; visiting duties per course.
- **Preferences** (soft): preferred dates, slots, buildings; faculty submit them in the
  availability window; the engine favours them; the run reports how many were met.
- **Lock** individual duties; re-run keeps locked and manual duties; **re-generate selected
  slots** only.
- **Backup pool**: backup priority, reserve-only, eligible buildings, max backup duties;
  reserve-only people are not used for regular duties; per-slot backup reservations
  (reserved → called → assigned → performed); replacement suggestions ordered by backup
  priority, never someone busy in an overlapping slot.
- Pre-allocation **validation** page and richer failure explanations (per slot: needed,
  eligible, unavailable, busy, at cap, and suggested fixes).

### Stage D — Operations
- **Missed duties**: absent → reason, excused or not, make-up obligation required or not.
  **Obligations** linked to the original duty; assign a make-up duty later (any published
  period), track completion; distinguish the absentee from whoever covered.
- Faculty **confirm receipt** of duties.
- **Roster versions**: snapshot at publish and after every post-publish change; compare two
  versions (who / room / slot changed).
- Move a duty to another room or slot; override reasons recorded; workload exceptions with
  reason.
- In-app **notifications** (bell in the top bar) alongside emails.

### Stage E — Dashboards and reports
- Admin dashboard for the selected period: cards that link to details, server-rendered SVG
  charts (no external scripts), coverage %, upcoming slots, recent changes.
- Workload table (filters, highlights: below / at / near / over target, no target, owing
  make-up) and per-person workload page.
- Report centre with filters (period, dates, department, category, person, room, building,
  status) and **CSV / Excel / PDF** for: duty records, room-wise, date/slot-wise,
  department and category workload, visiting per-course, assigned vs completed, missed,
  obligations, backup use, unfilled, fairness, preference satisfaction, period comparison,
  overrides and exceptions, over-limit, outstanding obligations.
- Timetable/calendar view; sortable, paginated tables; session selector in the top bar;
  collapsible sidebar.

### Stage F — WhatsApp (official Cloud API only)
- Contact fields: normalised number, verified, consent status/date/source, preference.
  Faculty can opt in/out.
- Settings page (IDs and template names in the database; **token and app secret only in
  `.env`**), connection test, test message to one chosen number, clear "not configured" state.
- Templates per message kind with language and parameter fields, preview.
- Message log with a **unique key per duty + kind + occurrence** (no duplicates, even with
  concurrent schedulers), statuses scheduled → queued → submitted → sent → delivered → read /
  failed / cancelled, provider id, error, attempts; controlled retries; webhook updates with
  signature check; export.
- Daily reminder at the configured time (Task Scheduler runs the queue every 5 minutes; the
  dispatch time and days ahead are settings). Messaging centre with recipient filters,
  preview and confirmation. Tomorrow's duties page and reminder preview.

### Stage G — Google Sheets (optional)
Service-account read-only access to one sheet, same mapping/preview pipeline as the Excel
import, manual sync button and optional schedule. Excel import works without it.

## Testing
Each stage keeps all existing tests green, adds tests for its acceptance items (spec §24 and
§29), extends `scripts/audit_e2e.py`, and is committed separately.
