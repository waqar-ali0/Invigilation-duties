from datetime import date, datetime, time

import pytest

from duties.models import Duty, DutySource
from exams.importers.departments import import_departments
from exams.importers.reader import read_rows
from exams.importers.schedule import import_schedule
from exams.models import ExamSession, PeriodLockedError, PeriodStatus, RoomAllocation, TimeSlot
from exams.tests.helpers import FACULTY_HEADER, SCHEDULE_HEADER, make_csv, make_xlsx
from faculty.models import Department, Designation, Faculty

pytestmark = pytest.mark.django_db


def good_row(**over):
    row = {
        "date": datetime(2026, 12, 2),
        "start": time(9, 0),
        "end": time(12, 0),
        "code": "CS101",
        "name": "Intro to Computing",
        "dept": "Computer Science",
        "room": "A1",
        "students": 61,
        "teachers": "",
    }
    row.update(over)
    return list(row.values())


# --- reader ---------------------------------------------------------------


def test_headers_are_normalised_and_blank_rows_skipped():
    f = make_xlsx([[" Student  Count ", "Name"], [40, "  Ali  "], [None, None], ["", ""]])
    assert read_rows(f) == [{"student_count": 40, "name": "Ali", "_row": 2}]


def test_csv_with_bom_is_read():
    f = make_csv("Name,Email\nAli,ali@uni.edu\n")
    assert read_rows(f) == [{"name": "Ali", "email": "ali@uni.edu", "_row": 2}]


def test_unknown_extension_rejected():
    from django.core.files.uploadedfile import SimpleUploadedFile

    with pytest.raises(ValueError, match="Upload an .xlsx or .csv file."):
        read_rows(SimpleUploadedFile("x.pdf", b"%PDF"))


# --- departments ------------------------------------------------------------


def test_departments_import_skips_existing_and_flags_duplicates(dept):
    res = import_departments(make_xlsx([["Name"], ["Physics"], ["Computer Science"]]))
    assert res.ok and res.created == 1
    res = import_departments(make_xlsx([["Name"], ["Maths"], ["maths"]]))
    assert res.errors == ["Row 3: duplicate department 'maths'"]
    assert not Department.objects.filter(name="Maths").exists()


# --- faculty ----------------------------------------------------------------










# --- schedule ---------------------------------------------------------------


def test_schedule_import_accepts_excel_date_and_time_cells(period, dept):
    res = import_schedule(period, make_xlsx([SCHEDULE_HEADER, good_row()]))
    assert res.ok and res.created == 1
    slot = TimeSlot.objects.get()
    assert (slot.date, slot.start_time, slot.end_time) == (date(2026, 12, 2), time(9), time(12))


def test_schedule_import_accepts_text_dates_and_times(period, dept):
    rows = [SCHEDULE_HEADER, good_row(date="2026-12-03", start="14:00", end="17:00"), good_row(date="04/12/2026")]
    res = import_schedule(period, make_xlsx(rows))
    assert res.ok, res.errors
    assert sorted(TimeSlot.objects.values_list("date", flat=True)) == [date(2026, 12, 3), date(2026, 12, 4)]


def test_required_invigilators_computed_on_import(period, dept):
    import_schedule(period, make_xlsx([SCHEDULE_HEADER, good_row(students=61)]))
    assert RoomAllocation.objects.get().invigilators_required == 3


def test_rooms_of_same_course_share_session(period, dept, make_faculty):
    t = make_faculty(email="t@uni.edu")
    rows = [SCHEDULE_HEADER, good_row(room="A1", teachers="t@uni.edu"), good_row(room="A2")]
    import_schedule(period, make_xlsx(rows))
    session = ExamSession.objects.get()
    assert session.rooms.count() == 2 and list(session.course_teachers.all()) == [t]


def test_one_bad_row_saves_nothing_and_reports_all_errors(period, dept):
    rows = [
        SCHEDULE_HEADER,
        good_row(),
        good_row(dept="XYZ", room="A2"),
        good_row(date=datetime(2027, 1, 5), room="A3"),
    ]
    res = import_schedule(period, make_xlsx(rows))
    assert not res.ok
    assert res.errors == ["Row 3: unknown department 'XYZ'", "Row 4: date 2027-01-05 is outside the exam period"]
    assert RoomAllocation.objects.count() == 0 and TimeSlot.objects.count() == 0


def test_schedule_row_level_errors(period, dept):
    rows = [
        SCHEDULE_HEADER,
        good_row(start=time(12), end=time(9)),
        good_row(students=-4, room="A2"),
        good_row(teachers="ghost@uni.edu", room="A3"),
        good_row(room="A4"),
        good_row(room="A4"),
        good_row(room="A5", end=time(11)),
        good_row(room="", code=""),
    ]
    res = import_schedule(period, make_xlsx(rows))
    assert res.errors == [
        "Row 2: end time must be after start time",
        "Row 3: student count must be a whole number of 0 or more",
        "Row 4: unknown teacher 'ghost@uni.edu'",
        "Row 6: duplicate of row 5",
        "Row 7: slot at 2026-12-02 09:00 already exists with a different end time",
        "Row 8: course_code is required",
        "Row 8: room is required",
    ]


def test_schedule_import_refused_when_duties_exist(period, dept, room, room_fac):
    Duty.objects.create(faculty=room_fac, room_allocation=room, source=DutySource.AUTO)
    res = import_schedule(period, make_xlsx([SCHEDULE_HEADER, good_row()]))
    assert res.errors == ["This period already has duties. Clear the draft before re-importing the schedule."]


def test_schedule_import_on_published_period_raises(period, dept):
    period.status = PeriodStatus.PUBLISHED
    with pytest.raises(PeriodLockedError):
        import_schedule(period, make_xlsx([SCHEDULE_HEADER, good_row()]))
