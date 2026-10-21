from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import Role, User
from audit.models import AuditLog
from duties.attendance import AttendanceError, can_change_mark, mark_attendance, mark_room_present
from duties.models import Duty, DutySource, DutyStatus
from duties.tests.conftest import move_slot_to_past

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(db):
    return User.objects.create_user("staff@uni.edu", "x", role=Role.EXAM_STAFF)


@pytest.fixture
def started(published_duty):
    move_slot_to_past(published_duty)
    return published_duty


def test_not_before_exam_starts(published_duty, staff):
    with pytest.raises(AttendanceError, match="Attendance can be marked once the exam has started."):
        mark_attendance(published_duty, staff, present=True)


def test_mark_present_and_absent_audited(started, staff):
    duty = mark_attendance(started, staff, present=False)
    assert (duty.status, duty.attendance_marked_by) == (DutyStatus.ABSENT, staff)
    entry = AuditLog.objects.get(action="duty.attendance")
    assert (entry.before, entry.after) == ({"status": "assigned"}, {"status": "absent"})
    mark_attendance(started, staff, present=False)  # unchanged: no new audit entry
    assert AuditLog.objects.filter(action="duty.attendance").count() == 1


def test_staff_same_day_change_allowed_next_day_refused(started, staff):
    mark_attendance(started, staff, present=True)
    mark_attendance(started, staff, present=False)
    Duty.objects.filter(pk=started.pk).update(attendance_marked_at=timezone.now() - timedelta(days=1))
    started.refresh_from_db()
    assert not can_change_mark(staff, started)
    with pytest.raises(AttendanceError, match="Only the exam office can change this mark now."):
        mark_attendance(started, staff, present=True)


def test_other_staff_cannot_change_but_admin_can(started, staff, admin_user):
    mark_attendance(started, staff, present=True)
    other = User.objects.create_user("staff2@uni.edu", "x", role=Role.EXAM_STAFF)
    started.refresh_from_db()
    assert not can_change_mark(other, started)
    assert mark_attendance(started, admin_user, present=False).status == DutyStatus.ABSENT


def test_cancelled_duty_refused(started, staff):
    Duty.objects.filter(pk=started.pk).update(status=DutyStatus.CANCELLED)
    with pytest.raises(AttendanceError, match="This duty was handed over to someone else."):
        mark_attendance(started, staff, present=True)


def test_mark_room_present_skips_marked(started, staff, make_faculty):
    room = started.room_allocation
    other = Duty.objects.create(faculty=make_faculty(), room_allocation=room, source=DutySource.AUTO)
    mark_attendance(other, staff, present=False)
    assert mark_room_present(room, staff) == 1
    started.refresh_from_db()
    other.refresh_from_db()
    assert (started.status, other.status) == (DutyStatus.COMPLETED, DutyStatus.ABSENT)
