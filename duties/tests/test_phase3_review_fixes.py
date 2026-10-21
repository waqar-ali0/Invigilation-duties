"""Regression tests for the findings of the Phase 3 whole-branch review."""

import pytest

from duties.models import SwapRequest, SwapStatus
from duties.swaps import accept_swap, approve_swap, cancel_swap, expire_if_started, request_swap
from duties.tests.conftest import move_slot_to_past
from notifications.models import OutboundEmail

pytestmark = pytest.mark.django_db


def test_expiry_never_overwrites_a_decided_swap(published_duty, make_member, admin_user):
    peer = make_member()
    swap = accept_swap(request_swap(published_duty, peer), peer)
    stale = SwapRequest.objects.get(pk=swap.pk)  # a list page loaded it while it was open
    approve_swap(swap, admin_user)
    move_slot_to_past(published_duty)
    stale.duty.refresh_from_db()
    expire_if_started(stale)
    swap.refresh_from_db()
    assert swap.status == SwapStatus.APPROVED and stale.status == SwapStatus.APPROVED


def test_cancelling_accepted_swap_tells_colleague_and_exam_office(published_duty, make_member, admin_user):
    peer = make_member()
    swap = accept_swap(request_swap(published_duty, peer), peer)
    cancel_swap(swap, published_duty.faculty)
    recipients = sorted(OutboundEmail.objects.filter(kind="swap_withdrawn").values_list("to", flat=True))
    assert recipients == sorted([peer.user.email, admin_user.email])


def test_cancelling_unanswered_swap_tells_colleague_only(published_duty, make_member, admin_user):
    peer = make_member()
    cancel_swap(request_swap(published_duty, peer), published_duty.faculty)
    assert list(OutboundEmail.objects.filter(kind="swap_withdrawn").values_list("to", flat=True)) == [peer.user.email]
