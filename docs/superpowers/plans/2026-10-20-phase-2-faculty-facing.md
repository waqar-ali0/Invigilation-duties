# Invigilation Duty System — Phase 2 (Faculty-facing) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Faculty and HODs use the system themselves (activation, My Duties, unavailability requests, HOD approval) and the system emails people through a reliable outbox with reminders.

**Architecture:** Two new apps: `notifications` (outbox model, sending, email templates, triggers) and `portal` (faculty/HOD pages). Business rules live in small service modules (`accounts/invitations.py`, `exams/availability.py`, `duties/unavailability.py`, `reports/personal.py`); views stay thin. Every email goes through `notifications.outbox.queue_email` and is sent later by the `send_emails` command.

**Tech Stack:** unchanged — Python 3.13, Django 5.2, SQLite dev / PostgreSQL prod, openpyxl, reportlab, pytest-django. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-20-phase-2-faculty-facing-design.md` (parent: `2026-10-20-invigilation-duty-system-design.md`)

## Global Constraints

- Activation and password-reset links valid 7 days: `PASSWORD_RESET_TIMEOUT = 604800`.
- Only `APPROVED` unavailability blocks allocation (unchanged).
- Faculty and HOD never see draft rosters: only periods with status `published` or `closed`.
- An HOD never decides their own request; Admin (or superuser) may decide any.
- Email is never sent inside a request: everything is queued with `queue_email` and sent by `send_queued` (`send_emails` command, every 5 min) or the admin's "Send queued emails now" button. Failed sends retry; after 3 attempts status is `failed`.
- Reminder job is idempotent: one reminder email per faculty member per day; `Duty.reminder_sent_at` marks reminded duties.
- Dev email backend writes files to `sent_emails/` (git-ignored); tests use Django's locmem backend (pytest-django default).
- Server-side permission checks on every view: anonymous → login redirect; wrong role → 403; another person's/department's object → 404.
- All state changes that Phase 1 audited stay audited; new audited actions: `availability.open`, `availability.close`, `unavailability.approve`, `unavailability.reject`, `account.invite`.
- Commands on Windows from project root: `.venv\Scripts\python ...`.

## Review Focus

1. **Email letter case** — faculty imported as `Ali@Uni.edu` but typing `ali@uni.edu` at login (or the reverse). Expected: login works regardless of case. *(test in Task 3)*
2. **Invitation to someone already active** — admin clicks Resend for a person who already set a password. Expected: refused with "already active", no new link. *(test in Task 2)*
3. **"Tomorrow" around midnight / time zone** — reminders and `.ics` times must use `TIME_ZONE` (Asia/Karachi), not UTC. Expected: a 09:00 exam is 09:00 local in the calendar and reminded the evening before local date. *(tests in Tasks 7, 8)*
4. **HOD of two departments, or HOD who is exempt** — Expected: sees and decides requests of both departments; still never their own. *(test in Task 6)*
5. **Faculty email changed after invitation** — Expected: the user's login email follows the faculty record. *(test in Task 3)*

---

## File Structure

```
notifications/  models.py (OutboundEmail)  outbox.py  render.py  triggers.py  admin.py
                views.py  urls.py  management/commands/send_emails.py, send_reminders.py  tests/
portal/         views.py  urls.py  forms.py  tests/
accounts/       invitations.py  forms.py (OutboxPasswordResetForm, SetPasswordForm reuse)
                backends.py (case-insensitive email login)  views.py  urls.py
