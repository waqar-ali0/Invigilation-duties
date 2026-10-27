from datetime import date, time

import pytest
from django.core.exceptions import ValidationError

from exams.models import (
    DesignationCap,
    PeriodLockedError,
    PeriodStatus,
    TimeSlot,
    required_invigilators,
)
from faculty.models import Designation, FacultyCategory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("students,expected", [(0, 2), (30, 2), (61, 3), (90, 3), (91, 4)])
def test_required_invigilators(period, students, expected):
    assert required_invigilators(students, period.settings) == expected


def test_new_period_gets_default_settings(period):
    s = period.settings
    assert (
        s.max_duties_per_faculty,
        s.max_duties_per_day,
        s.students_per_invigilator,
        s.min_invigilators_per_room,
        s.avoid_back_to_back,
        s.exclude_course_teacher,
    ) == (8, 2, 30, 2, True, True)


def test_cap_for_uses_designation_override(period):
    DesignationCap.objects.create(period=period, category=FacultyCategory.get(Designation.PROFESSOR), max_duties=4)
    assert period.cap_for(Designation.PROFESSOR) == 4
    assert period.cap_for(Designation.LECTURER) == 8


def test_published_period_is_locked(period):
    period.status = PeriodStatus.PUBLISHED
    with pytest.raises(PeriodLockedError, match="published"):
        period.ensure_editable()


def test_closed_period_is_locked(period):
    period.status = PeriodStatus.CLOSED
    with pytest.raises(PeriodLockedError, match="closed"):
        period.ensure_editable()


def test_draft_period_is_editable(period):
    period.ensure_editable()


def test_slot_outside_period_rejected(period):
    slot = TimeSlot(period=period, date=date(2027, 1, 5), start_time=time(9), end_time=time(12))
    with pytest.raises(ValidationError):
        slot.full_clean()


def test_slot_end_before_start_rejected(period):
    slot = TimeSlot(period=period, date=date(2026, 12, 2), start_time=time(12), end_time=time(9))
    with pytest.raises(ValidationError):
        slot.full_clean()
