# Invigilation Duty System — Phase 2 (Faculty-facing) Design Spec

**Date:** 2026-10-20
**Status:** Draft, awaiting review
**Builds on:** Phase 1 (on `main`), parent spec `2026-10-20-invigilation-duty-system-design.md` (§3, §6.2, §6.4, §8)

---

## 1. Purpose and scope

Phase 2 lets faculty and HODs use the system themselves, and makes the system tell people what they need to know by email.

### In scope
1. Faculty accounts via activation email; password reset.
2. Automatic HOD role from the department's HOD field.
3. Availability window (open/close with deadline) and unavailability requests by faculty.
4. HOD approval of their department's requests; exam office approval of HODs' own requests.
5. My Duties page with PDF and `.ics` calendar download; Department Duties page for HODs.
6. Email outbox with scheduled sending, retries and a failed-email list; emails for the triggers in §7.
7. Evening-before reminders.
8. Dashboard warning about pending requests' effect on capacity.

### Out of scope (later phases)
Swaps, emergency replacements, attendance, audit-log viewer (Phase 3); Docker/PostgreSQL deployment, backups (Phase 4); single sign-on; per-person limits on unavailable days (decision: no limit, HOD judgment).

### Success criteria
- An admin can invite all ~200 faculty in one click; each can set a password from the email link within 7 days.
- A faculty member can see and download only their own published duties.
- An HOD can approve/reject only their own department's requests, never their own.
- Publishing a roster never waits on email delivery; every queued email is eventually sent or shown as failed with a resend option.
- Running the reminder job twice on the same day sends no duplicate reminders.

---

## 2. Accounts and activation

- Faculty remain imported as in Phase 1 (no user account).
- **Accounts page** (Admin): lists faculty grouped as *No account*, *Invited (not activated)*, *Active*. Buttons: **Send activation emails** (every active faculty member — exempt or not — who has no account and was never invited), **Resend** per person (anyone invited but not yet activated).
- Sending an invitation:
  - creates the `User` if missing: `email = faculty.email`, `role = HOD` if they are HOD of any department else `FACULTY`, unusable password, `is_active=True`; links `Faculty.user`;
  - sets `Faculty.invited_at = now`;
  - queues an activation email containing a link `/activate/<uidb64>/<token>/` built with Django's `default_token_generator`.
- `PASSWORD_RESET_TIMEOUT = 7 days` (604 800 s). The same setting governs password-reset links.
- **Activation page:** validates the token; shows a set-password form (Django `SetPasswordForm`); on success logs the user in and redirects to My Duties. An invalid/expired link shows "This link is invalid or has expired. Ask the exam office to resend it."
- **Forgot password:** Django's built-in password reset views, email sent through the outbox.
- Login, logout and password change use Django's built-in views and the existing templates style.
- **Email sync:** when the faculty import or admin changes a faculty member's email and they have a user, the user's email is updated to match.
- Faculty marked `is_active=False` have their user deactivated (`User.is_active=False`) so they can no longer log in; reactivating the faculty reactivates the user.

## 3. Roles

