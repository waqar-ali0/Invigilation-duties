import pytest


@pytest.fixture
def published_duty(period, make_room, make_member):
    """A published period with one duty (2 Dec 2026, 09:00–12:00) owned by a member with an account."""
    from duties.models import Duty, DutySource
    from exams.models import ExamPeriod, PeriodStatus

    owner = make_member("Owner Person")
    duty = Duty.objects.create(faculty=owner, room_allocation=make_room(day=2, course="CS101", room="A1"), source=DutySource.AUTO)
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    duty.refresh_from_db()
    return duty


def set_cutoff(period, hours):
    s = period.settings
    s.swap_cutoff_hours = hours
    s.save()


def move_slot_to_past(duty):
    from datetime import date

    from exams.models import TimeSlot

    TimeSlot.objects.filter(pk=duty.room_allocation.session.slot_id).update(date=date(2020, 1, 1))
    duty.refresh_from_db()
