import pytest
from django.urls import reverse

from accounts.models import Role
from duties.models import Duty, DutySource
from faculty.models import Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def data(period, make_room, make_faculty):
    ali = make_faculty("Ali Raza", employee_id="EMP-777", email="ali.raza@uni.edu")
    make_faculty("Sara Khan", employee_id="EMP-888")
    room = make_room(day=2, course="PHY301", room="Hall-B")
    room.session.course_name = "Quantum Mechanics"
    room.session.save()
    Duty.objects.create(faculty=ali, room_allocation=room, source=DutySource.AUTO)
    return ali, room


def results(client, q):
    return client.get(reverse("search"), {"q": q}).context


def test_search_by_name_id_and_email(admin_client, data):
    for q in ("ali", "emp-777", "ali.raza@"):
        ctx = results(admin_client, q)
        assert [f.name for f in ctx["faculty"]] == ["Ali Raza"], q


def test_faculty_results_list_their_duties(admin_client, data):
    page = admin_client.get(reverse("search"), {"q": "Ali"}).content.decode()
    assert "PHY301" in page and "Hall-B" in page


def test_search_exams_by_course_and_room(admin_client, data):
    _, room = data
    for q in ("phy301", "quantum", "hall-b"):
        assert [r.pk for r in results(admin_client, q)["rooms"]] == [room.pk], q


def test_short_or_empty_query_returns_nothing(admin_client, data):
    ctx = results(admin_client, "")
    assert ctx["faculty"] == [] and ctx["rooms"] == []
    assert results(admin_client, "a")["faculty"] == []


def test_search_admin_only(client, faculty_user):
    client.force_login(faculty_user)
    assert client.get(reverse("search"), {"q": "ali"}).status_code == 403


def test_header_has_search_for_admin(admin_client):
    assert 'name="q"' in admin_client.get(reverse("period_list")).content.decode()


def test_hod_department_filter(client, period, dept, make_member, make_room):
    from exams.models import ExamPeriod, PeriodStatus

    hod = make_member("Head", role=Role.HOD)
    dept.hod = hod
    dept.save()
    hod.user.refresh_from_db()
    make_member("Zainab Shah", employee_id="Z-1")
    make_member("Omar Butt", employee_id="O-2")
    Department.objects.create(name="Physics")
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)
    client.force_login(hod.user)
    rows = client.get(reverse("department_duties"), {"q": "z-1"}).context["rows"]
    assert [r["faculty"].name for r in rows] == ["Zainab Shah"]
    rows = client.get(reverse("department_duties"), {"q": "omar"}).context["rows"]
    assert [r["faculty"].name for r in rows] == ["Omar Butt"]
