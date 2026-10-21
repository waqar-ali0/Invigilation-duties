"""Recording who actually invigilated: present (completed) or absent."""

from django.db import transaction
from django.utils import timezone

from accounts.models import Role
from audit.log import log
from exams.models import ExamPeriod

from .models import Duty, DutyStatus


class AttendanceError(Exception):
    pass


def can_change_mark(user, duty, now=None):
    """Admins always; exam staff for unmarked duties, or their own marks made the same day."""
    if user.is_superuser or user.role == Role.ADMIN:
        return True
    if user.role != Role.EXAM_STAFF:
        return False
    if duty.attendance_marked_at is None:
        return True
    now = now or timezone.now()
    return duty.attendance_marked_by_id == user.pk and (
        timezone.localdate(duty.attendance_marked_at) == timezone.localdate(now)
    )


def mark_attendance(duty, actor, present):
    with transaction.atomic():
        ExamPeriod.lock_published(duty.period_id)
        duty = Duty.objects.select_for_update().select_related("room_allocation__session__slot").get(pk=duty.pk)
        if duty.status == DutyStatus.CANCELLED:
            raise AttendanceError("This duty was handed over to someone else.")
        if timezone.now() < duty.room_allocation.session.slot.starts_at:
            raise AttendanceError("Attendance can be marked once the exam has started.")
        new_status = DutyStatus.COMPLETED if present else DutyStatus.ABSENT
        if duty.status == new_status:
            return duty
        if not can_change_mark(actor, duty):
            raise AttendanceError("Only the exam office can change this mark now.")
        old_status = duty.status
        duty.status = new_status
        duty.attendance_marked_by = actor
        duty.attendance_marked_at = timezone.now()
        duty.save(update_fields=["status", "attendance_marked_by", "attendance_marked_at", "updated_at"])
        log(actor, "duty.attendance", duty, before={"status": old_status}, after={"status": new_status})
    return duty


def mark_room_present(room, actor):
    """Mark every not-yet-marked duty in `room` present. Returns how many were marked."""
    count = 0
    for duty in Duty.objects.filter(room_allocation=room, status=DutyStatus.ASSIGNED):
        mark_attendance(duty, actor, present=True)
        count += 1
    return count
