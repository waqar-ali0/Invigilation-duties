"""Regression tests for the findings of the final whole-branch review."""

from datetime import date, time

import pytest
from django.contrib.admin.sites import site
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from allocation.engine import EngineInput, Person, Room, Rules, Slot, allocate
from allocation.services import clear_draft, publish_period, run_allocation
from duties.models import Duty
from duties.services import AssignmentBlocked, add_duty
from exams.importers.reader import read_rows
from exams.importers.schedule import import_schedule
from exams.models import ExamPeriod, PeriodLockedError, PeriodStatus, RoomAllocation, TimeSlot
from exams.tests.helpers import FACULTY_HEADER, SCHEDULE_HEADER, make_xlsx
from exams.importers.faculty import import_faculty

pytestmark = pytest.mark.django_db

RULES = Rules(max_per_day=2, avoid_back_to_back=False, exclude_course_teacher=False)


def schedule_row(room="A1", code="CS101", start="09:00", end="12:00"):
    return ["2026-12-02", start, end, code, "Intro", "Computer Science", room, 30, ""]


# --- 1. Exam office can enter unavailability ------------------------------------


def test_admin_role_can_add_unavailability(admin_user):
    assert admin_user.has_perm("duties.add_unavailability")
    assert admin_user.has_perm("duties.change_unavailability")
    assert not admin_user.has_perm("duties.change_duty")


def test_admin_can_open_unavailability_add_form(admin_client):
    assert admin_client.get(reverse("admin:duties_unavailability_add")).status_code == 200


# --- 2. Re-import after clearing replaces the schedule -------------------------


def test_reimport_same_file_replaces_schedule(period, dept):
    f = [SCHEDULE_HEADER, schedule_row()]
    assert import_schedule(period, make_xlsx(f)).ok
    res = import_schedule(period, make_xlsx(f))
    assert res.ok and RoomAllocation.objects.count() == 1


def test_reimport_corrected_file_drops_old_rooms(period, dept, admin_user):
    import_schedule(period, make_xlsx([SCHEDULE_HEADER, schedule_row(room="A1")]))
    run_allocation(period, admin_user, seed=1)
    clear_draft(period, admin_user)
    period.refresh_from_db()
    assert import_schedule(period, make_xlsx([SCHEDULE_HEADER, schedule_row(room="B2")])).ok
    assert list(RoomAllocation.objects.values_list("room", flat=True)) == ["B2"]


# --- 3. Overlapping slots are clashes -----------------------------------------


def test_engine_never_assigns_overlapping_slots():
    d = date(2026, 12, 1)
    slots = [Slot(1, d, 0, time(9), time(12)), Slot(2, d, 1, time(10), time(13))]
    rooms = [Room(10, 1, 1), Room(11, 2, 1)]
    res = allocate(EngineInput(slots, rooms, [Person(1, 1, 8)], RULES), seed=1)
    assert len(res.assignments) == 1
    assert res.unfilled[0].reason == "All faculty are already assigned in this time slot"


def test_manual_add_blocked_for_overlapping_slot(make_room, make_faculty, admin_user):
    a = make_room(start="09:00", end="12:00", course="CS101")
    b = make_room(start="10:00", end="13:00", course="CS102")
    fac = make_faculty()
    add_duty(fac, a, admin_user)
    with pytest.raises(AssignmentBlocked) as e:
        add_duty(fac, b, admin_user)
    assert e.value.errors == ["Already assigned in an overlapping time slot"]


# --- 4. Status is re-checked on the locked database row --------------------------


def _publish_behind_back(period):
    ExamPeriod.objects.filter(pk=period.pk).update(status=PeriodStatus.PUBLISHED)


def test_run_allocation_rechecks_status_in_database(small_period, admin_user):
    _publish_behind_back(small_period)  # the in-memory object still says DRAFT
    with pytest.raises(PeriodLockedError):
        run_allocation(small_period, admin_user)
    small_period.refresh_from_db()
    assert small_period.status == PeriodStatus.PUBLISHED and not Duty.objects.exists()


def test_publish_and_clear_recheck_status_in_database(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    _publish_behind_back(small_period)
    with pytest.raises(PeriodLockedError):
        clear_draft(small_period, admin_user)
    with pytest.raises(PeriodLockedError):
        publish_period(small_period, admin_user)
    assert Duty.objects.count() == 6


def test_import_rechecks_status_in_database(period, dept):
    _publish_behind_back(period)
    with pytest.raises(PeriodLockedError):
        import_schedule(period, make_xlsx([SCHEDULE_HEADER, schedule_row()]))


# --- 5. Admin panel can't change a published period's schedule or rules -------------


def test_admin_cannot_change_published_schedule(rf, admin_user, published_period):
    request = rf.get("/")
    request.user = admin_user
    room = RoomAllocation.objects.first()
    slot = TimeSlot.objects.first()
    for model, obj in ((RoomAllocation, room), (TimeSlot, slot), (ExamPeriod, published_period)):
        model_admin = site._registry[model]
        assert not model_admin.has_change_permission(request, obj)
        assert not model_admin.has_delete_permission(request, obj)


def test_admin_can_change_draft_schedule(rf, admin_user, room):
    request = rf.get("/")
    request.user = admin_user
    assert site._registry[RoomAllocation].has_change_permission(request, room)


def test_admin_cannot_add_room_to_published_period(admin_client, published_period):
    session = RoomAllocation.objects.first().session
    r = admin_client.post(
        reverse("admin:exams_roomallocation_add"),
        {"session": session.pk, "room": "NEW", "student_count": 10, "invigilators_required": 2},
    )
    assert r.status_code == 403 and not RoomAllocation.objects.filter(room="NEW").exists()


def test_admin_post_to_published_room_change_is_refused(admin_client, published_period):
    room = RoomAllocation.objects.first()
    url = reverse("admin:exams_roomallocation_change", args=[room.pk])
    admin_client.post(url, {"session": room.session_id, "room": room.room, "student_count": 1, "invigilators_required": 9})
    room.refresh_from_db()
    assert room.invigilators_required != 9


# --- 6. Unreadable uploads are a clear message, not a crash ----------------------


@pytest.mark.parametrize("name,content", [("bad.xlsx", b"not a zip file"), ("bad.csv", b"\xff\xfe\x00bad")])
def test_unreadable_file_raises_value_error(name, content):
    with pytest.raises(ValueError, match="Could not read the file"):
        read_rows(SimpleUploadedFile(name, content))


def test_unreadable_file_view_shows_message(admin_client):
    r = admin_client.post(reverse("faculty_import"), {"file": SimpleUploadedFile("bad.xlsx", b"junk")})
    assert r.status_code == 200 and "Could not read the file" in r.content.decode()


# --- 7. Over-long text is a row error ----------------------------------------------


def test_schedule_rejects_over_long_values(period, dept):
    res = import_schedule(period, make_xlsx([SCHEDULE_HEADER, schedule_row(room="R" * 51, code="C" * 31)]))
    assert res.errors == [
        "Row 2: course_code is longer than 30 characters",
        "Row 2: room is longer than 50 characters",
    ]


def test_faculty_rejects_over_long_values(dept):
    res = import_faculty(make_xlsx([FACULTY_HEADER, ["E" * 51, "Ali", "a@uni.edu", "Computer Science", "Lecturer"]]))
    assert res.errors == ["Row 2: employee_id is longer than 50 characters"]
