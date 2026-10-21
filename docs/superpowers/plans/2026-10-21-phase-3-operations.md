# Invigilation Duty System — Phase 3 (Operations) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After publishing, rosters can change safely — swaps (colleague accepts, exam office approves), emergency replacements, attendance marking — and the exam office can report on it and read the audit trail.

**Architecture:** Business rules in small modules in the `duties` app: `handover.py` (shared rule check, candidates, the cancel-and-create transaction, replacement), `swaps.py` (swap lifecycle), `attendance.py`. Reports in `reports/operations.py`. Views in `portal` (faculty, staff), `allocation` (room page), a new `audit/views.py`. All rule checks reuse the allocation engine's `AllocationState` via `build_engine_input`. Every change to a published roster runs in `transaction.atomic()` after `ExamPeriod.lock_published`.

**Tech Stack:** unchanged (Django 5.2, Python 3.13, SQLite dev / PostgreSQL prod, openpyxl, reportlab, pytest-django). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-21-phase-3-operations-design.md`

## Global Constraints

- Phase 3 actions require a **published** period; `closed` is final. `ExamPeriod.lock_published(pk)` raises `PeriodLockedError("Only a published roster can be changed this way.")` otherwise.
- `PeriodSettings.swap_cutoff_hours` default **24**. Swap request and accept allowed only while `now < slot.starts_at − cutoff`; admin approve allowed while `now < slot.starts_at`.
- Open swap = status `pending_peer` or `pending_admin`; at most one per duty.
- Hard rules are the engine's: same/overlapping slot, approved unavailability, period cap, daily limit. Re-checked at every decision inside the transaction.
- Cancelled duties are never deleted; new duties carry `replaces=<old duty>` and `source` `swap`/`replacement`.
- Attendance: only after `slot.starts_at`; exam staff may change only their own marks on the same local date; Admin any time. Absences send no email.
- Reports: "Completed" is the done figure; absent duties are not counted as done.
- Times: slot start/end are local (`TIME_ZONE`) → `TimeSlot.starts_at` / `ends_at` aware datetimes.
- All emails via `notifications.outbox.queue_email`; templates rendered with `render_email` (autoescape off).
- Permissions: anonymous → login; wrong role → 403; another person's object → 404.
- Commands on Windows: `.venv\Scripts\python ...`.

## Review Focus

1. **Colleague becomes ineligible between accepting and approval** (e.g. they were given a replacement duty in the same slot). Expected: approval blocked with the rule's message; nothing changes. *(Task 3)*
2. **Cut-off arithmetic in local time** — a request 23 hours before a 09:00 Asia/Karachi exam is refused; 25 hours before is allowed. *(Task 2)*
3. **Replacing a duty that has an open swap** — Expected: swap becomes `cancelled`, both people emailed, replacement goes through. *(Task 4)*
4. **Exam staff correcting a mark the next day** — Expected: refused; Admin can. *(Task 5)*
5. **Chains** — a duty swapped and then the new duty replaced: reports count one "swapped away" for the first person, one "taken over" + one "replaced" for the second, one "taken over" for the third. *(Task 6)*

---

## File Structure

```
duties/      models.py (+SwapRequest, Duty.replaces/attendance fields)  handover.py  swaps.py  attendance.py
             admin.py (SwapRequest read-only)  tests/test_handover.py test_swaps.py test_swap_approval.py
             test_replacement.py test_attendance.py
exams/       models.py (+swap_cutoff_hours, lock_published, TimeSlot.starts_at/ends_at)
allocation/  services.py (build_engine_input: exclude_duty_ids)  views.py (+replace views)  urls.py
portal/      views.py (+ask colleague, swap requests, admin swaps, attendance)  urls.py
reports/     operations.py  excel.py (+operations sheets)  views.py (+reports page)  urls.py
audit/       views.py  urls.py
notifications/ triggers.py (+swap/replacement emails)
templates/   portal/ask_colleague.html swap_requests.html admin_swaps.html attendance.html
             allocation/replace.html  reports/operations.html  audit/log.html  emails/swap_*.txt replacement_*.txt
