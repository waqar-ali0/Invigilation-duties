import pytest
from django.urls import reverse

from exams.tests.helpers import FACULTY_HEADER, SCHEDULE_HEADER, make_xlsx
from faculty.models import Department, Faculty

pytestmark = pytest.mark.django_db


def test_home_redirects_to_period_list(admin_client):
    r = admin_client.get("/")
    assert r.status_code == 302 and r.url == reverse("period_list")


def test_anonymous_redirected_to_login(client, period):
    r = client.get(reverse("period_list"))
    assert r.status_code == 302 and r.url.startswith(reverse("login"))


def test_non_admin_gets_403(client, faculty_user, period):
    client.force_login(faculty_user)
    assert client.get(reverse("period_dashboard", args=[period.pk])).status_code == 403


def test_login_page_renders(client):
    r = client.get(reverse("login"))
    assert r.status_code == 200 and b"Email" in r.content


def test_period_list_shows_periods(admin_client, period):
    r = admin_client.get(reverse("period_list"))
    assert r.status_code == 200 and "Fall 2026 Finals" in r.content.decode()


def test_dashboard_shows_capacity_warning(admin_client, period, make_room, make_faculty):
    make_room(needed=5)
    make_faculty()
    page = admin_client.get(reverse("period_dashboard", args=[period.pk])).content.decode()
    assert "Need 5 duties, only 8 possible. Raise the cap or add faculty." not in page
    make_room(day=2, needed=10)
    page = admin_client.get(reverse("period_dashboard", args=[period.pk])).content.decode()
    assert "Need 15 duties, only 8 possible. Raise the cap or add faculty." in page


def test_dashboard_shows_rooms_and_fairness(admin_client, small_period, admin_user):
    from allocation.services import run_allocation

    run_allocation(small_period, admin_user, seed=1)
    page = admin_client.get(reverse("period_dashboard", args=[small_period.pk])).content.decode()
    assert "CS101" in page and "B1" in page
    assert "Min 0" in page and "Max 1" in page


def test_schedule_import_shows_row_errors(admin_client, period, dept):
    bad = make_xlsx([SCHEDULE_HEADER, ["2026-12-02", "09:00", "12:00", "CS1", "X", "Nope", "A1", 30, ""]])
    r = admin_client.post(reverse("schedule_import", args=[period.pk]), {"file": bad})
    assert r.status_code == 200 and "Row 2: unknown department &#x27;Nope&#x27;" in r.content.decode()


def test_schedule_import_success(admin_client, period, dept):
    good = make_xlsx([SCHEDULE_HEADER, ["2026-12-02", "09:00", "12:00", "CS1", "X", dept.name, "A1", 30, ""]])
    r = admin_client.post(reverse("schedule_import", args=[period.pk]), {"file": good})
    assert "Imported 1 new" in r.content.decode()


def test_faculty_and_department_import(admin_client):
    admin_client.post(reverse("department_import"), {"file": make_xlsx([["Name"], ["Physics"]])})
    assert Department.objects.filter(name="Physics").exists()
    f = make_xlsx([FACULTY_HEADER, ["E1", "Ali", "ali@uni.edu", "Physics", "Lecturer"]])
    admin_client.post(reverse("faculty_import"), {"file": f})
    assert Faculty.objects.filter(email="ali@uni.edu").exists()


def test_import_rejects_wrong_file_type(admin_client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    r = admin_client.post(reverse("department_import"), {"file": SimpleUploadedFile("a.txt", b"x")})
    assert "Upload an .xlsx or .csv file." in r.content.decode()


def test_schedule_import_on_published_period_shows_locked(admin_client, published_period):
    r = admin_client.post(
        reverse("schedule_import", args=[published_period.pk]),
        {"file": make_xlsx([SCHEDULE_HEADER])},
        follow=True,
    )
    assert "This exam period is published" in r.content.decode()
