import pytest
from django.db import IntegrityError, transaction

from duties.models import Duty, DutySource, DutyStatus

pytestmark = pytest.mark.django_db


def test_duty_period_is_derived(room, room_fac):
    d = Duty.objects.create(faculty=room_fac, room_allocation=room, source=DutySource.AUTO)
    assert d.period == room.session.slot.period
    assert d.status == DutyStatus.ASSIGNED


def test_cannot_duplicate_active_duty(room, room_fac):
    Duty.objects.create(faculty=room_fac, room_allocation=room, source=DutySource.AUTO)
    with pytest.raises(IntegrityError), transaction.atomic():
        Duty.objects.create(faculty=room_fac, room_allocation=room, source=DutySource.MANUAL)


def test_cancelled_duty_allows_reassign(room, room_fac):
    Duty.objects.create(faculty=room_fac, room_allocation=room, source=DutySource.AUTO, status=DutyStatus.CANCELLED)
    Duty.objects.create(faculty=room_fac, room_allocation=room, source=DutySource.MANUAL)
    assert Duty.objects.active().count() == 1
