"""Handing a published duty to someone else: the shared rule check, candidates, and the transfer."""

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from allocation.engine import AllocationState
from allocation.services import build_engine_input
from audit.log import log
from exams.models import ExamPeriod
from faculty.models import Faculty
from notifications.models import EmailKind
from notifications.outbox import queue_email
from notifications.render import absolute_url, render_email
from notifications.triggers import address_of, duty_ics_attachment, queue_swap_email

from .models import OPEN_SWAP_STATUSES, Duty, DutySource, DutyStatus, SwapRequest, SwapStatus

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
    if faculty.pk == duty.faculty_id:
        return [MSG_SAME_PERSON], []
    state = _state_without(duty)
    if faculty.id not in state.people:
        return [MSG_INELIGIBLE], []
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
    for faculty in Faculty.objects.filter(pk__in=list(state.people)).exclude(pk=duty.faculty_id).select_related("department", "user"):
        if require_account and not _has_account(faculty):
            continue
        if state.hard_errors(faculty.id, room_id):
            continue
        result.append((faculty, state.count(faculty.id), state.soft_warnings(faculty.id, room_id)))
    result.sort(key=lambda item: (len(item[2]), item[1], item[0].name))
    return result


def replace_duty(duty, faculty, actor, reason):
    """Emergency replacement by the exam office: give `duty` to `faculty` now."""
    reason = (reason or "").strip()
    if not reason:
        raise HandoverBlocked(["A reason is required."])
    with transaction.atomic():
        ExamPeriod.lock_published(duty.period_id)
        duty = Duty.objects.select_for_update().select_related("faculty__user", "room_allocation__session__slot", "period").get(pk=duty.pk)
        if duty.status != DutyStatus.ASSIGNED:
            raise HandoverBlocked(["Only duties not yet marked present or absent can be replaced."])
        if timezone.now() >= duty.room_allocation.session.slot.ends_at:
            raise HandoverBlocked(["This exam has already ended."])
        errors = handover_check(duty, faculty)[0]
        if errors:
            raise HandoverBlocked(errors)

        for swap in SwapRequest.objects.filter(duty=duty, status__in=OPEN_SWAP_STATUSES).select_related(
            "requested_by__user", "requested_with__user"
        ):
            swap.status = SwapStatus.CANCELLED
            swap.save(update_fields=["status"])
            log(actor, "swap.cancel", swap, after={"reason": "duty replaced"})
            queue_swap_email(swap, "swap_cancelled", [address_of(swap.requested_by), address_of(swap.requested_with)])

        new_duty = transfer_duty(duty, faculty, DutySource.REPLACEMENT)
        log(actor, "duty.replace", duty, before={"faculty": duty.faculty_id}, after={"faculty": faculty.pk, "reason": reason, "duty": new_duty.pk})
        link = absolute_url(reverse("my_duties"))
        for kind, person, attachment in (
            (EmailKind.REPLACED_OUT, duty.faculty, None),
            (EmailKind.REPLACED_IN, faculty, duty_ics_attachment(new_duty)),
        ):
            subject, body = render_email(kind, {"person": person, "duty": new_duty, "link": link})
            queue_email(address_of(person), subject, body, kind, attachment=attachment)
    return new_duty


def transfer_duty(duty, faculty, source):
    """Cancel `duty` and create the same seat for `faculty`. Call inside a transaction, after checking."""
    duty.status = DutyStatus.CANCELLED
    duty.save(update_fields=["status", "updated_at"])
    return Duty.objects.create(faculty=faculty, room_allocation=duty.room_allocation, source=source, replaces=duty)