tests/test_phase3_end_to_end.py
```

---

### Task 1: Foundations — models, published lock, slot times, handover rule check

**Files:**
- Modify: `exams/models.py`, `duties/models.py`, `duties/admin.py`, `allocation/services.py`, `exams/admin.py` (no change needed — settings inline picks up the field)
- Create: `duties/handover.py`
- Test: `duties/tests/test_handover.py`

**Interfaces:**
- Produces:
  - `PeriodSettings.swap_cutoff_hours = PositiveIntegerField(default=24)`.
  - `ExamPeriod.lock_published(pk) -> ExamPeriod` (classmethod; call inside atomic).
  - `TimeSlot.starts_at` / `TimeSlot.ends_at` — properties: `timezone.make_aware(datetime.combine(date, start_time/end_time))`.
  - `Duty.replaces = ForeignKey("self", null, blank, SET_NULL, related_name="replaced_by")`, `Duty.attendance_marked_by = FK User null SET_NULL related_name="+"`, `Duty.attendance_marked_at = DateTimeField(null)`.
  - `duties.models.SwapStatus` — `PENDING_PEER="pending_peer"`, `PENDING_ADMIN="pending_admin"`, `APPROVED="approved"`, `DECLINED="declined"`, `REJECTED="rejected"`, `CANCELLED="cancelled"`, `EXPIRED="expired"`; `OPEN_SWAP_STATUSES = (PENDING_PEER, PENDING_ADMIN)`.
  - `SwapRequest(duty FK Duty CASCADE related_name="swap_requests", requested_by FK Faculty, requested_with FK Faculty related_name="swap_invitations", message TextField blank, status default PENDING_PEER, created_at auto, responded_at null, decided_at null, decided_by FK User null, comment TextField blank, new_duty FK Duty null related_name="+")`; `UniqueConstraint(fields=["duty"], condition=Q(status__in=OPEN_SWAP_STATUSES), name="one_open_swap_per_duty")`.
  - `allocation.services.build_engine_input(period, exclude_auto=False, include_pending=False, exclude_duty_ids=())` — duties in `exclude_duty_ids` are left out of `fixed` (their seat counts as free).
  - `duties.handover.class HandoverBlocked(Exception)` with `.errors: list[str]`.
  - `handover_check(duty, faculty) -> tuple[list[str], list[str]]` — hard errors and soft warnings for giving `duty`'s seat to `faculty`, with `duty` itself excluded from the state. Errors: `"Faculty is inactive or exempt"` (alone, if so), `"This is already their duty"` (same faculty), then engine hard errors.
  - `handover_candidates(duty, require_account: bool) -> list[tuple[Faculty, int, list[str]]]` — eligible faculty with no hard errors (excluding the duty's owner; with `require_account`, only users with a usable password and `is_active`), with active duty count and warnings, sorted by `(len(warnings), count, name)`.
  - `transfer_duty(duty, faculty, source: str) -> Duty` — inside the caller's transaction: `duty.status = CANCELLED`, create `Duty(faculty, room_allocation=duty.room_allocation, source=source, replaces=duty)`; returns the new duty. No checks (callers check first).

- [ ] **Step 1: Write the failing tests** — `lock_published` raises for draft/closed and returns for published; `starts_at` is aware and equals 04:00 UTC for 09:00 Asia/Karachi; `handover_check` returns `["Already assigned in this time slot"]` for someone busy in that slot, `["This is already their duty"]` for the owner, `[]` + `[]` for a free person (room full warning NOT present because the duty is excluded); `handover_candidates(require_account=True)` excludes faculty without usable password; `transfer_duty` cancels old, links `replaces`; second open SwapRequest on the same duty raises IntegrityError.
- [ ] **Step 2: Run** `.venv\Scripts\python -m pytest duties/tests/test_handover.py -q` → FAIL.
- [ ] **Step 3: Implement**; `makemigrations exams duties`.
- [ ] **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: published-roster lock, slot times, swap model and handover rules`

---

### Task 2: Swap requests (faculty side)

**Files:**
- Create: `duties/swaps.py`, `templates/portal/ask_colleague.html`, `templates/portal/swap_requests.html`, `templates/emails/swap_requested_{subject,body}.txt`, `swap_accepted_{subject,body}.txt`, `swap_declined_{subject,body}.txt`
- Modify: `notifications/triggers.py`, `portal/views.py`, `portal/urls.py`, `templates/portal/my_duties.html` ("Ask a colleague" per upcoming duty; badge when a swap is open), `templates/base.html` ("Swap requests" for Faculty/HOD)
- Test: `duties/tests/test_swaps.py`, `portal/tests/test_swap_views.py`

