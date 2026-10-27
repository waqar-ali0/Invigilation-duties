"""Database glue around the pure allocation engine."""

import random
from collections import Counter, defaultdict

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from audit.log import log
from duties.backups import reserve_backups
from duties.models import (
    BackupMember,
    BackupRole,
    Duty,
    DutySource,
    DutyStatus,
    Preference,
    PreferenceKind,
    Unavailability,
    UnavailabilityStatus,
)
from exams.halls import staff_shared_halls
from exams.models import ExamPeriod, ExamSession, PeriodStatus, RoomAllocation
from faculty.models import Faculty
from notifications.triggers import queue_roster_published

from .engine import AllocationResult, CapacityReport, EngineInput, Person, Room, Rules, Slot, allocate, capacity_check
from .engine import Preference as EnginePreference


class PublishBlocked(Exception):
    """Publishing is not possible (yet) for the reason in the message."""


def engine_people(period):
    """Engine Persons for everyone who may get duties in `period`, with their rule's bounds.

    Eligible = active and not exempt (or given an exception for this period), and not exempt
    for this period. Duties in periods counted together with this one are passed as `prior`."""
    from workload.calc import prior_counts
    from workload.rules import resolve_rules

    candidates = Faculty.objects.filter(Q(is_active=True, is_exempt=False) | Q(workload_rules__period=period, workload_rules__allow_inactive=True)).select_related("category").distinct()
    rules = resolve_rules(period, candidates)
    prior = prior_counts(period)
    reserve_only = set(BackupMember.objects.filter(role=BackupRole.RESERVE_ONLY).values_list("faculty_id", flat=True))
    people = []
    for f in candidates:
        rule = rules[f.id]
        if rule.exempt or (not (f.is_active and not f.is_exempt) and not rule.allow_inactive):
            continue
        low, high = rule.bounds()
        people.append(Person(
            f.id, f.department_id, high, minimum=low or 0, target=rule.target, prior=prior.get(f.id, 0),
            reserve=f.id in reserve_only,
        ))
    return people


def engine_preferences(period):
    """{faculty id: engine Preference} from the soft wishes recorded for `period`."""
    wishes = defaultdict(lambda: {"dates": set(), "slots": set(), "avoid_slots": set(), "buildings": set()})
    for faculty_id, kind, day, slot_id, building_id in Preference.objects.filter(period=period).values_list(
        "faculty_id", "kind", "date", "slot_id", "building_id"
    ):
        w = wishes[faculty_id]
        if kind == PreferenceKind.DATE and day:
            w["dates"].add(day)
        elif kind == PreferenceKind.SLOT and slot_id:
            w["slots"].add(slot_id)
        elif kind == PreferenceKind.AVOID_SLOT and slot_id:
            w["avoid_slots"].add(slot_id)
        elif kind == PreferenceKind.BUILDING and building_id:
            w["buildings"].add(building_id)
    return {fid: EnginePreference(**{k: frozenset(v) for k, v in w.items()}) for fid, w in wishes.items()}


def build_engine_input(period, exclude_auto=False, include_pending=False, exclude_duty_ids=(), only_slots=None):
    """Describe `period` for the engine. Existing active duties become `fixed`
    (AUTO ones are left out when `exclude_auto`; duties in `exclude_duty_ids` are left out,
    e.g. one being handed over). Approved unavailability blocks people; with
    `include_pending`, pending requests do too (for "what if all are approved").
    With `only_slots`, rooms in other slots keep exactly the duties they have."""
    s = period.settings
    people = engine_people(period)

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
    room_rows = list(RoomAllocation.objects.filter(session__slot__period=period).select_related("session", "room_ref"))
    held = Counter()
    if only_slots is not None:
        held.update(Duty.objects.active().filter(period=period).values_list("room_allocation_id", flat=True))
    rooms = [
        Room(
            r.id,
            r.session.slot_id,
            r.invigilators_required if only_slots is None or r.session.slot_id in only_slots else min(held[r.id], r.invigilators_required),
            frozenset(teachers[r.session_id]),
            r.room_ref.building_id if r.room_ref_id else None,
        )
        for r in room_rows
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
        rules=Rules(s.max_duties_per_day, s.avoid_back_to_back, s.exclude_course_teacher, s.reserves_fill_shortages),
        unavailable_days=frozenset(unavailable_days),
        unavailable_slots=frozenset(unavailable_slots),
        fixed=fixed,
        preferences=engine_preferences(period),
    )


