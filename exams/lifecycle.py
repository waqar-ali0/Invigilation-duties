"""An exam period's life: draft → availability → allocating → published (in progress while exams
run) → completed → archived, or cancelled. Every change is logged; finished periods never change."""

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from audit.log import log

from .models import DRAFT_STATUSES, ExamPeriod, PeriodStatus


class LifecycleError(Exception):
    pass


def _set(period, status, actor, **extra):
    before = period.status
    period.status, period.status_changed_at = status, timezone.now()
    for k, v in extra.items():
        setattr(period, k, v)
    period.save(update_fields=["status", "status_changed_at", *extra])
    log(actor, "period.status", period, before={"status": before}, after={"status": status, **extra})


def complete(period, actor):
    """Published → completed, once the last exam day has passed."""
    with transaction.atomic():
        period = ExamPeriod.objects.select_for_update().get(pk=period.pk)
        if period.status != PeriodStatus.PUBLISHED:
            raise LifecycleError("Only a published period can be marked completed.")
        if timezone.localdate() <= period.end_date:
            raise LifecycleError(f"The exams run until {period.end_date:%d %b %Y}; mark it completed after that.")
        _set(period, PeriodStatus.CLOSED, actor)
    return period


def archive(period, actor):
    with transaction.atomic():
        period = ExamPeriod.objects.select_for_update().get(pk=period.pk)
        if period.status not in (PeriodStatus.CLOSED, PeriodStatus.CANCELLED):
            raise LifecycleError("Only completed or cancelled periods can be archived.")
        _set(period, PeriodStatus.ARCHIVED, actor)
    return period


def unarchive(period, actor):
    with transaction.atomic():
        period = ExamPeriod.objects.select_for_update().get(pk=period.pk)
        if period.status != PeriodStatus.ARCHIVED:
            raise LifecycleError("This period is not archived.")
        _set(period, PeriodStatus.CLOSED, actor)
    return period


def cancel(period, actor, reason):
    """Cancel a period. In a draft the duties are simply dropped from use; once published every
    open duty is cancelled (kept on record) and the people concerned are notified."""
    from duties.models import Duty, DutyStatus
    from messaging.models import MessageKind
    from messaging.services import cancel_for_duty, notify_duty
    from notifications.models import EmailKind
    from notifications.outbox import queue_email
    from notifications.render import absolute_url
    from notifications.triggers import address_of

    reason = (reason or "").strip()
    if not reason:
        raise LifecycleError("Give the reason for cancelling.")
    with transaction.atomic():
        period = ExamPeriod.objects.select_for_update().get(pk=period.pk)
        if period.is_finished:
            raise LifecycleError(f"This period is already {period.get_status_display().lower()}.")
        published = period.status == PeriodStatus.PUBLISHED
        affected = []
        if published:
            for duty in Duty.objects.filter(period=period, status=DutyStatus.ASSIGNED).select_related(
                "faculty__user", "period", "room_allocation__session__slot", "room_allocation__room_ref__building"
            ):
                duty.status = DutyStatus.CANCELLED
                duty.save(update_fields=["status", "updated_at"])
                cancel_for_duty(duty)
                notify_duty(duty, MessageKind.CANCELLED, actor)
                affected.append(duty.faculty)
        _set(period, PeriodStatus.CANCELLED, actor, cancel_reason=reason[:300])
        link = absolute_url(reverse("my_duties"))
        for faculty in {f.pk: f for f in affected}.values():
            queue_email(
                address_of(faculty),
                f"Exam period cancelled: {period.name}",
                f"Dear {faculty.name},\n\nThe exam period {period.name} has been cancelled, so your invigilation duties in it are cancelled too.\nReason: {reason}\n\nYour duties: {link}\n\n— Exam Office\n",
                EmailKind.ANNOUNCEMENT,
            )
    return period


def actions(period):
    """[(action, label, needs_reason, confirm text)] that make sense now."""
    out = []
    if period.status == PeriodStatus.PUBLISHED:
        out.append(("complete", "Mark completed", False, "Mark this period completed? It can no longer be changed."))
    if period.status in (PeriodStatus.CLOSED, PeriodStatus.CANCELLED):
        out.append(("archive", "Archive", False, "Archive this period? It stays readable under Archived."))
    if period.status == PeriodStatus.ARCHIVED:
        out.append(("unarchive", "Take out of archive", False, ""))
    if period.status in DRAFT_STATUSES or period.status == PeriodStatus.PUBLISHED:
        out.append(("cancel", "Cancel period", True, "Cancel this exam period? Open duties are cancelled and people are notified."))
    return out
