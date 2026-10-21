# Invigilation Duty System — Phase 3 (Operations) Design Spec

**Date:** 2026-10-21
**Status:** Draft, awaiting review
**Builds on:** Phases 1–2 (on `main`); parent spec `2026-10-20-invigilation-duty-system-design.md` (§4 SwapRequest, §6.4, §6.5, §7)

---

## 1. Purpose and scope

Phase 3 handles what happens to a roster after it is published: people swap duties, the exam office replaces people in emergencies, exam staff record who actually showed up, and the exam office can report on all of it and read the audit trail.

### In scope
1. Swap requests: faculty → colleague accepts → exam office approves; cut-off before the exam (period setting, default 24 h).
2. Emergency replacement by the exam office.
3. Attendance marking by exam staff (room-wise checklist), corrections, auditing.
4. Reports: faculty record, department summary, swap/replacement history (+ Excel); department summary for HODs.
5. Audit log viewer.

### Out of scope
Deployment, PostgreSQL, HTTPS, backups (Phase 4). Automatic emails on absence (decision: recorded only). Changing allocation caps based on absences.

### Success criteria
- No swap or replacement can ever produce a clash, exceed a cap or daily limit, or assign someone unavailable — checked with the same engine rules at the moment of approval.
- Every swap, replacement, attendance mark and correction is traceable in the audit log; cancelled duties are never deleted.
- Exam staff can mark a full slot (≈30 rooms) in a few minutes from a phone.
- Reports show assigned, completed and absent duties per person, with absent duties not counted as done.

---

## 2. Data model changes

### PeriodSettings
- `swap_cutoff_hours` — PositiveIntegerField, default 24. Swap requests can be created and accepted only until this many hours before the slot starts.

### Duty
- `replaces` — FK to Duty (null), the duty this one took over from (swap or replacement). History chain.
- `attendance_marked_by` — FK User (null), `attendance_marked_at` — datetime (null).
- Status meanings (unchanged values): `assigned` (not yet marked), `completed` (present), `absent`, `cancelled` (handed over to someone else).

