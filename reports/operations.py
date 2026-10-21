"""What happened to a published roster: who did their duties, who swapped, who was replaced."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from audit.models import AuditLog
from duties.models import Duty, DutySource, DutyStatus, SwapRequest, SwapStatus

HISTORY_TYPES = {DutySource.SWAP: "Swap", DutySource.REPLACEMENT: "Replacement", DutySource.STAND_IN: "Stand-in"}
COUNT_FIELDS =("assigned", "completed", "absent", "unmarked", "swapped_away", "taken_over", "replaced")


@dataclass
class FacultyRecord:
    faculty: object
    assigned: int = 0  # duties they hold (not handed over), marked or not
    completed: int = 0  # the "done" figure: present
    absent: int = 0
    unmarked: int = 0
    swapped_away: int = 0
    taken_over: int = 0  # received by swap or replacement
    replaced: int = 0  # taken away from them by the exam office
    absence_dates: list[date] = field(default_factory=list)


def _period_duties(period, departments=None):
    duties = Duty.objects.filter(period=period).select_related(
        "faculty__department", "room_allocation__session__slot", "replaces"
    )
    if departments is not None:
        duties = duties.filter(faculty__department__in=departments)
    return list(duties)


def faculty_records(period, departments=None):
    duties = _period_duties(period, departments)
    # source of the duty that replaced each cancelled one
    successor_source = dict(
        Duty.objects.filter(period=period, replaces__isnull=False).values_list("replaces_id", "source")
    )
    records = {}
    for d in duties:
        r = records.setdefault(d.faculty_id, FacultyRecord(d.faculty))
        if d.source in (DutySource.SWAP, DutySource.REPLACEMENT, DutySource.STAND_IN):
            r.taken_over += 1
        if d.status == DutyStatus.CANCELLED:
            source = successor_source.get(d.pk)
            if source == DutySource.SWAP:
                r.swapped_away += 1
            elif source == DutySource.REPLACEMENT:
                r.replaced += 1
            continue
        r.assigned += 1
        if d.status == DutyStatus.COMPLETED:
            r.completed += 1
        elif d.status == DutyStatus.ABSENT:
            r.absent += 1
            r.absence_dates.append(d.room_allocation.session.slot.date)
        else:
            r.unmarked += 1
    for r in records.values():
        r.absence_dates.sort()
    return sorted(records.values(), key=lambda r: r.faculty.name)


def department_summary(period, departments=None, records=None):
    """Counts per department; pass `records` (from faculty_records) to avoid building them again."""
    totals = defaultdict(lambda: dict.fromkeys(COUNT_FIELDS, 0))
    for r in faculty_records(period, departments) if records is None else records:
        row = totals[r.faculty.department.name]
        for f in COUNT_FIELDS:
            row[f] += getattr(r, f)
    return [{"department": name, **totals[name]} for name in sorted(totals)]


def handover_history(period):
    """Every swap, replacement and stand-in in the period, newest first."""
    new_duties = list(
        Duty.objects.filter(period=period, replaces__isnull=False).select_related(
            "faculty", "replaces__faculty", "room_allocation__session__slot"
        )
    )
    swaps = {
        s.new_duty_id: s
        for s in SwapRequest.objects.filter(new_duty__in=new_duties, status=SwapStatus.APPROVED).select_related("decided_by")
    }
    replace_logs = {
        int(e.object_id): e
        for e in AuditLog.objects.filter(
            action__in=["duty.replace", "duty.stand_in"],
            object_type="duties.Duty",
            object_id__in=[str(d.replaces_id) for d in new_duties],
        ).select_related("actor")
    }
    rows = []
    for d in new_duties:
        if d.source == DutySource.SWAP:
            swap = swaps.get(d.pk)
            note = (swap.comment or swap.message) if swap else ""
            by = swap.decided_by if swap else None
        else:
            entry = replace_logs.get(d.replaces_id)
            note = (entry.after or {}).get("reason", "") if entry else ""
            by = entry.actor if entry else None
        rows.append(
            {
                "id": d.pk,
                "when": d.created_at,
                "slot": d.room_allocation.session.slot,
                "room": d.room_allocation.room,
                "from": d.replaces.faculty,
                "to": d.faculty,
                "type": HISTORY_TYPES.get(d.source, "Replacement"),
                "note": note,
                "by": by,
            }
        )
    rows.sort(key=lambda r: (r["when"], r["id"]), reverse=True)
    return rows
