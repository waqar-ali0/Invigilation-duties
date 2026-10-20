from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.models import Role
from accounts.permissions import role_required
from allocation.engine import fairness_stats
from allocation.services import capacity_report, unfilled_rooms
from duties.models import Duty
from reports.queries import faculty_load

from .importers.departments import import_departments
from .importers.faculty import COLUMNS as FACULTY_COLUMNS
from .importers.faculty import import_faculty
from .importers.schedule import COLUMNS as SCHEDULE_COLUMNS
from .importers.schedule import import_schedule
from .models import ExamPeriod, ExamSession, PeriodLockedError, RoomAllocation

admin_only = role_required(Role.ADMIN)


@admin_only
def home(request):
    return redirect("period_list")


@admin_only
def period_list(request):
    return render(request, "exams/period_list.html", {"periods": ExamPeriod.objects.all()})


def _slots_with_rooms(period):
    """Slots of the period, each with its rooms and the names assigned to them."""
    duties = Duty.objects.active().select_related("faculty").order_by("faculty__name")
    rooms = RoomAllocation.objects.order_by("room").prefetch_related(Prefetch("duties", queryset=duties))
    sessions = ExamSession.objects.order_by("course_code").prefetch_related(Prefetch("rooms", queryset=rooms))
    slots = period.slots.prefetch_related(Prefetch("sessions", queryset=sessions))
    result = []
    for slot in slots:
        rows = []
        for session in slot.sessions.all():
            for room in session.rooms.all():
                names = [d.faculty.name for d in room.duties.all()]
                rows.append({"session": session, "room": room, "names": names, "short": len(names) < room.invigilators_required})
        result.append({"slot": slot, "rows": rows})
    return result


@admin_only
def period_dashboard(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    capacity = capacity_report(period)
    slot_names = {s.id: str(s) for s in period.slots.all()}
    load = faculty_load(period)
    context = {
        "period": period,
        "settings": period.settings,
        "caps": period.designation_caps.all(),
        "capacity": capacity,
        "slot_shortages": [(slot_names[sid], needed, avail) for sid, needed, avail in capacity.slot_shortages],
        "stats": fairness_stats(n for _, n in load),
        "unfilled": unfilled_rooms(period),
        "slots": _slots_with_rooms(period),
        "duty_count": Duty.objects.active().filter(period=period).count(),
        "editable": period.status not in ("published", "closed"),
    }
    return render(request, "exams/dashboard.html", context)


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
    back = reverse("period_dashboard", args=[pk])
    try:
        return _import_view(
            request,
            f"Import exam schedule — {period.name}",
            SCHEDULE_COLUMNS + ["course_teacher_emails"],
            lambda f: import_schedule(period, f),
            notes="One row per room. course_teacher_emails is optional (separate several with ;). "
            "Dates as YYYY-MM-DD or DD/MM/YYYY, times as HH:MM.",
            back_url=back,
        )
    except PeriodLockedError as e:
        messages.error(request, str(e))
        return redirect(back)


@admin_only
def faculty_import(request):
    return _import_view(
        request,
        "Import faculty",
        FACULTY_COLUMNS,
        import_faculty,
        notes="Existing faculty are matched by employee_id and updated. Designation is one of: "
        "Professor, Associate Professor, Assistant Professor, Lecturer, Visiting.",
    )


@admin_only
def department_import(request):
    return _import_view(
        request, "Import departments", ["name"], import_departments, notes="Departments that already exist are skipped."
    )
