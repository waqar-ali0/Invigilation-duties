"""Extra pages for heads of department, switched on or off by the exam office (HOD permissions).
Every page is limited to the departments the HOD heads, and every permission is checked here on
the server, not only by hiding links."""

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from accounts.models import HodAccess, Role
from accounts.permissions import hod_required, role_required
from audit.log import log
from duties.missed import MissedDutyError, decide_absence
from duties.models import Duty, DutyStatus
from exams.models import ExamPeriod, PeriodStatus
from reports.center import REPORTS, Filters, to_csv, to_pdf, to_xlsx
from workload.calc import period_loads

# Reports a HOD may open: everything about people and duties, nothing about other departments.
HOD_REPORTS = ("faculty", "rooms", "slots", "workload", "assigned", "missed", "obligations", "outstanding", "over", "preferences", "visiting")
VISIBLE = (PeriodStatus.ALLOCATING, PeriodStatus.PUBLISHED, PeriodStatus.CLOSED, PeriodStatus.ARCHIVED)


def _require(flag):
    if not getattr(HodAccess.get(), flag):
        raise PermissionDenied


def _period(request):
    periods = list(ExamPeriod.objects.filter(status__in=VISIBLE).order_by("-start_date"))
    pk = request.GET.get("period", "")
    chosen = next((p for p in periods if str(p.pk) == pk), periods[0] if periods else None)
    return periods, chosen


def _department(request):
    depts = list(request.hod_departments)
    pk = request.GET.get("department", "")
    return depts, next((d for d in depts if str(d.pk) == pk), depts[0])


@hod_required
def department_workload(request):
    _require("see_workload")
    periods, period = _period(request)
    depts = set(request.hod_departments.values_list("pk", flat=True))
    loads = [x for x in period_loads(period) if x.faculty.department_id in depts] if period else []
    return render(request, "portal/hod_workload.html", {
        "periods": periods, "period": period, "loads": loads, "departments": request.hod_departments,
        "access": HodAccess.get(),
    })


@hod_required
def department_missed(request):
    _require("see_missed")
    access = HodAccess.get()
    duties = Duty.objects.filter(status=DutyStatus.ABSENT, faculty__department__in=request.hod_departments)
    if request.method == "POST":
        _require("decide_absences")
        duty = get_object_or_404(duties, pk=request.POST.get("duty") or 0)
        if duty.faculty_id == request.faculty.pk:
            raise PermissionDenied  # never decide on your own absence
        try:
            decide_absence(duty, request.user, request.POST.get("reason", ""), excused=request.POST.get("excused") == "yes",
                           owe_makeup=bool(getattr(duty, "obligation", None) and not duty.obligation.waived))
        except MissedDutyError as e:
            messages.error(request, str(e))
        else:
            messages.success(request, f"Decision recorded for {duty.faculty.name}. The exam office decides about make-up duties.")
        return redirect("hod_missed")
    rows = duties.select_related("faculty", "period", "room_allocation__session__slot", "absence_decided_by").order_by("-room_allocation__session__slot__date")
    return render(request, "portal/hod_missed.html", {"rows": rows[:200], "access": access, "departments": request.hod_departments})


@hod_required
def department_reports(request):
    _require("download_reports")
    depts, dept = _department(request)
    key = request.GET.get("type", "workload")
    if key not in HOD_REPORTS:
        key = "workload"
    filters = Filters.from_query(request.GET)
    filters.department = dept.pk  # always limited to their own department
    _, default_period = _period(request)
    if filters.period is None:
        filters.period = default_period
    elif filters.period.status not in VISIBLE:
        raise PermissionDenied
    title, build = REPORTS[key]
    report = build(filters)
    fmt = request.GET.get("format", "")
    if fmt in ("csv", "xlsx", "pdf"):
        content = {"csv": to_csv, "xlsx": to_xlsx}.get(fmt, lambda r: to_pdf(r, f"{dept.name} · {filters.period.name if filters.period else ''}"))(report)
        types = {"csv": "text/csv; charset=utf-8", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pdf": "application/pdf"}
        response = HttpResponse(content, content_type=types[fmt])
        response["Content-Disposition"] = f'attachment; filename="{slugify(dept.name + " " + title)[:80]}.{fmt}"'
        log(request.user, "report.download", dept, after={"report": key, "format": fmt})
        return response
    params = request.GET.copy()
    params.pop("format", None)
    return render(request, "portal/hod_reports.html", {
        "key": key, "reports": [(k, REPORTS[k][0]) for k in HOD_REPORTS], "report": report, "rows": report.rows[:200],
        "departments": depts, "department": dept, "periods": ExamPeriod.objects.filter(status__in=VISIBLE).order_by("-start_date"),
        "f": filters, "querystring": params.urlencode(),
    })


class HodAccessForm(forms.ModelForm):
    class Meta:
        model = HodAccess
        fields = ["see_workload", "see_missed", "decide_absences", "see_contacts", "download_reports"]


@role_required(Role.ADMIN)
def access_settings(request):
    access = HodAccess.get()
    form = HodAccessForm(request.POST or None, instance=access)
    if request.method == "POST" and form.is_valid():
        form.save()
        log(request.user, "settings.hod_access", access, after={f: form.cleaned_data[f] for f in form.fields})
        messages.success(request, "HOD permissions saved. They apply immediately.")
        return redirect("hod_access")
    return render(request, "portal/hod_access.html", {"form": form})
