"""Swap requests: a faculty member asks a colleague to take one of their published duties.

Flow: request (pending_peer) → colleague accepts (pending_admin) → exam office approves.
"""

from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.log import log
from exams.models import ExamPeriod, PeriodLockedError, PeriodStatus

from .handover import handover_check
from .models import DutyStatus, SwapRequest, SwapStatus

MSG_TOO_LATE = "It is too late to swap this duty. Contact the exam office."
MSG_NOT_OPEN = "This request is no longer open."
MSG_NOT_SWAPPABLE = "This duty can no longer be swapped."
MSG_NOT_PUBLISHED = "Only a published roster can be changed this way."


class SwapError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def _slot(duty):
    return duty.room_allocation.session.slot


def cutoff_passed(duty, now=None):
    now = now or timezone.now()
    hours = duty.period.settings.swap_cutoff_hours
    return now >= _slot(duty).starts_at - timedelta(hours=hours)


def expire_if_started(swap):
    if swap.is_open and timezone.now() >= _slot(swap.duty).starts_at:
        swap.status = SwapStatus.EXPIRED
        swap.save(update_fields=["status"])
    return swap


def _has_account(faculty):
    user = faculty.user
    return user is not None and user.is_active and user.has_usable_password()


def _admin_addresses():
    from accounts.models import Role, User

    return list(
        User.objects.filter(Q(role=Role.ADMIN) | Q(is_superuser=True), is_active=True).values_list("email", flat=True)
    )


def _emails(swap, name, recipients, **kwargs):
    from notifications.triggers import queue_swap_email

    queue_swap_email(swap, name, recipients, **kwargs)


def _addr(faculty):
    from notifications.triggers import address_of

    return address_of(faculty)


def request_swap(duty, colleague, message=""):
    errors = []
    if duty.period.status != PeriodStatus.PUBLISHED:
        errors.append(MSG_NOT_PUBLISHED)
    if duty.status != DutyStatus.ASSIGNED:
        errors.append(MSG_NOT_SWAPPABLE)
    if cutoff_passed(duty):
        errors.append(MSG_TOO_LATE)
    if SwapRequest.objects.filter(duty=duty, status__in=[SwapStatus.PENDING_PEER, SwapStatus.PENDING_ADMIN]).exists():
        errors.append("There is already an open swap request for this duty.")
    if not _has_account(colleague):
        errors.append(f"{colleague.name} has no active account.")
    errors += handover_check(duty, colleague)[0]
    if errors:
        raise SwapError(errors)
    try:
        with transaction.atomic():
            swap = SwapRequest.objects.create(
                duty=duty, requested_by=duty.faculty, requested_with=colleague, message=(message or "").strip()
            )
            log(duty.faculty.user, "swap.request", swap, after={"duty": duty.pk, "to": colleague.pk})
            _emails(swap, "swap_requested", [_addr(colleague)])
    except IntegrityError:
        raise SwapError(["There is already an open swap request for this duty."]) from None
    return swap


def _locked_swap(swap):
    """Lock the period (must be published) and the swap row; expire it if the exam has started."""
    try:
        ExamPeriod.lock_published(swap.duty.period_id)
    except PeriodLockedError as e:
        raise SwapError([str(e)]) from None
    swap = SwapRequest.objects.select_for_update().select_related(
        "duty__room_allocation__session__slot", "duty__period", "requested_by__user", "requested_with__user"
    ).get(pk=swap.pk)
    return expire_if_started(swap)


def accept_swap(swap, faculty):
    if swap.requested_with_id != faculty.id:
        raise PermissionDenied
    with transaction.atomic():
        swap = _locked_swap(swap)
        if swap.status != SwapStatus.PENDING_PEER:
            raise SwapError([MSG_NOT_OPEN])
        if cutoff_passed(swap.duty):
            raise SwapError([MSG_TOO_LATE])
        errors = handover_check(swap.duty, swap.requested_with)[0]
        if errors:
            raise SwapError(errors)
        swap.status = SwapStatus.PENDING_ADMIN
        swap.responded_at = timezone.now()
        swap.save(update_fields=["status", "responded_at"])
        log(faculty.user, "swap.accept", swap)
        _emails(swap, "swap_accepted", _admin_addresses())
    return swap


def decline_swap(swap, faculty):
    if swap.requested_with_id != faculty.id:
        raise PermissionDenied
    with transaction.atomic():
        swap = _locked_swap(swap)
        if swap.status != SwapStatus.PENDING_PEER:
            raise SwapError([MSG_NOT_OPEN])
        swap.status = SwapStatus.DECLINED
        swap.responded_at = timezone.now()
        swap.save(update_fields=["status", "responded_at"])
        log(faculty.user, "swap.decline", swap)
        _emails(swap, "swap_declined", [_addr(swap.requested_by)])
    return swap


def approve_swap(swap, actor):
    """Exam office approval: re-check the rules, then hand the duty to the colleague."""
    from notifications.triggers import duty_ics_attachment

    from .handover import transfer_duty
    from .models import DutySource

    with transaction.atomic():
        swap = _locked_swap(swap)
        if swap.status != SwapStatus.PENDING_ADMIN:
            raise SwapError(["This request is no longer waiting for approval."])
        duty = swap.duty
        if duty.status != DutyStatus.ASSIGNED:
            raise SwapError([MSG_NOT_SWAPPABLE])
        errors = handover_check(duty, swap.requested_with)[0]
        if errors:
            raise SwapError(errors)
        new_duty = transfer_duty(duty, swap.requested_with, DutySource.SWAP)
        swap.status = SwapStatus.APPROVED
        swap.decided_by = actor
        swap.decided_at = timezone.now()
        swap.new_duty = new_duty
        swap.save(update_fields=["status", "decided_by", "decided_at", "new_duty"])
        log(actor, "swap.approve", swap, before={"faculty": duty.faculty_id}, after={"faculty": new_duty.faculty_id, "duty": new_duty.pk})
        _emails(swap, "swap_approved", [_addr(swap.requested_by)])
        _emails(swap, "swap_approved", [_addr(swap.requested_with)], attachment=duty_ics_attachment(new_duty))
    return new_duty


def reject_swap(swap, actor, comment=""):
    with transaction.atomic():
        swap = _locked_swap(swap)
        if swap.status != SwapStatus.PENDING_ADMIN:
            raise SwapError(["This request is no longer waiting for approval."])
        swap.status = SwapStatus.REJECTED
        swap.decided_by = actor
        swap.decided_at = timezone.now()
        swap.comment = (comment or "").strip()
        swap.save(update_fields=["status", "decided_by", "decided_at", "comment"])
        log(actor, "swap.reject", swap, after={"comment": swap.comment})
        _emails(swap, "swap_rejected", [_addr(swap.requested_by), _addr(swap.requested_with)])
    return swap


def cancel_swap(swap, faculty):
    if swap.requested_by_id != faculty.id:
        raise PermissionDenied
    with transaction.atomic():
        swap = _locked_swap(swap)
        if not swap.is_open:
            raise SwapError([MSG_NOT_OPEN])
        swap.status = SwapStatus.CANCELLED
        swap.save(update_fields=["status"])
        log(faculty.user, "swap.cancel", swap)
    return swap
