import pytest
from django.urls import reverse

from notifications.models import EmailKind, EmailStatus, OutboundEmail
from notifications.outbox import queue_email

pytestmark = pytest.mark.django_db


def test_email_pages_admin_only(client, faculty_user):
    client.force_login(faculty_user)
    assert client.get(reverse("email_list")).status_code == 403
    assert client.post(reverse("send_emails_now")).status_code == 403


def test_email_list_shows_counts_and_failed(admin_client):
    queue_email("q@uni.edu", "Queued one", "B", EmailKind.REMINDER)
    failed = queue_email("f@uni.edu", "Failed one", "B", EmailKind.REMINDER)
    OutboundEmail.objects.filter(pk=failed.pk).update(status=EmailStatus.FAILED, attempts=3, last_error="boom")
    page = admin_client.get(reverse("email_list")).content.decode()
    assert "Failed one" in page and "boom" in page and "1 queued" in page


def test_send_now_and_resend_views(admin_client, mailoutbox):
    e = queue_email("q@uni.edu", "Hi", "B", EmailKind.REMINDER)
    admin_client.post(reverse("send_emails_now"))
    assert len(mailoutbox) == 1
    OutboundEmail.objects.filter(pk=e.pk).update(status=EmailStatus.FAILED, attempts=3)
    admin_client.post(reverse("resend_email", args=[e.pk]))
    e.refresh_from_db()
    assert e.status == EmailStatus.QUEUED


def test_send_now_is_capped_and_reports_remaining(admin_client, mailoutbox):
    for i in range(53):
        queue_email(f"q{i}@uni.edu", "Hi", "B", EmailKind.REMINDER)
    r = admin_client.post(reverse("send_emails_now"), follow=True)
    assert len(mailoutbox) == 50
    assert "Sent 50 email(s). 3 still queued" in r.content.decode()
    assert OutboundEmail.objects.filter(status=EmailStatus.QUEUED).count() == 3
