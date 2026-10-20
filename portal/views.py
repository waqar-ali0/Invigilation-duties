from itertools import groupby

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import faculty_required, role_required
from duties.models import Unavailability
from duties.unavailability import RequestRejected, submit_request, withdraw_request
from exams.models import ExamPeriod, PeriodStatus
from reports.personal import faculty_duties

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


@role_required(Role.EXAM_STAFF)
def staff_home(request):
    return render(request, "portal/staff_home.html")
