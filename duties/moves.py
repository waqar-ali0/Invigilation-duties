"""Moving a duty to another room or time slot, keeping the same person."""

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from allocation.engine import AllocationState
from allocation.services import build_engine_input
from audit.log import log
from exams.halls import hall_lead
from exams.models import ExamPeriod, PeriodStatus, RoomAllocation
from notifications.models import EmailKind
from notifications.outbox import queue_email
from notifications.render import absolute_url, render_email
from notifications.triggers import address_of, duty_ics_attachment

from .models import OPEN_SWAP_STATUSES, Duty, DutySource, DutyStatus, SwapRequest, SwapStatus
from .versions import record_roster_version


class MoveBlocked(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def move_targets(duty):
    """Rooms of the same period where the duty's holder could sit instead, earliest first."""
    period = duty.period
    state = AllocationState(build_engine_input(period, exclude_duty_ids=[duty.pk]))
    rooms = RoomAllocation.objects.filter(session__slot__period=period, invigilators_required__gt=0).exclude(pk=duty.room_allocation_id)
    rooms = rooms.select_related("session__slot").order_by("session__slot__date", "session__slot__start_time", "room")
    now = timezone.now()
    out = []
    for room in rooms:
        if period.status == PeriodStatus.PUBLISHED and room.session.slot.starts_at <= now:
            continue
        if room.id in state.rooms and not state.hard_errors(duty.faculty_id, room.id):
            out.append((room, state.remaining(room.id)))
    return out


def move_duty(duty, room, actor, reason=""):
    """Move `duty` to `room` (same period). In a draft the duty itself changes; once published
    the old duty is kept (cancelled) and a new one created, the person is emailed and a roster
    version is kept."""
    reason = (reason or "").strip()
    with transaction.atomic():
        published = ExamPeriod.objects.get(pk=duty.period_id).status == PeriodStatus.PUBLISHED
        period = ExamPeriod.lock_published(duty.period_id) if published else ExamPeriod.lock_for_edit(duty.period_id)
        duty = Duty.objects.select_for_update().select_related("faculty", "room_allocation__session__slot").get(pk=duty.pk)
        room = hall_lead(RoomAllocation.objects.select_related("session__slot").get(pk=room.pk))
        if room.session.slot.period_id != period.pk:
            raise MoveBlocked(["Choose a room in the same exam period."])
        if room.pk == duty.room_allocation_id:
            raise MoveBlocked(["The duty is already in this room."])
        if duty.status != DutyStatus.ASSIGNED:
            raise MoveBlocked(["Only duties not yet marked present or absent can be moved."])
        if published:
            if not reason:
                raise MoveBlocked(["A reason is required once the roster is published."])
            if duty.room_allocation.session.slot.starts_at <= timezone.now() or room.session.slot.starts_at <= timezone.now():
                raise MoveBlocked(["Exams that have started can't be moved."])
        state = AllocationState(build_engine_input(period, exclude_duty_ids=[duty.pk]))
        errors = state.hard_errors(duty.faculty_id, room.pk) if room.pk in state.rooms else ["Unknown room"]
        if errors:
            raise MoveBlocked(errors)
        old_room = duty.room_allocation
        if not published:
            duty.room_allocation = room
            duty.save(update_fields=["room_allocation", "updated_at"])
            log(actor, "duty.move", duty, before={"room": old_room.pk}, after={"room": room.pk, "reason": reason})
            return duty
        for swap in SwapRequest.objects.filter(duty=duty, status__in=OPEN_SWAP_STATUSES):
            swap.status = SwapStatus.CANCELLED
            swap.save(update_fields=["status"])
            log(actor, "swap.cancel", swap, after={"reason": "duty moved"})
        duty.status = DutyStatus.CANCELLED
        duty.save(update_fields=["status", "updated_at"])
        new = Duty.objects.create(faculty=duty.faculty, room_allocation=room, source=DutySource.MOVED, replaces=duty)
        log(actor, "duty.move", duty, before={"room": old_room.pk}, after={"room": room.pk, "duty": new.pk, "reason": reason})
        record_roster_version(period, actor, f"Moved {duty.faculty.name}: {old_room.room} → {room.room}")
        subject, body = render_email("duty_moved", {"person": duty.faculty, "duty": new, "old": duty, "link": absolute_url(reverse("my_duties"))})
        queue_email(address_of(duty.faculty), subject, body, EmailKind.DUTY_MOVED, attachment=duty_ics_attachment(new))
    return new