**Interfaces:**
- Consumes: Task 1 (`handover_check`, `handover_candidates`, `SwapRequest`, `TimeSlot.starts_at`).
- Produces:
  - `duties.swaps.class SwapError(Exception)` with `.errors: list[str]`.
  - `cutoff_passed(duty, now=None) -> bool` — `now >= slot.starts_at − timedelta(hours=settings.swap_cutoff_hours)`.
  - `expire_if_started(swap) -> SwapRequest` — open swap whose slot has started → status EXPIRED (saved); returns swap.
  - `request_swap(duty, colleague, message="") -> SwapRequest` — errors (collected): `"Only a published roster can be changed this way."` (period not published), `"This duty can no longer be swapped."` (duty not ASSIGNED), `"It is too late to swap this duty. Contact the exam office."` (cutoff passed), `"There is already an open swap request for this duty."`, `"<name> has no active account."` (colleague without usable password/inactive), plus `handover_check` hard errors. Creates PENDING_PEER, audit `swap.request`, queues `swap_requested` to colleague.
  - `accept_swap(swap, faculty) -> SwapRequest` — not `requested_with` → `PermissionDenied`; `expire_if_started`; status ≠ PENDING_PEER → `SwapError(["This request is no longer open."])`; cutoff passed → the cut-off message; hard errors re-checked → `SwapError(errors)`; → PENDING_ADMIN, `responded_at`; audit `swap.accept`; queues `swap_accepted` to every active Admin user.
  - `decline_swap(swap, faculty) -> SwapRequest` — only `requested_with`; open PENDING_PEER only; → DECLINED; audit `swap.decline`; email requester (`swap_declined`).
  - `cancel_swap(swap, faculty) -> SwapRequest` — only `requested_by`; open only; → CANCELLED; audit `swap.cancel`.
  - `notifications.triggers.queue_swap_email(swap, name: str, recipients: list[str], attachment=None) -> None` — renders `emails/<name>_*` with `{"swap": swap, "link": absolute_url(reverse("swap_requests"))}`.
  - URL names (faculty_required): `ask_colleague` `/my/duties/<int:duty_id>/swap/` (GET candidate list from `handover_candidates(require_account=True)`, POST `colleague_id`, `message`; own duty only else 404), `swap_requests` `/my/swaps/` (incoming open, outgoing all with status; expired ones shown as expired), `swap_respond` `/my/swaps/<int:pk>/respond/` (POST `action=accept|decline`), `swap_cancel` `/my/swaps/<int:pk>/cancel/` (POST).

- [ ] **Step 1: Write the failing tests** — happy request → email to colleague; each error message; cut-off: with cutoff 24 and slot 09:00 local on 2026-12-02, `now` = 2026-12-01 09:30 local → refused, 08:30 → allowed (use `freezegun`-free approach: pass `now` to `cutoff_passed`, and in service tests set `swap_cutoff_hours` large/small instead of freezing time); accept → PENDING_ADMIN + admin email; accept by someone else → PermissionDenied; accept after colleague gets a duty in that slot → SwapError with rule message; decline; cancel; expire when slot started (set slot in the past via `make_room` on a past period... use `swap_cutoff_hours=0` and a slot date in the past through direct ORM update of TimeSlot date + published status); views: ask page lists only account-holders, other person's duty 404, respond/cancel scoped 404.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: swap requests between colleagues`

---

### Task 3: Exam office swap approval

**Files:**
- Create: `templates/portal/admin_swaps.html`, `templates/emails/swap_approved_{subject,body}.txt`, `swap_rejected_{subject,body}.txt`
- Modify: `duties/swaps.py`, `portal/views.py`, `portal/urls.py`, `templates/base.html` ("Swaps" for Admin), `templates/exams/dashboard.html` (count of swaps awaiting approval when published)
- Test: `duties/tests/test_swap_approval.py`, `portal/tests/test_admin_swaps.py`

**Interfaces:**
- Produces:
  - `approve_swap(swap, actor) -> Duty` — inside `atomic()`: `lock_published(period)`; reload swap `select_for_update`; `expire_if_started`; status ≠ PENDING_ADMIN → `SwapError(["This request is no longer waiting for approval."])`; duty not ASSIGNED → `SwapError(["This duty can no longer be swapped."])`; `handover_check(duty, colleague)` hard errors → `SwapError(errors)`; `transfer_duty(duty, colleague, DutySource.SWAP)`; swap → APPROVED, `decided_by`, `decided_at`, `new_duty`; audit `swap.approve` (`before={"faculty": old}`, `after={"faculty": new, "duty": new_duty.pk}`); emails `swap_approved` to both, colleague's with `.ics` of the new duty.
  - `reject_swap(swap, actor, comment="") -> SwapRequest` — PENDING_ADMIN only; → REJECTED with comment; audit `swap.reject`; emails both.
  - URL names (ADMIN): `admin_swaps` `/swaps/` (awaiting approval first, then recent decided), `swap_decide` `/swaps/<int:pk>/decide/` (POST `decision=approve|reject`, `comment`).

