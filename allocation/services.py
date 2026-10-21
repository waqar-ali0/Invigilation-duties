"""Database glue around the pure allocation engine."""

import random
from collections import defaultdict

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from audit.log import log
from duties.models import Duty, DutySource, DutyStatus, Unavailability, UnavailabilityStatus
from exams.models import ExamPeriod, ExamSession, PeriodStatus, RoomAllocation
from faculty.models import Faculty
from notifications.triggers import queue_roster_published

from .engine import AllocationResult, CapacityReport, EngineInput, Person, Room, Rules, Slot, allocate, capacity_check


class PublishBlocked(Exception):
    """Publishing is not possible (yet) for the reason in the message."""


def build_engine_input(period, exclude_auto=False, include_pending=False, exclude_duty_ids=()):
    """Describe `period` for the engine. Existing active duties become `fixed`
    (AUTO ones are left out when `exclude_auto`; duties in `exclude_duty_ids` are left out,
    e.g. one being handed over). Approved unavailability blocks people; with
    `include_pending`, pending requests do too (for "what if all are approved")."""
    s = period.settings
    caps = {c.designation: c.max_duties for c in period.designation_caps.all()}
    people = [
        Person(f.id, f.department_id, caps.get(f.designation, s.max_duties_per_faculty))
        for f in Faculty.objects.eligible().only("id", "department_id", "designation")
    ]

    slots = []
    order_on_date = defaultdict(int)
    for slot in period.slots.order_by("date", "start_time"):
        slots.append(Slot(slot.id, slot.date, order_on_date[slot.date], slot.start_time, slot.end_time))
        order_on_date[slot.date] += 1

    teachers = defaultdict(set)
    for session_id, faculty_id in ExamSession.course_teachers.through.objects.filter(examsession__slot__period=period).values_list(
        "examsession_id", "faculty_id"
    ):
        teachers[session_id].add(faculty_id)
    rooms = [
        Room(r.id, r.session.slot_id, r.invigilators_required, frozenset(teachers[r.session_id]))
        for r in RoomAllocation.objects.filter(session__slot__period=period).select_related("session")
    ]

    statuses = [UnavailabilityStatus.APPROVED] + ([UnavailabilityStatus.PENDING] if include_pending else [])
    unavailable_days, unavailable_slots = set(), set()
    for faculty_id, day, slot_id in Unavailability.objects.filter(
        period=period, status__in=statuses
    ).values_list("faculty_id", "date", "slot_id"):
        if slot_id is None:
            unavailable_days.add((faculty_id, day))
        else:
            unavailable_slots.add((faculty_id, slot_id))

    duties = Duty.objects.active().filter(period=period)
    if exclude_auto:
        duties = duties.exclude(source=DutySource.AUTO)
    if exclude_duty_ids:
        duties = duties.exclude(pk__in=exclude_duty_ids)
    fixed = tuple(duties.values_list("faculty_id", "room_allocation_id"))

    return EngineInput(
        slots=slots,
        rooms=rooms,
        people=people,
        rules=Rules(s.max_duties_per_day, s.avoid_back_to_back, s.exclude_course_teacher),
        unavailable_days=frozenset(unavailable_days),
        unavailable_slots=frozenset(unavailable_slots),
        fixed=fixed,
    )


def run_allocation(period, actor, seed=None) -> AllocationResult:
    """(Re)allocate the draft: AUTO duties are replaced, manual ones kept."""
    caller_period = period
    period.ensure_editable()
    with transaction.atomic():
        period = ExamPeriod.lock_for_edit(period.pk)
        Duty.objects.active().filter(period=period, source=DutySource.AUTO).delete()
        if seed is None:
            seed = random.randrange(1, 2**31)
        result = allocate(build_engine_input(period), seed)
        Duty.objects.bulk_create(
            Duty(period=period, faculty_id=person_id, room_allocation_id=room_id, source=DutySource.AUTO)
            for person_id, room_id in result.assignments
        )
        summary = {
            "seed": seed,
            "run_at": timezone.now().isoformat(),
            "assigned": len(result.assignments),
            "stats": {"min": result.stats.min, "max": result.stats.max, "avg": result.stats.avg},
            "soft_violations": result.soft_violations,
        }
        period.allocation_seed = seed
        period.status = PeriodStatus.ALLOCATING
        period.last_run = {
            **summary,
            "unfilled": [{"room_id": u.room_id, "count": u.count, "reason": u.reason} for u in result.unfilled],
        }
        period.save(update_fields=["allocation_seed", "status", "last_run"])
        log(actor, "allocation.run", period, after={**summary, "unfilled_seats": sum(u.count for u in result.unfilled)})
    _copy_fields(period, caller_period, ["allocation_seed", "status", "last_run"])
    return result


def _copy_fields(source, target, fields):
    """Keep the caller's ExamPeriod object in step with the locked copy that was saved."""
    for f in fields:
        setattr(target, f, getattr(source, f))


def clear_draft(period, actor) -> int:
    """Delete every duty of the draft (auto and manual) and forget the last run's summary."""
    caller_period = period
    period.ensure_editable()
    with transaction.atomic():
        period = ExamPeriod.lock_for_edit(period.pk)
        deleted, _ = Duty.objects.filter(period=period).delete()
        period.last_run = None
        period.save(update_fields=["last_run"])
        log(actor, "allocation.clear", period, after={"deleted": deleted})
    _copy_fields(period, caller_period, ["last_run"])
    return deleted


def capacity_report(period) -> CapacityReport:
    return capacity_check(build_engine_input(period))


def pending_capacity(period):
    """(pending request count, capacity if they were all approved), or None when nothing is pending."""
    pending = Unavailability.objects.filter(period=period, status=UnavailabilityStatus.PENDING).count()
    if not pending:
        return None
    return pending, capacity_check(build_engine_input(period, include_pending=True))


def unfilled_rooms(period):
    """[(room, missing seats, reason from the last run or "")] from the current duties."""
    reasons = {u["room_id"]: u["reason"] for u in (period.last_run or {}).get("unfilled", [])}
    rooms = (
        RoomAllocation.objects.filter(session__slot__period=period)
        .select_related("session__slot")
        .annotate(assigned=Count("duties", filter=~Q(duties__status=DutyStatus.CANCELLED)))
    )
    return [
        (r, r.invigilators_required - r.assigned, reasons.get(r.id, ""))
        for r in rooms
        if r.assigned < r.invigilators_required
    ]


def publish_period(period, actor, acknowledge_unfilled=False) -> None:
    caller_period = period
    period.ensure_editable()
    with transaction.atomic():
        period = ExamPeriod.lock_for_edit(period.pk)
        if not Duty.objects.active().filter(period=period).exists():
            raise PublishBlocked("There are no duties to publish.")
        missing = sum(m for _, m, _ in unfilled_rooms(period))
        if missing and not acknowledge_unfilled:
            raise PublishBlocked(f"{missing} seats are unfilled. Confirm to publish anyway.")
        period.status = PeriodStatus.PUBLISHED
        period.published_at = timezone.now()
        period.save(update_fields=["status", "published_at"])
        emails = queue_roster_published(period)
        log(actor, "period.publish", period, after={"unfilled_seats": missing, "emails": emails})
    _copy_fields(period, caller_period, ["status", "published_at"])
