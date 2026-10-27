from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import role_required
from exams.models import ExamPeriod, PeriodLockedError

from .base import Status, error_report, template_workbook
from .courses import CourseImport
from .faculty import FacultyImport
from .models import BatchStatus, ImportBatch
from .reader import read_table
from .rooms import RoomImport
from .schedule import ScheduleImport

KINDS = {k.key: k for k in (FacultyImport(), RoomImport(), CourseImport(), ScheduleImport())}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

admin_only = role_required(Role.ADMIN)


def _kind(key):
    if key not in KINDS:
        raise Http404("Unknown import.")
    return KINDS[key]


def _batch(pk, *statuses):
    batch = get_object_or_404(ImportBatch.objects.select_related("period", "uploaded_by"), pk=pk)
    _kind(batch.kind)
    if statuses and batch.status not in statuses:
        return batch, False
    return batch, True


@admin_only
def import_home(request):
    kinds = [k for k in KINDS.values() if not k.needs_period]
    history = ImportBatch.objects.select_related("uploaded_by", "period")[:50]
    return render(request, "imports/home.html", {"kinds": kinds, "history": history, "titles": {k: v.title for k, v in KINDS.items()}})


@admin_only
def start(request, kind, period_pk=None):
    k = _kind(kind)
    period = get_object_or_404(ExamPeriod, pk=period_pk) if k.needs_period else None
    if k.needs_period and period_pk is None:
        raise Http404("Choose the exam period first.")
    context = {"kind": k, "period": period, "history": ImportBatch.objects.filter(kind=kind, period=period)[:10]}
    if request.method == "POST":
        if period is not None:
            try:
                period.ensure_editable()
            except PeriodLockedError as e:
                messages.error(request, str(e))
                return redirect("period_dashboard", pk=period.pk)
        upload = request.FILES.get("file")
        if upload is None:
            context["error"] = "Choose a file to upload."
        else:
            try:
                headers, rows = read_table(upload)
            except ValueError as e:
                context["error"] = str(e)
            else:
                if not rows:
                    context["error"] = "The file has headings but no rows."
                else:
                    batch = ImportBatch.objects.create(
                        kind=kind,
                        filename=upload.name[:255],
                        uploaded_by=request.user,
                        headers=headers,
                        rows=rows,
                        mapping=k.guess_mapping(headers),
                        options=k.default_options(),
                        period=period,
                    )
                    return redirect("import_columns", pk=batch.pk)
    return render(request, "imports/start.html", context)


@admin_only
def template_download(request, kind):
    k = _kind(kind)
    response = HttpResponse(template_workbook(k), content_type=XLSX)
    response["Content-Disposition"] = f'attachment; filename="{kind}-template.xlsx"'
    return response


def _samples(batch, index, limit=3):
    values = []
    for _, cells in batch.rows:
        value = cells[index] if index < len(cells) else None
        if value not in (None, ""):
            values.append(str(value))
        if len(values) == limit:
            break
    return values


@admin_only
def columns(request, pk):
    batch, ok = _batch(pk, BatchStatus.MAPPING, BatchStatus.PREVIEW)
    if not ok:
        return redirect("import_batch", pk=pk)
    k = _kind(batch.kind)
    options = k.options
    if request.method == "POST":
        mapping, errors = {}, []
        for f in k.fields:
            raw = request.POST.get(f"map_{f.key}", "")
            if raw.isdigit() and int(raw) < len(batch.headers):
                mapping[f.key] = int(raw)
        used = [i for i in mapping.values()]
        if len(used) != len(set(used)):
            errors.append("Each column can be used for one field only.")
        missing = [f.label for f in k.fields if f.required and f.key not in mapping]
        if missing:
            errors.append("Choose a column for: " + ", ".join(missing) + ".")
        chosen = {}
        for o in options:
            if o.kind == "bool":
                chosen[o.name] = request.POST.get(f"opt_{o.name}") == "on"
            else:
                chosen[o.name] = request.POST.get(f"opt_{o.name}", "").strip()[:150]
        batch.mapping, batch.options = mapping, chosen
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            batch.status = BatchStatus.PREVIEW
            batch.save(update_fields=["mapping", "options", "status"])
            return redirect("import_batch", pk=pk)
    columns_list = [(i, h, _samples(batch, i)) for i, h in enumerate(batch.headers)]
    context = {
        "batch": batch,
        "kind": k,
        "fields": [(f, batch.mapping.get(f.key)) for f in k.fields],
        "columns": columns_list,
        "options": [(o, batch.options.get(o.name, o.default)) for o in options],
    }
    return render(request, "imports/columns.html", context)


@admin_only
def batch_view(request, pk):
    batch, _ = _batch(pk)
    k = _kind(batch.kind)
    if batch.status == BatchStatus.MAPPING:
        return redirect("import_columns", pk=pk)
    if batch.status in (BatchStatus.DONE, BatchStatus.CANCELLED):
        return render(request, "imports/result.html", {"batch": batch, "kind": k})
    plan = k.plan(batch)
    if request.method == "POST":
        selected = {int(n) for n in request.POST.getlist("row") if n.isdigit()}
        if not k.selectable:
            selected = {p.number for p in plan.rows}
        decisions = {}
        for p in plan.rows:
            if p.status == Status.REVIEW:
                decisions[p.number] = request.POST.get(f"decision_{p.number}", "skip")
        mode = request.POST.get("mode", k.modes[0][0])
        if mode not in dict(k.modes):
            mode = k.modes[0][0]
        try:
            if batch.period_id:
                batch.period.ensure_editable()
            result = k.commit(batch, selected, decisions, mode, request.user)
        except (ValueError, PeriodLockedError) as e:
            messages.error(request, str(e))
            return redirect("import_batch", pk=pk)
        batch.status, batch.mode, batch.result, batch.finished_at = BatchStatus.DONE, mode, result, timezone.now()
        batch.save(update_fields=["status", "mode", "result", "finished_at"])
        messages.success(request, "Import finished.")
        return redirect("import_batch", pk=pk)
    groups = [(s, label, [p for p in plan.rows if p.status == s]) for s, label in Status.LABELS.items()]
    context = {
        "batch": batch,
        "kind": k,
        "plan": plan,
        "groups": [g for g in groups if g[2]],
        "counts": plan.counts,
        "total": len(plan.rows),
        "Status": Status,
        "mapped": [(f.label, batch.headers[batch.mapping[f.key]]) for f in k.fields if f.key in batch.mapping],
    }
    return render(request, "imports/preview.html", context)


@admin_only
def batch_report(request, pk):
    batch, _ = _batch(pk)
    k = _kind(batch.kind)
    response = HttpResponse(error_report(batch, k.plan(batch)), content_type=XLSX)
    response["Content-Disposition"] = f'attachment; filename="import-check-{batch.pk}.xlsx"'
    return response


@admin_only
@require_POST
def batch_cancel(request, pk):
    batch, ok = _batch(pk, BatchStatus.MAPPING, BatchStatus.PREVIEW)
    if ok:
        batch.status, batch.finished_at = BatchStatus.CANCELLED, timezone.now()
        batch.save(update_fields=["status", "finished_at"])
        messages.info(request, "Import cancelled. Nothing was saved.")
    back = reverse("period_dashboard", args=[batch.period_id]) if batch.period_id else reverse("import_home")
    return redirect(back)