| Role | Phase 2 pages |
|---|---|
| Faculty | My Duties, My Unavailability |
| HOD | Faculty pages + Department Requests + Department Duties |
| Exam Staff | (unchanged; attendance arrives in Phase 3) — sees a placeholder home page |
| Admin | Phase 1 screens + Accounts + Requests (HODs' own and all others) + Emails |

- **HOD sync:** saving a Department with a new `hod` sets that faculty member's user role to `HOD` (if their role is `FACULTY`). The previous HOD's role returns to `FACULTY` if they are no longer HOD of any department. Admin and Exam Staff roles are never changed automatically.
- After login each role lands on its home: Admin → exam periods; HOD/Faculty → My Duties; Exam Staff → placeholder.
- All checks are server-side. Faculty only ever access objects linked to their own `Faculty`; HODs only objects of faculty in a department where they are `hod`. Admin-only pages keep `role_required(ADMIN)`.
- A user with role Faculty/HOD but no linked `Faculty` sees a message "Your account is not linked to a faculty record; contact the exam office."

## 4. Availability window

- Dashboard button **Open availability** (Admin), allowed when the period is editable (not published/closed): form with deadline (date + time, must be in the future and on/before the period's end date). Sets `status = AVAILABILITY_OPEN`, `availability_deadline`, queues the "window opened" email to every active, non-exempt faculty member with a user, and writes audit `availability.open`.
- **Close now** button sets `availability_deadline = now` (audit `availability.close`).
- The window is *open* when `status == AVAILABILITY_OPEN` and `now < availability_deadline`. No scheduled job closes it.
- Running allocation still sets status to `ALLOCATING` (Phase 1 behaviour); this closes the window for faculty submissions as well.
- Each period has its own window; several periods may have open windows at the same time.

## 5. Unavailability requests

### Model changes (`duties.Unavailability`)
- New status value `WITHDRAWN`.
- New fields: `review_comment` (text, blank), `created_at` (auto).
- Existing rule unchanged: only `APPROVED` blocks allocation.

### Faculty: My Unavailability
- Shown for faculty who are active and not exempt.
- Lists periods whose window is open, with a form: date (within the period), time slot (optional — "whole day" if empty; choices limited to that date's slots), reason (required).
- Validation: date within period; slot belongs to the period and date; no duplicate pending/approved request for the same faculty, date and slot (whole day counts as covering every slot of that date).
- Submitting creates a `PENDING` request and queues nothing (HODs see it on their page).
- A pending request can be **withdrawn** by its owner while the window is open (status `WITHDRAWN`).
- History of all their requests with status and review comment.

### HOD: Department Requests
- Pending requests from faculty of their department(s) **excluding their own**, oldest first, with Approve / Reject (optional comment); below, decided requests of the current periods.
- A decision sets `status`, `reviewed_by`, `reviewed_at`, `review_comment`, writes audit `unavailability.approve` / `unavailability.reject`, and queues a decision email to the requester.
- Decisions are allowed while the period is editable (not published/closed), even after the window closes.

### Admin: Requests
- All pending requests, with HODs' own requests highlighted; same Approve / Reject actions. Admins can also still create requests in the Django admin (Phase 1 path).

### Capacity warning
- Dashboard shows, when pending requests exist: "*N* request(s) pending. If all are approved, capacity drops to *C* duties (need *S*)." computed by running `capacity_check` with pending requests treated as approved.

## 6. Duties pages

- **My Duties** (Faculty/HOD): for each **published or closed** period, the person's active duties: date, time, course code and name, room. Upcoming first, then past. Downloads per period:
  - **PDF** — the person's duty list (reportlab, same style as the roster);
  - **.ics** — one `VEVENT` per duty: `SUMMARY "Invigilation: <course code>"`, `LOCATION <room>`, start/end from the slot in `TIME_ZONE`, stable `UID duty-<id>@invigilation`. Generated by hand (RFC 5545 text), no new dependency.
- **Department Duties** (HOD): for a published/closed period, each faculty member of their department(s) with duty count and the list of dates/rooms; Excel download.
- Draft rosters are never visible to Faculty or HOD.

## 7. Email

### Outbox (`notifications` app)
`OutboundEmail(to, subject, body, kind, status, attempts, last_error, created_at, sent_at, attachment_name, attachment_content, attachment_mimetype)`
- `status`: `queued` → `sent` | `failed`.
- `kind`: `activation`, `password_reset`, `availability_open`, `request_decision`, `roster_published`, `reminder`.
- `queue_email(to, subject, body, kind, attachment=None) -> OutboundEmail` is the only way the app sends mail.
- `send_queued(limit=None) -> (sent, failed)`: sends `queued` emails oldest first through Django's email backend, reusing one connection. On error: `attempts += 1`, `last_error` stored; after 3 attempts status becomes `failed`, otherwise stays `queued`.
- Management command `send_emails` runs `send_queued`. Scheduled every 5 minutes in production (cron / Task Scheduler — documented in README).
- **Emails page** (Admin): counts by status, the failed list with **Resend** (resets to `queued`, attempts 0), and **Send queued emails now**.

### Configuration
- `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`, `SITE_URL` (absolute links in emails) from `.env`.
- Default in development: `django.core.mail.backends.filebased.EmailBackend` writing to `sent_emails/` (git-ignored). Tests use Django's locmem backend.

### Triggers
| Trigger | Recipient | Content |
|---|---|---|
| Invitation | Faculty member | Activation link, valid 7 days |
| Password reset | User | Reset link |
| Availability opened | Active non-exempt faculty with a user | Period, deadline, link to My Unavailability |
| Request decided | Requester | Date/slot, decision, comment |
| Roster published | Each faculty member with ≥1 active duty in the period (account or not) | Their duty list, link to My Duties, `.ics` attached |
| Reminder | Faculty member with a duty tomorrow in a published period | Tomorrow's duties |

### Reminders
- `Duty.reminder_sent_at` (nullable datetime).
- Management command `send_reminders` (scheduled daily 18:00): for every active duty in a PUBLISHED period whose slot date is tomorrow (in `TIME_ZONE`) and `reminder_sent_at` is null, queue one email per faculty member listing all their duties tomorrow, then set `reminder_sent_at`. Running it again the same day queues nothing.

## 8. Error handling
- Email failures never block the triggering action; they surface on the Emails page.
- Expired/invalid activation links show a clear message, never a 500.
- Requests for dates outside the period, slots from another date, duplicates, or after the deadline are rejected with form errors.
- Decision on an already-decided or withdrawn request: rejected with a message ("This request was already decided.").
- Publishing still works when some faculty have no account (they get the email; the link lets them activate first).

## 9. Testing
- Permissions: every new view tested for anonymous (redirect), wrong role (403), and cross-object access (another faculty member's request/duty, another department) → 404.
- Activation: invite → email in outbox → link sets password → logged in; expired token message; HOD role on activation.
- HOD sync on department change.
- Unavailability: submit, validation errors, withdraw, deadline enforcement; HOD cannot see or decide own request; admin can.
- Outbox: queue, send, failure with retry, failed after 3 attempts, resend.
- Reminders: correct recipients, one email per person per day, idempotent.
- `.ics` content: event count, times in local zone, UIDs stable.
- Capacity warning with pending requests.

## 10. Build order (one plan, ~2–3 weeks solo)
1. Email outbox, settings and Emails page.
2. Accounts: invitation, activation, password reset, HOD sync, role home pages.
3. Availability window and faculty requests.
4. HOD and admin decisions; capacity warning.
5. My Duties (PDF, .ics) and Department Duties.
6. Publish and reminder emails; README scheduling instructions.
