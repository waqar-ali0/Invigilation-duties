from collections import defaultdict
from datetime import datetime

from django import forms
from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import role_required
from audit.log import log
from allocation.engine import fairness_stats
from allocation.services import capacity_report, pending_capacity, roster_conflicts, unfilled_rooms
from duties.models import Duty, SwapRequest, SwapStatus

from .availability import AvailabilityError, close_availability, open_availability
from .importers.departments import import_departments
from .halls import hall_key, halls_by_key
from .lifecycle import actions as lifecycle_actions
from .models import ExamPeriod, ExamSession, ExamType, PeriodLockedError, PeriodStatus, RoomAllocation

admin_only = role_required(Role.ADMIN)


@admin_only
def period_list(request):
    show = request.GET.get("show", "current")
    periods = ExamPeriod.objects.select_related("exam_type")
    if show == "archived":
        periods = periods.filter(status=PeriodStatus.ARCHIVED)
    elif show == "cancelled":
        periods = periods.filter(status=PeriodStatus.CANCELLED)
    elif show != "all":
        periods = periods.exclude(status__in=[PeriodStatus.ARCHIVED, PeriodStatus.CANCELLED])
    counts = {s: ExamPeriod.objects.filter(status=s).count() for s in (PeriodStatus.ARCHIVED, PeriodStatus.CANCELLED)}
    return render(request, "exams/period_list.html", {"periods": periods, "show": show, "counts": counts})


