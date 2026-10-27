"""Exam operations pages: confirming receipt, missed duties and make-ups, moving duties,
roster versions, notifications and duty slips."""

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import faculty_required, role_required
from audit.log import log
from duties.missed import MissedDutyError, assign_makeup, decide_absence, makeup_rooms, waive
from duties.models import Duty, DutyStatus, Obligation, RosterVersion
from duties.moves import MoveBlocked, move_duty, move_targets
from duties.versions import compare, roster_snapshot
from exams.models import ExamPeriod, PeriodLockedError, RoomAllocation
from faculty.models import Faculty
from notifications.models import Notification
from reports.personal import VISIBLE_STATUSES, duties_pdf

admin_only = role_required(Role.ADMIN)


# --- faculty: confirm receipt --------------------------------------------------------------


@faculty_required
@require_POST
def acknowledge_duties(request, period_id):
    count = Duty.objects.filter(
        faculty=request.faculty, period_id=period_id, period__status__in=VISIBLE_STATUSES,
        status=DutyStatus.ASSIGNED, acknowledged_at__isnull=True,
    ).update(acknowledged_at=timezone.now())
    if count:
        log(request.user, "duty.acknowledge", request.faculty, after={"period": period_id, "duties": count})
        messages.success(request, f"Thank you — {count} duty(ies) confirmed as seen.")
    return redirect("my_duties")


# --- missed duties and make-ups --------------------------------------------------------------

MISSED_FILTERS = {
    "undecided": ("Not decided yet", Q(absence_excused__isnull=True)),
    "excused": ("Excused", Q(absence_excused=True)),
    "unexcused": ("Unexcused", Q(absence_excused=False)),
}


@admin_only
def missed_duties(request):
    if request.method == "POST":
        duty = get_object_or_404(Duty, pk=request.POST.get("duty") or 0, status=DutyStatus.ABSENT)
        try:
            decide_absence(
                duty, request.user, request.POST.get("reason", ""),
                excused=request.POST.get("excused") == "yes",
                owe_makeup=request.POST.get("makeup") == "on",
                note=request.POST.get("note", ""),
            )
        except MissedDutyError as e:
            messages.error(request, str(e))
        else:
            messages.success(request, f"Decision recorded for {duty.faculty.name}.")
        return redirect(request.get_full_path())
    qs = (
        Duty.objects.filter(status=DutyStatus.ABSENT)
        .select_related("faculty", "period", "room_allocation__session__slot", "absence_decided_by", "obligation__makeup__room_allocation__session__slot")
        .prefetch_related("replaced_by__faculty")
        .order_by("-room_allocation__session__slot__date", "faculty__name")
    )
    period = request.GET.get("period", "")
    show = request.GET.get("show", "")
    if period.isdigit():
        qs = qs.filter(period_id=period)
    if show in MISSED_FILTERS:
        qs = qs.filter(MISSED_FILTERS[show][1])
    params = request.GET.copy()
    params.pop("page", None)
    obligations = Obligation.objects.select_related("owed_by", "original__room_allocation__session__slot", "makeup__room_allocation__session__slot")
    context = {
        "page": Paginator(qs, 30).get_page(request.GET.get("page")),
        "periods": ExamPeriod.objects.filter(status__in=VISIBLE_STATUSES),
        "period": period,
        "show": show,
        "filters": [(k, v[0]) for k, v in MISSED_FILTERS.items()],
        "querystring": params.urlencode(),
        "outstanding": [o for o in obligations if o.is_outstanding],
    }
    return render(request, "portal/missed.html", context)


