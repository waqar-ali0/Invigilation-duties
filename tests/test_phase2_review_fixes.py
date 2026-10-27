"""Regression tests for the findings of the Phase 2 whole-branch review."""

import pytest
from django.contrib.admin.sites import site
from django.urls import reverse

from accounts.invitations import InvitationError, invite
from accounts.models import Role, User
from exams.tests.helpers import FACULTY_HEADER, make_xlsx
from faculty.models import Faculty
from notifications import outbox
from notifications.models import EmailKind, EmailStatus, OutboundEmail
from notifications.outbox import queue_email, send_queued
from notifications.render import render_email

pytestmark = pytest.mark.django_db


# --- 1. Reset and activation links can't be read by the exam office ----------------


def test_exam_office_cannot_open_outbox_in_admin(admin_client, superuser):
    admin_client.post(reverse("password_reset"), {"email": superuser.email})
    email = OutboundEmail.objects.get(kind=EmailKind.PASSWORD_RESET)
    assert admin_client.get(reverse("admin:notifications_outboundemail_change", args=[email.pk])).status_code == 403
    assert admin_client.get(reverse("admin:notifications_outboundemail_changelist")).status_code == 403


def test_links_removed_from_stored_body_after_sending(make_faculty, admin_user, mailoutbox):
    invite(make_faculty(), admin_user)
    send_queued()
    assert "/activate/" in mailoutbox[0].body
    stored = OutboundEmail.objects.get(kind=EmailKind.ACTIVATION)
    assert stored.status == EmailStatus.SENT and "/activate/" not in stored.body


# --- 2. Invitations never take over other accounts ------------------------------------


def test_invite_refuses_existing_active_account_with_same_email(make_faculty, admin_user):
    User.objects.create_user("ali@uni.edu", "Strong-pass-123")
    f = make_faculty("Ali", email="Ali@Uni.edu")
    with pytest.raises(InvitationError, match="already has an active account"):
        invite(f, admin_user)
    f.refresh_from_db()
    assert f.user is None


def test_invite_refuses_staff_account_with_same_email(make_faculty, admin_user):
    User.objects.create_user("boss@uni.edu", None, role=Role.ADMIN)
    with pytest.raises(InvitationError, match="exam office"):
        invite(make_faculty("Boss", email="boss@uni.edu"), admin_user)


def test_email_sync_never_touches_admin_accounts(make_faculty):
    admin = User.objects.create_user("adm@uni.edu", "Strong-pass-123", role=Role.ADMIN)
    f = make_faculty(email="adm@uni.edu")
    Faculty.objects.filter(pk=f.pk).update(user=admin)  # linked by hand
    f.refresh_from_db()
    f.email = "attacker@evil.com"
    f.is_active = False
    f.save()
    admin.refresh_from_db()
    assert admin.email == "adm@uni.edu" and admin.is_active


# --- 3. Overlapping send runs never send twice -------------------------------------------


def test_overlapping_send_runs_send_each_email_once(mailoutbox, monkeypatch):
    for i in range(3):
        queue_email(f"{i}@uni.edu", "Hi", "Body", EmailKind.REMINDER)
    real = outbox._message
    nested = {"done": False}

    def message_starting_second_run(email, connection):
        if not nested["done"]:
            nested["done"] = True
            send_queued()  # a second run starts while the first is mid-batch
        return real(email, connection)

    monkeypatch.setattr(outbox, "_message", message_starting_second_run)
    send_queued()
    assert sorted(m.to[0] for m in mailoutbox) == ["0@uni.edu", "1@uni.edu", "2@uni.edu"]


def test_stale_sending_rows_are_retried(mailoutbox):
    from datetime import timedelta

    from django.utils import timezone

    e = queue_email("a@uni.edu", "Hi", "Body", EmailKind.REMINDER)
    OutboundEmail.objects.filter(pk=e.pk).update(status=EmailStatus.SENDING, claimed_at=timezone.now() - timedelta(hours=1))
    assert send_queued() == (1, 0)


# --- 4. Email clashes are errors, not crashes -------------------------------------------




def test_admin_form_rejects_email_used_by_another_account(rf, admin_user, make_member):
    f = make_member(email="mine@uni.edu")
    User.objects.create_user("taken@uni.edu", "x")
    f.email = "Taken@uni.edu"
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        f.full_clean()


def test_signal_does_not_crash_on_clash(make_member):
    f = make_member(email="mine@uni.edu")
    User.objects.create_user("taken@uni.edu", "x")
    Faculty.objects.filter(pk=f.pk).update(email="taken@uni.edu")
    f.refresh_from_db()
    f.save()  # must not raise IntegrityError
    f.user.refresh_from_db()
    assert f.user.email == "mine@uni.edu"


# --- 5. Plain-text emails are not HTML-escaped --------------------------------------------


def test_email_text_not_html_escaped(make_faculty, admin_user):
    f = make_faculty("Sana O'Brien & Co")
    subject, body = render_email("activation", {"faculty": f, "link": "http://x/?a=1&b=2", "days": 7})
    assert "Sana O'Brien & Co" in body and "a=1&b=2" in body and "&amp;" not in body
