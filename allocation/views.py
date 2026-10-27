from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import role_required
from duties.handover import HandoverBlocked, handover_candidates, replace_duty
from duties.models import Duty
from duties.services import (
    AssignmentBlocked,
    AssignmentNeedsConfirmation,
    add_duty,
    eligible_candidates,
    remove_duty,
)
from exams.halls import hall_rooms
from exams.models import ExamPeriod, PeriodLockedError, PeriodStatus, RoomAllocation
from faculty.models import Faculty
from workload.models import WorkloadPolicy

from .services import PublishBlocked, clear_draft, publish_period, roster_conflicts, run_allocation, unfilled_rooms

admin_only = role_required(Role.ADMIN)


def _room_in_period(pk, room_id):
    period = get_object_or_404(ExamPeriod, pk=pk)
    room = get_object_or_404(
        RoomAllocation.objects.select_related("session__slot", "session__department"),
        pk=room_id,
        session__slot__period=period,
    )
    return period, room


def _render_room(request, period, room, pending=None):
    hall = hall_rooms(room)
    context = {
        "period": period,
        "room": room,
        "hall": hall if len(hall) > 1 else None,  # every course sitting in this room at this time
        "hall_lead": hall[0],
        "hall_students": sum(r.student_count for r in hall),
        "duties": Duty.objects.active().filter(room_allocation=room).select_related("faculty__department"),
        "editable": period.status not in (PeriodStatus.PUBLISHED, PeriodStatus.CLOSED),
        "published": period.status == PeriodStatus.PUBLISHED,
        "can_replace": period.status == PeriodStatus.PUBLISHED and timezone.now() < room.session.slot.ends_at,
        "pending": pending,  # (faculty, warnings) awaiting confirmation
    }
    if context["editable"] and room == hall[0]:
        context["candidates"] = eligible_candidates(room)
    return render(request, "allocation/room_detail.html", context)


@admin_only
def replace_duty_view(request, pk, duty_id):
    duty = get_object_or_404(
        Duty.objects.active().select_related("faculty", "room_allocation__session__slot", "period"),
        pk=duty_id,
        period_id=pk,
    )
    room = duty.room_allocation
    if request.method == "POST":
        faculty = get_object_or_404(Faculty, pk=request.POST.get("faculty_id") or 0)
        try:
            replace_duty(duty, faculty, request.user, request.POST.get("reason", ""))
        except HandoverBlocked as e:
            messages.error(request, " ".join(e.errors))
            return redirect("replace_duty", pk=pk, duty_id=duty.pk)
        except PeriodLockedError as e:
            messages.error(request, str(e))
        else:
            messages.success(request, f"{faculty.name} now has this duty instead of {duty.faculty.name}. Both are emailed.")
        return redirect("room_detail", pk=pk, room_id=room.pk)
    context = {"period": duty.period, "duty": duty, "room": room, "candidates": handover_candidates(duty, require_account=False)}
    return render(request, "allocation/replace.html", context)


@admin_only
def room_detail(request, pk, room_id):
    period, room = _room_in_period(pk, room_id)
    return _render_room(request, period, room)


@admin_only
@require_POST
def add_duty_view(request, pk, room_id):
    period, room = _room_in_period(pk, room_id)
    faculty_id = request.POST.get("faculty_id", "")
    if not faculty_id.isdigit():
        raise Http404("Unknown faculty member.")
    faculty = get_object_or_404(Faculty, pk=faculty_id)
    try:
        add_duty(faculty, room, request.user, allow_warnings=request.POST.get("confirm") == "1")
    except PeriodLockedError as e:
        messages.error(request, str(e))
    except AssignmentBlocked as e:
        messages.error(request, f"Cannot assign {faculty.name}: {'; '.join(e.errors)}.")
    except AssignmentNeedsConfirmation as e:
        return _render_room(request, period, room, pending=(faculty, e.warnings))
    else:
        messages.success(request, f"{faculty.name} assigned to {room.room}.")
    return redirect("room_detail", pk=pk, room_id=room_id)


@admin_only
@require_POST
def remove_duty_view(request, pk, duty_id):
    duty = get_object_or_404(Duty.objects.select_related("faculty", "room_allocation"), pk=duty_id, period_id=pk)
    try:
        remove_duty(duty, request.user)
    except PeriodLockedError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f"{duty.faculty.name} removed from {duty.room_allocation.room}.")
    return redirect("room_detail", pk=pk, room_id=duty.room_allocation_id)


@admin_only
def publish_view(request, pk):
    period = get_object_or_404(ExamPeriod, pk=pk)
    if request.method == "POST":
        try:
            publish_period(
                period,
                request.user,
                acknowledge_unfilled=bool(request.POST.get("acknowledge_unfilled")),
                confirm_rules=bool(request.POST.get("confirm_rules")),
            )
        except (PublishBlocked, PeriodLockedError) as e:
            messages.error(request, str(e))
            return redirect("publish", pk=pk)
        messages.success(request, f"{period.name} is published.")
        return redirect("period_dashboard", pk=pk)
    unfilled = unfilled_rooms(period)
    context = {
        "period": period,
        "duty_count": Duty.objects.active().filter(period=period).count(),
        "unfilled": unfilled,
        "missing": sum(m for _, m, _ in unfilled),
        "conflicts": roster_conflicts(period),
        "editable": period.status not in (PeriodStatus.PUBLISHED, PeriodStatus.CLOSED),
        "rules_confirmed": WorkloadPolicy.for_period(period).confirmed_at,
    }
    return render(request, "allocation/publish.html", context)


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
