from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import role_required
from exams.models import ExamPeriod, PeriodStatus
from faculty.models import Department, FacultyCategory

from .calc import Flag, period_loads, summary
from .forms import CategoryRuleForm, CopyForm, DefaultsForm, FacultyRuleForm, PolicyForm
from .models import CategoryRule, FacultyRule, WorkloadPolicy, WorkloadVersion
from .services import confirm, copy_rules, rules_changed

admin_only = role_required(Role.ADMIN)


def _category_forms(period, data=None):
    forms = []
    existing = {r.category_id: r for r in CategoryRule.objects.filter(period=period)}
    for category in FacultyCategory.objects.filter(is_active=True) | FacultyCategory.objects.filter(pk__in=existing):
        rule = existing.get(category.pk) or CategoryRule(period=period, category=category)
        forms.append((category, CategoryRuleForm(data, instance=rule, prefix=f"c{category.pk}")))
    return forms


def _editable(period):
    return period.status != PeriodStatus.CLOSED


@admin_only
def rules_page(request, pk):
    period = get_object_or_404(ExamPeriod.objects.select_related("settings"), pk=pk)
    policy = WorkloadPolicy.for_period(period)
    action = request.POST.get("action") if request.method == "POST" else None
    if action and not _editable(period):
        messages.error(request, "This exam period is closed; its rules can no longer change.")
        return redirect("workload_rules", pk=pk)

    defaults = DefaultsForm(request.POST if action == "defaults" else None, instance=period.settings, prefix="d")
    categories = _category_forms(period, request.POST if action == "categories" else None)
    policy_form = PolicyForm(request.POST if action == "policy" else None, instance=policy, prefix="p")
    person_form = FacultyRuleForm(request.POST if action == "person" else None, period=period, prefix="f")
    copy_form = CopyForm(request.POST if action == "copy" else None, period=period, prefix="copy")

    if action == "defaults" and defaults.is_valid():
        defaults.save()
        rules_changed(period, request.user, "defaults")
        messages.success(request, "Defaults saved.")
        return redirect("workload_rules", pk=pk)
    if action == "categories":
        if all(f.is_valid() for _, f in categories):
            with transaction.atomic():
                for category, form in categories:
                    if form.is_empty():
                        if form.instance.pk:
                            form.instance.delete()
                    else:
                        form.save()
                rules_changed(period, request.user, "designation rules")
            messages.success(request, "Designation rules saved.")
            return redirect("workload_rules", pk=pk)
        messages.error(request, "Some designation rules need correcting (see below).")
    if action == "policy" and policy_form.is_valid():
        policy_form.save()
        rules_changed(period, request.user, "counting policy")
        messages.success(request, "Counting rules saved.")
        return redirect("workload_rules", pk=pk)
    if action == "person":
        if person_form.is_valid():
            rule = person_form.save(commit=False)
            rule.period, rule.updated_by = period, request.user
            rule.save()
            rules_changed(period, request.user, f"personal rule for {rule.faculty.name}")
            messages.success(request, f"Rule for {rule.faculty.name} saved.")
            return redirect("workload_rules", pk=pk)
        messages.error(request, "The personal rule needs correcting (see below).")
    if action == "copy" and copy_form.is_valid():
        copy_rules(copy_form.cleaned_data["source"], period, request.user)
        messages.success(request, f"Rules copied from {copy_form.cleaned_data['source'].name}. Review them, then confirm.")
        return redirect("workload_rules", pk=pk)
    if action == "confirm":
        version = confirm(period, request.user)
        messages.success(request, f"Workload rules confirmed (version {version.number}).")
        return redirect("workload_rules", pk=pk)

    loads = period_loads(period)
    counts = summary(loads)
    context = {
        "period": period,
        "policy": policy,
        "defaults": defaults,
        "categories": categories,
        "policy_form": policy_form,
        "person_form": person_form,
        "copy_form": copy_form,
        "personal": FacultyRule.objects.filter(period=period).select_related("faculty__category", "updated_by"),
        "versions": WorkloadVersion.objects.filter(period=period).select_related("created_by")[:20],
        "editable": _editable(period),
        "no_target": counts[Flag.NO_TARGET],
        "eligible": sum(1 for x in loads if Flag.EXEMPT not in x.flags and Flag.NOT_ELIGIBLE not in x.flags),
        "missing_by_category": _missing_targets(loads),
        "seats": sum(r.invigilators_required for r in _rooms(period)),
    }
    return render(request, "workload/rules.html", context)


