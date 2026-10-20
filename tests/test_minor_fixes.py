"""Regression tests for the minor findings deferred from the Phase 1 review."""

import io

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from openpyxl import load_workbook

from allocation.engine import FairnessStats
from allocation.services import clear_draft, run_allocation
from duties.models import Duty, DutySource, Unavailability
from exams.importers.schedule import import_schedule
from exams.models import DesignationCap, TimeSlot
from exams.tests.helpers import make_csv
from faculty.models import Designation
from reports.excel import load_xlsx, roster_xlsx
from reports.queries import faculty_load

pytestmark = pytest.mark.django_db


# --- 1. Excel-style CSV dates and times -------------------------------------------


def test_schedule_accepts_excel_csv_dates_and_times(period, dept):
    csv = (
        "date,start_time,end_time,course_code,course_name,department,room,student_count\n"
        "2026-12-01 00:00:00,9:00 AM,12:00 PM,CS101,Intro,Computer Science,A1,30\n"
    )
    res = import_schedule(period, make_csv(csv))
    assert res.ok, res.errors
    slot = TimeSlot.objects.get()
    assert (slot.date.isoformat(), f"{slot.start_time:%H:%M}", f"{slot.end_time:%H:%M}") == ("2026-12-01", "09:00", "12:00")


# --- 2. Seed 0 is a real seed -------------------------------------------------------


def test_run_allocation_keeps_seed_zero(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=0)
    small_period.refresh_from_db()
    assert small_period.allocation_seed == 0 and small_period.last_run["seed"] == 0


# --- 3. Non-numeric faculty id is a 404 ----------------------------------------------


def test_add_duty_with_non_numeric_faculty_id_is_404(admin_client, room):
    url = reverse("add_duty", args=[room.session.slot.period_id, room.pk])
    assert admin_client.post(url, {"faculty_id": "abc"}).status_code == 404


# --- 4. Clearing the draft forgets the last run ------------------------------------


def test_clear_draft_resets_last_run(small_period, admin_user):
    run_allocation(small_period, admin_user, seed=1)
    clear_draft(small_period, admin_user)
    assert small_period.last_run is None
    small_period.refresh_from_db()
    assert small_period.last_run is None


# --- 5. Load report includes now-ineligible faculty who still have duties ----------


def test_faculty_load_includes_exempt_faculty_with_duties(room, make_faculty):
    busy = make_faculty("Busy")
    Duty.objects.create(faculty=busy, room_allocation=room, source=DutySource.MANUAL)
    make_faculty("Idle Exempt", is_exempt=True)
    busy.is_exempt = True
    busy.save()
    assert [(f.name, n) for f, n in faculty_load(room.session.slot.period)] == [("Busy", 1)]


# --- 6. Dashboard fairness ignores designation caps ---------------------------------


def test_dashboard_fairness_split_by_cap(admin_client, period, make_room, make_faculty):
    DesignationCap.objects.create(period=period, designation=Designation.PROFESSOR, max_duties=1)
    rooms = [make_room(day=d, course=f"C{d}") for d in range(1, 4)]
    for name in ("L1", "L2"):
        f = make_faculty(name)
        for r in rooms[:2]:
            Duty.objects.create(faculty=f, room_allocation=r, source=DutySource.MANUAL)
    prof = make_faculty("P1", designation=Designation.PROFESSOR)
    Duty.objects.create(faculty=prof, room_allocation=rooms[2], source=DutySource.MANUAL)
    r = admin_client.get(reverse("period_dashboard", args=[period.pk]))
    assert r.context["stats"] == FairnessStats(2, 2, 2.0)
    assert r.context["capped_stats"] == [("Professor", 1, FairnessStats(1, 1, 1.0))]
    assert "Professor (cap 1)" in r.content.decode()


# --- 7. Spreadsheet formula injection ------------------------------------------------


def test_excel_exports_escape_formula_like_text(room, make_faculty):
    f = make_faculty("=HYPERLINK(\"http://evil\")", employee_id="+E1")
    Duty.objects.create(faculty=f, room_allocation=room, source=DutySource.MANUAL)
    room.session.course_name = "@SUM(A1)"
    room.session.save()
    period = room.session.slot.period
    load = [list(r) for r in load_workbook(io.BytesIO(load_xlsx(period))).active.iter_rows(values_only=True)]
    assert load[1][:2] == ["'+E1", "'=HYPERLINK(\"http://evil\")"]
    roster = [list(r) for r in load_workbook(io.BytesIO(roster_xlsx(period))).active.iter_rows(values_only=True)]
    assert roster[1][4] == "'@SUM(A1)" and roster[1][8] == "'=HYPERLINK(\"http://evil\")"


# --- 8. Unavailability slot must match its period and date --------------------------


def test_unavailability_slot_must_match_period_and_date(make_room, make_faculty, period):
    slot = make_room(day=2).session.slot
    f = make_faculty()
    other = type(period).objects.create(name="Other", start_date=period.start_date, end_date=period.end_date)
    with pytest.raises(ValidationError) as e:
        Unavailability(faculty=f, period=other, date=slot.date, slot=slot, reason="x").full_clean()
    assert "slot" in e.value.message_dict
    with pytest.raises(ValidationError):
        Unavailability(faculty=f, period=period, date=period.start_date, slot=slot, reason="x").full_clean()
    Unavailability(faculty=f, period=period, date=slot.date, slot=slot, reason="x").full_clean()
