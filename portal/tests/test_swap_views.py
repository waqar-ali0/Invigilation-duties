import pytest
from django.urls import reverse

from duties.models import Duty, DutySource, SwapRequest, SwapStatus
from duties.swaps import request_swap
from exams.models import ExamPeriod, PeriodStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(period, make_room, make_member, make_faculty):
    owner = make_member("Owner Person")
    peer = make_member("Peer Person")
    make_faculty("No Account Person")
    duty = Duty.objects.create(faculty=owner, room_allocation=make_room(day=2, room="A1"), source=DutySource.AUTO)
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    return owner, peer, duty


def test_my_duties_has_ask_button(client, setup):
    owner, _, duty = setup
    client.force_login(owner.user)
    assert reverse("ask_colleague", args=[duty.pk]) in client.get(reverse("my_duties")).content.decode()


def test_ask_page_lists_account_holders_and_sends(client, setup):
    owner, peer, duty = setup
    client.force_login(owner.user)
    page = client.get(reverse("ask_colleague", args=[duty.pk])).content.decode()
    assert "Peer Person" in page and "No Account Person" not in page
    r = client.post(reverse("ask_colleague", args=[duty.pk]), {"colleague_id": peer.pk, "message": "Please"})
    assert r.status_code == 302
    assert SwapRequest.objects.get().requested_with == peer


def test_ask_for_someone_elses_duty_404(client, setup):
    _, peer, duty = setup
    client.force_login(peer.user)
    assert client.get(reverse("ask_colleague", args=[duty.pk])).status_code == 404


def test_swap_requests_page_and_accept(client, setup):
    owner, peer, duty = setup
    swap = request_swap(duty, peer)
    client.force_login(peer.user)
    assert "Owner Person" in client.get(reverse("swap_requests")).content.decode()
    client.post(reverse("swap_respond", args=[swap.pk]), {"action": "accept"})
    swap.refresh_from_db()
    assert swap.status == SwapStatus.PENDING_ADMIN


def test_respond_and_cancel_scoped(client, setup, make_member):
    owner, peer, duty = setup
    swap = request_swap(duty, peer)
    client.force_login(make_member().user)
    assert client.post(reverse("swap_respond", args=[swap.pk]), {"action": "accept"}).status_code == 404
    assert client.post(reverse("swap_cancel", args=[swap.pk])).status_code == 404
    client.force_login(owner.user)
    client.post(reverse("swap_cancel", args=[swap.pk]))
    swap.refresh_from_db()
    assert swap.status == SwapStatus.CANCELLED
