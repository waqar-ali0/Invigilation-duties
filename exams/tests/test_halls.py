import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from allocation.services import run_allocation
from duties.models import Duty, DutySource
from duties.services import add_duty
from exams.halls import hall_lead, staff_shared_halls
from exams.importers.schedule import import_schedule
from exams.models import RoomAllocation
from reports.queries import roster_rows


def _hall(make_room):
    """CS101 (40 students) and CS102 (35) both in Hall A on 1 Dec; CS103 alone in B1."""
    a = make_room(day=1, course="CS101", room="Hall A", students=40)
    b = make_room(day=1, course="CS102", room="hall a ", students=35)
    c = make_room(day=1, course="CS103", room="B1", students=20)
    return a, b, c


def test_shared_hall_is_staffed_once_from_total_students(period, make_room):
    a, b, c = _hall(make_room)  # 1 per 30 students, minimum 2
    assert staff_shared_halls(period) == 1
    for r in (a, b, c):
        r.refresh_from_db()
    assert a.invigilators_required == 3  # 75 students together
    assert b.invigilators_required == 0
    assert c.invigilators_required == 2


def test_same_room_in_another_slot_is_not_shared(period, make_room):
    a = make_room(day=1, course="CS101", room="Hall A", students=40)
    b = make_room(day=2, course="CS102", room="Hall A", students=40)
    assert staff_shared_halls(period) == 0
    a.refresh_from_db(), b.refresh_from_db()
    assert a.invigilators_required == b.invigilators_required == 2


def test_import_staffs_shared_halls(period, dept):
    csv = (
        "date,start_time,end_time,course_code,course_name,department,room,student_count\n"
        f"2026-12-01,09:00,12:00,CS101,Intro,{dept.name},Hall A,40\n"
        f"2026-12-01,09:00,12:00,CS102,Data,{dept.name},Hall A,35\n"
    )
    result = import_schedule(period, SimpleUploadedFile("schedule.csv", csv.encode()))
    assert not result.errors
    needed = sorted(RoomAllocation.objects.values_list("invigilators_required", flat=True))
    assert needed == [0, 3]


def test_allocation_puts_one_team_in_a_shared_hall(period, make_room, make_faculty, admin_user):
    a, b, _ = _hall(make_room)
    for i in range(8):
        make_faculty(f"T{i}")
    run_allocation(period, admin_user, seed=1)
    assert Duty.objects.filter(room_allocation=a).count() == 3
    assert Duty.objects.filter(room_allocation=b).count() == 0


def test_allocation_run_fixes_halls_imported_before_and_moves_their_duties(period, make_room, make_faculty, admin_user):
    a, b, _ = _hall(make_room)  # made directly, so both rows still need 2 each
    manual = Duty.objects.create(faculty=make_faculty("Kept"), room_allocation=b, source=DutySource.MANUAL)
    for i in range(8):
        make_faculty(f"T{i}")
    run_allocation(period, admin_user, seed=1)
    manual.refresh_from_db()
    assert manual.room_allocation == a
    assert Duty.objects.filter(room_allocation=a).count() == 3
    assert not Duty.objects.filter(room_allocation=b).exists()


def test_manual_assignment_to_a_shared_row_goes_to_the_hall(period, make_room, make_faculty, admin_user):
    a, b, _ = _hall(make_room)
    staff_shared_halls(period)
    duty = add_duty(make_faculty("Late"), b, admin_user)
    assert duty.room_allocation == a
    assert hall_lead(b) == a


def test_roster_lists_the_hall_team_on_every_course_row(period, make_room, make_faculty, admin_user):
    a, b, _ = _hall(make_room)
    for i in range(8):
        make_faculty(f"T{i}")
    run_allocation(period, admin_user, seed=1)
    rows = {r.course_code: r for r in roster_rows(period)}
    assert len(rows["CS101"].invigilators) == 3
    assert rows["CS102"].invigilators == rows["CS101"].invigilators


@pytest.mark.parametrize("url_room", ["lead", "other"])
def test_room_page_explains_the_shared_hall(admin_client, period, make_room, url_room):
    a, b, _ = _hall(make_room)
    staff_shared_halls(period)
    room = a if url_room == "lead" else b
    html = admin_client.get(f"/periods/{period.pk}/rooms/{room.pk}/").content.decode()
    main = html[html.index("<main"):]
    assert "Shared hall" in main and "CS101" in main and "CS102" in main
    assert "75 students" in main


def test_dashboard_marks_shared_rows(admin_client, period, make_room, make_faculty, admin_user):
    _hall(make_room)
    for i in range(8):
        make_faculty(f"T{i}")
    run_allocation(period, admin_user, seed=1)
    html = admin_client.get(f"/periods/{period.pk}/").content.decode()
    main = html[html.index("<main"):]
    assert "shared hall" in main.lower()
    assert "0/0" not in main
