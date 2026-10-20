from datetime import date

import pytest
from django.core.management import call_command

from allocation.services import publish_period
from duties.models import Duty, DutySource
from exams.models import ExamPeriod, PeriodStatus
from notifications.models import EmailKind, OutboundEmail
from notifications.triggers import queue_reminders, queue_roster_published

pytestmark = pytest.mark.django_db


@pytest.fixture
def roster(period, make_room, make_member, make_faculty):
    """Member with two duties on 1 Dec, a faculty member without an account with one on 2 Dec."""
    member = make_member("With Account")
    no_account = make_faculty("No Account", email="noacc@uni.edu")
    Duty.objects.create(faculty=member, room_allocation=make_room(day=1, start="09:00", end="12:00", course="A", room="R1"), source=DutySource.MANUAL)
    Duty.objects.create(faculty=member, room_allocation=make_room(day=1, start="14:00", end="17:00", course="B", room="R2"), source=DutySource.MANUAL)
    Duty.objects.create(faculty=no_account, room_allocation=make_room(day=2, course="C", room="R3"), source=DutySource.MANUAL)
    return member, no_account


def test_publish_queues_one_email_per_person_with_ics(roster, period, admin_user):
    member, no_account = roster
    publish_period(period, admin_user, acknowledge_unfilled=True)
    emails = OutboundEmail.objects.filter(kind=EmailKind.ROSTER_PUBLISHED)
    assert sorted(e.to for e in emails) == sorted(["noacc@uni.edu", member.user.email])
    mine = emails.get(to=member.user.email)
    assert "R1" in mine.body and "R2" in mine.body and "R3" not in mine.body
    assert mine.attachment_name == "fall-2026-finals-duties.ics"
    assert bytes(mine.attachment_content).count(b"BEGIN:VEVENT") == 2


def test_queue_roster_published_returns_count(roster, period):
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    period.refresh_from_db()
    assert queue_roster_published(period) == 2


def test_reminders_for_given_day_only_one_email_per_person(roster, period):
    member, _ = roster
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    assert queue_reminders(date(2026, 12, 1)) == 1
    email = OutboundEmail.objects.get(kind=EmailKind.REMINDER)
    assert email.to == member.user.email and "R1" in email.body and "R2" in email.body
    assert not Duty.objects.filter(reminder_sent_at__isnull=True, room_allocation__session__slot__date=date(2026, 12, 1)).exists()


def test_reminders_are_idempotent(roster, period):
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    queue_reminders(date(2026, 12, 1))
    assert queue_reminders(date(2026, 12, 1)) == 0
    assert OutboundEmail.objects.filter(kind=EmailKind.REMINDER).count() == 1


@pytest.mark.parametrize("status", [PeriodStatus.ALLOCATING, PeriodStatus.CLOSED])
def test_reminders_ignore_unpublished_periods(roster, period, status):
    ExamPeriod.objects.filter(pk=period.pk).update(status=status)
    assert queue_reminders(date(2026, 12, 1)) == 0


def test_send_reminders_command_with_date(roster, period, capsys):
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    call_command("send_reminders", "--date", "2026-12-02")
    assert "Queued 1 reminder(s) for 2026-12-02." in capsys.readouterr().out


def test_send_reminders_defaults_to_tomorrow_local(roster, period, capsys, monkeypatch):
    from django.utils import timezone

    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: date(2026, 11, 30))
    call_command("send_reminders")
    assert "for 2026-12-01." in capsys.readouterr().out
