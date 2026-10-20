from itertools import groupby

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.utils.text import slugify
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import faculty_required, hod_required, role_required
from duties.models import Unavailability, UnavailabilityStatus
from duties.unavailability import RequestRejected, can_decide, decide_request, submit_request, withdraw_request
from exams.models import ExamPeriod, PeriodLockedError, PeriodStatus
from faculty.models import Department
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
    periods = []
    for period, duties in groupby(faculty_duties(request.faculty), key=lambda d: d.period):
        duties = list(duties)
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


@role_required(Role.EXAM_STAFF)
def staff_home(request):
    return render(request, "portal/staff_home.html")
