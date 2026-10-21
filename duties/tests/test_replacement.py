import pytest
from django.urls import reverse

from audit.models import AuditLog
from duties.handover import HandoverBlocked, replace_duty
from duties.models import Duty, DutySource, DutyStatus, SwapStatus
from duties.swaps import request_swap
from duties.tests.conftest import move_slot_to_past
from exams.models import ExamPeriod, PeriodLockedError, PeriodStatus
from notifications.models import OutboundEmail

pytestmark = pytest.mark.django_db


def blocked(*args):
    with pytest.raises(HandoverBlocked) as e:
        replace_duty(*args)
    return e.value.errors


def test_replace_success(published_duty, make_faculty, admin_user):
    new_person = make_faculty("Stand In", email="standin@uni.edu")  # no account needed
    new = replace_duty(published_duty, new_person, admin_user, "Sick")
    published_duty.refresh_from_db()
    assert published_duty.status == DutyStatus.CANCELLED
    assert (new.faculty, new.source, new.replaces) == (new_person, DutySource.REPLACEMENT, published_duty)
    assert OutboundEmail.objects.get(kind="replaced_out").to == published_duty.faculty.user.email
    incoming = OutboundEmail.objects.get(kind="replaced_in")
    assert incoming.to == "standin@uni.edu" and incoming.attachment_name.endswith(".ics")
    entry = AuditLog.objects.get(action="duty.replace")
    assert entry.after["reason"] == "Sick" and entry.before["faculty"] == published_duty.faculty_id


def test_reason_required(published_duty, make_faculty, admin_user):
    assert blocked(published_duty, make_faculty(), admin_user, "  ") == ["A reason is required."]


def test_marked_duty_cannot_be_replaced(published_duty, make_faculty, admin_user):
    Duty.objects.filter(pk=published_duty.pk).update(status=DutyStatus.COMPLETED)
    assert blocked(published_duty, make_faculty(), admin_user, "x") == [
        "Only duties not yet marked present or absent can be replaced."
    ]


def test_ended_exam_cannot_be_replaced(published_duty, make_faculty, admin_user):
    move_slot_to_past(published_duty)
    assert blocked(published_duty, make_faculty(), admin_user, "x") == ["This exam has already ended."]


def test_busy_candidate_refused(published_duty, make_faculty, make_room, admin_user):
    busy = make_faculty()
    Duty.objects.create(faculty=busy, room_allocation=make_room(day=2, course="CS102", room="A2"), source=DutySource.AUTO)
    assert blocked(published_duty, busy, admin_user, "x") == ["Already assigned in this time slot"]


def test_open_swap_is_cancelled(published_duty, make_member, make_faculty, admin_user):
    peer = make_member()
    swap = request_swap(published_duty, peer)
    replace_duty(published_duty, make_faculty(), admin_user, "Emergency")
    swap.refresh_from_db()
    assert swap.status == SwapStatus.CANCELLED
    assert sorted(OutboundEmail.objects.filter(kind="swap_cancelled").values_list("to", flat=True)) == sorted(
        [published_duty.faculty.user.email, peer.user.email]
    )


def test_draft_period_refused(published_duty, make_faculty, admin_user):
    ExamPeriod.objects.filter(pk=published_duty.period_id).update(status=PeriodStatus.ALLOCATING)
    with pytest.raises(PeriodLockedError):
        replace_duty(published_duty, make_faculty(), admin_user, "x")


# --- views ---------------------------------------------------------------------------


def test_room_page_shows_replace_for_published(admin_client, published_duty):
    room = published_duty.room_allocation
    url = reverse("room_detail", args=[published_duty.period_id, room.pk])
    assert reverse("replace_duty", args=[published_duty.period_id, published_duty.pk]) in admin_client.get(url).content.decode()


def test_replace_view(admin_client, published_duty, make_faculty):
    new_person = make_faculty("Stand In")
    url = reverse("replace_duty", args=[published_duty.period_id, published_duty.pk])
    assert "Stand In" in admin_client.get(url).content.decode()
    r = admin_client.post(url, {"faculty_id": new_person.pk, "reason": "Sick"})
    assert r.status_code == 302 and Duty.objects.filter(faculty=new_person).exists()


def test_replace_view_shows_errors(admin_client, published_duty, make_faculty):
    url = reverse("replace_duty", args=[published_duty.period_id, published_duty.pk])
    r = admin_client.post(url, {"faculty_id": make_faculty().pk, "reason": ""}, follow=True)
    assert "A reason is required." in r.content.decode()


def test_replace_view_scoped_and_admin_only(client, admin_client, published_duty, period):
    other = ExamPeriod.objects.create(name="Other", start_date=period.start_date, end_date=period.end_date)
    assert admin_client.get(reverse("replace_duty", args=[other.pk, published_duty.pk])).status_code == 404
    client.force_login(published_duty.faculty.user)
    assert client.get(reverse("replace_duty", args=[published_duty.period_id, published_duty.pk])).status_code == 403
