from pathlib import Path

import pytest
from django.core.management import call_command

from notifications.models import EmailKind, EmailStatus, OutboundEmail
from notifications.outbox import queue_email, resend, send_queued
from notifications.render import absolute_url, render_email

pytestmark = pytest.mark.django_db
FAILING = "notifications.tests.failing_backend.FailingBackend"


def test_queue_then_send_delivers(mailoutbox):
    e = queue_email("a@uni.edu", "Hi", "Body", EmailKind.REMINDER, attachment=("d.ics", b"BEGIN", "text/calendar"))
    assert e.status == EmailStatus.QUEUED and mailoutbox == []
    assert send_queued() == (1, 0)
    e.refresh_from_db()
    assert e.status == EmailStatus.SENT and e.sent_at is not None
    assert mailoutbox[0].to == ["a@uni.edu"] and mailoutbox[0].subject == "Hi"
    assert mailoutbox[0].attachments[0][0] == "d.ics"


def test_failure_retries_then_fails(settings):
    settings.EMAIL_BACKEND = FAILING
    e = queue_email("a@uni.edu", "Hi", "Body", EmailKind.REMINDER)
    assert send_queued() == (0, 0)
    e.refresh_from_db()
    assert (e.status, e.attempts) == (EmailStatus.QUEUED, 1)
    send_queued()
    assert send_queued() == (0, 1)
    e.refresh_from_db()
    assert e.status == EmailStatus.FAILED and e.attempts == 3 and "boom" in e.last_error


def test_failed_emails_are_not_retried_automatically(settings):
    settings.EMAIL_BACKEND = FAILING
    e = queue_email("a@uni.edu", "Hi", "Body", EmailKind.REMINDER)
    for _ in range(4):
        send_queued()
    e.refresh_from_db()
    assert e.attempts == 3


def test_resend_requeues(settings, mailoutbox):
    settings.EMAIL_BACKEND = FAILING
    e = queue_email("a@uni.edu", "Hi", "Body", EmailKind.REMINDER)
    for _ in range(3):
        send_queued()
    resend(e)
    e.refresh_from_db()
    assert (e.status, e.attempts, e.last_error) == (EmailStatus.QUEUED, 0, "")


def test_sent_emails_not_resent(mailoutbox):
    queue_email("a@uni.edu", "Hi", "Body", EmailKind.REMINDER)
    send_queued()
    assert send_queued() == (0, 0) and len(mailoutbox) == 1


def test_send_limit(mailoutbox):
    for i in range(3):
        queue_email(f"{i}@uni.edu", "Hi", "Body", EmailKind.REMINDER)
    assert send_queued(limit=2) == (2, 0)
    assert OutboundEmail.objects.filter(status=EmailStatus.QUEUED).count() == 1


def test_render_email_uses_templates(settings):
    engine = {**settings.TEMPLATES[0], "DIRS": [*settings.TEMPLATES[0]["DIRS"], Path(__file__).parent / "templates"]}
    settings.TEMPLATES = [engine]
    subject, body = render_email("sample", {"name": "Ali", "link": "http://x"})
    assert subject == "Hello Ali" and body.strip() == "Dear Ali, see http://x."


def test_absolute_url(settings):
    settings.SITE_URL = "https://duties.uni.edu/"
    assert absolute_url("/my/duties/") == "https://duties.uni.edu/my/duties/"


def test_send_emails_command_prints_counts(capsys, mailoutbox):
    queue_email("a@uni.edu", "Hi", "Body", EmailKind.REMINDER)
    call_command("send_emails")
    assert "Sent 1, failed 0." in capsys.readouterr().out
