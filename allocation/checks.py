"""Checks before allocating and publishing: what is missing, what will go wrong, and how to fix it."""

from dataclasses import dataclass, field

from django.urls import reverse

from duties.models import BackupMember, Unavailability, UnavailabilityStatus
from exams.models import RoomAllocation
from faculty.models import Faculty
from workload.calc import Flag, period_loads, summary
from workload.models import WorkloadPolicy

OK, INFO, WARN, ERROR = "ok", "info", "warn", "error"
LIST_LIMIT = 12


@dataclass
class Check:
    level: str
    title: str
    details: list = field(default_factory=list)
    fix: str = ""
    link: str = ""


REASON_FIXES = (
    ("unavailable", "Review the approved unavailability for this time, or add faculty."),
    ("period cap", "Raise the maximum on the Workload rules page, or add a personal exception."),
    ("daily limit", "Raise the maximum per day on the Workload rules page."),
    ("already assigned", "Add faculty, or let backup-only members fill shortages (Backups page)."),
    ("reserve", "Let backup-only members fill shortages (Backups page)."),
)


def fixes_for(reason):
    return [fix for key, fix in REASON_FIXES if key in reason.lower()]


def period_checks(period):
    from .services import capacity_report, roster_conflicts, unfilled_rooms

    checks = []
    rooms = list(RoomAllocation.objects.filter(session__slot__period=period).select_related("session__slot", "room_ref"))
    dashboard = reverse("period_dashboard", args=[period.pk])
    rules_url = reverse("workload_rules", args=[period.pk])

    if not rooms:
        checks.append(Check(ERROR, "No exam schedule yet", fix="Import the exam schedule.", link=reverse("schedule_import", args=[period.pk])))
        return checks
    slots = {r.session.slot_id for r in rooms}
    seats = sum(r.invigilators_required for r in rooms)
    checks.append(Check(OK, f"Schedule: {len(slots)} time slot(s), {len(rooms)} room booking(s), {seats} invigilator seat(s)"))

    unlinked = sorted({r.room for r in rooms if r.room_ref_id is None})
    if unlinked:
        checks.append(Check(WARN, f"{len(unlinked)} room(s) are not in the room list", unlinked[:LIST_LIMIT],
                            "Add them to the room list so building and capacity are known.", reverse("room_list")))
    crowded = [f"{r.session.slot} · {r.session.course_code} · {r.room}: {r.student_count} students, {r.room_ref.capacity} seats"
               for r in rooms if r.room_ref_id and r.room_ref.capacity is not None and r.student_count > r.room_ref.capacity]
    if crowded:
        checks.append(Check(WARN, "Rooms with more students than seats", crowded[:LIST_LIMIT], "Check the schedule or the room capacity."))

    policy = WorkloadPolicy.for_period(period)
    loads = period_loads(period)
    counts = summary(loads)
    if counts[Flag.NO_TARGET]:
        checks.append(Check(INFO, f"{counts[Flag.NO_TARGET]} eligible faculty have no target",
                            ["Without a target the engine shares duties equally. Nothing is invented."],
                            "Set targets if the university has them.", rules_url))
    if policy.confirmed_at is None:
        checks.append(Check(WARN, "Workload rules not confirmed", fix="Review and confirm them before publishing.", link=rules_url))
    else:
        checks.append(Check(OK, f"Workload rules confirmed {policy.confirmed_at:%d %b %Y %H:%M}"))

    capacity = capacity_report(period)
    if not capacity.ok:
        checks.append(Check(ERROR, f"Not enough capacity: {capacity.seats_needed} seats still to fill, at most {capacity.capacity} possible",
                            [f"{capacity.seats_needed - capacity.capacity} more duties are needed than the rules allow."],
                            "Raise maximums or per-day limits, add faculty, approve exceptions, or let backups fill shortages.", rules_url))
    elif capacity.seats_needed:
        checks.append(Check(OK, f"Capacity: {capacity.seats_needed} seats to fill, up to {capacity.capacity} possible"))
    slot_names = {s.id: str(s) for s in period.slots.all()}
    shortages = [f"{slot_names[sid]}: {needed} seats, {available} people free" for sid, needed, available in capacity.slot_shortages]
    if shortages:
        checks.append(Check(ERROR, "Time slots with more seats than free people", shortages,
                            "Add faculty, review unavailability for these times, or move exams."))

    pending = Unavailability.objects.filter(period=period, status=UnavailabilityStatus.PENDING).count()
    if pending:
        checks.append(Check(WARN, f"{pending} unavailability request(s) still pending", fix="Ask HODs to decide them.", link=reverse("admin_requests")))

    conflicts = roster_conflicts(period)
    if conflicts:
        checks.append(Check(ERROR, f"{len(conflicts)} duty conflict(s)", [f"{d.faculty.name}: {why}" for d, why in conflicts[:LIST_LIMIT]],
                            "Re-run the allocation, or replace these duties.", dashboard))

    unfilled = unfilled_rooms(period)
    empty = sum(m for _, m, _ in unfilled)
    if unfilled and not period.last_run:
        checks.append(Check(INFO, f"{empty} seat(s) waiting for the allocation", fix="Run the allocation when the checks above are fine.", link=dashboard))
    elif unfilled:
        details, fixes = [], []
        for room, missing, reason in unfilled[:LIST_LIMIT]:
            details.append(f"{room.session.slot} · {room.session.course_code} · {room.room}: {missing} missing{' — ' + reason if reason else ''}")
        for _, _, reason in unfilled:
            fixes += [f for f in fixes_for(reason) if f not in fixes]
        if len(unfilled) > LIST_LIMIT:
            details.append(f"… and {len(unfilled) - LIST_LIMIT} more room(s)")
        checks.append(Check(ERROR, f"{empty} seat(s) unfilled after the last run", details, " ".join(fixes) or "See the reasons above.", dashboard))
    elif period.last_run:
        checks.append(Check(OK, "Every seat is filled"))

    over = [x for x in loads if Flag.OVER in x.flags]
    if over:
        checks.append(Check(ERROR, f"{len(over)} faculty over their limit", [f"{x.faculty.name}: {x.counted} counted, {x.rule.describe()}" for x in over[:LIST_LIMIT]],
                            "Remove duties or record a personal exception.", reverse("workload_people", args=[period.pk]) + "?flag=over"))
    below_min = [x for x in loads if x.rule.bounds()[0] and x.counted < x.rule.bounds()[0]]
    if below_min and period.last_run:
        checks.append(Check(WARN, f"{len(below_min)} faculty below their minimum", [f"{x.faculty.name}: {x.counted} of at least {x.rule.bounds()[0]}" for x in below_min[:LIST_LIMIT]],
                            "They were unavailable or there were not enough seats; adjust the rule or assign by hand.", reverse("workload_people", args=[period.pk]) + "?flag=below"))

    wanted = period.settings.backups_per_slot
    if wanted:
        pool = BackupMember.objects.filter(faculty__is_active=True).count()
        if pool < wanted:
            checks.append(Check(WARN, f"Backup pool has {pool} member(s) but {wanted} are wanted per slot", fix="Add people to the backup pool.", link=reverse("backup_pool")))

    bad_phones = Faculty.objects.filter(is_active=True).exclude(phone="").filter(phone_e164="").count()
    if bad_phones:
        checks.append(Check(INFO, f"{bad_phones} active faculty have an invalid phone number", fix="Correct them so reminders can reach them.", link=reverse("faculty_list") + "?contact=invalid"))
    return checks
