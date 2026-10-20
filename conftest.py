"""Shared pytest fixtures for all apps."""

import pytest


@pytest.fixture
def dept(db):
    from faculty.models import Department

    return Department.objects.create(name="Computer Science")


@pytest.fixture
def make_faculty(db, dept):
    """Factory: make_faculty(name="A", **overrides) -> Faculty."""
    from faculty.models import Designation, Faculty

    counter = {"n": 0}

    def make(name=None, **extra):
        counter["n"] += 1
        n = counter["n"]
        defaults = {
            "employee_id": f"E{n:04d}",
            "name": name or f"Faculty {n:04d}",
            "email": f"f{n}@uni.edu",
            "department": dept,
            "designation": Designation.LECTURER,
        }
        defaults.update(extra)
        return Faculty.objects.create(**defaults)

    return make


@pytest.fixture
def period(db):
    from datetime import date

    from exams.models import ExamPeriod

    return ExamPeriod.objects.create(name="Fall 2026 Finals", start_date=date(2026, 12, 1), end_date=date(2026, 12, 15))


@pytest.fixture
def make_room(db, period, dept):
    """Factory: make_room(day=1, start="09:00", end="12:00", course="CS101", room="A1", students=40) -> RoomAllocation.

    `day` is the day of December 2026. Slots and sessions are reused when they already exist.
    """
    from datetime import date, time

    from exams.models import ExamSession, RoomAllocation, TimeSlot, required_invigilators

    def make(day=1, start="09:00", end="12:00", course="CS101", room="A1", students=40, needed=None, p=None):
        p = p or period
        h1, m1 = map(int, start.split(":"))
        h2, m2 = map(int, end.split(":"))
        slot, _ = TimeSlot.objects.get_or_create(
            period=p, date=date(2026, 12, day), start_time=time(h1, m1), defaults={"end_time": time(h2, m2)}
        )
        session, _ = ExamSession.objects.get_or_create(
            slot=slot, course_code=course, defaults={"course_name": f"Course {course}", "department": dept}
        )
        return RoomAllocation.objects.create(
            session=session,
            room=room,
            student_count=students,
            invigilators_required=needed if needed is not None else required_invigilators(students, p.settings),
        )

    return make
