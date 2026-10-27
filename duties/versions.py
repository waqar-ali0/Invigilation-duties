"""Roster versions: the published roster at publishing and after every later change, and what
changed between two versions (who, which room, which time)."""

from django.db import transaction

from exams.models import PeriodStatus

from .models import Duty, RosterVersion


def roster_snapshot(period):
    rows = []
    duties = (
        Duty.objects.active()
        .filter(period=period)
        .select_related("faculty", "room_allocation__session__slot")
        .order_by("room_allocation__session__slot__date", "room_allocation__session__slot__start_time", "room_allocation__room", "faculty__name")
    )
    for d in duties:
        s = d.room_allocation.session
        rows.append({
            "duty_id": d.pk,
            "faculty_id": d.faculty_id,
            "faculty": d.faculty.name,
            "employee_id": d.faculty.employee_id,
            "room_id": d.room_allocation_id,
            "room": d.room_allocation.room,
            "slot_id": s.slot_id,
            "slot": str(s.slot),
            "course": s.course_code,
        })
    return rows


def record_roster_version(period, actor, reason):
    """Keep the roster as it is now. Only published periods have versions."""
    if period.status != PeriodStatus.PUBLISHED:
        return None
    with transaction.atomic():
        last = RosterVersion.objects.select_for_update().filter(period=period).order_by("-number").first()
        return RosterVersion.objects.create(
            period=period, number=(last.number + 1) if last else 1, created_by=actor, reason=reason[:200],
            snapshot=roster_snapshot(period),
        )


def compare(older, newer):
    """{"added": [...], "removed": [...], "people": [...], "rooms": [...], "slots": [...]} between two snapshots.

    A seat is (faculty, room booking); someone moved from one room to another shows as removed
    from the old and added to the new."""
    key = lambda r: (r["faculty_id"], r["room_id"])  # noqa: E731
    before = {key(r): r for r in older}
    after = {key(r): r for r in newer}
    added = [after[k] for k in after.keys() - before.keys()]
    removed = [before[k] for k in before.keys() - after.keys()]
    order = lambda r: (r["slot"], r["room"], r["faculty"])  # noqa: E731
    changed = added + removed
    return {
        "added": sorted(added, key=order),
        "removed": sorted(removed, key=order),
        "people": sorted({r["faculty"] for r in changed}),
        "rooms": sorted({r["room"] for r in changed}),
        "slots": sorted({r["slot"] for r in changed}),
    }