- [ ] **Step 1: Write the failing tests** — approve: old duty cancelled, new duty for colleague with `source=swap`, `replaces=old`, swap `new_duty` set, two emails (one with `.ics`), audit; colleague got a replacement duty in the same slot after accepting → approve raises with "Already assigned in this time slot" and nothing changes; approve on closed period → PeriodLockedError; reject → emails, status; views admin-only; dashboard shows "1 swap(s) waiting for approval".
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: exam office swap approval`

---

### Task 4: Emergency replacement

**Files:**
- Create: `templates/allocation/replace.html`, `templates/emails/replaced_out_{subject,body}.txt`, `replaced_in_{subject,body}.txt`, `swap_cancelled_{subject,body}.txt`
- Modify: `duties/handover.py`, `allocation/views.py`, `allocation/urls.py`, `templates/allocation/room_detail.html` (published: Replace button per active duty until slot ends; status column)
- Test: `duties/tests/test_replacement.py`, `allocation/tests/test_replace_views.py`

**Interfaces:**
- Produces:
  - `duties.handover.replace_duty(duty, faculty, actor, reason: str) -> Duty` — reason blank → `HandoverBlocked(["A reason is required."])`; inside `atomic()`: `lock_published`; reload duty `select_for_update`; status ≠ ASSIGNED → `HandoverBlocked(["Only duties not yet marked present or absent can be replaced."])`; `now >= slot.ends_at` → `HandoverBlocked(["This exam has already ended."])`; hard errors → `HandoverBlocked(errors)`; open swaps on the duty → CANCELLED + `swap_cancelled` emails to both; `transfer_duty(..., DutySource.REPLACEMENT)`; audit `duty.replace` (`before={"faculty": old}`, `after={"faculty": new, "reason": reason, "duty": new.pk}`); emails `replaced_out` (old person) and `replaced_in` (new person, `.ics`).
  - URL names (ADMIN): `replace_duty` `/periods/<int:pk>/duties/<int:duty_id>/replace/` (GET candidates from `handover_candidates(require_account=False)`; POST `faculty_id`, `reason`; duty scoped to period else 404).

- [ ] **Step 1: Write the failing tests** — success path (cancel+create+emails+audit+`replaces`); reason required; completed duty refused; open swap cancelled with emails; candidate busy in slot refused; draft period → PeriodLockedError; room page shows Replace only for published periods; view admin-only and scoped.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: emergency replacement`

---

### Task 5: Attendance

**Files:**
- Create: `duties/attendance.py`, `templates/portal/attendance.html`
- Modify: `portal/views.py` (`staff_home` redirects to `attendance`), `portal/urls.py`, `templates/base.html` (Exam Staff nav "Attendance"; Admin nav "Attendance"), `templates/exams/dashboard.html` (link to attendance when published)
- Test: `duties/tests/test_attendance.py`, `portal/tests/test_attendance_views.py`

**Interfaces:**
- Produces:
  - `duties.attendance.class AttendanceError(Exception)` (message string).
  - `can_change_mark(user, duty, now=None) -> bool` — Admin/superuser True; EXAM_STAFF True if duty unmarked, or marked by this user on the same local date as `now`; else False.
  - `mark_attendance(duty, actor, present: bool) -> Duty` — inside `atomic()`: `lock_published`; reload duty; CANCELLED → `AttendanceError("This duty was handed over to someone else.")`; `now < slot.starts_at` → `AttendanceError("Attendance can be marked once the exam has started.")`; not `can_change_mark` → `AttendanceError("Only the exam office can change this mark now.")`; set status COMPLETED/ABSENT, `attendance_marked_by`, `attendance_marked_at`; audit `duty.attendance` (`before={"status": old}`, `after={"status": new}`) — no-op (no audit) if unchanged.
  - `mark_room_present(room, actor) -> int` — marks every ASSIGNED duty in the room COMPLETED via `mark_attendance`; returns count.
  - URL names (role EXAM_STAFF or ADMIN): `attendance` `/attendance/` (`?date=YYYY-MM-DD`, default local today: slots of published periods that day), `attendance_slot` `/attendance/slots/<int:slot_id>/` (rooms with active+cancelled-excluded duties, Present/Absent buttons, Mark all present), `attendance_mark` `/attendance/duties/<int:duty_id>/` (POST `present=1|0`), `attendance_room_present` `/attendance/rooms/<int:room_id>/present/` (POST). Redirect back to the slot page with messages.

- [ ] **Step 1: Write the failing tests** — before start refused; mark present/absent with audit; unchanged mark not audited; staff same-day change OK, next-day refused (pass `now`), other staff's mark refused, admin OK; mark all present skips already-marked; cancelled refused; views: faculty 403, staff home redirects to attendance, slot page lists rooms and names.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: attendance marking by exam staff`

