"""Overview dashboard, report centre and timetable for the exam office."""

from collections import Counter, defaultdict

from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import Role
from accounts.permissions import role_required
from audit.models import AuditLog
from duties.models import BackupMember, BackupReservation, Duty, DutyStatus, Obligation, ReservationStatus
from exams.models import Building, ExamPeriod, PeriodStatus, RoomAllocation, TimeSlot
from faculty.models import Department, Faculty, FacultyCategory
from imports.models import BatchStatus, ImportBatch
from workload.calc import Flag, period_loads, summary

from . import charts
from .center import REPORTS, Filters, to_csv, to_pdf, to_xlsx

admin_only = role_required(Role.ADMIN)
SESSION_KEY = "dashboard_period"
ROW_LIMIT = 200


def chosen_period(request):
    """The period picked in the top bar (kept in the session), else the most relevant one."""
    pk = request.GET.get("period") or request.session.get(SESSION_KEY)
    period = ExamPeriod.objects.filter(pk=pk).first() if str(pk or "").isdigit() else None
    if period is None:
        period = (
            ExamPeriod.objects.filter(status=PeriodStatus.PUBLISHED).order_by("-start_date").first()
            or ExamPeriod.objects.exclude(status=PeriodStatus.CLOSED).order_by("-start_date").first()
            or ExamPeriod.objects.order_by("-start_date").first()
        )
    if period is not None:
        request.session[SESSION_KEY] = period.pk
    return period


