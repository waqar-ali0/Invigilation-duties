from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import CharField, Q
from django.db.models.functions import Cast
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from accounts.models import Role
from accounts.permissions import role_required
from exams.models import ExamPeriod

from .models import AuditLog

PAGE_SIZE = 50


def describe_changes(entry):
    """Lines like "status: assigned → absent" for every key in the entry's before/after."""
    before, after = entry.before or {}, entry.after or {}
    return [f"{key}: {before.get(key, '—')} → {after.get(key, '—')}" for key in sorted(set(before) | set(after))]


def _parse_date(value, request):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        messages.warning(request, "Ignored an invalid date.")
        return None


def _period_filter(period):
    """Entries about `period` or anything in it; object ids are matched by subquery (AuditLog stores them as text)."""
    from duties.models import Duty, SwapRequest, Unavailability
    from exams.models import ExamSession, RoomAllocation, TimeSlot

    ids = lambda qs: qs.annotate(text_pk=Cast("pk", CharField())).values("text_pk")  # noqa: E731
    in_period = (
        (TimeSlot, {"period": period}),
        (ExamSession, {"slot__period": period}),
        (RoomAllocation, {"session__slot__period": period}),
        (Duty, {"period": period}),
        (SwapRequest, {"duty__period": period}),
        (Unavailability, {"period": period}),
    )
    q = Q(object_type="exams.ExamPeriod", object_id=str(period.pk))
    for model, lookup in in_period:
        q |= Q(object_type=model._meta.label, object_id__in=ids(model.objects.filter(**lookup)))
    return q


@role_required(Role.ADMIN)
@require_GET
def audit_log(request):
    entries = AuditLog.objects.select_related("actor").order_by("-timestamp", "-id")
    action = request.GET.get("action", "")
    actor = request.GET.get("actor", "").strip()
    if action:
        entries = entries.filter(action=action)
    if actor:
        entries = entries.filter(actor__email__icontains=actor)
    date_from = _parse_date(request.GET.get("date_from"), request)
    date_to = _parse_date(request.GET.get("date_to"), request)
    if date_from:
        entries = entries.filter(timestamp__gte=timezone.make_aware(datetime.combine(date_from, time.min)))
    if date_to:
        entries = entries.filter(timestamp__lt=timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min)))
    period_id = request.GET.get("period", "")
    period = ExamPeriod.objects.filter(pk=period_id).first() if period_id.isdigit() else None
    if period:
        entries = entries.filter(_period_filter(period))

    page = Paginator(entries, PAGE_SIZE).get_page(request.GET.get("page"))
    for entry in page.object_list:
        entry.changes = describe_changes(entry)
    query = request.GET.copy()
    query.pop("page", None)
    context = {
        "page": page,
        "actions": AuditLog.objects.order_by("action").values_list("action", flat=True).distinct(),
        "periods": ExamPeriod.objects.all(),
        "filters": {"action": action, "actor": actor, "date_from": request.GET.get("date_from", ""), "date_to": request.GET.get("date_to", ""), "period": period.pk if period else ""},
        "query": query.urlencode(),
    }
    return render(request, "audit/log.html", context)
