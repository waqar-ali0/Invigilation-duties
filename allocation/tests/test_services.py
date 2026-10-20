from datetime import date

import pytest

from allocation.services import (
    PublishBlocked,
    capacity_report,
    clear_draft,
    publish_period,
    run_allocation,
    unfilled_rooms,
)
from audit.models import AuditLog
from duties.models import Duty, DutySource, Unavailability, UnavailabilityStatus
from exams.models import DesignationCap, PeriodLockedError, PeriodStatus, RoomAllocation
from faculty.models import Designation, Faculty

pytestmark = pytest.mark.django_db


def test_run_creates_auto_duties_and_records_last_run(small_period, admin_user):
    res = run_allocation(small_period, admin_user, seed=5)
    assert len(res.assignments) == 6
    assert Duty.objects.filter(period=small_period, source=DutySource.AUTO).count() == 6
    small_period.refresh_from_db()
    assert small_period.status == PeriodStatus.ALLOCATING
    assert small_period.allocation_seed == 5 and small_period.last_run["seed"] == 5
    assert small_period.last_run["unfilled"] == [] and small_period.last_run["assigned"] == 6
    assert AuditLog.objects.filter(action="allocation.run").exists()


def test_rerun_replaces_auto_duties(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    run_allocation(small_period, admin_user, seed=2)
    assert Duty.objects.filter(period=small_period).count() == 6


def test_rerun_keeps_manual_duties_and_counts_them(small_period, admin_user, make_faculty):
    DesignationCap.objects.create(period=small_period, designation=Designation.PROFESSOR, max_duties=1)
    prof = make_faculty("Prof", designation=Designation.PROFESSOR)
    Duty.objects.create(faculty=prof, room_allocation=RoomAllocation.objects.first(), source=DutySource.MANUAL)
    for seed in range(1, 6):
        run_allocation(small_period, admin_user, seed=seed)
        duties = Duty.objects.filter(faculty=prof)
        assert duties.count() == 1 and duties.get().source == DutySource.MANUAL
    assert Duty.objects.filter(period=small_period).count() == 6


def test_rerun_drops_auto_duties_of_newly_exempt(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    fac = Duty.objects.first().faculty
    Faculty.objects.filter(pk=fac.pk).update(is_exempt=True)
    run_allocation(small_period, admin_user, seed=1)
    assert not Duty.objects.filter(faculty=fac).exists()


def test_only_approved_unavailability_blocks(period, make_room, make_faculty, admin_user):
    make_room(day=1, needed=2)
    a = make_faculty("A")
    make_faculty("B")
    u = Unavailability.objects.create(faculty=a, period=period, date=date(2026, 12, 1), reason="Conference")
    run_allocation(period, admin_user, seed=1)
    assert Duty.objects.filter(faculty=a).exists()
    u.status = UnavailabilityStatus.APPROVED
    u.save()
    run_allocation(period, admin_user, seed=1)
    assert not Duty.objects.filter(faculty=a).exists()


def test_run_on_published_period_raises(small_period, admin_user):
    small_period.status = PeriodStatus.PUBLISHED
    small_period.save()
    with pytest.raises(PeriodLockedError):
        run_allocation(small_period, admin_user)


def test_clear_draft_deletes_all_and_audits(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    assert clear_draft(small_period, admin_user) == 6
    assert not Duty.objects.exists()
    assert AuditLog.objects.get(action="allocation.clear").after == {"deleted": 6}


def test_capacity_report_and_unfilled_rooms(short_period, admin_user):
    report = capacity_report(short_period)
    assert (report.seats_needed, report.capacity, report.ok) == (5, 16, True)
    assert report.slot_shortages != []
    run_allocation(short_period, admin_user, seed=1)
    [(room, missing, reason)] = unfilled_rooms(short_period)
    assert room.room == "Hall" and missing == 3
    assert reason == "All faculty are already assigned in this time slot"


def test_publish_requires_ack_when_unfilled(short_period, admin_user):
    run_allocation(short_period, admin_user, seed=1)
    with pytest.raises(PublishBlocked, match="3 seats are unfilled"):
        publish_period(short_period, admin_user)
    publish_period(short_period, admin_user, acknowledge_unfilled=True)
    short_period.refresh_from_db()
    assert short_period.status == PeriodStatus.PUBLISHED and short_period.published_at is not None
    assert AuditLog.objects.filter(action="period.publish").exists()


def test_publish_twice_raises_locked(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    publish_period(small_period, admin_user)
    with pytest.raises(PeriodLockedError):
        publish_period(small_period, admin_user)


def test_publish_with_no_duties_blocked(small_period, admin_user):
    with pytest.raises(PublishBlocked, match="no duties"):
        publish_period(small_period, admin_user, acknowledge_unfilled=True)
