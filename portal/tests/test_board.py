import pytest
from django.urls import reverse

from allocation.services import publish_period, run_allocation
from duties.models import Duty, DutySource, DutyStatus
from exams.models import RoomAllocation

pytestmark = pytest.mark.django_db
JSON = {"HTTP_ACCEPT": "application/json"}


@pytest.fixture
def allocated(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    return small_period


def first_slot(period):
    return period.slots.order_by("date").first()


def test_board_shows_rooms_and_names(admin_client, allocated):
    slot = first_slot(allocated)
    page = admin_client.get(reverse("slot_board", args=[allocated.pk, slot.pk])).content.decode()
    assert "A1" in page and "A2" in page and 'draggable="true"' in page and "2/2" in page


def test_drop_moves_in_draft(admin_client, allocated, make_faculty):
    slot = first_slot(allocated)
    a1, a2 = RoomAllocation.objects.filter(session__slot=slot).order_by("room")
    RoomAllocation.objects.filter(pk=a2.pk).update(invigilators_required=3)
    duty = Duty.objects.filter(room_allocation=a1).first()
    r = admin_client.post(reverse("move_duty", args=[allocated.pk, duty.pk]), {"room": a2.pk}, **JSON)
    assert r.json()["ok"] is True
    duty.refresh_from_db()
    assert duty.room_allocation == a2


def test_refused_move_explains_why(admin_client, allocated):
    slot1, slot2 = list(allocated.slots.order_by("date"))
    duty = Duty.objects.filter(room_allocation__session__slot=slot1).first()
    busy = Duty.objects.filter(room_allocation__session__slot=slot2).first()
    # Put the person in slot 2 already, then try to drop their slot-1 duty into slot 2's room.
    Duty.objects.filter(pk=busy.pk).update(faculty=duty.faculty)
    r = admin_client.post(reverse("move_duty", args=[allocated.pk, duty.pk]), {"room": busy.room_allocation_id}, **JSON)
    assert r.status_code == 409 and r.json()["ok"] is False and "Already assigned" in r.json()["message"]


def test_menu_fallback_without_javascript_returns_to_the_board(admin_client, allocated):
    slot = first_slot(allocated)
    a1, a2 = RoomAllocation.objects.filter(session__slot=slot).order_by("room")
    RoomAllocation.objects.filter(pk=a2.pk).update(invigilators_required=3)
    duty = Duty.objects.filter(room_allocation=a1).first()
    board = reverse("slot_board", args=[allocated.pk, slot.pk])
    r = admin_client.post(reverse("move_duty", args=[allocated.pk, duty.pk]), {"room": a2.pk, "next": board})
    assert r.status_code == 302 and r.url == board


def test_published_move_needs_a_reason(admin_client, allocated, admin_user):
    publish_period(allocated, admin_user, confirm_rules=True)
    slot = first_slot(allocated)
    a1, a2 = RoomAllocation.objects.filter(session__slot=slot).order_by("room")
    duty = Duty.objects.filter(room_allocation=a1).first()
    r = admin_client.post(reverse("move_duty", args=[allocated.pk, duty.pk]), {"room": a2.pk}, **JSON)
    assert r.json()["ok"] is False and "reason" in r.json()["message"]
    r = admin_client.post(reverse("move_duty", args=[allocated.pk, duty.pk]), {"room": a2.pk, "reason": "Balance rooms"}, **JSON)
    assert r.json()["ok"] is True
    duty.refresh_from_db()
    assert duty.status == DutyStatus.CANCELLED and Duty.objects.filter(replaces=duty, source=DutySource.MOVED).exists()


def test_started_exam_is_not_movable(admin_client, allocated, admin_user):
    from datetime import date

    from exams.models import TimeSlot

    publish_period(allocated, admin_user, confirm_rules=True)
    slot = first_slot(allocated)
    TimeSlot.objects.filter(pk=slot.pk).update(date=date(2020, 1, 1))
    page = admin_client.get(reverse("slot_board", args=[allocated.pk, slot.pk])).content.decode()
    assert 'draggable="true"' not in page and "can no longer be moved" in page


def test_board_is_admin_only(client, faculty_user, allocated):
    client.force_login(faculty_user)
    assert client.get(reverse("slot_board", args=[allocated.pk, first_slot(allocated).pk])).status_code in (302, 403)