### SwapRequest (new, `duties` app)
| Field | Notes |
|---|---|
| `duty` | FK Duty (the requester's duty) |
| `requested_by` | FK Faculty (owner of `duty`) |
| `requested_with` | FK Faculty (colleague asked) |
| `message` | text, blank |
| `status` | `pending_peer` → `pending_admin` → `approved`; or `declined` (by colleague), `rejected` (by exam office), `cancelled` (by requester, or because the duty was replaced), `expired` (exam started) |
| `created_at`, `responded_at`, `decided_at` | datetimes |
| `decided_by` | FK User (null) |
| `comment` | text, blank (exam office's comment) |
| `new_duty` | FK Duty (null) — the duty created on approval |

Constraint: at most one open request (`pending_peer` or `pending_admin`) per duty.

---

## 3. Published-roster lock

Phase 1's `ensure_editable` forbids changes once published. Phase 3 actions are the opposite: they **require** a published period.
- `ExamPeriod.lock_published(pk)` — `select_for_update` the period; if status ≠ `published`, raise `PeriodLockedError("Only a published roster can be changed this way.")` (`closed` periods are final).
- Every swap decision, replacement and attendance mark runs inside `transaction.atomic()` after `lock_published`, so they are serialised per period.

## 4. Swaps

### Rules
- **Who can be asked:** active, non-exempt faculty with an activated account (usable password), other than the requester, who pass every hard rule for the duty's room: not in that or an overlapping slot, not unavailable (approved), under their period cap and daily limit. Candidates sorted by fewest active duties, then name; soft-rule warnings shown.
- **Cut-off:** requests can be created and accepted only while `now < slot start − swap_cutoff_hours` (slot start in `TIME_ZONE`). The exam office may approve until the slot starts.
- **Expiry:** any open request whose slot has started is treated as `expired`; it is set to `expired` whenever it is loaded for an action or listed (no scheduled job).
- One open request per duty; the requester may cancel while open.

### Flow
1. **Request** (Faculty/HOD, from My Duties → "Ask a colleague"): choose colleague, optional message → `pending_peer`; email to colleague. Audit `swap.request`.
2. **Colleague** (new page *Swap requests* with incoming and outgoing lists): **Accept** → `pending_admin` (email to exam office address list = all Admin users; audit `swap.accept`); **Decline** → `declined`, email to requester (audit `swap.decline`). Accept re-checks hard rules and the cut-off; if they fail, the accept is refused with the reason.
3. **Exam office** (page *Swaps*): **Approve** → in one transaction after `lock_published`: re-check hard rules for the colleague (excluding nothing — the requester's duty stays theirs until now); old duty → `cancelled`; new Duty (`source=swap`, `replaces=old`, same room) for the colleague; request `approved`, `new_duty` set; audit `swap.approve`; emails to both, colleague's with an `.ics`. **Reject** (optional comment) → `rejected`; emails to both; audit `swap.reject`.
4. If a duty is replaced (§5) while a swap on it is open, the swap becomes `cancelled` and both people are emailed.

## 5. Emergency replacement

- On the room page of a **published** period, each active (non-cancelled) duty shows **Replace** until the slot ends.
- The replace page lists candidates (same hard-rule filter as swaps, no account needed), fewest duties first, with soft warnings, plus a required **reason** field.
- Confirm → in one transaction after `lock_published`: re-check hard rules; old duty → `cancelled`; new Duty (`source=replacement`, `replaces=old`); cancel open swaps on the old duty; audit `duty.replace` (before/after faculty, reason); emails: to the replaced person ("you no longer have this duty") and the new person (duty details + `.ics`).
- A duty already marked `completed` or `absent` cannot be replaced.

## 6. Attendance

- **Exam Staff home → Attendance.** Date picker (default today) listing the slots of published periods on that date; choosing a slot shows each room (course, room, students) with its active duties and, per person, **Present** / **Absent** buttons and current status. **Mark all present** per room marks every still-`assigned` duty in that room `completed`.
- Marking allowed only after the slot has started (`now ≥ slot start`).
- Exam Staff may change marks they made on the same local date; after that (or for marks by others) only Admin can change them. Admin uses the same page (linked from the dashboard).
- Each mark sets `status`, `attendance_marked_by`, `attendance_marked_at`; audit `duty.attendance` with before/after status. Cancelled duties cannot be marked.
- Absences are recorded only — no emails.
- **Stand-in (added 2026-10-21 at the user's request):** for a duty marked absent, exam staff or the exam office can **Record stand-in**: choose who covered and give a note. The absence stays; the stand-in gets a new duty (`source=stand_in`, `replaces=<absent duty>`) already marked completed. Only a clash with another room at the same time blocks it (caps and unavailability don't — the person did it). One stand-in per absent duty; audited as `duty.stand_in`; no emails. Reports count it as "taken over" for the stand-in and list it in the history as "Stand-in".

## 7. Reports (Admin; Excel download each)

Per period:
- **Faculty record:** per faculty member with any duty: Assigned (active duties: assigned+completed+absent), Completed, Absent, Not yet marked, Swapped away (their duties cancelled via swap), Taken over (duties they received by swap or replacement), Replaced (their duties cancelled via replacement), Absence dates.
  - **Completed** is the "done" figure; absent duties do not count as done (decision A + C).
- **Department summary:** the same totals aggregated per department.
- **Swap & replacement history:** each approved swap and each replacement: date/slot, room, from, to, type, reason/comment, approved/done by, when.
- HOD's Department Duties page gains the department summary rows for their departments.

## 8. Audit log viewer (Admin)

- `/audit/` — table: time, who, action, object, before → after (rendered as "field: old → new" lines). Filters: action (select), actor (search by email), date from/to, period. 50 per page, newest first. Read-only.

## 9. Emails (through the existing outbox)

| Trigger | Recipient |
|---|---|
| Swap requested | Colleague |
| Swap accepted | All Admin users |
| Swap declined | Requester |
| Swap approved | Requester and colleague (colleague gets `.ics`) |
| Swap rejected | Requester and colleague |
| Swap cancelled because the duty was replaced | Requester and colleague |
| Replacement | Replaced person and new person (new person gets `.ics`) |

## 10. Error handling
- Every action re-checks state inside its transaction: status of the request, duty still active, hard rules, cut-off, slot started/ended. Failures show a plain message; nothing changes.
- Requests and duties are always looked up scoped to the user (own requests, own duties, own role) → 404 otherwise.

## 11. Testing
- Swap: full happy path; cut-off for request and accept; expiry; colleague becomes ineligible between accept and approve → approval blocked; duplicate open request refused; requester cancel; replace cancels open swap.
- Replacement: rules re-checked; cannot replace completed/absent; emails; history chain via `replaces`.
- Attendance: before slot start refused; mark all present; same-day correction by staff allowed, next-day refused, admin allowed; audit entries.
- Reports: counts with a mix of completed/absent/swapped/replaced; absent not counted as completed.
- Audit viewer: filters and pagination; admin only.
- Permissions on every new view (anonymous, wrong role, other person's objects).

## 12. Build order (one plan)
1. Model changes, `lock_published`, shared candidate/rule helper.
2. Swap requests (request, accept/decline, cancel, expiry).
3. Exam office swap approval.
4. Emergency replacement.
5. Attendance.
6. Reports (+ HOD summary).
7. Audit viewer.
8. End-to-end test, README.
