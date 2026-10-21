from datetime import date
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.http import Http404, HttpResponse
from django.utils.text import slugify
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import faculty_required, hod_required, role_required
from duties.attendance import AttendanceError, mark_attendance, mark_room_present
from duties.handover import handover_candidates
from duties.models import OPEN_SWAP_STATUSES, Duty, DutyStatus, SwapRequest, SwapStatus, Unavailability, UnavailabilityStatus
from duties.swaps import (
    SwapError,
    accept_swap,
    approve_swap,
    cancel_swap,
    cutoff_passed,
    decline_swap,
    expire_if_started,
    reject_swap,
    request_swap,
)
from duties.unavailability import RequestRejected, can_decide, decide_request, submit_request, withdraw_request
from exams.models import ExamPeriod, PeriodLockedError, PeriodStatus, RoomAllocation, TimeSlot
from faculty.models import Department, Faculty
from reports.excel import department_rows, department_xlsx
from reports.personal import VISIBLE_STATUSES, duties_ics, duties_pdf, faculty_duties

from .forms import RequestForm


def home(request):
    user = request.user
    if not user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if user.is_superuser or user.role == Role.ADMIN:
        return redirect("period_list")
    if user.role in (Role.FACULTY, Role.HOD):
        return redirect("my_duties")
    if user.role == Role.EXAM_STAFF:
        return redirect("staff_home")
    raise PermissionDenied


@faculty_required
def my_duties(request):
    today = timezone.localdate()
    open_swaps = {
        s.duty_id: s for s in SwapRequest.objects.filter(requested_by=request.faculty, status__in=OPEN_SWAP_STATUSES)
    }
    periods = []
    for period, duties in groupby(faculty_duties(request.faculty), key=lambda d: d.period):
        duties = list(duties)
        for d in duties:
            d.open_swap = open_swaps.get(d.pk)
            d.can_swap = (
                period.status == PeriodStatus.PUBLISHED
                and d.status == DutyStatus.ASSIGNED
                and d.open_swap is None
                and not cutoff_passed(d)
            )
        upcoming = [d for d in duties if d.room_allocation.session.slot.date >= today]
        past = [d for d in duties if d.room_allocation.session.slot.date < today]
        periods.append({"period": period, "upcoming": upcoming, "past": past})
    periods.sort(key=lambda p: p["period"].start_date, reverse=True)
    return render(request, "portal/my_duties.html", {"periods": periods})


def _visible_period(pk):
    return get_object_or_404(ExamPeriod, pk=pk, status__in=VISIBLE_STATUSES)


def _attachment(content, filename, content_type):
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@faculty_required
def my_duties_pdf(request, period_id):
    period = _visible_period(period_id)
    return _attachment(duties_pdf(request.faculty, period), f"{slugify(period.name)}-my-duties.pdf", "application/pdf")


@faculty_required
def my_duties_ics(request, period_id):
    period = _visible_period(period_id)
    text = duties_ics(request.faculty, faculty_duties(request.faculty, period))
    return _attachment(text, f"{slugify(period.name)}-duties.ics", "text/calendar; charset=utf-8")


@hod_required
def department_duties(request):
    periods = list(ExamPeriod.objects.filter(status__in=VISIBLE_STATUSES).order_by("-start_date"))
    selected = next((p for p in periods if str(p.pk) == request.GET.get("period")), periods[0] if periods else None)
    rows = department_rows(selected, request.hod_departments) if selected else []
    context = {"periods": periods, "selected": selected, "rows": rows, "departments": request.hod_departments}
    return render(request, "portal/department_duties.html", context)