def period_choices(request):
    """Context processor: periods for the top-bar selector (exam office only)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not (user.is_superuser or user.role == Role.ADMIN):
        return {}
    return {"top_periods": ExamPeriod.objects.order_by("-start_date").only("pk", "name")[:30], "top_period_id": request.session.get(SESSION_KEY)}


@admin_only
def dashboard(request):
    period = chosen_period(request)
    context = {"period": period, "now": timezone.now()}
    faculty = Faculty.objects.all()
    context["faculty_total"] = faculty.count()
    context["faculty_to_fix"] = faculty.filter(Q(phone="") | (~Q(phone="") & Q(phone_e164=""))).count()
    context["imported"] = sum((b.result or {}).get("created", 0) for b in ImportBatch.objects.filter(kind="faculty", status=BatchStatus.DONE))
    if period is None:
        return render(request, "reports/dashboard.html", context)

    rooms = list(RoomAllocation.objects.filter(session__slot__period=period).select_related("session__slot").annotate(
        filled=Count("duties", filter=~Q(duties__status=DutyStatus.CANCELLED))))
    needed = sum(r.invigilators_required for r in rooms)
    filled = sum(min(r.filled, r.invigilators_required) for r in rooms)
    duties = Duty.objects.filter(period=period)
    status = Counter(duties.values_list("status", flat=True))
    loads = period_loads(period)
    flags = summary(loads)
    obligations = [o for o in Obligation.objects.filter(original__period=period).select_related("makeup") if o.is_outstanding]
    next_slots = list(TimeSlot.objects.filter(period=period, date__gte=timezone.localdate()).order_by("date", "start_time")[:5])
    slot_fill = defaultdict(lambda: [0, 0])
    for r in rooms:
        slot_fill[r.session.slot_id][0] += min(r.filled, r.invigilators_required)
        slot_fill[r.session.slot_id][1] += r.invigilators_required
    people_url = reverse("workload_people", args=[period.pk])
    reports_url = reverse("report_center")
    dash = reverse("period_dashboard", args=[period.pk])

    by_category = defaultdict(list)
    for x in loads:
        if Flag.EXEMPT not in x.flags and Flag.NOT_ELIGIBLE not in x.flags:
            by_category[x.faculty.category.name].append(x.counted)
    distribution = Counter(x.counted for x in loads if Flag.EXEMPT not in x.flags and Flag.NOT_ELIGIBLE not in x.flags)
    free_backups = BackupReservation.objects.filter(period=period, status=ReservationStatus.RESERVED).count()

    context.update({
        "rooms_total": len({(r.session.slot_id, r.room.lower()) for r in rooms}),
        "seats_needed": needed,
        "seats_filled": filled,
        "seats_empty": needed - filled,
        "coverage": round(100 * filled / needed) if needed else None,
        "assigned": sum(status.values()) - status.get(DutyStatus.CANCELLED, 0),
        "completed": status.get(DutyStatus.COMPLETED, 0),
        "pending": status.get(DutyStatus.ASSIGNED, 0),
        "missed": status.get(DutyStatus.ABSENT, 0),
        "unconfirmed": duties.filter(status=DutyStatus.ASSIGNED, acknowledged_at__isnull=True).count() if period.status == PeriodStatus.PUBLISHED else None,
        "obligations": len(obligations),
        "backups_pool": BackupMember.objects.filter(faculty__is_active=True).count(),
        "backups_free": free_backups,
        "below": flags[Flag.BELOW],
        "at": flags[Flag.AT],
        "over": flags[Flag.OVER],
        "no_target": flags[Flag.NO_TARGET],
        "next_slots": [(s, slot_fill[s.id]) for s in next_slots],
        "recent": AuditLog.objects.select_related("actor").order_by("-timestamp")[:8],
        "links": {
            "people": people_url, "reports": reports_url, "dash": dash,
            "missed": reverse("missed_duties") + f"?period={period.pk}",
            "backups": reverse("period_backups", args=[period.pk]),
            "check": reverse("period_check", args=[period.pk]),
            "faculty": reverse("faculty_list"), "rooms": reverse("room_list"),
            "timetable": reverse("timetable", args=[period.pk]),
        },
        "chart_status": charts.stacked([
            ("Completed", status.get(DutyStatus.COMPLETED, 0), "tone-ok"),
            ("Pending", status.get(DutyStatus.ASSIGNED, 0), "tone-info"),
            ("Missed", status.get(DutyStatus.ABSENT, 0), "tone-danger"),
        ]),
        "chart_categories": charts.hbars(
            [(name, round(sum(v) / len(v), 1), f"{name}: average {sum(v) / len(v):.1f} counted duties ({len(v)} people)", f"{people_url}?category={_cat(name)}")
             for name, v in sorted(by_category.items())], unit=" avg"),
        "chart_distribution": charts.columns([(str(n), distribution.get(n, 0)) for n in range(0, max(distribution, default=0) + 1)], unit=" people"),
        "chart_coverage": charts.hbars(
            [(f"{s.date:%a %d %b} {s.start_time:%H:%M}", round(100 * f / n) if n else 0, f"{s}: {f} of {n} seats filled", f"{dash}#slot-{s.pk}")
             for s in TimeSlot.objects.filter(period=period).order_by("date", "start_time")
             for f, n in [slot_fill[s.id]] if n][:30], unit="%", max_value=100),
    })
    return render(request, "reports/dashboard.html", context)


def _cat(name):
    c = FacultyCategory.objects.filter(name=name).first()
    return c.pk if c else ""


@admin_only
def report_center(request):
    key = request.GET.get("type", "faculty")
    if key not in REPORTS:
        key = "faculty"
    filters = Filters.from_query(request.GET)
    if filters.period is None and "period" not in request.GET:
        filters.period = chosen_period(request)
    title, build = REPORTS[key]
    report = build(filters)
    fmt = request.GET.get("format", "")
    if fmt in ("csv", "xlsx", "pdf"):
        name = slugify(f"{title} {filters.period.name if filters.period else ''}")[:80]
        if fmt == "csv":
            content, ctype = to_csv(report), "text/csv; charset=utf-8"
        elif fmt == "xlsx":
            content, ctype = to_xlsx(report), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            content, ctype = to_pdf(report, filters.period.name if filters.period else ""), "application/pdf"
        response = HttpResponse(content, content_type=ctype)
        response["Content-Disposition"] = f'attachment; filename="{name}.{fmt}"'
        return response
    params = request.GET.copy()
    params.pop("format", None)
    context = {
        "key": key,
        "reports": [(k, t) for k, (t, _) in REPORTS.items()],
        "report": report,
        "rows": report.rows[:ROW_LIMIT],
        "truncated": len(report.rows) > ROW_LIMIT,
        "f": filters,
        "q": request.GET,
        "querystring": params.urlencode(),
        "periods": ExamPeriod.objects.order_by("-start_date"),
        "departments": Department.objects.all(),
        "categories": FacultyCategory.objects.all(),
        "buildings": Building.objects.all(),
        "statuses": DutyStatus.choices,
        "people": Faculty.objects.order_by("name").only("pk", "name", "employee_id"),
    }
    return render(request, "reports/center.html", context)


@admin_only
def timetable(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    slots = list(period.slots.order_by("date", "start_time"))
    rooms = RoomAllocation.objects.filter(session__slot__period=period).annotate(
        filled=Count("duties", filter=~Q(duties__status=DutyStatus.CANCELLED)))
    per_slot = defaultdict(lambda: {"rooms": set(), "needed": 0, "filled": 0, "courses": set()})
    for r in rooms.select_related("session"):
        cell = per_slot[r.session.slot_id]
        cell["rooms"].add(r.room.lower())
        cell["needed"] += r.invigilators_required
        cell["filled"] += min(r.filled, r.invigilators_required)
        cell["courses"].add(r.session.course_code)
    times = sorted({(s.start_time, s.end_time) for s in slots})
    days = sorted({s.date for s in slots})
    grid = []
    lookup = {(s.date, s.start_time, s.end_time): s for s in slots}
    for day in days:
        cells = []
        for start, end in times:
            s = lookup.get((day, start, end))
            if s is None:
                cells.append(None)
                continue
            c = per_slot[s.id]
            cells.append({"slot": s, "rooms": len(c["rooms"]), "courses": len(c["courses"]), "needed": c["needed"], "filled": c["filled"]})
        grid.append((day, cells))
    return render(request, "reports/timetable.html", {"period": period, "times": times, "grid": grid})


@admin_only
def pick_period(request):
    period = get_object_or_404(ExamPeriod, pk=request.GET.get("period") or 0)
    request.session[SESSION_KEY] = period.pk
    return redirect(request.GET.get("next") if str(request.GET.get("next", "")).startswith("/") else reverse("dashboard"))