---

### Task 6: Operations reports

**Files:**
- Create: `reports/operations.py`, `templates/reports/operations.html`
- Modify: `reports/excel.py` (+`operations_xlsx`), `reports/views.py`, `reports/urls.py`, `templates/exams/dashboard.html` (link), `portal/views.py` + `templates/portal/department_duties.html` (HOD summary rows)
- Test: `reports/tests/test_operations.py`

**Interfaces:**
- Produces:
  - `reports.operations.FacultyRecord` dataclass: `faculty, assigned, completed, absent, unmarked, swapped_away, taken_over, replaced, absence_dates: list[date]`.
  - `faculty_records(period, departments=None) -> list[FacultyRecord]` — every faculty member with any duty (incl. cancelled) in the period, by name. `assigned` = non-cancelled; `completed`/`absent`/`unmarked` by status; `swapped_away` = their cancelled duties whose `replaced_by` has `source=swap`; `replaced` = same with `source=replacement`; `taken_over` = their duties with `source` in (swap, replacement).
  - `department_summary(period, departments=None) -> list[dict]` — per department: name and the summed counts of `FacultyRecord` fields (except dates/faculty), by name.
  - `handover_history(period) -> list[dict]` — one row per duty with `replaces` set: `{"when": duty.created_at, "slot": slot, "room": room, "from": old faculty, "to": new faculty, "type": "Swap"|"Replacement", "note": swap comment/message or replacement reason (from audit `after["reason"]`), "by": decided_by / audit actor}`, newest first.
  - `reports.excel.operations_xlsx(period) -> bytes` — sheets "Faculty record", "Department summary", "Swaps and replacements".
  - URL names (ADMIN): `operations_report` `/periods/<int:pk>/reports/`, `operations_xlsx` `/periods/<int:pk>/reports.xlsx`.
  - HOD Department Duties shows `department_summary(period, request.hod_departments)` above the table.

- [ ] **Step 1: Write the failing tests** — the Review Focus #5 chain (A→swap→B, B's new duty replaced→C) counts exactly; absent not counted as completed; department summary sums; history rows with types and notes; xlsx sheet names/headers; report page admin-only; HOD page shows summary for own departments only.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: operations reports for faculty, departments and handovers`

---

### Task 7: Audit log viewer

**Files:**
- Create: `audit/views.py`, `audit/urls.py`, `templates/audit/log.html`
- Modify: `config/urls.py`, `templates/base.html` ("Audit log" for Admin)
- Test: `audit/tests/test_viewer.py`

**Interfaces:**
- Produces:
  - `audit.views.describe_changes(entry) -> list[str]` — lines `"<field>: <before> → <after>"` for keys in either dict (missing side shown as `—`), keys sorted.
  - URL name `audit_log` `/audit/` (ADMIN) — GET filters `action` (exact, select of distinct actions), `actor` (email icontains), `date_from`, `date_to` (local dates, inclusive), `period` (entries whose object is the period, or whose `after`/`before` JSON is irrelevant — filter by `object_type="exams.ExamPeriod", object_id=<id>` OR duty/swap objects whose period matches: implement as: ExamPeriod entries for that id plus `object_type in (duties.Duty, duties.SwapRequest, duties.Unavailability)` with `object_id` in that period's object ids). Paginated 50 per page (`Paginator`), newest first; invalid dates ignored with a message.

- [ ] **Step 1: Write the failing tests** — filters each narrow results; pagination (60 entries → 2 pages); describe_changes output; admin only; read-only (no POST handler → 405).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: audit log viewer`

---

### Task 8: End-to-end test and README

**Files:**
- Create: `tests/test_phase3_end_to_end.py`
- Modify: `README.md` (roles table: Exam Staff attendance; workflow steps for swaps, replacement, attendance, reports, audit log; "Coming next": Phase 4 only)

- [ ] **Step 1: Write the test** — published roster → faculty A asks B (account) → B accepts → admin approves → B's duty replaced by admin with C (reason) → exam staff marks C present after start (move slot date to today and time to earlier via ORM, keep `now` real) and another duty absent → report counts and history match → audit viewer lists `swap.approve`, `duty.replace`, `duty.attendance` → `send_emails` sends everything with 0 failed.
- [ ] **Step 2: Run** → pass (failures are bugs in Tasks 1–7: fix with a regression test in the owning app).
- [ ] **Step 3: README**; **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `test: Phase 3 end-to-end flow; docs: README for operations`
