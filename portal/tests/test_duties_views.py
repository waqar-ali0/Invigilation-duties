import pytest
from django.urls import reverse

from accounts.models import Role
from duties.models import Duty, DutySource
from exams.models import ExamPeriod, PeriodStatus
from faculty.models import Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(period, make_room, make_member, dept):
    me = make_member("Me Person")
    hod = make_member("Head Person", role=Role.HOD)
    dept.hod = hod
    dept.save()
    hod.user.refresh_from_db()
    physics = Department.objects.create(name="Physics")
    outsider = make_member("Outside Person", department=physics)
    Duty.objects.create(faculty=me, room_allocation=make_room(room="R1", course="C1"), source=DutySource.MANUAL)
    Duty.objects.create(faculty=outsider, room_allocation=make_room(room="R9", course="C9"), source=DutySource.MANUAL)
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    return me, hod


def test_my_duties_downloads(client, setup, period):
    me, _ = setup
    client.force_login(me.user)
    page = client.get(reverse("my_duties")).content.decode()
    assert reverse("my_duties_pdf", args=[period.pk]) in page and reverse("my_duties_ics", args=[period.pk]) in page
    pdf = client.get(reverse("my_duties_pdf", args=[period.pk]))
    assert pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    ics = client.get(reverse("my_duties_ics", args=[period.pk]))
    assert ics["Content-Type"] == "text/calendar; charset=utf-8" and b"LOCATION:R1" in ics.content
    assert b"R9" not in ics.content


def test_downloads_404_for_draft_period(client, setup, period):
    me, _ = setup
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.ALLOCATING)
    client.force_login(me.user)
    assert client.get(reverse("my_duties_ics", args=[period.pk])).status_code == 404


def test_department_duties_lists_headed_departments_only(client, setup, period):
    _, hod = setup
    client.force_login(hod.user)
    page = client.get(reverse("department_duties")).content.decode()
    assert "Me Person" in page and "R1" in page and "Outside Person" not in page
    xlsx = client.get(reverse("department_duties_xlsx", args=[period.pk]))
    assert xlsx.status_code == 200


def test_department_duties_hod_only(client, setup):
    me, _ = setup
    client.force_login(me.user)
    assert client.get(reverse("department_duties")).status_code == 403


def test_department_duties_hide_drafts(client, setup, period):
    _, hod = setup
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.ALLOCATING)
    client.force_login(hod.user)
    main = client.get(reverse("department_duties")).content.decode().split("<main>")[1]  # skip the CSRF token in the header
    assert "R1" not in main
    assert client.get(reverse("department_duties_xlsx", args=[period.pk])).status_code == 404
