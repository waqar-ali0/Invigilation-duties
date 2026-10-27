from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from accounts.models import Role
from accounts.permissions import role_required
from audit.log import log

from .forms import CategoryForm, FacultyForm
from .models import Department, Faculty, FacultyCategory

admin_only = role_required(Role.ADMIN)

CONTACT_FILTERS = {
    "missing": ("No phone number", Q(phone="")),
    "invalid": ("Invalid phone number", ~Q(phone="") & Q(phone_e164="")),
    "no_email": ("No email", Q(email__isnull=True)),
}
STATUS_FILTERS = {
    "active": ("Active", Q(is_active=True, is_exempt=False)),
    "exempt": ("Exempt", Q(is_exempt=True)),
    "inactive": ("Inactive", Q(is_active=False)),
}


@admin_only
def faculty_list(request):
    qs = Faculty.objects.select_related("department", "category")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(employee_id__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
            | Q(phone_e164__icontains=q.replace(" ", ""))
        )
    filters = {
        "department": request.GET.get("department", ""),
        "category": request.GET.get("category", ""),
        "status": request.GET.get("status", ""),
        "contact": request.GET.get("contact", ""),
    }
    if filters["department"].isdigit():
        qs = qs.filter(department_id=filters["department"])
    if filters["category"].isdigit():
        qs = qs.filter(category_id=filters["category"])
    if filters["status"] in STATUS_FILTERS:
        qs = qs.filter(STATUS_FILTERS[filters["status"]][1])
    if filters["contact"] in CONTACT_FILTERS:
        qs = qs.filter(CONTACT_FILTERS[filters["contact"]][1])
    sort = request.GET.get("sort", "name")
    orderings = {"name": "name", "id": "employee_id", "department": "department__name", "category": "category__order"}
    qs = qs.order_by(orderings.get(sort.lstrip("-"), "name") if not sort.startswith("-") else "-" + orderings.get(sort[1:], "name"), "name")
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    context = {
        "page": page,
        "q": q,
        "filters": filters,
        "sort": sort,
        "querystring": params.urlencode(),
        "departments": Department.objects.all(),
        "categories": FacultyCategory.objects.all(),
        "status_filters": [(k, v[0]) for k, v in STATUS_FILTERS.items()],
        "contact_filters": [(k, v[0]) for k, v in CONTACT_FILTERS.items()],
        "needs_fixing": Faculty.objects.filter(CONTACT_FILTERS["invalid"][1] | CONTACT_FILTERS["missing"][1]).count(),
        "total": Faculty.objects.count(),
    }
    return render(request, "faculty/list.html", context)


@admin_only
def faculty_detail(request, pk):
    from duties.models import Duty

    faculty = get_object_or_404(Faculty.objects.select_related("department", "category", "user", "whatsapp").prefetch_related("courses"), pk=pk)
    duties = (
        Duty.objects.filter(faculty=faculty)
        .select_related("period", "room_allocation__session__slot", "room_allocation__room_ref__building", "replaces__faculty")
        .order_by("-room_allocation__session__slot__date", "-room_allocation__session__slot__start_time")
    )
    by_period = {}
    for d in duties:
        by_period.setdefault(d.period, []).append(d)
    summary = []
    for period, items in by_period.items():
        counts = {"assigned": 0, "completed": 0, "absent": 0, "cancelled": 0}
        for d in items:
            counts[d.status] = counts.get(d.status, 0) + 1
        summary.append((period, items, counts))
    return render(request, "faculty/detail.html", {"faculty": faculty, "summary": summary})


def _save_form(request, form, created):
    faculty = form.save()
    log(request.user, "faculty.create" if created else "faculty.update", faculty, after={"changed": form.changed_data})
    messages.success(request, f"{faculty.name} ({faculty.employee_id}) saved.")
    return redirect("faculty_detail", pk=faculty.pk)


@admin_only
def faculty_create(request):
    form = FacultyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        return _save_form(request, form, True)
    return render(request, "faculty/form.html", {"form": form, "title": "Add faculty member"})


@admin_only
def faculty_edit(request, pk):
    faculty = get_object_or_404(Faculty, pk=pk)
    form = FacultyForm(request.POST or None, instance=faculty)
    if request.method == "POST" and form.is_valid():
        return _save_form(request, form, False)
    return render(request, "faculty/form.html", {"form": form, "title": f"Edit {faculty.name}", "faculty": faculty})


@admin_only
def categories(request):
    editing = None
    if request.GET.get("edit", "").isdigit():
        editing = get_object_or_404(FacultyCategory, pk=request.GET["edit"])
    form = CategoryForm(request.POST or None, instance=editing)
    if request.method == "POST" and form.is_valid():
        category = form.save(commit=False)
        if not category.code:
            code = slugify(category.name).replace("-", "_")[:36] or "category"
            base, n = code, 2
            while FacultyCategory.objects.filter(code=code).exists():
                code, n = f"{base}_{n}", n + 1
            category.code = code
        category.save()
        log(request.user, "category.update" if editing else "category.create", category, after={"name": category.name, "active": category.is_active})
        messages.success(request, f"Designation “{category.name}” saved.")
        return redirect("faculty_categories")
    rows = FacultyCategory.objects.annotate(members=Count("faculty"))
    return render(request, "faculty/categories.html", {"form": form, "editing": editing, "rows": rows})