def _rooms(period):
    from exams.models import RoomAllocation

    return RoomAllocation.objects.filter(session__slot__period=period).only("invigilators_required")


def _missing_targets(loads):
    out = {}
    for load in loads:
        if Flag.NO_TARGET in load.flags:
            out[load.faculty.category.name] = out.get(load.faculty.category.name, 0) + 1
    return sorted(out.items())


@admin_only
def person_rule(request, pk, rule_id):
    period = get_object_or_404(ExamPeriod, pk=pk)
    rule = get_object_or_404(FacultyRule.objects.select_related("faculty"), pk=rule_id, period=period)
    if request.method == "POST" and not _editable(period):
        messages.error(request, "This exam period is closed; its rules can no longer change.")
        return redirect("workload_rules", pk=pk)
    if request.method == "POST" and request.POST.get("action") == "delete":
        name = rule.faculty.name
        rule.delete()
        rules_changed(period, request.user, f"personal rule for {name} removed")
        messages.success(request, f"Rule for {name} removed; their designation rule applies again.")
        return redirect("workload_rules", pk=pk)
    form = FacultyRuleForm(request.POST or None, instance=rule, period=period)
    if request.method == "POST" and form.is_valid():
        rule = form.save(commit=False)
        rule.updated_by = request.user
        rule.save()
        rules_changed(period, request.user, f"personal rule for {rule.faculty.name}")
        messages.success(request, f"Rule for {rule.faculty.name} saved.")
        return redirect("workload_rules", pk=pk)
    return render(request, "workload/person_rule.html", {"period": period, "rule": rule, "form": form, "editable": _editable(period)})


@admin_only
def version_detail(request, pk, number):
    period = get_object_or_404(ExamPeriod, pk=pk)
    version = get_object_or_404(WorkloadVersion, period=period, number=number)
    return render(request, "workload/version.html", {"period": period, "version": version, "s": version.snapshot})


FLAG_FILTERS = [
    (Flag.BELOW, "Below target"), (Flag.AT, "At target"), (Flag.NEAR_LIMIT, "Near limit"), (Flag.OVER, "Over limit"),
    (Flag.NO_TARGET, "No target set"), ("owes", "Owes a make-up duty"), (Flag.EXEMPT, "Exempt"),
]


def filtered_loads(period, params):
    loads = period_loads(period)
    dept, cat, flag, q = params.get("department", ""), params.get("category", ""), params.get("flag", ""), params.get("q", "").strip().lower()
    if dept.isdigit():
        loads = [x for x in loads if x.faculty.department_id == int(dept)]
    if cat.isdigit():
        loads = [x for x in loads if x.faculty.category_id == int(cat)]
    if flag == "owes":
        loads = [x for x in loads if x.makeup_owed]
    elif flag:
        loads = [x for x in loads if flag in x.flags]
    if q:
        loads = [x for x in loads if q in x.faculty.name.lower() or q in x.faculty.employee_id.lower()]
    return loads


@admin_only
def people(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    loads = filtered_loads(period, request.GET)
    sort = request.GET.get("sort", "name")
    keys = {
        "name": lambda x: x.faculty.name,
        "counted": lambda x: (x.counted, x.faculty.name),
        "remaining": lambda x: (x.remaining if x.remaining is not None else -1, x.faculty.name),
        "percent": lambda x: (x.percent if x.percent is not None else -1, x.faculty.name),
    }
    loads.sort(key=keys.get(sort.lstrip("-"), keys["name"]), reverse=sort.startswith("-"))
    params = request.GET.copy()
    params.pop("sort", None)
    context = {
        "period": period,
        "loads": loads,
        "flags": FLAG_FILTERS,
        "departments": Department.objects.all(),
        "categories": FacultyCategory.objects.all(),
        "f": request.GET,
        "sort": sort,
        "querystring": params.urlencode(),
        "totals": summary(period_loads(period)),
        "Flag": Flag,
    }
    return render(request, "workload/people.html", context)