faculty/        signals.py (HOD sync, user email/active sync)  migrations (invited_at)
exams/          availability.py  views.py (+open/close availability)
duties/         unavailability.py  migrations (WITHDRAWN, review_comment, created_at, reminder_sent_at)
reports/        personal.py (faculty duties, personal PDF, .ics)  excel.py (+department_xlsx)
templates/      emails/*.txt  accounts/*.html  registration/password_reset_*.html  portal/*.html
                notifications/email_list.html  exams/dashboard.html (+availability, pending warning)
tests/test_phase2_end_to_end.py
```

---

### Task 1: Email outbox

**Files:**
- Create: `notifications/` app (`startapp`), `notifications/outbox.py`, `notifications/render.py`, `notifications/triggers.py` (empty module docstring), `notifications/views.py`, `notifications/urls.py`, `notifications/management/commands/send_emails.py`, `templates/notifications/email_list.html`
- Modify: `config/settings.py`, `config/urls.py`, `.env.example`, `.gitignore`, `templates/base.html` (Emails nav link for admins), `accounts/signals.py` (Exam Office: view `notifications`)
- Test: `notifications/tests/test_outbox.py`, `notifications/tests/failing_backend.py`, `notifications/tests/test_views.py`

**Interfaces:**
- Produces:
  - `notifications.models.EmailStatus` — `QUEUED="queued"`, `SENT="sent"`, `FAILED="failed"`; `EmailKind` — `ACTIVATION="activation"`, `PASSWORD_RESET="password_reset"`, `AVAILABILITY_OPEN="availability_open"`, `REQUEST_DECISION="request_decision"`, `ROSTER_PUBLISHED="roster_published"`, `REMINDER="reminder"`.
  - `OutboundEmail(to: EmailField, subject: CharField(255), body: TextField, kind, status default QUEUED, attempts int 0, last_error TextField blank, created_at auto, sent_at null, attachment_name CharField blank, attachment_content BinaryField null, attachment_mimetype CharField blank)`, ordering `created_at`.
  - `notifications.outbox.MAX_ATTEMPTS = 3`
  - `queue_email(to: str, subject: str, body: str, kind: str, attachment: tuple[str, bytes, str] | None = None) -> OutboundEmail`
  - `send_queued(limit: int | None = None) -> tuple[int, int]` — (sent, newly failed); one `get_connection()` for the batch; `EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [to])` plus attachment; on exception: `attempts += 1`, `last_error = str(exc)`, status `FAILED` when `attempts >= MAX_ATTEMPTS`.
  - `resend(email: OutboundEmail) -> None` — status `QUEUED`, attempts 0, last_error "".
  - `notifications.render.render_email(name: str, context: dict) -> tuple[str, str]` — renders `templates/emails/<name>_subject.txt` (stripped, newlines removed) and `<name>_body.txt`.
  - `notifications.render.absolute_url(path: str) -> str` — `settings.SITE_URL.rstrip("/") + path`.
  - Command `send_emails` → prints `Sent N, failed M.`
  - URL names (ADMIN): `email_list` `/emails/` (counts by status, failed table with Resend, queued count, "Send queued emails now"); `send_emails_now` `/emails/send/` (POST); `resend_email` `/emails/<int:pk>/resend/` (POST).
  - Settings from env: `EMAIL_BACKEND` (default `django.core.mail.backends.filebased.EmailBackend`), `EMAIL_FILE_PATH = BASE_DIR / "sent_emails"`, `EMAIL_HOST`, `EMAIL_PORT` (587), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` (True), `DEFAULT_FROM_EMAIL` (`invigilation@localhost`), `SITE_URL` (`http://127.0.0.1:8000`), `PASSWORD_RESET_TIMEOUT = 604800`.

- [ ] **Step 1: Write the failing tests**

```python
def test_queue_then_send_delivers(mailoutbox):
    e = queue_email("a@uni.edu", "Hi", "Body", EmailKind.REMINDER, attachment=("d.ics", b"BEGIN", "text/calendar"))
    assert e.status == EmailStatus.QUEUED and mailoutbox == []
    assert send_queued() == (1, 0)
    e.refresh_from_db()
    assert e.status == EmailStatus.SENT and e.sent_at
    assert mailoutbox[0].to == ["a@uni.edu"] and mailoutbox[0].attachments[0][0] == "d.ics"

def test_failure_retries_then_fails(settings):
    settings.EMAIL_BACKEND = "notifications.tests.failing_backend.FailingBackend"
    e = queue_email("a@uni.edu", "Hi", "Body", EmailKind.REMINDER)
    assert send_queued() == (0, 0); e.refresh_from_db(); assert (e.status, e.attempts) == ("queued", 1)
    send_queued(); send_queued(); e.refresh_from_db()
    assert e.status == EmailStatus.FAILED and "boom" in e.last_error

def test_resend_requeues(...): ...
def test_sent_emails_not_resent(mailoutbox): ...          # second send_queued() == (0, 0)
def test_render_email_uses_templates(): ...               # a test template under notifications/tests/templates
def test_send_emails_command_prints_counts(capsys): ...
def test_email_pages_admin_only(client, faculty_user): ... # 403
def test_send_now_and_resend_views(admin_client, mailoutbox): ...
```
`FailingBackend.send_messages` raises `smtplib.SMTPException("boom")`.

- [ ] **Step 2: Run** `.venv\Scripts\python -m pytest notifications -q` → FAIL (no module).
- [ ] **Step 3: Implement**; `makemigrations notifications`.
- [ ] **Step 4: Run full suite** → all pass.
- [ ] **Step 5: Commit** — `feat: email outbox with retries and admin email page`

---

### Task 2: Invitations and activation

**Files:**
- Create: `accounts/invitations.py`, `accounts/forms.py`, `accounts/views.py`, `accounts/urls.py`, `templates/accounts/accounts_page.html`, `templates/accounts/activate.html`, `templates/registration/password_reset_form.html`, `..._done.html`, `..._confirm.html`, `..._complete.html`, `templates/emails/activation_subject.txt`, `activation_body.txt`, `password_reset_subject.txt`, `password_reset_body.txt`
- Modify: `faculty/models.py` (`invited_at = DateTimeField(null=True, blank=True)`), `config/urls.py`, `templates/registration/login.html` ("Forgot your password?" link), `templates/base.html` (Accounts link for admins)
- Test: `accounts/tests/test_invitations.py`, `accounts/tests/test_activation_views.py`

**Interfaces:**
- Consumes: `queue_email`, `render_email`, `absolute_url` (Task 1).
- Produces:
  - `accounts.invitations.class InvitationError(Exception)`.
  - `role_for(faculty) -> str` — `Role.HOD` if `Department.objects.filter(hod=faculty).exists()` else `Role.FACULTY`.
  - `activation_url(user) -> str` — `absolute_url(reverse("activate", args=[urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)]))`.
  - `invite(faculty, actor) -> User` — faculty inactive → `InvitationError("<name> is inactive.")`; user exists with usable password → `InvitationError("<name> already has an active account.")`; else create user if missing (`email=faculty.email.lower()`, role `role_for`, `set_unusable_password()`), link `faculty.user`, set `invited_at=now()`, queue `activation` email, audit `account.invite`.
  - `invite_all(actor) -> int` — invites every active faculty with `user` null and `invited_at` null.
  - `accounts.forms.OutboxPasswordResetForm(PasswordResetForm)` — overrides `send_mail(...)` to `queue_email(to_email, subject, body, EmailKind.PASSWORD_RESET)`; uses `templates/emails/password_reset_*.txt`; `domain_override` from `SITE_URL`.
  - URL names: `accounts_page` `/accounts/` (ADMIN; groups *No account* / *Invited* / *Active* with counts), `invite_all` `/accounts/invite/` (POST), `invite_one` `/accounts/<int:faculty_id>/invite/` (POST; `InvitationError` → `messages.error`), `activate` `/activate/<uidb64>/<token>/` (public; invalid → template message `"This link is invalid or has expired. Ask the exam office to resend it."`; valid → `SetPasswordForm`; success → `login()` then redirect to `home`), Django `password_reset`, `password_reset_done`, `password_reset_confirm`, `password_reset_complete` at `/password-reset/...` with `form_class=OutboxPasswordResetForm`.

- [ ] **Step 1: Write the failing tests**

```python
def test_invite_creates_linked_user_and_queues_email(make_faculty, admin_user):
    f = make_faculty(email="Ali@Uni.edu")
    user = invite(f, admin_user)
    f.refresh_from_db()
    assert f.user == user and user.email == "ali@uni.edu" and not user.has_usable_password()
    assert f.invited_at and OutboundEmail.objects.get(kind="activation").to == "ali@uni.edu"
    assert AuditLog.objects.filter(action="account.invite").exists()

def test_invite_gives_hod_role(dept, make_faculty, admin_user): ...      # dept.hod = f before invite → role HOD
def test_resend_to_active_user_refused(make_faculty, admin_user): ...    # set_password then invite → "already has an active account"
def test_invite_inactive_refused(...): ...
def test_invite_all_skips_invited_and_inactive(...): ...                 # returns count
def test_activation_link_sets_password_and_logs_in(client, make_faculty, admin_user):
    user = invite(make_faculty(), admin_user)
    url = activation_url(user).replace(settings.SITE_URL, "")
    r = client.get(url, follow=True)                                     # Django redirects to the set-password URL form
    r = client.post(r.redirect_chain[-1][0] if r.redirect_chain else url, {"new_password1": "Strong-pass-123", "new_password2": "Strong-pass-123"})
    user.refresh_from_db(); assert user.has_usable_password()
    assert client.session["_auth_user_id"] == str(user.pk)
def test_bad_activation_link_shows_message(client): ...
def test_password_reset_goes_through_outbox(client, ...): ...           # OutboundEmail kind password_reset
def test_accounts_page_admin_only_and_groups(...): ...
```
Note for the implementer: implement `activate` as its own view (check token with `default_token_generator.check_token`, render `SetPasswordForm`) — no redirect step required; the test's `follow=True` handles either.

- [ ] **Step 2: Run** `.venv\Scripts\python -m pytest accounts -q` → FAIL.
- [ ] **Step 3: Implement**; `makemigrations faculty`.
- [ ] **Step 4: Run full suite** → pass.
- [ ] **Step 5: Commit** — `feat: faculty invitations, activation links and password reset`

---

### Task 3: Roles, HOD sync, home pages and case-insensitive login

**Files:**
- Create: `faculty/signals.py`, `accounts/backends.py`, `portal/` app, `portal/views.py`, `portal/urls.py`, `templates/portal/my_duties.html` (list only), `templates/accounts/not_linked.html`, `templates/portal/staff_home.html`
- Modify: `faculty/apps.py` (connect signals), `exams/importers/faculty.py` (update existing faculty with `save(update_fields=[...])` instead of `queryset.update`, so signals fire), `accounts/permissions.py`, `exams/views.py` (`home` becomes role-aware, moves to `portal.views.home`), `config/settings.py` (`AUTHENTICATION_BACKENDS = ["accounts.backends.EmailBackend"]`, `LOGIN_REDIRECT_URL = "home"`), `templates/base.html` (role-aware nav)
- Test: `faculty/tests/test_signals.py`, `accounts/tests/test_roles.py`, `portal/tests/test_home.py`

**Interfaces:**
- Produces:
  - `faculty.signals.sync_hod_role(faculty) -> None` — if `faculty.user` and `user.role in (FACULTY, HOD)`: role = HOD if they head any department else FACULTY. Called for old and new HOD on Department save (pre_save captures old `hod_id`).
  - Faculty `post_save`: if linked user, set `user.email = faculty.email.lower()` and `user.is_active = faculty.is_active` when different.
  - `accounts.backends.EmailBackend(ModelBackend)` — `authenticate` looks up `email__iexact`.
  - `accounts.permissions.faculty_required(view)` — anonymous → login redirect; role not in (FACULTY, HOD) → 403; no linked faculty → render `accounts/not_linked.html` (status 200, text `"Your account is not linked to a faculty record; contact the exam office."`); sets `request.faculty`.
  - `accounts.permissions.hod_required(view)` — like `faculty_required` but role HOD and heads ≥1 department; sets `request.faculty`, `request.hod_departments` (QuerySet of Department).
  - `portal.views.home` — URL name `home` at `/`: ADMIN/superuser → `period_list`; FACULTY/HOD → `my_duties`; EXAM_STAFF → `staff_home`.
  - URL names: `my_duties` `/my/duties/` (faculty_required; list of the person's active duties in published/closed periods: date, time, course, room — upcoming first), `staff_home` `/staff/` (role EXAM_STAFF; placeholder "Attendance marking arrives in Phase 3.").

- [ ] **Step 1: Write the failing tests**

```python
def test_setting_hod_promotes_and_replacing_demotes(dept, make_faculty, admin_user):
    a, b = make_faculty(), make_faculty()
    ua, ub = invite(a, admin_user), invite(b, admin_user)
    dept.hod = a; dept.save(); ua.refresh_from_db(); assert ua.role == Role.HOD
    dept.hod = b; dept.save(); ua.refresh_from_db(); ub.refresh_from_db()
    assert (ua.role, ub.role) == (Role.FACULTY, Role.HOD)
def test_hod_sync_leaves_admin_role_alone(...): ...
def test_faculty_email_change_updates_user(...): ...          # incl. via import_faculty update path
def test_deactivating_faculty_blocks_login(client, ...): ...
def test_login_is_case_insensitive(client, ...):               # user ali@uni.edu logs in as "ALI@uni.edu"
def test_home_redirects_by_role(client, admin_user, ...): ...  # admin→period_list, faculty→my_duties, exam staff→staff_home
def test_unlinked_faculty_user_sees_message(client, faculty_user): ...
def test_my_duties_shows_only_own_published_duties(client, ...): ...  # draft period hidden; other person's duties hidden
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Full suite** → pass (update Phase 1 tests that assert `/` redirects for admin if needed — behaviour unchanged for admin).
- [ ] **Step 5: Commit** — `feat: HOD role sync, role home pages and case-insensitive login`

---

### Task 4: Availability window

**Files:**
- Create: `exams/availability.py`, `templates/emails/availability_open_subject.txt`, `availability_open_body.txt`
- Modify: `exams/models.py` (`availability_is_open` property), `exams/views.py`, `exams/urls.py`, `templates/exams/dashboard.html`
- Test: `exams/tests/test_availability.py`

**Interfaces:**
- Consumes: `queue_email`, `render_email`, `absolute_url`, `log`, `ExamPeriod.lock_for_edit`.
- Produces:
  - `ExamPeriod.availability_is_open -> bool` — `status == AVAILABILITY_OPEN and availability_deadline and timezone.now() < availability_deadline`.
  - `exams.availability.class AvailabilityError(Exception)`.
  - `open_availability(period, deadline: datetime, actor) -> int` — inside `atomic()` with `lock_for_edit`; deadline ≤ now → `AvailabilityError("The deadline must be in the future.")`; deadline local date > `end_date` → `AvailabilityError("The deadline must be on or before the last exam day.")`; sets status/deadline; queues `availability_open` email to each active, non-exempt faculty with a user; audit `availability.open` (`after={"deadline": iso, "emails": n}`); returns n.
  - `close_availability(period, actor) -> None` — `lock_for_edit`; sets `availability_deadline = now()`; audit `availability.close`.
  - URL names (ADMIN, POST): `open_availability` `/periods/<int:pk>/availability/open/` (field `deadline`, HTML `datetime-local`, parsed as local time), `close_availability` `/periods/<int:pk>/availability/close/`.
  - Dashboard: when editable, a small form "Open availability until [datetime]" and, when open, "Availability open until <deadline> · Close now".

- [ ] **Step 1: Write the failing tests** — open sets status and queues emails only to active non-exempt users; past deadline rejected (exact message); deadline after end rejected; close makes `availability_is_open` False; published period → `PeriodLockedError`; views admin-only and show messages.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: availability window with opening emails`

---

### Task 5: Faculty unavailability requests

**Files:**
- Create: `duties/unavailability.py`, `portal/forms.py`, `templates/portal/my_unavailability.html`
- Modify: `duties/models.py` (`UnavailabilityStatus.WITHDRAWN = "withdrawn"`, `review_comment = TextField(blank=True)`, `created_at = DateTimeField(auto_now_add=True, null=True)`), `portal/views.py`, `portal/urls.py`, `templates/base.html` (nav)
- Test: `duties/tests/test_unavailability.py`, `portal/tests/test_my_unavailability.py`

**Interfaces:**
- Produces:
  - `duties.unavailability.class RequestRejected(Exception)` with `.errors: list[str]`.
  - `submit_request(faculty, period, day: date, slot: TimeSlot | None, reason: str) -> Unavailability` — errors (all collected, in this order): `"You are exempt from invigilation."` (exempt), `"The availability window for this period is closed."` (not `availability_is_open`), `"Date is outside the exam period."`, `"That time slot is not on the chosen date."` (slot of another period or date), `"A reason is required."` (blank after strip), `"You already have a request covering this date and slot."` (pending/approved request same faculty+date where either is whole-day or slots equal).
  - `withdraw_request(req, faculty) -> None` — not owner → `PermissionDenied`; status not PENDING → `RequestRejected(["Only pending requests can be withdrawn."])`; window closed → `RequestRejected(["The availability window for this period is closed."])`; sets WITHDRAWN.
  - `portal.forms.RequestForm(period)` — fields `date` (DateField), `slot` (ModelChoiceField over `period.slots`, optional, empty label "Whole day", option labels `"<date> <start>–<end>"`), `reason` (CharField, Textarea).
  - URL names (faculty_required): `my_unavailability` `/my/unavailability/` (GET: one form per open period + request history with status and review_comment; POST with `period` id → `submit_request`; `RequestRejected` errors shown on the form), `withdraw_request` `/my/unavailability/<int:pk>/withdraw/` (POST). Another person's request id → 404.

- [ ] **Step 1: Write the failing tests** — each error message case; whole-day vs slot duplicate rule (both directions); withdraw pending ok, withdraw approved refused, other person's → 404 in view; exempt faculty page shows "exempt" message and no form; draft period without open window shows no form.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**; `makemigrations duties`. **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: faculty unavailability requests`

---

### Task 6: Decisions (HOD and exam office) and pending-capacity warning

**Files:**
- Create: `templates/portal/department_requests.html`, `templates/portal/admin_requests.html`, `templates/emails/request_decision_subject.txt`, `request_decision_body.txt`
- Modify: `duties/unavailability.py`, `portal/views.py`, `portal/urls.py`, `allocation/services.py`, `exams/views.py`, `templates/exams/dashboard.html`, `templates/base.html`
- Test: `duties/tests/test_decisions.py`, `portal/tests/test_requests_views.py`, `allocation/tests/test_pending_capacity.py`

**Interfaces:**
- Produces:
  - `duties.unavailability.can_decide(user, req) -> bool` — superuser or role ADMIN → True; role HOD with linked faculty → `req.faculty.department_id in` departments they head and `req.faculty_id != user.faculty.id`; else False.
  - `decide_request(req, actor, approve: bool, comment: str = "") -> None` — `not can_decide` → `PermissionDenied`; `ExamPeriod.lock_for_edit(req.period_id)` inside `atomic()` (published → `PeriodLockedError`); status ≠ PENDING → `RequestRejected(["This request was already decided."])`; sets status APPROVED/REJECTED, `reviewed_by`, `reviewed_at`, `review_comment`; audit `unavailability.approve`/`unavailability.reject`; queues `request_decision` email.
  - `allocation.services.build_engine_input(period, exclude_auto=False, include_pending=False)` — `include_pending` also treats PENDING requests as unavailability.
  - `allocation.services.pending_capacity(period) -> tuple[int, CapacityReport] | None` — None when no pending requests; else (pending count, capacity with pending included).
  - URL names: `department_requests` `/department/requests/` (hod_required; pending of headed departments excluding own, oldest first; decided below), `admin_requests` `/requests/` (ADMIN; all pending, HODs' own flagged "HOD request"), `decide_request` `/requests/<int:pk>/decide/` (POST `decision=approve|reject`, `comment`; HOD or ADMIN; `can_decide` False → 404 for HOD, redirect back with message otherwise).
  - Dashboard line (exact): `"{n} request(s) pending. If all are approved, capacity drops to {capacity} duties (need {seats_needed})."`

- [ ] **Step 1: Write the failing tests**

```python
def test_hod_cannot_decide_own_request(...): ...                  # can_decide False; view 404
def test_hod_of_two_departments_decides_both(...): ...
def test_hod_cannot_decide_other_department(...): ...
def test_admin_decides_hod_request_and_email_queued(...): ...
def test_decided_twice_rejected(...): ...                          # "This request was already decided."
def test_decision_on_published_period_locked(...): ...
def test_pending_capacity_counts_pending_as_unavailable(...): ...
def test_dashboard_shows_pending_warning(admin_client, ...): ...
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: HOD and exam office decisions with pending-capacity warning`

---

### Task 7: My Duties downloads and Department Duties

**Files:**
- Create: `reports/personal.py`
- Modify: `portal/views.py`, `portal/urls.py`, `templates/portal/my_duties.html`, `templates/portal/department_duties.html` (create), `reports/excel.py`
- Test: `reports/tests/test_personal.py`, `portal/tests/test_duties_views.py`

**Interfaces:**
- Produces:
  - `reports.personal.VISIBLE_STATUSES = ("published", "closed")`.
  - `faculty_duties(faculty, period=None) -> list[Duty]` — active duties in visible periods (one period or all), `select_related` slot/session/room/period, ordered by date, start.
  - `duties_pdf(faculty, period) -> bytes` — A4, title `"Invigilation Duties — {faculty.name} — {period.name}"`, table Date | Time | Course | Room.
  - `duties_ics(faculty, duties) -> str` — `BEGIN:VCALENDAR` / `VERSION:2.0` / `PRODID:-//Invigilation Duties//EN`, one `VEVENT` per duty with `UID:duty-{id}@invigilation`, `DTSTAMP`, `DTSTART`/`DTEND` as UTC (`YYYYMMDDTHHMMSSZ`) converted from the slot's local date/time in `TIME_ZONE`, `SUMMARY:Invigilation: {course_code}`, `LOCATION:{room}`, `DESCRIPTION:{course_name}`; CRLF line endings; text escaped per RFC 5545 (`\`, `;`, `,`, newline).
  - `reports.excel.department_xlsx(period, departments) -> bytes` — sheet "Department Duties", header `Name, Department, Duties, Dates and rooms`.
  - URL names: `my_duties_pdf` `/my/duties/<int:period_id>/pdf/`, `my_duties_ics` `/my/duties/<int:period_id>/ics/` (faculty_required; non-visible period → 404; content type `text/calendar; charset=utf-8`), `department_duties` `/department/duties/` (hod_required; `?period=<id>` select among visible periods, default latest), `department_duties_xlsx` `/department/duties/<int:period_id>/xlsx/`.

- [ ] **Step 1: Write the failing tests** — ics contains one VEVENT per duty, `DTSTART:20261201T040000Z` for a 09:00 Asia/Karachi slot on 2026-12-01, escaping of commas; PDF bytes start with `%PDF`; draft period → 404; department page lists only headed departments' faculty; xlsx header exact.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: personal PDF and calendar downloads, department duties for HODs`

---

### Task 8: Publish and reminder emails

**Files:**
- Create: `notifications/triggers.py` (fill), `notifications/management/commands/send_reminders.py`, `templates/emails/roster_published_subject.txt`, `roster_published_body.txt`, `reminder_subject.txt`, `reminder_body.txt`
- Modify: `duties/models.py` (`reminder_sent_at = DateTimeField(null=True, blank=True)`), `allocation/services.py` (`publish_period` calls `queue_roster_published(period)` inside its transaction), `README.md` (email settings, scheduling `send_emails` every 5 min and `send_reminders` daily 18:00 with cron and Windows Task Scheduler examples, faculty/HOD workflow)
- Test: `notifications/tests/test_triggers.py`

**Interfaces:**
- Consumes: `faculty_duties`, `duties_ics` (Task 7), `queue_email`, `render_email`, `absolute_url`.
- Produces:
  - `notifications.triggers.queue_roster_published(period) -> int` — one email per faculty member with ≥1 active duty in the period (whether or not they have an account): duty list in body, link to `my_duties`, `.ics` attachment `"<period-slug>-duties.ics"`; returns count.
  - `queue_reminders(day: date) -> int` — for PUBLISHED periods, active duties on `day` with `reminder_sent_at` null: one email per faculty member listing that day's duties; sets `reminder_sent_at`; returns emails queued.
  - Command `send_reminders [--date YYYY-MM-DD]` — default `timezone.localdate() + 1 day`; prints `Queued N reminder(s) for <date>.`

- [ ] **Step 1: Write the failing tests** — publish queues one email per person with duties incl. person without account, with `.ics` attachment; reminders for tomorrow only, one email per person with two duties that day, second run queues 0; draft/closed periods ignored; command default date uses local date.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**; `makemigrations duties`. **Step 4: Full suite** → pass.
- [ ] **Step 5: Commit** — `feat: roster-published and reminder emails`

---

### Task 9: Phase 2 end-to-end test

**Files:**
- Create: `tests/test_phase2_end_to_end.py`

- [ ] **Step 1: Write the test** — admin invites all → `send_queued()` → faculty activates via link from the sent email body → admin opens availability → faculty submits a whole-day request → HOD approves (email queued) → admin runs allocation (faculty has no duty that day) → publish → faculty sees duties on My Duties and downloads `.ics` → `send_reminders --date` for the first exam day queues reminders; finally `send_emails` sends everything with `failed == 0`.
- [ ] **Step 2: Run** `.venv\Scripts\python -m pytest tests/test_phase2_end_to_end.py -q` → passes (if it fails, the failure is a bug in Tasks 1–8: fix with a regression test in the owning app).
- [ ] **Step 3: Run full suite** → pass.
- [ ] **Step 4: Commit** — `test: Phase 2 end-to-end flow`
