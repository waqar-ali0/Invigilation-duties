import pytest

from audit.models import AuditLog
from duties.models import Duty, DutySource
from duties.services import (
    AssignmentBlocked,
    AssignmentNeedsConfirmation,
    add_duty,
    check_assignment,
    eligible_candidates,
    remove_duty,
)
from exams.models import PeriodLockedError, PeriodStatus

pytestmark = pytest.mark.django_db


def test_add_blocked_when_already_in_slot(make_room, make_faculty, admin_user):
    a1 = make_room(course="CS101", room="A1")
    a2 = make_room(course="CS102", room="A2")
    fac = make_faculty()
    add_duty(fac, a1, admin_user)
    with pytest.raises(AssignmentBlocked) as e:
        add_duty(fac, a2, admin_user)
    assert e.value.errors == ["Already assigned in this time slot"]


def test_add_blocked_for_exempt(room, make_faculty, admin_user):
    fac = make_faculty(is_exempt=True)
    with pytest.raises(AssignmentBlocked) as e:
        add_duty(fac, room, admin_user)
    assert e.value.errors == ["Faculty is inactive or exempt"]


def test_add_needs_confirmation_for_course_teacher(room, make_faculty, admin_user):
    fac = make_faculty()
    room.session.course_teachers.add(fac)
    with pytest.raises(AssignmentNeedsConfirmation) as e:
        add_duty(fac, room, admin_user)
    assert e.value.warnings == ["Teaches this course"]
    assert not Duty.objects.exists()
    add_duty(fac, room, admin_user, allow_warnings=True)
    assert Duty.objects.filter(faculty=fac).exists()


def test_full_room_is_a_warning(make_room, make_faculty, admin_user):
    room = make_room(needed=1)
    add_duty(make_faculty(), room, admin_user)
    assert check_assignment(make_faculty(), room) == ([], ["Room already has its required invigilators"])


def test_add_creates_manual_duty_and_audits(room, make_faculty, admin_user):
    fac = make_faculty()
    duty = add_duty(fac, room, admin_user)
    assert duty.source == DutySource.MANUAL
    entry = AuditLog.objects.get(action="duty.assign")
    assert entry.after == {"faculty": fac.id, "room": room.id, "warnings": []}


def test_add_on_published_period_raises_locked(room, make_faculty, admin_user, period):
    period.status = PeriodStatus.PUBLISHED
    period.save()
    room.refresh_from_db()
    with pytest.raises(PeriodLockedError):
        add_duty(make_faculty(), room, admin_user)


def test_remove_deletes_and_audits(room, make_faculty, admin_user):
    duty = add_duty(make_faculty(), room, admin_user)
    remove_duty(duty, admin_user)
    assert not Duty.objects.exists()
    assert AuditLog.objects.get(action="duty.remove").before["room"] == room.id


def test_eligible_candidates_sorted_and_excludes_hard_blocked(make_room, make_faculty, admin_user):
    a1 = make_room(course="CS101", room="A1")
    a2 = make_room(course="CS102", room="A2")
    busy = make_faculty("Busy")
    teacher = make_faculty("Teacher")
    free = make_faculty("Free")
    make_faculty("Exempt", is_exempt=True)
    add_duty(busy, a1, admin_user)
    a2.session.course_teachers.add(teacher)
    result = eligible_candidates(a2)
    assert [(f.name, count, warnings) for f, count, warnings in result] == [
        ("Free", 0, []),
        ("Teacher", 0, ["Teaches this course"]),
    ]
    assert free in [f for f, _, _ in result]
