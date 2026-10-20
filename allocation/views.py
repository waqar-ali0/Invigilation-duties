from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import role_required
from exams.models import ExamPeriod, PeriodLockedError

from .services import clear_draft, run_allocation

admin_only = role_required(Role.ADMIN)


@admin_only
@require_POST
def run_allocation_view(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    try:
        result = run_allocation(period, request.user)
    except PeriodLockedError as e:
        messages.error(request, str(e))
    else:
        missing = sum(u.count for u in result.unfilled)
        text = f"Allocation finished: {len(result.assignments)} duties assigned."
        if missing:
            messages.warning(request, f"{text} {missing} seats could not be filled — see below.")
        else:
            messages.success(request, text)
    return redirect("period_dashboard", pk=pk)


@admin_only
@require_POST
def clear_draft_view(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    try:
        deleted = clear_draft(period, request.user)
    except PeriodLockedError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f"Draft cleared: {deleted} duties removed.")
    return redirect("period_dashboard", pk=pk)
