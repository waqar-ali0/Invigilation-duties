from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from duties.models import Unavailability, UnavailabilityStatus
from duties.unavailability import submit_request
from exams.availability import open_availability

pytestmark = pytest.mark.django_db


@pytest.fixture
def open_period(period, make_room, admin_user):
    make_room(day=2)
    open_availability(period, timezone.now() + timedelta(days=3), admin_user)
    period.refresh_from_db()
    return period


def test_form_shown_for_open_period_and_submit(client, open_period, make_member):
    me = make_member()
    client.force_login(me.user)
    page = client.get(reverse("my_unavailability")).content.decode()
    assert "Fall 2026 Finals" in page and 'name="reason"' in page
    r = client.post(reverse("my_unavailability"), {"period": open_period.pk, "date": "2026-12-02", "slot": "", "reason": "Wedding"})
    assert r.status_code == 302
    assert Unavailability.objects.get(faculty=me).reason == "Wedding"
    assert "Wedding" in client.get(reverse("my_unavailability")).content.decode()


def test_submit_errors_shown(client, open_period, make_member):
    client.force_login(make_member().user)
    r = client.post(reverse("my_unavailability"), {"period": open_period.pk, "date": "2027-01-01", "slot": "", "reason": "x"})
    assert r.status_code == 200 and "Date is outside the exam period." in r.content.decode()
    assert not Unavailability.objects.exists()


def test_no_form_when_window_closed(client, period, make_member):
    client.force_login(make_member().user)
    page = client.get(reverse("my_unavailability")).content.decode()
    assert 'name="reason"' not in page and "No exam period is accepting requests" in page


def test_exempt_faculty_sees_message(client, open_period, make_member):
    client.force_login(make_member(is_exempt=True).user)
    page = client.get(reverse("my_unavailability")).content.decode()
    assert "You are exempt from invigilation" in page and 'name="reason"' not in page


def test_withdraw_own_request(client, open_period, make_member):
    me = make_member()
    req = submit_request(me, open_period, open_period.start_date + timedelta(days=1), None, "x")
    client.force_login(me.user)
    client.post(reverse("withdraw_request", args=[req.pk]))
    req.refresh_from_db()
    assert req.status == UnavailabilityStatus.WITHDRAWN


def test_cannot_withdraw_someone_elses_request(client, open_period, make_member):
    owner, intruder = make_member(), make_member()
    req = submit_request(owner, open_period, open_period.start_date + timedelta(days=1), None, "x")
    client.force_login(intruder.user)
    assert client.post(reverse("withdraw_request", args=[req.pk])).status_code == 404
    req.refresh_from_db()
    assert req.status == UnavailabilityStatus.PENDING


def test_slot_options_carry_their_date_for_filtering(client, open_period, make_member, make_room):
    make_room(day=3, start="14:00", end="17:00", course="Z")
    client.force_login(make_member().user)
    page = client.get(reverse("my_unavailability")).content.decode()
    assert page.count('data-date="2026-12-02"') == 1 and page.count('data-date="2026-12-03"') == 1
    assert "data-slot-filter" in page
