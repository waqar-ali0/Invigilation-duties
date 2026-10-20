from itertools import groupby

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.models import Role
from accounts.permissions import faculty_required, role_required
from reports.personal import faculty_duties


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


@role_required(Role.EXAM_STAFF)
def staff_home(request):
    return render(request, "portal/staff_home.html")
