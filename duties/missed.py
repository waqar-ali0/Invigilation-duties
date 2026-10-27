"""Missed duties: why someone was absent, whether it is excused, and the make-up duty they may owe.

The absence itself is never erased. Whoever covered on the day has their own stand-in duty;
the absentee's make-up is a separate duty, linked here, assigned later and tracked until done."""

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from audit.log import log
from exams.models import ExamPeriod, PeriodStatus, RoomAllocation
from notifications.models import EmailKind
from notifications.outbox import queue_email
from notifications.render import absolute_url, render_email
from notifications.triggers import address_of, duty_ics_attachment

from .models import Duty, DutySource, DutyStatus, Obligation
from .versions import record_roster_version


class MissedDutyError(Exception):
    pass


def decide_absence(duty, actor, reason, excused, owe_makeup, note=""):
    """Record the reason and decision for an absence, and create (or waive) the make-up owed."""
    reason = (reason or "").strip()
    if not reason:
        raise MissedDutyError("Give the reason for the absence.")
    with transaction.atomic():
        duty = Duty.objects.select_for_update().select_related("faculty").get(pk=duty.pk)
        if duty.status != DutyStatus.ABSENT:
            raise MissedDutyError("Only a duty marked absent can be decided.")
        before = {"reason": duty.absence_reason, "excused": duty.absence_excused}
        duty.absence_reason = reason
        duty.absence_excused = bool(excused)
        duty.absence_decided_by = actor
        duty.absence_decided_at = timezone.now()
        duty.save(update_fields=["absence_reason", "absence_excused", "absence_decided_by", "absence_decided_at", "updated_at"])
        obligation = Obligation.objects.filter(original=duty).first()
        if owe_makeup:
            if obligation is None:
                obligation = Obligation.objects.create(original=duty, owed_by=duty.faculty, created_by=actor, note=note[:300])
            elif obligation.waived:
                obligation.waived = False
                obligation.note = note[:300] or obligation.note
                obligation.save(update_fields=["waived", "note", "updated_at"])
        elif obligation is not None and obligation.makeup_id is None:
            obligation.waived = True
            obligation.note = note[:300] or "No make-up required"
            obligation.save(update_fields=["waived", "note", "updated_at"])
        log(actor, "duty.absence_decided", duty, before=before, after={
            "reason": reason, "excused": bool(excused), "makeup_owed": bool(owe_makeup),
        })
    return obligation


def waive(obligation, actor, note):
    with transaction.atomic():
        obligation = Obligation.objects.select_for_update().get(pk=obligation.pk)
        if obligation.makeup_id and obligation.makeup.status != DutyStatus.CANCELLED:
            raise MissedDutyError("A make-up duty is already scheduled; cancel it first.")
        obligation.waived, obligation.note = True, (note or "Waived")[:300]
        obligation.save(update_fields=["waived", "note", "updated_at"])
        log(actor, "obligation.waive", obligation, after={"note": obligation.note})


def _hard_errors(period, faculty, room):
    """Rule problems for an extra (make-up) seat. The period maximum does not apply: it is owed."""
    from allocation.engine import MSG_PERIOD_CAP, AllocationState
    from allocation.services import build_engine_input

    state = AllocationState(build_engine_input(period))
    if faculty.id not in state.people:
        return ["Not eligible for duties in that period"]
    cap_prefix = MSG_PERIOD_CAP.split("(")[0]
    return [e for e in state.hard_errors(faculty.id, room.id) if not e.startswith(cap_prefix)]


def makeup_rooms(faculty, limit=40):
    """Rooms in upcoming exams (published or draft periods) where `faculty` could do a make-up."""
    now = timezone.now()
    rooms = (
        RoomAllocation.objects.filter(
            session__slot__period__status__in=[PeriodStatus.PUBLISHED, PeriodStatus.ALLOCATING, PeriodStatus.DRAFT, PeriodStatus.AVAILABILITY_OPEN],
            session__slot__date__gte=timezone.localdate(),
            invigilators_required__gt=0,
        )
        .select_related("session__slot__period")
        .order_by("session__slot__date", "session__slot__start_time", "room")
    )
    out, by_period = [], {}
    for room in rooms:
        if room.session.slot.starts_at <= now:
            continue
        period = room.session.slot.period
        if period.pk not in by_period:
            by_period[period.pk] = period
        if not _hard_errors(period, faculty, room):
            out.append(room)
        if len(out) >= limit:
            break
    return out


def assign_makeup(obligation, room, actor, faculty=None):
    """Schedule the make-up duty in `room` (a future exam). `faculty` defaults to the absentee."""
    with transaction.atomic():
        obligation = Obligation.objects.select_for_update().select_related("original", "owed_by").get(pk=obligation.pk)
        faculty = faculty or obligation.owed_by
        if obligation.waived:
            raise MissedDutyError("This obligation was waived.")
        if obligation.makeup_id and obligation.makeup.status not in (DutyStatus.CANCELLED,):
            raise MissedDutyError("A make-up duty is already scheduled.")
        room = RoomAllocation.objects.select_related("session__slot__period").get(pk=room.pk)
        period = room.session.slot.period
        if period.status == PeriodStatus.PUBLISHED:
            ExamPeriod.lock_published(period.pk)
        else:
            ExamPeriod.lock_for_edit(period.pk)
        if room.session.slot.starts_at <= timezone.now():
            raise MissedDutyError("Choose an exam that has not started yet.")
        errors = _hard_errors(period, faculty, room)
        if errors:
            raise MissedDutyError("; ".join(errors))
        duty = Duty.objects.create(faculty=faculty, room_allocation=room, source=DutySource.MAKEUP, replaces=obligation.original)
        obligation.makeup = duty
        obligation.save(update_fields=["makeup", "updated_at"])
        log(actor, "obligation.assign", obligation, after={"duty": duty.pk, "faculty": faculty.pk, "room": room.pk})
        if period.status == PeriodStatus.PUBLISHED:
            record_roster_version(period, actor, f"Make-up duty for {faculty.name}")
            from messaging.models import MessageKind
            from messaging.services import notify_duty

            notify_duty(duty, MessageKind.NEW_DUTY, actor)
            subject, body = render_email("makeup_assigned", {
                "person": faculty, "duty": duty, "original": obligation.original, "link": absolute_url(reverse("my_duties")),
            })
            if address_of(faculty):
                queue_email(address_of(faculty), subject, body, EmailKind.MAKEUP_ASSIGNED, attachment=duty_ics_attachment(duty))
    return duty
