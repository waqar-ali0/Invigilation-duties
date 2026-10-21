from datetime import datetime, time, timezone as dt_timezone

import pytest
from django.db import IntegrityError, transaction

from duties.handover import handover_candidates, handover_check, transfer_duty
from duties.models import Duty, DutySource, DutyStatus, SwapRequest, SwapStatus
from exams.models import ExamPeriod, PeriodLockedError, PeriodStatus, TimeSlot

pytestmark = pytest.mark.django_db


def test_lock_published(period):
    with pytest.raises(PeriodLockedError, match="Only a published roster can be changed this way."):
        with transaction.atomic():
            ExamPeriod.lock_published(period.pk)
    for status, ok in ((PeriodStatus.PUBLISHED, True), (PeriodStatus.CLOSED, False)):
        ExamPeriod.objects.filter(pk=period.pk).update(status=status)
        with transaction.atomic():
            if ok:
                assert ExamPeriod.lock_published(period.pk).pk == period.pk
            else:
                with pytest.raises(PeriodLockedError):
                    ExamPeriod.lock_published(period.pk)


def test_default_swap_cutoff(period):
    assert period.settings.swap_cutoff_hours == 24


def test_slot_start_and_end_are_local(room):
    slot = room.session.slot
    assert slot.starts_at.astimezone(dt_timezone.utc) == datetime(2026, 12, 1, 4, 0, tzinfo=dt_timezone.utc)
    assert slot.ends_at.astimezone(dt_timezone.utc) == datetime(2026, 12, 1, 7, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def duty(room, make_faculty):
    return Duty.objects.create(faculty=make_faculty("Owner"), room_allocation=room, source=DutySource.AUTO)


def test_handover_check(duty, make_room, make_faculty):
    free = make_faculty("Free")
    busy = make_faculty("Busy")
    other_room = make_room(course="CS999", room="Z9")
    Duty.objects.create(faculty=busy, room_allocation=other_room, source=DutySource.AUTO)
    assert handover_check(duty, free) == ([], [])  # the room isn't "full": the handed-over seat is free
    assert handover_check(duty, busy)[0] == ["Already assigned in this time slot"]
    assert handover_check(duty, duty.faculty)[0] == ["This is already their duty"]
    assert handover_check(duty, make_faculty(is_exempt=True))[0] == ["Faculty is inactive or exempt"]


def test_handover_candidates_require_account(duty, make_faculty, make_member):
    make_faculty("No Account")
    member = make_member("Has Account")
    invited = make_member("Invited Only")
    invited.user.set_unusable_password()
    invited.user.save()
    names = [f.name for f, _, _ in handover_candidates(duty, require_account=True)]
    assert names == ["Has Account"]
    all_names = [f.name for f, _, _ in handover_candidates(duty, require_account=False)]
    assert "No Account" in all_names and "Owner" not in all_names and member.name in all_names


def test_transfer_duty(duty, make_faculty):
    new = transfer_duty(duty, make_faculty("New"), DutySource.REPLACEMENT)
    duty.refresh_from_db()
    assert duty.status == DutyStatus.CANCELLED
    assert (new.source, new.replaces, new.room_allocation) == (DutySource.REPLACEMENT, duty, duty.room_allocation)


def test_one_open_swap_per_duty(duty, make_faculty):
    peer = make_faculty()
    SwapRequest.objects.create(duty=duty, requested_by=duty.faculty, requested_with=peer)
    with pytest.raises(IntegrityError), transaction.atomic():
        SwapRequest.objects.create(duty=duty, requested_by=duty.faculty, requested_with=peer, status=SwapStatus.PENDING_ADMIN)
    SwapRequest.objects.create(duty=duty, requested_by=duty.faculty, requested_with=peer, status=SwapStatus.DECLINED)
