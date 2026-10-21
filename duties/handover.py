"""Handing a published duty to someone else: the shared rule check, candidates, and the transfer."""

from allocation.engine import AllocationState
from allocation.services import build_engine_input
from faculty.models import Faculty

from .models import Duty

MSG_INELIGIBLE = "Faculty is inactive or exempt"
MSG_SAME_PERSON = "This is already their duty"


class HandoverBlocked(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def _state_without(duty):
    return AllocationState(build_engine_input(duty.period, exclude_duty_ids=[duty.pk]))


def handover_check(duty, faculty):
    """(hard errors, soft warnings) for giving `duty`'s seat to `faculty`; the duty's own seat counts as free."""
    if not faculty.is_active or faculty.is_exempt:
        return [MSG_INELIGIBLE], []
    if faculty.pk == duty.faculty_id:
        return [MSG_SAME_PERSON], []
    state = _state_without(duty)
    room_id = duty.room_allocation_id
    return state.hard_errors(faculty.id, room_id), state.soft_warnings(faculty.id, room_id)


def _has_account(faculty):
    user = faculty.user
    return user is not None and user.is_active and user.has_usable_password()


def handover_candidates(duty, require_account):
    """[(faculty, active duty count, warnings)] who could take `duty`, best first."""
    state = _state_without(duty)
    room_id = duty.room_allocation_id
    result = []
    for faculty in Faculty.objects.eligible().exclude(pk=duty.faculty_id).select_related("department", "user"):
        if require_account and not _has_account(faculty):
            continue
        if state.hard_errors(faculty.id, room_id):
            continue
        result.append((faculty, state.count(faculty.id), state.soft_warnings(faculty.id, room_id)))
    result.sort(key=lambda item: (len(item[2]), item[1], item[0].name))
    return result


def transfer_duty(duty, faculty, source):
    """Cancel `duty` and create the same seat for `faculty`. Call inside a transaction, after checking."""
    from .models import DutyStatus

    duty.status = DutyStatus.CANCELLED
    duty.save(update_fields=["status", "updated_at"])
    return Duty.objects.create(faculty=faculty, room_allocation=duty.room_allocation, source=source, replaces=duty)
