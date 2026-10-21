from datetime import date

import pytest
from django.urls import reverse

from allocation.services import roster_conflicts, run_allocation
from duties.models import Duty, DutySource, Unavailability, UnavailabilityStatus
from faculty.models import Faculty

pytestmark = pytest.mark.django_db


def test_no_conflicts_after_allocation(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    assert roster_conflicts(small_period) == []


def test_unavailability_approved_after_allocation_is_a_conflict(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    duty = Duty.objects.filter(period=small_period).select_related("room_allocation__session__slot").first()
    Unavailability.objects.create(
        faculty=duty.faculty, period=small_period, date=duty.room_allocation.session.slot.date,
        reason="x", status=UnavailabilityStatus.APPROVED,
    )
    assert [(d.pk, reason) for d, reason in roster_conflicts(small_period)] == [(duty.pk, "Approved as unavailable at this time")]


def test_slot_specific_and_pending_requests(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    duty = Duty.objects.filter(period=small_period).select_related("room_allocation__session__slot").first()
    slot = duty.room_allocation.session.slot
    other_slot = small_period.slots.exclude(pk=slot.pk).first()
    Unavailability.objects.create(faculty=duty.faculty, period=small_period, date=other_slot.date, slot=other_slot, reason="x", status="approved")
    Unavailability.objects.create(faculty=duty.faculty, period=small_period, date=slot.date, slot=slot, reason="x", status="pending")
    assert roster_conflicts(small_period) == []
    Unavailability.objects.create(faculty=duty.faculty, period=small_period, date=slot.date, slot=slot, reason="y", status="approved")
    assert len(roster_conflicts(small_period)) == 1


def test_exempt_or_inactive_after_allocation_is_a_conflict(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    duty = Duty.objects.filter(period=small_period).first()
    Faculty.objects.filter(pk=duty.faculty_id).update(is_exempt=True)
    conflicts = roster_conflicts(small_period)
    assert {reason for _, reason in conflicts} == {"Faculty is now inactive or exempt"}
    assert all(d.faculty_id == duty.faculty_id for d, _ in conflicts)


def test_rerun_clears_auto_conflicts(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    duty = Duty.objects.filter(period=small_period, source=DutySource.AUTO).first()
    Faculty.objects.filter(pk=duty.faculty_id).update(is_active=False)
    run_allocation(small_period, admin_user, seed=1)
    assert roster_conflicts(small_period) == []


def test_dashboard_and_publish_page_warn(admin_client, small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    duty = Duty.objects.filter(period=small_period).first()
    Faculty.objects.filter(pk=duty.faculty_id).update(is_exempt=True)
    for name in ("period_dashboard", "publish"):
        page = admin_client.get(reverse(name, args=[small_period.pk])).content.decode()
        assert "conflict" in page.lower() and "Faculty is now inactive or exempt" in page, name
