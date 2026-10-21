import pytest
from django.urls import reverse

from accounts.models import Role, User
from audit.models import AuditLog
from duties.attendance import AttendanceError, mark_attendance, record_stand_in
from duties.models import Duty, DutySource, DutyStatus
from duties.tests.conftest import move_slot_to_past
from exams.models import RoomAllocation
from reports.operations import faculty_records, handover_history

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(db):
    return User.objects.create_user("staff@uni.edu", "x", role=Role.EXAM_STAFF)


@pytest.fixture
def absent(published_duty, staff):
    move_slot_to_past(published_duty)
    return mark_attendance(published_duty, staff, present=False)


def test_stand_in_recorded_as_completed_and_absence_kept(absent, staff, make_faculty):
    helper = make_faculty("Helper")
    new = record_stand_in(absent, helper, staff, "Covered from 09:15")
    absent.refresh_from_db()
    assert absent.status == DutyStatus.ABSENT
    assert (new.faculty, new.status, new.source, new.replaces, new.attendance_marked_by) == (
        helper,
        DutyStatus.COMPLETED,
        DutySource.STAND_IN,
        absent,
        staff,
    )
    assert AuditLog.objects.get(action="duty.stand_in").after["reason"] == "Covered from 09:15"


def test_stand_in_only_for_absent_duties(published_duty, staff, make_faculty):
    move_slot_to_past(published_duty)
    with pytest.raises(AttendanceError, match="Only a duty marked absent can have a stand-in."):
        record_stand_in(published_duty, make_faculty(), staff, "x")


def test_stand_in_needs_reason_and_free_person(absent, staff, make_faculty):
    with pytest.raises(AttendanceError, match="A reason is required."):
        record_stand_in(absent, make_faculty(), staff, " ")
    busy = make_faculty("Busy")
    same_slot_room = RoomAllocation.objects.create(
        session=absent.room_allocation.session, room="A2", student_count=10, invigilators_required=2
    )
    Duty.objects.create(faculty=busy, room_allocation=same_slot_room, source=DutySource.AUTO)
    with pytest.raises(AttendanceError, match="Busy is already invigilating at this time."):
        record_stand_in(absent, busy, staff, "x")


def test_only_one_stand_in(absent, staff, make_faculty):
    record_stand_in(absent, make_faculty(), staff, "x")
    with pytest.raises(AttendanceError, match="A stand-in is already recorded for this duty."):
        record_stand_in(absent, make_faculty(), staff, "y")


def test_caps_do_not_block_a_stand_in(absent, staff, make_faculty):
    helper = make_faculty("Exempt Helper", is_exempt=True)
    assert record_stand_in(absent, helper, staff, "x").faculty == helper


def test_reports_count_stand_in(absent, staff, make_faculty, period):
    record_stand_in(absent, make_faculty("Helper"), staff, "Covered")
    records = {r.faculty.name: r for r in faculty_records(period)}
    assert (records["Owner Person"].absent, records["Helper"].completed, records["Helper"].taken_over) == (1, 1, 1)
    [row] = handover_history(period)
    assert (row["type"], row["note"]) == ("Stand-in", "Covered")


def test_stand_in_view(client, absent, staff, make_faculty):
    helper = make_faculty("Helper")
    client.force_login(staff)
    slot_id = absent.room_allocation.session.slot_id
    assert reverse("record_stand_in", args=[absent.pk]) in client.get(reverse("attendance_slot", args=[slot_id])).content.decode()
    assert "Helper" in client.get(reverse("record_stand_in", args=[absent.pk])).content.decode()
    r = client.post(reverse("record_stand_in", args=[absent.pk]), {"faculty_id": helper.pk, "reason": "Covered"})
    assert r.status_code == 302 and Duty.objects.filter(faculty=helper, status=DutyStatus.COMPLETED).exists()


def test_stand_in_view_role(client, absent):
    client.force_login(absent.faculty.user)
    assert client.get(reverse("record_stand_in", args=[absent.pk])).status_code == 403
