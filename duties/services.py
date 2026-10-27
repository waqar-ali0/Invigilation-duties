"""Manual edits to a draft roster, checked against the same rules as the allocation engine."""

from django.db import transaction

from allocation.engine import AllocationState
from allocation.services import build_engine_input
from audit.log import log
from exams.halls import hall_lead
from exams.models import ExamPeriod
from faculty.models import Faculty

from .models import Duty, DutySource

MSG_INELIGIBLE = "Faculty is inactive or exempt in this period"
MSG_ROOM_FULL = "Room already has its required invigilators"


class AssignmentBlocked(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


class AssignmentNeedsConfirmation(Exception):
    def __init__(self, warnings):
        super().__init__("; ".join(warnings))
        self.warnings = warnings


def _period_of(room):
    return ExamPeriod.objects.get(pk=room.session.slot.period_id)


MSG_PREFERENCE = "Goes against their preferences"


def _warnings(state, faculty_id, room_id):
    warnings = state.soft_warnings(faculty_id, room_id)
    if state.remaining(room_id) == 0:
        warnings.append(MSG_ROOM_FULL)
    wish = (state.data.preferences or {}).get(faculty_id)
    room = state.rooms[room_id]
    if wish is not None and wish.misses(state.slots[room.slot_id], room):
        warnings.append(MSG_PREFERENCE)
    return warnings


def check_assignment(faculty, room):
    """(hard errors, soft warnings) for giving `faculty` a seat in `room`. Eligibility follows the
    period's workload rules (exempt this period, or an exception for someone inactive)."""
    state = AllocationState(build_engine_input(_period_of(room)))
    if faculty.id not in state.people:
        return [MSG_INELIGIBLE], []
    return state.hard_errors(faculty.id, room.id), _warnings(state, faculty.id, room.id)


def add_duty(faculty, room, actor, allow_warnings=False, reason=""):
    with transaction.atomic():
        # Locking the period serialises every change to this draft, so two people
        # editing at once cannot double-book anyone.
        ExamPeriod.lock_for_edit(room.session.slot.period_id)
        room = hall_lead(room)  # a shared hall's invigilators all sit on its first row
        faculty = Faculty.objects.get(pk=faculty.pk)
        errors, warnings = check_assignment(faculty, room)
        if errors:
            raise AssignmentBlocked(errors)
        if warnings and not allow_warnings:
            raise AssignmentNeedsConfirmation(warnings)
        duty = Duty.objects.create(faculty=faculty, room_allocation=room, source=DutySource.MANUAL)
        after = {"faculty": faculty.id, "room": room.id, "warnings": warnings}
        if warnings and reason.strip():
            after["override_reason"] = reason.strip()[:300]
        log(actor, "duty.assign", duty, after=after)
    return duty


def set_locked(duty, locked, actor):
    """Lock a draft duty so allocation re-runs keep it, or unlock it again."""
    with transaction.atomic():
        ExamPeriod.lock_for_edit(duty.period_id)
        duty.locked = locked
        duty.save(update_fields=["locked", "updated_at"])
        log(actor, "duty.lock" if locked else "duty.unlock", duty, after={"locked": locked})


def remove_duty(duty, actor):
    with transaction.atomic():
        ExamPeriod.lock_for_edit(duty.period_id)
        before = {"faculty": duty.faculty_id, "room": duty.room_allocation_id, "source": duty.source}
        log(actor, "duty.remove", duty, before=before)
        duty.delete()


def eligible_candidates(room):
    """[(faculty, duty count, warnings)] who could take a seat in `room`, best first."""
    state = AllocationState(build_engine_input(_period_of(room)))
    result = []
    for faculty in Faculty.objects.filter(pk__in=list(state.people)).select_related("department"):
        if state.hard_errors(faculty.id, room.id):
            continue
        result.append((faculty, state.count(faculty.id), _warnings(state, faculty.id, room.id)))
    result.sort(key=lambda item: (len(item[2]), item[1], item[0].name))
    return result
