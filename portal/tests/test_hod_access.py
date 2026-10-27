import pytest
from django.urls import reverse

from accounts.models import HodAccess, Role
from allocation.services import publish_period, run_allocation
from duties.models import Duty, DutyStatus
from faculty.models import Department

pytestmark = pytest.mark.django_db


def allow(**flags):
    HodAccess.get()  # the row is created on first use
    HodAccess.objects.update(**flags)


@pytest.fixture
def hod(client, dept, make_member):
    head = make_member("Head Person", role=Role.HOD)
    dept.hod = head
    dept.save()
    client.force_login(head.user)
    return head


@pytest.fixture
def published(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    publish_period(small_period, admin_user, confirm_rules=True)
    return small_period


def test_defaults_and_switching_off_is_enforced_on_the_server(client, hod, published):
    assert client.get(reverse("hod_workload")).status_code == 200
    assert client.get(reverse("hod_missed")).status_code == 200
    assert client.get(reverse("hod_reports")).status_code == 403  # off by default
    allow(see_workload=False)
    assert client.get(reverse("hod_workload")).status_code == 403
    assert "Workload" not in client.get(reverse("department_duties")).content.decode().split("<main", 1)[0].split("My department", 1)[1]


def test_workload_is_limited_to_their_department(client, hod, published, make_faculty):
    other = Department.objects.create(name="Physics")
    stranger = make_faculty("Physics Person", department=other)
    page = client.get(reverse("hod_workload")).content.decode()
    assert "Teacher 0" in page and stranger.name not in page


def test_contacts_only_when_allowed(client, hod, published):
    Duty.objects.filter(period=published).first().faculty.__class__.objects.update(phone="0300-1234567", phone_e164="+923001234567")
    assert "+923001234567" not in client.get(reverse("department_duties")).content.decode()
    allow(see_contacts=True)
    assert "+923001234567" in client.get(reverse("department_duties")).content.decode()


def test_deciding_absences_needs_permission_and_never_their_own(client, hod, published):
    duty = Duty.objects.filter(period=published).first()
    Duty.objects.filter(pk=duty.pk).update(status=DutyStatus.ABSENT)
    assert client.post(reverse("hod_missed"), {"duty": duty.pk, "reason": "Ill", "excused": "yes"}).status_code == 403
    allow(decide_absences=True)
    client.post(reverse("hod_missed"), {"duty": duty.pk, "reason": "Ill", "excused": "yes"})
    duty.refresh_from_db()
    assert duty.absence_excused is True and duty.absence_reason == "Ill"
    own = Duty.objects.create(faculty=hod, room_allocation=duty.room_allocation, status=DutyStatus.ABSENT, source="manual")
    assert client.post(reverse("hod_missed"), {"duty": own.pk, "reason": "Me", "excused": "yes"}).status_code == 403


def test_reports_are_forced_to_their_department(client, hod, published, make_faculty):
    allow(download_reports=True)
    other = Department.objects.create(name="Physics")
    make_faculty("Physics Person", department=other)
    r = client.get(reverse("hod_reports"), {"type": "workload", "department": other.pk, "format": "csv"})
    text = r.content.decode("utf-8-sig")
    assert r.status_code == 200 and "Physics Person" not in text and "Teacher 0" in text
    assert client.get(reverse("hod_reports"), {"type": "overrides"}).status_code == 200  # unknown type falls back to workload


def test_only_the_exam_office_sets_permissions(client, hod, admin_user):
    from django.test import Client

    assert client.get(reverse("hod_access")).status_code in (302, 403)
    office = Client()
    office.force_login(admin_user)
    office.post(reverse("hod_access"), {"see_workload": "on", "download_reports": "on"})
    access = HodAccess.get()
    assert access.download_reports and not access.see_missed