class PeriodDetailsForm(forms.ModelForm):
    class Meta:
        model = ExamPeriod
        fields = ["name", "term", "exam_type", "start_date", "end_date", "departments", "remarks"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "departments": forms.CheckboxSelectMultiple,
            "remarks": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["exam_type"].queryset = ExamType.objects.filter(is_active=True) | ExamType.objects.filter(pk=self.instance.exam_type_id or 0)
        if not self.instance.is_draft:
            # Dates shape the schedule; once published they stay as they are.
            for name in ("start_date", "end_date"):
                self.fields[name].disabled = True


@admin_only
def period_details(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    if period.is_finished:
        messages.error(request, f"This period is {period.get_status_display().lower()}; its details can no longer change.")
        return redirect("period_dashboard", pk=pk)
    form = PeriodDetailsForm(request.POST or None, instance=period)
    if request.method == "POST" and form.is_valid():
        form.save()
        log(request.user, "period.details", period, after={"changed": form.changed_data})
        messages.success(request, "Details saved.")
        return redirect("period_dashboard", pk=pk)
    return render(request, "exams/period_details.html", {"period": period, "form": form})


@admin_only
@require_POST
def period_status(request, pk):
    from .lifecycle import LifecycleError, archive, cancel, complete, unarchive

    period = get_object_or_404(ExamPeriod, pk=pk)
    action = request.POST.get("action")
    try:
        if action == "complete":
            complete(period, request.user)
        elif action == "archive":
            archive(period, request.user)
        elif action == "unarchive":
            unarchive(period, request.user)
        elif action == "cancel":
            cancel(period, request.user, request.POST.get("reason", ""))
        else:
            raise LifecycleError("Unknown action.")
    except LifecycleError as e:
        messages.error(request, str(e))
    else:
        period.refresh_from_db()
        messages.success(request, f"{period.name} is now {period.get_status_display().lower()}.")
    return redirect("period_dashboard", pk=pk)


def _slots_with_rooms(period):
    """Slots of the period, each with its rooms and the names assigned to them. Courses in a
    shared hall each show the hall's whole team and the other courses sitting with them."""
    duties = Duty.objects.active().select_related("faculty")
    rooms = RoomAllocation.objects.order_by("room").prefetch_related(Prefetch("duties", queryset=duties))
    sessions = ExamSession.objects.order_by("course_code").prefetch_related(Prefetch("rooms", queryset=rooms))
    slots = period.slots.prefetch_related(Prefetch("sessions", queryset=sessions))
    result = []
    for slot in slots:
        entries = [(session, room) for session in slot.sessions.all() for room in session.rooms.all()]
        halls = halls_by_key(room for _, room in entries)
        rows = []
        for session, room in entries:
            hall = halls[hall_key(room)]
            names = sorted(d.faculty.name for r in hall for d in r.duties.all())
            needed = sum(r.invigilators_required for r in hall)
            rows.append({
                "session": session,
                "room": room,
                "names": names,
                "needed": needed,
                "short": len(names) < needed,
                "shared_with": [r.session.course_code for r in hall if r is not room],
            })
        result.append({"slot": slot, "rows": rows})
    return result


def _fairness(period, load):
    """Stats for eligible faculty without a category rule, and (label, rule, stats) per category
    that has its own rule for this period."""
    from workload.models import CategoryRule

    ruled = {r.category_id: r for r in CategoryRule.objects.filter(period=period).select_related("category")}
    groups = defaultdict(list)
    for f, n in load:
        if f.is_active and not f.is_exempt:
            groups[f.category_id if f.category_id in ruled else None].append(n)
    capped = [
        (r.category.name, _rule_text(r), fairness_stats(groups[cid]))
        for cid, r in sorted(ruled.items(), key=lambda item: (item[1].category.order, item[1].category.name))
        if groups[cid]
    ]
    return fairness_stats(groups[None]), capped


def _rule_text(rule):
    parts = []
    if rule.per_course:
        parts.append(f"{rule.per_course} per course")
    elif rule.target is not None:
        parts.append(f"{rule.get_kind_display() or 'about'} {rule.target}".strip())
    if rule.max_duties is not None:
        parts.append(f"max {rule.max_duties}")
    if rule.min_duties is not None:
        parts.append(f"min {rule.min_duties}")
    return ", ".join(parts) or "no numbers set"


def _workload_summary(period, loads):
    from workload.calc import Flag, summary
    from workload.models import WorkloadPolicy

    policy = WorkloadPolicy.for_period(period)
    counts = summary(loads)
    return {
        "policy": policy,
        "category_rules": period.category_rules.count(),
        "faculty_rules": period.faculty_rules.count(),
        "no_target": counts[Flag.NO_TARGET],
        "below": counts[Flag.BELOW],
        "over": counts[Flag.OVER],
        "at": counts[Flag.AT],
        "eligible": sum(1 for x in loads if Flag.EXEMPT not in x.flags and Flag.NOT_ELIGIBLE not in x.flags),
    }


@admin_only
def period_dashboard(request, pk):
    from allocation.services import engine_people
    from workload.calc import period_loads

    period = get_object_or_404(ExamPeriod.objects.select_related("settings"), pk=pk)
    people = engine_people(period)  # worked out once and shared by the checks below
    loads = period_loads(period)
    capacity = capacity_report(period, people=people)
    slot_names = {s.id: str(s) for s in period.slots.all()}
    stats, capped_stats = _fairness(period, [(x.faculty, x.assigned) for x in loads])
    context = {
        "period": period,
        "settings": period.settings,
        "workload": _workload_summary(period, loads),
        "capacity": capacity,
        "pending": pending_capacity(period, people=people),
        "conflicts": roster_conflicts(period, people=people),
        "swaps_waiting": SwapRequest.objects.filter(duty__period=period, status=SwapStatus.PENDING_ADMIN).count(),
        "slot_shortages": [(slot_names[sid], needed, avail) for sid, needed, avail in capacity.slot_shortages],
        "stats": stats,
        "capped_stats": capped_stats,
        "unfilled": unfilled_rooms(period),
        "slots": _slots_with_rooms(period),
        "duty_count": Duty.objects.active().filter(period=period).count(),
        "editable": period.is_draft,
        "lifecycle": lifecycle_actions(period),
    }
    return render(request, "exams/dashboard.html", context)


@admin_only
@require_POST
def open_availability_view(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    try:
        deadline = timezone.make_aware(datetime.strptime(request.POST.get("deadline", ""), "%Y-%m-%dT%H:%M"))
    except ValueError:
        messages.error(request, "Choose a deadline date and time.")
        return redirect("period_dashboard", pk=pk)
    try:
        count = open_availability(period, deadline, request.user)
    except (AvailabilityError, PeriodLockedError) as e:
        messages.error(request, str(e))
    else:
        messages.success(
            request,
            f"Availability is open until {timezone.localtime(deadline):%d %b %Y %H:%M}. {count} faculty will be emailed.",
        )
    return redirect("period_dashboard", pk=pk)


@admin_only
@require_POST
def close_availability_view(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    try:
        close_availability(period, request.user)
    except PeriodLockedError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, "Availability closed. Faculty can no longer submit requests.")
    return redirect("period_dashboard", pk=pk)


def _import_view(request, title, columns, run, notes="", back_url=None):
    context = {"title": title, "columns": columns, "notes": notes, "back_url": back_url}
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            context["error"] = "Choose a file to upload."
        else:
            try:
                context["result"] = run(upload)
            except ValueError as e:
                context["error"] = str(e)
    return render(request, "exams/import.html", context)


@admin_only
def schedule_import(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    try:
        period.ensure_editable()
    except PeriodLockedError as e:
        messages.error(request, str(e))
        return redirect("period_dashboard", pk=pk)
    return redirect("import_start_period", kind="schedule", period_pk=pk)


@admin_only
def faculty_import(request):
    return redirect("import_start", kind="faculty")


@admin_only
def department_import(request):
    return _import_view(
        request, "Import departments", ["name"], import_departments, notes="Departments that already exist are skipped."
    )