@admin_only
def obligation_view(request, pk):
    obligation = get_object_or_404(
        Obligation.objects.select_related("owed_by", "original__room_allocation__session__slot", "original__period", "makeup__room_allocation__session__slot"),
        pk=pk,
    )
    person = obligation.owed_by
    if request.GET.get("faculty", "").isdigit():
        person = get_object_or_404(Faculty, pk=request.GET["faculty"])
    if request.method == "POST":
        try:
            if request.POST.get("action") == "waive":
                waive(obligation, request.user, request.POST.get("note", ""))
                messages.success(request, "Obligation waived.")
            else:
                room = get_object_or_404(RoomAllocation, pk=request.POST.get("room") or 0)
                who = get_object_or_404(Faculty, pk=request.POST.get("faculty") or obligation.owed_by_id)
                duty = assign_makeup(obligation, room, request.user, faculty=who)
                messages.success(request, f"Make-up duty scheduled for {who.name}: {duty.room_allocation.session.slot} · {duty.room_allocation.room}.")
        except (MissedDutyError, PeriodLockedError) as e:
            messages.error(request, str(e))
        return redirect("obligation", pk=pk)
    context = {
        "o": obligation,
        "person": person,
        "rooms": makeup_rooms(person) if obligation.is_outstanding and obligation.status != "assigned" else [],
        "people": Faculty.objects.filter(is_active=True, is_exempt=False).order_by("name"),
    }
    return render(request, "portal/obligation.html", context)


# --- moving a duty --------------------------------------------------------------------------


@admin_only
def move_duty_view(request, pk, duty_id):
    duty = get_object_or_404(Duty.objects.select_related("faculty", "period", "room_allocation__session__slot"), pk=duty_id, period_id=pk)
    if request.method == "POST":
        room = get_object_or_404(RoomAllocation, pk=request.POST.get("room") or 0)
        try:
            new = move_duty(duty, room, request.user, request.POST.get("reason", ""))
        except (MoveBlocked, PeriodLockedError) as e:
            messages.error(request, "; ".join(e.errors) if isinstance(e, MoveBlocked) else str(e))
            return redirect("move_duty", pk=pk, duty_id=duty_id)
        messages.success(request, f"{duty.faculty.name} moved to {new.room_allocation.room}.")
        return redirect("room_detail", pk=pk, room_id=new.room_allocation_id)
    try:
        targets = move_targets(duty)
    except PeriodLockedError:
        targets = []
    return render(request, "portal/move_duty.html", {"duty": duty, "period": duty.period, "targets": targets})


# --- roster versions ------------------------------------------------------------------------


@admin_only
def roster_versions(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    versions = list(RosterVersion.objects.filter(period=period).select_related("created_by"))
    by_number = {v.number: v for v in versions}
    a = by_number.get(int(request.GET["a"])) if request.GET.get("a", "").isdigit() else None
    b = by_number.get(int(request.GET["b"])) if request.GET.get("b", "").isdigit() else None
    if versions and a is None:
        a = by_number.get(1) or versions[-1]
    current = request.GET.get("b") in (None, "", "now")
    newer = roster_snapshot(period) if (b is None or current) else b.snapshot
    diff = compare(a.snapshot, newer) if a else None
    context = {"period": period, "versions": versions, "a": a, "b": b if not current else None, "diff": diff}
    return render(request, "portal/roster_versions.html", context)


# --- notifications ----------------------------------------------------------------------------


def notifications(request):
    if not request.user.is_authenticated:
        return redirect("login")
    qs = Notification.objects.filter(user=request.user)
    if request.method == "POST":
        qs.filter(read_at__isnull=True).update(read_at=timezone.now())
        return redirect("notifications")
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "portal/notifications.html", {"page": page, "querystring": ""})


def unread_notifications(request):
    """Context processor: the unread count for the bell in the top bar."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"unread_notifications": Notification.objects.filter(user=user, read_at__isnull=True).count()}


# --- duty slip for any faculty member -----------------------------------------------------------


@admin_only
def faculty_duty_slip(request, faculty_id, period_id):
    faculty = get_object_or_404(Faculty, pk=faculty_id)
    period = get_object_or_404(ExamPeriod, pk=period_id, status__in=VISIBLE_STATUSES)
    response = HttpResponse(duties_pdf(faculty, period), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{slugify(faculty.name)}-{slugify(period.name)}-duties.pdf"'
    return response


def hod_access(request):
    """Context processor: HOD permissions, for showing only the HOD pages that are switched on."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or user.role != Role.HOD:
        return {}
    from accounts.models import HodAccess

    return {"hod_access": HodAccess.get()}
