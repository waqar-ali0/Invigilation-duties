"""Faculty requests for dates they cannot invigilate, and decisions on them."""

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .models import Unavailability, UnavailabilityStatus

MSG_WINDOW_CLOSED = "The availability window for this period is closed."


class RequestRejected(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def submit_request(faculty, period, day, slot, reason):
    """Create a pending request for `day` (whole day when `slot` is None)."""
    reason = (reason or "").strip()
    errors = []
    if faculty.is_exempt:
        errors.append("You are exempt from invigilation.")
    if not period.availability_is_open:
        errors.append(MSG_WINDOW_CLOSED)
    if not (period.start_date <= day <= period.end_date):
        errors.append("Date is outside the exam period.")
    if slot is not None and (slot.period_id != period.id or slot.date != day):
        errors.append("That time slot is not on the chosen date.")
    if not reason:
        errors.append("A reason is required.")
    overlapping = Unavailability.objects.filter(
        faculty=faculty, date=day, status__in=[UnavailabilityStatus.PENDING, UnavailabilityStatus.APPROVED]
    )
    if slot is not None:
        overlapping = overlapping.filter(Q(slot__isnull=True) | Q(slot=slot))
    if overlapping.exists():
        errors.append("You already have a request covering this date and slot.")
    if errors:
        raise RequestRejected(errors)
    return Unavailability.objects.create(faculty=faculty, period=period, date=day, slot=slot, reason=reason)


def can_decide(user, req):
    """Admins decide any request; an HOD decides requests from departments they head, except their own."""
    from accounts.models import Role
    from faculty.models import Department, Faculty

    if user.is_superuser or user.role == Role.ADMIN:
        return True
    if user.role != Role.HOD:
        return False
    me = Faculty.objects.filter(user=user).first()
    if me is None or req.faculty_id == me.id:
        return False
    return Department.objects.filter(hod=me, pk=req.faculty.department_id).exists()


def decide_request(req, actor, approve, comment=""):
    from django.db import transaction
    from django.urls import reverse
    from django.utils import timezone

    from audit.log import log
    from exams.models import ExamPeriod
    from notifications.models import EmailKind
    from notifications.outbox import queue_email
    from notifications.render import absolute_url, render_email

    if not can_decide(actor, req):
        raise PermissionDenied
    with transaction.atomic():
        ExamPeriod.lock_for_edit(req.period_id)
        req = Unavailability.objects.select_for_update().select_related("faculty__user", "period", "slot").get(pk=req.pk)
        if req.status != UnavailabilityStatus.PENDING:
            raise RequestRejected(["This request was already decided."])
        req.status = UnavailabilityStatus.APPROVED if approve else UnavailabilityStatus.REJECTED
        req.reviewed_by = actor
        req.reviewed_at = timezone.now()
        req.review_comment = (comment or "").strip()
        req.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_comment"])
        log(
            actor,
            "unavailability.approve" if approve else "unavailability.reject",
            req,
            after={"faculty": req.faculty_id, "date": req.date.isoformat(), "comment": req.review_comment},
        )
        to = req.faculty.user.email if req.faculty.user else req.faculty.email
        subject, body = render_email(
            "request_decision", {"req": req, "approved": approve, "link": absolute_url(reverse("my_unavailability"))}
        )
        queue_email(to, subject, body, EmailKind.REQUEST_DECISION)
    return req


def withdraw_request(req, faculty):
    if req.faculty_id != faculty.id:
        raise PermissionDenied
    if req.status != UnavailabilityStatus.PENDING:
        raise RequestRejected(["Only pending requests can be withdrawn."])
    if not req.period.availability_is_open:
        raise RequestRejected([MSG_WINDOW_CLOSED])
    req.status = UnavailabilityStatus.WITHDRAWN
    req.save(update_fields=["status"])
