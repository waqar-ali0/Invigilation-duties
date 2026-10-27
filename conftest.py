"""Shared pytest fixtures for all apps."""

import pytest


@pytest.fixture
def dept(db):
    from faculty.models import Department

    return Department.objects.create(name="Computer Science")


@pytest.fixture
def make_faculty(db, dept):
    """Factory: make_faculty(name="A", **overrides) -> Faculty."""
    from faculty.models import Designation, Faculty, FacultyCategory

    counter = {"n": 0}

    def make(name=None, **extra):
        counter["n"] += 1
        n = counter["n"]
        defaults = {
            "employee_id": f"E{n:04d}",
            "name": name or f"Faculty {n:04d}",
            "email": f"f{n}@uni.edu",
            "department": dept,
        }
        # designation="professor" (a category code) is accepted as a shortcut for category=...
        defaults["category"] = FacultyCategory.get(extra.pop("designation", Designation.LECTURER))
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


@pytest.fixture
def room(make_room):
    return make_room()


@pytest.fixture
def room_fac(make_faculty):
    return make_faculty("Room Faculty")


@pytest.fixture
def admin_user(db):
    from accounts.models import Role, User

    return User.objects.create_user("manager@uni.edu", "pw12345!", role=Role.ADMIN)


@pytest.fixture
def faculty_user(db):
    from accounts.models import User

    return User.objects.create_user("teacher@uni.edu", "pw12345!")


@pytest.fixture
def superuser(db):
    from accounts.models import User

    return User.objects.create_superuser("root@uni.edu", "pw12345!")


@pytest.fixture
def small_period(period, make_room, make_faculty):
    """2 slots, 3 rooms (2 seats each), 8 eligible faculty — plenty of capacity."""
    make_room(day=1, course="CS101", room="A1")
    make_room(day=1, course="CS102", room="A2")
    make_room(day=2, course="CS201", room="B1")
    for i in range(8):
        make_faculty(f"Teacher {i}")
    return period


@pytest.fixture
def short_period(period, make_room, make_faculty):
    """One room needing 5 invigilators but only 2 faculty."""
    make_room(day=1, room="Hall", students=150, needed=5)
    make_faculty("Only One")
    make_faculty("Only Two")
    return period


@pytest.fixture
def published_period(small_period, admin_user):
    from allocation.services import publish_period, run_allocation

    run_allocation(small_period, admin_user, seed=1)
    publish_period(small_period, admin_user)
    small_period.refresh_from_db()
    return small_period


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def make_member(db, make_faculty):
    """Factory: make_member(name=None, role=FACULTY, **faculty_fields) -> Faculty with a linked, active user."""
    from accounts.models import Role, User

    def make(name=None, role=Role.FACULTY, **extra):
        f = make_faculty(name, **extra)
        f.user = User.objects.create_user(f.email.lower(), "Strong-pass-123", role=role)
        f.save()
        return f

    return make
