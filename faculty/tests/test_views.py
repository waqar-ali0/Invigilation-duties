import pytest
from django.urls import reverse

from exams.models import Building, Room
from faculty.models import Faculty, FacultyCategory
from faculty.phones import normalize_phone

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0300-1234567", "+923001234567"),
        ("3001234567", "+923001234567"),
        (3001234567.0, "+923001234567"),
        ("+92 300 1234567", "+923001234567"),
        ("0092 300 1234567", "+923001234567"),
        ("+44 20 7946 0958", "+442079460958"),
        ("12ab", None),
        ("0300-12345", None),
        ("", None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_faculty_list_pages_filters_and_flags_phones(admin_client, make_faculty):
    for i in range(60):
        make_faculty(f"Person {i:02d}", phone=f"0300-12345{i:02d}")
    make_faculty("No Phone")
    make_faculty("Bad Phone", phone="123")
    page = admin_client.get(reverse("faculty_list")).content.decode()
    assert "Page 1 of 2" in page and "2 with a missing or invalid phone number" in page
    flagged = admin_client.get(reverse("faculty_list"), {"contact": "invalid"}).content.decode()
    assert "Bad Phone" in flagged and "Person 01" not in flagged
    found = admin_client.get(reverse("faculty_list"), {"q": "+92300123450"}).content.decode()
    assert "Person 01" in found


def test_create_without_id_generates_one_and_edit_keeps_it(admin_client, dept):
    lecturer = FacultyCategory.get("lecturer")
    r = admin_client.post(reverse("faculty_create"), {
        "name": "New Person", "phone": "0300-7777777", "department": dept.pk, "category": lecturer.pk,
        "employment_status": "permanent", "is_active": "on",
    })
    f = Faculty.objects.get(name="New Person")
    assert r.status_code == 302 and f.employee_id == "FAC-0001" and f.email is None
    admin_client.post(reverse("faculty_edit", args=[f.pk]), {
        "employee_id": "HACKED", "name": "Renamed", "phone": "0300-7777777", "department": dept.pk,
        "category": lecturer.pk, "employment_status": "permanent", "is_active": "on",
    })
    f.refresh_from_db()
    assert f.name == "Renamed" and f.employee_id == "FAC-0001"


def test_profile_shows_history(admin_client, published_period):
    duty = published_period.duties.first()
    page = admin_client.get(reverse("faculty_detail", args=[duty.faculty_id])).content.decode()
    assert published_period.name in page and duty.room_allocation.session.course_code in page


def test_designations_can_be_added_renamed_and_retired(admin_client, make_faculty):
    admin_client.post(reverse("faculty_categories"), {"name": "Research Associate", "is_active": "on", "order": 9})
    ra = FacultyCategory.objects.get(name="Research Associate")
    assert ra.code == "research_associate"
    admin_client.post(reverse("faculty_categories") + f"?edit={ra.pk}", {"name": "Research Fellow", "order": 9})
    ra.refresh_from_db()
    assert ra.name == "Research Fellow" and not ra.is_active
    f = make_faculty(designation="lecturer")
    f.category = ra
    with pytest.raises(Exception):
        f.full_clean()  # a retired designation can't be given to someone new to it


def test_rooms_page_add_building_room_and_deactivate(admin_client):
    admin_client.post(reverse("room_list"), {"b-name": "Science Block", "b-campus": "City"})
    block = Building.objects.get(name="Science Block")
    admin_client.post(reverse("room_create"), {"building": block.pk, "name": " Lab  1 ", "capacity": 40, "is_active": "on"})
    room = Room.objects.get(building=block)
    assert room.name == "Lab 1"
    r = admin_client.post(reverse("room_create"), {"building": block.pk, "name": "lab 1", "capacity": 40, "is_active": "on"})
    assert "already has a room with this name" in r.content.decode()
    admin_client.post(reverse("room_edit", args=[room.pk]), {"building": block.pk, "name": "Lab 1", "capacity": 40})
    room.refresh_from_db()
    assert not room.is_active
    assert "Lab 1" in admin_client.get(reverse("room_list"), {"show": "inactive"}).content.decode()


def test_pages_are_admin_only(client, faculty_user):
    client.force_login(faculty_user)
    for name in ("faculty_list", "faculty_create", "faculty_categories", "room_list", "room_create", "import_home"):
        assert client.get(reverse(name)).status_code in (302, 403), name
