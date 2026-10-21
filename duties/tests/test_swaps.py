from datetime import datetime

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from accounts.models import Role, User
from audit.models import AuditLog
from duties.models import Duty, DutySource, DutyStatus, SwapRequest, SwapStatus
from duties.swaps import SwapError, accept_swap, cancel_swap, cutoff_passed, decline_swap, expire_if_started, request_swap
from duties.tests.conftest import move_slot_to_past, set_cutoff
from exams.models import ExamPeriod, PeriodStatus
from notifications.models import OutboundEmail

pytestmark = pytest.mark.django_db


def errors(fn, *args):
    with pytest.raises(SwapError) as e:
        fn(*args)
    return e.value.errors


def test_cutoff_in_local_time(published_duty):
    # Exam 2 Dec 2026 09:00 local, cut-off 24 h → requests close 1 Dec 09:00 local.
    local = lambda h, m: timezone.make_aware(datetime(2026, 12, 1, h, m))  # noqa: E731
    assert not cutoff_passed(published_duty, now=local(8, 30))
    assert cutoff_passed(published_duty, now=local(9, 30))


def test_request_happy_path(published_duty, make_member):
    peer = make_member("Peer Person")
    swap = request_swap(published_duty, peer, "Family event")
    assert (swap.status, swap.requested_by, swap.message) == (SwapStatus.PENDING_PEER, published_duty.faculty, "Family event")
    email = OutboundEmail.objects.get(kind="swap_requested")
    assert email.to == peer.user.email and "Owner Person" in email.body and "A1" in email.body
    assert AuditLog.objects.filter(action="swap.request").exists()


def test_request_errors(published_duty, make_member, make_faculty, make_room):
    assert errors(request_swap, published_duty, make_faculty("No Account")) == ["No Account has no active account."]
    busy = make_member("Busy")
    Duty.objects.create(faculty=busy, room_allocation=make_room(day=2, course="CS102", room="A2"), source=DutySource.AUTO)
    assert errors(request_swap, published_duty, busy) == ["Already assigned in this time slot"]


def test_request_needs_published_period(published_duty, make_member):
    ExamPeriod.objects.filter(pk=published_duty.period_id).update(status=PeriodStatus.CLOSED)
    published_duty.refresh_from_db()
    assert errors(request_swap, published_duty, make_member()) == ["Only a published roster can be changed this way."]


def test_request_after_cutoff(published_duty, make_member):
    set_cutoff(published_duty.period, 100000)
    assert errors(request_swap, published_duty, make_member()) == [
        "It is too late to swap this duty. Contact the exam office."
    ]


def test_request_duplicate_and_marked_duty(published_duty, make_member):
    request_swap(published_duty, make_member())
    assert errors(request_swap, published_duty, make_member()) == ["There is already an open swap request for this duty."]
    Duty.objects.filter(pk=published_duty.pk).update(status=DutyStatus.COMPLETED)
    published_duty.refresh_from_db()
    assert "This duty can no longer be swapped." in errors(request_swap, published_duty, make_member())


def test_accept_notifies_admins(published_duty, make_member, admin_user):
    User.objects.create_user("inactive-admin@uni.edu", "x", role=Role.ADMIN, is_active=False)
    peer = make_member()
    swap = accept_swap(request_swap(published_duty, peer), peer)
    assert swap.status == SwapStatus.PENDING_ADMIN and swap.responded_at
    assert list(OutboundEmail.objects.filter(kind="swap_accepted").values_list("to", flat=True)) == [admin_user.email]


def test_accept_only_by_colleague(published_duty, make_member):
    swap = request_swap(published_duty, make_member())
    with pytest.raises(PermissionDenied):
        accept_swap(swap, make_member())


def test_accept_rechecks_rules(published_duty, make_member, make_room):
    peer = make_member()
    swap = request_swap(published_duty, peer)
    Duty.objects.create(faculty=peer, room_allocation=make_room(day=2, course="CS102", room="A2"), source=DutySource.AUTO)
    assert errors(accept_swap, swap, peer) == ["Already assigned in this time slot"]
    swap.refresh_from_db()
    assert swap.status == SwapStatus.PENDING_PEER


def test_accept_after_cutoff(published_duty, make_member):
    peer = make_member()
    swap = request_swap(published_duty, peer)
    set_cutoff(published_duty.period, 100000)
    assert errors(accept_swap, swap, peer) == ["It is too late to swap this duty. Contact the exam office."]


def test_decline_and_cancel(published_duty, make_member):
    peer = make_member()
    swap = decline_swap(request_swap(published_duty, peer), peer)
    assert swap.status == SwapStatus.DECLINED
    assert OutboundEmail.objects.get(kind="swap_declined").to == published_duty.faculty.user.email
    swap2 = request_swap(published_duty, peer)
    with pytest.raises(PermissionDenied):
        cancel_swap(swap2, peer)
    assert cancel_swap(swap2, published_duty.faculty).status == SwapStatus.CANCELLED
    assert errors(decline_swap, swap2, peer) == ["This request is no longer open."]


def test_open_swap_expires_when_exam_starts(published_duty, make_member):
    peer = make_member()
    swap = request_swap(published_duty, peer)
    move_slot_to_past(published_duty)
    swap = SwapRequest.objects.get(pk=swap.pk)
    assert expire_if_started(swap).status == SwapStatus.EXPIRED
    assert errors(accept_swap, swap, peer) == ["This request is no longer open."]