def run_allocation(period, actor, seed=None, slot_ids=None) -> AllocationResult:
    """(Re)allocate the draft: unlocked AUTO duties are replaced, manual and locked ones kept.
    With `slot_ids`, only those time slots are re-generated; the rest stays exactly as it is."""
    caller_period = period
    period.ensure_editable()
    with transaction.atomic():
        period = ExamPeriod.lock_for_edit(period.pk)
        staff_shared_halls(period)  # also catches rooms edited in the admin panel since the import
        replaced = Duty.objects.active().filter(period=period, source=DutySource.AUTO, locked=False)
        if slot_ids is not None:
            slot_ids = set(period.slots.filter(pk__in=slot_ids).values_list("pk", flat=True))
            replaced = replaced.filter(room_allocation__session__slot_id__in=slot_ids)
        replaced.delete()
        if seed is None:
            seed = random.randrange(1, 2**31)
        result = allocate(build_engine_input(period, only_slots=slot_ids), seed)
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
            "preferences": {"met": result.preferences_met, "total": result.preferences_total},
            "slots": sorted(slot_ids) if slot_ids is not None else "all",
        }
        period.allocation_seed = seed
        period.status = PeriodStatus.ALLOCATING
        period.last_run = {
            **summary,
            "unfilled": [{"room_id": u.room_id, "count": u.count, "reason": u.reason} for u in result.unfilled],
        }
        period.save(update_fields=["allocation_seed", "status", "last_run"])
        reserve_backups(period)
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


def roster_conflicts(period):
    """[(duty, reason)] for duties that break a rule made after they were assigned: an approved
    unavailability for that time, or a faculty member who is now inactive or exempt."""
    duties = list(
        Duty.objects.filter(period=period, status=DutyStatus.ASSIGNED)
        .select_related("faculty", "room_allocation__session__slot")
        .order_by("room_allocation__session__slot__date", "room_allocation__session__slot__start_time")
    )
    blocked = set()
    for faculty_id, day, slot_id in Unavailability.objects.filter(
        period=period, status=UnavailabilityStatus.APPROVED
    ).values_list("faculty_id", "date", "slot_id"):
        blocked.add((faculty_id, day, slot_id))
    eligible = {p.id for p in engine_people(period)}
    conflicts = []
    for d in duties:
        slot = d.room_allocation.session.slot
        if d.faculty_id not in eligible:
            conflicts.append((d, "Faculty is now inactive or exempt"))
        elif (d.faculty_id, slot.date, None) in blocked or (d.faculty_id, slot.date, slot.id) in blocked:
            conflicts.append((d, "Approved as unavailable at this time"))
    return conflicts


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


def publish_period(period, actor, acknowledge_unfilled=False, confirm_rules=False) -> None:
    """Publish the draft. The workload rules must be confirmed first (or now, with `confirm_rules`)."""
    from workload.models import WorkloadPolicy
    from workload.services import confirm

    caller_period = period
    period.ensure_editable()
    with transaction.atomic():
        period = ExamPeriod.lock_for_edit(period.pk)
        if not Duty.objects.active().filter(period=period).exists():
            raise PublishBlocked("There are no duties to publish.")
        missing = sum(m for _, m, _ in unfilled_rooms(period))
        if missing and not acknowledge_unfilled:
            raise PublishBlocked(f"{missing} seats are unfilled. Confirm to publish anyway.")
        if WorkloadPolicy.for_period(period).confirmed_at is None:
            if not confirm_rules:
                raise PublishBlocked("The workload rules have not been confirmed. Review them, then confirm.")
            confirm(period, actor)
        period.status = PeriodStatus.PUBLISHED
        period.published_at = timezone.now()
        period.save(update_fields=["status", "published_at"])
        emails = queue_roster_published(period)
        log(actor, "period.publish", period, after={"unfilled_seats": missing, "emails": emails})
    _copy_fields(period, caller_period, ["status", "published_at"])
