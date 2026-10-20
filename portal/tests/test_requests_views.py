from datetime import date

import pytest
from django.urls import reverse

from accounts.models import Role
from duties.models import Unavailability, UnavailabilityStatus
from faculty.models import Department

pytestmark = pytest.mark.django_db
D = date(2026, 12, 2)


@pytest.fixture
def hod(dept, make_member):
    h = make_member("Head Person", role=Role.HOD)
    dept.hod = h
    dept.save()
    h.user.refresh_from_db()
    return h


def req(faculty, period, reason="Conference"):
    return Unavailability.objects.create(faculty=faculty, period=period, date=D, reason=reason)


def test_department_requests_lists_department_only_and_not_own(client, period, hod, make_member):
    req(make_member("Team Member"), period, "Team reason")
    req(hod, period, "Own reason")
    physics = Department.objects.create(name="Physics")
    req(make_member("Outsider", department=physics), period, "Outside reason")
    client.force_login(hod.user)
    page = client.get(reverse("department_requests")).content.decode()
    assert "Team reason" in page and "Own reason" not in page and "Outside reason" not in page


def test_department_requests_hod_only(client, make_member):
    client.force_login(make_member().user)
    assert client.get(reverse("department_requests")).status_code == 403


def test_hod_approves_via_view(client, period, hod, make_member):
    r = req(make_member(), period)
    client.force_login(hod.user)
    client.post(reverse("decide_request", args=[r.pk]), {"decision": "approve", "comment": "Fine"})
    r.refresh_from_db()
    assert r.status == UnavailabilityStatus.APPROVED and r.review_comment == "Fine"


def test_hod_cannot_decide_own_via_view(client, period, hod):
    r = req(hod, period)
    client.force_login(hod.user)
    assert client.post(reverse("decide_request", args=[r.pk]), {"decision": "approve"}).status_code == 404
    r.refresh_from_db()
    assert r.status == UnavailabilityStatus.PENDING


def test_admin_requests_page_flags_hod_requests(admin_client, period, hod, make_member):
    req(hod, period, "Hod own reason")
    req(make_member(), period, "Member reason")
    page = admin_client.get(reverse("admin_requests")).content.decode()
    assert "Hod own reason" in page and "Member reason" in page and "HOD request" in page


def test_admin_rejects_via_view(admin_client, period, hod):
    r = req(hod, period)
    admin_client.post(reverse("decide_request", args=[r.pk]), {"decision": "reject", "comment": "No"})
    r.refresh_from_db()
    assert r.status == UnavailabilityStatus.REJECTED


def test_bad_decision_value_does_nothing(admin_client, period, make_member):
    r = req(make_member(), period)
    admin_client.post(reverse("decide_request", args=[r.pk]), {"decision": "maybe"})
    r.refresh_from_db()
    assert r.status == UnavailabilityStatus.PENDING
