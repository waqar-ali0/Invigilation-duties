import pytest
from django.urls import reverse

from audit.models import AuditLog
from duties.models import Duty, DutySource, DutyStatus, SwapStatus
from duties.swaps import SwapError, accept_swap, approve_swap, reject_swap, request_swap
from exams.models import ExamPeriod, PeriodStatus
from notifications.models import OutboundEmail

pytestmark = pytest.mark.django_db


@pytest.fixture
def accepted(published_duty, make_member):
    peer = make_member("Peer Person")
    swap = accept_swap(request_swap(published_duty, peer), peer)
    return swap, peer


def test_approve_transfers_duty(accepted, admin_user, published_duty):
    swap, peer = accepted
    new = approve_swap(swap, admin_user)
    published_duty.refresh_from_db()
    swap.refresh_from_db()
    assert published_duty.status == DutyStatus.CANCELLED
    assert (new.faculty, new.source, new.replaces, new.room_allocation_id) == (
        peer,
        DutySource.SWAP,
        published_duty,
        published_duty.room_allocation_id,
    )
    assert (swap.status, swap.new_duty, swap.decided_by) == (SwapStatus.APPROVED, new, admin_user)
    emails = {e.to: e for e in OutboundEmail.objects.filter(kind="swap_approved")}
    assert set(emails) == {peer.user.email, published_duty.faculty.user.email}
    assert emails[peer.user.email].attachment_name.endswith(".ics")
    assert not emails[published_duty.faculty.user.email].attachment_name
    assert AuditLog.objects.get(action="swap.approve").after["faculty"] == peer.pk


def test_approve_blocked_when_colleague_no_longer_free(accepted, admin_user, make_room, published_duty):
    swap, peer = accepted
    Duty.objects.create(faculty=peer, room_allocation=make_room(day=2, course="CS102", room="A2"), source=DutySource.REPLACEMENT)
    with pytest.raises(SwapError) as e:
        approve_swap(swap, admin_user)
    assert e.value.errors == ["Already assigned in this time slot"]
    published_duty.refresh_from_db()
    swap.refresh_from_db()
    assert published_duty.status == DutyStatus.ASSIGNED and swap.status == SwapStatus.PENDING_ADMIN


def test_approve_only_pending_admin(published_duty, make_member, admin_user):
    swap = request_swap(published_duty, make_member())
    with pytest.raises(SwapError) as e:
        approve_swap(swap, admin_user)
    assert e.value.errors == ["This request is no longer waiting for approval."]


def test_approve_on_closed_period(accepted, admin_user, published_duty):
    swap, _ = accepted
    ExamPeriod.objects.filter(pk=published_duty.period_id).update(status=PeriodStatus.CLOSED)
    with pytest.raises(SwapError) as e:
        approve_swap(swap, admin_user)
    assert e.value.errors == ["Only a published roster can be changed this way."]


def test_reject(accepted, admin_user, published_duty):
    swap, peer = accepted
    swap = reject_swap(swap, admin_user, "Not this week")
    assert (swap.status, swap.comment) == (SwapStatus.REJECTED, "Not this week")
    bodies = OutboundEmail.objects.filter(kind="swap_rejected")
    assert bodies.count() == 2 and all("Not this week" in e.body for e in bodies)


def test_admin_swaps_page_and_decide(admin_client, accepted, published_duty):
    swap, peer = accepted
    page = admin_client.get(reverse("admin_swaps")).content.decode()
    assert "Owner Person" in page and "Peer Person" in page
    admin_client.post(reverse("swap_decide", args=[swap.pk]), {"decision": "approve"})
    swap.refresh_from_db()
    assert swap.status == SwapStatus.APPROVED


def test_admin_swaps_admin_only(client, accepted):
    swap, peer = accepted
    client.force_login(peer.user)
    assert client.get(reverse("admin_swaps")).status_code == 403
    assert client.post(reverse("swap_decide", args=[swap.pk]), {"decision": "approve"}).status_code == 403


def test_dashboard_shows_waiting_swaps(admin_client, accepted, published_duty):
    page = admin_client.get(reverse("period_dashboard", args=[published_duty.period_id])).content.decode()
    assert "1 swap(s) waiting for approval" in page