@hod_required
def department_duties_xlsx(request, period_id):
    period = _visible_period(period_id)
    return _attachment(
        department_xlsx(period, request.hod_departments),
        f"{slugify(period.name)}-department-duties.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@faculty_required
def my_unavailability(request):
    faculty = request.faculty
    open_periods = [p for p in ExamPeriod.objects.filter(status=PeriodStatus.AVAILABILITY_OPEN) if p.availability_is_open]
    forms_by_period = {p.pk: RequestForm(p) for p in open_periods}
    if request.method == "POST" and not faculty.is_exempt:
        period = next((p for p in open_periods if str(p.pk) == request.POST.get("period")), None)
        if period is None:
            messages.error(request, "The availability window for this period is closed.")
            return redirect("my_unavailability")
        form = forms_by_period[period.pk] = RequestForm(period, request.POST)
        if form.is_valid():
            try:
                submit_request(faculty, period, form.cleaned_data["date"], form.cleaned_data["slot"], form.cleaned_data["reason"])
            except RequestRejected as e:
                for error in e.errors:
                    form.add_error(None, error)
            else:
                messages.success(request, "Request sent to your head of department.")
                return redirect("my_unavailability")
    context = {
        "faculty": faculty,
        "forms": [(p, forms_by_period[p.pk]) for p in open_periods],
        "requests": Unavailability.objects.filter(faculty=faculty)
        .select_related("period", "slot")
        .order_by("-period__start_date", "date"),
    }
    return render(request, "portal/my_unavailability.html", context)


@faculty_required
@require_POST
def withdraw_request_view(request, pk):
    req = get_object_or_404(Unavailability, pk=pk, faculty=request.faculty)
    try:
        withdraw_request(req, request.faculty)
    except RequestRejected as e:
        messages.error(request, " ".join(e.errors))
    else:
        messages.success(request, "Request withdrawn.")
    return redirect("my_unavailability")


def _requests_qs():
    return Unavailability.objects.select_related("faculty__department", "period", "slot", "reviewed_by").order_by(
        "created_at", "date"
    )


DECIDED = [UnavailabilityStatus.APPROVED, UnavailabilityStatus.REJECTED]


@hod_required
def department_requests(request):
    mine = _requests_qs().filter(faculty__department__in=request.hod_departments).exclude(faculty=request.faculty)
    context = {
        "pending": mine.filter(status=UnavailabilityStatus.PENDING),
        "decided": mine.filter(status__in=DECIDED).exclude(period__status__in=["published", "closed"]).order_by("-reviewed_at"),
        "departments": request.hod_departments,
    }
    return render(request, "portal/department_requests.html", context)


@role_required(Role.ADMIN)
def admin_requests(request):
    hod_ids = set(Department.objects.exclude(hod=None).values_list("hod_id", flat=True))
    pending = list(_requests_qs().filter(status=UnavailabilityStatus.PENDING))
    for r in pending:
        r.is_hod_request = r.faculty_id in hod_ids
    pending.sort(key=lambda r: (not r.is_hod_request, r.created_at or timezone.now()))
    return render(request, "portal/admin_requests.html", {"pending": pending})


@role_required(Role.ADMIN, Role.HOD)
@require_POST
def decide_request_view(request, pk):
    req = get_object_or_404(_requests_qs(), pk=pk)
    back = "admin_requests" if request.user.is_superuser or request.user.role == Role.ADMIN else "department_requests"
    if not can_decide(request.user, req):
        raise Http404
    decision = request.POST.get("decision")
    if decision not in ("approve", "reject"):
        messages.error(request, "Choose approve or reject.")
        return redirect(back)
    try:
        decide_request(req, request.user, approve=decision == "approve", comment=request.POST.get("comment", ""))
    except (RequestRejected, PeriodLockedError) as e:
        messages.error(request, " ".join(e.errors) if isinstance(e, RequestRejected) else str(e))
    else:
        messages.success(request, f"Request from {req.faculty.name} {'approved' if decision == 'approve' else 'rejected'}.")
    return redirect(back)


def _swap_message(request, e):
    messages.error(request, " ".join(e.errors))


@faculty_required
def ask_colleague(request, duty_id):
    duty = get_object_or_404(
        Duty.objects.active().select_related("room_allocation__session__slot", "period"),
        pk=duty_id,
        faculty=request.faculty,
        period__status=PeriodStatus.PUBLISHED,
    )
    if request.method == "POST":
        colleague = get_object_or_404(Faculty, pk=request.POST.get("colleague_id") or 0)
        try:
            request_swap(duty, colleague, request.POST.get("message", ""))
        except SwapError as e:
            _swap_message(request, e)
            return redirect("ask_colleague", duty_id=duty.pk)
        messages.success(request, f"Request sent to {colleague.name}.")
        return redirect("swap_requests")
    context = {
        "duty": duty,
        "too_late": cutoff_passed(duty),
        "candidates": handover_candidates(duty, require_account=True),
    }
    return render(request, "portal/ask_colleague.html", context)


@faculty_required
def swap_requests(request):
    me = request.faculty
    related = "duty__room_allocation__session__slot", "requested_by", "requested_with"
    incoming = [expire_if_started(s) for s in SwapRequest.objects.filter(requested_with=me).select_related(*related)]
    outgoing = [expire_if_started(s) for s in SwapRequest.objects.filter(requested_by=me).select_related(*related)]
    context = {
        "incoming_open": [s for s in incoming if s.status == SwapStatus.PENDING_PEER],
        "incoming_other": [s for s in incoming if s.status != SwapStatus.PENDING_PEER],
        "outgoing": outgoing,
    }
    return render(request, "portal/swap_requests.html", context)


@faculty_required
@require_POST
def swap_respond(request, pk):
    swap = get_object_or_404(SwapRequest, pk=pk, requested_with=request.faculty)
    action = request.POST.get("action")
    try:
        if action == "accept":
            accept_swap(swap, request.faculty)
            messages.success(request, "Accepted. The exam office will make the final decision.")
        elif action == "decline":
            decline_swap(swap, request.faculty)
            messages.success(request, "Declined.")
        else:
            messages.error(request, "Choose accept or decline.")
    except SwapError as e:
        _swap_message(request, e)
    return redirect("swap_requests")


@faculty_required
@require_POST
def swap_cancel(request, pk):
    swap = get_object_or_404(SwapRequest, pk=pk, requested_by=request.faculty)
    try:
        cancel_swap(swap, request.faculty)
    except SwapError as e:
        _swap_message(request, e)
    else:
        messages.success(request, "Request cancelled.")
    return redirect("swap_requests")


@role_required(Role.ADMIN)
def admin_swaps(request):
    related = "duty__room_allocation__session__slot", "requested_by", "requested_with", "decided_by"
    waiting = [
        expire_if_started(s)
        for s in SwapRequest.objects.filter(status=SwapStatus.PENDING_ADMIN).select_related(*related).order_by("responded_at")
    ]
    context = {
        "waiting": [s for s in waiting if s.status == SwapStatus.PENDING_ADMIN],
        "recent": SwapRequest.objects.filter(status__in=[SwapStatus.APPROVED, SwapStatus.REJECTED])
        .select_related(*related)
        .order_by("-decided_at")[:50],
    }
    return render(request, "portal/admin_swaps.html", context)


@role_required(Role.ADMIN)
@require_POST
def swap_decide(request, pk):
    swap = get_object_or_404(SwapRequest, pk=pk)
    decision = request.POST.get("decision")
    try:
        if decision == "approve":
            approve_swap(swap, request.user)
            messages.success(request, f"Swap approved: {swap.requested_with.name} now has the duty.")
        elif decision == "reject":
            reject_swap(swap, request.user, request.POST.get("comment", ""))
            messages.success(request, "Swap rejected.")
        else:
            messages.error(request, "Choose approve or reject.")
    except SwapError as e:
        _swap_message(request, e)
    return redirect("admin_swaps")


@role_required(Role.EXAM_STAFF)
def staff_home(request):
    return redirect("attendance")


attendance_roles = role_required(Role.EXAM_STAFF, Role.ADMIN)


@attendance_roles
def attendance(request):
    day = timezone.localdate()
    if request.GET.get("date"):
        try:
            day = date.fromisoformat(request.GET["date"])
        except ValueError:
            messages.error(request, "Choose a valid date.")
    slots = TimeSlot.objects.filter(period__status=PeriodStatus.PUBLISHED, date=day).select_related("period").order_by(
        "start_time"
    )
    return render(request, "portal/attendance.html", {"day": day, "slots": slots})


@attendance_roles
def attendance_slot(request, slot_id):
    slot = get_object_or_404(TimeSlot.objects.select_related("period"), pk=slot_id, period__status=PeriodStatus.PUBLISHED)
    duties = Duty.objects.active().select_related("faculty", "attendance_marked_by").order_by("faculty__name")
    rooms = (
        RoomAllocation.objects.filter(session__slot=slot)
        .select_related("session")
        .prefetch_related(Prefetch("duties", queryset=duties, to_attr="active_duties"))
        .order_by("room")
    )
    context = {"slot": slot, "rooms": rooms, "started": timezone.now() >= slot.starts_at}
    return render(request, "portal/attendance_slot.html", context)


@attendance_roles
@require_POST
def attendance_mark(request, duty_id):
    duty = get_object_or_404(Duty.objects.select_related("room_allocation__session"), pk=duty_id)
    try:
        mark_attendance(duty, request.user, present=request.POST.get("present") == "1")
    except (AttendanceError, PeriodLockedError) as e:
        messages.error(request, str(e))
    return redirect("attendance_slot", slot_id=duty.room_allocation.session.slot_id)


@attendance_roles
@require_POST
def attendance_room_present(request, room_id):
    room = get_object_or_404(RoomAllocation.objects.select_related("session"), pk=room_id)
    try:
        count = mark_room_present(room, request.user)
    except (AttendanceError, PeriodLockedError) as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f"{room.room}: {count} marked present.")
    return redirect("attendance_slot", slot_id=room.session.slot_id)
