from datetime import datetime, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from exams.availability import AvailabilityError, close_availability, open_availability
from exams.models import ExamPeriod, PeriodLockedError, PeriodStatus
from notifications.models import EmailKind, OutboundEmail

pytestmark = pytest.mark.django_db


def soon(days=3):
    return timezone.now() + timedelta(days=days)


def test_open_sets_status_and_emails_active_non_exempt_users(period, make_member, make_faculty, admin_user):
    a = make_member()
    make_member(is_exempt=True)
    make_faculty()  # no account
    n = open_availability(period, soon(), admin_user)
    period.refresh_from_db()
    assert n == 1 and period.status == PeriodStatus.AVAILABILITY_OPEN and period.availability_is_open
    email = OutboundEmail.objects.get(kind=EmailKind.AVAILABILITY_OPEN)
    assert email.to == a.user.email and period.name in email.body
    assert AuditLog.objects.filter(action="availability.open").exists()


def test_past_deadline_rejected(period, admin_user):
    with pytest.raises(AvailabilityError, match="The deadline must be in the future."):
        open_availability(period, timezone.now() - timedelta(minutes=1), admin_user)


def test_deadline_after_last_exam_day_rejected(period, admin_user):
    late = timezone.make_aware(datetime(2026, 12, 16, 9, 0))
    with pytest.raises(AvailabilityError, match="The deadline must be on or before the last exam day."):
        open_availability(period, late, admin_user)


def test_close_ends_window(period, admin_user):
    open_availability(period, soon(), admin_user)
    close_availability(period, admin_user)
    period.refresh_from_db()
    assert not period.availability_is_open
    assert AuditLog.objects.filter(action="availability.close").exists()


def test_window_closes_by_itself_after_deadline(period, admin_user):
    open_availability(period, soon(), admin_user)
    ExamPeriod.objects.filter(pk=period.pk).update(availability_deadline=timezone.now() - timedelta(seconds=1))
    period.refresh_from_db()
    assert not period.availability_is_open


def test_published_period_locked(published_period, admin_user):
    with pytest.raises(PeriodLockedError):
        open_availability(published_period, soon(), admin_user)


def test_open_view(admin_client, period):
    deadline = timezone.localtime(soon()).strftime("%Y-%m-%dT%H:%M")
    r = admin_client.post(reverse("open_availability", args=[period.pk]), {"deadline": deadline}, follow=True)
    period.refresh_from_db()
    assert period.availability_is_open and "Availability is open until" in r.content.decode()


def test_open_view_shows_error(admin_client, period):
    r = admin_client.post(reverse("open_availability", args=[period.pk]), {"deadline": "2020-01-01T09:00"}, follow=True)
    assert "The deadline must be in the future." in r.content.decode()


def test_close_view_and_dashboard(admin_client, period, admin_user):
    open_availability(period, soon(), admin_user)
    page = admin_client.get(reverse("period_dashboard", args=[period.pk])).content.decode()
    assert "Availability open until" in page and reverse("close_availability", args=[period.pk]) in page
    admin_client.post(reverse("close_availability", args=[period.pk]))
    period.refresh_from_db()
    assert not period.availability_is_open


def test_availability_views_admin_only(client, faculty_user, period):
    client.force_login(faculty_user)
    assert client.post(reverse("open_availability", args=[period.pk])).status_code == 403


def test_extending_open_window_sends_no_emails(period, make_member, admin_user):
    make_member()
    assert open_availability(period, soon(), admin_user) == 1
    assert open_availability(period, soon(5), admin_user) == 0
    period.refresh_from_db()
    assert OutboundEmail.objects.filter(kind=EmailKind.AVAILABILITY_OPEN).count() == 1
    assert timezone.localtime(period.availability_deadline).date() == timezone.localtime(soon(5)).date()


def test_reopening_after_close_sends_no_emails(period, make_member, admin_user):
    make_member()
    open_availability(period, soon(), admin_user)
    close_availability(period, admin_user)
    assert open_availability(period, soon(), admin_user) == 0
    period.refresh_from_db()
    assert period.availability_is_open
    assert OutboundEmail.objects.filter(kind=EmailKind.AVAILABILITY_OPEN).count() == 1


def test_extend_view_reports_no_emails(admin_client, period, make_member, admin_user):
    make_member()
    open_availability(period, soon(), admin_user)
    deadline = timezone.localtime(soon(5)).strftime("%Y-%m-%dT%H:%M")
    r = admin_client.post(reverse("open_availability", args=[period.pk]), {"deadline": deadline}, follow=True)
    assert "0 faculty will be emailed." in r.content.decode()
